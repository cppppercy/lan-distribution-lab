from __future__ import annotations

import errno
import hashlib
import http.client
import http.server
import io
import json
import socket
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from lan_distribution_lab import client as client_module
from lan_distribution_lab.client import (
    DownloadError,
    IntegrityError,
    LocalStorageError,
    NetworkError,
    ProtocolError,
    ResumableDownloader,
    exchange_cli_pairing_code,
    resume_artifact_paths,
)


TOKEN = "secret-cli-token-must-not-be-written"
FILE_ID = str(uuid.UUID("12345678-1234-5678-1234-567812345678"))


class FakeResponse:
    def __init__(
        self,
        status: int,
        body: bytes,
        headers: dict[str, str],
        *,
        interrupt_after: int | None = None,
        timeout_after: int | None = None,
    ) -> None:
        self.status = status
        self.headers = headers
        self._source = io.BytesIO(body)
        self._interrupt_after = interrupt_after
        self._timeout_after = timeout_after
        self._read_bytes = 0

    def read(self, amount: int = -1) -> bytes:
        if self._timeout_after is not None and self._read_bytes >= self._timeout_after:
            raise socket.timeout()
        if self._interrupt_after is not None and self._read_bytes >= self._interrupt_after:
            raise http.client.IncompleteRead(b"", 1)
        if amount < 0:
            amount = len(self._source.getbuffer())
        cap = amount
        if self._interrupt_after is not None:
            cap = min(cap, self._interrupt_after - self._read_bytes)
        if self._timeout_after is not None:
            cap = min(cap, self._timeout_after - self._read_bytes)
        chunk = self._source.read(max(cap, 0))
        self._read_bytes += len(chunk)
        return chunk

    def close(self) -> None:
        pass


class FakeFileServer:
    """A deterministic HTTP-ish transport recording Range and credential headers."""

    def __init__(self, content: bytes, *, interrupt_first_at: int | None = None) -> None:
        self.content = content
        self.file_id = FILE_ID
        self.interrupt_first_at = interrupt_first_at
        self.force_full_on_range_once = False
        self.force_416_on_range_once = False
        self.bad_partial_response = False
        self.timeout_on_content_once = False
        self.requests = []
        self._content_calls = 0

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def etag(self) -> str:
        return f'"sha256:{self.sha256}"'

    def opener(self, request, *, timeout: float):
        self.requests.append(request)
        path = urlsplit(request.full_url).path
        if path == f"/api/files/{self.file_id}":
            return self._metadata_response()
        if path == f"/api/files/{self.file_id}/content":
            return self._content_response(request)
        if path == "/api/pairings":
            return FakeResponse(201, json.dumps({"access_token": TOKEN}).encode(), {"Content-Length": "49"})
        raise AssertionError(path)

    def _metadata_response(self) -> FakeResponse:
        payload = json.dumps(
            {
                "file_id": self.file_id,
                "etag": self.etag,
                "size_bytes": len(self.content),
                "content_sha256": self.sha256,
            }
        ).encode()
        return FakeResponse(200, payload, {"Content-Length": str(len(payload))})

    def _content_response(self, request) -> FakeResponse:
        self._content_calls += 1
        assert request.get_header("Authorization") == f"Bearer {TOKEN}"
        range_header = request.get_header("Range")
        if range_header and self.force_416_on_range_once:
            self.force_416_on_range_once = False
            return FakeResponse(416, b"", {"Content-Range": f"bytes */{len(self.content)}"})
        if range_header and self.force_full_on_range_once:
            self.force_full_on_range_once = False
            return FakeResponse(
                200,
                self.content,
                {"Content-Length": str(len(self.content)), "ETag": self.etag},
            )
        if range_header:
            offset = int(range_header.removeprefix("bytes=").removesuffix("-"))
            body = self.content[offset:]
            headers = {
                "Content-Length": str(len(body)),
                "Content-Range": f"bytes {offset}-{len(self.content) - 1}/{len(self.content)}",
                "ETag": self.etag,
            }
            if self.bad_partial_response:
                headers["Content-Range"] = f"bytes 0-{len(body) - 1}/{len(self.content)}"
            return FakeResponse(206, body, headers)
        headers = {"Content-Length": str(len(self.content)), "ETag": self.etag}
        return FakeResponse(
            200,
            self.content,
            headers,
            interrupt_after=self.interrupt_first_at if self._content_calls == 1 else None,
            timeout_after=0 if self.timeout_on_content_once and self._content_calls == 1 else None,
        )


def _downloader(server: FakeFileServer, *, retries: int = 0) -> ResumableDownloader:
    return ResumableDownloader(
        "http://127.0.0.1:8123",
        TOKEN,
        retries=retries,
        opener=server.opener,
        progress=lambda *_: None,
        sleep=lambda _: None,
    )


def test_full_download_verifies_hash_and_publishes_atomically(tmp_path: Path) -> None:
    server = FakeFileServer(b"A\x00unicode-\xe4\xb8\xad\xe6\x96\x87" * 10_000)
    output = tmp_path / "课程资料.bin"

    result = _downloader(server).download(FILE_ID, output)

    assert output.read_bytes() == server.content
    assert result.content_sha256 == server.sha256
    assert result.resumed_bytes == 0
    assert not resume_artifact_paths(output)[0].exists()
    assert not resume_artifact_paths(output)[1].exists()


def test_interrupted_download_resumes_only_missing_bytes(tmp_path: Path) -> None:
    content = bytes(range(251)) * 600
    server = FakeFileServer(content, interrupt_first_at=777)
    output = tmp_path / "resumed.bin"

    with pytest.raises(NetworkError):
        _downloader(server).download(FILE_ID, output)
    part_path, state_path = resume_artifact_paths(output)
    assert part_path.stat().st_size == 777
    assert json.loads(state_path.read_text()) ["downloaded_bytes"] == 777

    result = _downloader(server).download(FILE_ID, output)

    assert result.resumed_bytes == 777
    assert output.read_bytes() == content
    content_requests = [request for request in server.requests if request.full_url.endswith("/content")]
    assert content_requests[1].get_header("Range") == "bytes=777-"
    assert content_requests[1].get_header("If-range") == server.etag


def test_full_response_to_resume_isolated_then_restarted(tmp_path: Path) -> None:
    original = b"old-content" * 200
    server = FakeFileServer(original, interrupt_first_at=300)
    output = tmp_path / "versioned.bin"
    with pytest.raises(NetworkError):
        _downloader(server).download(FILE_ID, output)

    server.content = b"new-content" * 250
    # Keep the stored validator observable long enough to exercise the T005
    # full-200 fallback path, then serve current metadata for the fresh restart.
    old_metadata = server._metadata_response
    calls = 0

    def old_then_current_metadata() -> FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            server.content = original
            response = old_metadata()
            server.content = b"new-content" * 250
            return response
        return old_metadata()

    server._metadata_response = old_then_current_metadata  # type: ignore[method-assign]
    server.force_full_on_range_once = True
    result = _downloader(server).download(FILE_ID, output)

    assert result.resumed_bytes == 0
    assert output.read_bytes() == server.content
    assert list(tmp_path.glob("versioned.bin.part.stale-*"))
    content_requests = [request for request in server.requests if request.full_url.endswith("/content")]
    assert any(request.get_header("Range") == "bytes=300-" for request in content_requests)


def test_416_resume_isolated_and_fresh_download_succeeds(tmp_path: Path) -> None:
    server = FakeFileServer(b"range-restart" * 300, interrupt_first_at=100)
    output = tmp_path / "range.bin"
    with pytest.raises(NetworkError):
        _downloader(server).download(FILE_ID, output)
    server.force_416_on_range_once = True

    result = _downloader(server).download(FILE_ID, output)

    assert result.resumed_bytes == 0
    assert output.read_bytes() == server.content
    assert list(tmp_path.glob("range.bin.part.stale-*"))


def test_invalid_206_does_not_append_partial_data(tmp_path: Path) -> None:
    server = FakeFileServer(b"broken-range" * 500, interrupt_first_at=101)
    output = tmp_path / "bad-range.bin"
    with pytest.raises(NetworkError):
        _downloader(server).download(FILE_ID, output)
    before = resume_artifact_paths(output)[0].read_bytes()
    server.bad_partial_response = True

    with pytest.raises(ProtocolError):
        _downloader(server).download(FILE_ID, output)

    assert resume_artifact_paths(output)[0].read_bytes() == before
    assert not output.exists()


def test_corrupted_partial_is_quarantined_after_final_hash_check(tmp_path: Path) -> None:
    server = FakeFileServer(b"integrity" * 1_000, interrupt_first_at=100)
    output = tmp_path / "corrupt.bin"
    with pytest.raises(NetworkError):
        _downloader(server).download(FILE_ID, output)
    part_path, _ = resume_artifact_paths(output)
    part_path.write_bytes(b"X" + part_path.read_bytes()[1:])

    with pytest.raises(IntegrityError):
        _downloader(server).download(FILE_ID, output)

    assert not output.exists()
    assert list(tmp_path.glob("corrupt.bin.part.stale-*"))


def test_empty_file_timeout_and_local_disk_failure(tmp_path: Path, monkeypatch) -> None:
    empty = FakeFileServer(b"")
    output = tmp_path / "empty.bin"
    assert _downloader(empty).download(FILE_ID, output).size_bytes == 0
    assert output.read_bytes() == b""

    timeout = FakeFileServer(b"timeout")
    timeout.timeout_on_content_once = True
    with pytest.raises(NetworkError):
        _downloader(timeout).download(FILE_ID, tmp_path / "timeout.bin")

    disk = FakeFileServer(b"disk")

    def fail_write(*_args, **_kwargs) -> None:
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(client_module, "_write_chunk", fail_write)
    with pytest.raises(LocalStorageError, match="disk space"):
        _downloader(disk).download(FILE_ID, tmp_path / "disk.bin")


def test_completed_repeat_is_safe_and_state_never_contains_token(tmp_path: Path, capsys) -> None:
    server = FakeFileServer(b"safe-repeat" * 1_000, interrupt_first_at=33)
    output = tmp_path / "safe.bin"
    with pytest.raises(NetworkError):
        _downloader(server).download(FILE_ID, output)
    _, state_path = resume_artifact_paths(output)
    assert TOKEN not in state_path.read_text()
    assert TOKEN not in capsys.readouterr().out

    server.interrupt_first_at = None
    _downloader(server).download(FILE_ID, output)
    repeated = _downloader(server).download(FILE_ID, output)
    assert repeated.output_path == output
    assert output.read_bytes() == server.content


def test_pairing_exchange_keeps_token_in_memory_and_rejects_bad_target(tmp_path: Path) -> None:
    server = FakeFileServer(b"pair")
    assert exchange_cli_pairing_code("http://127.0.0.1:8123", "123456", opener=server.opener) == TOKEN
    assert server.requests[-1].get_header("Authorization") is None
    with pytest.raises(DownloadError):
        _downloader(server).download("not-a-uuid", tmp_path / "out.bin")
    with pytest.raises(DownloadError):
        ResumableDownloader("http://token@example.test", TOKEN)


# ---------------------------------------------------------------------------
# Real loopback HTTP servers: redirects must never forward credentials
# ---------------------------------------------------------------------------


class _RecordingHttpHandler(http.server.BaseHTTPRequestHandler):
    """Record every request, then delegate the response to the owning server."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _dispatch(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        record = {
            "method": self.command,
            "path": self.path,
            "headers": {name.lower(): value for name, value in self.headers.items()},
        }
        self.server.captured.append(record)  # type: ignore[attr-defined]
        self.server.responder(self, record)  # type: ignore[attr-defined]

    do_GET = _dispatch
    do_POST = _dispatch


class LocalHttpServer:
    """A real loopback HTTP server on an ephemeral port that records requests."""

    def __init__(self, responder) -> None:
        self.captured: list[dict[str, object]] = []
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHttpHandler)
        self._httpd.captured = self.captured  # type: ignore[attr-defined]
        self._httpd.responder = responder  # type: ignore[attr-defined]
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def paths(self) -> list[str]:
        return [str(record["path"]) for record in self.captured]

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def _send_json(handler: _RecordingHttpHandler, status: int, payload: dict[str, object]) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_bytes(
    handler: _RecordingHttpHandler, status: int, body: bytes, headers: dict[str, str] | None = None
) -> None:
    handler.send_response(status)
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_redirect(handler: _RecordingHttpHandler, status: int, location: str) -> None:
    handler.send_response(status)
    handler.send_header("Location", location)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def _metadata_payload(content: bytes) -> dict[str, object]:
    sha256 = hashlib.sha256(content).hexdigest()
    return {
        "file_id": FILE_ID,
        "etag": f'"sha256:{sha256}"',
        "size_bytes": len(content),
        "content_sha256": sha256,
    }


def _file_responder(content: bytes, redirect: tuple[int, str] | None = None):
    metadata = _metadata_payload(content)

    def respond(handler: _RecordingHttpHandler, record: dict[str, object]) -> None:
        path = record["path"]
        if path == f"/api/files/{FILE_ID}":
            _send_json(handler, 200, metadata)
        elif path == f"/api/files/{FILE_ID}/content":
            if redirect is not None:
                _send_redirect(handler, redirect[0], redirect[1])
            else:
                _send_bytes(
                    handler,
                    200,
                    content,
                    {"ETag": str(metadata["etag"]), "Accept-Ranges": "bytes"},
                )
        else:
            _send_json(handler, 404, {"error": "not_found"})

    return respond


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirected_content_is_refused_and_credentials_never_forwarded(
    status: int, tmp_path: Path
) -> None:
    content = b"redirect-guarded-content-" * 200
    evil = LocalHttpServer(lambda handler, record: _send_json(handler, 200, {"stolen": True}))
    origin = LocalHttpServer(_file_responder(content, redirect=(status, f"{evil.origin}/collect")))
    try:
        output = tmp_path / "redirected.bin"
        with pytest.raises(ProtocolError) as error:
            ResumableDownloader(origin.origin, TOKEN, retries=0, progress=lambda *_: None).download(
                FILE_ID, output
            )

        assert str(status) in str(error.value)
        assert TOKEN not in str(error.value)
        assert evil.captured == [], "a redirect target must never receive a credentialed request"
        assert not any("authorization" in record["headers"] for record in evil.captured)
        assert not output.exists(), "a failed download must not publish an incomplete file"
    finally:
        origin.close()
        evil.close()


def test_real_local_http_download_still_succeeds(tmp_path: Path) -> None:
    content = b"legitimate-loopback-content-" * 300
    server = LocalHttpServer(_file_responder(content))
    try:
        output = tmp_path / "legit.bin"
        result = ResumableDownloader(
            server.origin, TOKEN, retries=0, progress=lambda *_: None
        ).download(FILE_ID, output)

        assert output.read_bytes() == content
        assert result.resumed_bytes == 0
        assert result.content_sha256 == hashlib.sha256(content).hexdigest()
        assert any(
            record["headers"].get("authorization") == f"Bearer {TOKEN}" for record in server.captured
        )
    finally:
        server.close()


@pytest.mark.parametrize("status", [302, 307])
def test_pairing_redirect_is_refused_and_code_never_forwarded(status: int) -> None:
    evil = LocalHttpServer(lambda handler, record: _send_json(handler, 201, {"access_token": "stolen"}))

    def respond(handler: _RecordingHttpHandler, record: dict[str, object]) -> None:
        if record["path"] == "/api/pairings":
            _send_redirect(handler, status, f"{evil.origin}/pairing")
        else:
            _send_json(handler, 404, {"error": "not_found"})

    origin = LocalHttpServer(respond)
    try:
        with pytest.raises(ProtocolError) as error:
            exchange_cli_pairing_code(origin.origin, "246810")

        assert "246810" not in str(error.value)
        assert evil.captured == [], "the pairing code must not be forwarded to another origin"
        assert not any("authorization" in record["headers"] for record in evil.captured)
    finally:
        origin.close()
        evil.close()


# ---------------------------------------------------------------------------
# A complete .part is verified locally instead of being downloaded again
# ---------------------------------------------------------------------------


def test_complete_part_is_verified_and_published_without_content_request(tmp_path: Path) -> None:
    content = bytes(range(256)) * 300
    server = FakeFileServer(content, interrupt_first_at=len(content))
    output = tmp_path / "already-complete.bin"

    with pytest.raises(NetworkError):
        _downloader(server).download(FILE_ID, output)
    part_path, state_path = resume_artifact_paths(output)
    assert part_path.stat().st_size == len(content)
    assert json.loads(state_path.read_text())["downloaded_bytes"] == len(content)
    content_requests_before = [
        request for request in server.requests if request.full_url.endswith("/content")
    ]
    assert len(content_requests_before) == 1

    result = _downloader(server).download(FILE_ID, output)

    assert result.size_bytes == len(content)
    assert result.resumed_bytes == len(content)
    assert output.read_bytes() == content
    content_requests_after = [
        request for request in server.requests if request.full_url.endswith("/content")
    ]
    assert len(content_requests_after) == 1, "the complete .part must not be downloaded again"
    assert not part_path.exists()
    assert not state_path.exists()


def test_complete_empty_part_is_published_without_content_request(tmp_path: Path) -> None:
    server = FakeFileServer(b"", interrupt_first_at=0)
    output = tmp_path / "empty-complete.bin"

    with pytest.raises(NetworkError):
        _downloader(server).download(FILE_ID, output)
    part_path, _ = resume_artifact_paths(output)
    assert part_path.exists() and part_path.stat().st_size == 0
    content_requests_before = [
        request for request in server.requests if request.full_url.endswith("/content")
    ]
    assert len(content_requests_before) == 1

    result = _downloader(server).download(FILE_ID, output)

    assert result.size_bytes == 0
    assert result.resumed_bytes == 0
    assert output.read_bytes() == b""
    content_requests_after = [
        request for request in server.requests if request.full_url.endswith("/content")
    ]
    assert len(content_requests_after) == 1
    assert not part_path.exists()


def test_complete_part_with_wrong_hash_is_isolated_and_not_published(tmp_path: Path) -> None:
    content = b"complete-but-corrupt" * 500
    server = FakeFileServer(content, interrupt_first_at=len(content))
    output = tmp_path / "corrupt-complete.bin"

    with pytest.raises(NetworkError):
        _downloader(server).download(FILE_ID, output)
    part_path, _ = resume_artifact_paths(output)
    assert part_path.stat().st_size == len(content)
    part_path.write_bytes(b"X" + part_path.read_bytes()[1:])

    with pytest.raises(IntegrityError):
        _downloader(server).download(FILE_ID, output)

    assert not output.exists()
    assert list(tmp_path.glob("corrupt-complete.bin.part.stale-*"))


# ---------------------------------------------------------------------------
# resumed_bytes only counts data reused by the successful download
# ---------------------------------------------------------------------------


def test_version_change_restarts_from_zero_and_resets_resumed_bytes(tmp_path: Path) -> None:
    server = FakeFileServer(b"version-one-" * 400, interrupt_first_at=120)
    output = tmp_path / "version-change.bin"
    with pytest.raises(NetworkError):
        _downloader(server).download(FILE_ID, output)

    server.content = b"version-two-is-different" * 300
    result = _downloader(server).download(FILE_ID, output)

    assert result.resumed_bytes == 0
    assert output.read_bytes() == server.content
    assert list(tmp_path.glob("version-change.bin.part.stale-*"))
