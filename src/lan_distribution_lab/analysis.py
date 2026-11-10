"""Read-only validation, descriptive analysis and SVG reporting for T010 data.

The module deliberately accepts only the versioned T010 export format.  It is
not a packet sniffer and does not infer link-layer properties from controller
CSV files.  Invalid or incomparable input stops analysis before charts are
created, so a polished report can never disguise malformed observations.
"""

from __future__ import annotations

import csv
import html
import json
import math
import os
import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

from .distribution import STRATEGIES


ANALYSIS_SCHEMA_VERSION = 1
SUPPORTED_EXPERIMENT_SCHEMA = {1}
SUPPORTED_DISTRIBUTION_SCHEMA = {1, 2}
CHART_METRICS = ("duration", "throughput", "success", "controlled", "client-timing")


class AnalysisError(ValueError):
    """Raised for an input that cannot safely support a descriptive report."""


@dataclass(frozen=True, slots=True)
class Trial:
    experiment_id: str
    trial_id: str
    round_number: int
    strategy: str
    file_id: str
    size_bytes: int | None
    clients: int
    max_concurrent: int | None
    duration_seconds: float
    succeeded: int
    failed: int
    cancelled: int
    observed_max_active: int
    payload_bytes: int
    throughput_Bps: float
    state: str
    raw_path: str | None


@dataclass(frozen=True, slots=True)
class Client:
    experiment_id: str
    trial_id: str
    strategy: str
    client_id: str
    status: str
    wait_seconds: float
    duration_seconds: float
    downloaded_bytes: int
    integrity_verified: bool


@dataclass(frozen=True, slots=True)
class ExperimentData:
    root: Path
    experiment_id: str
    environment_type: str
    configuration: dict[str, Any]
    software: dict[str, Any]
    state: str
    trials: tuple[Trial, ...]
    clients: tuple[Client, ...]
    file_hashes: tuple[tuple[str, str], ...]


def analyze_experiments(
    inputs: Sequence[Path],
    output_dir: Path,
    *,
    metrics: Sequence[str] = CHART_METRICS,
    packet_summary: Path | None = None,
) -> Path:
    """Validate one or more T010 roots and atomically create analysis outputs."""
    if not inputs:
        raise AnalysisError("At least one experiment input directory is required.")
    selected = _validate_metrics(metrics)
    destination = _prepare_new_directory(output_dir)
    try:
        experiments = tuple(load_experiment(Path(value)) for value in inputs)
        if len({data.experiment_id for data in experiments}) != len(experiments):
            raise AnalysisError("Input directories must contain distinct experiment_id values.")
        packet = _load_packet_summary(packet_summary) if packet_summary is not None else None
        report = build_analysis(experiments, selected, packet)
        chart_paths = _write_charts(destination, report, selected)
        report["charts"] = chart_paths
        _atomic_json(destination / "analysis.json", report)
        _atomic_text(destination / "report.md", _markdown_report(report))
        return destination
    except Exception:
        # The output must be new; remove only its own empty/partial contents.
        # This is safe because `_prepare_new_directory` refuses an existing path.
        _remove_created_tree(destination)
        raise


def load_experiment(root: Path) -> ExperimentData:
    """Load and cross-check one T010 export root without modifying it."""
    source = Path(root).expanduser().resolve(strict=False)
    if not source.is_dir():
        raise AnalysisError("Experiment input must be an existing directory.")
    manifest = _read_json(source / "experiments.json", "experiments.json")
    if not isinstance(manifest, dict):
        raise AnalysisError("experiments.json must contain a JSON object.")
    version = _required_int(manifest, "schema_version", "experiments.json", minimum=1)
    if version not in SUPPORTED_EXPERIMENT_SCHEMA:
        raise AnalysisError(f"Unsupported T010 experiments.json schema_version: {version}.")
    experiment_id = _required_uuid(manifest, "experiment_id", "experiments.json")
    configuration = _required_object(manifest, "configuration", "experiments.json")
    environment_type = _required_string(configuration, "environment_type", "configuration")
    if environment_type not in {"local_loopback", "controller_to_lan"}:
        raise AnalysisError("configuration.environment_type is unsupported.")
    software = _required_object(manifest, "software", "experiments.json")
    state = _required_string(manifest, "state", "experiments.json")
    _validate_configuration(configuration)
    index = _required_list(manifest, "trials", "experiments.json")
    trials = _load_trials(source / "trials.csv", experiment_id, index, configuration)
    clients = _load_clients(source / "clients.csv", experiment_id, trials)
    _validate_trial_client_consistency(trials, clients)
    file_hashes = _validate_raw_records(source, trials, clients)
    if state == "completed" and any(trial.state != "completed" for trial in trials):
        raise AnalysisError("A completed experiment contains failed or cancelled trials.")
    if state != "completed" and all(trial.state == "completed" for trial in trials):
        raise AnalysisError("Experiment state contradicts an all-successful trial set.")
    return ExperimentData(source, experiment_id, environment_type, configuration, software, state, tuple(trials), tuple(clients), tuple(sorted(file_hashes.items())))


def build_analysis(
    experiments: Sequence[ExperimentData], metrics: Sequence[str], packet: dict[str, Any] | None
) -> dict[str, Any]:
    """Create only descriptive summaries; never rank incomparable conditions."""
    conditions: dict[tuple[Any, ...], list[Trial]] = defaultdict(list)
    client_map: dict[str, list[Client]] = defaultdict(list)
    experiment_lookup = {experiment.experiment_id: experiment for experiment in experiments}
    for experiment in experiments:
        for trial in experiment.trials:
            conditions[_comparison_key(experiment, trial)].append(trial)
        for client in experiment.clients:
            client_map[client.trial_id].append(client)

    condition_reports: list[dict[str, Any]] = []
    for number, (key, trials) in enumerate(sorted(conditions.items(), key=lambda item: str(item[0])), start=1):
        grouped: dict[str, list[Trial]] = defaultdict(list)
        for trial in trials:
            grouped[trial.strategy].append(trial)
        condition_reports.append({
            "condition_id": f"condition-{number:03d}",
            "comparison_key": _comparison_key_payload(key),
            "strategies": {strategy: _strategy_summary(values, client_map) for strategy, values in sorted(grouped.items())},
            "comparable_strategy_set": len(grouped) >= 2,
            "note": (
                "Strategies share file identity/size, client count, environment, timeout and retry settings "
                "inside this condition. Statistics are descriptive only."
                if len(grouped) >= 2
                else "Only one strategy is present; no strategy comparison is claimed."
            ),
        })
    provenance = [
        {
            "experiment_id": experiment.experiment_id,
            "environment_type": experiment.environment_type,
            "experiment_state": experiment.state,
            "git_commit": _safe_text(experiment.software.get("git_commit")),
            "trial_ids": [trial.trial_id for trial in experiment.trials],
        }
        for experiment in experiments
    ]
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "input_kind": "T010 experiment exports",
        "input_experiments": provenance,
        "conditions": condition_reports,
        "metrics": list(metrics),
        "packet_evidence": packet or {"status": "unavailable", "note": "No packet-summary input was supplied; no packet-level claim is made."},
        "limitations": [
            "application_payload_throughput_Bps is application payload divided by batch wall time, not TCP or NIC bandwidth.",
            "local_loopback and controller_to_lan use controller-side logical clients; neither proves independent physical-device or Wi-Fi performance.",
            "Failed and cancelled trials remain in completion-rate denominators; duration and throughput use fully completed trials only.",
            "No causal, significance, packet-loss or retransmission conclusion is generated without matching packet evidence.",
        ],
    }


def jain_fairness(values: Iterable[float]) -> float | None:
    """Return Jain's index for positive, finite same-unit samples, else unavailable."""
    samples = list(values)
    if len(samples) < 2 or any(not math.isfinite(value) or value <= 0 for value in samples):
        return None
    denominator = len(samples) * sum(value * value for value in samples)
    return (sum(samples) ** 2) / denominator if denominator else None


def _strategy_summary(trials: Sequence[Trial], clients_by_trial: dict[str, list[Client]]) -> dict[str, Any]:
    completed = [trial for trial in trials if trial.state == "completed"]
    partial = [trial for trial in trials if trial.state == "completed_with_failures"]
    failed = [trial for trial in trials if trial.state == "failed"]
    cancelled = [trial for trial in trials if trial.state == "cancelled"]
    successful_clients = [
        client for trial in trials for client in clients_by_trial[trial.trial_id] if client.status == "succeeded"
    ]
    all_clients = [client for trial in trials for client in clients_by_trial[trial.trial_id]]
    fairness = []
    for trial in completed:
        rates = [
            client.downloaded_bytes / client.duration_seconds
            for client in clients_by_trial[trial.trial_id]
            if client.status == "succeeded" and client.duration_seconds > 0
        ]
        value = jain_fairness(rates)
        if value is not None:
            fairness.append(value)
    return {
        "trial_counts": {
            "planned": len(trials), "completed": len(completed), "partial": len(partial),
            "failed": len(failed), "cancelled": len(cancelled),
            "complete_success_rate": len(completed) / len(trials) if trials else 0.0,
        },
        "client_counts": {
            "total": len(all_clients), "succeeded": len(successful_clients),
            "success_rate": len(successful_clients) / len(all_clients) if all_clients else 0.0,
        },
        "batch_duration_seconds_completed_trials": _descriptive([trial.duration_seconds for trial in completed]),
        "application_payload_throughput_Bps_completed_trials": _descriptive([trial.throughput_Bps for trial in completed]),
        "application_payload_throughput_MiBps_completed_trials": _descriptive([trial.throughput_Bps / (1024 * 1024) for trial in completed]),
        "observed_max_active_all_trials": _descriptive([float(trial.observed_max_active) for trial in trials]),
        "configured_max_concurrent_values": sorted({trial.max_concurrent for trial in trials if trial.max_concurrent is not None}),
        "client_wait_seconds_all_clients": _descriptive([client.wait_seconds for client in all_clients]),
        "client_execution_seconds_successful_clients": _descriptive([client.duration_seconds for client in successful_clients]),
        "jain_fairness_completed_trials": _descriptive(fairness),
        "fairness_definition": (
            "Per completed trial, x_i = successful client downloaded_bytes / client duration_seconds (B/s); "
            "J=(sum(x_i)^2)/(n*sum(x_i^2)). Failed, zero-duration and missing clients are excluded; fewer than two valid clients is unavailable."
        ),
    }


def _load_trials(path: Path, experiment_id: str, index: list[Any], configuration: dict[str, Any]) -> list[Trial]:
    rows = _read_csv(path, "trials.csv")
    _require_columns(rows, _TRIAL_COLUMNS, "trials.csv")
    expected = len(configuration["file_ids"]) * len(configuration["strategies"]) * configuration["rounds"]
    if len(rows) != expected or len(index) != expected:
        raise AnalysisError("Trial count does not match the T010 configuration or manifest index.")
    index_by_id: dict[str, dict[str, Any]] = {}
    for item in index:
        if not isinstance(item, dict):
            raise AnalysisError("experiments.json trials index must contain objects.")
        trial_id = _required_uuid(item, "trial_id", "experiments.json trials index")
        if trial_id in index_by_id:
            raise AnalysisError("Duplicate trial_id in experiments.json trials index.")
        index_by_id[trial_id] = item
    trials: list[Trial] = []
    seen: set[str] = set()
    for row in rows:
        if row["experiment_id"] != experiment_id:
            raise AnalysisError("trials.csv experiment_id does not match experiments.json.")
        trial_id = _csv_uuid(row, "trial_id", "trials.csv")
        if trial_id in seen:
            raise AnalysisError("Duplicate trial_id in trials.csv.")
        seen.add(trial_id)
        strategy = row["strategy"]
        if strategy not in STRATEGIES or strategy not in configuration["strategies"]:
            raise AnalysisError("trials.csv contains an unsupported or unplanned strategy.")
        file_id = _csv_uuid(row, "file_id", "trials.csv")
        if file_id not in configuration["file_ids"]:
            raise AnalysisError("trials.csv contains an unplanned file_id.")
        trial = Trial(
            experiment_id, trial_id, _csv_int(row, "round", "trials.csv", minimum=1), strategy, file_id,
            _csv_optional_int(row, "size_bytes", "trials.csv", minimum=0),
            _csv_int(row, "clients", "trials.csv", minimum=1),
            _csv_optional_int(row, "max_concurrent", "trials.csv", minimum=1),
            _csv_float(row, "duration_seconds", "trials.csv", minimum=0),
            _csv_int(row, "succeeded", "trials.csv", minimum=0), _csv_int(row, "failed", "trials.csv", minimum=0),
            _csv_int(row, "cancelled", "trials.csv", minimum=0), _csv_int(row, "observed_max_active", "trials.csv", minimum=0),
            _csv_int(row, "application_payload_bytes", "trials.csv", minimum=0),
            _csv_float(row, "application_payload_throughput_Bps", "trials.csv", minimum=0), row["state"], None,
        )
        _validate_trial(trial, configuration)
        item = index_by_id.get(trial_id)
        if item is None or item.get("state") != trial.state:
            raise AnalysisError("Trial state differs between trials.csv and experiments.json index.")
        raw_path = item.get("path")
        if raw_path is not None and (not isinstance(raw_path, str) or not raw_path):
            raise AnalysisError("Manifest raw trial path is invalid.")
        trials.append(replace(trial, raw_path=raw_path))
    if set(index_by_id) != seen:
        raise AnalysisError("Manifest trials index and trials.csv use different trial_id values.")
    return trials


def _load_clients(path: Path, experiment_id: str, trials: Sequence[Trial]) -> list[Client]:
    rows = _read_csv(path, "clients.csv")
    _require_columns(rows, _CLIENT_COLUMNS, "clients.csv")
    trial_ids = {trial.trial_id for trial in trials}
    clients: list[Client] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if row["experiment_id"] != experiment_id:
            raise AnalysisError("clients.csv experiment_id does not match experiments.json.")
        trial_id = _csv_uuid(row, "trial_id", "clients.csv")
        if trial_id not in trial_ids:
            raise AnalysisError("clients.csv contains an unknown trial_id.")
        key = (trial_id, row["client_id"])
        if not row["client_id"] or key in seen:
            raise AnalysisError("clients.csv has an empty or duplicate client_id within a trial.")
        seen.add(key)
        if row["status"] not in {"succeeded", "failed", "cancelled"}:
            raise AnalysisError("clients.csv contains an unsupported client status.")
        clients.append(Client(
            experiment_id, trial_id, row["strategy"], row["client_id"], row["status"],
            _csv_float(row, "wait_seconds", "clients.csv", minimum=0),
            _csv_float(row, "duration_seconds", "clients.csv", minimum=0),
            _csv_int(row, "downloaded_bytes", "clients.csv", minimum=0),
            _csv_bool(row, "integrity_verified", "clients.csv"),
        ))
    return clients


def _validate_trial_client_consistency(trials: Sequence[Trial], clients: Sequence[Client]) -> None:
    grouped: dict[str, list[Client]] = defaultdict(list)
    for client in clients:
        grouped[client.trial_id].append(client)
    for trial in trials:
        rows = grouped.get(trial.trial_id, [])
        if len(rows) != trial.clients or any(row.strategy != trial.strategy for row in rows):
            raise AnalysisError("clients.csv does not match trial client count or strategy.")
        counts = {name: sum(row.status == name for row in rows) for name in ("succeeded", "failed", "cancelled")}
        if counts != {"succeeded": trial.succeeded, "failed": trial.failed, "cancelled": trial.cancelled}:
            raise AnalysisError("Client status counts differ from trials.csv.")
        if trial.state == "completed" and (trial.duration_seconds <= 0 or not all(row.integrity_verified for row in rows)):
            raise AnalysisError("A completed trial needs positive duration and verified successful clients.")


def _validate_raw_records(root: Path, trials: Sequence[Trial], clients: Sequence[Client]) -> dict[str, str]:
    clients_by_trial: dict[str, list[Client]] = defaultdict(list)
    for client in clients:
        clients_by_trial[client.trial_id].append(client)
    hashes: dict[str, str] = {}
    for trial in trials:
        if trial.raw_path is None:
            if trial.state in {"completed", "completed_with_failures"}:
                raise AnalysisError("A run that reached distribution must retain its raw DistributionRun JSON.")
            continue
        relative = Path(trial.raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise AnalysisError("Manifest raw path escapes the experiment directory.")
        raw = _read_json(root / relative, "raw DistributionRun JSON")
        if not isinstance(raw, dict) or _required_int(raw, "schema_version", "raw DistributionRun JSON", minimum=1) not in SUPPORTED_DISTRIBUTION_SCHEMA:
            raise AnalysisError("Unsupported raw DistributionRun JSON schema.")
        if _required_string(raw, "strategy", "raw DistributionRun JSON") != trial.strategy:
            raise AnalysisError("Raw DistributionRun strategy does not match trials.csv.")
        file_info = _required_object(raw, "file", "raw DistributionRun JSON")
        if _required_uuid(file_info, "file_id", "raw DistributionRun JSON file") != trial.file_id:
            raise AnalysisError("Raw DistributionRun file_id does not match trials.csv.")
        if _required_int(file_info, "size_bytes", "raw DistributionRun JSON file", minimum=0) != trial.size_bytes:
            raise AnalysisError("Raw DistributionRun file size does not match trials.csv.")
        digest = _required_string(file_info, "content_sha256", "raw DistributionRun JSON file")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
            raise AnalysisError("Raw DistributionRun SHA-256 is invalid.")
        prior = hashes.setdefault(trial.file_id, digest)
        if prior != digest:
            raise AnalysisError("The same file_id has contradictory SHA-256 values in raw records.")
        summary = _required_object(raw, "summary", "raw DistributionRun JSON")
        for field, value in (("succeeded", trial.succeeded), ("failed", trial.failed), ("cancelled", trial.cancelled)):
            if _required_int(summary, field, "raw DistributionRun JSON summary", minimum=0) != value:
                raise AnalysisError("Raw DistributionRun counts do not match trials.csv.")
        if len(_required_list(raw, "clients", "raw DistributionRun JSON")) != len(clients_by_trial[trial.trial_id]):
            raise AnalysisError("Raw DistributionRun client count does not match clients.csv.")
    return hashes


def _validate_configuration(configuration: dict[str, Any]) -> None:
    file_ids = _required_list(configuration, "file_ids", "configuration")
    strategies = _required_list(configuration, "strategies", "configuration")
    if not file_ids or not strategies or any(not isinstance(value, str) for value in file_ids + strategies):
        raise AnalysisError("configuration file_ids and strategies must be non-empty string arrays.")
    if len(set(file_ids)) != len(file_ids) or len(set(strategies)) != len(strategies):
        raise AnalysisError("configuration file_ids and strategies must not contain duplicates.")
    for value in file_ids:
        _uuid_or_error(value, "configuration file_id")
    if any(value not in STRATEGIES for value in strategies):
        raise AnalysisError("configuration contains an unsupported strategy.")
    _required_int(configuration, "clients", "configuration", minimum=1)
    _required_int(configuration, "rounds", "configuration", minimum=1)
    _finite_number(configuration.get("timeout_seconds"), "configuration.timeout_seconds", positive=True)
    _required_int(configuration, "retries", "configuration", minimum=0)


def _validate_trial(trial: Trial, configuration: dict[str, Any]) -> None:
    if trial.round_number > configuration["rounds"] or trial.clients != configuration["clients"]:
        raise AnalysisError("trials.csv round or client count contradicts configuration.")
    if trial.succeeded + trial.failed + trial.cancelled != trial.clients:
        raise AnalysisError("trials.csv client outcome counts do not add up.")
    if trial.strategy == "controlled":
        if trial.max_concurrent is None or trial.max_concurrent > trial.clients:
            raise AnalysisError("Controlled trial has an invalid max_concurrent value.")
    elif trial.max_concurrent is not None:
        raise AnalysisError("Non-controlled trial must not set max_concurrent.")
    if trial.state not in {"completed", "completed_with_failures", "failed", "cancelled"}:
        raise AnalysisError("trials.csv contains an unsupported trial state.")
    if trial.state == "completed" and (trial.failed or trial.cancelled or trial.succeeded != trial.clients):
        raise AnalysisError("Completed trial outcome counts are contradictory.")
    if trial.state == "cancelled" and trial.cancelled != trial.clients:
        raise AnalysisError("Cancelled trial outcome counts are contradictory.")
    if trial.size_bytes is not None:
        if trial.payload_bytes != trial.succeeded * trial.size_bytes:
            raise AnalysisError("application_payload_bytes contradicts successful client count and file size.")
        expected = trial.payload_bytes / trial.duration_seconds if trial.duration_seconds > 0 else 0.0
        if not math.isclose(trial.throughput_Bps, expected, rel_tol=1e-8, abs_tol=1e-9):
            raise AnalysisError("application_payload_throughput_Bps contradicts bytes and duration.")


def _comparison_key(experiment: ExperimentData, trial: Trial) -> tuple[Any, ...]:
    config = experiment.configuration
    digest = dict(experiment.file_hashes).get(trial.file_id)
    return (
        trial.file_id, digest, trial.size_bytes, trial.clients, experiment.environment_type,
        config["timeout_seconds"], config["retries"], config.get("server", ""),
        config.get("max_concurrent"),
    )


def _comparison_key_payload(key: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "file_id": key[0], "content_sha256": key[1], "size_bytes": key[2], "clients": key[3], "environment_type": key[4],
        "timeout_seconds": key[5], "retries": key[6], "controlled_max_concurrent": key[8],
    }


def _descriptive(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"samples": 0, "mean": None, "median": None, "min": None, "max": None}
    if any(not math.isfinite(value) for value in values):
        raise AnalysisError("Non-finite numeric data cannot be analysed.")
    return {
        "samples": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
        "min": min(values), "max": max(values),
    }


def _write_charts(destination: Path, report: dict[str, Any], metrics: Sequence[str]) -> list[str]:
    paths: list[str] = []
    for condition in report["conditions"]:
        condition_id = condition["condition_id"]
        strategies = condition["strategies"]
        if "duration" in metrics:
            paths.append(_chart_bar(destination, condition_id, "batch-duration", "Batch completion time", "seconds", strategies, "batch_duration_seconds_completed_trials", "mean"))
        if "throughput" in metrics:
            paths.append(_chart_bar(destination, condition_id, "payload-throughput", "Application payload throughput", "MiB/s", strategies, "application_payload_throughput_MiBps_completed_trials", "mean"))
        if "success" in metrics:
            paths.append(_chart_bar(destination, condition_id, "success-rate", "Complete trial success rate", "percent", strategies, "trial_counts", "complete_success_rate", multiplier=100.0))
        if "client-timing" in metrics:
            paths.append(_chart_client_timing(destination, condition_id, strategies))
    if "controlled" in metrics:
        paths.extend(_controlled_charts(destination, report))
    return paths


def _chart_bar(destination: Path, condition: str, stem: str, title: str, unit: str, strategies: dict[str, Any], outer: str, inner: str, *, multiplier: float = 1.0) -> str:
    data = []
    for name, summary in strategies.items():
        value = summary[outer][inner] if outer != "trial_counts" else summary[outer][inner]
        samples = summary[outer].get("samples", summary["trial_counts"]["planned"])
        data.append((name, None if value is None else float(value) * multiplier, int(samples)))
    relative = f"charts/{condition}-{stem}.svg"
    _svg_bars(destination / relative, title, unit, data)
    return relative


def _chart_client_timing(destination: Path, condition: str, strategies: dict[str, Any]) -> str:
    data = []
    for name, summary in strategies.items():
        wait = summary["client_wait_seconds_all_clients"]
        execute = summary["client_execution_seconds_successful_clients"]
        data.append((f"{name} wait", wait["mean"], wait["samples"]))
        data.append((f"{name} execute", execute["mean"], execute["samples"]))
    relative = f"charts/{condition}-client-timing.svg"
    _svg_bars(destination / relative, "Client wait and execution time", "seconds", data)
    return relative


def _controlled_charts(destination: Path, report: dict[str, Any]) -> list[str]:
    groups: dict[tuple[Any, ...], list[tuple[int, float, int]]] = defaultdict(list)
    for condition in report["conditions"]:
        key = condition["comparison_key"]
        for strategy, summary in condition["strategies"].items():
            if strategy != "controlled":
                continue
            caps = summary["configured_max_concurrent_values"]
            value = summary["batch_duration_seconds_completed_trials"]["mean"]
            if len(caps) == 1 and value is not None:
                groups[(key["file_id"], key["content_sha256"], key["size_bytes"], key["clients"], key["environment_type"], key["timeout_seconds"], key["retries"])].append((caps[0], float(value), int(summary["batch_duration_seconds_completed_trials"]["samples"])))
    paths: list[str] = []
    for number, values in enumerate(groups.values(), start=1):
        unique = {cap for cap, _, _ in values}
        if len(unique) < 2:
            continue
        relative = f"charts/controlled-concurrency-{number:03d}.svg"
        _svg_bars(destination / relative, "Controlled concurrency and batch duration", "seconds", [(f"max {cap}", value, samples) for cap, value, samples in sorted(values)])
        paths.append(relative)
    return paths


def _svg_bars(path: Path, title: str, unit: str, rows: Sequence[tuple[str, float | None, int]]) -> None:
    width, height, left, bottom = 860, 430, 90, 90
    maximum = max((value for _, value, _ in rows if value is not None), default=0.0)
    maximum = maximum if maximum > 0 else 1.0
    plot_height = height - 150
    bar_space = (width - left - 45) / max(1, len(rows))
    pieces = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{left}" y="35" font-family="Arial" font-size="20" font-weight="bold">{html.escape(title)}</text>',
        f'<text x="{left}" y="58" font-family="Arial" font-size="12">Unit: {html.escape(unit)}; labels show sample count n.</text>',
        f'<line x1="{left}" y1="80" x2="{left}" y2="{height-bottom}" stroke="#333"/>',
        f'<line x1="{left}" y1="{height-bottom}" x2="{width-35}" y2="{height-bottom}" stroke="#333"/>',
    ]
    for index, (label, value, samples) in enumerate(rows):
        x = left + index * bar_space + bar_space * 0.18
        bar_width = bar_space * 0.64
        if value is None:
            pieces.append(f'<text x="{x + bar_width / 2:.1f}" y="{height-bottom-10}" text-anchor="middle" font-family="Arial" font-size="12">unavailable (n={samples})</text>')
        else:
            bar_height = max(1, (value / maximum) * plot_height)
            y = height - bottom - bar_height
            pieces.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="#2563eb"/>')
            pieces.append(f'<text x="{x + bar_width / 2:.1f}" y="{y - 8:.1f}" text-anchor="middle" font-family="Arial" font-size="12">{value:.4g} (n={samples})</text>')
        pieces.append(f'<text x="{x + bar_width / 2:.1f}" y="{height-bottom+22}" text-anchor="middle" font-family="Arial" font-size="12">{html.escape(label)}</text>')
    pieces.append(f'<text x="{left-8}" y="90" text-anchor="end" font-family="Arial" font-size="11">{maximum:.4g}</text></svg>')
    _atomic_text(path, "".join(pieces))


def _markdown_report(report: dict[str, Any]) -> str:
    lines = ["# LAN Distribution Lab — T011 分析报告", "", "## 证据边界", ""]
    lines.append("本报告仅汇总输入 T010 导出的应用层记录。它不把应用层完成时间解释为 TCP/NIC 带宽，也不把控制器逻辑客户端描述为真实无线设备。")
    lines.extend(["", "## 输入与可追溯性", ""])
    for item in report["input_experiments"]:
        lines.append(f"- experiment_id：`{item['experiment_id']}`；环境：`{item['environment_type']}`；状态：`{item['experiment_state']}`；Git：`{item['git_commit']}`")
        lines.append(f"  - trial_id：{', '.join(f'`{value}`' for value in item['trial_ids'])}")
    lines.extend(["", "## 描述性统计", ""])
    for condition in report["conditions"]:
        key = condition["comparison_key"]
        lines.append(f"### {condition['condition_id']}")
        lines.append(f"文件：`{key['file_id']}`，大小：{key['size_bytes']} B，逻辑客户端：{key['clients']}，环境：`{key['environment_type']}`。")
        lines.append("")
        lines.append("| 策略 | 完整成功/计划 | 批次时间均值（s，仅完整） | 有效吞吐均值（MiB/s，仅完整） | 客户端成功率 | 公平性均值 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for strategy, summary in condition["strategies"].items():
            counts = summary["trial_counts"]
            duration = summary["batch_duration_seconds_completed_trials"]["mean"]
            throughput = summary["application_payload_throughput_MiBps_completed_trials"]["mean"]
            fairness = summary["jain_fairness_completed_trials"]["mean"]
            lines.append(f"| {strategy} | {counts['completed']}/{counts['planned']} | {_number(duration)} | {_number(throughput)} | {summary['client_counts']['success_rate']:.1%} | {_number(fairness)} |")
        lines.append("")
        lines.append(f"说明：{condition['note']}")
        lines.append("")
    lines.extend(["## 抓包证据", "", _packet_markdown(report["packet_evidence"]), "", "## 可直接观测的结果", ""])
    lines.append("上述 CSV/JSON 提供状态、应用层字节、批次时间、排队/执行时间和完整性结果；图表为这些字段的描述性汇总。")
    lines.extend(["", "## 分析解释与待验证假设", "", "- 不对策略优劣、TCP 丢包、重传原因或无线瓶颈作因果断言；这些需要匹配同一实验窗口的抓包和真实设备对照。", "- Jain 指数只比较同一完整 trial 内成功客户端的应用层完成速率，不能代替网络层公平性。", "", "## 限制", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def _packet_markdown(packet: dict[str, Any]) -> str:
    if packet.get("status") == "unavailable":
        return "未提供脱敏抓包摘要；本报告没有 TCP/HTTP 包级证据。"
    return "已提供字段白名单抓包摘要。它只报告导出中实际存在的 HTTP 状态、TCP 重传标记和方法说明，不由 CSV 推算丢包率。"


def _load_packet_summary(path: Path) -> dict[str, Any]:
    payload = _read_json(path, "packet summary")
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise AnalysisError("packet summary must be a schema_version 1 JSON object.")
    observations = _required_list(payload, "observations", "packet summary")
    cleaned = []
    for observation in observations:
        if not isinstance(observation, dict):
            raise AnalysisError("packet summary observations must be objects.")
        allowed = {"window_id", "http_status_counts", "tcp_retransmission_packets", "rtt_method", "rtt_seconds", "note"}
        if set(observation) - allowed:
            raise AnalysisError("packet summary contains a field outside the privacy whitelist.")
        entry = {key: observation[key] for key in allowed if key in observation}
        if "http_status_counts" in entry:
            counts = entry["http_status_counts"]
            if not isinstance(counts, dict) or any(str(code) not in {"200", "206", "416"} or not isinstance(count, int) or count < 0 for code, count in counts.items()):
                raise AnalysisError("packet summary HTTP status counts are invalid.")
        if "tcp_retransmission_packets" in entry and (not isinstance(entry["tcp_retransmission_packets"], int) or entry["tcp_retransmission_packets"] < 0):
            raise AnalysisError("packet summary retransmission count is invalid.")
        if "rtt_seconds" in entry:
            _finite_number(entry["rtt_seconds"], "packet summary rtt_seconds", positive=False)
        cleaned.append(entry)
    return {"status": "provided", "observations": cleaned, "note": "Optional offline, sanitized packet summary; no PCAP was read or uploaded."}


_TRIAL_COLUMNS = {"experiment_id", "trial_id", "round", "strategy", "file_id", "size_bytes", "clients", "max_concurrent", "duration_seconds", "succeeded", "failed", "cancelled", "observed_max_active", "application_payload_bytes", "application_payload_throughput_Bps", "state"}
_CLIENT_COLUMNS = {"experiment_id", "trial_id", "strategy", "client_id", "status", "wait_seconds", "duration_seconds", "downloaded_bytes", "integrity_verified"}


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError(f"Could not read valid {label}.") from error


def _read_csv(path: Path, label: str) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise AnalysisError(f"Could not read valid {label}.") from error


def _require_columns(rows: list[dict[str, str]], columns: set[str], label: str) -> None:
    if not rows:
        raise AnalysisError(f"{label} must not be empty.")
    if not columns.issubset(rows[0]):
        raise AnalysisError(f"{label} is missing required columns.")


def _required_object(payload: dict[str, Any], key: str, label: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise AnalysisError(f"{label}.{key} must be an object.")
    return value


def _required_list(payload: dict[str, Any], key: str, label: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise AnalysisError(f"{label}.{key} must be an array.")
    return value


def _required_string(payload: dict[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AnalysisError(f"{label}.{key} must be a non-empty string.")
    return value


def _required_uuid(payload: dict[str, Any], key: str, label: str) -> str:
    return _uuid_or_error(_required_string(payload, key, label), f"{label}.{key}")


def _uuid_or_error(value: str, label: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise AnalysisError(f"{label} must be a UUID.") from error


def _required_int(payload: dict[str, Any], key: str, label: str, *, minimum: int) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AnalysisError(f"{label}.{key} must be an integer >= {minimum}.")
    return value


def _csv_uuid(row: dict[str, str], key: str, label: str) -> str:
    return _uuid_or_error(row.get(key, ""), f"{label}.{key}")


def _csv_int(row: dict[str, str], key: str, label: str, *, minimum: int) -> int:
    value = row.get(key, "")
    try:
        parsed = int(value)
    except ValueError as error:
        raise AnalysisError(f"{label}.{key} must be an integer.") from error
    if parsed < minimum:
        raise AnalysisError(f"{label}.{key} must be >= {minimum}.")
    return parsed


def _csv_optional_int(row: dict[str, str], key: str, label: str, *, minimum: int) -> int | None:
    if row.get(key, "") == "":
        return None
    return _csv_int(row, key, label, minimum=minimum)


def _csv_float(row: dict[str, str], key: str, label: str, *, minimum: float) -> float:
    try:
        return _finite_number(float(row.get(key, "")), f"{label}.{key}", positive=False, minimum=minimum)
    except ValueError as error:
        raise AnalysisError(f"{label}.{key} must be a finite number.") from error


def _csv_bool(row: dict[str, str], key: str, label: str) -> bool:
    value = row.get(key, "")
    if value not in {"True", "False"}:
        raise AnalysisError(f"{label}.{key} must be True or False.")
    return value == "True"


def _finite_number(value: Any, label: str, *, positive: bool, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < minimum or (positive and float(value) <= 0):
        raise AnalysisError(f"{label} must be a finite {'positive ' if positive else ''}number.")
    return float(value)


def _validate_metrics(values: Sequence[str]) -> tuple[str, ...]:
    selected = tuple(values)
    if not selected or any(value not in CHART_METRICS for value in selected) or len(set(selected)) != len(selected):
        raise AnalysisError("metrics must be a non-empty unique subset of " + ", ".join(CHART_METRICS) + ".")
    return selected


def _prepare_new_directory(value: Path) -> Path:
    destination = Path(value).expanduser().resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise AnalysisError("Analysis output directory must not already exist.")
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        raise AnalysisError("Could not create analysis output directory.") from error
    return destination


def _remove_created_tree(root: Path) -> None:
    try:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        root.rmdir()
    except OSError:
        # Preserve failed diagnostic output if cleanup itself cannot complete.
        pass


def _atomic_text(path: Path, value: str) -> None:
    _atomic_bytes(path, value.encode("utf-8"))


def _atomic_json(path: Path, payload: Any) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise AnalysisError("Could not write analysis output.") from error


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) and value else "unknown"


def _number(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.4g}"
