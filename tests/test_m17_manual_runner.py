"""Focused contract tests for the M17-S2 manual reference runner."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from experiments.hierarchical_battery_resilience import manual_runner as runner
from experiments.hierarchical_battery_resilience.scenario import (
    load_frozen_scenario,
)


def _audit(*, accepted=True, outcome=None, wall_time_seconds=0.0):
    return runner.SolveAudit(
        status="optimal" if accepted else "solver_error",
        outcome=outcome or ("accepted" if accepted else "solver_failure"),
        accepted_primal=accepted,
        missing_or_nonfinite_fields=(),
        identity_error=None,
        residuals={},
        exception=None if accepted else "SolverError: failed",
        wall_time_seconds=wall_time_seconds,
        solver_num_iters=None,
        solver_setup_time_seconds=None,
        solver_solve_time_seconds=None,
    )


def test_normative_endpoint_cases_are_the_reviewed_equal_length_pair():
    assert runner.FROZEN_ENDPOINT_CASES == (
        runner.EndpointCase("crosses_saturation_boundary_32_50", 32, 50),
        runner.EndpointCase("within_regime_60_78", 60, 78),
    )
    assert {
        case.stop - case.start for case in runner.FROZEN_ENDPOINT_CASES
    } == {18}


def _short_scenario(horizon=3, window=2):
    scenario = load_frozen_scenario()
    control = replace(
        scenario.control,
        horizon_steps=horizon,
        nominal_ac_window_steps=window,
        global_terminal_boundary=horizon,
    )
    return replace(
        scenario,
        control=control,
        df_load_p=scenario.df_load_p.iloc[:horizon],
        df_load_q=scenario.df_load_q.iloc[:horizon],
        df_nd=scenario.df_nd.iloc[:horizon],
    )


def _outer_record(scenario, created_iteration, initial_soc):
    storage_ids = tuple(unit.device_id for unit in scenario.storage)
    boundaries = np.arange(
        created_iteration, scenario.control.horizon_steps + 1, dtype=float
    ).reshape(-1, 1)
    boundaries += float(next(iter(initial_soc.values())))
    local = np.arange(len(boundaries))
    return runner.OuterPlanRecord(
        outer_plan_id=f"outer-{created_iteration:03d}",
        created_iteration=created_iteration,
        global_interval_start=created_iteration,
        global_interval_stop=scenario.control.horizon_steps,
        local_boundary_indices=local,
        global_boundary_indices=created_iteration + local,
        storage_device_ids=storage_ids,
        boundary_soc_mwh=boundaries,
        build=SimpleNamespace(),
        results={},
        audit=_audit(),
    )


def _ac_record(
    scenario,
    *,
    attempt_id,
    attempt_kind,
    iteration,
    stop,
    outer_plan,
    outer_local_boundary,
    realized_soc,
    target_soc,
    policy,
    solve_kwargs=None,
    accepted=True,
):
    storage_ids = tuple(unit.device_id for unit in scenario.storage)
    initial = np.array([realized_soc[value] for value in storage_ids])
    first_b = np.full(len(storage_ids), 1.0)
    first_soc = initial - scenario.control.delta_hours * first_b
    return runner.ACAttemptRecord(
        attempt_id=attempt_id,
        attempt_kind=attempt_kind,
        iteration=iteration,
        interval_start=iteration,
        interval_stop=stop,
        effective_window_steps=stop - iteration,
        outer_plan_id=outer_plan.outer_plan_id,
        outer_local_boundary=outer_local_boundary,
        outer_global_boundary=stop,
        terminal_policy=policy,
        storage_device_ids=storage_ids,
        initial_soc_mwh=dict(realized_soc),
        target_soc_mwh=None if target_soc is None else dict(target_soc),
        terminal_deviation_mwh=None,
        window_diagnosis="hard_target_met" if accepted else "unresolved_failure",
        build=SimpleNamespace(),
        results={
            "b": first_b.reshape(1, -1),
            "soc": first_soc.reshape(1, -1),
            "Pg": np.zeros((1, len(scenario.generators))),
            "curtailment": np.zeros((1, len(scenario.nondispatchable))),
            "branch_p_from": np.zeros((1, len(scenario.case["branch"]))),
            "branch_p_to": np.zeros((1, len(scenario.case["branch"]))),
            "branch_s_from": np.zeros((1, len(scenario.case["branch"]))),
            "branch_s_to": np.zeros((1, len(scenario.case["branch"]))),
            "Vm": np.ones((1, len(scenario.case["bus"]))),
            "p_net": np.zeros((1, len(scenario.case["bus"]))),
        },
        audit=_audit(accepted=accepted),
    )


def test_storage_alignment_rejects_missing_extra_and_reordered_identity():
    scenario = load_frozen_scenario()
    storage_ids = runner._storage_id_order(scenario.storage)
    values = {storage_ids[0]: 12.0}
    np.testing.assert_array_equal(
        runner._aligned_values(values, storage_ids, "state"), [12.0]
    )
    with pytest.raises(ValueError, match="storage IDs do not match"):
        runner._aligned_values({"wrong": 12.0}, storage_ids, "state")

    unit = scenario.storage[0]
    with pytest.raises(ValueError, match="nonempty"):
        runner._storage_id_order((replace(unit, device_id=""),))
    with pytest.raises(ValueError, match="unique"):
        runner._storage_id_order((unit, unit))


def test_identity_audit_rejects_result_order_different_from_frozen_fleet():
    scenario = load_frozen_scenario()
    first = scenario.storage[0]
    second = replace(first, device_id="battery_bus_8", bus=8)
    scenario = replace(scenario, storage=(first, second))
    build = SimpleNamespace(
        data={"storage_device_ids": ["battery_bus_8", "battery_bus_7"]}
    )
    results = {
        "storage_device_ids": np.array(
            ["battery_bus_8", "battery_bus_7"], dtype=object
        ),
        "storage_device_id_is_explicit": np.array([True, True]),
    }

    assert runner._identity_error(scenario, build, results) == (
        "storage result order differs from the frozen fleet order"
    )


def test_outer_target_uses_local_boundary_and_retains_global_mapping():
    scenario = _short_scenario()
    outer = _outer_record(scenario, 1, {"battery_bus_7": 10.0})

    assert outer.global_boundary_indices.tolist() == [1, 2, 3]
    assert runner._outer_target(outer, 2) == {"battery_bus_7": 13.0}
    with pytest.raises(IndexError, match="local boundary"):
        runner._outer_target(outer, 3)


@pytest.mark.parametrize(
    ("outer_policy", "expected_local_boundaries", "expected_outer_count"),
    [
        ("frozen", [2, 3, 3], 1),
        ("replan_every_step", [2, 2, 1], 3),
    ],
)
def test_sequential_indexing_truncation_and_first_action_execution(
    monkeypatch, outer_policy, expected_local_boundaries, expected_outer_count
):
    scenario = _short_scenario()
    observed = []

    monkeypatch.setattr(runner, "load_frozen_scenario", lambda: scenario)
    monkeypatch.setattr(
        runner,
        "_solve_outer_plan",
        lambda scenario, created_iteration, realized_soc, solve_kwargs: (
            _outer_record(scenario, created_iteration, realized_soc)
        ),
    )

    def solve_ac(scenario, **kwargs):
        observed.append(
            (
                kwargs["iteration"],
                kwargs["stop"],
                kwargs["outer_local_boundary"],
                dict(kwargs["target_soc"]),
            )
        )
        return _ac_record(scenario, **kwargs)

    monkeypatch.setattr(runner, "_solve_ac_attempt", solve_ac)
    result = runner.run_sequential_execution(outer_policy, "hard_equality")

    assert result.completed
    assert result.completed_intervals == 3
    assert result.completion_fraction == 1.0
    assert len(result.outer_plans) == expected_outer_count
    assert [attempt.effective_window_steps for attempt in result.ac_attempts] == [2, 2, 1]
    assert [value[2] for value in observed] == expected_local_boundaries
    assert [value[1] for value in observed] == [2, 3, 3]
    np.testing.assert_allclose(result.executed_b_mw, 1.0)
    np.testing.assert_allclose(result.realized_soc_mwh[:, 0], [500, 499, 498, 497])
    assert len(result.executed_intervals) == 3
    assert result.trajectory_summary["renewable_curtailment_mwh"] == 0.0
    assert result.trajectory_summary["maximum_voltage_violation_pu"] == 0.0
    assert result.trajectory_summary["maximum_thermal_residual_mva"] == 0.0
    assert result.trajectory_summary[
        "maximum_normalized_squared_thermal_residual"
    ] == 0.0
    assert result.trajectory_summary[
        "cumulative_absolute_signpost_deviation_mwh"
    ] == 0.0
    assert result.trajectory_summary["runtime_seconds"] == 0.0


def test_failed_controlling_attempt_is_diagnosed_but_never_executed(monkeypatch):
    scenario = _short_scenario()
    calls = []
    monkeypatch.setattr(runner, "load_frozen_scenario", lambda: scenario)
    monkeypatch.setattr(
        runner,
        "_solve_outer_plan",
        lambda scenario, created_iteration, realized_soc, solve_kwargs: (
            _outer_record(scenario, created_iteration, realized_soc)
        ),
    )

    def failed_ac(scenario, **kwargs):
        calls.append(kwargs["attempt_kind"])
        return _ac_record(scenario, accepted=False, **kwargs)

    monkeypatch.setattr(runner, "_solve_ac_attempt", failed_ac)
    monkeypatch.setattr(
        runner,
        "_diagnose_failed_window",
        lambda scenario, controlling, outer_plan, solve_kwargs: replace(
            controlling,
            attempt_id=f"{controlling.attempt_id}-diagnostic",
            attempt_kind="diagnostic",
            window_diagnosis="target_conditioned_failure",
            audit=_audit(),
        ),
    )
    result = runner.run_sequential_execution("frozen", "hard_equality")

    assert calls == ["controlling"]
    assert not result.completed
    assert result.completed_intervals == 0
    assert result.termination_iteration == 0
    assert result.executed_b_mw.shape == (0, 1)
    assert result.executed_intervals == ()
    assert all(value == 0.0 for value in result.trajectory_summary.values())
    np.testing.assert_array_equal(result.realized_soc_mwh, [[500.0]])
    assert [attempt.attempt_kind for attempt in result.ac_attempts] == [
        "controlling",
        "diagnostic",
    ]
    assert result.ac_attempts[0].window_diagnosis == "target_conditioned_failure"


def test_dc_diagnostics_separate_reporting_consistency_from_nodal_balance():
    scenario = _short_scenario(horizon=1, window=1)
    nb = scenario.case["bus"].shape[0]
    nl = scenario.case["branch"].shape[0]
    results = {
        "Pg": np.zeros((1, len(scenario.generators))),
        "b": np.zeros((1, len(scenario.storage))),
        "soc": np.array([[500.0]]),
        "p_nd": np.zeros((1, len(scenario.nondispatchable))),
        "p_load_served": np.zeros((1, len(scenario.loads))),
        "p_net": np.zeros((1, nb)),
        "p_flows": np.zeros((1, nl)),
    }
    results["p_net"][0, 0] = 1.0
    build = SimpleNamespace(data={"storage_initial_soc": np.array([500.0])})

    residuals = runner._dc_residuals(
        scenario, build, results, {"battery_bus_7": 500.0}
    )

    assert residuals["dc_injection_reporting_mw_abs"] == 1.0
    assert residuals["dc_nodal_balance_pu_abs"] == 0.01


def test_trajectory_summary_aggregates_signpost_deviation_and_all_solve_runtime():
    scenario = _short_scenario(horizon=1, window=1)
    outer = replace(
        _outer_record(scenario, 0, {"battery_bus_7": 500.0}),
        audit=_audit(wall_time_seconds=2.0),
    )
    attempt = _ac_record(
        scenario,
        attempt_id="ac-000-quadratic_soft",
        attempt_kind="controlling",
        iteration=0,
        stop=1,
        outer_plan=outer,
        outer_local_boundary=1,
        realized_soc={"battery_bus_7": 500.0},
        target_soc={"battery_bus_7": 498.0},
        policy="quadratic_soft",
    )
    attempt = replace(
        attempt,
        terminal_deviation_mwh={"battery_bus_7": -3.5},
        audit=_audit(wall_time_seconds=4.0),
    )
    diagnostic = replace(
        attempt,
        attempt_id="diagnostic",
        attempt_kind="diagnostic",
        terminal_deviation_mwh=None,
        audit=_audit(wall_time_seconds=1.0),
    )
    interval = runner._executed_interval_record(scenario, attempt)
    interval = replace(
        interval,
        voltage_violation_pu=0.02,
        thermal_residual_mva=0.3,
        normalized_squared_thermal_residual=0.004,
    )

    summary = runner._trajectory_summary(
        [interval], {outer.outer_plan_id: outer}, [attempt, diagnostic]
    )

    assert summary["cumulative_absolute_signpost_deviation_mwh"] == 3.5
    assert summary["runtime_seconds"] == 7.0
    assert summary["maximum_voltage_violation_pu"] == 0.02
    assert summary["maximum_thermal_residual_mva"] == 0.3
    assert summary["maximum_normalized_squared_thermal_residual"] == 0.004


def test_endpoint_realization_reuses_one_plan_and_outer_boundary_states(monkeypatch):
    scenario = _short_scenario()
    outer = _outer_record(scenario, 0, {"battery_bus_7": 500.0})
    observed = []
    monkeypatch.setattr(runner, "load_frozen_scenario", lambda: scenario)
    monkeypatch.setattr(runner, "_solve_outer_plan", lambda *args: outer)

    def solve_ac(scenario, **kwargs):
        observed.append(kwargs)
        return _ac_record(scenario, **kwargs)

    monkeypatch.setattr(runner, "_solve_ac_attempt", solve_ac)
    study = runner.run_endpoint_realization(
        (runner.EndpointCase("crossing", 1, 3),)
    )

    assert study.completed
    assert study.termination_reason is None
    assert study.outer_plan is outer
    assert len(study.realizations) == 1
    assert study.realizations[0].outer_plan is outer
    assert study.realizations[0].diagnostic_attempt is None
    assert observed[0]["realized_soc"] == {"battery_bus_7": 501.0}
    assert observed[0]["target_soc"] == {"battery_bus_7": 503.0}
    assert observed[0]["outer_local_boundary"] == 3


def test_endpoint_outer_failure_is_returned_without_attempting_ac(monkeypatch):
    scenario = _short_scenario()
    failed_outer = replace(
        _outer_record(scenario, 0, {"battery_bus_7": 500.0}),
        boundary_soc_mwh=None,
        audit=_audit(accepted=False, outcome="solver_failure"),
    )
    monkeypatch.setattr(runner, "load_frozen_scenario", lambda: scenario)
    monkeypatch.setattr(runner, "_solve_outer_plan", lambda *args: failed_outer)
    monkeypatch.setattr(
        runner,
        "_solve_ac_attempt",
        lambda *args, **kwargs: pytest.fail("AC must not run"),
    )

    study = runner.run_endpoint_realization(
        (runner.EndpointCase("unreached", 0, 1),)
    )

    assert study.outer_plan is failed_outer
    assert study.outer_plan.audit.outcome == "solver_failure"
    assert study.realizations == ()
    assert not study.completed
    assert study.termination_reason == "outer_solver_failure"


def test_frozen_outer_and_first_ac_window_pass_full_audit_contract():
    """Exercise the reference boundary without running the S3 trajectory."""
    scenario = load_frozen_scenario()
    initial = runner._initial_soc(scenario)
    outer = runner._solve_outer_plan(scenario, 0, initial, None)

    assert outer.audit.accepted_primal
    assert outer.audit.outcome == "accepted"
    assert outer.boundary_soc_mwh.shape == (97, 1)
    assert outer.audit.solver_num_iters is not None
    assert set(outer.audit.residuals) == {
        "soc_recurrence_mwh_abs",
        "dc_injection_reporting_mw_abs",
        "dc_nodal_balance_pu_abs",
        "terminal_soc_mwh_abs",
    }

    target = runner._outer_target(outer, 5)
    attempt = runner._solve_ac_attempt(
        scenario,
        attempt_id="s2-first-window-audit",
        attempt_kind="controlling",
        iteration=0,
        stop=5,
        outer_plan=outer,
        outer_local_boundary=5,
        realized_soc=initial,
        target_soc=target,
        policy="hard_equality",
        solve_kwargs=None,
    )

    assert attempt.audit.accepted_primal
    assert attempt.window_diagnosis == "hard_target_met"
    assert attempt.audit.solver_num_iters is not None
    assert set(attempt.audit.residuals) == {
        "soc_recurrence_mwh_abs",
        "ac_active_balance_pu_abs",
        "ac_reactive_balance_pu_abs",
        "voltage_bound_pu_abs",
        "branch_mva_abs",
        "branch_normalized_squared_residual",
        "terminal_soc_mwh_abs",
    }
    realized = runner._executed_interval_record(scenario, attempt)
    assert realized.generation_cost > 0.0
    assert realized.storage_cycling_cost >= 0.0
    assert realized.renewable_curtailment_mwh >= 0.0
    assert realized.active_loss_mwh >= 0.0
    assert realized.active_loss_crosscheck_mw_abs <= 1e-6
    assert realized.state_transition_residual_mwh_abs <= 1e-4
    assert realized.voltage_violation_pu <= 1e-6
    assert realized.thermal_residual_mva <= 1e-4
    assert realized.normalized_squared_thermal_residual <= 1e-7
