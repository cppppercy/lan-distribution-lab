"""Controlled object storage and persistent transfer summaries for T003-B."""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .errors import ApiError
from .models import FileRecord, TransferRecord
from .storage import LocalStateStore

CHUNK_BYTES = 64 * 1024


@dataclass(slots=True)
class UploadSink:
    file_id: str
    transfer_id: str
    session_id: str
    staging_path: Path
    descriptor: int
    digest: hashlib._Hash
    bytes_written: int = 0
    closed: bool = False

    def write(self, data: bytes, maximum: int) -> None:
        if self.bytes_written + len(data) > maximum:
            raise ApiError(413, "payload_too_large", "Uploaded file is too large.")
        view = memoryview(data)
        while view:
            written = os.write(self.descriptor, view)
            if written <= 0:
                raise OSError(errno.EIO, "short file write")
            view = view[written:]
        self.digest.update(data)
        self.bytes_written += len(data)

    def close(self) -> None:
        if not self.closed:
            os.close(self.descriptor)
            self.closed = True


class FileService:
    """Keeps object paths and SQLite mutations out of HTTP route handlers."""

    def __init__(self, store: LocalStateStore) -> None:
        self.store = store

    def start_upload(self, session_id: str) -> UploadSink:
        self._cleanup_expired_state()
        self._ensure_safe_directory(self.store.staging_dir)
        self._ensure_safe_directory(self.store.objects_dir)
        file_id = str(uuid.uuid4())
        transfer_id = str(uuid.uuid4())
        staging_path = self.store.staging_dir / f"{transfer_id}.part"
        try:
            descriptor = os.open(
                staging_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
        except OSError as error:
            raise _storage_error(error) from None
        now = self.store.now_epoch()
        try:
            with self.store.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO transfer_records (
                        transfer_id, session_id, file_id, direction, state, bytes_transferred,
                        size_bytes, error_code, started_at, finished_at
                    ) VALUES (?, ?, ?, 'upload', 'started', 0, NULL, NULL, ?, NULL)
                    """,
                    (transfer_id, session_id, file_id, now),
                )
        except Exception:
            os.close(descriptor)
            self._unlink_regular(staging_path)
            raise
        return UploadSink(
            file_id=file_id,
            transfer_id=transfer_id,
            session_id=session_id,
            staging_path=staging_path,
            descriptor=descriptor,
            digest=hashlib.sha256(),
        )

    def finish_upload(self, sink: UploadSink, display_name: str) -> tuple[FileRecord, TransferRecord]:
        sink.close()
        display_name = validate_display_name(display_name)
        self._ensure_safe_directory(self.store.staging_dir)
        self._ensure_safe_directory(self.store.objects_dir)
        self._ensure_regular_file(sink.staging_path)
        content_sha256 = sink.digest.hexdigest()
        etag = f'"sha256:{content_sha256}"'
        object_path = self.store.objects_dir / sink.file_id
        linked = False
        try:
            with self.store.transaction() as connection:
                # BEGIN IMMEDIATE serializes this check with file publication across
                # processes sharing the SQLite state.  Active staging files may
                # exist, but only one metadata/object publication can pass quota.
                self._assert_capacity(connection, sink.bytes_written)
                # A hard link is same-volume and fails if a collision already exists;
                # it never replaces an immutable published object.
                os.link(sink.staging_path, object_path)
                linked = True
                now = self.store.now_epoch()
                connection.execute(
                    """
                    INSERT INTO file_metadata (
                        file_id, display_name, storage_key, size_bytes, content_sha256, etag,
                        created_at, publisher_session_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sink.file_id,
                        display_name,
                        sink.file_id,
                        sink.bytes_written,
                        content_sha256,
                        etag,
                        now,
                        sink.session_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE transfer_records
                    SET state = 'completed', bytes_transferred = ?, size_bytes = ?, finished_at = ?
                    WHERE transfer_id = ? AND state = 'started'
                    """,
                    (sink.bytes_written, sink.bytes_written, now, sink.transfer_id),
                )
        except Exception:
            if linked:
                self._unlink_regular(object_path)
            raise
        self._unlink_regular(sink.staging_path)
        record = FileRecord(
            file_id=sink.file_id,
            display_name=display_name,
            storage_key=sink.file_id,
            size_bytes=sink.bytes_written,
            content_sha256=content_sha256,
            etag=etag,
            created_at=_to_datetime(now),
            publisher_session_id=sink.session_id,
        )
        return record, self.get_transfer_for_session(sink.session_id, sink.transfer_id)

    def fail_upload(self, sink: UploadSink, error_code: str) -> None:
        # Cleanup is intentionally idempotent: the route's finally block may run
        # after any point in multipart parsing or publication failure.
        try:
            sink.close()
        except OSError:
            pass
        try:
            self._unlink_regular(sink.staging_path)
        finally:
            self._finish_transfer(sink.transfer_id, "failed", sink.bytes_written, error_code)

    def list_files(self, limit: int) -> list[FileRecord]:
        with self.store.connection() as connection:
            rows = connection.execute(
                """
                SELECT file_id, display_name, storage_key, size_bytes, content_sha256, etag,
                       created_at, publisher_session_id
                FROM file_metadata ORDER BY created_at DESC, file_id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_file_from_row(row) for row in rows]

    def get_file(self, raw_file_id: str) -> FileRecord:
        file_id = _parse_uuid(raw_file_id, "file_id")
        with self.store.connection() as connection:
            row = connection.execute(
                """
                SELECT file_id, display_name, storage_key, size_bytes, content_sha256, etag,
                       created_at, publisher_session_id
                FROM file_metadata WHERE file_id = ?
                """,
                (file_id,),
            ).fetchone()
        if row is None:
            raise ApiError(404, "resource_not_found", "The requested resource does not exist.")
        return _file_from_row(row)

    def start_download(self, session_id: str, record: FileRecord) -> TransferRecord:
        self._safe_object_path(record)
        self._cleanup_expired_state()
        transfer_id = str(uuid.uuid4())
        now = self.store.now_epoch()
        with self.store.transaction() as connection:
            connection.execute(
                """
                INSERT INTO transfer_records (
                    transfer_id, session_id, file_id, direction, state, bytes_transferred,
                    size_bytes, error_code, started_at, finished_at
                ) VALUES (?, ?, ?, 'download', 'started', 0, ?, NULL, ?, NULL)
                """,
                (transfer_id, session_id, record.file_id, record.size_bytes, now),
            )
        return TransferRecord(
            transfer_id=transfer_id,
            session_id=session_id,
            file_id=record.file_id,
            direction="download",
            state="started",
            bytes_transferred=0,
            size_bytes=record.size_bytes,
            error_code=None,
            started_at=_to_datetime(now),
            finished_at=None,
        )

    def open_download(self, record: FileRecord):
        path = self._safe_object_path(record)
        try:
            handle = path.open("rb", buffering=0)
        except OSError as error:
            raise _storage_error(error) from None
        try:
            descriptor_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(descriptor_stat.st_mode) or descriptor_stat.st_size != record.size_bytes:
                raise ApiError(404, "resource_not_found", "The requested resource does not exist.")
            return handle
        except Exception:
            handle.close()
            raise

    def complete_download(self, transfer_id: str, bytes_sent: int) -> None:
        self._finish_transfer(transfer_id, "completed", bytes_sent, None)

    def fail_download(self, transfer_id: str, bytes_sent: int, error_code: str) -> None:
        self._finish_transfer(transfer_id, "failed", bytes_sent, error_code)

    def get_transfer_for_session(self, session_id: str, raw_transfer_id: str) -> TransferRecord:
        transfer_id = _parse_uuid(raw_transfer_id, "transfer_id")
        with self.store.connection() as connection:
            row = connection.execute(
                """
                SELECT transfer_id, session_id, file_id, direction, state, bytes_transferred,
                       size_bytes, error_code, started_at, finished_at
                FROM transfer_records WHERE transfer_id = ? AND session_id = ?
                """,
                (transfer_id, session_id),
            ).fetchone()
        if row is None:
            raise ApiError(404, "resource_not_found", "The requested resource does not exist.")
        return _transfer_from_row(row)

    def _finish_transfer(self, transfer_id: str, state: str, bytes_transferred: int, error_code: str | None) -> None:
        now = self.store.now_epoch()
        with self.store.transaction() as connection:
            connection.execute(
                """
                UPDATE transfer_records
                SET state = ?, bytes_transferred = ?, error_code = ?, finished_at = ?
                WHERE transfer_id = ? AND state = 'started'
                """,
                (state, bytes_transferred, error_code, now, transfer_id),
            )

    def _assert_capacity(self, connection, incoming_bytes: int) -> None:
        used = connection.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM file_metadata").fetchone()[0]
        if used + incoming_bytes > self.store.settings.max_managed_bytes:
            raise ApiError(507, "insufficient_storage", "Managed storage quota is exhausted.")
        try:
            free = shutil.disk_usage(self.store.objects_dir).free
        except OSError as error:
            raise _storage_error(error) from None
        if free - incoming_bytes < self.store.settings.min_free_bytes:
            raise ApiError(507, "insufficient_storage", "Insufficient free storage is available.")

    def _safe_object_path(self, record: FileRecord) -> Path:
        self._ensure_safe_directory(self.store.objects_dir)
        if record.storage_key != record.file_id:
            raise ApiError(404, "resource_not_found", "The requested resource does not exist.")
        path = self.store.objects_dir / record.storage_key
        try:
            self._ensure_regular_file(path)
        except FileNotFoundError:
            raise ApiError(404, "resource_not_found", "The requested resource does not exist.") from None
        return path

    def _cleanup_expired_state(self) -> None:
        cutoff = self.store.now_epoch() - self.store.settings.transfer_retention_seconds
        with self.store.transaction() as connection:
            connection.execute(
                "DELETE FROM transfer_records WHERE finished_at IS NOT NULL AND finished_at < ?",
                (cutoff,),
            )
        try:
            for entry in self.store.staging_dir.iterdir():
                try:
                    details = os.lstat(entry)
                    if stat.S_ISREG(details.st_mode) and not _is_reparse(details) and details.st_mtime < cutoff:
                        entry.unlink()
                except OSError:
                    continue
        except OSError:
            return

    @staticmethod
    def _ensure_safe_directory(path: Path) -> None:
        details = os.lstat(path)
        if not stat.S_ISDIR(details.st_mode) or _is_reparse(details):
            raise ApiError(503, "service_unavailable", "Managed storage is unavailable.")

    @staticmethod
    def _ensure_regular_file(path: Path) -> None:
        details = os.lstat(path)
        if not stat.S_ISREG(details.st_mode) or _is_reparse(details):
            raise ApiError(404, "resource_not_found", "The requested resource does not exist.")

    @staticmethod
    def _unlink_regular(path: Path) -> None:
        try:
            details = os.lstat(path)
            if stat.S_ISREG(details.st_mode) and not _is_reparse(details):
                path.unlink()
        except FileNotFoundError:
            return


def validate_display_name(value: str) -> str:
    if not 1 <= len(value) <= 255:
        raise ApiError(422, "invalid_input", "display_name is invalid.")
    if value in {".", ".."} or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ApiError(422, "invalid_input", "display_name is invalid.")
    if any(character in '<>:"/\\|?*' for character in value):
        raise ApiError(422, "invalid_input", "display_name is invalid.")
    return value


def _parse_uuid(value: str, field: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise ApiError(422, "invalid_input", f"{field} is invalid.") from None


def _is_reparse(details: os.stat_result) -> bool:
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(details.st_mode) or bool(getattr(details, "st_file_attributes", 0) & reparse_attribute)


def _storage_error(error: OSError) -> ApiError:
    if error.errno in {errno.ENOSPC, errno.EDQUOT}:
        return ApiError(507, "insufficient_storage", "Insufficient storage is available.")
    return ApiError(507, "insufficient_storage", "Managed storage is unavailable.")


def _to_datetime(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, UTC)


def _file_from_row(row) -> FileRecord:
    return FileRecord(
        file_id=row["file_id"],
        display_name=row["display_name"],
        storage_key=row["storage_key"],
        size_bytes=row["size_bytes"],
        content_sha256=row["content_sha256"],
        etag=row["etag"],
        created_at=_to_datetime(row["created_at"]),
        publisher_session_id=row["publisher_session_id"],
    )


def _transfer_from_row(row) -> TransferRecord:
    return TransferRecord(
        transfer_id=row["transfer_id"],
        session_id=row["session_id"],
        file_id=row["file_id"],
        direction=row["direction"],
        state=row["state"],
        bytes_transferred=row["bytes_transferred"],
        size_bytes=row["size_bytes"],
        error_code=row["error_code"],
        started_at=_to_datetime(row["started_at"]),
        finished_at=_to_datetime(row["finished_at"]) if row["finished_at"] is not None else None,
    )
