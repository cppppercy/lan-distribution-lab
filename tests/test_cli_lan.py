from __future__ import annotations

import argparse

import pytest

from lan_distribution_lab.__main__ import _listen_host, _settings_from_arguments, build_parser


def test_default_serve_stays_on_loopback() -> None:
    arguments = build_parser().parse_args(["serve"])

    assert arguments.host == "127.0.0.1"
    assert _settings_from_arguments(arguments).origin == "http://127.0.0.1:8000"


def test_explicit_private_lan_host_sets_matching_browser_origin() -> None:
    arguments = build_parser().parse_args(["serve", "--host", "192.168.50.20", "--port", "8123"])

    assert _listen_host(arguments.host) == "192.168.50.20"
    assert _settings_from_arguments(arguments).origin == "http://192.168.50.20:8123"


def test_selected_lan_host_overrides_stale_origin_environment(monkeypatch) -> None:
    monkeypatch.setenv("LAN_DISTRIBUTION_ORIGIN", "http://127.0.0.1:9999")
    arguments = build_parser().parse_args(["serve", "--host", "10.0.0.8", "--port", "8010"])

    assert _settings_from_arguments(arguments).origin == "http://10.0.0.8:8010"


@pytest.mark.parametrize("host", ["0.0.0.0", "8.8.8.8", "localhost", "::1"])
def test_lan_host_rejects_unspecified_public_and_hostnames(host: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _listen_host(host)
