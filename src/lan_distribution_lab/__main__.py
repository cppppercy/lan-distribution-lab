"""Windows-friendly local commands and explicit private-LAN server entry point."""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import sys
from pathlib import Path

import uvicorn

from .app import create_app
from .client import DownloadError, ResumableDownloader, exchange_cli_pairing_code
from .config import Settings
from .distribution import (
    MAX_CLIENTS,
    DistributionError,
    build_client_tasks,
    client_artifact_paths,
    prepare_output_directory,
    run_distribution,
    safe_output_name,
    validate_distribution_paths,
    write_result_json,
)
from .distribution_store import DistributionStore
from .runtime import Runtime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m lan_distribution_lab")
    commands = parser.add_subparsers(dest="command")

    serve = commands.add_parser("serve", help="run the HTTP service (localhost by default)")
    _add_storage_arguments(serve)
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="listen only on this loopback or private LAN IPv4 address (default: 127.0.0.1)",
    )
    serve.add_argument("--port", type=int, default=8000)

    pairing = commands.add_parser("pair", help="manage short-lived local pairing codes")
    pair_commands = pairing.add_subparsers(dest="pair_command", required=True)
    create = pair_commands.add_parser("create", help="create a one-time pairing code")
    _add_storage_arguments(create)
    create.add_argument("--scope", choices=("read", "read_write"), default="read")

    download = commands.add_parser("download", help="safely download or resume an immutable file")
    download.add_argument("--server", required=True, help="bare http(s) server address, without an API path")
    download.add_argument("--file-id", required=True, help="UUID returned by the file list or metadata API")
    download.add_argument("--output", type=Path, required=True, help="new local destination file path")
    credentials = download.add_mutually_exclusive_group()
    credentials.add_argument(
        "--pair",
        action="store_true",
        help="securely prompt for a one-time pairing code and keep the resulting token only in memory",
    )
    credentials.add_argument(
        "--token-stdin",
        action="store_true",
        help="read one access token from standard input (the default is a no-echo prompt)",
    )
    download.add_argument("--device-label", help="optional 1-64 character label used only while pairing")
    download.add_argument("--timeout", type=float, default=15.0, help="per-request timeout in seconds")
    download.add_argument("--retries", type=int, default=2, help="finite retry count for network failures")

    distribute = commands.add_parser(
        "distribute",
        help="download one immutable file through several independent client sessions",
    )
    distribute.add_argument("--server", required=True, help="bare http(s) server address, without an API path")
    distribute.add_argument("--file-id", required=True, help="UUID returned by the file list or metadata API")
    distribute.add_argument(
        "--strategy",
        choices=("sequential", "parallel"),
        required=True,
        help="sequential waits for each client; parallel overlaps the downloads",
    )
    distribute.add_argument("--clients", type=int, default=2, help=f"number of logical clients (1-{MAX_CLIENTS})")
    distribute.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="directory that receives one subdirectory per logical client",
    )
    distribute.add_argument(
        "--output-name",
        default="download.bin",
        help="file name written inside each client directory (default: download.bin)",
    )
    distribute_credentials = distribute.add_mutually_exclusive_group(required=True)
    distribute_credentials.add_argument(
        "--pair",
        action="store_true",
        help="securely prompt for one one-time pairing code per client (token kept in memory only)",
    )
    distribute_credentials.add_argument(
        "--tokens-stdin",
        action="store_true",
        help="read one access token per client from standard input (for an already secure pipe)",
    )
    distribute.add_argument("--timeout", type=float, default=15.0, help="per-request timeout in seconds")
    distribute.add_argument("--retries", type=int, default=2, help="finite retry count for network failures")
    distribute.add_argument(
        "--stop-on-error",
        action="store_true",
        help="sequential only: mark the remaining clients cancelled after the first failure",
    )
    distribute.add_argument(
        "--results",
        type=Path,
        help="machine readable JSON result path (default: <output-dir>/distribution-result.json)",
    )
    distribute.add_argument(
        "--records",
        type=Path,
        help="SQLite distribution record path (default: <output-dir>/distribution-runs.db)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        arguments = parser.parse_args(["serve"])

    if arguments.command == "serve":
        try:
            settings = _settings_from_arguments(arguments)
            host = _listen_host(arguments.host)
        except argparse.ArgumentTypeError as error:
            parser.error(str(error))
        uvicorn.run(create_app(settings), host=host, port=arguments.port, reload=False)
        return

    if arguments.command == "pair" and arguments.pair_command == "create":
        settings = _settings_from_arguments(arguments)
        pairing = Runtime(settings).auth.create_pairing_code(arguments.scope)
        print(f"Pairing code: {pairing.code}")
        print(f"Scope: {pairing.scope}")
        print(f"Expires (UTC): {pairing.expires_at.isoformat().replace('+00:00', 'Z')}")
        print(f"Data directory: {settings.data_dir}")
        return

    if arguments.command == "download":
        try:
            if arguments.pair:
                pairing_code = getpass.getpass("Pairing code: ")
                token = exchange_cli_pairing_code(
                    arguments.server,
                    pairing_code,
                    timeout_seconds=arguments.timeout,
                    device_label=arguments.device_label,
                )
            elif arguments.token_stdin:
                token = sys.stdin.readline().strip()
            else:
                token = getpass.getpass("Access token: ")
            result = ResumableDownloader(
                arguments.server,
                token,
                timeout_seconds=arguments.timeout,
                retries=arguments.retries,
            ).download(arguments.file_id, arguments.output)
        except DownloadError as error:
            print(f"Download failed: {error}", file=sys.stderr)
            raise SystemExit(1) from None
        print(
            f"Downloaded {result.size_bytes} bytes to {result.output_path} "
            f"(SHA-256: {result.content_sha256}; resumed: {result.resumed_bytes} bytes)."
        )
        return

    if arguments.command == "distribute":
        _run_distribute_command(arguments)
        return

    parser.error("unknown command")


def _run_distribute_command(arguments: argparse.Namespace) -> None:
    try:
        if not 1 <= arguments.clients <= MAX_CLIENTS:
            raise DistributionError(f"--clients must be between 1 and {MAX_CLIENTS}.")
        if arguments.timeout <= 0:
            raise DistributionError("--timeout must be greater than zero.")
        if arguments.retries < 0:
            raise DistributionError("--retries cannot be negative.")
        if arguments.strategy == "parallel" and arguments.stop_on_error:
            raise DistributionError("--stop-on-error is only valid with --strategy sequential.")
        output_name = safe_output_name(arguments.output_name)
        output_dir = prepare_output_directory(arguments.output_dir)
        results_path = _record_path(arguments.results, output_dir / "distribution-result.json")
        records_path = _record_path(arguments.records, output_dir / "distribution-runs.db")
        client_outputs = [
            output_dir / f"client-{index + 1}" / output_name
            for index in range(arguments.clients)
        ]
        # Validate every record path before credentials are requested or any
        # download starts, so a conflicting path can never overwrite a file.
        validate_distribution_paths(results_path, records_path, client_outputs)
        tokens = _gather_client_tokens(arguments)
        tasks = build_client_tasks(output_dir, output_name, tokens)
        run = run_distribution(
            arguments.server,
            arguments.file_id,
            arguments.strategy,
            tasks,
            timeout_seconds=arguments.timeout,
            retries=arguments.retries,
            stop_on_error=arguments.stop_on_error,
        )
        protected_paths = [
            artifact for output in client_outputs for artifact in client_artifact_paths(output)
        ]
        results_path = write_result_json(results_path, run, protected_paths=protected_paths)
        store = DistributionStore(records_path)
        store.save(run)
    except (DistributionError, DownloadError) as error:
        print(f"Distribution failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None

    counts = run.counts()
    print(
        f"Distribution {run.run_id} ({run.strategy}): "
        f"{counts['succeeded']} succeeded, {counts['failed']} failed, "
        f"{counts['cancelled']} cancelled in {run.duration_seconds:.3f} s."
    )
    for client in run.clients:
        if client.status == "succeeded":
            print(
                f"  [ok] {client.client_id} {client.downloaded_bytes} bytes "
                f"(resumed {client.resumed_bytes}), {client.duration_seconds:.3f} s"
            )
        else:
            detail = client.error_message or "no detail"
            if client.error_type:
                detail = f"{client.error_type}: {detail}"
            print(f"  [{client.status}] {client.client_id} {detail}")
    print(f"Result JSON: {results_path}")
    print(f"Records: {store.path}")
    if counts["succeeded"] != len(run.clients):
        raise SystemExit(1)


def _record_path(value: Path | None, default: Path) -> Path:
    candidate = Path(value) if value is not None else default
    return candidate.expanduser().resolve(strict=False)


def _gather_client_tokens(arguments: argparse.Namespace) -> list[str]:
    count = arguments.clients
    if arguments.pair:
        tokens: list[str] = []
        for index in range(count):
            client_id = f"client-{index + 1}"
            pairing_code = getpass.getpass(f"Pairing code for {client_id}: ")
            tokens.append(
                exchange_cli_pairing_code(
                    arguments.server,
                    pairing_code,
                    timeout_seconds=arguments.timeout,
                    device_label=f"distribution-{client_id}",
                )
            )
        return tokens
    tokens = []
    for _ in range(count):
        line = sys.stdin.readline()
        if not line:
            break
        token = line.strip()
        if token:
            tokens.append(token)
    if len(tokens) != count:
        raise DistributionError(
            f"Expected {count} access token(s) on standard input; received {len(tokens)}."
        )
    return tokens


def _add_storage_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="local SQLite and server-secret directory; use the same value for pair and serve",
    )
    parser.add_argument(
        "--managed-root",
        type=Path,
        help="separate managed storage root; only objects/ and staging/ are created in T003-A",
    )
    parser.add_argument(
        "--origin",
        help="exact browser Origin accepted for cookie state changes (default: localhost URL)",
    )


def _settings_from_arguments(arguments: argparse.Namespace) -> Settings:
    port = getattr(arguments, "port", 8000)
    host = _listen_host(getattr(arguments, "host", "127.0.0.1"))
    base = Settings.from_environment()
    data_dir = arguments.data_dir or base.data_dir
    managed_root = arguments.managed_root or base.managed_root
    # A production web page is served from this listener.  An explicit --origin
    # is reserved for local Vite development; environment state must not silently
    # make a LAN browser's Origin mismatch the selected listening address.
    origin = arguments.origin or f"http://{host}:{port}"
    return Settings(data_dir=data_dir, managed_root=managed_root, origin=origin.rstrip("/"))


def _listen_host(value: str) -> str:
    """Accept explicit loopback/private IPv4 bindings, never an all-interface bind."""
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as error:
        raise argparse.ArgumentTypeError("--host must be an IPv4 address") from error
    if address.is_unspecified or not (address.is_loopback or address.is_private):
        raise argparse.ArgumentTypeError("--host must be a loopback or private LAN IPv4 address")
    return str(address)


if __name__ == "__main__":
    main()
