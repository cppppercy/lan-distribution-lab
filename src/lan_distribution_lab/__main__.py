"""Windows-friendly local commands and explicit private-LAN server entry point."""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import sys
from pathlib import Path

import uvicorn

from .analysis import ANALYSIS_SCHEMA_VERSION, CHART_METRICS, AnalysisError, analyze_experiments
from .app import create_app
from .client import DownloadError, ResumableDownloader, exchange_cli_pairing_code
from .config import Settings
from .discovery import (
    DEFAULT_DISCOVERY_PORT,
    DEFAULT_SERVICE_NAME,
    DiscoveryError,
    DiscoveryResponder,
    discover_services,
    validate_discovery_bind,
    validate_interface_address,
    validate_service_name,
    validate_target_address,
)
from .distribution import (
    MAX_CLIENTS,
    MAX_CONCURRENT,
    STRATEGIES,
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
from .experiment import (
    MAX_ROUNDS,
    ExperimentConfig,
    ExperimentError,
    ExperimentRunner,
    parse_strategies,
    prepare_experiment_directory,
    validate_config,
)
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
    serve.add_argument(
        "--discover",
        action="store_true",
        help="enable the UDP LAN discovery responder (requires a private LAN --host)",
    )
    serve.add_argument(
        "--discover-port",
        type=int,
        default=None,
        help=f"UDP discovery port (default: {DEFAULT_DISCOVERY_PORT}); requires --discover",
    )
    serve.add_argument(
        "--discover-bind",
        default=None,
        help="UDP bind address: 0.0.0.0, loopback or private IPv4 (default: 0.0.0.0); requires --discover",
    )
    serve.add_argument(
        "--service-name",
        default=None,
        help=f"display name returned by discovery (default: {DEFAULT_SERVICE_NAME!r})",
    )

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

    discover = commands.add_parser(
        "discover", help="find LAN Distribution Lab services over UDP"
    )
    discover.add_argument(
        "--port",
        type=int,
        default=DEFAULT_DISCOVERY_PORT,
        help=f"UDP discovery port (default: {DEFAULT_DISCOVERY_PORT})",
    )
    discover.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="total discovery wait in seconds (default: 2.0, maximum: 30)",
    )
    discover.add_argument(
        "--broadcast",
        action="append",
        type=_discovery_target,
        help="IPv4 target, broadcast or unicast; repeatable (default: 255.255.255.255)",
    )
    discover.add_argument(
        "--interface",
        type=_discovery_interface,
        help="local loopback/private IPv4 to bind the UDP source (multi-NIC selection)",
    )
    discover.add_argument(
        "--json",
        action="store_true",
        help="print a machine-readable JSON array instead of the text list",
    )

    distribute = commands.add_parser(
        "distribute",
        help="download one immutable file through several independent client sessions",
    )
    distribute.add_argument("--server", required=True, help="bare http(s) server address, without an API path")
    distribute.add_argument("--file-id", required=True, help="UUID returned by the file list or metadata API")
    distribute.add_argument(
        "--strategy",
        choices=STRATEGIES,
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
        "--max-concurrent",
        type=int,
        default=None,
        help=(
            "controlled only: maximum number of client tasks running at once "
            f"(1 to --clients, at most {MAX_CONCURRENT})"
        ),
    )
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

    experiment = commands.add_parser(
        "experiment", help="run repeatable local-controller distribution trials and export raw records"
    )
    experiment.add_argument("--server", required=True, help="bare http(s) server address")
    experiment.add_argument(
        "--file-id", action="append", required=True, dest="file_ids",
        help=f"file UUID to test; repeatable (maximum 4)",
    )
    experiment.add_argument(
        "--strategies", default="sequential,parallel,controlled",
        help="comma-separated ordered strategies (sequential,parallel,controlled)",
    )
    experiment.add_argument("--clients", type=int, default=2, help=f"logical clients (1-{MAX_CLIENTS})")
    experiment.add_argument(
        "--max-concurrent", type=int, default=2,
        help="controlled-only fixed limit; required when controlled is selected",
    )
    experiment.add_argument("--rounds", type=int, default=1, help=f"repetitions per file/strategy (1-{MAX_ROUNDS})")
    experiment.add_argument("--output-dir", type=Path, required=True, help="new, empty experiment result directory")
    experiment.add_argument("--timeout", type=float, default=15.0)
    experiment.add_argument("--retries", type=int, default=2)
    experiment.add_argument(
        "--environment-type", choices=("local_loopback", "controller_to_lan"), default="local_loopback",
        help="actual controller deployment; never labels logical clients as physical devices",
    )
    experiment.add_argument("--on-error", choices=("continue", "stop"), default="continue")
    experiment_credentials = experiment.add_mutually_exclusive_group(required=True)
    experiment_credentials.add_argument("--pair", action="store_true", help="prompt once per logical client")
    experiment_credentials.add_argument("--tokens-stdin", action="store_true", help="read one token per client from protected stdin")

    analyze = commands.add_parser(
        "analyze", help="validate T010 exports and generate descriptive SVG charts and a Markdown report"
    )
    analyze.add_argument(
        "--input", action="append", type=Path, required=True, dest="inputs",
        help="T010 experiment result directory; repeat for separate experiments",
    )
    analyze.add_argument(
        "--output-dir", type=Path, required=True,
        help="new analysis output directory (never overwrites an existing directory)",
    )
    analyze.add_argument(
        "--metrics", default=",".join(CHART_METRICS),
        help="comma-separated charts: duration,throughput,success,controlled,client-timing",
    )
    analyze.add_argument(
        "--packet-summary", type=Path,
        help=("optional local sanitized schema-v1 JSON summary; no PCAP is read, uploaded or captured"),
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
            discovery = _discovery_config(arguments, host)
        except (argparse.ArgumentTypeError, DiscoveryError) as error:
            parser.error(str(error))
        responder: DiscoveryResponder | None = None
        try:
            if discovery is not None:
                responder = DiscoveryResponder(
                    http_port=arguments.port,
                    service_name=discovery["service_name"],
                    bind_address=discovery["bind"],
                    discovery_port=discovery["port"],
                )
                responder.start()
                print(
                    f"UDP discovery enabled: {discovery['bind']}:{responder.port} "
                    f"(service name: {discovery['service_name']})"
                )
                print("Find this service from another Windows CLI: python -m lan_distribution_lab discover")
            if host != "127.0.0.1":
                print(f"LAN access URL: http://{host}:{arguments.port}/")
            uvicorn.run(create_app(settings), host=host, port=arguments.port, reload=False)
        except DiscoveryError as error:
            print(f"Discovery failed: {error}", file=sys.stderr)
            raise SystemExit(1) from None
        finally:
            if responder is not None:
                responder.close()
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

    if arguments.command == "experiment":
        _run_experiment_command(arguments)
        return

    if arguments.command == "analyze":
        _run_analyze_command(arguments)
        return

    if arguments.command == "discover":
        _run_discover_command(arguments)
        return

    parser.error("unknown command")


def _discovery_target(value: str) -> str:
    try:
        return validate_target_address(value)
    except DiscoveryError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _discovery_interface(value: str) -> str:
    try:
        return validate_interface_address(value)
    except DiscoveryError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _discovery_config(
    arguments: argparse.Namespace, host: str
) -> dict[str, object] | None:
    """Validate the opt-in discovery options; ``None`` means discovery is off."""
    if not arguments.discover:
        if any(
            value is not None
            for value in (
                arguments.discover_port,
                arguments.discover_bind,
                arguments.service_name,
            )
        ):
            raise argparse.ArgumentTypeError(
                "--discover-port, --discover-bind and --service-name require --discover."
            )
        return None
    if ipaddress.IPv4Address(host).is_loopback:
        raise argparse.ArgumentTypeError(
            "--discover requires --host with a private LAN IPv4 address, not loopback."
        )
    port = (
        arguments.discover_port
        if arguments.discover_port is not None
        else DEFAULT_DISCOVERY_PORT
    )
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("--discover-port must be between 1 and 65535.")
    try:
        bind_address = validate_discovery_bind(arguments.discover_bind or "0.0.0.0")
        service_name = validate_service_name(arguments.service_name or DEFAULT_SERVICE_NAME)
    except DiscoveryError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    return {"port": port, "bind": bind_address, "service_name": service_name}


def _run_discover_command(arguments: argparse.Namespace) -> None:
    try:
        services = discover_services(
            arguments.broadcast or None,
            discovery_port=arguments.port,
            timeout=arguments.timeout,
            interface=arguments.interface,
        )
    except DiscoveryError as error:
        print(f"Discovery failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    if arguments.json:
        print(json.dumps([service.to_dict() for service in services], ensure_ascii=False))
        return
    if not services:
        print(f"No LAN Distribution Lab service discovered within {arguments.timeout:.1f} s.")
        print("Check that the server runs with --discover, the device is on the same subnet,")
        print(f"and that UDP port {arguments.port} is not blocked by Windows Firewall.")
        print("Fallback: open http://<LAN-IP>:<HTTP-PORT>/ manually and pair with a one-time code.")
        return
    print(f"Discovered {len(services)} service(s):")
    for service in services:
        print(f"  {service.service_name}")
        print(f"    URL: {service.url}")
        print(f"    Address: {service.address}:{service.http_port}")
        print(f"    Protocol: v{service.protocol_version}; pairing is still required")


def _run_distribute_command(arguments: argparse.Namespace) -> None:
    try:
        if not 1 <= arguments.clients <= MAX_CLIENTS:
            raise DistributionError(f"--clients must be between 1 and {MAX_CLIENTS}.")
        if arguments.timeout <= 0:
            raise DistributionError("--timeout must be greater than zero.")
        if arguments.retries < 0:
            raise DistributionError("--retries cannot be negative.")
        if arguments.strategy != "sequential" and arguments.stop_on_error:
            raise DistributionError("--stop-on-error is only valid with --strategy sequential.")
        if arguments.strategy == "controlled":
            if arguments.max_concurrent is None:
                raise DistributionError("--strategy controlled requires --max-concurrent N.")
            if not 1 <= arguments.max_concurrent <= arguments.clients:
                raise DistributionError("--max-concurrent must be between 1 and --clients.")
            if arguments.max_concurrent > MAX_CONCURRENT:
                raise DistributionError(
                    f"--max-concurrent cannot exceed {MAX_CONCURRENT}."
                )
        elif arguments.max_concurrent is not None:
            raise DistributionError("--max-concurrent is only valid with --strategy controlled.")
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
            max_concurrent=arguments.max_concurrent,
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
    if run.max_concurrent is not None:
        print(
            f"Scheduling: max concurrent {run.max_concurrent}, "
            f"observed max active {run.observed_max_active}."
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


def _run_experiment_command(arguments: argparse.Namespace) -> None:
    try:
        strategies = parse_strategies(arguments.strategies)
        max_concurrent = arguments.max_concurrent if "controlled" in strategies else None
        config = ExperimentConfig(
            server=arguments.server,
            file_ids=tuple(arguments.file_ids),
            strategies=strategies,
            clients=arguments.clients,
            max_concurrent=max_concurrent,
            rounds=arguments.rounds,
            timeout_seconds=arguments.timeout,
            retries=arguments.retries,
            environment_type=arguments.environment_type,
            on_error=arguments.on_error,
        )
        validate_config(config)
        output_dir = prepare_experiment_directory(arguments.output_dir)
        # The distribution token reader already enforces one distinct, in-memory
        # token per logical client and never prints them.
        tokens = _gather_client_tokens(arguments)
        runner = ExperimentRunner(config, tokens, output_dir)
        runner.run()
    except (ExperimentError, DistributionError, DownloadError) as error:
        print(f"Experiment failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("Experiment interrupted; completed trial records were persisted.", file=sys.stderr)
        raise SystemExit(130) from None
    print(f"Experiment {runner.experiment_id} state={runner.state}: {output_dir}")
    print("Exports: experiments.json, trials.csv, clients.csv and trials/<trial>/distribution-result.json")
    if runner.state != "completed":
        print(
            f"Experiment is incomplete ({runner.state}); result files were kept at: {output_dir}",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _run_analyze_command(arguments: argparse.Namespace) -> None:
    try:
        metrics = tuple(item.strip() for item in arguments.metrics.split(",") if item.strip())
        output_dir = analyze_experiments(
            arguments.inputs,
            arguments.output_dir,
            metrics=metrics,
            packet_summary=arguments.packet_summary,
        )
    except AnalysisError as error:
        print(f"Analysis failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"Analysis schema v{ANALYSIS_SCHEMA_VERSION} written to: {output_dir}")
    print("Outputs: analysis.json, report.md and charts/*.svg")


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
