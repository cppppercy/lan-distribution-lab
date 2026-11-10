from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from lan_distribution_lab.__main__ import main
from lan_distribution_lab.analysis import AnalysisError, analyze_experiments, jain_fairness, load_experiment
from lan_distribution_lab.experiment import ExperimentConfig, ExperimentRunner, prepare_experiment_directory
from test_distribution import FILE_ID, FakeOrigin


def _config(server: str, **changes) -> ExperimentConfig:
    values = {
        "server": server,
        "file_ids": (FILE_ID,),
        "strategies": ("sequential", "parallel", "controlled"),
        "clients": 2,
        "max_concurrent": 2,
        "rounds": 1,
        "timeout_seconds": 5.0,
        "retries": 0,
        "environment_type": "local_loopback",
        "on_error": "continue",
    }
    values.update(changes)
    return ExperimentConfig(**values)


def _experiment(tmp_path: Path, name: str, content: bytes = b"analysis" * 200, **changes) -> Path:
    valid_tokens = changes.pop("valid_tokens", None)
    origin = FakeOrigin(content, valid_tokens=valid_tokens)
    try:
        root = prepare_experiment_directory(tmp_path / name)
        return ExperimentRunner(_config(origin.origin, **changes), [f"{name}-one", f"{name}-two"], root).run()
    finally:
        origin.close()


def _rewrite_csv(path: Path, transform) -> None:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0])
    transform(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_analyze_valid_t010_exports_generates_svg_report_and_preserves_input(tmp_path: Path) -> None:
    root = _experiment(tmp_path, "standard")
    before = {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}

    output = analyze_experiments([root], tmp_path / "analysis")

    after = {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}
    payload = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
    report = (output / "report.md").read_text(encoding="utf-8")
    assert before == after
    assert payload["input_experiments"][0]["experiment_id"]
    assert len(payload["conditions"]) == 1
    strategies = payload["conditions"][0]["strategies"]
    assert set(strategies) == {"sequential", "parallel", "controlled"}
    assert all(value["trial_counts"]["completed"] == 1 for value in strategies.values())
    assert all(value["application_payload_throughput_MiBps_completed_trials"]["mean"] is not None for value in strategies.values())
    assert "TCP/NIC" in report and "未提供脱敏抓包摘要" in report
    charts = list((output / "charts").glob("*.svg"))
    assert len(charts) >= 4
    assert "Batch completion time" in charts[0].read_text(encoding="utf-8")


def test_statistics_success_failure_wait_and_fairness_semantics(tmp_path: Path) -> None:
    root = _experiment(tmp_path, "partial", valid_tokens={"partial-one"})
    data = load_experiment(root)
    output = analyze_experiments([root], tmp_path / "analysis")
    summary = json.loads((output / "analysis.json").read_text(encoding="utf-8"))["conditions"][0]["strategies"]
    assert all(item["trial_counts"]["completed"] == 0 for item in summary.values())
    assert all(item["trial_counts"]["partial"] == 1 for item in summary.values())
    assert all(item["trial_counts"]["complete_success_rate"] == 0 for item in summary.values())
    assert all(item["application_payload_throughput_Bps_completed_trials"]["samples"] == 0 for item in summary.values())
    assert all(item["client_counts"]["success_rate"] == 0.5 for item in summary.values())
    assert all(item["jain_fairness_completed_trials"]["mean"] is None for item in summary.values())
    assert len(data.trials) == 3
    assert jain_fairness([10.0, 10.0]) == 1.0
    assert jain_fairness([10.0]) is None
    assert jain_fairness([10.0, 0.0]) is None


def test_different_file_size_and_environment_are_not_merged(tmp_path: Path) -> None:
    first = _experiment(tmp_path, "first", b"a" * 512)
    second = _experiment(tmp_path, "second", b"b" * 1024, environment_type="controller_to_lan")

    output = analyze_experiments([first, second], tmp_path / "analysis")

    conditions = json.loads((output / "analysis.json").read_text(encoding="utf-8"))["conditions"]
    assert len(conditions) == 2
    assert {item["comparison_key"]["size_bytes"] for item in conditions} == {512, 1024}
    assert {item["comparison_key"]["environment_type"] for item in conditions} == {"local_loopback", "controller_to_lan"}


@pytest.mark.parametrize("kind", ["duplicate", "nan", "zero", "missing-raw", "bad-json"])
def test_invalid_or_incomplete_records_fail_without_trusted_output(tmp_path: Path, kind: str) -> None:
    root = _experiment(tmp_path, f"bad-{kind}")
    if kind == "duplicate":
        def duplicate(rows):
            rows[1]["trial_id"] = rows[0]["trial_id"]
        _rewrite_csv(root / "trials.csv", duplicate)
    elif kind == "nan":
        def non_finite(rows):
            rows[0]["duration_seconds"] = "NaN"
        _rewrite_csv(root / "trials.csv", non_finite)
    elif kind == "zero":
        def zero_duration(rows):
            rows[0]["duration_seconds"] = "0"
            rows[0]["application_payload_throughput_Bps"] = "0"
        _rewrite_csv(root / "trials.csv", zero_duration)
    elif kind == "missing-raw":
        (root / "trials" / "trial-001" / "distribution-result.json").unlink()
    else:
        (root / "experiments.json").write_text("{not-json", encoding="utf-8")

    destination = tmp_path / f"analysis-{kind}"
    with pytest.raises(AnalysisError):
        analyze_experiments([root], destination)
    assert not destination.exists()


def test_raw_record_and_client_count_contradictions_are_rejected(tmp_path: Path) -> None:
    root = _experiment(tmp_path, "raw-mismatch")
    raw_path = root / "trials" / "trial-001" / "distribution-result.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["summary"]["succeeded"] = 99
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(AnalysisError, match="counts"):
        load_experiment(root)


def test_cli_packet_summary_whitelist_and_no_secret_or_path_leak(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _experiment(tmp_path, "safe", b"x" * 1024)
    packet = tmp_path / "packet-summary.json"
    packet.write_text(json.dumps({
        "schema_version": 1,
        "observations": [{
            "window_id": "trial-window-1", "http_status_counts": {"200": 2, "206": 1, "416": 0},
            "tcp_retransmission_packets": 0, "rtt_method": "Wireshark TCP analysis field", "rtt_seconds": 0.012,
        }],
    }), encoding="utf-8")
    destination = tmp_path / "analysis"

    main(["analyze", "--input", str(root), "--output-dir", str(destination), "--packet-summary", str(packet)])

    captured = capsys.readouterr()
    report = (destination / "report.md").read_text(encoding="utf-8")
    assert str(destination) in captured.out
    assert "safe-one" not in report and "safe-two" not in report
    assert str(root) not in report
    assert json.loads((destination / "analysis.json").read_text(encoding="utf-8"))["packet_evidence"]["status"] == "provided"
    packet.write_text(json.dumps({"schema_version": 1, "observations": [{"authorization": "secret"}]}), encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        main(["analyze", "--input", str(root), "--output-dir", str(tmp_path / "reject"), "--packet-summary", str(packet)])
    assert exit_info.value.code == 1
