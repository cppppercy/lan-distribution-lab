from __future__ import annotations

import asyncio
import hashlib
import io
import os
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from lan_distribution_lab.app import create_app
from lan_distribution_lab.config import Settings
from lan_distribution_lab.errors import ApiError
from lan_distribution_lab.files import FileService
from lan_distribution_lab.multipart import MAX_STREAM_CHUNK_BYTES, stream_single_file_multipart
from lan_distribution_lab.runtime import Runtime
from lan_distribution_lab.storage import LocalStateStore


@pytest.fixture
def file_environment(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "state",
        managed_root=tmp_path / "managed",
        origin="http://testserver",
        max_upload_bytes=2048,
        max_request_bytes=4096,
        max_managed_bytes=8192,
        min_free_bytes=0,
        max_active_uploads=1,
        max_active_downloads=1,
    )
    runtime = Runtime(settings, store=LocalStateStore(settings))
    return settings, runtime, TestClient(create_app(settings, runtime=runtime))


def _token(runtime: Runtime, client: TestClient, scope: str = "read_write") -> str:
    code = runtime.auth.create_pairing_code(scope).code
    response = client.post("/api/pairings", json={"pairing_code": code, "client_kind": "cli"})
    assert response.status_code == 201
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _upload(client: TestClient, token: str, name: str, content: bytes, *, display_name: str | None = None):
    data = {"display_name": display_name} if display_name is not None else None
    return client.post(
        "/api/uploads",
        headers=_headers(token),
        data=data,
        files={"file": (name, content, "application/octet-stream")},
    )


def test_upload_list_metadata_download_and_transfer_integrity(file_environment) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)
    content = b"network lab\x00" * 150

    uploaded = _upload(client, token, "report.bin", content, display_name="报告.bin")

    assert uploaded.status_code == 201
    payload = uploaded.json()
    file_data = payload["file"]
    assert file_data["display_name"] == "报告.bin"
    assert file_data["size_bytes"] == len(content)
    assert file_data["content_sha256"] == hashlib.sha256(content).hexdigest()
    assert file_data["etag"] == f'"sha256:{hashlib.sha256(content).hexdigest()}"'
    assert (runtime.store.objects_dir / file_data["file_id"]).read_bytes() == content
    assert list(runtime.store.staging_dir.iterdir()) == []

    listed = client.get("/api/files", headers=_headers(token))
    metadata = client.get(f"/api/files/{file_data['file_id']}", headers=_headers(token))
    downloaded = client.get(f"/api/files/{file_data['file_id']}/content", headers=_headers(token))

    assert listed.status_code == metadata.status_code == downloaded.status_code == 200
    assert listed.json()["items"][0]["file_id"] == file_data["file_id"]
    assert metadata.json()["content_sha256"] == file_data["content_sha256"]
    assert downloaded.content == content
    assert downloaded.headers["etag"] == file_data["etag"]
    assert "filename*=UTF-8''" in downloaded.headers["content-disposition"]
    transfer = client.get(
        f"/api/transfers/{downloaded.headers['x-transfer-id']}", headers=_headers(token)
    )
    assert transfer.json()["state"] == "completed"
    assert transfer.json()["bytes_transferred"] == len(content)


def test_single_byte_ranges_if_range_and_reassembled_hash(file_environment) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)
    content = bytes(range(64))
    uploaded = _upload(client, token, "range.bin", content).json()["file"]
    url = f"/api/files/{uploaded['file_id']}/content"

    full = client.get(url, headers=_headers(token))
    assert full.status_code == 200
    assert full.content == content
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-length"] == str(len(content))

    exact = client.get(url, headers={**_headers(token), "Range": "bytes=0-0"})
    assert exact.status_code == 206
    assert exact.content == content[:1]
    assert exact.headers["content-range"] == f"bytes 0-0/{len(content)}"
    assert exact.headers["content-length"] == "1"
    assert exact.headers["etag"] == uploaded["etag"]
    exact_transfer = client.get(
        f"/api/transfers/{exact.headers['x-transfer-id']}", headers=_headers(token)
    ).json()
    assert exact_transfer["state"] == "completed"
    assert exact_transfer["bytes_transferred"] == exact_transfer["size_bytes"] == 1

    open_ended = client.get(url, headers={**_headers(token), "Range": "bytes=60-"})
    suffix = client.get(url, headers={**_headers(token), "Range": "bytes=-4"})
    clamped = client.get(url, headers={**_headers(token), "Range": "bytes=62-9999"})
    assert open_ended.content == suffix.content == content[60:]
    assert clamped.content == content[62:]
    assert open_ended.headers["content-range"] == f"bytes 60-63/{len(content)}"
    assert suffix.headers["content-range"] == f"bytes 60-63/{len(content)}"
    assert clamped.headers["content-range"] == f"bytes 62-63/{len(content)}"

    pieces = [
        client.get(url, headers={**_headers(token), "Range": "bytes=0-19"}).content,
        client.get(url, headers={**_headers(token), "Range": "bytes=20-39"}).content,
        client.get(url, headers={**_headers(token), "Range": "bytes=40-"}).content,
    ]
    assert hashlib.sha256(b"".join(pieces)).hexdigest() == uploaded["content_sha256"]

    matching = client.get(
        url,
        headers={**_headers(token), "Range": "bytes=1-3", "If-Range": uploaded["etag"]},
    )
    weak_or_stale = client.get(
        url,
        headers={**_headers(token), "Range": "bytes=1-3", "If-Range": f"W/{uploaded['etag']}"},
    )
    assert matching.status_code == 206
    assert matching.content == content[1:4]
    assert weak_or_stale.status_code == 200
    assert weak_or_stale.content == content


@pytest.mark.parametrize("header", ["bytes=", "bytes=abc", "bytes=1--2", "bytes=-0", "items=0-1", "bytes=0-1,3-4"])
def test_invalid_or_multi_range_is_rejected(file_environment, header) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)
    file_id = _upload(client, token, "range.bin", b"abcdef").json()["file"]["file_id"]

    response = client.get(
        f"/api/files/{file_id}/content", headers={**_headers(token), "Range": header}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_range"
    with runtime.store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM transfer_records WHERE direction = 'download'").fetchone()[0] == 0


@pytest.mark.parametrize("header", ["bytes=6-", "bytes=4-3"])
def test_unsatisfiable_range_returns_416_without_a_transfer(file_environment, header) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)
    file_id = _upload(client, token, "range.bin", b"abcdef").json()["file"]["file_id"]

    response = client.get(
        f"/api/files/{file_id}/content", headers={**_headers(token), "Range": header}
    )

    assert response.status_code == 416
    assert response.json()["error"]["code"] == "range_not_satisfiable"
    assert response.headers["content-range"] == "bytes */6"
    assert response.headers["accept-ranges"] == "bytes"
    with runtime.store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM transfer_records WHERE direction = 'download'").fetchone()[0] == 0


def test_empty_file_range_is_unsatisfiable_and_unauthorized_request_is_denied(file_environment) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)
    file_id = _upload(client, token, "empty.bin", b"").json()["file"]["file_id"]
    url = f"/api/files/{file_id}/content"

    empty_range = client.get(url, headers={**_headers(token), "Range": "bytes=0-0"})
    unauthorized = client.get(url, headers={"Range": "bytes=0-0"})

    assert empty_range.status_code == 416
    assert empty_range.headers["content-range"] == "bytes */0"
    assert unauthorized.status_code == 401


def test_range_seeks_and_reads_only_selected_bytes_and_releases_download_slot(file_environment, monkeypatch) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)
    file_id = _upload(client, token, "range.bin", b"0123456789").json()["file"]["file_id"]

    class TrackingSource(io.BytesIO):
        def __init__(self, payload: bytes) -> None:
            super().__init__(payload)
            self.seek_positions: list[int] = []
            self.read_sizes: list[int] = []

        def seek(self, offset: int, whence: int = 0) -> int:
            self.seek_positions.append(offset)
            return super().seek(offset, whence)

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return super().read(size)

    source = TrackingSource(b"0123456789")
    monkeypatch.setattr(runtime.files, "open_download", lambda _: source)
    url = f"/api/files/{file_id}/content"
    response = client.get(url, headers={**_headers(token), "Range": "bytes=3-5"})

    assert response.status_code == 206
    assert response.content == b"345"
    assert source.seek_positions == [3]
    assert source.read_sizes == [3]
    assert runtime.download_slots.acquire(blocking=False)
    runtime.download_slots.release()

    assert runtime.download_slots.acquire(blocking=False)
    try:
        limited = client.get(url, headers={**_headers(token), "Range": "bytes=0-1"})
    finally:
        runtime.download_slots.release()
    assert limited.status_code == 503


def test_empty_unicode_and_duplicate_display_names_are_independent(file_environment) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)

    empty = _upload(client, token, "课程资料-你好.txt", b"")
    first = _upload(client, token, "same.txt", b"first")
    second = _upload(client, token, "same.txt", b"second")

    assert empty.status_code == first.status_code == second.status_code == 201
    assert empty.json()["file"]["size_bytes"] == 0
    assert first.json()["file"]["file_id"] != second.json()["file"]["file_id"]
    assert (runtime.store.objects_dir / first.json()["file"]["file_id"]).read_bytes() == b"first"
    assert (runtime.store.objects_dir / second.json()["file"]["file_id"]).read_bytes() == b"second"


def test_unauthorized_read_scope_and_revoked_session_are_denied(file_environment) -> None:
    _, runtime, client = file_environment
    write_token = _token(runtime, client)
    file_id = _upload(client, write_token, "a.txt", b"a").json()["file"]["file_id"]
    read_token = _token(runtime, client, "read")

    assert client.get("/api/files").status_code == 401
    assert client.get(f"/api/files/{file_id}").status_code == 401
    assert _upload(client, read_token, "no.txt", b"x").status_code == 403
    assert client.delete("/api/sessions/current", headers=_headers(write_token)).status_code == 204
    assert client.get(f"/api/files/{file_id}/content", headers=_headers(write_token)).status_code == 401


def test_private_transfer_is_not_visible_to_another_session(file_environment) -> None:
    _, runtime, client = file_environment
    owner = _token(runtime, client)
    other = _token(runtime, client)
    transfer_id = _upload(client, owner, "a.txt", b"a").json()["transfer"]["transfer_id"]

    response = client.get(f"/api/transfers/{transfer_id}", headers=_headers(other))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


@pytest.mark.parametrize("name", ["../outside.txt", "C:\\outside.txt", "a/b.txt", "..", "bad\x00name"])
def test_malicious_display_names_are_rejected_without_publish(file_environment, name) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)

    response = _upload(client, token, "safe.txt", b"unsafe", display_name=name)

    assert response.status_code == 422
    assert list(runtime.store.objects_dir.iterdir()) == []
    assert list(runtime.store.staging_dir.iterdir()) == []


def test_file_and_request_limits_clean_staging(file_environment) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)

    file_limit = _upload(client, token, "large.bin", b"x" * 2049)
    request_limit = client.post(
        "/api/uploads",
        headers={**_headers(token), "Content-Type": "multipart/form-data; boundary=x", "Content-Length": "4097"},
        content=b"x" * 4097,
    )

    assert file_limit.status_code == request_limit.status_code == 413
    assert list(runtime.store.objects_dir.iterdir()) == []
    assert list(runtime.store.staging_dir.iterdir()) == []


def test_total_managed_quota_rejects_publish_and_cleans_staging(file_environment) -> None:
    settings, _, _ = file_environment
    limited = replace(
        settings,
        data_dir=settings.data_dir.parent / "quota-state",
        managed_root=settings.managed_root.parent / "quota-managed",
        max_managed_bytes=1,
    )
    runtime = Runtime(limited, store=LocalStateStore(limited))
    client = TestClient(create_app(limited, runtime=runtime))
    token = _token(runtime, client)

    response = _upload(client, token, "quota.txt", b"12")

    assert response.status_code == 507
    assert list(runtime.store.objects_dir.iterdir()) == []
    assert list(runtime.store.staging_dir.iterdir()) == []


def test_injected_enospc_marks_transfer_failed_and_cleans_staging(file_environment, monkeypatch) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)

    def no_space(*_args):
        raise OSError(errno.ENOSPC, "no space")

    import errno
    import lan_distribution_lab.files as file_module

    monkeypatch.setattr(file_module.os, "write", no_space)
    response = _upload(client, token, "full.bin", b"data")

    assert response.status_code == 507
    assert list(runtime.store.objects_dir.iterdir()) == []
    assert list(runtime.store.staging_dir.iterdir()) == []
    with runtime.store.connection() as connection:
        assert connection.execute("SELECT state, error_code FROM transfer_records").fetchone()[0] == "failed"


def test_interrupted_upload_sink_is_cleaned_and_not_marked_completed(file_environment) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)
    sink = runtime.files.start_upload(runtime.auth.authenticate_token(token).session_id)
    sink.write(b"partial", runtime.settings.max_upload_bytes)

    runtime.files.fail_upload(sink, "cancelled")

    assert list(runtime.store.staging_dir.iterdir()) == []
    with runtime.store.connection() as connection:
        row = connection.execute("SELECT state, error_code FROM transfer_records").fetchone()
    assert tuple(row) == ("failed", "cancelled")


def test_client_disconnect_cleans_staging_marks_transfer_and_releases_slot(file_environment) -> None:
    settings, runtime, client = file_environment
    token = _token(runtime, client)
    app = create_app(settings, runtime=runtime)
    partial_content = b"partial" * 100
    body = (
        b'--cut\r\nContent-Disposition: form-data; name="file"; filename="partial.txt"\r\n\r\n'
        + partial_content
    )
    messages = iter(
        (
            {"type": "http.request", "body": body, "more_body": True},
            {"type": "http.disconnect"},
        )
    )
    async def receive():
        return next(messages)

    async def send(_message):
        return None

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/uploads",
        "raw_path": b"/api/uploads",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"multipart/form-data; boundary=cut"),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    with pytest.raises(ClientDisconnect):
        asyncio.run(app(scope, receive, send))

    assert list(runtime.store.staging_dir.iterdir()) == []
    assert runtime.upload_slots.acquire(blocking=False)
    runtime.upload_slots.release()
    with runtime.store.connection() as connection:
        row = connection.execute("SELECT state, error_code, bytes_transferred FROM transfer_records").fetchone()
    assert row[0:2] == ("failed", "cancelled")
    assert 0 < row[2] < len(partial_content)


@pytest.mark.parametrize("filename", ["plain.txt", "file with spaces.txt", "中文 文件.txt"])
def test_upload_preserves_utf8_and_ascii_filenames(file_environment, filename) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)

    response = _upload(client, token, filename, b"content")

    assert response.status_code == 201
    assert response.json()["file"]["display_name"] == filename


def test_invalid_utf8_filename_is_rejected_and_cleaned(file_environment) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)
    body = (
        b"--bad\r\nContent-Disposition: form-data; name=\"file\"; filename=\"bad\xff.txt\"\r\n\r\n"
        b"content\r\n--bad--\r\n"
    )

    response = client.post(
        "/api/uploads",
        headers={**_headers(token), "Content-Type": "multipart/form-data; boundary=bad"},
        content=body,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
    assert list(runtime.store.staging_dir.iterdir()) == []


@pytest.mark.parametrize("chunk_size", [1, 7, 19, 4096])
def test_multipart_preserves_boundary_like_binary_content_across_chunks(chunk_size) -> None:
    boundary = b"boundary"
    content = b"prefix\r\n--boundaryXXX\x00middle\r\n--boundary-\x01suffix"
    body = (
        b"--boundary\r\n"
        b"Content-Disposition: form-data; name=\"file\"; filename=\"binary.bin\"\r\n\r\n"
        + content
        + b"\r\n--boundary--\r\n"
    )

    class FakeRequest:
        headers = {"content-type": "multipart/form-data; boundary=boundary"}

        async def stream(self):
            for start in range(0, len(body), chunk_size):
                yield body[start : start + chunk_size]

    names: list[str] = []
    received: list[bytes] = []
    parsed = asyncio.run(
        stream_single_file_multipart(
            FakeRequest(),
            maximum_request_bytes=8192,
            on_file_start=names.append,
            on_file_data=received.append,
        )
    )

    assert parsed.filename == "binary.bin"
    assert names == ["binary.bin"]
    assert b"".join(received) == content
    assert max(map(len, received)) <= max(chunk_size, len(boundary) + 5)


def test_multipart_file_data_is_forwarded_in_bounded_chunks() -> None:
    content = b"x" * (MAX_STREAM_CHUNK_BYTES * 3 + 17)
    body = (
        b"--bounded\r\n"
        b"Content-Disposition: form-data; name=\"file\"; filename=\"large.bin\"\r\n\r\n"
        + content
        + b"\r\n--bounded--\r\n"
    )

    class FakeRequest:
        headers = {"content-type": "multipart/form-data; boundary=bounded"}

        async def stream(self):
            yield body

    received: list[bytes] = []
    asyncio.run(
        stream_single_file_multipart(
            FakeRequest(),
            maximum_request_bytes=len(body),
            on_file_start=lambda _: None,
            on_file_data=received.append,
        )
    )

    assert b"".join(received) == content
    assert max(map(len, received)) <= MAX_STREAM_CHUNK_BYTES


def test_concurrent_upload_publication_cannot_exceed_sqlite_quota(file_environment) -> None:
    settings, _, _ = file_environment
    limited = replace(
        settings,
        data_dir=settings.data_dir.parent / "concurrent-state",
        managed_root=settings.managed_root.parent / "concurrent-managed",
        max_managed_bytes=10,
        max_active_uploads=2,
    )
    runtime = Runtime(limited, store=LocalStateStore(limited))
    session_id = runtime.auth.exchange_pairing_code(
        pairing_code=runtime.auth.create_pairing_code("read_write").code,
        client_kind="cli",
        device_label=None,
        source="test",
    ).session.session_id
    sinks = [runtime.files.start_upload(session_id), runtime.files.start_upload(session_id)]
    for sink in sinks:
        sink.write(b"123456", limited.max_upload_bytes)
    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def publish(sink) -> None:
        barrier.wait()
        try:
            outcomes.append(runtime.files.finish_upload(sink, "same.txt"))
        except ApiError as error:
            runtime.files.fail_upload(sink, error.code)
            outcomes.append(error)

    workers = [threading.Thread(target=publish, args=(sink,)) for sink in sinks]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert sum(not isinstance(outcome, ApiError) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, ApiError) and outcome.status_code == 507 for outcome in outcomes) == 1
    with runtime.store.connection() as connection:
        used = connection.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM file_metadata").fetchone()[0]
    assert used == 6
    assert list(runtime.store.staging_dir.iterdir()) == []


def test_upload_cookie_requires_origin_and_resource_slots_are_limited(file_environment) -> None:
    _, runtime, client = file_environment
    code = runtime.auth.create_pairing_code("read_write").code
    paired = client.post(
        "/api/pairings",
        json={"pairing_code": code, "client_kind": "browser"},
        headers={"Origin": "http://testserver"},
    )
    assert paired.status_code == 201
    rejected = client.post(
        "/api/uploads",
        headers={"Origin": "https://evil.example"},
        files={"file": ("a.txt", b"a")},
    )
    assert rejected.status_code == 403

    assert runtime.upload_slots.acquire(blocking=False)
    try:
        limited = client.post(
            "/api/uploads",
            headers={"Origin": "http://testserver"},
            files={"file": ("a.txt", b"a")},
        )
    finally:
        runtime.upload_slots.release()
    assert limited.status_code == 503

    cli_token = _token(runtime, client)
    file_id = _upload(client, cli_token, "download.txt", b"download").json()["file"]["file_id"]
    assert runtime.download_slots.acquire(blocking=False)
    try:
        limited_download = client.get(f"/api/files/{file_id}/content", headers=_headers(cli_token))
    finally:
        runtime.download_slots.release()
    assert limited_download.status_code == 503


def test_reparse_or_symlink_object_is_not_followed(file_environment, tmp_path) -> None:
    _, runtime, client = file_environment
    token = _token(runtime, client)
    file_id = _upload(client, token, "safe.txt", b"safe").json()["file"]["file_id"]
    target = tmp_path / "outside.txt"
    target.write_bytes(b"outside")
    object_path = runtime.store.objects_dir / file_id
    object_path.unlink()
    try:
        object_path.symlink_to(target)
    except OSError:
        pytest.skip("creating a symlink is not permitted in this Windows test environment")

    response = client.get(f"/api/files/{file_id}/content", headers=_headers(token))

    assert response.status_code == 404
    assert response.content != b"outside"


def test_streaming_multipart_rejects_oversized_chunk_before_retaining_it() -> None:
    class FakeRequest:
        headers = {"content-type": "multipart/form-data; boundary=x"}

        async def stream(self):
            yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="x.txt"\r\n\r\nabc'
            yield b"z" * (1024 * 1024)

    started: list[str] = []
    written: list[bytes] = []
    with pytest.raises(ApiError, match="payload_too_large") as error:
        asyncio.run(
            stream_single_file_multipart(
                FakeRequest(), maximum_request_bytes=1024, on_file_start=started.append, on_file_data=written.append
            )
        )
    assert error.value.status_code == 413
    assert started == ["x.txt"]
    assert written == []
