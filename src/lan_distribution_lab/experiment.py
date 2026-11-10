"""Repeatable controller-side experiment runner built on ``run_distribution``.

This module records application-level observations only.  Logical clients run
inside the controller process and are never labelled as independent wireless
devices.  Credentials are accepted as an in-memory sequence and deliberately
never enter manifests, CSV files, trial JSON or error strings.
"""

from __future__ import annotations

import csv
import json
import os
import platform
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Sequence

from .distribution import (
    MAX_CLIENTS,
    MAX_CONCURRENT,
    STRATEGIES,
    ClientResult,
    DistributionError,
    DistributionRun,
    build_client_tasks,
    prepare_output_directory,
    run_distribution,
    write_result_json,
)
from .client import (
    AuthenticationError,
    DownloadError,
    IntegrityError,
    LocalStorageError,
    NetworkError,
    ProtocolError,
    normalize_server,
)

MAX_FILE_IDS = 4
MAX_ROUNDS = 10
MAX_TRIALS = 100
FORMAT_VERSION = 1
EnvironmentType = Literal["local_loopback", "controller_to_lan"]
OnError = Literal["continue", "stop"]


class ExperimentError(RuntimeError):
    """A safe configuration/output error for the experiment controller."""


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    server: str
    file_ids: tuple[str, ...]
    strategies: tuple[str, ...]
    clients: int
    max_concurrent: int | None
    rounds: int
    timeout_seconds: float
    retries: int
    environment_type: EnvironmentType
    on_error: OnError = "continue"


@dataclass(frozen=True, slots=True)
class TrialPlan:
    ordinal: int
    round_number: int
    strategy: str
    file_id: str
    trial_id: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_config(config: ExperimentConfig) -> None:
    if not config.file_ids or len(config.file_ids) > MAX_FILE_IDS:
        raise ExperimentError(f"Select between 1 and {MAX_FILE_IDS} file_id values.")
    if len(set(config.file_ids)) != len(config.file_ids):
        raise ExperimentError("Each file_id must appear only once in an experiment configuration.")
    try:
        for file_id in config.file_ids:
            uuid.UUID(file_id)
        # This rejects userinfo/query/fragment before the server address can
        # reach the initial manifest.  A valid bare origin contains no token.
        normalize_server(config.server)
    except (ValueError, DownloadError) as error:
        raise ExperimentError("server must be a bare HTTP(S) origin and file_id values must be UUIDs.") from error
    if not config.strategies or any(strategy not in STRATEGIES for strategy in config.strategies):
        raise ExperimentError("Strategies must be sequential, parallel or controlled.")
    if len(set(config.strategies)) != len(config.strategies):
        raise ExperimentError("Each strategy must appear only once.")
    if not 1 <= config.clients <= MAX_CLIENTS:
        raise ExperimentError(f"clients must be between 1 and {MAX_CLIENTS}.")
    if not 1 <= config.rounds <= MAX_ROUNDS:
        raise ExperimentError(f"rounds must be between 1 and {MAX_ROUNDS}.")
    trial_count = len(config.file_ids) * len(config.strategies) * config.rounds
    if trial_count > MAX_TRIALS:
        raise ExperimentError(f"Experiment would create {trial_count} trials; maximum is {MAX_TRIALS}.")
    if config.timeout_seconds <= 0 or config.retries < 0:
        raise ExperimentError("timeout must be positive and retries cannot be negative.")
    if config.environment_type not in {"local_loopback", "controller_to_lan"}:
        raise ExperimentError("environment_type must describe the actual controller deployment.")
    if config.on_error not in {"continue", "stop"}:
        raise ExperimentError("on_error must be continue or stop.")
    if "controlled" in config.strategies:
        if config.max_concurrent is None or not 1 <= config.max_concurrent <= config.clients:
            raise ExperimentError("controlled strategy requires max_concurrent between 1 and clients.")
        if config.max_concurrent > MAX_CONCURRENT:
            raise ExperimentError(f"max_concurrent cannot exceed {MAX_CONCURRENT}.")
    elif config.max_concurrent is not None:
        raise ExperimentError("max_concurrent is only meaningful when controlled is selected.")


def parse_strategies(value: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items:
        raise ExperimentError("At least one strategy is required.")
    return items


def prepare_experiment_directory(value: Path) -> Path:
    """Create an entirely new result root; never merge with a prior experiment."""
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise ExperimentError("Experiment output directory must not be a symbolic link.")
    destination = candidate.resolve(strict=False)
    if destination.exists():
        raise ExperimentError("Experiment output directory already exists; refusing to mix or overwrite data.")
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        raise ExperimentError("Could not create the experiment output directory.") from error
    return destination


def make_trial_plan(config: ExperimentConfig) -> list[TrialPlan]:
    """Stable file→strategy order, repeated by round; no unrecorded randomisation."""
    plans: list[TrialPlan] = []
    for round_number in range(1, config.rounds + 1):
        for file_id in config.file_ids:
            for strategy in config.strategies:
                plans.append(
                    TrialPlan(len(plans) + 1, round_number, strategy, file_id, str(uuid.uuid4()))
                )
    return plans


def _git_revision() -> str:
    root = Path(__file__).resolve().parents[2]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        value = completed.stdout.strip()
        if value:
            return value
    except (OSError, subprocess.SubprocessError):
        pass
    # The Windows launcher may not have Git on PATH.  A normal checkout still
    # exposes HEAD without invoking a shell, so preserve the best available
    # revision identifier in the manifest rather than dropping it to unknown.
    try:
        git_dir = root / ".git"
        head = (git_dir / "HEAD").read_text(encoding="ascii").strip()
        if not head.startswith("ref: "):
            return head if len(head) >= 7 else "unknown"
        reference = head.removeprefix("ref: ")
        loose_ref = git_dir / reference
        if loose_ref.is_file():
            value = loose_ref.read_text(encoding="ascii").strip()
            return value if len(value) >= 7 else "unknown"
        packed = git_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="ascii").splitlines():
                if line and not line.startswith(("#", "^")):
                    value, _, name = line.partition(" ")
                    if name == reference and len(value) >= 7:
                        return value
    except OSError:
        pass
    return "unknown"


def _atomic_json(path: Path, payload: object) -> None:
    _atomic_bytes(path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))


def _atomic_csv(path: Path, rows: list[dict[str, object]], fields: Sequence[str]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ExperimentError("Could not write experiment CSV output.") from error


def _atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ExperimentError("Could not write experiment JSON output.") from error


TRIAL_FIELDS = (
    "experiment_id", "trial_id", "round", "strategy", "file_id", "size_bytes", "clients",
    "max_concurrent", "duration_seconds", "succeeded", "failed", "cancelled",
    "observed_max_active", "logical_completed_bytes", "application_payload_bytes",
    "application_payload_throughput_Bps", "resumed_bytes", "state", "error_type",
)
CLIENT_FIELDS = (
    "experiment_id", "trial_id", "round", "strategy", "file_id", "client_id", "status",
    "wait_seconds", "duration_seconds", "downloaded_bytes", "resumed_bytes", "error_type",
    "integrity_verified",
)


class ExperimentRunner:
    """Persist every finished trial before beginning the next one."""

    def __init__(
        self,
        config: ExperimentConfig,
        tokens: Sequence[str],
        output_dir: Path,
        *,
        distribution_runner: Callable[..., DistributionRun] = run_distribution,
    ) -> None:
        validate_config(config)
        if len(tokens) != config.clients or any(not token.strip() for token in tokens):
            raise ExperimentError("One non-empty in-memory token is required for every logical client.")
        if len(set(tokens)) != len(tokens):
            raise ExperimentError("Every logical client must use a distinct credential.")
        self.config = config
        self._tokens = tuple(tokens)
        self.output_dir = output_dir
        self._distribution_runner = distribution_runner
        self.experiment_id = str(uuid.uuid4())
        self.started_at = utc_now()
        self.plans = make_trial_plan(config)
        self.trial_rows: list[dict[str, object]] = []
        self.client_rows: list[dict[str, object]] = []
        self.trial_index: list[dict[str, object]] = []
        self.state = "running"

    def run(self) -> Path:
        self._write_exports()
        stopped = False
        active_plan: TrialPlan | None = None
        try:
            for plan in self.plans:
                active_plan = plan
                if stopped:
                    self._record_not_started(plan, "not_started")
                else:
                    stopped = self._run_one(plan)
                self._write_exports()
        except KeyboardInterrupt:
            if active_plan is not None and not self._has_record(active_plan):
                # A Ctrl+C can arrive while an individual downloader is active.
                # Its output may be incomplete, but this trial was not a success.
                self._record_failure(active_plan, "interrupted", "cancelled")
            self.state = "interrupted"
            self._write_exports()
            raise
        self.state = "completed" if not stopped else "completed_with_cancelled_trials"
        self._write_exports()
        return self.output_dir

    def _run_one(self, plan: TrialPlan) -> bool:
        trial_dir = self.output_dir / "trials" / f"trial-{plan.ordinal:03d}"
        try:
            trial_dir.mkdir(parents=True, exist_ok=False)
            tasks = build_client_tasks(prepare_output_directory(trial_dir), "download.bin", self._tokens)
            run = self._distribution_runner(
                self.config.server,
                plan.file_id,
                plan.strategy,
                tasks,
                max_concurrent=self.config.max_concurrent if plan.strategy == "controlled" else None,
                timeout_seconds=self.config.timeout_seconds,
                retries=self.config.retries,
            )
            write_result_json(trial_dir / "distribution-result.json", run)
            self._record_run(plan, run, raw_file="distribution-result.json")
            return self.config.on_error == "stop" and run.counts()["succeeded"] != self.config.clients
        except (DistributionError, DownloadError, ExperimentError) as error:
            self._record_failure(plan, _safe_error_type(error), "failed")
            return self.config.on_error == "stop"
        except Exception:
            # Do not persist an arbitrary exception string: it may contain
            # server-provided text.  The failed trial remains observable.
            self._record_failure(plan, "internal", "failed")
            return self.config.on_error == "stop"

    def _has_record(self, plan: TrialPlan) -> bool:
        return any(row["trial_id"] == plan.trial_id for row in self.trial_rows)

    def _record_run(self, plan: TrialPlan, run: DistributionRun, *, raw_file: str) -> None:
        counts = run.counts()
        completed_bytes = sum(client.downloaded_bytes for client in run.clients)
        resumed_bytes = sum(client.resumed_bytes for client in run.clients)
        payload_bytes = counts["succeeded"] * run.size_bytes
        throughput = payload_bytes / run.duration_seconds if run.duration_seconds > 0 else 0.0
        state = "completed" if counts["failed"] == counts["cancelled"] == 0 else "completed_with_failures"
        error_types = sorted({client.error_type for client in run.clients if client.error_type})
        self.trial_rows.append({
            "experiment_id": self.experiment_id, "trial_id": plan.trial_id, "round": plan.round_number,
            "strategy": plan.strategy, "file_id": run.file_id, "size_bytes": run.size_bytes,
            "clients": self.config.clients, "max_concurrent": run.max_concurrent or "",
            "duration_seconds": run.duration_seconds, "succeeded": counts["succeeded"],
            "failed": counts["failed"], "cancelled": counts["cancelled"],
            "observed_max_active": run.observed_max_active, "logical_completed_bytes": completed_bytes,
            "application_payload_bytes": payload_bytes,
            "application_payload_throughput_Bps": throughput, "resumed_bytes": resumed_bytes,
            "state": state, "error_type": ";".join(error_types),
        })
        self.trial_index.append({"trial_id": plan.trial_id, "ordinal": plan.ordinal, "path": f"trials/trial-{plan.ordinal:03d}/{raw_file}", "state": state})
        for client in run.clients:
            self.client_rows.append(_client_row(self.experiment_id, plan, run.file_id, client))

    def _record_failure(self, plan: TrialPlan, error_type: str, state: str) -> None:
        self.trial_rows.append({
            "experiment_id": self.experiment_id, "trial_id": plan.trial_id, "round": plan.round_number,
            "strategy": plan.strategy, "file_id": plan.file_id, "size_bytes": "", "clients": self.config.clients,
            "max_concurrent": self.config.max_concurrent if plan.strategy == "controlled" else "",
            "duration_seconds": 0.0, "succeeded": 0, "failed": self.config.clients if state == "failed" else 0,
            "cancelled": self.config.clients if state != "failed" else 0, "observed_max_active": 0,
            "logical_completed_bytes": 0, "application_payload_bytes": 0,
            "application_payload_throughput_Bps": 0.0, "resumed_bytes": 0, "state": state,
            "error_type": error_type,
        })
        self.trial_index.append({"trial_id": plan.trial_id, "ordinal": plan.ordinal, "path": None, "state": state})
        for index in range(self.config.clients):
            self.client_rows.append({
                "experiment_id": self.experiment_id, "trial_id": plan.trial_id, "round": plan.round_number,
                "strategy": plan.strategy, "file_id": plan.file_id, "client_id": f"client-{index + 1}",
                "status": "failed" if state == "failed" else "cancelled", "wait_seconds": 0.0,
                "duration_seconds": 0.0, "downloaded_bytes": 0, "resumed_bytes": 0,
                "error_type": error_type, "integrity_verified": False,
            })

    def _record_not_started(self, plan: TrialPlan, error_type: str) -> None:
        self._record_failure(plan, error_type, "cancelled")

    def _write_exports(self) -> None:
        _atomic_csv(self.output_dir / "trials.csv", self.trial_rows, TRIAL_FIELDS)
        _atomic_csv(self.output_dir / "clients.csv", self.client_rows, CLIENT_FIELDS)
        _atomic_json(self.output_dir / "experiments.json", {
            "schema_version": FORMAT_VERSION,
            "experiment_id": self.experiment_id,
            "state": self.state,
            "started_at": iso(self.started_at),
            "updated_at": iso(utc_now()),
            "configuration": {
                "server": self.config.server,
                "file_ids": list(self.config.file_ids),
                "strategies": list(self.config.strategies),
                "clients": self.config.clients,
                "max_concurrent": self.config.max_concurrent,
                "rounds": self.config.rounds,
                "timeout_seconds": self.config.timeout_seconds,
                "retries": self.config.retries,
                "environment_type": self.config.environment_type,
                "order": "round,file_id,strategy (deterministic; no random seed)",
                "on_error": self.config.on_error,
            },
            "software": {"git_commit": _git_revision(), "python": sys.version.split()[0], "platform": platform.platform()},
            "trials": self.trial_index,
            "statistics": _statistics(self.trial_rows, self.client_rows),
        })


def _client_row(experiment_id: str, plan: TrialPlan, file_id: str, client: ClientResult) -> dict[str, object]:
    return {
        "experiment_id": experiment_id, "trial_id": plan.trial_id, "round": plan.round_number,
        "strategy": plan.strategy, "file_id": file_id, "client_id": client.client_id,
        "status": client.status, "wait_seconds": client.wait_seconds,
        "duration_seconds": client.duration_seconds, "downloaded_bytes": client.downloaded_bytes,
        "resumed_bytes": client.resumed_bytes, "error_type": client.error_type or "",
        "integrity_verified": client.status == "succeeded",
    }


def _statistics(
    rows: Sequence[dict[str, object]], client_rows: Sequence[dict[str, object]]
) -> dict[str, object]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["strategy"]), []).append(row)
    summary: dict[str, object] = {}
    for strategy, values in grouped.items():
        successful = [row for row in values if row["state"] == "completed"]
        strategy_clients = [
            row for row in client_rows if row["strategy"] == strategy
        ]
        successful_clients = [
            row for row in strategy_clients if row["status"] == "succeeded"
        ]
        summary[strategy] = {
            "trials": len(values),
            "successful_trials": len(successful),
            "success_rate": len(successful) / len(values) if values else 0.0,
            "successful_trial_duration_seconds": _descriptive(
                [float(row["duration_seconds"]) for row in successful]
            ),
            "successful_application_payload_throughput_Bps": _descriptive(
                [float(row["application_payload_throughput_Bps"]) for row in successful]
            ),
            "all_trial_logical_completed_bytes": _descriptive(
                [float(row["logical_completed_bytes"]) for row in values]
            ),
            "all_trial_resumed_bytes": _descriptive(
                [float(row["resumed_bytes"]) for row in values]
            ),
            "observed_max_active": _descriptive(
                [float(row["observed_max_active"]) for row in values]
            ),
            "successful_client_duration_seconds": _descriptive(
                [float(row["duration_seconds"]) for row in successful_clients]
            ),
            "all_client_wait_seconds": _descriptive(
                [float(row["wait_seconds"]) for row in strategy_clients]
            ),
            "note": "application-level samples only; no link bandwidth or significance claim",
        }
    return summary


def _descriptive(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "min": None, "max": None}
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return {"mean": sum(ordered) / len(ordered), "median": median, "min": ordered[0], "max": ordered[-1]}


def _safe_error_type(error: BaseException) -> str:
    if isinstance(error, AuthenticationError):
        return "authentication"
    if isinstance(error, NetworkError):
        return "network"
    if isinstance(error, IntegrityError):
        return "integrity"
    if isinstance(error, LocalStorageError):
        return "storage"
    if isinstance(error, ProtocolError):
        return "protocol"
    if isinstance(error, DistributionError):
        return "distribution"
    if isinstance(error, ExperimentError):
        return "experiment"
    return "internal"
