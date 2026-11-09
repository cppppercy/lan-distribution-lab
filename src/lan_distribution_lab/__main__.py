"""Windows-friendly local commands and explicit private-LAN server entry point."""

from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path

import uvicorn

from .app import create_app
from .config import Settings
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

    parser.error("unknown command")


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
