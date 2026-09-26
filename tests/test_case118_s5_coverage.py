"""Small scientific/indexing/corruption tests; no solver execution."""

import gzip
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from experiments.case118_annual_hierarchy.analysis.coverage_report import (
    Settings,
    build_features,
    candidate_windows,
    feature_summary,
    finite,
    gap_sensitivity,
    nearest_coverage,
    regime_coverage,
    report_html,
    sha,
    snapshot_completed,
    window_stops,
)


class FixtureLoadReached(Exception):
    """Stop the CLI after destination checks, before reading scientific data."""


@pytest.fixture
def coverage_cli(tmp_path, monkeypatch):
    from experiments.case118_annual_hierarchy.analysis import coverage_report
    from experiments.case118_annual_hierarchy import s4_fixture

    monkeypatch.setattr(coverage_report, "ROOT", tmp_path)
    run = tmp_path / "experiments/case118_annual_hierarchy/results/s4b_annual_ac"
    monkeypatch.setattr(coverage_report, "DEFAULT_RUN", run)
    now = datetime(2026, 9, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(coverage_report, "datetime", SimpleNamespace(now=lambda tz: now))
    reproduction = tmp_path / "experiments/case118_annual_hierarchy/results/reproductions/s5_coverage"
    destination = reproduction / now.strftime("%Y%m%dT%H%M%S%fZ")

    def stop_before_loading():
        raise FixtureLoadReached

    monkeypatch.setattr(s4_fixture, "load_s4_fixture", stop_before_loading)

    def invoke(*args):
        monkeypatch.setattr(sys, "argv", ["coverage_report", *map(str, args)])
        coverage_report.main()

    return invoke, destination, run


def test_default_output_passes_guard_without_loading_archives(coverage_cli):
    invoke, destination, _ = coverage_cli
    with pytest.raises(FixtureLoadReached):
        invoke()
    assert not destination.exists()


def test_default_output_refuses_existing_directory(coverage_cli):
    invoke, destination, _ = coverage_cli
    destination.mkdir(parents=True)
    marker = destination / "report.json"
    marker.write_text("retained evidence")
    with pytest.raises(ValueError, match="fresh report output"):
        invoke()
    assert marker.read_text() == "retained evidence"


@pytest.mark.parametrize("relationship", ["ancestor", "same", "descendant"])
def test_default_output_rejects_run_overlap(coverage_cli, relationship):
    invoke, destination, _ = coverage_cli
    run = {"ancestor": destination.parent, "same": destination,
           "descendant": destination / "archive"}[relationship]
    with pytest.raises(ValueError, match="separate from execution/scientific artifacts"):
        invoke("--run-dir", run)


@pytest.mark.parametrize("relative", [
    "experiments/case118_annual_hierarchy/analysis/new-report",
    "experiments/case118_annual_hierarchy/results/s5_coverage/new-report",
    "experiments/case118_annual_hierarchy/results/reproductions/s5_coverage_other/new-report",
])
def test_other_experiment_outputs_remain_protected(coverage_cli, tmp_path, relative):
    invoke, _, _ = coverage_cli
    with pytest.raises(ValueError, match="separate from execution/scientific artifacts"):
        invoke("--output", tmp_path / relative)


def test_explicit_output_outside_experiments_remains_allowed(coverage_cli, tmp_path):
    invoke, _, _ = coverage_cli
    with pytest.raises(FixtureLoadReached):
        invoke("--output", tmp_path / "fresh-report")


def test_reproduction_symlink_cannot_bypass_archive_guard(coverage_cli):
    invoke, destination, run = coverage_cli
    run.mkdir(parents=True)
    destination.parent.parent.mkdir(parents=True)
    destination.parent.symlink_to(run, target_is_directory=True)
    with pytest.raises(ValueError, match="separate from execution/scientific artifacts"):
        invoke()


def test_gap_sensitivity_is_monotone_and_ignores_completed():
    actual = gap_sensitivity(
        np.array([0.0, 0.6, 1.5, 3.0]), np.array([True, False, False, False])
    )
    assert actual == {"0.5": 3, "1.0": 2, "2.0": 1}


def test_html_tables_are_closed_and_escaped():
    rendered = report_html(
        ["# Title", "| x | y |", "|---|---|", "| 1 | <script> |", "", "## Next"]
    )
    assert rendered.count("<table>") == rendered.count("</table>") == 1
    assert "<script>" not in rendered and "&lt;script&gt;" in rendered
    assert "<h2>Next</h2>" in rendered


def test_shard_and_year_truncation():
    assert window_stops(7, [0, 4, 7], 3).tolist() == [3, 4, 4, 4, 7, 7, 7]
    with pytest.raises(ValueError):
        window_stops(7, [0, 4, 4, 7], 3)


def test_units_signs_and_lower_rated_branch():
    n = 6
    branch = np.zeros((3, 13))
    branch[:, 5] = [100, 10, 1]
    branch[:, 10] = [1, 1, 0]
    soc = np.array(
        [[5, 20], [6, 18], [7, 16], [8, 14], [7, 16], [6, 18], [5, 20]], float
    )
    power = -np.diff(soc, axis=0)
    f, scales, r, stop, rated = build_features(
        np.full((n, 1), 100.0),
        np.full((n, 1), 20.0),
        np.full((n, 1), 30.0),
        np.tile([50, 9.8, 100], (n, 1)),
        branch,
        soc,
        power,
        np.array([10, 40]),
        np.array([2, 8]),
        ["small", "large"],
        ["wind"],
        [0, 3, 6],
        3,
        1.0,
        Settings(),
    )
    np.testing.assert_allclose(f["maximum_branch_utilization"], 0.98)
    assert rated.tolist() == [True, True, False]
    assert f["branches_at_or_above_95pct"].tolist() == [1] * 6
    assert f["small.power_fraction_positive_discharge"][0] == -0.5
    assert f["small.target_soc_fraction"][0] == 0.8
    assert f["small.required_average_power_fraction"][0] == -0.5
    assert f["window_steps"].tolist() == [3, 2, 1, 3, 2, 1]
    assert (
        f["net_load_mw"][0] == 70
        and f["renewable_available_fraction_of_load"][0] == 0.3
    )
    assert r["simultaneous_charging_and_discharging_across_devices"].all()
    assert scales["small.soc_fraction"] == 0.1
    assert stop.tolist() == [3, 3, 3, 6, 6, 6]


def test_joint_gap_can_exist_with_covered_marginals():
    features = {"x": np.array([0.0, 1.0, 0.0]), "y": np.array([0.0, 1.0, 1.0])}
    mask = np.array([True, True, False])
    _, dist, _ = nearest_coverage(features, {"x": 0.1, "y": 0.1}, mask)
    assert dist[2] == 10
    assert all(
        v["remaining_outside_completed_range"] == 0
        for v in feature_summary(features, mask).values()
    )


def test_regime_denominators_and_no_double_count_claim():
    completed = np.r_[np.ones(50, bool), np.zeros(50, bool)]
    regimes = {
        "unseen": np.arange(100) >= 60,
        "balanced": np.arange(100) % 2 == 0,
        "rare": np.arange(100) == 99,
    }
    result = {r["name"]: r for r in regime_coverage(regimes, completed, Settings())}
    assert (
        result["unseen"]["flag"] == "unseen"
        and result["unseen"]["remaining_hours"] == 40
    )
    assert result["balanced"]["relative_to_overall_coverage"] == 1
    assert result["rare"]["flag"] == "not_flagged"


def test_candidates_are_unexecuted_nonwrapping_diverse_and_deterministic():
    features = {"x": np.array([0, 0, 1, 1, 1, 1, 3, 3, 3, 3], float)}
    mask = np.array([True, True] + [False] * 8)
    matrix, distance, nearest = nearest_coverage(features, {"x": 0.1}, mask)
    a = candidate_windows(matrix, distance, nearest, mask, [0, 5, 10], Settings())
    assert a == candidate_windows(
        matrix, distance, nearest, mask, [0, 5, 10], Settings()
    )
    assert [(c["start"], c["stop"]) for c in a] == [(6, 10), (2, 5)]
    assert all(not mask[c["start"] : c["stop"]].any() for c in a)


def test_empty_and_complete_coverage():
    features = {"constant": np.ones(4)}
    empty = np.zeros(4, bool)
    assert nearest_coverage(features, {"constant": 1}, empty)[1] is None
    assert feature_summary(features, empty)["constant"]["completed_quantiles"] is None
    full = ~empty
    matrix, distance, nearest = nearest_coverage(features, {"constant": 1}, full)
    assert candidate_windows(matrix, distance, nearest, full, [0, 4], Settings()) == []


@pytest.mark.parametrize("value", [[[1]], [float("nan")], [1, 2]])
def test_malformed_feature(value):
    with pytest.raises(ValueError):
        finite(value, (1,), "test")


def fixture_checkpoint(tmp_path, mutation=None):
    directory = tmp_path / "shard-000"
    directory.mkdir()
    w = {
        "iteration": 0,
        "interval_start": 0,
        "interval_stop": 3,
        "storage_device_ids": ["s"],
        "delta_hours": 1.0,
        "target_soc_mwh": [2.0],
        "attempts": [
            {
                "attempt_id": "a",
                "role": "primary_controlling",
                "slot_state": "executed",
                "supplied_executed_action": True,
                "audit": {"accepted_primal": True},
            }
        ],
        "executed_interval": {"controlling_attempt_id": "a"},
    }
    if mutation:
        mutation(w)
    raw = gzip.compress(json.dumps(w).encode())
    (directory / "w.gz").write_bytes(raw)
    c = {
        "interval": {"start": 0, "stop": 4, "half_open": True},
        "storage_device_ids": ["s"],
        "outer_plan_sha256": "outer",
        "manifest_sha256": "manifest",
        "completed_intervals": 1,
        "next_global_iteration": 1,
        "windows": [
            {
                "iteration": 0,
                "relative_path": "w.gz",
                "bytes": len(raw),
                "sha256": sha(raw),
            }
        ],
    }
    (directory / "checkpoint.json").write_text(json.dumps(c))
    return directory


def test_snapshot_counts_only_checkpointed_accepted_actions(tmp_path):
    directory = fixture_checkpoint(tmp_path)
    (directory / "unreferenced.gz").write_bytes(b"not counted")
    mask, _, _ = snapshot_completed(
        tmp_path, [0, 4], ["s"], "outer", "manifest", np.full((5, 1), 2.0), 3
    )
    assert mask.tolist() == [True, False, False, False]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda w: w.update(target_soc_mwh=[3.0]),
        lambda w: w["attempts"][0]["audit"].update(accepted_primal=False),
        lambda w: w.update(interval_stop=2),
        lambda w: w.update(storage_device_ids=["wrong"]),
        lambda w: w["executed_interval"].update(controlling_attempt_id="wrong"),
    ],
)
def test_rehashed_invalid_window_is_rejected(tmp_path, mutation):
    fixture_checkpoint(tmp_path, mutation)
    with pytest.raises(ValueError):
        snapshot_completed(
            tmp_path, [0, 4], ["s"], "outer", "manifest", np.full((5, 1), 2.0), 3
        )


def test_hash_drift_rejected(tmp_path):
    directory = fixture_checkpoint(tmp_path)
    (directory / "w.gz").write_bytes(b"drift")
    with pytest.raises(ValueError, match="hash/size"):
        snapshot_completed(
            tmp_path, [0, 4], ["s"], "outer", "manifest", np.full((5, 1), 2.0), 3
        )
