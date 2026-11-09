from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lan_distribution_lab.app import create_app
from lan_distribution_lab.config import Settings
from lan_distribution_lab.errors import ApiError
from lan_distribution_lab.files import FileService
from lan_distribution_lab.multipart import stream_single_file_multipart
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
