"""UDP LAN discovery for the LAN Distribution Lab service.

Discovery only locates candidate HTTP endpoints.  It is deliberately *not* an
authentication mechanism: a discovered address is untrusted, the client derives
it from the actual UDP source address (never from a URL in the payload), only
private/loopback IPv4 sources are accepted, and the normal one-time pairing flow
is still required before any file operation.

Wire format: one bounded UTF-8 JSON datagram per request/response.

request::

    {"protocol": "lan-distribution-lab.discovery", "protocol_version": 1,
     "type": "discover", "request_id": "<1-64 safe chars>"}

response::

    {"protocol": "...", "protocol_version": 1, "request_id": "<echo>",
     "instance_id": "<non-secret id>", "service_name": "...",
     "http_port": 8000, "requires_pairing": true, "features": ["discovery"]}

The responder is opt-in, binds only the UDP socket the caller asks for, applies
per-source rate limits and ignores malformed or oversized datagrams instead of
answering them.
"""

from __future__ import annotations

import ipaddress
import json
import re
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Sequence

DISCOVERY_PROTOCOL = "lan-distribution-lab.discovery"
DISCOVERY_VERSION = 1
DEFAULT_DISCOVERY_PORT = 47800
DEFAULT_SERVICE_NAME = "LAN Distribution Lab"

MAX_REQUEST_BYTES = 1024
MAX_RESPONSE_BYTES = 1024
MAX_SERVICE_NAME = 64
MAX_IDENTIFIER = 64
MAX_RESPONSES = 32
MAX_PACKETS = 256

RATE_LIMIT_WINDOW_SECONDS = 5.0
RATE_LIMIT_MAX_REQUESTS = 30
RATE_LIMIT_MAX_SOURCES = 256

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class DiscoveryError(RuntimeError):
    """A safe, user-facing discovery configuration or socket error."""


@dataclass(frozen=True, slots=True)
class DiscoveredService:
    service_name: str
    address: str
    http_port: int
    protocol_version: int
    instance_id: str
    requires_pairing: bool = True

    @property
    def url(self) -> str:
        return f"http://{self.address}:{self.http_port}/"

    def to_dict(self) -> dict[str, object]:
        return {
            "service_name": self.service_name,
            "address": self.address,
            "http_port": self.http_port,
            "url": self.url,
            "protocol_version": self.protocol_version,
            "instance_id": self.instance_id,
            "requires_pairing": self.requires_pairing,
        }


def _is_private_or_loopback(address: str) -> bool:
    try:
        parsed = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        return False
    return parsed.is_private or parsed.is_loopback


def _is_safe_identifier(value: object) -> bool:
    return isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value) is not None


def _is_safe_service_name(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if not 1 <= len(value) <= MAX_SERVICE_NAME:
        return False
    return not any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def validate_service_name(value: str) -> str:
    name = value.strip()
    if not _is_safe_service_name(name):
        raise DiscoveryError(
            f"Service name must be 1-{MAX_SERVICE_NAME} printable characters."
        )
    return name


def validate_discovery_bind(value: str) -> str:
    """Allow only 0.0.0.0, loopback or private IPv4 as the UDP bind address."""
    try:
        parsed = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as error:
        raise DiscoveryError("--discover-bind must be an IPv4 address.") from error
    if parsed.is_unspecified:
        return str(parsed)
    if not (parsed.is_private or parsed.is_loopback):
        raise DiscoveryError("--discover-bind must be 0.0.0.0, loopback or private IPv4.")
    return str(parsed)


def validate_target_address(value: str) -> str:
    """Accept only an IPv4 literal as a discovery target (broadcast or unicast)."""
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError as error:
        raise DiscoveryError(
            "Discovery target must be an IPv4 address, for example 192.168.1.255."
        ) from error


def validate_interface_address(value: str) -> str:
    try:
        parsed = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as error:
        raise DiscoveryError("--interface must be a loopback or private IPv4 address.") from error
    if not (parsed.is_private or parsed.is_loopback):
        raise DiscoveryError("--interface must be a loopback or private IPv4 address.")
    return str(parsed)


def build_discovery_request(request_id: str) -> bytes:
    if not _is_safe_identifier(request_id):
        raise DiscoveryError("Discovery request id is invalid.")
    payload = {
        "protocol": DISCOVERY_PROTOCOL,
        "protocol_version": DISCOVERY_VERSION,
        "type": "discover",
        "request_id": request_id,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def parse_discovery_response(
    data: bytes,
    *,
    request_id: str,
    source: tuple[str, int],
) -> DiscoveredService | None:
    """Validate one datagram; return a service or ``None`` for anything unsafe."""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_RESPONSE_BYTES:
        return None
    if source[0] is None or source[1] is None:
        return None
    if not _is_private_or_loopback(source[0]):
        return None
    try:
        payload = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("protocol") != DISCOVERY_PROTOCOL:
        return None
    if payload.get("protocol_version") != DISCOVERY_VERSION:
        return None
    if payload.get("request_id") != request_id:
        return None
    if payload.get("requires_pairing") is not True:
        return None
    http_port = payload.get("http_port")
    if type(http_port) is not int or not 1 <= http_port <= 65535:
        return None
    service_name = payload.get("service_name")
    if not _is_safe_service_name(service_name):
        return None
    instance_id = payload.get("instance_id")
    if not _is_safe_identifier(instance_id):
        return None
    return DiscoveredService(
        service_name=str(service_name),
        address=source[0],
        http_port=int(http_port),
        protocol_version=DISCOVERY_VERSION,
        instance_id=str(instance_id),
        requires_pairing=True,
    )


class _RateLimiter:
    """Bounded per-source request limiter so discovery cannot be used to flood."""

    def __init__(
        self,
        *,
        max_requests: int = RATE_LIMIT_MAX_REQUESTS,
        window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
        max_sources: int = RATE_LIMIT_MAX_SOURCES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._max_sources = max_sources
        self._clock = clock
        self._lock = threading.Lock()
        self._sources: dict[str, list[float]] = {}

    def allow(self, source: str) -> bool:
        with self._lock:
            now = self._clock()
            state = self._sources.get(source)
            if state is None or now - state[0] >= self._window:
                if state is None and len(self._sources) >= self._max_sources:
                    self._prune(now)
                    if len(self._sources) >= self._max_sources:
                        return False
                self._sources[source] = [now, 1.0]
                return True
            if state[1] >= self._max_requests:
                return False
            state[1] += 1.0
            return True

    def _prune(self, now: float) -> None:
        expired = [
            source
            for source, (started, _count) in self._sources.items()
            if now - started >= self._window
        ]
        for source in expired:
            del self._sources[source]
        while len(self._sources) >= self._max_sources:
            oldest = min(self._sources, key=lambda key: self._sources[key][0])
            del self._sources[oldest]


class DiscoveryResponder:
    """Answer valid UDP discovery requests for one HTTP service endpoint."""

    def __init__(
        self,
        *,
        http_port: int,
        service_name: str = DEFAULT_SERVICE_NAME,
        bind_address: str = "0.0.0.0",
        discovery_port: int = DEFAULT_DISCOVERY_PORT,
        instance_id: str | None = None,
        rate_limiter: _RateLimiter | None = None,
    ) -> None:
        if type(http_port) is not int or not 1 <= http_port <= 65535:
            raise DiscoveryError("HTTP port for discovery must be between 1 and 65535.")
        if type(discovery_port) is not int or not 0 <= discovery_port <= 65535:
            raise DiscoveryError("Discovery port must be between 1 and 65535.")
        self._bind_address = validate_discovery_bind(bind_address)
        self._service_name = validate_service_name(service_name)
        self._http_port = http_port
        self._instance_id = instance_id or str(uuid.uuid4())
        if not _is_safe_identifier(self._instance_id):
            raise DiscoveryError("Discovery instance id is invalid.")
        self._rate_limiter = rate_limiter or _RateLimiter()
        self._stop = threading.Event()
        self._bound_port = discovery_port
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.bind((self._bind_address, discovery_port))
        except OSError as error:
            self._socket.close()
            reason = error.strerror or str(error)
            raise DiscoveryError(
                f"Could not bind UDP discovery port {discovery_port} on "
                f"{self._bind_address}: {reason}"
            ) from error
        self.port = int(self._socket.getsockname()[1])
        self._thread: threading.Thread | None = None

    @property
    def bind_address(self) -> str:
        return self._bind_address

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._serve_forever, name="udp-discovery", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._socket.close()
        except OSError:
            pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3)
        self._thread = None

    def __enter__(self) -> "DiscoveryResponder":
        self.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _serve_forever(self) -> None:
        try:
            self._socket.settimeout(0.25)
            while not self._stop.is_set():
                try:
                    data, address = self._socket.recvfrom(MAX_REQUEST_BYTES + 1)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    continue
                response = self._handle_request(data, address)
                if response is None:
                    continue
                try:
                    self._socket.sendto(response, address)
                except OSError:
                    continue
        except OSError:
            # Closing the socket from another thread wakes the loop here; the
            # responder is stopping, so there is nothing left to report.
            return

    def _handle_request(self, data: bytes, address: tuple[str, int]) -> bytes | None:
        if not isinstance(data, (bytes, bytearray)):
            return None
        if len(data) > MAX_REQUEST_BYTES:
            return None
        try:
            payload = json.loads(bytes(data).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("protocol") != DISCOVERY_PROTOCOL:
            return None
        if payload.get("protocol_version") != DISCOVERY_VERSION:
            return None
        if payload.get("type") != "discover":
            return None
        request_id = payload.get("request_id")
        if not _is_safe_identifier(request_id):
            return None
        source_ip = address[0]
        if not _is_private_or_loopback(source_ip):
            return None
        if not self._rate_limiter.allow(source_ip):
            return None
        response = {
            "protocol": DISCOVERY_PROTOCOL,
            "protocol_version": DISCOVERY_VERSION,
            "request_id": request_id,
            "instance_id": self._instance_id,
            "service_name": self._service_name,
            "http_port": self._http_port,
            "requires_pairing": True,
            "features": ["discovery"],
        }
        encoded = json.dumps(response, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(encoded) > MAX_RESPONSE_BYTES:
            return None
        return encoded


def discover_services(
    targets: Sequence[str] | None = None,
    *,
    discovery_port: int = DEFAULT_DISCOVERY_PORT,
    timeout: float = 2.0,
    interface: str | None = None,
    max_responses: int = MAX_RESPONSES,
) -> list[DiscoveredService]:
    """Send one discovery probe per target and collect validated responses.

    Targets are IPv4 literals (broadcast addresses such as 192.168.1.255 or
    unicast hosts).  The returned services use the UDP source address, are
    deduplicated by instance id and sorted by (address, HTTP port, name).  The
    call is bounded by ``timeout`` seconds and never sends credentials.
    """
    if type(discovery_port) is not int or not 1 <= discovery_port <= 65535:
        raise DiscoveryError("Discovery port must be between 1 and 65535.")
    if not 0 < timeout <= 30:
        raise DiscoveryError("Discovery timeout must be greater than 0 and at most 30 seconds.")
    if max_responses < 1:
        raise DiscoveryError("max_responses must be positive.")
    address_targets = [validate_target_address(target) for target in (targets or ["255.255.255.255"])]
    if not address_targets:
        raise DiscoveryError("At least one discovery target is required.")
    bind_address = validate_interface_address(interface) if interface else ""
    request_id = secrets.token_hex(16)
    request = build_discovery_request(request_id)
    deadline = time.monotonic() + timeout
    services: list[DiscoveredService] = []
    seen: set[tuple[str, str, int]] = set()
    packets = 0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        try:
            sock.bind((bind_address, 0))
        except OSError as error:
            raise DiscoveryError(f"Could not bind the discovery client socket: {error.strerror or error}") from error
        for target in address_targets:
            try:
                sock.sendto(request, (target, discovery_port))
            except OSError:
                # One unreachable target (e.g. a disabled NIC broadcast) must not
                # abort discovery for the remaining targets.
                continue
        while (
            time.monotonic() < deadline
            and len(services) < max_responses
            and packets < MAX_PACKETS
        ):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(min(0.2, remaining))
            try:
                data, source = sock.recvfrom(MAX_RESPONSE_BYTES + 1)
            except socket.timeout:
                continue
            except OSError:
                # Oversized or otherwise unreadable datagrams are discarded; a
                # malicious packet must not terminate the bounded discovery.
                packets += 1
                continue
            packets += 1
            service = parse_discovery_response(
                data, request_id=request_id, source=(source[0], source[1])
            )
            if service is None:
                continue
            key = (service.instance_id, service.address, service.http_port)
            if key in seen:
                continue
            seen.add(key)
            services.append(service)
    finally:
        sock.close()
    services.sort(key=lambda service: (service.address, service.http_port, service.service_name))
    return services
