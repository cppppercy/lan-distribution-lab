"""Controller-side coordination of sequential and parallel client downloads.

The module deliberately stays on top of :class:`ResumableDownloader`: it adds
multi-client orchestration, per-client in-memory credentials and a machine
readable run record, but every byte still travels through the T006 protections
(strong ETag validation, Range/If-Range resume, final SHA-256 verification and
no-overwrite publication).

Only the controller process is coordinated here.  Each logical client performs
its own HTTP download with its own credential; the module never pretends that
threads inside one process are independent remote devices.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from .client import (
    AuthenticationError,
    DownloadError,
    IntegrityError,
    LocalStorageError,
    NetworkError,
    ProtocolError,
    ResumableDownloader,
    ServerBusyError,
)


MAX_CLIENTS = 8
STRATEGIES = ("sequential", "parallel")

STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

RESULT_SCHEMA_VERSION = 1

# Ordered on purpose: subclasses of DownloadError must come before DownloadError.
_ERROR_TYPES: tuple[tuple[type[BaseException], str], ...] = (
    (AuthenticationError, "authentication"),
    (ServerBusyError, "service_unavailable"),
    (NetworkError, "network"),
    (ProtocolError, "protocol"),
    (IntegrityError, "integrity"),
    (LocalStorageError, "local_storage"),
    (DownloadError, "download"),
)


class DistributionError(RuntimeError):
    """A controller-side configuration or orchestration error (never a credential)."""


ProgressReporter = Callable[[str, int, int], None]


def utc_now() -> datetime:
    """Wall-clock timestamp used only for the human readable record fields."""
    return datetime.now(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _elapsed_seconds(started: float) -> float:
    """Monotonic duration rounded to microseconds for a stable JSON record."""
    return round(time.monotonic() - started, 6)


@dataclass(frozen=True, slots=True)
class ClientTask:
    """One logical client.

    ``token`` is held only in process memory and is intentionally absent from
    :meth:`to_dict`, the JSON result and the SQLite record.
    """

    client_id: str
    token: str
    output_path: Path


@dataclass(slots=True)
class ClientResult:
    client_id: str
    task_id: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float
    downloaded_bytes: int
    resumed_bytes: int
    error_type: str | None
    error_message: str | None
    output_name: str

    def to_dict(self) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "task_id": self.task_id,
            "status": self.status,
            "started_at": _isoformat(self.started_at) if self.started_at else None,
            "finished_at": _isoformat(self.finished_at) if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
            "downloaded_bytes": self.downloaded_bytes,
            "resumed_bytes": self.resumed_bytes,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "output_name": self.output_name,
        }


@dataclass(slots=True)
class DistributionRun:
    run_id: str
    strategy: str
    file_id: str
    size_bytes: int
    content_sha256: str
    etag: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    clients: list[ClientResult] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        counts = {STATUS_SUCCEEDED: 0, STATUS_FAILED: 0, STATUS_CANCELLED: 0}
        for client in self.clients:
            counts[client.status] = counts.get(client.status, 0) + 1
        return counts

    def to_dict(self) -> dict[str, object]:
        counts = self.counts()
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "run_id": self.run_id,
            "strategy": self.strategy,
            "file": {
                "file_id": self.file_id,
                "size_bytes": self.size_bytes,
                "content_sha256": self.content_sha256,
                "etag": self.etag,
            },
            "started_at": _isoformat(self.started_at),
            "finished_at": _isoformat(self.finished_at),
            "duration_seconds": self.duration_seconds,
            "clients": [client.to_dict() for client in self.clients],
            "summary": {
                "total_clients": len(self.clients),
                "succeeded": counts[STATUS_SUCCEEDED],
                "failed": counts[STATUS_FAILED],
                "cancelled": counts[STATUS_CANCELLED],
                "duration_seconds": self.duration_seconds,
            },
            "units": {"bytes": "bytes", "time": "seconds"},
            "timing": "process-local durations use time.monotonic(); timestamps are UTC wall clock",
        }


def _classify_error(error: BaseException) -> str:
    for exception_type, label in _ERROR_TYPES:
        if isinstance(error, exception_type):
            return label
    return "internal"


def _make_downloader(
    server: str,
    token: str,
    *,
    timeout_seconds: float,
    retries: int,
    opener: object | None,
    sleep: Callable[[float], None] | None,
    progress: Callable[[int, int], None],
) -> ResumableDownloader:
    kwargs: dict[str, object] = {
        "timeout_seconds": timeout_seconds,
        "retries": retries,
        "progress": progress,
    }
    if opener is not None:
        kwargs["opener"] = opener
    if sleep is not None:
        kwargs["sleep"] = sleep
    return ResumableDownloader(server, token, **kwargs)


def _validate_tasks(tasks: Sequence[ClientTask]) -> list[ClientTask]:
    if not 1 <= len(tasks) <= MAX_CLIENTS:
        raise DistributionError(f"Client count must be between 1 and {MAX_CLIENTS}.")
    client_ids = [task.client_id for task in tasks]
    if any(not client_id.strip() for client_id in client_ids):
        raise DistributionError("Client identifiers must not be empty.")
    if len(set(client_ids)) != len(client_ids):
        raise DistributionError("Client identifiers must be unique.")
    output_paths = [str(task.output_path) for task in tasks]
    if len(set(output_paths)) != len(output_paths):
        raise DistributionError("Each client must download to a distinct output file.")
    for task in tasks:
        if not isinstance(task.token, str) or not task.token.strip():
            raise DistributionError("Every client needs its own non-empty access token.")
    return list(tasks)


def _prepare_client_outputs(tasks: Sequence[ClientTask]) -> None:
    for task in tasks:
        parent = task.output_path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise DistributionError("Could not create a client download directory.") from error


def safe_output_name(value: str) -> str:
    """Return a plain file name; reject separators, traversal and absolute paths."""
    if not isinstance(value, str):
        raise DistributionError("Output name must be a string.")
    name = value.strip()
    if not name or name in {".", ".."}:
        raise DistributionError("Output name must be a plain file name.")
    if any(separator in name for separator in ("/", "\\")) or "\x00" in name:
        raise DistributionError("Output name must not contain path separators.")
    if Path(name).is_absolute() or Path(name).name != name:
        raise DistributionError("Output name must not be a path.")
    return name


def prepare_output_directory(value: Path) -> Path:
    """Resolve and create the controller output directory without following links."""
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise DistributionError("Output directory must not be a symbolic link.")
    resolved = candidate.resolve(strict=False)
    if resolved.exists() and not resolved.is_dir():
        raise DistributionError("Output directory must be a directory.")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise DistributionError("Could not create the output directory.") from error
    return resolved


def build_client_tasks(output_dir: Path, output_name: str, tokens: Sequence[str]) -> list[ClientTask]:
    """Build distinct per-client output paths; the token list stays in memory."""
    tasks = [
        ClientTask(
            client_id=f"client-{index + 1}",
            token=token,
            output_path=output_dir / f"client-{index + 1}" / output_name,
        )
        for index, token in enumerate(tokens)
    ]
    return _validate_tasks(tasks)


def _execute_client(
    server: str,
    file_id: str,
    task: ClientTask,
    *,
    timeout_seconds: float,
    retries: int,
    opener: object | None,
    sleep: Callable[[float], None] | None,
    progress: ProgressReporter | None,
) -> ClientResult:
    task_id = str(uuid.uuid4())
    started_at = utc_now()
    started_monotonic = time.monotonic()
    progress_state = {"downloaded": 0}

    def report(downloaded: int, total: int) -> None:
        progress_state["downloaded"] = max(0, downloaded)
        if progress is not None:
            progress(task.client_id, downloaded, total)

    status = STATUS_FAILED
    downloaded_bytes = 0
    resumed_bytes = 0
    error_type: str | None = None
    error_message: str | None = None
    try:
        downloader = _make_downloader(
            server,
            task.token,
            timeout_seconds=timeout_seconds,
            retries=retries,
            opener=opener,
            sleep=sleep,
            progress=report,
        )
        result = downloader.download(file_id, task.output_path)
        status = STATUS_SUCCEEDED
        downloaded_bytes = result.size_bytes
        resumed_bytes = result.resumed_bytes
    except DownloadError as error:
        status = STATUS_FAILED
        error_type = _classify_error(error)
        error_message = str(error)
        downloaded_bytes = progress_state["downloaded"]
    except Exception:  # pragma: no cover - defensive: never leak arbitrary details
        status = STATUS_FAILED
        error_type = "internal"
        error_message = "Unexpected internal client failure."
        downloaded_bytes = progress_state["downloaded"]
    finished_at = utc_now()
    return ClientResult(
        client_id=task.client_id,
        task_id=task_id,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=_elapsed_seconds(started_monotonic),
        downloaded_bytes=downloaded_bytes,
        resumed_bytes=resumed_bytes,
        error_type=error_type,
        error_message=error_message,
        output_name=task.output_path.name,
    )


def _cancelled_result(task: ClientTask, reason: str) -> ClientResult:
    return ClientResult(
        client_id=task.client_id,
        task_id=str(uuid.uuid4()),
        status=STATUS_CANCELLED,
        started_at=None,
        finished_at=None,
        duration_seconds=0.0,
        downloaded_bytes=0,
        resumed_bytes=0,
        error_type=None,
        error_message=reason,
        output_name=task.output_path.name,
    )


def run_distribution(
    server: str,
    file_id: str,
    strategy: str,
    clients: Sequence[ClientTask],
    *,
    timeout_seconds: float = 15.0,
    retries: int = 2,
    stop_on_error: bool = False,
    opener: object | None = None,
    sleep: Callable[[float], None] | None = None,
    progress: ProgressReporter | None = None,
) -> DistributionRun:
    """Download ``file_id`` to every client with the requested strategy.

    ``sequential`` starts the next client only after the previous one finished.
    ``parallel`` starts every client immediately through a bounded thread pool;
    the actual overlap is created by concurrent blocking HTTP downloads, not by
    the pool itself.  ``stop_on_error`` only applies to ``sequential`` and marks
    the not-yet-started clients as ``cancelled``.
    """
    if strategy not in STRATEGIES:
        raise DistributionError("Strategy must be sequential or parallel.")
    if timeout_seconds <= 0:
        raise DistributionError("Timeout must be greater than zero.")
    if retries < 0:
        raise DistributionError("Retries cannot be negative.")
    if strategy == "parallel" and stop_on_error:
        raise DistributionError("stop_on_error is only meaningful for sequential distribution.")
    tasks = _validate_tasks(clients)
    _prepare_client_outputs(tasks)

    run_id = str(uuid.uuid4())
    started_at = utc_now()
    started_monotonic = time.monotonic()

    # One authenticated metadata read provides the run-level file identity.
    # Try the client credentials in order so a single invalid first credential
    # cannot abort a batch whose other clients are fine.
    metadata = None
    preflight_error: DownloadError | None = None
    for task in tasks:
        candidate = _make_downloader(
            server,
            task.token,
            timeout_seconds=timeout_seconds,
            retries=min(retries, 1),
            opener=opener,
            sleep=sleep,
            progress=lambda *_: None,
        )
        try:
            metadata = candidate.metadata(file_id)
            break
        except DownloadError as error:
            preflight_error = error
    if metadata is None:
        assert preflight_error is not None
        raise preflight_error

    if strategy == "sequential":
        results: list[ClientResult] = []
        for index, task in enumerate(tasks):
            result = _execute_client(
                server,
                metadata.file_id,
                task,
                timeout_seconds=timeout_seconds,
                retries=retries,
                opener=opener,
                sleep=sleep,
                progress=progress,
            )
            results.append(result)
            if stop_on_error and result.status != STATUS_SUCCEEDED:
                results.extend(
                    _cancelled_result(
                        remaining,
                        "Not started because an earlier sequential task failed.",
                    )
                    for remaining in tasks[index + 1 :]
                )
                break
    else:
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = [
                pool.submit(
                    _execute_client,
                    server,
                    metadata.file_id,
                    task,
                    timeout_seconds=timeout_seconds,
                    retries=retries,
                    opener=opener,
                    sleep=sleep,
                    progress=progress,
                )
                for task in tasks
            ]
            results = [future.result() for future in futures]

    finished_at = utc_now()
    return DistributionRun(
        run_id=run_id,
        strategy=strategy,
        file_id=metadata.file_id,
        size_bytes=metadata.size_bytes,
        content_sha256=metadata.content_sha256,
        etag=metadata.etag,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=_elapsed_seconds(started_monotonic),
        clients=results,
    )


def write_result_json(path: Path, run: DistributionRun) -> Path:
    """Atomically write the machine readable run result."""
    destination = path.expanduser().resolve(strict=False)
    if destination.exists() and destination.is_dir():
        raise DistributionError("The result path must name a file.")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(run.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise DistributionError("Could not write the distribution result file.") from error
    except OSError as error:
        raise DistributionError("Could not write the distribution result file.") from error
    return destination
