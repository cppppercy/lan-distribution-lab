"""T009 tests: UDP discovery protocol, responder lifecycle and CLI wiring.

All network tests use loopback UDP sockets.  Successful loopback discovery does
not prove that real Wi-Fi/hotspot broadcast discovery works; that remains a
manual experiment (see docs/EXPERIMENTS.md).
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time

import pytest

from lan_distribution_lab.__main__ import (
    _discovery_config,
    _run_discover_command,
    build_parser,
)
from lan_distribution_lab.discovery import (
    DISCOVERY_PROTOCOL,
    DISCOVERY_VERSION,
    MAX_RESPONSE_BYTES,
    DiscoveryError,
    DiscoveryResponder,
    DiscoveredService,
    build_discovery_request,
    discover_services,
    parse_discovery_response,
    validate_discovery_bind,
    validate_interface_address,
    validate_service_name,
    validate_target_address,
)
from lan_distribution_lab.discovery import _RateLimiter


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _FakeUdpResponder:
    """A loopback UDP socket driven by a test handler."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.settimeout(0.2)
        self.port = int(self._socket.getsockname()[1])
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "_FakeUdpResponder":
        self._thread.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._stop.set()
        try:
            self._socket.close()
        except OSError:
            pass
        self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, address = self._socket.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handler(self._socket, address, data)
            except Exception:  # pragma: no cover - test handler safety
                continue


def _response_payload(request_id: str, *, instance: str, name: str, port: int) -> dict:
    return {
        "protocol": DISCOVERY_PROTOCOL,
        "protocol_version": DISCOVERY_VERSION,
        "request_id": request_id,
        "instance_id": instance,
        "service_name": name,
        "http_port": port,
        "requires_pairing": True,
        "features": ["discovery"],
    }


def test_loopback_discovery_finds_service() -> None:
    with DiscoveryResponder(
        http_port=8123,
        service_name="Studio PC",
        bind_address="127.0.0.1",
        discovery_port=0,
    ) as responder:
        services = discover_services(
            ["127.0.0.1"], discovery_port=responder.port, timeout=0.6
        )

    assert len(services) == 1
    service = services[0]
    assert service.service_name == "Studio PC"
    assert service.address == "127.0.0.1"
    assert service.http_port == 8123
    assert service.url == "http://127.0.0.1:8123/"
    assert service.protocol_version == DISCOVERY_VERSION
    assert service.requires_pairing is True


def test_response_is_bounded_and_contains_no_secrets() -> None:
    with DiscoveryResponder(
        http_port=8000,
        service_name="Lab host",
        bind_address="127.0.0.1",
        discovery_port=0,
    ) as responder:
        request_id = "test-request-1"
        request = build_discovery_request(request_id)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(2.0)
            client.sendto(request, ("127.0.0.1", responder.port))
            data, _ = client.recvfrom(MAX_RESPONSE_BYTES + 1)

    assert len(data) <= MAX_RESPONSE_BYTES
    payload = json.loads(data.decode("utf-8"))
    assert payload["protocol"] == DISCOVERY_PROTOCOL
    assert payload["protocol_version"] == DISCOVERY_VERSION
    assert payload["request_id"] == request_id
    assert payload["http_port"] == 8000
    assert payload["requires_pairing"] is True
    assert set(payload) == {
        "protocol",
        "protocol_version",
        "request_id",
        "instance_id",
        "service_name",
        "http_port",
        "requires_pairing",
        "features",
    }
    text = data.decode("utf-8")
    for forbidden in ("Bearer", "token", "cookie", "pairing_code", "secret", "objects"):
        assert forbidden not in text


def test_invalid_requests_are_ignored_and_server_stays_alive() -> None:
    with DiscoveryResponder(
        http_port=8000, bind_address="127.0.0.1", discovery_port=0
    ) as responder, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(0.4)
        target = ("127.0.0.1", responder.port)
        invalid_payloads = [
            b"not-json",
            json.dumps({"protocol": "wrong", "protocol_version": 1}).encode(),
            json.dumps(
                {"protocol": DISCOVERY_PROTOCOL, "protocol_version": 99, "type": "discover"}
            ).encode(),
            json.dumps(
                {
                    "protocol": DISCOVERY_PROTOCOL,
                    "protocol_version": DISCOVERY_VERSION,
                    "type": "discover",
                    "request_id": "has spaces in it",
                }
            ).encode(),
            b"x" * 2000,
        ]
        for payload in invalid_payloads:
            client.sendto(payload, target)
        with pytest.raises(socket.timeout):
            client.recvfrom(MAX_RESPONSE_BYTES + 1)

        request_id = "alive-check-1"
        client.settimeout(2.0)
        client.sendto(build_discovery_request(request_id), target)
        data, _ = client.recvfrom(MAX_RESPONSE_BYTES + 1)
    assert json.loads(data.decode("utf-8"))["request_id"] == request_id


def test_parse_rejects_mismatched_versions_and_untrusted_sources() -> None:
    request_id = "expected-request"
    valid = _response_payload(request_id, instance="instance-1", name="PC", port=8000)
    encoded = json.dumps(valid).encode("utf-8")
    source = ("127.0.0.1", 50000)

    assert parse_discovery_response(encoded, request_id=request_id, source=source) is not None
    assert (
        parse_discovery_response(
            json.dumps({**valid, "request_id": "other"}).encode(),
            request_id=request_id,
            source=source,
        )
        is None
    )
    assert (
        parse_discovery_response(
            json.dumps({**valid, "protocol_version": 99}).encode(),
            request_id=request_id,
            source=source,
        )
        is None
    )
    assert (
        parse_discovery_response(
            json.dumps({**valid, "protocol": "other"}).encode(),
            request_id=request_id,
            source=source,
        )
        is None
    )
    assert (
        parse_discovery_response(
            json.dumps({**valid, "requires_pairing": False}).encode(),
            request_id=request_id,
            source=source,
        )
        is None
    )
    assert (
        parse_discovery_response(
            json.dumps({**valid, "http_port": 70000}).encode(),
            request_id=request_id,
            source=source,
        )
        is None
    )
    assert (
        parse_discovery_response(
            json.dumps({**valid, "service_name": "bad\nname"}).encode(),
            request_id=request_id,
            source=source,
        )
        is None
    )
    assert (
        parse_discovery_response(
            json.dumps({**valid, "instance_id": "bad id"}).encode(),
            request_id=request_id,
            source=source,
        )
        is None
    )
    assert (
        parse_discovery_response(b"x" * (MAX_RESPONSE_BYTES + 1), request_id=request_id, source=source)
        is None
    )
    assert parse_discovery_response(b"\xff\xfe", request_id=request_id, source=source) is None
    assert parse_discovery_response(encoded, request_id=request_id, source=("8.8.8.8", 50000)) is None
    assert parse_discovery_response(encoded, request_id=request_id, source=("localhost", 50000)) is None


def test_duplicate_and_malicious_responses_are_filtered() -> None:
    def handler(sock: socket.socket, address, data: bytes) -> None:
        try:
            request_id = json.loads(data.decode("utf-8"))["request_id"]
        except (KeyError, ValueError, UnicodeDecodeError):
            return

        def send(payload: dict) -> None:
            sock.sendto(json.dumps(payload).encode("utf-8"), address)

        send(_response_payload(request_id, instance="instance-a", name="Alpha", port=9001))
        send(_response_payload(request_id, instance="instance-a", name="Alpha", port=9001))
        send(_response_payload(request_id, instance="instance-b", name="Beta", port=9002))
        send(
            {
                **_response_payload(request_id, instance="instance-c", name="Gamma", port=9003),
                "request_id": "wrong-request-id",
            }
        )
        sock.sendto(b"not-json", address)
        sock.sendto(b"y" * 2000, address)
        send(_response_payload(request_id, instance="instance-d", name="Delta", port=9004))

    with _FakeUdpResponder(handler) as fake:
        services = discover_services(["127.0.0.1"], discovery_port=fake.port, timeout=1.0)

    assert [(service.address, service.http_port) for service in services] == [
        ("127.0.0.1", 9001),
        ("127.0.0.1", 9002),
        ("127.0.0.1", 9004),
    ]
    assert [service.instance_id for service in services] == [
        "instance-a",
        "instance-b",
        "instance-d",
    ]


def test_discovery_request_contains_no_credentials() -> None:
    captured: list[bytes] = []

    def handler(sock: socket.socket, address, data: bytes) -> None:
        captured.append(data)
        request_id = json.loads(data.decode("utf-8"))["request_id"]
        sock.sendto(
            json.dumps(
                _response_payload(request_id, instance="instance-1", name="PC", port=8000)
            ).encode("utf-8"),
            address,
        )

    with _FakeUdpResponder(handler) as fake:
        services = discover_services(["127.0.0.1"], discovery_port=fake.port, timeout=0.6)

    assert len(services) == 1
    assert captured, "the client must send exactly one bounded JSON probe"
    payload = json.loads(captured[0].decode("utf-8"))
    assert set(payload) == {"protocol", "protocol_version", "type", "request_id"}
    text = captured[0].decode("utf-8")
    for forbidden in ("Bearer", "token", "cookie", "pairing_code", "secret"):
        assert forbidden not in text


def test_discovery_timeout_returns_quickly_without_hanging() -> None:
    unused_port = _free_udp_port()
    started = time.monotonic()
    services = discover_services(["127.0.0.1"], discovery_port=unused_port, timeout=0.3)
    elapsed = time.monotonic() - started

    assert services == []
    assert elapsed < 2.0


def test_udp_port_conflict_is_reported_and_released_after_close() -> None:
    responder = DiscoveryResponder(
        http_port=8000, bind_address="127.0.0.1", discovery_port=0
    )
    responder.start()
    port = responder.port
    try:
        with pytest.raises(DiscoveryError, match="bind"):
            DiscoveryResponder(
                http_port=8000, bind_address="127.0.0.1", discovery_port=port
            )
    finally:
        responder.close()

    with DiscoveryResponder(
        http_port=8001, bind_address="127.0.0.1", discovery_port=port
    ) as rebound:
        assert rebound.port == port


def test_rate_limiter_is_window_based_and_bounded() -> None:
    now = [100.0]

    def clock() -> float:
        return now[0]

    limiter = _RateLimiter(max_requests=2, window_seconds=5.0, max_sources=3, clock=clock)
    assert limiter.allow("10.0.0.1")
    assert limiter.allow("10.0.0.1")
    assert not limiter.allow("10.0.0.1")

    now[0] += 5.0
    assert limiter.allow("10.0.0.1")
    assert limiter.allow("10.0.0.2")
    assert limiter.allow("10.0.0.3")
    assert limiter.allow("10.0.0.4")
    assert limiter.allow("10.0.0.5")
    assert len(limiter._sources) <= 3


def test_validation_helpers_reject_unsafe_values() -> None:
    assert validate_target_address("255.255.255.255") == "255.255.255.255"
    assert validate_target_address("192.168.1.255") == "192.168.1.255"
    with pytest.raises(DiscoveryError):
        validate_target_address("example.com")
    assert validate_interface_address("127.0.0.1") == "127.0.0.1"
    assert validate_interface_address("192.168.1.20") == "192.168.1.20"
    with pytest.raises(DiscoveryError):
        validate_interface_address("8.8.8.8")
    assert validate_discovery_bind("0.0.0.0") == "0.0.0.0"
    with pytest.raises(DiscoveryError):
        validate_discovery_bind("8.8.8.8")
    with pytest.raises(DiscoveryError):
        validate_service_name("x" * 65)
    with pytest.raises(DiscoveryError):
        validate_service_name("bad\nname")


def test_serve_discovery_is_opt_in_and_validated() -> None:
    parser = build_parser()

    disabled = parser.parse_args(["serve"])
    assert _discovery_config(disabled, "127.0.0.1") is None

    loopback = parser.parse_args(["serve", "--discover"])
    with pytest.raises(argparse.ArgumentTypeError):
        _discovery_config(loopback, "127.0.0.1")

    enabled = parser.parse_args(
        [
            "serve",
            "--discover",
            "--host",
            "192.168.1.5",
            "--port",
            "8123",
            "--discover-port",
            "47900",
            "--service-name",
            "Lab PC",
        ]
    )
    assert _discovery_config(enabled, "192.168.1.5") == {
        "port": 47900,
        "bind": "0.0.0.0",
        "service_name": "Lab PC",
    }

    with pytest.raises(argparse.ArgumentTypeError):
        _discovery_config(
            parser.parse_args(
                ["serve", "--discover", "--host", "192.168.1.5", "--discover-port", "0"]
            ),
            "192.168.1.5",
        )
    with pytest.raises(argparse.ArgumentTypeError):
        _discovery_config(
            parser.parse_args(
                [
                    "serve",
                    "--discover",
                    "--host",
                    "192.168.1.5",
                    "--discover-bind",
                    "8.8.8.8",
                ]
            ),
            "192.168.1.5",
        )
    with pytest.raises(argparse.ArgumentTypeError):
        _discovery_config(parser.parse_args(["serve", "--discover-port", "47900"]), "127.0.0.1")


def test_cli_discover_timeout_prints_manual_fallback(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "lan_distribution_lab.__main__.discover_services", lambda *args, **kwargs: []
    )
    arguments = build_parser().parse_args(["discover", "--port", "47800", "--timeout", "0.5"])

    _run_discover_command(arguments)

    captured = capsys.readouterr()
    assert "No LAN Distribution Lab service discovered" in captured.out
    assert "Fallback:" in captured.out
    assert "http://<LAN-IP>:<HTTP-PORT>/" in captured.out


def test_cli_discover_json_output(monkeypatch, capsys) -> None:
    service = DiscoveredService(
        service_name="Office PC",
        address="192.168.1.20",
        http_port=8000,
        protocol_version=DISCOVERY_VERSION,
        instance_id="instance-1",
    )
    monkeypatch.setattr(
        "lan_distribution_lab.__main__.discover_services",
        lambda *args, **kwargs: [service],
    )
    arguments = build_parser().parse_args(["discover", "--json"])

    _run_discover_command(arguments)

    payload = json.loads(capsys.readouterr().out)
    assert payload == [service.to_dict()]
    assert payload[0]["url"] == "http://192.168.1.20:8000/"
    assert payload[0]["requires_pairing"] is True


def test_cli_discover_reports_errors(monkeypatch, capsys) -> None:
    def fail(*args, **kwargs):
        raise DiscoveryError("socket unavailable")

    monkeypatch.setattr("lan_distribution_lab.__main__.discover_services", fail)
    arguments = build_parser().parse_args(["discover"])

    with pytest.raises(SystemExit) as error:
        _run_discover_command(arguments)

    assert error.value.code == 1
    assert "Discovery failed: socket unavailable" in capsys.readouterr().err
