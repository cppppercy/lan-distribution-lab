"""SQLite persistence for controller-side distribution run records.

The database stores run identity, strategy, scheduling configuration, file
identity, per-client outcome and timing only.  Access tokens, cookies and
pairing codes are intentionally absent, and output paths are reduced to their
file name by the caller.

The schema is migrated in place: T007 databases created with only the
``sequential``/``parallel`` CHECK constraint are rebuilt with the
``controlled`` strategy allowed, and the T008 scheduling/timing columns are
added.  Existing rows are copied unchanged, repeated initialisation is a no-op
and the migration never deletes the database.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .distribution import DistributionError, DistributionRun

_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS distribution_runs (
    run_id TEXT PRIMARY KEY,
    strategy TEXT NOT NULL CHECK (strategy IN ('sequential', 'parallel', 'controlled')),
    file_id TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    content_sha256 TEXT NOT NULL,
    etag TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_seconds REAL NOT NULL CHECK (duration_seconds >= 0),
    succeeded INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    cancelled INTEGER NOT NULL,
    max_concurrent INTEGER,
    observed_max_active INTEGER NOT NULL DEFAULT 0
)
"""

_CLIENTS_DDL = """
CREATE TABLE IF NOT EXISTS distribution_clients (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES distribution_runs(run_id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'cancelled')),
    queued_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    wait_seconds REAL NOT NULL DEFAULT 0.0,
    duration_seconds REAL NOT NULL CHECK (duration_seconds >= 0),
    downloaded_bytes INTEGER NOT NULL CHECK (downloaded_bytes >= 0),
    resumed_bytes INTEGER NOT NULL CHECK (resumed_bytes >= 0),
    error_type TEXT,
    output_name TEXT NOT NULL
)
"""

_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS distribution_clients_run_idx
    ON distribution_clients(run_id)
"""

_RUNS_COLUMNS = (
    "run_id, strategy, file_id, size_bytes, content_sha256, etag, "
    "started_at, finished_at, duration_seconds, succeeded, failed, cancelled, "
    "max_concurrent, observed_max_active"
)

_CLIENTS_COLUMNS = (
    "task_id, run_id, client_id, status, queued_at, started_at, finished_at, "
    "wait_seconds, duration_seconds, downloaded_bytes, resumed_bytes, "
    "error_type, output_name"
)


def _table_sql(connection: sqlite3.Connection, table: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return str(row["sql"]) if row is not None and row["sql"] else ""


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


class DistributionStore:
    """Append one immutable record per distribution run; migrate T007 databases in place."""

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
                connection.execute("PRAGMA foreign_keys = OFF")
                try:
                    connection.executescript(f"{_RUNS_DDL};{_CLIENTS_DDL};{_INDEX_DDL};")
                    self._migrate(connection)
                finally:
                    connection.execute("PRAGMA foreign_keys = ON")
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise DistributionError(
                        "The distribution record database failed its integrity check."
                    )
        except (OSError, sqlite3.Error) as error:
            raise DistributionError("Could not open the distribution record database.") from error

    def _migrate(self, connection: sqlite3.Connection) -> None:
        self._migrate_runs(connection)
        self._migrate_clients(connection)

    def _migrate_runs(self, connection: sqlite3.Connection) -> None:
        if "'controlled'" not in _table_sql(connection, "distribution_runs"):
            self._rebuild_runs_table(connection)
        columns = _column_names(connection, "distribution_runs")
        additions: list[str] = []
        if "max_concurrent" not in columns:
            additions.append("max_concurrent INTEGER")
        if "observed_max_active" not in columns:
            additions.append("observed_max_active INTEGER NOT NULL DEFAULT 0")
        self._add_columns(connection, "distribution_runs", additions)

    def _migrate_clients(self, connection: sqlite3.Connection) -> None:
        columns = _column_names(connection, "distribution_clients")
        additions: list[str] = []
        if "queued_at" not in columns:
            additions.append("queued_at TEXT")
        if "wait_seconds" not in columns:
            additions.append("wait_seconds REAL NOT NULL DEFAULT 0.0")
        self._add_columns(connection, "distribution_clients", additions)

    def _add_columns(
        self, connection: sqlite3.Connection, table: str, additions: list[str]
    ) -> None:
        if not additions:
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            for clause in additions:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {clause}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _rebuild_runs_table(self, connection: sqlite3.Connection) -> None:
        """Replace the T007 runs table so the strategy CHECK accepts ``controlled``."""
        old_columns = _column_names(connection, "distribution_runs")
        if not old_columns:
            return
        max_concurrent = "max_concurrent" if "max_concurrent" in old_columns else "NULL"
        observed = (
            "observed_max_active" if "observed_max_active" in old_columns else "0"
        )
        select_columns = (
            "run_id, strategy, file_id, size_bytes, content_sha256, etag, "
            "started_at, finished_at, duration_seconds, succeeded, failed, cancelled, "
            f"{max_concurrent} AS max_concurrent, {observed} AS observed_max_active"
        )
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = connection.execute(f"SELECT {select_columns} FROM distribution_runs").fetchall()
            connection.execute("DROP TABLE distribution_runs")
            connection.execute(_RUNS_DDL.replace("IF NOT EXISTS ", ""))
            connection.executemany(
                f"INSERT INTO distribution_runs ({_RUNS_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(row) for row in rows],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

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
                    f"INSERT OR REPLACE INTO distribution_runs ({_RUNS_COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        run.max_concurrent,
                        run.observed_max_active,
                    ),
                )
                connection.executemany(
                    f"INSERT OR REPLACE INTO distribution_clients ({_CLIENTS_COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            client.task_id,
                            run.run_id,
                            client.client_id,
                            client.status,
                            client.queued_at.isoformat() if client.queued_at else None,
                            client.started_at.isoformat() if client.started_at else None,
                            client.finished_at.isoformat() if client.finished_at else None,
                            client.wait_seconds,
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
