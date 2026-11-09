"""SQLite-backed local state and the non-client-controlled storage layout."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings


class LocalStateStore:
    """Own local SQLite state without exposing filesystem paths to HTTP clients."""

    def __init__(self, settings: Settings, *, now: Callable[[], datetime] | None = None) -> None:
        self.settings = settings
        self._now = now or (lambda: datetime.now(UTC))
        self._initialize_layout()

    @property
    def database_path(self) -> Path:
        return self._data_dir / "state.sqlite3"

    @property
    def secret_path(self) -> Path:
        return self._data_dir / "server-secret.bin"

    @property
    def objects_dir(self) -> Path:
        return self._managed_root / "objects"

    @property
    def staging_dir(self) -> Path:
        return self._managed_root / "staging"

    @property
    def secret_key(self) -> bytes:
        return self._secret_key

    def now_epoch(self) -> int:
        return int(self._now().astimezone(UTC).timestamp())

    def _initialize_layout(self) -> None:
        if not self.settings.data_dir.is_absolute() or not self.settings.managed_root.is_absolute():
            raise ValueError("data_dir and managed_root must be absolute directories")
        data_dir = self.settings.data_dir.expanduser().resolve()
        managed_root = self.settings.managed_root.expanduser().resolve()
        if data_dir == managed_root or _is_within(data_dir, managed_root) or _is_within(
            managed_root, data_dir
        ):
            raise ValueError("data_dir and managed_root must be separate directories")

        self._data_dir = data_dir
        self._managed_root = managed_root
        data_dir.mkdir(parents=True, exist_ok=True)
        (managed_root / "objects").mkdir(parents=True, exist_ok=True)
        (managed_root / "staging").mkdir(parents=True, exist_ok=True)
        self._secret_key = self._load_or_create_secret()
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pairing_codes (
                    code_digest BLOB PRIMARY KEY,
                    scope TEXT NOT NULL CHECK (scope IN ('read', 'read_write')),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    used_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    token_digest BLOB NOT NULL,
                    scope TEXT NOT NULL CHECK (scope IN ('read', 'read_write')),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    revoked_at INTEGER,
                    device_label TEXT
                );

                CREATE TABLE IF NOT EXISTS pairing_failures (
                    key_type TEXT NOT NULL CHECK (key_type IN ('source', 'code')),
                    key_value BLOB NOT NULL,
                    window_started_at INTEGER NOT NULL,
                    failure_count INTEGER NOT NULL,
                    locked_until INTEGER,
                    PRIMARY KEY (key_type, key_value)
                );

                CREATE TABLE IF NOT EXISTS file_metadata (
                    file_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    storage_key TEXT NOT NULL UNIQUE,
                    size_bytes INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    etag TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    publisher_session_id TEXT REFERENCES sessions(session_id)
                );
                """
            )

    def _load_or_create_secret(self) -> bytes:
        if self.secret_path.exists():
            secret = self.secret_path.read_bytes()
            if len(secret) != 32:
                raise RuntimeError("local server secret has an invalid length")
            return secret

        secret = os.urandom(32)
        try:
            descriptor = os.open(
                self.secret_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return self._load_or_create_secret()
        with os.fdopen(descriptor, "wb") as secret_file:
            secret_file.write(secret)
        try:
            os.chmod(self.secret_path, 0o600)
        except OSError:
            # Windows ACL verification is documented and tested separately from POSIX mode bits.
            pass
        return secret

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True
