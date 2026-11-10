"""Controller-side coordination of sequential, parallel and controlled downloads.

The module deliberately stays on top of :class:`ResumableDownloader`: it adds
multi-client orchestration, per-client in-memory credentials and a machine
readable run record, but every byte still travels through the T006 protections
(strong ETag validation, Range/If-Range resume, final SHA-256 verification and
no-overwrite publication).

``sequential`` runs one client at a time, ``parallel`` starts every client at
once and ``controlled`` keeps a fixed number of client tasks active with FIFO
submission order.  Only the controller process is coordinated here.  Each
logical client performs its own HTTP download with its own credential; the
module never pretends that threads inside one process are independent remote
devices.
"""

from __future__ import annotations

import json
import os
import threading
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
    resume_artifact_paths,
)


MAX_CLIENTS = 8
MAX_CONCURRENT = MAX_CLIENTS
STRATEGIES = ("sequential", "parallel", "controlled")

STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

RESULT_SCHEMA_VERSION = 2

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
    queued_at: datetime | None = None
    wait_seconds: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "task_id": self.task_id,
            "status": self.status,
            "queued_at": _isoformat(self.queued_at) if self.queued_at else None,
            "started_at": _isoformat(self.started_at) if self.started_at else None,
            "finished_at": _isoformat(self.finished_at) if self.finished_at else None,
            "wait_seconds": self.wait_seconds,
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
    max_concurrent: int | None = None
    observed_max_active: int = 0

    def counts(self) -> dict[str, int]:
        counts = {STATUS_SUCCEEDED: 0, STATUS_FAILED: 0, STATUS_CANCELLED: 0}
        for client in self.clients:
            counts[client.status] = counts.get(client.status, 0) + 1
        return counts

    def to_dict(self) -> dict[str, object]:
        counts = self.counts()
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "schema_notes": (
                "v2 adds scheduling.max_concurrent, scheduling.observed_max_active, "
                "client queued_at and client wait_seconds; all v1 fields keep their meaning"
            ),
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
            "scheduling": {
                "max_concurrent": self.max_concurrent,
                "observed_max_active": self.observed_max_active,
            },
            "clients": [client.to_dict() for client in self.clients],
            "summary": {
                "total_clients": len(self.clients),
                "succeeded": counts[STATUS_SUCCEEDED],
                "failed": counts[STATUS_FAILED],
                "cancelled": counts[STATUS_CANCELLED],
                "duration_seconds": self.duration_seconds,
            },
            "units": {"bytes": "bytes", "time": "seconds"},
            "timing": (
                "process-local durations use time.monotonic(); client duration_seconds is "
                "execution time, wait_seconds is queue wait and run duration_seconds is batch "
                "wall time; timestamps are UTC wall clock"
            ),
        }


def _classify_error(error: BaseException) -> str:
    for exception_type, label in _ERROR_TYPES:
        if isinstance(error, exception_type):
            return label
    return "internal"


class _ActiveCounter:
    """Track concurrently executing client tasks and the observed maximum."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def enter(self) -> None:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def leave(self) -> None:
        with self._lock:
            self.active -= 1


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
    tokens = [task.token for task in tasks]
    if len(set(tokens)) != len(tokens):
        raise DistributionError("Each client must use a distinct Bearer credential.")
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


def client_artifact_paths(output_path: Path) -> tuple[Path, Path, Path]:
    """Return the download target and its adjacent ``.part``/``.part.json`` files."""
    target = Path(output_path)
    part_path, state_path = resume_artifact_paths(target)
    return (target, part_path, state_path)


def path_identity(path: Path) -> str:
    """Comparison key that normalizes aliases, symlinks and Windows case differences."""
    resolved = Path(path).expanduser().resolve(strict=False)
    return os.path.normcase(os.path.normpath(str(resolved)))


def validate_distribution_paths(
    results_path: Path,
    records_path: Path,
    client_outputs: Sequence[Path],
) -> None:
    """Reject record paths that could overwrite a client download or each other.

    The comparison resolves ``.``/``..`` and symlink aliases and applies Windows
    case folding, so ``client-1/download.bin``, ``client-1/../client-1/download.bin``
    and ``CLIENT-1\\DOWNLOAD.BIN`` are all treated as the same protected file.
    """
    results_identity = path_identity(results_path)
    records_identity = path_identity(records_path)
    if results_identity == records_identity:
        raise DistributionError(
            "The result JSON and the SQLite record database must use different paths."
        )
    for label, path in (("result JSON", results_path), ("SQLite record", records_path)):
        resolved = Path(path).expanduser().resolve(strict=False)
        if resolved.exists() and resolved.is_dir():
            raise DistributionError(f"The {label} path must name a file, not a directory.")
        if resolved.parent.exists() and not resolved.parent.is_dir():
            raise DistributionError(f"The {label} path has a parent that is not a directory.")
    protected: dict[str, str] = {}
    for output in client_outputs:
        for artifact, description in zip(
            client_artifact_paths(output),
            ("download target", "partial download", "resume state"),
        ):
            protected[path_identity(artifact)] = description
    for label, identity in (("result JSON", results_identity), ("SQLite record", records_identity)):
        description = protected.get(identity)
        if description is not None:
            raise DistributionError(
                f"The {label} path conflicts with a client {description}; refusing to overwrite it."
            )


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
    queued_monotonic: float | None = None,
    queued_at: datetime | None = None,
    active_counter: _ActiveCounter | None = None,
) -> ClientResult:
    task_id = str(uuid.uuid4())
    entry_monotonic = time.monotonic()
    started_at = utc_now()
    wait_seconds = (
        round(max(0.0, entry_monotonic - queued_monotonic), 6)
        if queued_monotonic is not None
        else 0.0
    )
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
    if active_counter is not None:
        active_counter.enter()
    try:
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
    finally:
        if active_counter is not None:
            active_counter.leave()
    finished_at = utc_now()
    return ClientResult(
        client_id=task.client_id,
        task_id=task_id,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=_elapsed_seconds(entry_monotonic),
        downloaded_bytes=downloaded_bytes,
        resumed_bytes=resumed_bytes,
        error_type=error_type,
        error_message=error_message,
        output_name=task.output_path.name,
        queued_at=queued_at if queued_at is not None else started_at,
        wait_seconds=wait_seconds,
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
    max_concurrent: int | None = None,
    timeout_seconds: float = 15.0,
    retries: int = 2,
    stop_on_error: bool = False,
    opener: object | None = None,
    sleep: Callable[[float], None] | None = None,
    progress: ProgressReporter | None = None,
) -> DistributionRun:
    """Download ``file_id`` to every client with the requested strategy.

    ``sequential`` starts the next client only after the previous one finished.
    ``parallel`` submits every client immediately through a bounded thread pool.
    ``controlled`` submits clients in FIFO order to a pool of ``max_concurrent``
    workers, so at most that many client tasks run at any moment and a finished
    task automatically admits the next waiting one.  In every case the overlap
    is created by concurrent blocking HTTP downloads, not by the pool itself.
    ``stop_on_error`` only applies to ``sequential`` and marks the not-yet-started
    clients as ``cancelled``.
    """
    if strategy not in STRATEGIES:
        raise DistributionError("Strategy must be sequential, parallel or controlled.")
    if timeout_seconds <= 0:
        raise DistributionError("Timeout must be greater than zero.")
    if retries < 0:
        raise DistributionError("Retries cannot be negative.")
    if strategy != "sequential" and stop_on_error:
        raise DistributionError("stop_on_error is only meaningful for sequential distribution.")
    tasks = _validate_tasks(clients)
    if strategy == "controlled":
        if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int):
            raise DistributionError("controlled distribution requires a positive max_concurrent.")
        if max_concurrent < 1:
            raise DistributionError("max_concurrent must be a positive integer.")
        if max_concurrent > MAX_CONCURRENT:
            raise DistributionError(f"max_concurrent cannot exceed {MAX_CONCURRENT}.")
        if max_concurrent > len(tasks):
            raise DistributionError("max_concurrent cannot exceed the number of clients.")
    elif max_concurrent is not None:
        raise DistributionError("max_concurrent is only valid for the controlled strategy.")
    _prepare_client_outputs(tasks)

    run_id = str(uuid.uuid4())
    started_at = utc_now()
    started_monotonic = time.monotonic()
    active_counter = _ActiveCounter()

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
            queued_at = utc_now()
            queued_monotonic = time.monotonic()
            result = _execute_client(
                server,
                metadata.file_id,
                task,
                timeout_seconds=timeout_seconds,
                retries=retries,
                opener=opener,
                sleep=sleep,
                progress=progress,
                queued_monotonic=queued_monotonic,
                queued_at=queued_at,
                active_counter=active_counter,
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
        worker_limit = len(tasks) if strategy == "parallel" else int(max_concurrent)  # type: ignore[arg-type]
        with ThreadPoolExecutor(max_workers=worker_limit) as pool:
            futures = []
            for task in tasks:
                queued_at = utc_now()
                queued_monotonic = time.monotonic()
                futures.append(
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
                        queued_monotonic=queued_monotonic,
                        queued_at=queued_at,
                        active_counter=active_counter,
                    )
                )
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
        max_concurrent=max_concurrent,
        observed_max_active=active_counter.max_active,
    )


def write_result_json(
    path: Path,
    run: DistributionRun,
    *,
    protected_paths: Sequence[Path] = (),
) -> Path:
    """Atomically write the machine readable run result.

    ``protected_paths`` lists client download targets and resume artifacts; the
    writer refuses to replace any of them, so a misconfigured ``--results`` can
    never clobber a file a client just downloaded.  Ordinary result files are
    still replaced atomically through a temporary file plus ``os.replace``.
    """
    destination = path.expanduser().resolve(strict=False)
    if destination.exists() and destination.is_dir():
        raise DistributionError("The result path must name a file.")
    destination_identity = path_identity(destination)
    if any(path_identity(candidate) == destination_identity for candidate in protected_paths):
        raise DistributionError(
            "The result JSON path conflicts with a downloaded file; refusing to overwrite it."
        )
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
