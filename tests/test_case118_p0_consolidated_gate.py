from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

from experiments.case118_annual_hierarchy import p0_consolidated_gate
from experiments.case118_annual_hierarchy.p0_consolidated_gate import (
    atomic_write_report,
    run_consolidated_p0,
)
from experiments.case118_annual_hierarchy.p0_injected_equivalence import (
    INJECTED_CASES,
)


ROOT = Path(__file__).resolve().parents[1]
TRACKED_RESULT = (
    ROOT / "experiments/case118_annual_hierarchy/P0_RESULTS.json"
)
TRACKED_RESULT_SHA256 = (
    "6bd6b65b7475bd0ac00ad1e3070b00ac687fbdeaa9f3c929797a9f8df5818198"
)


def test_tracked_formal_p0_result_is_integrity_bound_and_clean():
    payload = json.loads(TRACKED_RESULT.read_text())

    assert hashlib.sha256(TRACKED_RESULT.read_bytes()).hexdigest() == (
        TRACKED_RESULT_SHA256
    )
    assert payload["passed"] is True
    assert payload["decision"] == "advance_to_s2"
    assert payload["failures"] == []
    assert payload["clean_source_required"] is True
    assert payload["execution_context"]["git_status_porcelain"] == ""
    assert payload["execution_context"]["git_commit"] == (
        "81b31894e70ef3c13720b5f44aeddcff6f70bd71"
    )


def test_consolidated_p0_gate_executes_complete_frozen_registry(
    tmp_path, monkeypatch
):
    report = run_consolidated_p0(
        tmp_path / "work", clean_source_required=False
    )

    assert report.passed, report.failures
    assert tuple(item.horizon_steps for item in report.nominal) == (6, 24)
    assert all(item.equivalent for item in report.nominal)
    assert tuple(item.case_name for item in report.injected) == tuple(
        case.name for case in INJECTED_CASES
    )
    assert all(item.equivalent for item in report.injected)
    assert all(item.unsuccessful_evidence_verified for item in report.injected)
    assert report.persistence.passed
    assert report.s3b.passed
    assert report.s1_boundary.passed
    assert report.import_boundary.passed

    destination = tmp_path / "P0_RESULTS.json"
    atomic_write_report(destination, report)
    payload = json.loads(destination.read_text())
    assert payload["passed"] is True
    assert payload["decision"] == "preliminary_pass"
    assert payload["failures"] == []
    assert payload["execution_context"]["git_commit"]
    assert len(payload["execution_context"]["combined_source_sha256"]) == 64

    nominal = {item.horizon_steps: item for item in report.nominal}
    injected = {item.case_name: item for item in report.injected}
    bad_persistence = replace(
        report.persistence,
        stopped_status="complete",
        stopped_intervals=6,
        stopped_reason=None,
        failures=("observer_stop",),
    )
    monkeypatch.setattr(
        p0_consolidated_gate,
        "run_nominal_equivalence",
        lambda horizon, _path: nominal[horizon],
    )
    monkeypatch.setattr(
        p0_consolidated_gate,
        "run_injected_equivalence",
        lambda case, _path: injected[case.name],
    )
    monkeypatch.setattr(
        p0_consolidated_gate,
        "run_persistence_gate",
        lambda _path: bad_persistence,
    )
    monkeypatch.setattr(
        p0_consolidated_gate,
        "run_s3b_normalization_gate",
        lambda: report.s3b,
    )
    monkeypatch.setattr(
        p0_consolidated_gate,
        "run_s1_boundary_gate",
        lambda: report.s1_boundary,
    )
    monkeypatch.setattr(
        p0_consolidated_gate,
        "run_import_gate",
        lambda: report.import_boundary,
    )

    blocked = run_consolidated_p0(
        tmp_path / "observer-broken", clean_source_required=False
    )

    assert not blocked.passed
    assert blocked.to_json_value()["decision"] == "p0_blocked"
    assert "persistence:observer_stop" in blocked.failures
