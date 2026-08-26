"""M14a solved, failure, and scaling-record baselines."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import warnings
from dataclasses import asdict
import subprocess

import cvxpy as cp
import numpy as np
import pytest

from cvxopf.results import extract_results
from cvxopf.characterization import (
    characterize_convex_canonicalization,
    characterize_source_graph,
)
from experiments.m14_time_vectorization import run_m14a as runner
from experiments.m14_time_vectorization.baseline import (
    audit_result,
    build_baseline_fixture,
    json_result,
    result_schema,
)
from experiments.m14_time_vectorization.run_m14a import (
    _run_parent,
    _append_phase,
    _sha256,
    _validate_worker_artifact,
    _write_immutable,
    measure_point,
)


SCHEMA_DIGESTS = {
    ("feasible", "ac"): (
        "95df1da0ca597f6a8c509c72dce458fd7c22bcea1b462f5bf67bb50efe093099"
    ),
    ("feasible", "lossy_dc"): (
        "68b9db7c84a45791839fee7810cad1a2cc1a93a55b2c92f3fa75cfb0be01d1f4"
    ),
    ("feasible", "singlenode_dc"): (
        "35840e680698e47e48d7736701e1b93690b3fae36b5f581d1fd587e6168d11ad"
    ),
    ("infeasible", "ac"): (
        "84f19de0ac9f06cbd74213a2545d64d85b0c34e9d4d9691891aa8bdab822b702"
    ),
    ("infeasible", "lossy_dc"): (
        "275c3414bc230d279d374b708da61894bbff6df9161da36eff42d014f4031101"
    ),
    ("infeasible", "singlenode_dc"): (
        "8fb04ccd9612d51ac3ee9314746defbea44991b2de8ae5d3006be2ad3c20b0ba"
    ),
}

FULL_SCHEMA_DIGESTS = {
    "ac": "7260fc0fa5957f3e54d01ff3b2bac7dad7ae67f22450e4ae41f3c53c54b43d99",
    "lossy_dc": ("bf47538be9cd141318b6da6f90bc9eaac161795bd8d2aa9490d6bd70a206269e"),
    "singlenode_dc": (
        "5b91cdbb8dd1b7729ed87c89fc895b2a389e1358dd6ffa2a62c8f579d0672b88"
    ),
}

FULL_SOURCE_DIGESTS = {
    "ac": "edeb22b142edc2342543e0c88aa2d7bf1040cc4549089bebb743fec3cd2ad42d",
    "lossy_dc": ("de40a7a65ab125bbdd699f0daeccc6b2afcc88b9f08fdc9cda04b35d154cb661"),
    "singlenode_dc": (
        "1da6f9ecc3880440b8d4f81b950d22b74807e5c6de9ff4275cd9eda67b2fdf1f"
    ),
}

FULL_CANONICAL_DIMENSIONS = {
    "lossy_dc": (66, 31, 96, 127, 66, 218, 18),
    "singlenode_dc": (22, 11, 34, 45, 22, 68, 6),
}

EXPECTED_OBJECTIVES = {
    "ac": 10593.258496096834,
    "lossy_dc": 10432.130660908686,
    "singlenode_dc": 10432.053215494549,
}


def _schema_digest(result: dict) -> str:
    encoded = json.dumps(
        result_schema(result), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_successful_fixture_retains_result_and_state_contract(formulation):
    fixture = build_baseline_fixture(formulation)
    fixture.build.solve()
    result = extract_results(fixture.build)

    assert result["status"] in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}
    assert _schema_digest(result) == SCHEMA_DIGESTS[("feasible", formulation)]
    assert float(result["objective"]) == pytest.approx(
        EXPECTED_OBJECTIVES[formulation], abs=2e-4
    )
    storage_power = np.asarray(result["b"], dtype=float)
    state = np.asarray(result["soc"], dtype=float)
    preceding = np.vstack(([50.0], state[:-1]))
    np.testing.assert_allclose(state, preceding - storage_power, atol=2e-7)
    np.testing.assert_allclose(state[-1], [50.0], atol=2e-7)
    assert np.isfinite(float(result["storage_cost"]))


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_unreachable_fixture_retains_real_infeasible_schema(formulation):
    fixture = build_baseline_fixture(formulation, outcome="infeasible")
    fixture.build.solve()
    result = extract_results(fixture.build)

    assert result["status"] in {cp.INFEASIBLE, cp.INFEASIBLE_INACCURATE}
    assert _schema_digest(result) == SCHEMA_DIGESTS[("infeasible", formulation)]
    assert result["b"] is None
    assert result["soc"] is None
    assert np.isnan(float(result["objective"]))
    assert json_result(result)["objective"] is None


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_full_component_fixture_retains_schema_and_physical_audit(formulation):
    fixture = build_baseline_fixture(formulation, components="full")
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*not DPP.*")
        fixture.build.solve()
    result = extract_results(fixture.build)

    assert result["status"] in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}
    assert _schema_digest(result) == FULL_SCHEMA_DIGESTS[formulation]
    source = characterize_source_graph(fixture.build)
    source_digest = hashlib.sha256(
        json.dumps(asdict(source), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert source_digest == FULL_SOURCE_DIGESTS[formulation]
    if formulation != "ac":
        canonical = characterize_convex_canonicalization(fixture.build)
        assert (
            canonical.canonical_variable_count,
            canonical.equality_rows,
            canonical.nonnegative_rows,
            canonical.coefficient_rows,
            canonical.coefficient_columns,
            canonical.coefficient_nonzeros,
            canonical.quadratic_nonzeros,
        ) == FULL_CANONICAL_DIMENSIONS[formulation]
    residuals = audit_result(fixture, result)
    assert residuals
    assert max(residuals.values()) <= 1e-6
    assert "active_nodal_balance_mw_abs" in residuals
    assert "active_load_service_mw_abs" in residuals
    assert "curtailment_nonnegativity_mw_abs" in residuals
    if formulation == "ac":
        assert "reactive_nodal_balance_mvar_abs" in residuals
        assert "voltage_bound_pu_abs" in residuals
        assert "thermal_limit_mva_abs" in residuals
    elif formulation == "lossy_dc":
        assert "thermal_limit_mw_abs" in residuals


@pytest.mark.parametrize("formulation", ["lossy_dc", "singlenode_dc"])
def test_measurement_record_separates_phases_and_dimensions(formulation):
    record = measure_point(formulation, 1, "case9")

    assert record["status"] in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}
    assert record["temporal_assembly"] == "stepwise"
    assert record["canonicalization_backend"] == "CPP"
    assert record["canonical_structure"] is not None
    timing = record["timing_seconds"]
    assert all(timing[name] >= 0 for name in timing)
    memory = record["peak_rss_bytes"]
    assert all(memory[name] > 0 for name in memory)
    assert record["serialized_result_bytes"] > 0


def test_ac_measurement_labels_dnlp_canonicalization_as_solve_owned():
    record = measure_point("ac", 1, "case9")
    assert record["canonicalization_backend"] == "DNLP_IPOPT"
    assert record["canonical_structure"] is None
    assert record["timing_seconds"]["canonicalization"] is None
    assert record["peak_rss_bytes"]["after_canonicalization"] is None
    context = record["execution_context"]
    assert len(context["source_fingerprint"]) == 64
    assert context["git_commit"]
    assert context["packages"]["cvxpy"]


def test_artifact_writer_is_immutable(tmp_path: Path):
    destination = tmp_path / "record.json"
    _write_immutable(destination, {"value": 1})
    with pytest.raises(FileExistsError):
        _write_immutable(destination, {"value": 2})
    assert json.loads(destination.read_text()) == {"value": 1}


def test_parent_manifest_hashes_isolated_worker_artifact(tmp_path: Path):
    output = tmp_path / "run"
    args = SimpleNamespace(
        output=output,
        formulations=["singlenode_dc"],
        horizons=[1],
        case="case9",
        timeout_seconds=30.0,
    )
    _run_parent(args)

    manifest = json.loads((output / "manifest.json").read_text())
    record = manifest["records"][0]
    assert record["classification"] == "completed"
    artifact = output / record["artifact"]["path"]
    assert record["artifact"]["bytes"] == artifact.stat().st_size
    assert record["artifact"]["sha256"] == _sha256(artifact)
    phases = output / record["evidence"]["phases"]["path"]
    assert record["evidence"]["phases"]["sha256"] == _sha256(phases)
    assert [json.loads(line)["phase"] for line in phases.read_text().splitlines()] == [
        "construction",
        "canonicalization",
        "solve",
        "extraction",
    ]


def test_worker_artifact_validation_rejects_provenance_drift(tmp_path: Path):
    destination = tmp_path / "worker.json"
    destination.write_text(
        json.dumps(
            {
                "formulation": "lossy_dc",
                "horizon": 1,
                "case": "case9",
                "execution_context": {
                    "git_commit": "commit-a",
                    "source_fingerprint": "source-b",
                },
            }
        )
    )
    with pytest.raises(ValueError, match="source_fingerprint"):
        _validate_worker_artifact(
            destination,
            formulation="lossy_dc",
            horizon=1,
            case_name="case9",
            parent_context={
                "git_commit": "commit-a",
                "source_fingerprint": "source-a",
            },
        )


def test_phase_journal_retains_construction_before_canonical_failure(
    tmp_path: Path, monkeypatch
):
    phase_path = tmp_path / "phases.jsonl"
    phase_path.write_bytes(b"")
    monkeypatch.setattr(
        runner,
        "characterize_convex_canonicalization",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("synthetic canonicalization failure")
        ),
    )
    with pytest.raises(RuntimeError, match="synthetic canonicalization"):
        measure_point(
            "lossy_dc",
            1,
            "case9",
            observer=lambda phase, payload: _append_phase(phase_path, phase, payload),
        )
    records = [json.loads(line) for line in phase_path.read_text().splitlines()]
    assert [record["phase"] for record in records] == ["construction"]
    assert records[0]["source_structure"]["formulation"] == "lossy_dc"


def test_parent_rejects_zero_return_without_worker_artifact(
    tmp_path: Path, monkeypatch
):
    context = {
        "git_commit": "commit",
        "source_fingerprint": "source",
    }
    monkeypatch.setattr(runner, "_execution_context", lambda: context)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        ),
    )
    output = tmp_path / "missing"
    _run_parent(
        SimpleNamespace(
            output=output,
            formulations=["lossy_dc"],
            horizons=[1],
            case="case9",
            timeout_seconds=30.0,
        )
    )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["records"][0]["classification"] == "artifact_missing"


def test_infeasible_fixture_rejects_horizon_that_can_reach_target():
    with pytest.raises(ValueError, match="horizon < 100"):
        build_baseline_fixture("lossy_dc", horizon=100, outcome="infeasible")
