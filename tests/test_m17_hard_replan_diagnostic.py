"""Synthetic contract tests for the M17 interval-35 diagnostic runner."""

import gzip
import json

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import OPFBuild
from experiments.hierarchical_battery_resilience import (
    hard_replan_diagnostic as diagnostic,
)
from experiments.hierarchical_battery_resilience.manual_runner import SolveAudit
from experiments.hierarchical_battery_resilience.scenario import (
    load_frozen_scenario,
)


def _source_record(values):
    return diagnostic.DiagnosticRecord(
        record_id="source",
        category="source",
        problem=diagnostic.PRECEDING_SOURCE,
        input_hashes={},
        solver_context=diagnostic.SOLVER_CONTEXT,
        initialization=diagnostic.InitializationSpec("A_flat"),
        solver_executed=True,
        x0_verified=True,
        solver_x0=[0.0],
        solver_x0_layout=(),
        solver_x0_layout_signature="synthetic",
        model_x0_count=1,
        auxiliary_x0_count=0,
        source_classification="reproduced_authoritative_source",
        source_differences=(),
        starting_values={},
        raw_perturbations=None,
        object_ids_before={"variables": (1,)},
        object_ids_after={"variables": (1,)},
        object_identity_preserved=True,
        results={},
        audit=None,
        terminal_deviation_mwh=None,
        scientific_classification="accepted",
        solution_values=values,
    )


def _synthetic_build(variable):
    problem = cp.Problem(cp.Minimize(cp.sum_squares(variable)))
    return OPFBuild(problem, {}, {}, "ac", False)


def test_canonical_registry_is_fixed_before_execution():
    records = diagnostic.CANONICAL_RECORDS

    assert len(records) == diagnostic.EXPECTED_RECORD_COUNT == 14
    assert len({record.record_id for record in records}) == 14
    assert [record.category for record in records].count("matched_state") == 6
    assert [record.category for record in records].count("source") == 2
    assert (
        [record.category for record in records].count(
            "alternate_initialization"
        )
        == 6
    )
    assert records[0].record_id == "matched_frozen_raw"
    assert records[1].record_id == "matched_replanned_raw"


def test_integrity_gate_checks_sources_and_artifact_hashes(tmp_path):
    tracked = json.loads(diagnostic.AUTHORITATIVE_MANIFEST.read_text())
    # This test isolates artifact and source-gate behavior using the current
    # tree. The committed S3 manifest intentionally retains its historical
    # fingerprint and production verification must reject later source edits.
    tracked["execution_source"]["source_fingerprints"][
        "cvxopf_python_tree_sha256"
    ] = diagnostic._source_fingerprint(
        sorted((diagnostic.REPOSITORY_ROOT / "src/cvxopf").rglob("*.py"))
    )
    scenario_path = (
        diagnostic.REPOSITORY_ROOT
        / "experiments/hierarchical_battery_resilience/scenario.py"
    )
    tracked["execution_source"]["source_fingerprints"]["files"][
        "experiments/hierarchical_battery_resilience/scenario.py"
    ] = diagnostic._sha256(scenario_path)
    results = tmp_path / "results"
    results.mkdir()
    for name in (
        "frozen__hard_equality.json.gz",
        "replan_every_step__hard_equality.json.gz",
    ):
        with gzip.open(results / name, "wt", encoding="utf-8") as stream:
            json.dump({"name": name}, stream)
        tracked["artifacts"][name] = {
            "bytes": (results / name).stat().st_size,
            "sha256": diagnostic._sha256(results / name),
        }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(tracked))

    diagnostic.verify_authoritative_sources(results, manifest)
    with gzip.open(
        results / "frozen__hard_equality.json.gz", "wt", encoding="utf-8"
    ) as stream:
        json.dump({"altered": True}, stream)
    with pytest.raises(ValueError, match="integrity failure"):
        diagnostic.verify_authoritative_sources(results, manifest)


def test_shifted_start_uses_suffixes_and_recomputes_soc():
    values = {"static": np.array([9.0])}
    for step in range(5):
        values[f"Pg_{step}"] = np.array([10.0 + step])
        values[f"b_{step}"] = np.array([-(step + 1.0)])
        values[f"b_q_{step}"] = np.array([step + 0.5])
        values[f"soc_{step}"] = np.array([100.0 + step])
    shifted = diagnostic._shift_preceding_start(
        _source_record(values), initial_soc_mwh=500.0, delta_hours=1.0
    )

    assert shifted["static"] == pytest.approx([9.0])
    assert shifted["Pg_0"] == pytest.approx([11.0])
    assert shifted["Pg_3"] == pytest.approx([14.0])
    assert shifted["Pg_4"] == pytest.approx([14.0])
    assert shifted["b_0"] == pytest.approx([-2.0])
    assert shifted["b_3"] == pytest.approx([-5.0])
    assert shifted["b_4"] == pytest.approx([0.0])
    assert shifted["b_q_4"] == pytest.approx([0.0])
    assert [shifted[f"soc_{step}"][0] for step in range(5)] == pytest.approx(
        [502.0, 505.0, 509.0, 514.0, 514.0]
    )


def test_source_equivalence_distinguishes_reproduction_from_new_basin():
    scenario = load_frozen_scenario()
    record = _source_record({})
    record.problem = diagnostic.MATCHED_PROBLEMS[0]
    record.results = {"objective": 10.0}
    record.audit = SolveAudit(
        status="optimal",
        outcome="accepted",
        accepted_primal=True,
        missing_or_nonfinite_fields=(),
        identity_error=None,
        residuals={},
        exception=None,
        wall_time_seconds=1.0,
        solver_num_iters=None,
        solver_setup_time_seconds=None,
        solver_solve_time_seconds=None,
    )
    authoritative = {
        "audit": {
            "status": "optimal",
            "outcome": "accepted",
            "residuals": {},
        },
        "window_diagnosis": "hard_target_met",
        "results": {"objective": 10.0},
        "initial_soc_mwh": {
            diagnostic.STORAGE_ID: diagnostic.FROZEN_INITIAL_SOC_MWH
        },
        "target_soc_mwh": {
            diagnostic.STORAGE_ID: diagnostic.FROZEN_TARGET_SOC_MWH
        },
        "interval_start": 35,
        "interval_stop": 40,
        "storage_device_ids": [diagnostic.STORAGE_ID],
    }

    classification, differences = diagnostic.classify_source_equivalence(
        record, authoritative, scenario
    )
    assert classification == "reproduced_authoritative_source"
    assert differences == ()

    record.results["objective"] = 10.1
    classification, differences = diagnostic.classify_source_equivalence(
        record, authoritative, scenario
    )
    assert classification == "new_accepted_source_basin"
    assert differences == ("objective",)

def test_perturbation_is_deterministic_fortran_order_and_projected():
    variable = cp.Variable((2, 2), bounds=[-2.0, 2.0], name="x")
    build = _synthetic_build(variable)
    center = np.array([[1.0, 2.0], [3.0, 4.0]])
    source = _source_record({"x": center})

    assigned, changes = diagnostic._perturb_start(
        source, build, scale=0.1, seed=17
    )
    rng = np.random.default_rng(17)
    flat = center.flatten(order="F")
    expected_change = 0.1 * np.maximum(1.0, np.abs(flat)) * rng.standard_normal(4)
    expected_raw = (flat + expected_change).reshape((2, 2), order="F")

    assert changes["x"].flatten(order="F") == pytest.approx(expected_change)
    assert assigned["x"] == pytest.approx(variable.project(expected_raw))
    assert np.max(assigned["x"]) <= 2.0


def test_synthetic_dnlp_receives_assigned_value_as_actual_x0():
    variable = cp.Variable(bounds=[-2.0, 2.0], name="x")
    variable.value = np.array(0.375)
    build = _synthetic_build(variable)

    run = diagnostic._run_build_with_verified_x0(build)

    assert run.exception is None
    assert run.x0_verified
    assert run.solver_executed
    assert not run.intercepted_before_ipopt
    assert run.object_ids_before == run.object_ids_after
    assert set(run.object_ids_before) == {
        "variables",
        "constraints",
        "parameters",
    }
    assert run.starting_values["x"] == pytest.approx(0.375)
    assert run.solver_x0 == pytest.approx([0.375])
    assert run.solver_x0_layout[0]["name"] == "x"
    assert run.solver_x0_layout[0]["is_original_variable"] is True


def test_record_payload_retains_before_and_after_object_identities():
    payload = diagnostic.record_payload(_source_record({}))

    assert payload["solver_x0"] == [0.0]
    assert payload["solver_x0_layout_signature"] == "synthetic"
    assert payload["model_x0_count"] == 1
    assert payload["auxiliary_x0_count"] == 0
    assert payload["object_ids_before"] == {"variables": [1]}
    assert payload["object_ids_after"] == {"variables": [1]}
    assert payload["object_identity_preserved"] is True


def _auxiliary_x0(run):
    return np.concatenate(
        [
            run.solver_x0[item["start"] : item["stop"]]
            for item in run.solver_x0_layout
            if not item["is_original_variable"]
        ]
    )


def test_x0_interception_characterizes_reduction_without_calling_ipopt():
    scenario = load_frozen_scenario()
    build = diagnostic._build_problem(scenario, diagnostic.MATCHED_PROBLEMS[1])
    run = diagnostic._run_build_with_verified_x0(
        build, intercept_before_ipopt=True
    )

    assert run.exception == (
        "X0InterceptionComplete: verified IPOPT x0; "
        "stopped before solver execution"
    )
    assert run.x0_verified
    assert not run.solver_executed
    assert run.intercepted_before_ipopt
    assert len(run.solver_x0) == 930
    assert run.model_x0_count == 745
    assert run.auxiliary_x0_count == 185
    assert sum(
        item["stop"] - item["start"]
        for item in run.solver_x0_layout
        if item["is_original_variable"]
    ) == 745

    for item in run.solver_x0_layout:
        if not item["is_original_variable"]:
            continue
        expected = run.starting_values[item["name"]].flatten(order="F")
        assert run.solver_x0[item["start"] : item["stop"]] == pytest.approx(
            expected
        )


def test_auxiliary_initialization_is_deterministic_and_dependence_is_captured():
    scenario = load_frozen_scenario()
    first_build = diagnostic._build_problem(
        scenario, diagnostic.MATCHED_PROBLEMS[1]
    )
    repeated_build = diagnostic._build_problem(
        scenario, diagnostic.MATCHED_PROBLEMS[1]
    )
    changed_build = diagnostic._build_problem(
        scenario, diagnostic.MATCHED_PROBLEMS[1]
    )
    diagnostic._complete_start(changed_build)
    for index, variable in enumerate(
        diagnostic._variables_by_name(changed_build).values(), start=1
    ):
        value = np.asarray(variable.value, dtype=float)
        change = index * 1e-6 * np.maximum(1.0, np.abs(value))
        variable.value = variable.project(value + change)

    first = diagnostic._run_build_with_verified_x0(
        first_build, intercept_before_ipopt=True
    )
    repeated = diagnostic._run_build_with_verified_x0(
        repeated_build, intercept_before_ipopt=True
    )
    changed = diagnostic._run_build_with_verified_x0(
        changed_build, intercept_before_ipopt=True
    )

    assert first.solver_x0_layout_signature == repeated.solver_x0_layout_signature
    assert first.solver_x0_layout_signature == changed.solver_x0_layout_signature
    assert _auxiliary_x0(first) == pytest.approx(_auxiliary_x0(repeated))
    auxiliary_change = _auxiliary_x0(changed) - _auxiliary_x0(first)
    assert np.count_nonzero(auxiliary_change) == 5
    assert np.max(np.abs(auxiliary_change)) == pytest.approx(1e-5)
    assert any(
        first.starting_values[name] != pytest.approx(value)
        for name, value in changed.starting_values.items()
    )


def test_incomplete_dependency_keeps_record_cardinality_and_blocks_all_failed():
    records = {}
    for registered in diagnostic.CANONICAL_RECORDS:
        records[registered.record_id] = diagnostic.unavailable_record(
            registered.problem,
            registered.initialization,
            "synthetic unavailable dependency",
        )
    summary = diagnostic.diagnostic_summary(records)

    assert summary["actual_record_count"] == 14
    assert summary["actual_solver_call_count"] == 0
    assert not summary["complete"]
    assert summary["classification"] == "incomplete"


def test_initialization_construction_error_retains_registered_record(
    monkeypatch,
):
    registered = diagnostic.CANONICAL_RECORDS[0]

    def fail_before_solver(*_args, **_kwargs):
        raise ValueError("synthetic construction failure")

    monkeypatch.setattr(
        diagnostic, "_solve_with_verified_x0", fail_before_solver
    )
    record = diagnostic._execute_registered_record(
        registered, load_frozen_scenario(), {}
    )

    assert record.record_id == registered.record_id
    assert not record.solver_executed
    assert not record.x0_verified
    assert record.source_classification is None
    assert record.scientific_classification.startswith(
        "initialization_construction_error:ValueError"
    )


def test_unexecuted_nearby_matrix_row_makes_diagnostic_incomplete():
    records = {}
    for registered in diagnostic.CANONICAL_RECORDS:
        record = diagnostic.unavailable_record(
            registered.problem, registered.initialization, "placeholder"
        )
        record.solver_executed = True
        record.x0_verified = True
        record.object_identity_preserved = True
        record.audit = type("Audit", (), {"accepted_primal": False})()
        if registered.record_id in {
            "matched_frozen_raw",
            "source_target_free",
            "source_preceding",
        }:
            record.source_classification = "reproduced_authoritative_source"
        records[registered.record_id] = record
    records["matched_frozen_canonical"].solver_executed = False

    summary = diagnostic.diagnostic_summary(records)

    assert not summary["complete"]
    assert summary["classification"] == "incomplete"


def test_exact_acceptance_survives_incomplete_protocol():
    records = {
        registered.record_id: diagnostic.unavailable_record(
            registered.problem, registered.initialization, "placeholder"
        )
        for registered in diagnostic.CANONICAL_RECORDS
    }
    records["B_frozen"].audit = type(
        "Audit", (), {"accepted_primal": True}
    )()
    records["B_frozen"].solver_executed = True
    records["B_frozen"].x0_verified = True

    summary = diagnostic.diagnostic_summary(records)

    assert not summary["complete"]
    assert summary["exact_problem_accepted"]
    assert summary["feasibility_classification"] == (
        "modeled_feasible_alternate_initialization_incomplete_control"
    )
    assert summary["classification"] == (
        "modeled_feasible_alternate_initialization_incomplete_control"
    )


def test_dependent_record_retains_parent_source_basin(monkeypatch):
    registered = next(
        record
        for record in diagnostic.CANONICAL_RECORDS
        if record.record_id == "B_frozen"
    )
    parent = _source_record({"x": np.array([1.0])})
    parent.record_id = str(registered.dependency)
    parent.source_classification = "new_accepted_source_basin"
    parent.source_differences = ("objective",)
    solved = diagnostic.unavailable_record(
        registered.problem, registered.initialization, "synthetic"
    )

    monkeypatch.setattr(diagnostic, "_copy_start", lambda _source: {})
    monkeypatch.setattr(
        diagnostic, "_solve_with_verified_x0", lambda *_args: solved
    )
    record = diagnostic._execute_registered_record(
        registered,
        load_frozen_scenario(),
        {str(registered.dependency): parent},
    )

    assert record.source_classification == "new_accepted_source_basin"
    assert record.source_differences == ("objective",)


def test_control_success_has_distinct_classification():
    records = {}
    accepted_audit = type(
        "Audit", (), {"accepted_primal": True}
    )()
    for registered in diagnostic.CANONICAL_RECORDS:
        record = diagnostic.unavailable_record(
            registered.problem, registered.initialization, "placeholder"
        )
        record.solver_executed = True
        record.x0_verified = True
        record.object_identity_preserved = True
        record.audit = type(
            "Audit", (), {"accepted_primal": False}
        )()
        if registered.record_id in {
            "matched_frozen_raw",
            "source_target_free",
            "source_preceding",
        }:
            record.source_classification = "reproduced_authoritative_source"
        records[registered.record_id] = record
    records["matched_replanned_raw"].audit = accepted_audit

    summary = diagnostic.diagnostic_summary(records)

    assert summary["complete"]
    assert summary["classification"] == (
        "modeled_feasible_run_to_run_or_backend_sensitivity"
    )


def test_complete_failures_and_alternate_success_are_distinct():
    records = {}
    for registered in diagnostic.CANONICAL_RECORDS:
        record = diagnostic.unavailable_record(
            registered.problem, registered.initialization, "placeholder"
        )
        record.solver_executed = True
        record.x0_verified = True
        record.object_identity_preserved = True
        record.audit = type(
            "Audit", (), {"accepted_primal": False}
        )()
        if registered.record_id in {
            "matched_frozen_raw",
            "source_target_free",
            "source_preceding",
        }:
            record.source_classification = "reproduced_authoritative_source"
        records[registered.record_id] = record

    summary = diagnostic.diagnostic_summary(records)
    assert summary["complete"]
    assert summary["classification"] == (
        "all_declared_initializations_failed_unresolved"
    )

    records["B_frozen"].audit = type(
        "Audit", (), {"accepted_primal": True}
    )()
    summary = diagnostic.diagnostic_summary(records)
    assert summary["classification"] == (
        "modeled_feasible_initialization_dependent"
    )


def test_artifact_writer_preserves_all_registered_records(tmp_path):
    records = {
        registered.record_id: diagnostic.unavailable_record(
            registered.problem,
            registered.initialization,
            "synthetic unavailable dependency",
        )
        for registered in diagnostic.CANONICAL_RECORDS
    }
    summary = diagnostic.diagnostic_summary(records)
    context = {"git_commit": "synthetic", "git_dirty": False}

    metadata = diagnostic.write_artifacts(
        records, summary, tmp_path, context
    )

    assert set(metadata["artifacts"]) == {
        "diagnostic_records.json.gz",
        "diagnostic_summary.json",
    }
    with gzip.open(
        tmp_path / "diagnostic_records.json.gz", "rt", encoding="utf-8"
    ) as stream:
        payload = json.load(stream)
    assert len(payload) == 14
    assert [record["record_id"] for record in payload] == [
        registered.record_id for registered in diagnostic.CANONICAL_RECORDS
    ]
    assert json.loads((tmp_path / "diagnostic_summary.json").read_text())[
        "classification"
    ] == "incomplete"
