"""Replay admission and independent three-way timing calculations."""

import csv
import json

import numpy as np
import pytest

from experiments.case118_spacetime_pq_replay import analyze, run
from experiments.case118_spacetime_pq_replay.telemetry import TemperatureCollector
from experiments.case118_vectorization_replay import analyze as previous_analysis
from experiments.case118_vectorization_replay.run import validate_versions


def test_dependency_exception_is_exact_and_preserves_all_other_checks():
    old = dict(python="3.11.15", cvxpy="1.9.2", numpy="2.4.6")
    new = dict(old, cvxpy="1.9.3")
    validate_versions(new, old, run.VERSION_CHANGES)
    validate_versions(old, old)
    for bad in (dict(new, numpy="different"), dict(new, cvxpy="1.9.4"),
                dict(new, extra="unexpected"), old):
        with pytest.raises(ValueError):
            validate_versions(bad, old, run.VERSION_CHANGES)
    with pytest.raises(ValueError):
        validate_versions(new, old)


def test_destination_cannot_reuse_or_overlap_retained_evidence(tmp_path):
    old = tmp_path / "previous"
    old.mkdir()
    marker = old / "sample.json"
    marker.write_text("retained")
    for output in (old, old / "nested", tmp_path):
        with pytest.raises(ValueError):
            run.validate_destination(output, old)
    new = tmp_path / "new"
    run.validate_destination(new, old)
    assert not new.exists()
    new.mkdir()
    with pytest.raises(FileExistsError):
        run.validate_destination(new, old)
    assert marker.read_text() == "retained"


@pytest.mark.parametrize("head,dirty,message", [
    ("wrong", "", "Commit binding mismatch"),
    ("expected", " M experiments/runner.py", "Commit the reviewed runner"),
    ("expected", "?? experiments/new_runner/", "Commit the reviewed runner"),
])
def test_launch_rejects_wrong_commit_or_uncommitted_sources(
    tmp_path, monkeypatch, head, dirty, message,
):
    monkeypatch.setattr(run, "OUTPUT", tmp_path / "new")
    monkeypatch.setattr(run, "PREVIOUS", tmp_path / "previous")
    monkeypatch.setattr(run, "git", lambda *args: head if args[0] == "rev-parse" else dirty)
    with pytest.raises(ValueError, match=message):
        run.launch("expected", fan_on=True)
    assert not run.OUTPUT.exists()


def test_launch_requires_fan_confirmation():
    with pytest.raises(ValueError, match="fan is on"):
        run.launch("unused", fan_on=False)


def comparison_fixture():
    previous, current = [], []
    for iteration, weight, group, seconds in ((1, 9, "primary", 10),
                                             (2, 1, "primary", 100),
                                             (3, 100, "helper", 1000)):
        common = dict(iteration=iteration, population_weight=weight, historical_group=group,
                      stratum=str(iteration), historical_solve_seconds=seconds * 4,
                      historical_window_seconds=seconds * 8, historical_build_seconds=2,
                      historical_objective=100)
        previous.append(dict(common, new_solve_seconds=seconds * 2,
                             new_window_seconds=seconds * 4, new_build_seconds=1,
                             new_objective=100, new_winner_order=0))
        current.append(dict(common, new_solve_seconds=seconds,
                            new_window_seconds=seconds * 2, new_build_seconds=0.5,
                            new_objective=100.2 if iteration == 2 else 100,
                            new_winner_order=1 if iteration == 2 else 0,
                            accepted=True, new_status="optimal"))
    # Prior rows are read from CSV in real analysis.
    previous = [{k: str(v) for k, v in row.items()} for row in previous]
    return previous, current


def test_three_way_pairing_weights_and_helper_separation():
    old, new = comparison_fixture()
    rows = analyze.compare(list(reversed(old)), new)
    summary = analyze.summarize(rows)
    means = summary["weighted_primary_means"]
    assert means["combined_solve_seconds"] == 19
    assert means["time_only_solve_seconds"] == 38
    assert means["stepwise_solve_seconds"] == 76
    assert summary["weighted_primary_geometric_speedups"][
        "time_only_to_combined_solve_seconds_speedup"] == pytest.approx(2)
    assert summary["weighted_primary_geometric_speedups"][
        "stepwise_to_combined_solve_seconds_speedup"] == pytest.approx(4)
    assert summary["primary_completed"] == 2
    assert summary["helper_completed"] == 1
    assert summary["helper_cohort"][0]["iteration"] == 3
    assert summary["objective_over_0_1_percent_vs_time_only"] == [2]
    assert summary["changed_winners_vs_time_only"] == [2]


@pytest.mark.parametrize("field,value", [
    ("population_weight", 1), ("stratum", "wrong"),
    ("historical_group", "helper"), ("historical_solve_seconds", 999),
])
def test_mismatched_historical_pair_is_rejected(field, value):
    old, new = comparison_fixture()
    old[0][field] = str(value)
    with pytest.raises(ValueError, match="Historical"):
        analyze.compare(old, new)


@pytest.mark.parametrize("value", [0, -1, np.nan, np.inf])
def test_invalid_timing_is_not_silently_excluded(value):
    old, new = comparison_fixture()
    new[0]["new_solve_seconds"] = value
    with pytest.raises(ValueError, match="finite positive"):
        analyze.compare(old, new)


def test_missing_telemetry_is_explicit_and_does_not_break_analysis(tmp_path, monkeypatch):
    from experiments.case118_spacetime_pq_replay import telemetry
    monkeypatch.setattr(telemetry.shutil, "which", lambda name: None)
    folder = tmp_path / "temperature"
    with TemperatureCollector(folder):
        assert json.loads((folder / "metadata.json").read_text())["unavailable_reason"]
    assert json.loads((folder / "finished.json").read_text())["samples"] == 0
    assert previous_analysis.temperature_snapshot(None, output=tmp_path, folder=folder) is None


def test_dispatch_comparison_wraps_angles_and_rejects_shape_changes():
    old = {k: [[0, 1]] for k in ("Pg", "Qg", "b", "b_q", "soc", "p_nd", "q_nd", "Vm", "Va_deg")}
    new = dict(old, Pg=[[2, 3]], Va_deg=[[360, 361]])
    differences = analyze.dispatch_deltas(old, new)
    assert differences["time_only_to_combined_max_abs_delta_Pg"] == 2
    assert differences["time_only_to_combined_max_abs_delta_Va_deg"] == 0
    with pytest.raises(ValueError, match="Invalid dispatch"):
        analyze.dispatch_deltas(old, dict(new, Pg=[2, 3]))


def test_three_way_analysis_writes_only_new_output(tmp_path, monkeypatch):
    from experiments.case118_vectorization_replay.sample import ref
    old, new = comparison_fixture()
    previous = tmp_path / "previous"
    previous.mkdir()
    prior_csv = previous / "comparison.csv"
    with prior_csv.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(old[0]))
        writer.writeheader()
        writer.writerows(old)
    original = prior_csv.read_bytes()
    output = tmp_path / "new"
    output.mkdir()
    sample = output / "sample.json"
    sample.write_text(json.dumps(dict(selected=[dict(iteration=r["iteration"]) for r in new])))
    binding = dict(commit="reviewed", sample=ref(sample),
                   previous_replay={"comparison.csv": ref(prior_csv)})
    (output / "binding.json").write_text(json.dumps(binding))
    diagnostics = dict(complete=False, accepted_count=3, helper_attempts=1,
                       attempt_outcomes={"accepted": 3}, objective_over_0_1_percent=[2])
    (output / "summary.json").write_text(json.dumps(diagnostics))
    monkeypatch.setattr(analyze, "OUTPUT", output)
    monkeypatch.setattr(analyze, "analyze", lambda *args, **kwargs: (new, diagnostics))
    physical = {k: [[1]] for k in ("Pg", "Qg", "b", "b_q", "soc", "p_nd", "q_nd", "Vm", "Va_deg")}
    monkeypatch.setattr(analyze, "winner_result", lambda *args: physical)
    _, summary = analyze.run_analysis()
    assert summary["complete"] is False
    assert "PARTIAL" in (output / "REPORT.md").read_text()
    assert prior_csv.read_bytes() == original
    assert sorted(p.name for p in previous.iterdir()) == ["comparison.csv"]
