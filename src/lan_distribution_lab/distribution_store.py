"""SQLite persistence for controller-side distribution run records.

The database stores run identity, strategy, file identity, per-client outcome
and timing only.  Access tokens, cookies and pairing codes are intentionally
absent, and output paths are reduced to their file name by the caller.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .distribution import DistributionError, DistributionRun

_SCHEMA = """
CREATE TABLE IF NOT EXISTS distribution_runs (
    run_id TEXT PRIMARY KEY,
    strategy TEXT NOT NULL CHECK (strategy IN ('sequential', 'parallel')),
    file_id TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    content_sha256 TEXT NOT NULL,
    etag TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_seconds REAL NOT NULL CHECK (duration_seconds >= 0),
    succeeded INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    cancelled INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS distribution_clients (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES distribution_runs(run_id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'cancelled')),
    started_at TEXT,
    finished_at TEXT,
    duration_seconds REAL NOT NULL CHECK (duration_seconds >= 0),
    downloaded_bytes INTEGER NOT NULL CHECK (downloaded_bytes >= 0),
    resumed_bytes INTEGER NOT NULL CHECK (resumed_bytes >= 0),
    error_type TEXT,
    output_name TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS distribution_clients_run_idx
    ON distribution_clients(run_id);
"""


class DistributionStore:
    """Append one immutable record per distribution run."""

    def __init__(self, path: Path) -> None:
        raw = Path(path).expanduser()
        if raw.is_symlink():
            raise DistributionError("The distribution record path must be a regular file.")
        self.path = raw.resolve(strict=False)
        if self.path.exists() and self.path.is_dir():
            raise DistributionError("The distribution record path must be a regular file.")
        self._initialize()

    def _initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connection() as connection:
                connection.executescript(_SCHEMA)
        except (OSError, sqlite3.Error) as error:
            raise DistributionError("Could not open the distribution record database.") from error

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
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

    def save(self, run: DistributionRun) -> None:
        counts = run.counts()
        try:
            with self.transaction() as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO distribution_runs (
                        run_id, strategy, file_id, size_bytes, content_sha256, etag,
                        started_at, finished_at, duration_seconds,
                        succeeded, failed, cancelled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.run_id,
                        run.strategy,
                        run.file_id,
                        run.size_bytes,
                        run.content_sha256,
                        run.etag,
                        run.started_at.isoformat(),
                        run.finished_at.isoformat(),
                        run.duration_seconds,
                        counts["succeeded"],
                        counts["failed"],
                        counts["cancelled"],
                    ),
                )
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO distribution_clients (
                        task_id, run_id, client_id, status, started_at, finished_at,
                        duration_seconds, downloaded_bytes, resumed_bytes,
                        error_type, output_name
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            client.task_id,
                            run.run_id,
                            client.client_id,
                            client.status,
                            client.started_at.isoformat() if client.started_at else None,
                            client.finished_at.isoformat() if client.finished_at else None,
                            client.duration_seconds,
                            client.downloaded_bytes,
                            client.resumed_bytes,
                            client.error_type,
                            client.output_name,
                        )
                        for client in run.clients
                    ],
                )
        except sqlite3.Error as error:
            raise DistributionError("Could not persist the distribution record.") from error
