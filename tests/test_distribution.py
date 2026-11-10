"""T007 tests: multi-client distribution strategies, isolation and records.

Two kinds of servers are used:

* ``FakeOrigin`` - a real loopback HTTP server with controllable concurrency,
  delays, interruptions and transfer-slot behaviour for deterministic strategy
  tests.
* a real uvicorn instance of the project API for independent-session and
  transfer-record isolation tests.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import socket
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

from lan_distribution_lab.__main__ import (
    _gather_client_tokens,
    _run_distribute_command,
    build_parser,
    main,
)
from lan_distribution_lab.app import create_app
from lan_distribution_lab.client import exchange_cli_pairing_code
from lan_distribution_lab.config import Settings
from lan_distribution_lab.distribution import (
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
from lan_distribution_lab.distribution_store import DistributionStore
from lan_distribution_lab.runtime import Runtime

FILE_ID = str(uuid.UUID("12345678-1234-5678-1234-567812345678"))


# ---------------------------------------------------------------------------
# Controllable local HTTP origin
# ---------------------------------------------------------------------------


class _OriginState:
    def __init__(
        self,
        content: bytes,
        *,
        valid_tokens: set[str] | None = None,
        slot_limit: int | None = None,
        barrier: threading.Barrier | None = None,
        delay_for_token: dict[str, float] | None = None,
        interrupt_once_for_token: set[str] | None = None,
        hold_first_seconds: float = 0.0,
    ) -> None:
        self.content = content
        self.sha256 = hashlib.sha256(content).hexdigest()
        self.etag = f'"sha256:{self.sha256}"'
        self.valid_tokens = set(valid_tokens) if valid_tokens is not None else None
        self.slot_limit = slot_limit
        self.slot = threading.Semaphore(slot_limit) if slot_limit else None
        self.barrier = barrier
        self.delay_for_token = dict(delay_for_token or {})
        self.interrupt_once_for_token = set(interrupt_once_for_token or ())
        self.hold_first_seconds = hold_first_seconds
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.content_calls = 0
        self.content_tokens: list[str] = []
        self.requests: list[dict[str, object]] = []
        self.interrupted: set[str] = set()

    def metadata(self) -> dict[str, object]:
        return {
            "file_id": FILE_ID,
            "etag": self.etag,
            "size_bytes": len(self.content),
            "content_sha256": self.sha256,
        }


class _QuietThreadingHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        pass


class _OriginHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    @property
    def state(self) -> _OriginState:
        return self.server.state  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        state = self.state
        header = self.headers.get("Authorization", "")
        token = header[len("Bearer ") :] if header.startswith("Bearer ") else ""
        with state.lock:
            state.requests.append(
                {
                    "path": self.path,
                    "headers": {name.lower(): value for name, value in self.headers.items()},
                    "token": token,
                }
            )
        if state.valid_tokens is not None and token not in state.valid_tokens:
            self._json(401, {"error": "authorization_required"})
            return
        if self.path == f"/api/files/{FILE_ID}":
            self._json(200, state.metadata())
            return
        if self.path == f"/api/files/{FILE_ID}/content":
            self._content(state, token)
            return
        self._json(404, {"error": "not_found"})

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _content(self, state: _OriginState, token: str) -> None:
        acquired = True
        if state.slot is not None:
            acquired = state.slot.acquire(blocking=False)
            if not acquired:
                self._json(503, {"error": "service_unavailable"})
                return
        with state.lock:
            state.active += 1
            state.max_active = max(state.max_active, state.active)
            state.content_calls += 1
            call_index = state.content_calls
            state.content_tokens.append(token)
        try:
            if state.barrier is not None:
                try:
                    state.barrier.wait(timeout=5)
                except threading.BrokenBarrierError:
                    pass
            if state.hold_first_seconds and call_index == 1:
                time.sleep(state.hold_first_seconds)
            delay = state.delay_for_token.get(token, 0.0)
            if delay:
                time.sleep(delay)
            with state.lock:
                interrupt = (
                    token in state.interrupt_once_for_token and token not in state.interrupted
                )
                if interrupt:
                    state.interrupted.add(token)
            if interrupt:
                self._interrupted_full(state)
                return
            range_header = self.headers.get("Range")
            if_range = self.headers.get("If-Range")
            if range_header and if_range == state.etag:
                offset = int(range_header.split("=", 1)[1].rstrip("-"))
                body = state.content[offset:]
                self.send_response(206)
                self.send_header("ETag", state.etag)
                self.send_header(
                    "Content-Range",
                    f"bytes {offset}-{len(state.content) - 1}/{len(state.content)}",
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(200)
                self.send_header("ETag", state.etag)
                self.send_header("Content-Length", str(len(state.content)))
                self.end_headers()
                self.wfile.write(state.content)
        finally:
            with state.lock:
                state.active -= 1
            if state.slot is not None and acquired:
                state.slot.release()

    def _interrupted_full(self, state: _OriginState) -> None:
        """Announce the full length, write half of it and drop the connection."""
        self.close_connection = True
        self.send_response(200)
        self.send_header("ETag", state.etag)
        self.send_header("Content-Length", str(len(state.content)))
        self.end_headers()
        half = max(1, len(state.content) // 2)
        self.wfile.write(state.content[:half])
        self.wfile.flush()


class FakeOrigin:
    """Loopback origin used to observe real concurrent HTTP downloads."""

    def __init__(
        self,
        content: bytes,
        *,
        valid_tokens: set[str] | None = None,
        slot_limit: int | None = None,
        barrier: threading.Barrier | None = None,
        delay_for_token: dict[str, float] | None = None,
        interrupt_once_for_token: set[str] | None = None,
        hold_first_seconds: float = 0.0,
    ) -> None:
        self.state = _OriginState(
            content,
            valid_tokens=valid_tokens,
            slot_limit=slot_limit,
            barrier=barrier,
            delay_for_token=delay_for_token,
            interrupt_once_for_token=interrupt_once_for_token,
            hold_first_seconds=hold_first_seconds,
        )
        self._httpd = _QuietThreadingHTTPServer(("127.0.0.1", 0), _OriginHandler)
        self._httpd.state = self.state  # type: ignore[attr-defined]
        self.origin = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def file_id(self) -> str:
        return FILE_ID

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Real API server for session isolation
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def real_environment(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "state",
        managed_root=tmp_path / "managed",
        origin="http://127.0.0.1",
        min_free_bytes=0,
        max_upload_bytes=16 * 1024 * 1024,
        max_request_bytes=16 * 1024 * 1024 + 128 * 1024,
        max_managed_bytes=64 * 1024 * 1024,
        max_active_uploads=2,
        max_active_downloads=8,
    )
    app = create_app(settings)
    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the test API server did not start")
        time.sleep(0.02)
    runtime = Runtime(settings)
    environment = SimpleNamespace(
        settings=settings,
        runtime=runtime,
        base=f"http://127.0.0.1:{port}",
    )
    try:
        yield environment
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def _pair(environment, scope: str = "read") -> str:
    code = environment.runtime.auth.create_pairing_code(scope).code
    return exchange_cli_pairing_code(environment.base, code, timeout_seconds=5.0)


def _upload(environment, token: str, content: bytes, name: str = "payload.bin") -> str:
    response = httpx.post(
        f"{environment.base}/api/uploads",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (name, content, "application/octet-stream")},
        timeout=15.0,
    )
    assert response.status_code == 201, response.text
    return response.json()["file"]["file_id"]


def _download_transfers(environment) -> list[sqlite3.Row]:
    with environment.runtime.store.connection() as connection:
        return list(
            connection.execute(
                """
                SELECT session_id, file_id, state, bytes_transferred, size_bytes
                FROM transfer_records WHERE direction = 'download'
                """
            ).fetchall()
        )


# ---------------------------------------------------------------------------
# Strategy tests
# ---------------------------------------------------------------------------


def test_sequential_three_clients_download_with_independent_sessions(
    tmp_path: Path, real_environment
) -> None:
    environment = real_environment
    content = b"T007-sequential\x00" * 4000
    uploader = _pair(environment, "read_write")
    file_id = _upload(environment, uploader, content)
    tokens = [_pair(environment) for _ in range(3)]
    output_dir = prepare_output_directory(tmp_path / "downloads")
    tasks = build_client_tasks(output_dir, "payload.bin", tokens)

    run = run_distribution(
        environment.base, file_id, "sequential", tasks, timeout_seconds=10.0, retries=1
    )

    assert run.strategy == "sequential"
    assert [client.status for client in run.clients] == ["succeeded"] * 3
    assert run.size_bytes == len(content)
    assert run.content_sha256 == hashlib.sha256(content).hexdigest()
    for task in tasks:
        assert task.output_path.read_bytes() == content

    transfers = _download_transfers(environment)
    assert len(transfers) == 3
    assert all(row["state"] == "completed" for row in transfers)
    assert all(row["bytes_transferred"] == len(content) for row in transfers)
    assert all(row["size_bytes"] == len(content) for row in transfers)
    session_ids = {row["session_id"] for row in transfers}
    assert session_ids == {token.split(".", 1)[0] for token in tokens}


def test_parallel_clients_requests_really_overlap(tmp_path: Path) -> None:
    content = b"parallel-overlap-" * 20_000
    barrier = threading.Barrier(2, timeout=5)
    origin = FakeOrigin(content, barrier=barrier)
    try:
        output_dir = prepare_output_directory(tmp_path / "downloads")
        tasks = build_client_tasks(output_dir, "same-name.bin", ["token-left", "token-right"])
        run = run_distribution(
            origin.origin, FILE_ID, "parallel", tasks, timeout_seconds=10.0, retries=0
        )

        assert [client.status for client in run.clients] == ["succeeded", "succeeded"]
        assert origin.state.max_active >= 2, "parallel clients must overlap on the wire"
        assert tasks[0].output_path.read_bytes() == content
        assert tasks[1].output_path.read_bytes() == content
        assert tasks[0].output_path != tasks[1].output_path
    finally:
        origin.close()


def test_sequential_clients_do_not_overlap(tmp_path: Path) -> None:
    content = b"sequential-only" * 5_000
    origin = FakeOrigin(content)
    try:
        output_dir = prepare_output_directory(tmp_path / "downloads")
        tasks = build_client_tasks(output_dir, "payload.bin", ["token-first", "token-second"])
        run = run_distribution(
            origin.origin, FILE_ID, "sequential", tasks, timeout_seconds=10.0, retries=0
        )

        assert [client.status for client in run.clients] == ["succeeded", "succeeded"]
        assert origin.state.max_active == 1
        assert origin.state.content_tokens == ["token-first", "token-second"]
    finally:
        origin.close()


def test_parallel_failure_is_recorded_without_marking_batch_success(tmp_path: Path) -> None:
    content = b"failure-isolation" * 5_000
    origin = FakeOrigin(content, delay_for_token={"token-slow": 2.0})
    try:
        output_dir = prepare_output_directory(tmp_path / "downloads")
        tasks = build_client_tasks(output_dir, "payload.bin", ["token-fast", "token-slow"])
        run = run_distribution(
            origin.origin,
            FILE_ID,
            "parallel",
            tasks,
            timeout_seconds=0.5,
            retries=0,
        )

        statuses = {client.client_id: client.status for client in run.clients}
        assert statuses == {"client-1": "succeeded", "client-2": "failed"}
        assert run.clients[1].error_type == "network"
        assert run.counts() == {"succeeded": 1, "failed": 1, "cancelled": 0}
        assert tasks[0].output_path.read_bytes() == content
        assert not tasks[1].output_path.exists()
    finally:
        origin.close()


def test_authentication_failure_is_isolated_in_parallel(
    tmp_path: Path, real_environment
) -> None:
    environment = real_environment
    content = b"credential-isolation" * 2_000
    uploader = _pair(environment, "read_write")
    file_id = _upload(environment, uploader, content)
    good_tokens = [_pair(environment), _pair(environment)]
    bad_token = f"{uuid.uuid4()}.this-secret-is-not-valid"
    output_dir = prepare_output_directory(tmp_path / "downloads")
    tasks = build_client_tasks(
        output_dir, "payload.bin", [bad_token, good_tokens[0], good_tokens[1]]
    )

    run = run_distribution(
        environment.base, file_id, "parallel", tasks, timeout_seconds=5.0, retries=0
    )

    assert [client.status for client in run.clients] == ["failed", "succeeded", "succeeded"]
    assert run.clients[0].error_type == "authentication"
    assert not tasks[0].output_path.exists()
    assert tasks[1].output_path.read_bytes() == content
    assert tasks[2].output_path.read_bytes() == content
    assert run.counts()["failed"] == 1


def test_server_slot_limit_produces_a_clear_failure(tmp_path: Path) -> None:
    content = b"slot-limit" * 5_000
    origin = FakeOrigin(content, slot_limit=1, hold_first_seconds=1.0)
    try:
        output_dir = prepare_output_directory(tmp_path / "downloads")
        tasks = build_client_tasks(output_dir, "payload.bin", ["token-a", "token-b"])
        run = run_distribution(
            origin.origin, FILE_ID, "parallel", tasks, timeout_seconds=10.0, retries=0
        )

        statuses = sorted(client.status for client in run.clients)
        assert statuses == ["failed", "succeeded"]
        failed = next(client for client in run.clients if client.status == "failed")
        assert failed.error_type == "service_unavailable"
        succeeded = next(client for client in run.clients if client.status == "succeeded")
        assert succeeded.downloaded_bytes == len(content)
    finally:
        origin.close()


def test_sequential_stop_on_error_cancels_remaining_clients(tmp_path: Path) -> None:
    content = b"stop-on-error" * 4_000
    origin = FakeOrigin(content, interrupt_once_for_token={"token-b"})
    try:
        output_dir = prepare_output_directory(tmp_path / "downloads")
        tasks = build_client_tasks(
            output_dir, "payload.bin", ["token-a", "token-b", "token-c"]
        )
        run = run_distribution(
            origin.origin,
            FILE_ID,
            "sequential",
            tasks,
            timeout_seconds=10.0,
            retries=0,
            stop_on_error=True,
        )

        assert [client.status for client in run.clients] == [
            "succeeded",
            "failed",
            "cancelled",
        ]
        assert run.clients[1].error_type == "network"
        assert run.counts() == {"succeeded": 1, "failed": 1, "cancelled": 1}
        assert tasks[0].output_path.read_bytes() == content
        assert not tasks[1].output_path.exists()
        assert not tasks[2].output_path.exists()
        assert origin.state.content_calls == 2
    finally:
        origin.close()


# ---------------------------------------------------------------------------
# T006 protocol reuse
# ---------------------------------------------------------------------------


def test_interrupted_client_resumes_with_range_and_final_hash(tmp_path: Path) -> None:
    content = bytes(range(256)) * 2_000
    origin = FakeOrigin(content, interrupt_once_for_token={"token-resume"})
    try:
        output_dir = prepare_output_directory(tmp_path / "downloads")
        tasks = build_client_tasks(output_dir, "resume.bin", ["token-resume"])

        first = run_distribution(
            origin.origin, FILE_ID, "sequential", tasks, timeout_seconds=10.0, retries=0
        )
        assert first.clients[0].status == "failed"
        assert first.clients[0].error_type == "network"
        part_path = tasks[0].output_path.with_name(f"{tasks[0].output_path.name}.part")
        assert part_path.exists()
        reused = part_path.stat().st_size
        assert 0 < reused < len(content)

        second = run_distribution(
            origin.origin, FILE_ID, "sequential", tasks, timeout_seconds=10.0, retries=0
        )
        assert second.clients[0].status == "succeeded"
        assert second.clients[0].resumed_bytes == reused
        assert tasks[0].output_path.read_bytes() == content

        ranged = [
            request
            for request in origin.state.requests
            if str(request["path"]).endswith("/content")
            and "range" in request["headers"]  # type: ignore[operator]
        ]
        assert ranged, "the resumed download must send a Range request"
        assert str(ranged[-1]["headers"]["range"]).startswith("bytes=")
        assert ranged[-1]["headers"]["if-range"] == origin.state.etag
    finally:
        origin.close()


# ---------------------------------------------------------------------------
# Records and JSON export
# ---------------------------------------------------------------------------


def test_result_json_and_sqlite_records_exclude_secrets(tmp_path: Path) -> None:
    content = b"records-export" * 3_000
    origin = FakeOrigin(content)
    tokens = ["token-alpha", "token-beta"]
    try:
        output_dir = prepare_output_directory(tmp_path / "downloads")
        tasks = build_client_tasks(output_dir, "payload.bin", tokens)
        run = run_distribution(
            origin.origin, FILE_ID, "sequential", tasks, timeout_seconds=10.0, retries=0
        )
        result_path = write_result_json(tmp_path / "records" / "distribution.json", run)
        store = DistributionStore(tmp_path / "records" / "distribution-runs.db")
        store.save(run)
    finally:
        origin.close()

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["strategy"] == "sequential"
    assert payload["file"]["size_bytes"] == len(content)
    assert payload["file"]["content_sha256"] == hashlib.sha256(content).hexdigest()
    assert len(payload["clients"]) == 2
    assert payload["summary"]["succeeded"] == 2
    assert payload["units"] == {"bytes": "bytes", "time": "seconds"}

    result_text = result_path.read_text(encoding="utf-8")
    for token in tokens:
        assert token not in result_text
    assert str(tmp_path) not in result_text

    with sqlite3.connect(store.path) as connection:
        runs = connection.execute(
            "SELECT run_id, strategy, size_bytes, succeeded, failed, cancelled "
            "FROM distribution_runs"
        ).fetchall()
        clients = connection.execute(
            "SELECT client_id, status, downloaded_bytes, resumed_bytes, error_type, output_name "
            "FROM distribution_clients ORDER BY client_id"
        ).fetchall()
    assert runs == [
        (run.run_id, "sequential", len(content), 2, 0, 0)
    ]
    assert [row[0] for row in clients] == ["client-1", "client-2"]
    assert all(row[1] == "succeeded" for row in clients)
    assert all(row[2] == len(content) and row[3] == 0 for row in clients)
    assert all(row[4] is None and row[5] == "payload.bin" for row in clients)

    database_bytes = store.path.read_bytes()
    for token in tokens:
        assert token.encode("utf-8") not in database_bytes


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_repeated_run_is_safe_and_empty_file_distributes(tmp_path: Path) -> None:
    content = b"repeatable" * 2_000
    origin = FakeOrigin(content)
    try:
        output_dir = prepare_output_directory(tmp_path / "downloads")
        tasks = build_client_tasks(output_dir, "payload.bin", ["token-a", "token-b"])
        first = run_distribution(
            origin.origin, FILE_ID, "parallel", tasks, timeout_seconds=10.0, retries=0
        )
        second = run_distribution(
            origin.origin, FILE_ID, "parallel", tasks, timeout_seconds=10.0, retries=0
        )
        assert first.counts()["succeeded"] == 2
        assert second.counts()["succeeded"] == 2
        assert all(task.output_path.read_bytes() == content for task in tasks)
    finally:
        origin.close()

    empty_origin = FakeOrigin(b"")
    try:
        empty_tasks = build_client_tasks(
            prepare_output_directory(tmp_path / "empty"), "empty.bin", ["token-c", "token-d"]
        )
        empty_run = run_distribution(
            empty_origin.origin,
            FILE_ID,
            "parallel",
            empty_tasks,
            timeout_seconds=10.0,
            retries=0,
        )
        assert empty_run.counts()["succeeded"] == 2
        assert empty_run.size_bytes == 0
        assert all(task.output_path.read_bytes() == b"" for task in empty_tasks)
    finally:
        empty_origin.close()


def test_existing_user_file_is_never_overwritten(tmp_path: Path) -> None:
    content = b"downloaded-content" * 2_000
    origin = FakeOrigin(content)
    try:
        output_dir = prepare_output_directory(tmp_path / "downloads")
        tasks = build_client_tasks(output_dir, "payload.bin", ["token-a", "token-b"])
        tasks[0].output_path.parent.mkdir(parents=True, exist_ok=True)
        tasks[0].output_path.write_bytes(b"user-owned-file")

        run = run_distribution(
            origin.origin, FILE_ID, "sequential", tasks, timeout_seconds=10.0, retries=0
        )

        assert [client.status for client in run.clients] == ["failed", "succeeded"]
        assert run.clients[0].error_type == "local_storage"
        assert tasks[0].output_path.read_bytes() == b"user-owned-file"
        assert tasks[1].output_path.read_bytes() == content
    finally:
        origin.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_safe_output_name_rejects_path_traversal(tmp_path: Path) -> None:
    for name in ["../escape.bin", "sub/dir.bin", "..", "", "C:/windows.bin"]:
        with pytest.raises(DistributionError):
            safe_output_name(name)
    assert safe_output_name("课程资料.bin") == "课程资料.bin"


def test_build_client_tasks_limits_and_distinct_paths(tmp_path: Path) -> None:
    with pytest.raises(DistributionError):
        build_client_tasks(tmp_path, "f.bin", ["t"] * (MAX_CLIENTS + 1))
    tasks = build_client_tasks(tmp_path, "f.bin", ["t1", "t2"])
    assert tasks[0].output_path != tasks[1].output_path
    assert tasks[0].client_id != tasks[1].client_id


def test_gather_tokens_from_stdin_requires_one_per_client(monkeypatch) -> None:
    parser = build_parser()
    arguments = parser.parse_args(
        [
            "distribute",
            "--server",
            "http://127.0.0.1:1",
            "--file-id",
            FILE_ID,
            "--strategy",
            "parallel",
            "--clients",
            "2",
            "--output-dir",
            "unused",
            "--tokens-stdin",
        ]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("only-one\n"))
    with pytest.raises(DistributionError):
        _gather_client_tokens(arguments)


def test_cli_rejects_conflicting_stop_on_error(tmp_path: Path, capsys) -> None:
    parser = build_parser()
    arguments = parser.parse_args(
        [
            "distribute",
            "--server",
            "http://127.0.0.1:1",
            "--file-id",
            FILE_ID,
            "--strategy",
            "parallel",
            "--clients",
            "2",
            "--output-dir",
            str(tmp_path / "out"),
            "--tokens-stdin",
            "--stop-on-error",
        ]
    )
    with pytest.raises(SystemExit):
        _run_distribute_command(arguments)
    assert "Distribution failed:" in capsys.readouterr().err


def test_cli_distribute_end_to_end(tmp_path: Path, monkeypatch, capsys) -> None:
    content = b"cli-distribution" * 3_000
    origin = FakeOrigin(content)
    try:
        output_dir = tmp_path / "cli-output"
        argv = [
            "distribute",
            "--server",
            origin.origin,
            "--file-id",
            FILE_ID,
            "--strategy",
            "parallel",
            "--clients",
            "2",
            "--output-dir",
            str(output_dir),
            "--tokens-stdin",
            "--timeout",
            "10",
            "--retries",
            "0",
        ]
        monkeypatch.setattr("sys.stdin", io.StringIO("token-one\ntoken-two\n"))
        main(argv)
    finally:
        origin.close()

    captured = capsys.readouterr()
    assert "2 succeeded" in captured.out
    assert "token-one" not in captured.out and "token-two" not in captured.out
    assert (output_dir / "client-1" / "download.bin").read_bytes() == content
    assert (output_dir / "client-2" / "download.bin").read_bytes() == content
    result_path = output_dir / "distribution-result.json"
    assert result_path.exists()
    records_path = output_dir / "distribution-runs.db"
    assert records_path.exists()
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["summary"]["succeeded"] == 2


# ---------------------------------------------------------------------------
# Review remediation: record-path collisions and duplicate credentials
# ---------------------------------------------------------------------------


def test_results_or_records_cannot_alias_client_artifacts(tmp_path: Path) -> None:
    output_dir = prepare_output_directory(tmp_path / "downloads")
    client_outputs = [
        output_dir / "client-1" / "download.bin",
        output_dir / "client-2" / "download.bin",
    ]

    for artifact in client_artifact_paths(client_outputs[0]):
        with pytest.raises(DistributionError, match="conflicts"):
            validate_distribution_paths(artifact, output_dir / "records.db", client_outputs)
        with pytest.raises(DistributionError, match="conflicts"):
            validate_distribution_paths(output_dir / "result.json", artifact, client_outputs)

    with pytest.raises(DistributionError, match="different paths"):
        validate_distribution_paths(
            output_dir / "same.json", output_dir / "same.json", client_outputs
        )

    validate_distribution_paths(
        output_dir / "result.json", output_dir / "records.db", client_outputs
    )


def test_path_alias_and_windows_case_conflicts_are_detected(tmp_path: Path) -> None:
    output_dir = prepare_output_directory(tmp_path / "downloads")
    client_output = output_dir / "client-1" / "download.bin"

    alias = output_dir / "client-1" / ".." / "client-1" / "download.bin"
    with pytest.raises(DistributionError, match="conflicts"):
        validate_distribution_paths(alias, output_dir / "records.db", [client_output])

    if os.name == "nt":
        upper = Path(str(client_output).upper())
        with pytest.raises(DistributionError, match="conflicts"):
            validate_distribution_paths(output_dir / "result.json", upper, [client_output])


def test_result_json_never_overwrites_a_downloaded_file(tmp_path: Path) -> None:
    content = b"review-remediation-payload" * 2_000
    origin = FakeOrigin(content)
    try:
        output_dir = prepare_output_directory(tmp_path / "downloads")
        tasks = build_client_tasks(output_dir, "payload.bin", ["token-a", "token-b"])
        run = run_distribution(
            origin.origin, FILE_ID, "sequential", tasks, timeout_seconds=10.0, retries=0
        )
        target = tasks[0].output_path
        before = target.read_bytes()

        with pytest.raises(DistributionError, match="conflicts"):
            write_result_json(target, run, protected_paths=[target])

        assert target.read_bytes() == before == content
    finally:
        origin.close()


def test_cli_rejects_results_path_that_would_overwrite_a_client_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    output_dir = tmp_path / "cli-collision"
    target = output_dir / "client-1" / "download.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"keep-me")

    arguments = build_parser().parse_args(
        [
            "distribute",
            "--server",
            "http://127.0.0.1:1",
            "--file-id",
            FILE_ID,
            "--strategy",
            "parallel",
            "--clients",
            "2",
            "--output-dir",
            str(output_dir),
            "--tokens-stdin",
            "--results",
            str(target),
        ]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    with pytest.raises(SystemExit):
        _run_distribute_command(arguments)

    assert target.read_bytes() == b"keep-me"
    assert "conflicts" in capsys.readouterr().err
    assert not (output_dir / "distribution-runs.db").exists()


def test_cli_rejects_records_path_that_would_overwrite_resume_artifacts(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    output_dir = tmp_path / "cli-collision"
    target = output_dir / "client-2" / "download.bin.part.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"keep-resume-state")

    arguments = build_parser().parse_args(
        [
            "distribute",
            "--server",
            "http://127.0.0.1:1",
            "--file-id",
            FILE_ID,
            "--strategy",
            "sequential",
            "--clients",
            "2",
            "--output-dir",
            str(output_dir),
            "--tokens-stdin",
            "--records",
            str(target),
        ]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    with pytest.raises(SystemExit):
        _run_distribute_command(arguments)

    assert target.read_bytes() == b"keep-resume-state"
    assert "conflicts" in capsys.readouterr().err


def test_cli_rejects_same_results_and_records_path(tmp_path: Path, monkeypatch, capsys) -> None:
    output_dir = tmp_path / "cli-collision"
    shared = output_dir / "distribution.json"
    arguments = build_parser().parse_args(
        [
            "distribute",
            "--server",
            "http://127.0.0.1:1",
            "--file-id",
            FILE_ID,
            "--strategy",
            "parallel",
            "--clients",
            "2",
            "--output-dir",
            str(output_dir),
            "--tokens-stdin",
            "--results",
            str(shared),
            "--records",
            str(shared),
        ]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    with pytest.raises(SystemExit):
        _run_distribute_command(arguments)

    assert "different paths" in capsys.readouterr().err
    assert not shared.exists()


def test_duplicate_credentials_are_rejected_without_echo(tmp_path: Path) -> None:
    output_dir = prepare_output_directory(tmp_path / "downloads")

    with pytest.raises(DistributionError, match="distinct") as error:
        build_client_tasks(
            output_dir, "payload.bin", ["duplicate-secret-one", "duplicate-secret-one"]
        )

    assert "duplicate-secret-one" not in str(error.value)


def test_cli_rejects_duplicate_tokens_before_download(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    arguments = build_parser().parse_args(
        [
            "distribute",
            "--server",
            "http://127.0.0.1:1",
            "--file-id",
            FILE_ID,
            "--strategy",
            "parallel",
            "--clients",
            "2",
            "--output-dir",
            str(tmp_path / "duplicate"),
            "--tokens-stdin",
        ]
    )
    monkeypatch.setattr(
        "sys.stdin", io.StringIO("duplicate-secret-one\nduplicate-secret-one\n")
    )

    with pytest.raises(SystemExit):
        _run_distribute_command(arguments)

    captured = capsys.readouterr()
    assert "distinct" in captured.err
    assert "duplicate-secret-one" not in captured.err


def test_result_json_replaces_its_own_previous_result_atomically(tmp_path: Path) -> None:
    content = b"atomic-result" * 1_000
    origin = FakeOrigin(content)
    try:
        output_dir = prepare_output_directory(tmp_path / "downloads")
        tasks = build_client_tasks(output_dir, "payload.bin", ["token-a"])
        first = run_distribution(
            origin.origin, FILE_ID, "sequential", tasks, timeout_seconds=10.0, retries=0
        )
        second = run_distribution(
            origin.origin, FILE_ID, "sequential", tasks, timeout_seconds=10.0, retries=0
        )
        result_path = output_dir / "distribution-result.json"
        write_result_json(result_path, first)
        write_result_json(result_path, second)

        payload = json.loads(result_path.read_text(encoding="utf-8"))
        assert payload["run_id"] == second.run_id
        assert not list(output_dir.glob(".*.tmp"))
    finally:
        origin.close()


def test_records_database_keeps_previous_runs(tmp_path: Path) -> None:
    content = b"records-append" * 1_000
    origin = FakeOrigin(content)
    try:
        output_dir = prepare_output_directory(tmp_path / "downloads")
        tasks = build_client_tasks(output_dir, "payload.bin", ["token-a"])
        first = run_distribution(
            origin.origin, FILE_ID, "sequential", tasks, timeout_seconds=10.0, retries=0
        )
        second = run_distribution(
            origin.origin, FILE_ID, "sequential", tasks, timeout_seconds=10.0, retries=0
        )
        store = DistributionStore(output_dir / "distribution-runs.db")
        store.save(first)
        store.save(second)

        with sqlite3.connect(store.path) as connection:
            rows = connection.execute("SELECT run_id FROM distribution_runs").fetchall()
        assert {row[0] for row in rows} == {first.run_id, second.run_id}
    finally:
        origin.close()
