"""A small, standard-library resumable download client for immutable file objects.

The client intentionally keeps Bearer credentials in process memory only.  The
adjacent resume state contains object/version information, never a credential.
Every request is pinned to the configured server: redirects are never followed,
so a hostile or misconfigured peer cannot move a credential-bearing request to
another host, port or scheme.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import socket
import stat
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


CHUNK_BYTES = 64 * 1024
MAX_METADATA_BYTES = 64 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CONTENT_RANGE_RE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)")
# urllib's redirect handler replays the original request headers (including
# ``Authorization``) at the target location.  The CLI therefore refuses every
# redirect instead of trusting that it points at the same host, port or scheme.
_REDIRECT_STATUSES = frozenset({300, 301, 302, 303, 305, 307, 308})
_REDIRECT_MESSAGE = (
    "Server returned an HTTP {status} redirect; the client refuses to forward "
    "credentials, pairing data or content requests to another location."
)


class _RefuseRedirects(HTTPRedirectHandler):
    """Leave every 3xx response unhandled so the caller can reject it safely."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_DEFAULT_OPENER = build_opener(_RefuseRedirects())


def _safe_opener(request: Request, *, timeout: float) -> Any:
    """Default opener used by both metadata/content and pairing requests."""
    return _DEFAULT_OPENER.open(request, timeout=timeout)


class DownloadError(RuntimeError):
    """A safe, user-facing download error which never includes credentials."""


class NetworkError(DownloadError):
    """A transient connection/timeout error; state has been saved up to a chunk boundary."""


class ProtocolError(DownloadError):
    """The peer response did not prove it is safe to write or append data."""


class LocalStorageError(DownloadError):
    """The local target, partial file or state file cannot be safely updated."""


class IntegrityError(DownloadError):
    """The complete local bytes do not match the immutable object digest."""


class ResumeRestartRequired(DownloadError):
    """A previous partial object cannot be safely appended to."""


class _Response(Protocol):
    status: int
    headers: Any

    def read(self, amount: int = -1) -> bytes: ...

    def close(self) -> None: ...


Opener = Callable[..., _Response]
ProgressReporter = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class FileMetadata:
    file_id: str
    etag: str
    size_bytes: int
    content_sha256: str


@dataclass(slots=True)
class ResumeState:
    schema_version: int
    server: str
    file_id: str
    etag: str
    size_bytes: int
    content_sha256: str
    downloaded_bytes: int


@dataclass(frozen=True, slots=True)
class DownloadResult:
    output_path: Path
    size_bytes: int
    content_sha256: str
    resumed_bytes: int


class ResumableDownloader:
    """Download immutable objects without ever appending across a version boundary."""

    def __init__(
        self,
        server: str,
        token: str,
        *,
        timeout_seconds: float = 15.0,
        retries: int = 2,
        opener: Opener = _safe_opener,
        progress: ProgressReporter | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.server = normalize_server(server)
        if not token or not token.strip():
            raise DownloadError("An access token is required.")
        if timeout_seconds <= 0:
            raise DownloadError("Timeout must be greater than zero.")
        if retries < 0:
            raise DownloadError("Retries cannot be negative.")
        self._token = token.strip()
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self._opener = opener
        self._progress = progress or _console_progress
        self._sleep = sleep

    def download(self, file_id: str, output_path: Path) -> DownloadResult:
        """Download or safely resume ``file_id`` into a new, atomically published target."""
        file_id = _validate_file_id(file_id)
        output = _validate_output_path(output_path)
        metadata = self._fetch_metadata(file_id)
        part_path, state_path = resume_artifact_paths(output)
        if output.exists():
            if _completed_output_matches(output, state_path, self.server, metadata):
                _quarantine_artifacts(part_path, state_path)
                return DownloadResult(output, metadata.size_bytes, metadata.content_sha256, metadata.size_bytes)
            if _matches_metadata(output, metadata):
                return DownloadResult(output, metadata.size_bytes, metadata.content_sha256, metadata.size_bytes)
        state = self._prepare_state(output, part_path, state_path, metadata)
        # Only bytes that already existed on disk when this run started can be
        # reported as reused; a forced restart (200/416/version change) resets
        # this counter so the statistics describe the successful download only.
        resumed_bytes = state.downloaded_bytes
        restart_count = 0
        retry_count = 0

        if _partial_covers_object(part_path, state, metadata):
            # A previous run may have written every byte and then stopped before
            # its final SHA-256 check or publication.  Verify and publish that
            # data instead of re-requesting the whole object.
            self._verify_and_publish(output, part_path, state_path, state, metadata)
            self._progress(metadata.size_bytes, metadata.size_bytes)
            return DownloadResult(
                output_path=output,
                size_bytes=metadata.size_bytes,
                content_sha256=metadata.content_sha256,
                resumed_bytes=resumed_bytes,
            )

        while True:
            try:
                self._receive_response(part_path, state_path, state, metadata)
                self._verify_and_publish(output, part_path, state_path, state, metadata)
                self._progress(metadata.size_bytes, metadata.size_bytes)
                return DownloadResult(
                    output_path=output,
                    size_bytes=metadata.size_bytes,
                    content_sha256=metadata.content_sha256,
                    resumed_bytes=resumed_bytes,
                )
            except ResumeRestartRequired:
                if restart_count >= 1:
                    raise ProtocolError("The server did not provide a safe resumable response.") from None
                _quarantine_artifacts(part_path, state_path)
                metadata = self._fetch_metadata(file_id)
                state = _new_state(self.server, metadata)
                _write_state(state_path, state)
                restart_count += 1
                retry_count = 0
                resumed_bytes = 0
            except NetworkError:
                if retry_count >= self.retries:
                    raise
                retry_count += 1
                self._sleep(0.25 * retry_count)

    def _fetch_metadata(self, file_id: str) -> FileMetadata:
        for attempt in range(self.retries + 1):
            try:
                return self._fetch_metadata_once(file_id)
            except NetworkError:
                if attempt == self.retries:
                    raise
                self._sleep(0.25 * (attempt + 1))
        raise AssertionError("unreachable")

    def _fetch_metadata_once(self, file_id: str) -> FileMetadata:
        response = self._open("GET", f"/api/files/{quote(file_id, safe='')}")
        try:
            if response.status != 200:
                raise _http_error(response, "Could not read file metadata.")
            body = _read_limited(response, MAX_METADATA_BYTES)
        finally:
            response.close()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise ProtocolError("File metadata response is invalid JSON.") from error
        try:
            returned_id = payload["file_id"]
            etag = payload["etag"]
            size_bytes = payload["size_bytes"]
            content_sha256 = payload["content_sha256"]
        except (KeyError, TypeError) as error:
            raise ProtocolError("File metadata response is incomplete.") from error
        if returned_id != file_id or not isinstance(size_bytes, int) or size_bytes < 0:
            raise ProtocolError("File metadata response has an invalid identity or size.")
        if not isinstance(content_sha256, str) or not _SHA256_RE.fullmatch(content_sha256):
            raise ProtocolError("File metadata response has an invalid SHA-256.")
        expected_etag = f'"sha256:{content_sha256}"'
        if etag != expected_etag:
            raise ProtocolError("File metadata response has an invalid strong ETag.")
        return FileMetadata(file_id, etag, size_bytes, content_sha256)

    def _prepare_state(
        self,
        output: Path,
        part_path: Path,
        state_path: Path,
        metadata: FileMetadata,
    ) -> ResumeState:
        _ensure_regular_or_missing(output, "output target")
        _ensure_regular_or_missing(part_path, "partial file")
        _ensure_regular_or_missing(state_path, "resume state")

        if output.exists():
            raise LocalStorageError("The output path already exists; refusing to overwrite it.")

        part_exists = part_path.exists()
        state_exists = state_path.exists()
        if part_exists != state_exists:
            _quarantine_artifacts(part_path, state_path)
            part_exists = state_exists = False

        if part_exists:
            state = _load_state_or_none(state_path)
            if state is None or not _state_matches_metadata(state, self.server, metadata):
                _quarantine_artifacts(part_path, state_path)
            elif part_path.stat().st_size != state.downloaded_bytes:
                _quarantine_artifacts(part_path, state_path)
            else:
                return state

        state = _new_state(self.server, metadata)
        _write_state(state_path, state)
        return state

    def _receive_response(
        self,
        part_path: Path,
        state_path: Path,
        state: ResumeState,
        metadata: FileMetadata,
    ) -> None:
        offset = state.downloaded_bytes
        headers: dict[str, str] = {}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            headers["If-Range"] = state.etag
        response = self._open("GET", f"/api/files/{quote(metadata.file_id, safe='')}/content", headers=headers)
        try:
            if response.status == 200 and offset:
                raise ResumeRestartRequired("The object version changed while resuming.")
            if response.status == 416:
                raise ResumeRestartRequired("The saved partial range is no longer satisfiable.")
            expected_bytes = _validate_download_response(response, offset, metadata)
            self._append_response(response, part_path, state_path, state, expected_bytes)
        finally:
            response.close()

    def _append_response(
        self,
        response: _Response,
        part_path: Path,
        state_path: Path,
        state: ResumeState,
        expected_bytes: int,
    ) -> None:
        try:
            with _open_part(part_path, state.downloaded_bytes) as destination:
                remaining = expected_bytes
                while remaining:
                    chunk = _read_chunk(response, min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise NetworkError("Connection ended before the promised byte count.")
                    _write_chunk(destination, chunk)
                    state.downloaded_bytes += len(chunk)
                    remaining -= len(chunk)
                    _write_state(state_path, state)
                    self._progress(state.downloaded_bytes, state.size_bytes)
                if _read_chunk(response, 1):
                    raise ProtocolError("Download response exceeded its declared length.")
        except DownloadError:
            raise
        except OSError as error:
            raise _local_write_error(error) from error

    def _verify_and_publish(
        self,
        output: Path,
        part_path: Path,
        state_path: Path,
        state: ResumeState,
        metadata: FileMetadata,
    ) -> None:
        if state.downloaded_bytes != metadata.size_bytes or not part_path.exists():
            raise ProtocolError("Download did not reach the expected object size.")
        try:
            if part_path.stat().st_size != metadata.size_bytes:
                raise ProtocolError("Partial file size does not match saved download state.")
            actual_hash = _hash_file(part_path)
        except OSError as error:
            raise _local_write_error(error) from error
        if actual_hash != metadata.content_sha256:
            _quarantine_artifacts(part_path, state_path)
            raise IntegrityError("Downloaded bytes failed SHA-256 verification; partial data was isolated.")
        try:
            # hard-link creation is atomic and fails if a user-created target appeared
            # after validation; unlinking the adjacent .part then publishes the name.
            os.link(part_path, output)
            part_path.unlink()
            state_path.unlink(missing_ok=True)
        except FileExistsError as error:
            raise LocalStorageError("The output path appeared during download; it was not overwritten.") from error
        except OSError as error:
            raise _local_write_error(error) from error

    def _open(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        include_auth: bool = True,
    ) -> _Response:
        request_headers = {"Accept": "application/json"}
        if include_auth:
            request_headers["Authorization"] = f"Bearer {self._token}"
        if headers:
            request_headers.update(headers)
        request = Request(f"{self.server}{path}", data=data, headers=request_headers, method=method)
        try:
            response = self._opener(request, timeout=self.timeout_seconds)
        except HTTPError as error_response:
            response = error_response
        except (URLError, socket.timeout, TimeoutError, OSError) as error:
            raise NetworkError("Network request failed or timed out.") from error
        _reject_redirect(response)
        return response


def _reject_redirect(response: _Response) -> None:
    """Fail closed instead of replaying credentials or pairing data elsewhere."""
    status = getattr(response, "status", None)
    if status in _REDIRECT_STATUSES:
        message = _REDIRECT_MESSAGE.format(status=status)
        try:
            response.close()
        finally:
            raise ProtocolError(message)


def normalize_server(value: str) -> str:
    """Accept only a bare HTTP(S) origin, never a token-bearing URL."""
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DownloadError("Server must be an absolute http:// or https:// address.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise DownloadError("Server address must not contain credentials, a query, or a fragment.")
    if parsed.path not in {"", "/"}:
        raise DownloadError("Server address must not contain an API path.")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def exchange_cli_pairing_code(
    server: str,
    pairing_code: str,
    *,
    timeout_seconds: float = 15.0,
    device_label: str | None = None,
    opener: Opener = _safe_opener,
) -> str:
    """Obtain a CLI token in memory from a locally prompted one-time code."""
    if timeout_seconds <= 0:
        raise DownloadError("Timeout must be greater than zero.")
    normalized_server = normalize_server(server)
    payload: dict[str, str] = {"pairing_code": pairing_code, "client_kind": "cli"}
    if device_label:
        payload["device_label"] = device_label
    request = Request(
        f"{normalized_server}/api/pairings",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        response = opener(request, timeout=timeout_seconds)
    except HTTPError as error:
        response = error
    except (URLError, socket.timeout, TimeoutError, OSError) as error:
        raise NetworkError("Pairing request failed or timed out.") from error
    _reject_redirect(response)
    try:
        if response.status != 201:
            raise _http_error(response, "Pairing was rejected.")
        body = _read_limited(response, MAX_METADATA_BYTES)
    finally:
        response.close()
    try:
        token = json.loads(body).get("access_token")
    except (AttributeError, json.JSONDecodeError):
        token = None
    if not isinstance(token, str) or not token:
        raise ProtocolError("Pairing response did not contain a usable access token.")
    return token


def resume_artifact_paths(output_path: Path) -> tuple[Path, Path]:
    """Return fixed adjacent paths; state content never controls a filesystem path."""
    return (
        output_path.with_name(f"{output_path.name}.part"),
        output_path.with_name(f"{output_path.name}.part.json"),
    )


def _validate_file_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (AttributeError, ValueError) as error:
        raise DownloadError("file_id must be a UUID.") from error


def _validate_output_path(value: Path) -> Path:
    output = value.expanduser().resolve(strict=False)
    if not output.name or output.exists() and output.is_dir():
        raise LocalStorageError("Output must name a file, not a directory.")
    if not output.parent.is_dir():
        raise LocalStorageError("Output directory does not exist.")
    return output


def _new_state(server: str, metadata: FileMetadata) -> ResumeState:
    return ResumeState(
        schema_version=1,
        server=server,
        file_id=metadata.file_id,
        etag=metadata.etag,
        size_bytes=metadata.size_bytes,
        content_sha256=metadata.content_sha256,
        downloaded_bytes=0,
    )


def _state_matches_metadata(state: ResumeState, server: str, metadata: FileMetadata) -> bool:
    return (
        state.schema_version == 1
        and state.server == server
        and state.file_id == metadata.file_id
        and state.etag == metadata.etag
        and state.size_bytes == metadata.size_bytes
        and state.content_sha256 == metadata.content_sha256
        and 0 <= state.downloaded_bytes <= metadata.size_bytes
    )


def _partial_covers_object(part_path: Path, state: ResumeState, metadata: FileMetadata) -> bool:
    """True when the saved partial already contains the whole object payload."""
    if state.downloaded_bytes != metadata.size_bytes:
        return False
    try:
        return part_path.is_file() and part_path.stat().st_size == metadata.size_bytes
    except OSError as error:
        raise _local_write_error(error) from error


def _completed_output_matches(
    output: Path, state_path: Path, server: str, metadata: FileMetadata
) -> bool:
    state = _load_state_or_none(state_path)
    if state is None or not _state_matches_metadata(state, server, metadata):
        return False
    return _matches_metadata(output, metadata)


def _matches_metadata(output: Path, metadata: FileMetadata) -> bool:
    try:
        return output.stat().st_size == metadata.size_bytes and _hash_file(output) == metadata.content_sha256
    except OSError:
        return False


def _load_state_or_none(path: Path) -> ResumeState | None:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_METADATA_BYTES:
            return None
        payload = json.loads(raw)
        state = ResumeState(**payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(state.schema_version, int) or not isinstance(state.downloaded_bytes, int):
        return None
    if not isinstance(state.size_bytes, int) or not isinstance(state.server, str):
        return None
    if not isinstance(state.file_id, str) or not isinstance(state.etag, str):
        return None
    if not isinstance(state.content_sha256, str) or not _SHA256_RE.fullmatch(state.content_sha256):
        return None
    return state


def _write_state(path: Path, state: ResumeState) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        encoded = json.dumps(asdict(state), sort_keys=True, separators=(",", ":")).encode("utf-8")
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise _local_write_error(error) from error


def _quarantine_artifacts(part_path: Path, state_path: Path) -> None:
    for path in (part_path, state_path):
        if path.exists() or path.is_symlink():
            candidate = path.with_name(f"{path.name}.stale-{uuid.uuid4().hex}")
            try:
                os.replace(path, candidate)
            except OSError as error:
                raise LocalStorageError("Could not isolate unsafe partial download data.") from error


def _ensure_regular_or_missing(path: Path, label: str) -> None:
    if not (path.exists() or path.is_symlink()):
        return
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise LocalStorageError(f"Cannot inspect {label}.") from error
    if not stat.S_ISREG(mode):
        raise LocalStorageError(f"{label.capitalize()} must be a regular file.")


def _open_part(path: Path, offset: int) -> BinaryIO:
    if offset:
        if not path.exists() or path.stat().st_size != offset:
            raise LocalStorageError("Partial file no longer matches saved resume state.")
        return path.open("ab")
    if path.exists():
        if path.stat().st_size != 0:
            raise LocalStorageError("Partial file no longer matches saved resume state.")
        return path.open("ab")
    return path.open("xb")


def _write_chunk(destination: BinaryIO, chunk: bytes) -> None:
    written = destination.write(chunk)
    if written != len(chunk):
        raise OSError("partial local write")
    destination.flush()
    os.fsync(destination.fileno())


def _read_chunk(response: _Response, amount: int) -> bytes:
    try:
        return response.read(amount)
    except (http.client.IncompleteRead, socket.timeout, TimeoutError, OSError) as error:
        raise NetworkError("Connection was interrupted while downloading.") from error


def _read_limited(response: _Response, limit: int) -> bytes:
    data = _read_chunk(response, limit + 1)
    if len(data) > limit:
        raise ProtocolError("Server response was unexpectedly large.")
    return data


def _validate_download_response(response: _Response, offset: int, metadata: FileMetadata) -> int:
    status = response.status
    if status == 416:
        return 0
    if status not in {200, 206}:
        raise _http_error(response, "Download request failed.")
    etag = response.headers.get("ETag")
    if etag != metadata.etag:
        raise ProtocolError("Download response ETag does not match metadata.")
    length = _parse_content_length(response.headers.get("Content-Length"))
    if status == 200:
        if offset:
            return length
        if length != metadata.size_bytes:
            raise ProtocolError("Full download Content-Length does not match metadata.")
        return length

    if not offset:
        raise ProtocolError("Server returned a partial response without a requested range.")
    content_range = response.headers.get("Content-Range")
    match = _CONTENT_RANGE_RE.fullmatch(content_range or "")
    if match is None:
        raise ProtocolError("Partial download response has an invalid Content-Range.")
    start, end, total = (int(value) for value in match.groups())
    if start != offset or end < start or total != metadata.size_bytes or end != total - 1:
        raise ProtocolError("Partial download response does not match the saved resume position.")
    if length != end - start + 1:
        raise ProtocolError("Partial download Content-Length does not match Content-Range.")
    return length


def _parse_content_length(value: str | None) -> int:
    if value is None or not value.isascii() or not value.isdecimal():
        raise ProtocolError("Download response is missing a valid Content-Length.")
    return int(value)


def _http_error(response: _Response, fallback: str) -> DownloadError:
    if response.status == 401:
        return DownloadError("Server rejected the access credential.")
    return DownloadError(f"{fallback} HTTP {response.status}.")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _local_write_error(error: OSError) -> LocalStorageError:
    if getattr(error, "errno", None) == getattr(os, "ENOSPC", 28):
        return LocalStorageError("Insufficient local disk space; the partial download was kept safely.")
    return LocalStorageError("Could not safely write local download data.")


def _console_progress(downloaded: int, total: int) -> None:
    print(f"\rDownloaded {downloaded}/{total} bytes", end="", flush=True)
