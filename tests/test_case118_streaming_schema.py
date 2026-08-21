"""Contract tests for the experiment-owned P0 streaming archive."""

from copy import deepcopy
import gzip
import hashlib
import json

import pytest

from experiments.case118_annual_hierarchy.streaming_schema import (
    ATTEMPT_ROLES,
    PERTURBATION_SCALES,
    attempt_id,
    atomic_gzip_json,
    atomic_json,
    checkpoint_payload,
    load_verified_checkpoint,
    perturbation_seed,
    validate_checkpoint,
    validate_window_archive,
)


SOC_TOLERANCE_MWH = 1e-6
POLICY_HASH = "frozen-policy"
HORIZON_STEPS = 4
AC_WINDOW_STEPS = 3
RESULT_DIMENSIONS = {
    "generators": 2,
    "buses": 3,
    "branches": 2,
    "loads": 2,
    "storage": 1,
    "nondispatchable": 1,
    "hvdc": 0,
}
DELTA_HOURS = 1.0
OUTER_BOUNDARY_SOC_MWH = {
    boundary: {"battery": 5.0} for boundary in range(HORIZON_STEPS + 1)
}
RESIDUAL_TOLERANCES = {
    "soc_recurrence_mwh_abs": 1e-4,
    "terminal_soc_mwh_abs": 1e-3,
    "ac_active_balance_pu_abs": 1e-6,
    "ac_reactive_balance_pu_abs": 1e-6,
    "voltage_bound_pu_abs": 1e-6,
    "branch_mva_abs": 1e-4,
    "branch_normalized_squared_residual": 1e-7,
}


def _executed_evidence(accepted, role, terminal_policy, steps):
    layout = [
        {
            "name": "x",
            "shape": [2],
            "start": 0,
            "stop": 2,
            "is_original_variable": True,
        },
        {
            "name": "aux",
            "shape": [1],
            "start": 2,
            "stop": 3,
            "is_original_variable": False,
        },
    ]
    normalized = [
        {
            "label": "x",
            "shape": [2],
            "start": 0,
            "stop": 2,
            "is_original_variable": True,
        },
        {
            "label": "auxiliary_0",
            "shape": [1],
            "start": 2,
            "stop": 3,
            "is_original_variable": False,
        },
    ]
    layout_signature = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    residuals = {
        "soc_recurrence_mwh_abs": 0.0,
        "ac_active_balance_pu_abs": 0.0,
        "ac_reactive_balance_pu_abs": 0.0,
        "voltage_bound_pu_abs": 0.0,
        "branch_mva_abs": 0.0,
        "branch_normalized_squared_residual": 0.0,
        "curtailment_nonnegativity_pu_abs": 0.0,
        "branch_loss_nonnegativity_pu_abs": 0.0,
    }
    if role != "target_free":
        residuals[
            "terminal_soc_mwh_abs"
            if terminal_policy == "hard_equality"
            else "soft_terminal_cost_abs"
        ] = 0.0
    def matrix(columns, value=0.0):
        return [[value] * columns for _ in range(steps)]

    result = {
        "status": "optimal" if accepted else "user_limit",
        "objective": 1.0,
        "b": matrix(1),
        "b_q": matrix(1),
        "soc": matrix(1, 5.0),
        "Pg": matrix(2),
        "Qg": matrix(2),
        "Vm": matrix(3, 1.0),
        "Va_deg": matrix(3),
        "p_net": matrix(3),
        "q_net": matrix(3),
        "branch_p_from": matrix(2),
        "branch_q_from": matrix(2),
        "branch_p_to": matrix(2),
        "branch_q_to": matrix(2),
        "branch_s_from": matrix(2),
        "branch_s_to": matrix(2),
        "p_load": matrix(2),
        "q_load": matrix(2),
        "p_load_served": matrix(2),
        "q_load_served": matrix(2),
        "p_nd": matrix(1),
        "q_nd": matrix(1),
        "curtailment": matrix(1),
    }
    return {
        "assigned_start": {"x": [1.0, 2.0]},
        "solver_x0": [1.0, 2.0, 0.5],
        "solver_x0_layout": layout,
        "solver_evidence": {
            "layout_signature": layout_signature,
            "model_coordinate_count": 2,
            "auxiliary_coordinate_count": 1,
            "object_ids_before": {
                "variables": [1],
                "constraints": [2],
                "parameters": [3],
            },
            "object_ids_after": {
                "variables": [1],
                "constraints": [2],
                "parameters": [3],
            },
        },
        "structural_signature": {
            "variables": [{"name": "x", "shape": [2]}],
            "constraints": ["constraint-0"],
            "parameters": [{"name": "p", "shape": [1]}],
        },
        "result": result,
        "audit": {
            "status": "optimal" if accepted else "user_limit",
            "outcome": "accepted" if accepted else "unusable_primal",
            "accepted_primal": accepted,
            "missing_or_nonfinite_fields": [],
            "identity_error": None,
            "residuals": residuals,
            "exception": None,
            "wall_time_seconds": 0.1,
            "solver_num_iters": 5,
            "solver_setup_time_seconds": 0.01,
            "solver_solve_time_seconds": 0.08,
        },
    }


def _attempt(
    iteration,
    ordinal,
    *,
    accepted=False,
    controlling=False,
    state="executed",
    preceding_id=None,
    terminal_policy="hard_equality",
    steps=3,
):
    transformation = (
        ("flat" if iteration == 0 else "shifted_preceding")
        if ordinal < 2
        else "copy_target_free"
        if ordinal == 2
        else "perturb_target_free"
        if ordinal < 6
        else "perturb_causal"
    )
    scale = None if ordinal < 3 else PERTURBATION_SCALES[(ordinal - 3) % 3]
    seed = None if ordinal < 3 else perturbation_seed(iteration, ordinal)
    if state in {"not_needed_after_acceptance", "source_unavailable"}:
        source_kind = None
        source_id = None
    elif ordinal in {0, 1, 6, 7, 8} and iteration == 0:
        source_kind = "generated_flat"
        source_id = None
    elif ordinal in {0, 1, 6, 7, 8}:
        source_kind = "attempt"
        source_id = preceding_id
    else:
        source_kind = "attempt"
        source_id = attempt_id(iteration, 1)
    role = ATTEMPT_ROLES[ordinal]
    evidence = (
        _executed_evidence(accepted, role, terminal_policy, steps)
        if state == "executed"
        else {}
    )
    return {
        "attempt_id": attempt_id(iteration, ordinal),
        "ordinal": ordinal,
        "role": role,
        "inner_terminal_policy": terminal_policy,
        "slot_state": state,
        "solver_executed": state == "executed",
        "supplied_executed_action": controlling,
        "source_kind": source_kind,
        "source_attempt_id": source_id,
        "transformation": transformation,
        "scale": scale,
        "seed": seed,
        "assigned_start": evidence.get("assigned_start"),
        "solver_x0": evidence.get("solver_x0"),
        "solver_x0_layout": evidence.get("solver_x0_layout"),
        "solver_evidence": evidence.get("solver_evidence"),
        "result": evidence.get("result"),
        "audit": evidence.get("audit"),
        "structural_signature": evidence.get("structural_signature"),
    }


def _archive(
    *,
    iteration=0,
    initial=5.0,
    post=4.0,
    accepted_ordinal=0,
    preceding_id=None,
    terminal_policy="hard_equality",
):
    stop = min(iteration + AC_WINDOW_STEPS, HORIZON_STEPS)
    steps = stop - iteration
    attempts = []
    target_free_available = False
    accepted_seen = False
    for ordinal in range(9):
        if accepted_seen:
            state = "not_needed_after_acceptance"
        elif ordinal in {2, 3, 4, 5} and not target_free_available:
            state = "source_unavailable"
        else:
            state = "executed"
        accepted = ordinal == accepted_ordinal or (
            ordinal == 1 and accepted_ordinal in {2, 3, 4, 5}
        )
        attempts.append(
            _attempt(
                iteration,
                ordinal,
                accepted=accepted,
                controlling=ordinal == accepted_ordinal,
                state=state,
                preceding_id=preceding_id,
                terminal_policy=terminal_policy,
                steps=steps,
            )
        )
        if ordinal == 1 and state == "executed":
            target_free_available = attempts[-1]["audit"]["accepted_primal"]
        if ordinal == accepted_ordinal and ordinal != 1:
            accepted_seen = True
    executed = accepted_ordinal is not None
    controller_id = attempt_id(iteration, accepted_ordinal) if executed else None
    return {
        "schema_version": 1,
        "iteration": iteration,
        "interval_start": iteration,
        "interval_stop": stop,
        "storage_device_ids": ["battery"],
        "initial_soc_mwh": [initial],
        "target_soc_mwh": [5.0],
        "delta_hours": 1.0,
        "soc_tolerance_mwh": SOC_TOLERANCE_MWH,
        "preceding_controlling_attempt_id": preceding_id,
        "attempts": attempts,
        "executed_interval": {
            "controlling_attempt_id": controller_id,
            "b_mw": [initial - post],
        }
        if executed
        else None,
        "post_step_soc_mwh": [post] if executed else None,
    }


def _validate(archive):
    return validate_window_archive(
        archive,
        expected_soc_tolerance_mwh=SOC_TOLERANCE_MWH,
        expected_residual_tolerances=RESIDUAL_TOLERANCES,
        expected_inner_terminal_policy="hard_equality",
        expected_horizon_steps=HORIZON_STEPS,
        expected_ac_window_steps=AC_WINDOW_STEPS,
        expected_result_dimensions=RESULT_DIMENSIONS,
        expected_delta_hours=DELTA_HOURS,
        expected_outer_boundary_soc_mwh=OUTER_BOUNDARY_SOC_MWH,
    )


def _checkpoint(entries, *, initial=5.0, realized=4.0):
    return checkpoint_payload(
        source_fingerprint="source",
        scenario_hash="scenario",
        outer_plan_sha256="outer",
        policy_hash=POLICY_HASH,
        storage_device_ids=("battery",),
        initial_soc_mwh=(initial,),
        realized_soc_mwh=(realized,),
        entries=entries,
    )


def _load(path):
    return load_verified_checkpoint(
        path,
        expected_source_fingerprint="source",
        expected_scenario_hash="scenario",
        expected_outer_plan_sha256="outer",
        expected_policy_hash=POLICY_HASH,
        expected_soc_tolerance_mwh=SOC_TOLERANCE_MWH,
        expected_residual_tolerances=RESIDUAL_TOLERANCES,
        expected_inner_terminal_policy="hard_equality",
        expected_horizon_steps=HORIZON_STEPS,
        expected_ac_window_steps=AC_WINDOW_STEPS,
        expected_result_dimensions=RESULT_DIMENSIONS,
        expected_delta_hours=DELTA_HOURS,
        expected_outer_boundary_soc_mwh=OUTER_BOUNDARY_SOC_MWH,
    )


def test_complete_window_archive_accepts_no_live_build():
    archive = _archive()
    assert _validate(archive) is archive
    assert archive["attempts"][0]["attempt_id"] == (
        "ac-000-00-primary_controlling"
    )
    assert archive["attempts"][1]["source_kind"] is None
    archive["attempts"][0]["build"] = object()
    with pytest.raises(ValueError, match="must not retain a live build"):
        _validate(archive)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value["attempts"].pop(), "exactly nine"),
        (lambda value: value["attempts"][3].update({"ordinal": 4}), "ordinals"),
        (
            lambda value: value["attempts"][6].update(
                {"role": "perturbed_target_free"}
            ),
            "role",
        ),
        (
            lambda value: value.update({"post_step_soc_mwh": [float("nan")]}),
            "finite vector",
        ),
    ],
)
def test_window_archive_rejects_structural_drift(mutation, match):
    archive = _archive()
    mutation(archive)
    with pytest.raises(ValueError, match=match):
        _validate(archive)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("attempt_id", "wrong", "attempt ID"),
        ("transformation", "wrong", "transformation"),
        ("scale", 0.5, "scale"),
        ("seed", 1, "seed"),
        ("source_kind", "wrong", "source kind"),
    ],
)
def test_window_rejects_registry_provenance_drift(field, value, match):
    archive = _archive(accepted_ordinal=6)
    ordinal = 3 if field in {"scale", "seed"} else 0
    archive["attempts"][ordinal][field] = value
    with pytest.raises(ValueError, match=match):
        _validate(archive)


def test_window_binds_soc_tolerance_to_frozen_policy():
    archive = _archive()
    archive["soc_tolerance_mwh"] = 1e6
    with pytest.raises(ValueError, match="differs from frozen policy"):
        _validate(archive)


def test_window_enforces_three_hour_geometry_and_final_truncation():
    archive = _archive()
    archive["interval_stop"] = 2
    with pytest.raises(ValueError, match="frozen window geometry"):
        _validate(archive)

    archive = _archive()
    archive["interval_stop"] = 4
    with pytest.raises(ValueError, match="frozen window geometry"):
        _validate(archive)

    final = _archive(
        iteration=3,
        initial=4.0,
        post=3.0,
        preceding_id=attempt_id(2, 0),
    )
    assert final["interval_stop"] == 4
    assert _validate(final)


def test_window_binds_delta_to_frozen_hourly_configuration():
    archive = _archive()
    archive["delta_hours"] = 0.5
    archive["executed_interval"]["b_mw"] = [2.0]
    with pytest.raises(ValueError, match="delta_hours differs"):
        _validate(archive)


def test_window_target_must_match_id_aligned_outer_signpost():
    archive = _archive()
    archive["target_soc_mwh"] = [6.0]
    with pytest.raises(ValueError, match="differs from frozen outer plan"):
        _validate(archive)

    changed_targets = deepcopy(OUTER_BOUNDARY_SOC_MWH)
    changed_targets[3] = {"different-battery": 5.0}
    with pytest.raises(ValueError, match="storage identities"):
        validate_window_archive(
            _archive(),
            expected_soc_tolerance_mwh=SOC_TOLERANCE_MWH,
            expected_residual_tolerances=RESIDUAL_TOLERANCES,
            expected_inner_terminal_policy="hard_equality",
            expected_horizon_steps=HORIZON_STEPS,
            expected_ac_window_steps=AC_WINDOW_STEPS,
            expected_result_dimensions=RESULT_DIMENSIONS,
            expected_delta_hours=DELTA_HOURS,
            expected_outer_boundary_soc_mwh=changed_targets,
        )


def test_result_contract_rejects_missing_nonfinite_and_wrong_shape_fields():
    archive = _archive()
    archive["attempts"][0]["result"].pop("Qg")
    with pytest.raises(ValueError, match="missing required keys"):
        _validate(archive)

    archive = _archive()
    archive["attempts"][0]["result"]["Vm"][0][0] = float("nan")
    with pytest.raises(ValueError, match="Vm.*finite with shape"):
        _validate(archive)

    archive = _archive()
    archive["attempts"][0]["result"]["branch_p_from"].pop()
    with pytest.raises(ValueError, match="branch_p_from.*finite with shape"):
        _validate(archive)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda attempt: attempt.update({"assigned_start": None}), "assigned start"),
        (lambda attempt: attempt.update({"solver_x0": [9.0, 2.0, 0.5]}), "assigned start"),
        (
            lambda attempt: attempt["solver_evidence"].update(
                {"model_coordinate_count": 1}
            ),
            "coordinate counts",
        ),
        (
            lambda attempt: attempt["solver_evidence"].update(
                {"object_ids_after": {"variables": [9]}}
            ),
            "object identities",
        ),
        (
            lambda attempt: attempt.update({"structural_signature": None}),
            "structural signature",
        ),
        (lambda attempt: attempt.update({"audit": {}}), "acceptance evidence"),
        (lambda attempt: attempt.update({"result": {}}), "required keys"),
    ],
)
def test_executed_attempt_requires_complete_auditable_evidence(mutation, match):
    archive = _archive()
    mutation(archive["attempts"][0])
    with pytest.raises((TypeError, ValueError), match=match):
        _validate(archive)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda audit: audit.update({"status": "infeasible"}), "eligible"),
        (
            lambda audit: audit.update(
                {"missing_or_nonfinite_fields": ["Pg"]}
            ),
            "failed acceptance gates",
        ),
        (
            lambda audit: audit.update({"identity_error": "wrong storage order"}),
            "failed acceptance gates",
        ),
        (
            lambda audit: audit.update({"exception": "SolverError: failed"}),
            "requires solver_failure",
        ),
        (lambda audit: audit.pop("solver_num_iters"), "acceptance evidence"),
    ],
)
def test_controlling_audit_rejects_public_acceptance_invariant_drift(
    mutation, match
):
    archive = _archive()
    mutation(archive["attempts"][0]["audit"])
    with pytest.raises(ValueError, match=match):
        _validate(archive)


def test_unsuccessful_audit_rejects_contradictory_classification():
    archive = _archive(accepted_ordinal=6)
    audit = archive["attempts"][0]["audit"]
    audit.update({"status": "infeasible", "outcome": "unusable_primal"})
    with pytest.raises(ValueError, match="certified classification"):
        _validate(archive)

    archive = _archive(accepted_ordinal=6)
    audit = archive["attempts"][0]["audit"]
    audit.update({"outcome": "solver_failure", "exception": None})
    with pytest.raises(ValueError, match="requires an exception"):
        _validate(archive)


def test_residual_gate_rejects_missing_and_excessive_accepted_residuals():
    archive = _archive()
    residuals = archive["attempts"][0]["audit"]["residuals"]
    residuals.pop("voltage_bound_pu_abs")
    with pytest.raises(ValueError, match="missing required residuals"):
        _validate(archive)

    archive = _archive()
    archive["attempts"][0]["audit"]["residuals"][
        "ac_reactive_balance_pu_abs"
    ] = 2e-6
    with pytest.raises(ValueError, match="exceeds frozen residual tolerances"):
        _validate(archive)


def test_residual_gate_requires_accepted_outcome_when_every_gate_passes():
    archive = _archive(accepted_ordinal=6)
    audit = archive["attempts"][0]["audit"]
    audit.update({"status": "optimal", "outcome": "unusable_primal"})
    with pytest.raises(ValueError, match="does not match accepted residual gates"):
        _validate(archive)


def test_target_free_residual_set_omits_terminal_residual():
    assert _validate(_archive(accepted_ordinal=2))


def test_experiment_rejects_soft_terminal_policy(tmp_path):
    soft = _archive(terminal_policy="quadratic_soft")
    with pytest.raises(ValueError, match="differs from frozen policy"):
        _validate(soft)

    with pytest.raises(ValueError, match="requires hard_equality"):
        validate_window_archive(
            _archive(),
            expected_soc_tolerance_mwh=SOC_TOLERANCE_MWH,
            expected_residual_tolerances=RESIDUAL_TOLERANCES,
            expected_inner_terminal_policy="quadratic_soft",
            expected_horizon_steps=HORIZON_STEPS,
            expected_ac_window_steps=AC_WINDOW_STEPS,
            expected_result_dimensions=RESULT_DIMENSIONS,
            expected_delta_hours=DELTA_HOURS,
            expected_outer_boundary_soc_mwh=OUTER_BOUNDARY_SOC_MWH,
        )

    first = atomic_gzip_json(tmp_path / "window_000000.json.gz", _archive())
    second = atomic_gzip_json(
        tmp_path / "window_000001.json.gz",
        _archive(
            iteration=1,
            initial=4.0,
            post=3.0,
            preceding_id=attempt_id(0, 0),
            terminal_policy="quadratic_soft",
        ),
    )
    checkpoint_path = tmp_path / "checkpoint.json"
    atomic_json(
        checkpoint_path,
        _checkpoint((first, second), realized=3.0),
    )
    with pytest.raises(ValueError, match="differs from frozen policy"):
        _load(checkpoint_path)


def test_window_enforces_source_availability_and_stop_after_acceptance():
    archive = _archive(accepted_ordinal=2)
    archive["attempts"][2]["source_attempt_id"] = None
    with pytest.raises(ValueError, match="source ID"):
        _validate(archive)

    archive = _archive()
    archive["attempts"][1]["slot_state"] = "executed"
    archive["attempts"][1]["solver_executed"] = True
    with pytest.raises(ValueError, match="frozen lifecycle"):
        _validate(archive)


@pytest.mark.parametrize("field", ["audit", "result"])
def test_unexecuted_target_free_cannot_fabricate_accepted_source(field):
    archive = _archive(accepted_ordinal=6)
    target_free = archive["attempts"][1]
    target_free["slot_state"] = "construction_error"
    target_free["solver_executed"] = False
    target_free["audit"] = None
    target_free["result"] = None
    target_free[field] = (
        {"accepted_primal": True} if field == "audit" else {"soc": [5.0]}
    )
    for ordinal in range(2, 6):
        archive["attempts"][ordinal] = _attempt(
            0, ordinal, state="source_unavailable"
        )
    with pytest.raises(ValueError, match="unexecuted slot"):
        _validate(archive)

    archive = _archive(accepted_ordinal=None)
    archive["attempts"][8]["slot_state"] = "not_needed_after_acceptance"
    archive["attempts"][8]["solver_executed"] = False
    with pytest.raises(ValueError, match="frozen lifecycle"):
        _validate(archive)


def test_atomic_archive_and_checkpoint_round_trip(tmp_path):
    window_path = tmp_path / "window_000000.json.gz"
    entry = atomic_gzip_json(window_path, _archive())
    checkpoint = _checkpoint((entry,))
    checkpoint_path = tmp_path / "checkpoint.json"
    atomic_json(checkpoint_path, checkpoint)

    loaded = _load(checkpoint_path)
    assert loaded["next_iteration"] == 1
    assert loaded["realized_soc_mwh"] == [4.0]
    assert json.loads(checkpoint_path.read_text()) == checkpoint


def test_resume_rejects_hash_drift_without_reblessing(tmp_path):
    window_path = tmp_path / "window_000000.json.gz"
    entry = atomic_gzip_json(window_path, _archive())
    checkpoint = _checkpoint((entry,))
    checkpoint_path = tmp_path / "checkpoint.json"
    atomic_json(checkpoint_path, checkpoint)
    with gzip.open(window_path, "wt", encoding="utf-8") as stream:
        json.dump(_archive(accepted_ordinal=None), stream)

    with pytest.raises(ValueError, match="byte count mismatch|hash mismatch"):
        _load(checkpoint_path)
    assert json.loads(checkpoint_path.read_text()) == checkpoint


def test_checkpoint_rejects_gaps_and_policy_mismatch(tmp_path):
    base = _checkpoint((), initial=4.0, realized=4.0)
    changed = deepcopy(base)
    changed["next_iteration"] = 1
    with pytest.raises(ValueError, match="next_iteration"):
        validate_checkpoint(changed)

    path = tmp_path / "checkpoint.json"
    atomic_json(path, base)
    with pytest.raises(ValueError, match="policy_hash mismatch"):
        load_verified_checkpoint(
            path,
            expected_source_fingerprint="source",
            expected_scenario_hash="scenario",
            expected_outer_plan_sha256="outer",
            expected_policy_hash="different",
            expected_soc_tolerance_mwh=SOC_TOLERANCE_MWH,
            expected_residual_tolerances=RESIDUAL_TOLERANCES,
            expected_inner_terminal_policy="hard_equality",
            expected_horizon_steps=HORIZON_STEPS,
            expected_ac_window_steps=AC_WINDOW_STEPS,
            expected_result_dimensions=RESULT_DIMENSIONS,
            expected_delta_hours=DELTA_HOURS,
            expected_outer_boundary_soc_mwh=OUTER_BOUNDARY_SOC_MWH,
        )


def test_window_rejects_target_free_or_mismatched_controller():
    archive = _archive(accepted_ordinal=2)
    archive["attempts"][2]["supplied_executed_action"] = False
    archive["attempts"][1]["supplied_executed_action"] = True
    with pytest.raises(ValueError, match="target-free"):
        _validate(archive)

    archive = _archive()
    archive["executed_interval"]["controlling_attempt_id"] = "wrong"
    with pytest.raises(ValueError, match="controlling attempt mismatch"):
        _validate(archive)


def test_resume_rejects_state_or_identity_discontinuity(tmp_path):
    first = _archive()
    first_entry = atomic_gzip_json(tmp_path / "window_000000.json.gz", first)
    preceding = attempt_id(0, 0)
    second = _archive(
        iteration=1,
        initial=4.5,
        post=3.5,
        preceding_id=preceding,
    )
    second_entry = atomic_gzip_json(tmp_path / "window_000001.json.gz", second)
    checkpoint = _checkpoint((first_entry, second_entry), realized=3.5)
    checkpoint_path = tmp_path / "checkpoint.json"
    atomic_json(checkpoint_path, checkpoint)
    with pytest.raises(ValueError, match="state chain"):
        _load(checkpoint_path)

    second["initial_soc_mwh"] = [4.0]
    second["post_step_soc_mwh"] = [3.0]
    second["executed_interval"]["b_mw"] = [1.0]
    second["storage_device_ids"] = ["different"]
    second_entry = atomic_gzip_json(tmp_path / "window_000001.json.gz", second)
    checkpoint["windows"][1] = second_entry.__dict__
    checkpoint["realized_soc_mwh"] = [3.0]
    atomic_json(checkpoint_path, checkpoint)
    with pytest.raises(ValueError, match="identity mismatch|storage identities"):
        _load(checkpoint_path)


def test_resume_rejects_stale_preceding_controller_identity(tmp_path):
    first = _archive()
    first_entry = atomic_gzip_json(tmp_path / "window_000000.json.gz", first)
    second = _archive(
        iteration=1,
        initial=4.0,
        post=3.0,
        preceding_id="ac-999-00-primary_controlling",
    )
    second_entry = atomic_gzip_json(tmp_path / "window_000001.json.gz", second)
    checkpoint = _checkpoint((first_entry, second_entry), realized=3.0)
    checkpoint_path = tmp_path / "checkpoint.json"
    atomic_json(checkpoint_path, checkpoint)

    with pytest.raises(ValueError, match="controller identity chain"):
        _load(checkpoint_path)


@pytest.mark.parametrize("relative_path", ["../outside.json.gz", "/tmp/x", ""])
def test_checkpoint_rejects_escaping_or_empty_paths(relative_path):
    checkpoint = {
        "schema_version": 1,
        "source_fingerprint": "source",
        "scenario_hash": "scenario",
        "outer_plan_sha256": "outer",
        "policy_hash": POLICY_HASH,
        "storage_device_ids": ["battery"],
        "initial_soc_mwh": [5.0],
        "realized_soc_mwh": [4.0],
        "completed_intervals": 1,
        "next_iteration": 1,
        "windows": [
            {
                "iteration": 0,
                "relative_path": relative_path,
                "bytes": 1,
                "sha256": "0" * 64,
            }
        ],
    }
    with pytest.raises(ValueError, match="relative_path"):
        validate_checkpoint(checkpoint)
