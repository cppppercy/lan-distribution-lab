from __future__ import annotations

import csv
import io
import json
import sys
import uuid
from pathlib import Path

import pytest

import lan_distribution_lab.experiment as experiment_module
from lan_distribution_lab.__main__ import main
from lan_distribution_lab.experiment import (
    ExperimentConfig,
    ExperimentError,
    ExperimentRunner,
    parse_strategies,
    prepare_experiment_directory,
)
from test_distribution import FILE_ID, FakeOrigin


def _config(**changes) -> ExperimentConfig:
    values = {
        "server": "http://127.0.0.1:1",
        "file_ids": (FILE_ID,),
        "strategies": ("sequential",),
        "clients": 2,
        "max_concurrent": None,
        "rounds": 1,
        "timeout_seconds": 5.0,
        "retries": 0,
        "environment_type": "local_loopback",
        "on_error": "continue",
    }
    values.update(changes)
    return ExperimentConfig(**values)


def _runner(config: ExperimentConfig, tokens: list[str], destination: Path) -> ExperimentRunner:
    return ExperimentRunner(config, tokens, prepare_experiment_directory(destination))


def test_one_trial_exports_raw_json_csv_and_integrity(tmp_path: Path) -> None:
    origin = FakeOrigin(b"experiment-one" * 1_000)
    try:
        config = _config(server=origin.origin)
        output = _runner(config, ["experiment-a", "experiment-b"], tmp_path / "experiment")
        root = output.run()
    finally:
        origin.close()

    manifest = json.loads((root / "experiments.json").read_text(encoding="utf-8"))
    trials = list(csv.DictReader((root / "trials.csv").open(encoding="utf-8")))
    clients = list(csv.DictReader((root / "clients.csv").open(encoding="utf-8")))
    raw = json.loads((root / "trials" / "trial-001" / "distribution-result.json").read_text())
    assert manifest["state"] == "completed"
    assert manifest["configuration"]["environment_type"] == "local_loopback"
    assert len(manifest["software"]["git_commit"]) >= 7
    assert len(trials) == 1 and trials[0]["succeeded"] == "2"
    assert trials[0]["application_payload_throughput_Bps"]
    assert len(clients) == 2 and all(row["integrity_verified"] == "True" for row in clients)
    assert raw["file"]["content_sha256"]
    assert manifest["statistics"]["sequential"]["successful_trial_duration_seconds"]["mean"] is not None
    assert manifest["statistics"]["sequential"]["all_client_wait_seconds"]["mean"] is not None
    all_exports = "\n".join(
        path.read_text(encoding="utf-8") for path in root.rglob("*.json")
    ) + (root / "trials.csv").read_text(encoding="utf-8") + (root / "clients.csv").read_text(encoding="utf-8")
    assert "experiment-a" not in all_exports
    assert "experiment-b" not in all_exports


def test_three_strategies_three_rounds_are_deterministic_and_fresh(tmp_path: Path) -> None:
    origin = FakeOrigin(b"experiment-rounds" * 300)
    try:
        config = _config(
            server=origin.origin,
            strategies=("sequential", "parallel", "controlled"),
            max_concurrent=1,
            rounds=3,
            clients=1,
        )
        root = _runner(config, ["round-token"], tmp_path / "rounds").run()
    finally:
        origin.close()

    rows = list(csv.DictReader((root / "trials.csv").open(encoding="utf-8")))
    assert len(rows) == 9
    assert [row["strategy"] for row in rows[:3]] == ["sequential", "parallel", "controlled"]
    assert len({row["trial_id"] for row in rows}) == 9
    assert origin.state.content_calls == 9, "fresh per-trial output must not reuse completed files"


def test_multiple_file_ids_keep_unique_trial_identity(tmp_path: Path) -> None:
    first = FILE_ID
    second = str(uuid.uuid4())
    calls: list[str] = []

    def failing_runner(_server, file_id, *_args, **_kwargs):
        calls.append(file_id)
        raise ExperimentError("controlled test failure")

    config = _config(file_ids=(first, second), rounds=2)
    root = ExperimentRunner(config, ["a", "b"], prepare_experiment_directory(tmp_path / "multi"), distribution_runner=failing_runner).run()
    rows = list(csv.DictReader((root / "trials.csv").open(encoding="utf-8")))
    assert calls == [first, second, first, second]
    assert [row["file_id"] for row in rows] == calls
    assert len({row["trial_id"] for row in rows}) == 4
    assert all(row["state"] == "failed" for row in rows)


def test_client_failure_and_failure_trial_are_both_retained(tmp_path: Path) -> None:
    origin = FakeOrigin(b"failure-record" * 500, valid_tokens={"good"})
    try:
        config = _config(server=origin.origin)
        root = _runner(config, ["good", "bad"], tmp_path / "failure").run()
    finally:
        origin.close()
    trials = list(csv.DictReader((root / "trials.csv").open(encoding="utf-8")))
    clients = list(csv.DictReader((root / "clients.csv").open(encoding="utf-8")))
    assert trials[0]["state"] == "completed_with_failures"
    assert {row["status"] for row in clients} == {"succeeded", "failed"}
    assert any(row["error_type"] == "authentication" for row in clients)


def test_stop_policy_marks_remaining_trials_cancelled(tmp_path: Path) -> None:
    def fail_once(*_args, **_kwargs):
        raise ExperimentError("no server")

    config = _config(rounds=2, on_error="stop")
    root = ExperimentRunner(config, ["a", "b"], prepare_experiment_directory(tmp_path / "stop"), distribution_runner=fail_once).run()
    rows = list(csv.DictReader((root / "trials.csv").open(encoding="utf-8")))
    assert [row["state"] for row in rows] == ["failed", "cancelled"]
    assert (root / "experiments.json").read_text(encoding="utf-8").find("completed_with_cancelled_trials") >= 0


def test_interrupt_marks_active_trial_cancelled_without_false_success(tmp_path: Path) -> None:
    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    output_dir = prepare_experiment_directory(tmp_path / "interrupted")
    runner = ExperimentRunner(
        _config(rounds=2), ["a", "b"], output_dir, distribution_runner=interrupted
    )
    with pytest.raises(KeyboardInterrupt):
        runner.run()

    manifest = json.loads((output_dir / "experiments.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader((output_dir / "trials.csv").open(encoding="utf-8")))
    assert manifest["state"] == "interrupted"
    assert rows[0]["state"] == "cancelled"
    assert rows[0]["succeeded"] == "0"
    assert len(rows) == 1


def test_export_write_failure_never_publishes_completed_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = experiment_module._atomic_csv
    calls = 0

    def fail_after_initial_exports(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise ExperimentError("simulated export write failure")
        return original(*args, **kwargs)

    output_dir = prepare_experiment_directory(tmp_path / "write-failure")
    runner = ExperimentRunner(
        _config(), ["a", "b"], output_dir,
        distribution_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(ExperimentError("trial fails")),
    )
    monkeypatch.setattr(experiment_module, "_atomic_csv", fail_after_initial_exports)
    with pytest.raises(ExperimentError, match="simulated export write failure"):
        runner.run()
    manifest = json.loads((output_dir / "experiments.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "running"


def test_rejects_existing_output_invalid_limits_and_tokens_without_secrets(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "user.txt").write_text("do not overwrite")
    with pytest.raises(ExperimentError):
        prepare_experiment_directory(existing)
    with pytest.raises(ExperimentError):
        _runner(_config(rounds=0), ["a", "b"], tmp_path / "bad-rounds")
    with pytest.raises(ExperimentError):
        _runner(_config(strategies=("controlled",), max_concurrent=3), ["a", "b"], tmp_path / "bad-limit")
    with pytest.raises(ExperimentError):
        _runner(_config(server="http://secret@example.invalid"), ["a", "b"], tmp_path / "bad-server")
    with pytest.raises(ExperimentError):
        _runner(_config(file_ids=("not-a-uuid",)), ["a", "b"], tmp_path / "bad-file")
    with pytest.raises(ExperimentError) as error:
        _runner(_config(), ["duplicate-secret", "duplicate-secret"], tmp_path / "bad-token")
    assert "duplicate-secret" not in str(error.value)
    assert parse_strategies("parallel, controlled") == ("parallel", "controlled")


def test_cli_validates_before_creating_output_or_reading_tokens(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    destination = tmp_path / "must-not-exist"
    with pytest.raises(SystemExit) as exit_info:
        main([
            "experiment", "--server", "http://127.0.0.1:8000", "--file-id", FILE_ID,
            "--strategies", "sequential", "--rounds", "0", "--clients", "1",
            "--output-dir", str(destination), "--tokens-stdin",
        ])
    assert exit_info.value.code == 1
    assert not destination.exists()
    assert "Experiment failed" in capsys.readouterr().err


def test_cli_runs_a_fresh_experiment_from_protected_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    origin = FakeOrigin(b"cli-experiment" * 100, valid_tokens={"cli-one", "cli-two"})
    destination = tmp_path / "cli-experiment"
    try:
        monkeypatch.setattr(sys, "stdin", io.StringIO("cli-one\ncli-two\n"))
        main([
            "experiment", "--server", origin.origin, "--file-id", FILE_ID,
            "--strategies", "sequential", "--clients", "2", "--rounds", "1",
            "--output-dir", str(destination), "--tokens-stdin",
        ])
    finally:
        origin.close()
    output = capsys.readouterr().out
    assert "finished" in output
    assert "cli-one" not in output and "cli-two" not in output
    assert (destination / "trials" / "trial-001" / "distribution-result.json").exists()
