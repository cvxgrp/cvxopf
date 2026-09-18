"""Independent M14a scaling-record reconstruction tests."""

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.m14_time_vectorization import run_m14a
from experiments.m14_time_vectorization.m14a_analysis import (
    _analysis_source_fingerprint,
    _promote,
    _validate_classification_record,
    analyze_run,
    analyze_runs,
)
from experiments.m14_time_vectorization.run_m14a import _source_fingerprint


def _small_frozen_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    ladder = (("singlenode_dc", 1, "case9"),)
    monkeypatch.setitem(run_m14a.FROZEN_LADDERS, "case9", ladder)
    import experiments.m14_time_vectorization.m14a_analysis as analysis

    monkeypatch.setitem(analysis.FROZEN_LADDERS, "case9", ladder)


def test_frozen_ladders_are_formulation_specific_and_ordered():
    case9 = run_m14a.FROZEN_LADDERS["case9"]
    case118 = run_m14a.FROZEN_LADDERS["case118"]

    assert [point[1] for point in case9 if point[0] == "ac"] == [1, 2, 4, 8, 24]
    assert [point[1] for point in case118 if point[0] == "lossy_dc"] == [
        24,
        168,
        720,
    ]
    assert [point[1] for point in case118 if point[0] == "singlenode_dc"][-1] == 8760


def test_frozen_ladder_rejects_formulation_subset(tmp_path: Path):
    with pytest.raises(ValueError, match="complete formulation registry"):
        run_m14a._run_parent(
            SimpleNamespace(
                output=tmp_path / "run",
                formulations=["lossy_dc"],
                horizons=[1],
                case="case9",
                frozen_ladder="case9",
                timeout_seconds=30.0,
            )
        )


def test_complete_frozen_run_is_independently_reconstructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _small_frozen_ladder(monkeypatch)
    output = tmp_path / "run"
    run_m14a._run_parent(
        SimpleNamespace(
            output=output,
            formulations=["singlenode_dc"],
            horizons=[99],
            case="case118",
            frozen_ladder="case9",
            timeout_seconds=30.0,
        )
    )

    result = analyze_run(output)

    assert result["execution_complete"] is True
    assert (
        result["accepted_as_ladder_record"]
        is result["execution_context"]["worktree_clean"]
    )
    assert len(result["points"]) == 1
    assert result["points"][0]["horizon"] == 1
    assert result["points"][0]["classification"] == "completed"
    assert result["points"][0]["maximum_residual"] <= result["audit_tolerance"]


def test_analysis_rejects_tampered_worker_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _small_frozen_ladder(monkeypatch)
    output = tmp_path / "run"
    run_m14a._run_parent(
        SimpleNamespace(
            output=output,
            formulations=["singlenode_dc"],
            horizons=[1],
            case="case9",
            frozen_ladder="case9",
            timeout_seconds=30.0,
        )
    )
    artifact = output / "singlenode_dc-00001.json"
    payload = json.loads(artifact.read_text())
    payload["residuals"]["storage_recurrence_mwh_abs"] = 1.0
    artifact.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="size mismatch|hash mismatch"):
        analyze_run(output)


def test_analysis_independently_reconstructs_residuals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _small_frozen_ladder(monkeypatch)
    output = tmp_path / "run"
    run_m14a._run_parent(
        SimpleNamespace(
            output=output,
            formulations=["singlenode_dc"],
            horizons=[1],
            case="case9",
            frozen_ladder="case9",
            timeout_seconds=30.0,
        )
    )
    artifact = output / "singlenode_dc-00001.json"
    payload = json.loads(artifact.read_text())
    payload["residuals"]["storage_recurrence_mwh_abs"] = 1e-7
    artifact.write_text(json.dumps(payload))
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    identity = manifest["records"][0]["artifact"]
    identity["bytes"] = artifact.stat().st_size
    identity["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="does not reconstruct exactly"):
        analyze_run(output)


def test_incomplete_run_cannot_be_promoted(tmp_path: Path):
    destination = tmp_path / "M14A_RESULTS.json"
    with pytest.raises(ValueError, match="incomplete"):
        _promote(
            destination,
            {"execution_complete": False, "accepted_for_m14b": False},
        )
    assert not destination.exists()


def test_complete_promotion_is_immutable(tmp_path: Path):
    destination = tmp_path / "M14A_RESULTS.json"
    payload = {
        "execution_complete": True,
        "accepted_for_m14b": True,
        "execution_commit": "commit",
        "execution_source_fingerprint": "s" * 64,
        "analysis_context": {
            "git_commit": "commit",
            "source_fingerprint": "a" * 64,
            "worktree_clean": True,
        },
        "value": 1,
    }
    _promote(destination, payload)
    changed = dict(payload, value=2)
    with pytest.raises(FileExistsError):
        _promote(destination, changed)
    assert json.loads(destination.read_text()) == payload


def test_consolidation_requires_both_frozen_ladders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _small_frozen_ladder(monkeypatch)
    output = tmp_path / "run"
    run_m14a._run_parent(
        SimpleNamespace(
            output=output,
            formulations=["singlenode_dc"],
            horizons=[1],
            case="case9",
            frozen_ladder="case9",
            timeout_seconds=30.0,
        )
    )
    with pytest.raises(ValueError, match="every frozen ladder"):
        analyze_runs([output])


def _synthetic_run(
    ladder: str,
    *,
    platform: str = "platform",
    accepted: bool = True,
    execution_complete: bool = True,
    dirty_worker_points: list[dict] | None = None,
) -> dict:
    return {
        "frozen_ladder": ladder,
        "execution_complete": execution_complete,
        "execution_provenance_clean": accepted,
        "accepted_as_ladder_record": execution_complete and accepted,
        "dirty_worker_points": dirty_worker_points or [],
        "execution_context": {
            "git_commit": "commit",
            "source_fingerprint": "source",
            "worktree_clean": True,
            "platform": platform,
            "machine": "machine",
            "python": "3.11",
            "packages": {"cvxpy": "1", "ipopt": "2"},
        },
    }


def test_complete_consolidation_requires_matched_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import experiments.m14_time_vectorization.m14a_analysis as analysis

    runs = {
        "case9": _synthetic_run("case9"),
        "case118": _synthetic_run("case118"),
    }
    monkeypatch.setattr(analysis, "analyze_run", lambda path: runs[path.name])
    monkeypatch.setattr(
        analysis,
        "_git",
        lambda *args: "" if args == ("status", "--porcelain") else "commit",
    )
    monkeypatch.setattr(analysis, "_analysis_source_fingerprint", lambda: "a" * 64)

    result = analyze_runs([tmp_path / "case9", tmp_path / "case118"])
    assert result["execution_complete"] is True
    assert result["accepted_for_m14b"] is True

    runs["case118"] = _synthetic_run("case118", platform="other")
    with pytest.raises(ValueError, match="platform"):
        analyze_runs([tmp_path / "case9", tmp_path / "case118"])


def test_dirty_analysis_cannot_advance_or_promote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import experiments.m14_time_vectorization.m14a_analysis as analysis

    runs = {
        "case9": _synthetic_run("case9"),
        "case118": _synthetic_run("case118"),
    }
    monkeypatch.setattr(analysis, "analyze_run", lambda path: runs[path.name])
    monkeypatch.setattr(
        analysis,
        "_git",
        lambda *args: "dirty" if args == ("status", "--porcelain") else "commit",
    )
    monkeypatch.setattr(analysis, "_analysis_source_fingerprint", lambda: "source")
    result = analyze_runs([tmp_path / "case9", tmp_path / "case118"])
    assert result["accepted_for_m14b"] is False
    with pytest.raises(ValueError, match="clean provenance"):
        _promote(tmp_path / "result.json", result)


def test_reviewed_nonexecution_changes_qualify_dirty_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import experiments.m14_time_vectorization.m14a_analysis as analysis

    dirty_point = {
        "case": "case118",
        "formulation": "singlenode_dc",
        "horizon": 8760,
        "git_commit": "commit",
        "source_fingerprint": "source",
    }
    runs = {
        "case9": _synthetic_run("case9"),
        "case118": _synthetic_run(
            "case118", accepted=False, dirty_worker_points=[dirty_point]
        ),
    }
    monkeypatch.setattr(analysis, "analyze_run", lambda path: runs[path.name])
    monkeypatch.setattr(
        analysis,
        "_git",
        lambda *args: "" if args == ("status", "--porcelain") else "analysis",
    )
    monkeypatch.setattr(analysis, "_analysis_source_fingerprint", lambda: "a" * 64)
    exception = {
        "schema_version": 1,
        "scope": "non_execution_worktree_changes",
        "reason": "presentation preparation",
        "paths": ["presentations/update.tex"],
        "execution_commit": "commit",
        "execution_source_fingerprint": "source",
    }

    result = analyze_runs(
        [tmp_path / "case9", tmp_path / "case118"],
        reviewed_worktree_exception=exception,
    )

    assert result["accepted_for_m14b"] is True
    assert result["reviewed_worktree_exception"]["dirty_worker_points"] == [dirty_point]

    exception["paths"] = ["experiments/m14_time_vectorization/run_m14a.py"]
    with pytest.raises(ValueError, match="execution-source path"):
        analyze_runs(
            [tmp_path / "case9", tmp_path / "case118"],
            reviewed_worktree_exception=exception,
        )


def test_reviewed_dirty_worker_exception_does_not_qualify_incomplete_ladder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import experiments.m14_time_vectorization.m14a_analysis as analysis

    dirty_point = {
        "case": "case118",
        "formulation": "singlenode_dc",
        "horizon": 8760,
        "git_commit": "commit",
        "source_fingerprint": "source",
    }
    runs = {
        "case9": _synthetic_run("case9"),
        "case118": _synthetic_run(
            "case118",
            accepted=False,
            execution_complete=False,
            dirty_worker_points=[dirty_point],
        ),
    }
    monkeypatch.setattr(analysis, "analyze_run", lambda path: runs[path.name])
    monkeypatch.setattr(
        analysis,
        "_git",
        lambda *args: "" if args == ("status", "--porcelain") else "analysis",
    )
    monkeypatch.setattr(analysis, "_analysis_source_fingerprint", lambda: "a" * 64)

    result = analyze_runs(
        [tmp_path / "case9", tmp_path / "case118"],
        reviewed_worktree_exception={
            "schema_version": 1,
            "scope": "non_execution_worktree_changes",
            "reason": "presentation preparation",
            "paths": ["presentations/update.tex"],
            "execution_commit": "commit",
            "execution_source_fingerprint": "source",
        },
    )

    assert result["execution_complete"] is False
    assert result["accepted_for_m14b"] is False
    assert result["reviewed_worktree_exception"] is not None


def test_analysis_fingerprint_uses_execution_source_order():
    assert _analysis_source_fingerprint() == _source_fingerprint()


@pytest.mark.parametrize(
    "record",
    [
        {
            "classification": "completed",
            "returncode": 1,
            "artifact": {"path": "result.json"},
            "evidence": {},
        },
        {
            "classification": "wall_time_limit",
            "returncode": 0,
            "artifact": None,
            "evidence": {},
        },
        {
            "classification": "solve_not_accepted",
            "returncode": 0,
            "artifact": None,
            "evidence": {},
        },
    ],
)
def test_classification_matrix_rejects_contradictory_records(record):
    with pytest.raises(ValueError, match="classification evidence"):
        _validate_classification_record(record)
