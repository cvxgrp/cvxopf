from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "experiments/m14_time_vectorization/M14C_SOLVER_MATRIX_RESULTS.json"
S4_RESULT = ROOT / "experiments/case118_annual_hierarchy/S4_RESULTS.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record() -> dict[str, object]:
    return json.loads(RESULT.read_text())


def test_promoted_s4_record_is_the_accepted_annual_authority() -> None:
    result = json.loads(S4_RESULT.read_text())

    assert _sha256(S4_RESULT) == (
        "f8194ef39d18084f90d0d6216bd1a7ee85a889bf3699571fd9f7f2b3c3dc4947"
    )
    assert result["execution_complete"] is True
    assert result["accepted_for_s4b"] is True
    assert result["classification"] == "accepted"
    assert result["horizon_steps"] == 8760
    assert result["temporal_assembly"] == "vectorized"
    assert result["canonicalization_backend"] == "SCIPY"


def test_solver_matrix_record_freezes_scope_and_authority() -> None:
    record = _record()

    assert record["classification"] == "complete_non_promotional_characterization"
    assert record["authoritative_solver"] == "CLARABEL"
    assert record["solver_policy_changed"] is False
    assert record["temporal_assembly"] == "vectorized"
    assert record["canonicalization_backend"] == "SCIPY"

    contract = record["matrix_contract"]
    assert isinstance(contract, dict)
    assert contract["solvers"] == ["OSQP", "SCS", "HIGHS"]
    assert contract["horizons"] == [24, 168, 720, 8760]
    assert contract["solver_configuration"] == "defaults"
    assert contract["automatic_retry"] is False


def test_solver_matrix_record_freezes_all_solver_versions() -> None:
    record = _record()
    environment = record["execution_environment"]
    assert isinstance(environment, dict)
    assert environment["clarabel"] == "0.11.1"
    assert environment["osqp_package"] == "1.1.3"
    assert environment["osqp_native_banner"] == "1.0.0"
    assert environment["scs"] == "3.2.11"
    assert environment["highs"] == "1.15.1"
    assert environment["highs_commit"] == "04024d7"
    assert environment["mosek"] == "11.2.3"

    bindings = record["artifact_bindings"]
    assert isinstance(bindings, dict)
    protocol_path = ROOT / str(bindings["tracked_protocol_path"])
    assert _sha256(protocol_path) == bindings["tracked_protocol_sha256"]
    protocol = json.loads(protocol_path.read_text())
    assert protocol["solver_versions"] == {
        "clarabel": "0.11.1",
        "osqp_package": "1.1.3",
        "osqp_native_banner": "1.0.0",
        "scs": "3.2.11",
        "highs": "1.15.1",
        "highs_commit": "04024d7",
        "mosek": "11.2.3",
    }
    assert protocol["version_evidence_status"].startswith("backfilled after execution")


def test_solver_matrix_record_binds_exact_runner_and_raw_root_when_present() -> None:
    record = _record()
    bindings = record["artifact_bindings"]
    assert isinstance(bindings, dict)

    runner = ROOT / str(bindings["runner_path"])
    assert _sha256(runner) == bindings["runner_sha256"]
    mosek_runner = ROOT / str(bindings["mosek_runner_path"])
    assert _sha256(mosek_runner) == bindings["mosek_runner_sha256"]

    raw_root = ROOT / str(bindings["raw_output_path"])
    if raw_root.exists():
        assert _sha256(raw_root / "protocol.json") == bindings["protocol_sha256"]
        assert (
            _sha256(raw_root / "reference-solvers.json")
            == bindings["reference_solvers_sha256"]
        )
        assert (
            _sha256(raw_root / "matrix-result.json") == bindings["matrix_result_sha256"]
        )

    mosek_root = ROOT / str(bindings["mosek_raw_output_path"])
    if mosek_root.exists():
        assert (
            _sha256(mosek_root / "arm-result.json")
            == bindings["mosek_arm_result_sha256"]
        )
        assert (
            _sha256(mosek_root / "comparison-result.json")
            == bindings["mosek_comparison_result_sha256"]
        )


def test_solver_matrix_is_complete_and_has_only_one_accepted_alternative() -> None:
    record = _record()
    records = record["matrix_records"]
    assert isinstance(records, list)

    identities = {
        (item["solver"], item["horizon_steps"])
        for item in records
        if isinstance(item, dict)
    }
    assert identities == {
        (solver, horizon)
        for solver in ("OSQP", "SCS", "HIGHS")
        for horizon in (24, 168, 720, 8760)
    }

    accepted = [
        item
        for item in records
        if isinstance(item, dict) and item["classification"] == "accepted"
    ]
    assert accepted == [
        next(
            item
            for item in records
            if isinstance(item, dict)
            and item["solver"] == "HIGHS"
            and item["horizon_steps"] == 24
        )
    ]
    assert all(
        item["classification"] != "accepted"
        for item in records
        if isinstance(item, dict) and item["horizon_steps"] == 8760
    )

    summary = record["summary"]
    assert isinstance(summary, dict)
    assert summary["accepted_alternative_arms"] == 1
    assert summary["total_alternative_arms"] == 12
    assert summary["accepted_alternative_annual_arms"] == 0


def test_clarabel_reference_is_matched_and_accepted_at_every_horizon() -> None:
    references = _record()["clarabel_reference"]
    assert isinstance(references, list)
    assert [item["horizon_steps"] for item in references] == [24, 168, 720, 8760]
    assert all(item["classification"] == "accepted" for item in references)


def test_mosek_prefix_qualification_is_not_misrepresented_as_matched() -> None:
    references = _record()["mosek_reference"]
    assert isinstance(references, list)
    assert all(
        item["conditioned_input_match"] is False
        for item in references
        if item["horizon_steps"] < 8760
    )
    annual = next(item for item in references if item["horizon_steps"] == 8760)
    assert annual["conditioned_input_match"] is True
    assert annual["classification"] == "failed"
