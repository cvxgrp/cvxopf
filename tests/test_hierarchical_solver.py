"""Focused tests for the M17-S5 public orchestration."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import (
    HierarchicalInputs,
    HierarchicalPolicy,
    HierarchicalSolveAudit,
    HierarchicalSolveConfig,
    Load,
    OPFBuild,
    StorageUnitIdeal,
    build_opf,
    gen_from_matpower,
    solve_hierarchical_opf,
)
from cvxopf._hierarchical_solver import _execution_snapshot
from cvxopf import _hierarchical_solver
from cvxopf.testcases import case9
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT


def _inputs(*, horizon_steps: int = 2) -> HierarchicalInputs:
    case = case9()
    loads = tuple(
        Load(
            bus=int(row[0]),
            p_load_mw=float(row[2]),
            q_load_mvar=float(row[3]),
            device_id=f"load-{int(row[0])}",
        )
        for row in case["bus"]
    )
    columns = [unit.device_id for unit in loads]
    p = pd.DataFrame(
        [[unit.p_load_mw for unit in loads]] * horizon_steps,
        columns=columns,
    )
    q = pd.DataFrame(
        [[unit.q_load_mvar for unit in loads]] * horizon_steps,
        columns=columns,
    )
    return HierarchicalInputs(
        case=case,
        horizon_steps=horizon_steps,
        delta=1.0,
        generators=tuple(gen_from_matpower(case["gen"], case["gencost"])),
        loads=loads,
        storage=(
            StorageUnitIdeal(
                bus=7,
                apparent_power_rating=125.0,
                capacity=1_000.0,
                initial_soc=500.0,
                terminal_soc=500.0,
                terminal_constraint="equality",
                device_id="battery-7",
            ),
        ),
        df_load_p=p,
        df_load_q=q,
    )


def _named_build(*variables: cp.Variable) -> OPFBuild:
    objective = cp.sum([cp.sum_squares(variable) for variable in variables])
    return OPFBuild(cp.Problem(cp.Minimize(objective)), {}, {}, "ac", True)


def test_execution_snapshot_isolates_all_mutable_physical_inputs():
    inputs = _inputs()
    snapshot = _execution_snapshot(inputs)

    inputs.df_load_p.iloc[0, 0] = 999.0
    inputs.loads[0].p_load_mw = 999.0
    inputs.storage[0].initial_soc = 100.0
    inputs.options.loss_weight = 42.0

    assert snapshot.df_load_p.iloc[0, 0] != 999.0
    assert snapshot.loads[0].p_load_mw != 999.0
    assert snapshot.storage[0].initial_soc == 500.0
    assert snapshot.options.loss_weight != 42.0


def test_private_alignment_and_window_bounds_fail_clearly():
    snapshot = _execution_snapshot(_inputs(horizon_steps=1))
    with pytest.raises(ValueError, match="storage identity"):
        _hierarchical_solver._aligned({}, snapshot.storage_device_ids, "state")
    with pytest.raises(ValueError, match="invalid half-open interval"):
        _hierarchical_solver._build_window(
            snapshot, "ac", 0, 2, snapshot.storage
        )


def test_finite_field_and_storage_identity_diagnostics_are_total():
    missing = _hierarchical_solver._finite_fields(
        {"bad": object(), "nan": np.nan}, ("bad", "nan", "absent")
    )
    assert missing == ("bad", "nan", "absent")

    snapshot = _execution_snapshot(_inputs(horizon_steps=1))
    build = _hierarchical_solver._build_window(
        snapshot, "ac", 0, 1, snapshot.storage
    )
    common = {
        "storage_device_ids": np.array(["battery-7"]),
        "storage_device_id_is_explicit": np.array([True]),
    }
    assert _hierarchical_solver._identity_error(snapshot, build, common) is None
    implicit = {**common, "storage_device_id_is_explicit": np.array([False])}
    assert "build-local" in _hierarchical_solver._identity_error(
        snapshot, build, implicit
    )
    reordered = {**common, "storage_device_ids": np.array(["other"])}
    assert "ordering differs" in _hierarchical_solver._identity_error(
        snapshot, build, reordered
    )


def test_named_start_assignment_rejects_ambiguous_or_misaligned_values():
    duplicate = _named_build(cp.Variable(name="x"), cp.Variable(name="x"))
    with pytest.raises(ValueError, match="unique variable names"):
        _hierarchical_solver._variables_by_name(duplicate)

    build = _named_build(cp.Variable(2, name="x"))
    with pytest.raises(ValueError, match="namespace"):
        _hierarchical_solver._assign_start(build, {"y": np.zeros(2)})
    with pytest.raises(ValueError, match="shape mismatch"):
        _hierarchical_solver._assign_start(build, {"x": np.zeros((1, 2))})


def test_shift_and_perturbation_initialization_validate_source_structure():
    snapshot = _execution_snapshot(_inputs(horizon_steps=1))
    policy = HierarchicalPolicy(
        ac_window_steps=1, initialization_policy="flat_only"
    )
    state = {"battery-7": 500.0}

    with pytest.raises(ValueError, match="lacks variable x"):
        _hierarchical_solver._shifted_start(
            {}, _named_build(cp.Variable(name="x")), snapshot, policy, state
        )
    with pytest.raises(ValueError, match="lacks family Pg"):
        _hierarchical_solver._shifted_start(
            {"Qg_1": np.zeros(1)},
            _named_build(cp.Variable(1, name="Pg_0")),
            snapshot,
            policy,
            state,
        )
    with pytest.raises(ValueError, match="shape mismatch"):
        _hierarchical_solver._shifted_start(
            {"Pg_1": np.zeros(2)},
            _named_build(cp.Variable(1, name="Pg_0")),
            snapshot,
            policy,
            state,
        )
    with pytest.raises(ValueError, match="SoC steps are not consecutive"):
        _hierarchical_solver._shifted_start(
            {},
            _named_build(cp.Variable(1, name="soc_1")),
            snapshot,
            policy,
            state,
        )
    with pytest.raises(ValueError, match="lacks b_0"):
        _hierarchical_solver._shifted_start(
            {},
            _named_build(cp.Variable(1, name="soc_0")),
            snapshot,
            policy,
            state,
        )
    with pytest.raises(ValueError, match="center does not match"):
        _hierarchical_solver._perturbed_start(
            {}, _named_build(cp.Variable(name="x")), scale=1e-3, seed=1
        )


def test_network_limit_diagnostics_handle_an_unrated_network():
    snapshot = _execution_snapshot(_inputs(horizon_steps=1))
    snapshot.case["branch"][:, 5] = 0.0
    result = {
        "Vm": np.ones((1, 9)),
        "branch_s_from": np.zeros((1, 9)),
        "branch_s_to": np.zeros((1, 9)),
    }
    assert _hierarchical_solver._network_limit_residuals(snapshot, result) == (
        0.0,
        0.0,
        0.0,
    )


def test_outer_audit_accepts_mixed_terminal_policy_fleet():
    inputs = _inputs(horizon_steps=1)
    storage = (
        inputs.storage[0],
        StorageUnitIdeal(
            bus=5,
            apparent_power_rating=50.0,
            capacity=200.0,
            initial_soc=100.0,
            device_id="battery-5",
        ),
    )
    mixed = replace(inputs, storage=storage)
    snapshot = _execution_snapshot(mixed)

    outer = _hierarchical_solver._solve_outer(
        snapshot,
        HierarchicalPolicy(
            ac_window_steps=1, initialization_policy="flat_only"
        ),
        HierarchicalSolveConfig(),
        0,
        {"battery-7": 500.0, "battery-5": 100.0},
    )

    assert outer.audit.accepted_primal
    assert outer.terminal_modes == {
        "battery-7": "equality",
        "battery-5": "none",
    }
    assert outer.boundary_soc_mwh.shape == (2, 2)


@pytest.mark.filterwarnings(
    "ignore:Loading 'cvxopt' into a process that has already imported 'cyipopt'"
)
def test_flat_only_executes_one_interval_with_verified_ipopt_start():
    result = solve_hierarchical_opf(
        _inputs(horizon_steps=1),
        HierarchicalPolicy(
            ac_window_steps=1,
            outer_policy="frozen",
            initialization_policy="flat_only",
        ),
    )

    assert result.completed
    assert result.completed_intervals == 1
    assert len(result.outer_plans) == 1
    assert len(result.ac_attempts) == 1
    attempt = result.ac_attempts[0]
    assert attempt.audit is not None and attempt.audit.accepted_primal
    assert attempt.supplied_executed_action
    assert attempt.solver_evidence is not None
    assert attempt.solver_evidence.model_coordinate_count == sum(
        value.size for value in attempt.assigned_start.values()
    )
    assert result.realized_soc_mwh.shape == (2, 1)
    assert result.executed_b_mw.shape == (1, 1)
    assert np.allclose(
        result.realized_soc_mwh[1],
        result.realized_soc_mwh[0] - result.executed_b_mw[0],
    )
    assert result.provenance.software_versions["clarabel"] != "unknown"
    assert result.provenance.software_versions["ipopt"] != "unknown"


@pytest.mark.filterwarnings(
    "ignore:Loading 'cvxopt' into a process that has already imported 'cyipopt'"
)
def test_quadratic_soft_policy_audits_modeled_terminal_cost():
    result = solve_hierarchical_opf(
        _inputs(horizon_steps=1),
        HierarchicalPolicy(
            ac_window_steps=1,
            inner_terminal_policy="quadratic_soft",
            quadratic_soft_weight=1.0,
            initialization_policy="flat_only",
        ),
    )

    attempt = result.ac_attempts[0]
    assert result.completed
    assert attempt.audit is not None and attempt.audit.accepted_primal
    assert attempt.terminal_deviation_mwh is not None
    assert attempt.audit.residuals["soft_terminal_cost_abs"] <= 1e-12
    assert "terminal_soc_mwh_abs" not in attempt.audit.residuals


@pytest.mark.filterwarnings(
    "ignore:Loading 'cvxopt' into a process that has already imported 'cyipopt'"
)
def test_hierarchical_x0_capture_is_isolated_from_ordinary_ac_solve(monkeypatch):
    ordinary = build_opf(case9(), formulation="ac")
    original = IPOPT.solve_via_data
    rendezvous = Barrier(2)
    solver_lock = Lock()
    receiver_types = []

    def synchronized_solve(self, *args, **kwargs):
        receiver_types.append(type(self))
        rendezvous.wait(timeout=10.0)
        with solver_lock:
            return original(self, *args, **kwargs)

    monkeypatch.setattr(IPOPT, "solve_via_data", synchronized_solve)

    def run_hierarchy():
        return solve_hierarchical_opf(
            _inputs(horizon_steps=1),
            HierarchicalPolicy(
                ac_window_steps=1, initialization_policy="flat_only"
            ),
        )

    def run_ordinary():
        ordinary.solve()
        return ordinary.prob.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        hierarchical_future = executor.submit(run_hierarchy)
        ordinary_future = executor.submit(run_ordinary)
        hierarchical = hierarchical_future.result(timeout=30.0)
        ordinary_status = ordinary_future.result(timeout=30.0)

    assert hierarchical.completed
    assert ordinary_status == "optimal"
    assert len(receiver_types) == 2
    assert IPOPT in receiver_types
    assert any(receiver is not IPOPT for receiver in receiver_types)


@pytest.mark.filterwarnings(
    "ignore:Loading 'cvxopt' into a process that has already imported 'cyipopt'"
)
def test_shifted_recovery_registers_nine_slots_and_uses_preceding_attempt():
    inputs = _inputs(horizon_steps=2)
    result = solve_hierarchical_opf(
        inputs,
        HierarchicalPolicy(
            ac_window_steps=1,
            outer_policy="replan_every_step",
            initialization_policy="shifted_with_recovery",
        ),
    )

    assert result.completed
    assert len(result.outer_plans) == 2
    assert len(result.ac_attempts) == 18
    first, second = result.ac_attempts[:9], result.ac_attempts[9:]
    assert [attempt.ordinal for attempt in first] == list(range(9))
    assert [attempt.ordinal for attempt in second] == list(range(9))
    assert first[0].source_kind == "generated_flat"
    assert second[0].source_kind == "attempt"
    assert second[0].source_attempt_id == first[0].attempt_id
    assert second[0].transformation == "shifted_preceding"
    assert all(
        attempt.slot_state == "not_needed_after_acceptance"
        for attempt in first[1:] + second[1:]
    )


@pytest.mark.filterwarnings(
    "ignore:Loading 'cvxopt' into a process that has already imported 'cyipopt'"
)
def test_shifted_recovery_uses_target_free_only_as_initialization(monkeypatch):
    original = _hierarchical_solver._solve_ac_with_verified_x0
    calls = 0

    def fail_primary_once(build, solve_config):
        nonlocal calls
        calls += 1
        run = original(build, solve_config)
        if calls == 1:
            return replace(run, exception="SolverError: forced primary failure")
        return run

    monkeypatch.setattr(
        _hierarchical_solver, "_solve_ac_with_verified_x0", fail_primary_once
    )
    result = solve_hierarchical_opf(
        _inputs(horizon_steps=1),
        HierarchicalPolicy(
            ac_window_steps=1,
            outer_policy="replan_every_step",
            initialization_policy="shifted_with_recovery",
        ),
    )

    assert result.completed
    primary, target_free, copied, *unused = result.ac_attempts
    assert primary.audit is not None
    assert primary.audit.outcome == "solver_failure"
    assert target_free.audit is not None and target_free.audit.accepted_primal
    assert not target_free.supplied_executed_action
    assert copied.audit is not None and copied.audit.accepted_primal
    assert copied.supplied_executed_action
    assert result.executed_intervals[0].controlling_attempt_id == copied.attempt_id
    assert all(
        attempt.slot_state == "not_needed_after_acceptance"
        for attempt in unused
    )


@pytest.mark.filterwarnings(
    "ignore:Loading 'cvxopt' into a process that has already imported 'cyipopt'"
)
def test_construction_errors_retain_reached_payload_and_provenance(monkeypatch):
    original_solve = _hierarchical_solver._solve_ac_with_verified_x0
    solve_calls = 0

    def fail_primary_and_copied_construction(build, solve_config):
        nonlocal solve_calls
        solve_calls += 1
        if solve_calls == 3:
            raise RuntimeError("forced copied-start construction failure")
        run = original_solve(build, solve_config)
        if solve_calls == 1:
            return replace(run, exception="SolverError: forced primary failure")
        return run

    def fail_perturbation(*args, **kwargs):
        raise RuntimeError("forced perturbation construction failure")

    monkeypatch.setattr(
        _hierarchical_solver,
        "_solve_ac_with_verified_x0",
        fail_primary_and_copied_construction,
    )
    monkeypatch.setattr(
        _hierarchical_solver, "_perturbed_start", fail_perturbation
    )
    result = solve_hierarchical_opf(
        _inputs(horizon_steps=1),
        HierarchicalPolicy(
            ac_window_steps=1,
            initialization_policy="shifted_with_recovery",
        ),
    )

    primary, target_free, copied, target_perturb, *_, causal_perturb = (
        result.ac_attempts
    )
    assert primary.audit is not None and primary.audit.outcome == "solver_failure"
    assert target_free.audit is not None and target_free.audit.accepted_primal
    assert copied.slot_state == "construction_error"
    assert copied.build is not None
    assert copied.raw_start is not None
    assert copied.assigned_start is not None
    assert copied.source_kind == "attempt"
    assert copied.source_attempt_id == target_free.attempt_id
    assert target_perturb.slot_state == "construction_error"
    assert target_perturb.source_kind == "attempt"
    assert target_perturb.source_attempt_id == target_free.attempt_id
    assert causal_perturb.slot_state == "construction_error"
    assert causal_perturb.source_kind == "generated_flat"
    assert causal_perturb.source_attempt_id is None


@pytest.mark.filterwarnings(
    "ignore:Loading 'cvxopt' into a process that has already imported 'cyipopt'"
)
def test_recovery_exhaustion_retains_all_slots_without_advancing(monkeypatch):
    original = _hierarchical_solver._solve_ac_with_verified_x0

    def fail_every_attempt(build, solve_config):
        return replace(
            original(build, solve_config),
            exception="SolverError: forced recovery failure",
        )

    monkeypatch.setattr(
        _hierarchical_solver, "_solve_ac_with_verified_x0", fail_every_attempt
    )
    result = solve_hierarchical_opf(
        _inputs(horizon_steps=1),
        HierarchicalPolicy(
            ac_window_steps=1,
            initialization_policy="shifted_with_recovery",
        ),
    )

    assert not result.completed
    assert result.completed_intervals == 0
    assert result.termination_iteration == 0
    assert result.termination_reason.startswith("ac_recovery_exhausted")
    assert len(result.ac_attempts) == 9
    assert not any(
        attempt.supplied_executed_action for attempt in result.ac_attempts
    )
    assert result.realized_soc_mwh.shape == (1, 1)
    assert result.executed_b_mw.shape == (0, 1)


def test_outer_failure_terminates_before_ac_registration(monkeypatch):
    original = _hierarchical_solver._solve_outer

    def fail_outer(*args, **kwargs):
        record = original(*args, **kwargs)
        failed_audit = HierarchicalSolveAudit(
            status="infeasible",
            outcome="solver_certified_infeasible",
            accepted_primal=False,
            missing_or_nonfinite_fields=(),
            identity_error=None,
            residuals={},
            exception=None,
            wall_time_seconds=record.audit.wall_time_seconds,
            solver_num_iters=record.audit.solver_num_iters,
            solver_setup_time_seconds=record.audit.solver_setup_time_seconds,
            solver_solve_time_seconds=record.audit.solver_solve_time_seconds,
        )
        return replace(record, boundary_soc_mwh=None, audit=failed_audit)

    monkeypatch.setattr(_hierarchical_solver, "_solve_outer", fail_outer)
    result = solve_hierarchical_opf(
        _inputs(horizon_steps=1),
        HierarchicalPolicy(
            ac_window_steps=1, initialization_policy="flat_only"
        ),
    )

    assert not result.completed
    assert result.termination_reason == "outer_solver_certified_infeasible"
    assert len(result.outer_plans) == 1
    assert result.ac_attempts == ()


def test_public_entry_point_rejects_noncontract_arguments():
    inputs = _inputs(horizon_steps=1)
    policy = HierarchicalPolicy(
        ac_window_steps=1, initialization_policy="flat_only"
    )

    with pytest.raises(TypeError, match="inputs must be"):
        solve_hierarchical_opf(object(), policy)
    with pytest.raises(TypeError, match="policy must be"):
        solve_hierarchical_opf(inputs, object())
    with pytest.raises(TypeError, match="solve_config must be"):
        solve_hierarchical_opf(inputs, policy, object())
