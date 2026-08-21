"""End-to-end verification gates for the M17-S6 hierarchy."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from cvxopf import (
    HVDCLink,
    HierarchicalPolicy,
    NondispatchableUnit,
    StorageUnitIdeal,
    solve_hierarchical_opf,
)
from cvxopf import _hierarchical_solver
from tests.test_hierarchical_solver import _inputs


pytestmark = pytest.mark.filterwarnings(
    "ignore:Loading 'cvxopt' into a process that has already imported 'cyipopt'"
)


def _two_storage_inputs(*, horizon_steps: int = 3):
    """Return a deliberately non-bus-ordered two-storage hierarchy."""
    inputs = _inputs(horizon_steps=horizon_steps)
    storage = (
        inputs.storage[0],
        StorageUnitIdeal(
            bus=5,
            apparent_power_rating=50.0,
            capacity=200.0,
            initial_soc=100.0,
            terminal_soc=100.0,
            terminal_constraint="equality",
            device_id="battery-5",
        ),
    )
    return replace(inputs, storage=storage)


def _assert_state_and_signpost_handoff(result) -> None:
    ids = result.storage_device_ids
    for attempt in result.ac_attempts:
        outer = result.outer_plans[attempt.outer_plan_id]
        assert outer.boundary_soc_mwh is not None
        local_boundary = attempt.global_interval_stop - outer.global_interval_start
        expected_target = outer.boundary_soc_mwh[local_boundary]
        assert np.allclose(
            [attempt.target_soc_mwh[device_id] for device_id in ids],
            expected_target,
        )
        assert np.allclose(
            [attempt.initial_soc_mwh[device_id] for device_id in ids],
            result.realized_soc_mwh[attempt.iteration],
        )


def test_storage_state_alignment_uses_ids_not_mapping_order():
    inputs = _two_storage_inputs(horizon_steps=1)
    snapshot = _hierarchical_solver._execution_snapshot(inputs)
    units = _hierarchical_solver._inner_storage(
        snapshot,
        HierarchicalPolicy(
            ac_window_steps=1, initialization_policy="flat_only"
        ),
        {"battery-5": 90.0, "battery-7": 610.0},
        {"battery-5": 95.0, "battery-7": 620.0},
    )

    assert [unit.device_id for unit in units] == ["battery-7", "battery-5"]
    assert [unit.initial_soc for unit in units] == [610.0, 90.0]
    assert [unit.terminal_soc for unit in units] == [620.0, 95.0]


@pytest.mark.parametrize("outer_policy", ["frozen", "replan_every_step"])
def test_two_storage_t3_w2_alignment_and_final_truncation(outer_policy):
    """Exercise the protocol's hand-checkable T=3, W=2 recurrence."""
    result = solve_hierarchical_opf(
        _two_storage_inputs(),
        HierarchicalPolicy(
            ac_window_steps=2,
            outer_policy=outer_policy,
            initialization_policy="flat_only",
        ),
    )

    assert result.completed
    assert result.storage_device_ids == ("battery-7", "battery-5")
    assert result.realized_soc_mwh.shape == (4, 2)
    assert result.executed_b_mw.shape == (3, 2)
    assert [attempt.global_interval_stop for attempt in result.ac_attempts] == [
        2,
        3,
        3,
    ]
    assert [attempt.local_interval_stop for attempt in result.ac_attempts] == [
        2,
        2,
        1,
    ]
    assert [attempt.build.data["T"] for attempt in result.ac_attempts] == [2, 2, 1]
    assert np.allclose(
        result.realized_soc_mwh[1:],
        result.realized_soc_mwh[:-1] - result.delta * result.executed_b_mw,
    )
    _assert_state_and_signpost_handoff(result)

    plans = tuple(result.outer_plans.values())
    if outer_policy == "frozen":
        assert len(plans) == 1
        assert np.array_equal(plans[0].global_boundary_indices, [0, 1, 2, 3])
    else:
        assert len(plans) == 3
        assert [plan.created_iteration for plan in plans] == [0, 1, 2]
        assert [plan.local_boundary_indices.tolist() for plan in plans] == [
            [0, 1, 2, 3],
            [0, 1, 2],
            [0, 1],
        ]


@pytest.mark.parametrize("outer_policy", ["frozen", "replan_every_step"])
def test_w1_targets_the_immediate_post_step_boundary(outer_policy):
    result = solve_hierarchical_opf(
        _inputs(horizon_steps=3),
        HierarchicalPolicy(
            ac_window_steps=1,
            outer_policy=outer_policy,
            initialization_policy="flat_only",
        ),
    )

    assert result.completed
    assert [attempt.global_interval_stop for attempt in result.ac_attempts] == [
        1,
        2,
        3,
    ]
    assert all(attempt.local_interval_stop == 1 for attempt in result.ac_attempts)
    assert all(attempt.build.data["T"] == 1 for attempt in result.ac_attempts)
    _assert_state_and_signpost_handoff(result)


def test_shifted_start_survives_final_window_structure_change():
    result = solve_hierarchical_opf(
        _inputs(horizon_steps=3),
        HierarchicalPolicy(
            ac_window_steps=2,
            outer_policy="replan_every_step",
            initialization_policy="shifted_with_recovery",
        ),
    )

    assert result.completed
    primaries = [result.ac_attempts[index] for index in (0, 9, 18)]
    assert [attempt.local_interval_stop for attempt in primaries] == [2, 2, 1]
    assert [attempt.transformation for attempt in primaries] == [
        "flat",
        "shifted_preceding",
        "shifted_preceding",
    ]
    for attempt in primaries:
        assert attempt.solver_evidence is not None
        assert attempt.assigned_start is not None
        assert (
            attempt.solver_evidence.object_ids_before
            == attempt.solver_evidence.object_ids_after
        )
        assert attempt.solver_evidence.model_coordinate_count == sum(
            value.size for value in attempt.assigned_start.values()
        )
    final_evidence = primaries[2].solver_evidence
    preceding_evidence = primaries[1].solver_evidence
    assert final_evidence is not None and preceding_evidence is not None
    assert final_evidence.complete_x0.size < preceding_evidence.complete_x0.size


def test_conditional_nondispatchable_and_hvdc_paths_are_audited():
    inputs = _inputs(horizon_steps=1)
    nd = NondispatchableUnit(
        bus=5,
        p_available=20.0,
        apparent_power_rating=25.0,
        device_id="solar-5",
    )
    link = HVDCLink(
        from_bus=5,
        to_bus=7,
        p_min_mw=-5.0,
        p_max_mw=-5.0,
        loss_percent=2.0,
        device_id="dc-5-7",
    )
    inputs = replace(
        inputs,
        nondispatchable=(nd,),
        df_nd=pd.DataFrame([[20.0]], columns=["solar-5"]),
        hvdc=(link,),
        df_hvdc_min=pd.DataFrame([[-5.0]], columns=["dc-5-7"]),
        df_hvdc_max=pd.DataFrame([[-5.0]], columns=["dc-5-7"]),
    )

    result = solve_hierarchical_opf(
        inputs,
        HierarchicalPolicy(ac_window_steps=1, initialization_policy="flat_only"),
    )

    assert result.completed
    outer = next(iter(result.outer_plans.values()))
    attempt = result.ac_attempts[0]
    assert outer.audit.missing_or_nonfinite_fields == ()
    assert attempt.audit is not None
    assert attempt.audit.missing_or_nonfinite_fields == ()
    assert attempt.audit.residuals["ac_active_balance_pu_abs"] <= 1e-8
    assert attempt.audit.residuals["ac_reactive_balance_pu_abs"] <= 1e-8
    assert np.allclose(attempt.result["p_nd"], [[20.0]], atol=1e-7)
    assert np.allclose(attempt.result["p_hvdc_in"], [[-5.0]], atol=1e-7)
    assert np.allclose(attempt.result["p_hvdc_out"], [[4.9]], atol=1e-7)
    assert np.allclose(attempt.result["hvdc_loss"], [[0.1]], atol=1e-7)


def test_missing_conditional_device_output_prevents_execution(monkeypatch):
    inputs = _inputs(horizon_steps=1)
    nd = NondispatchableUnit(
        bus=5,
        p_available=20.0,
        apparent_power_rating=25.0,
        device_id="solar-5",
    )
    inputs = replace(
        inputs,
        nondispatchable=(nd,),
        df_nd=pd.DataFrame([[20.0]], columns=["solar-5"]),
    )
    original = _hierarchical_solver.extract_results
    calls = 0

    def omit_ac_reactive_output(build):
        nonlocal calls
        calls += 1
        result = original(build)
        if calls == 2:
            result["q_nd"] = None
        return result

    monkeypatch.setattr(
        _hierarchical_solver, "extract_results", omit_ac_reactive_output
    )
    result = solve_hierarchical_opf(
        inputs,
        HierarchicalPolicy(ac_window_steps=1, initialization_policy="flat_only"),
    )

    assert not result.completed
    assert result.completed_intervals == 0
    attempt = result.ac_attempts[0]
    assert attempt.audit is not None
    assert attempt.audit.outcome == "unusable_primal"
    assert attempt.audit.missing_or_nonfinite_fields == ("q_nd",)
    assert not attempt.supplied_executed_action


def test_trajectory_summary_reconstructs_executed_records_exactly_once():
    result = solve_hierarchical_opf(
        _inputs(horizon_steps=3),
        HierarchicalPolicy(ac_window_steps=2, initialization_policy="flat_only"),
    )
    records = result.executed_intervals
    summary = result.trajectory_summary

    for name in (
        "generation_cost",
        "storage_cycling_cost",
        "renewable_curtailment_mwh",
        "active_loss_mwh",
    ):
        assert summary[name] == pytest.approx(
            sum(getattr(record, name) for record in records)
        )
    assert summary["maximum_voltage_violation_pu"] == max(
        record.voltage_violation_pu for record in records
    )
    assert summary["maximum_thermal_residual_mva"] == max(
        record.thermal_residual_mva for record in records
    )
    assert summary["maximum_normalized_squared_thermal_residual"] == max(
        record.normalized_squared_thermal_residual for record in records
    )
    assert summary["cumulative_absolute_signpost_deviation_mwh"] == (
        pytest.approx(
            sum(
                sum(abs(value) for value in attempt.terminal_deviation_mwh.values())
                for attempt in result.ac_attempts
                if attempt.supplied_executed_action
                and attempt.terminal_deviation_mwh is not None
            )
        )
    )
    expected_runtime = sum(
        plan.audit.wall_time_seconds for plan in result.outer_plans.values()
    ) + sum(
        attempt.audit.wall_time_seconds
        for attempt in result.ac_attempts
        if attempt.audit is not None
    )
    assert summary["runtime_seconds"] == pytest.approx(expected_runtime)


def test_tolerance_level_negative_curtailment_is_zeroed_for_accounting(
    monkeypatch,
):
    inputs = _inputs(horizon_steps=1)
    nd = NondispatchableUnit(
        bus=5,
        p_available=20.0,
        apparent_power_rating=25.0,
        device_id="solar-5",
    )
    inputs = replace(
        inputs,
        nondispatchable=(nd,),
        df_nd=pd.DataFrame([[20.0]], columns=["solar-5"]),
    )
    original = _hierarchical_solver.extract_results
    calls = 0

    def inject_tolerance_level_residual(build):
        nonlocal calls
        calls += 1
        result = original(build)
        if calls == 2:
            result["curtailment"] = np.array([[-1e-10]])
        return result

    monkeypatch.setattr(
        _hierarchical_solver,
        "extract_results",
        inject_tolerance_level_residual,
    )
    result = solve_hierarchical_opf(
        inputs,
        HierarchicalPolicy(ac_window_steps=1, initialization_policy="flat_only"),
    )

    assert result.completed
    assert result.ac_attempts[0].audit is not None
    assert result.ac_attempts[0].audit.residuals[
        "curtailment_nonnegativity_pu_abs"
    ] == pytest.approx(1e-12)
    assert result.executed_intervals[0].renewable_curtailment_mwh == 0.0
    assert result.trajectory_summary["renewable_curtailment_mwh"] == 0.0


def test_material_negative_curtailment_is_not_hidden(monkeypatch):
    inputs = _inputs(horizon_steps=1)
    nd = NondispatchableUnit(
        bus=5,
        p_available=20.0,
        apparent_power_rating=25.0,
        device_id="solar-5",
    )
    inputs = replace(
        inputs,
        nondispatchable=(nd,),
        df_nd=pd.DataFrame([[20.0]], columns=["solar-5"]),
    )
    original = _hierarchical_solver.extract_results
    calls = 0

    def inject_material_residual(build):
        nonlocal calls
        calls += 1
        result = original(build)
        if calls == 2:
            result["curtailment"] = np.array([[-1.0]])
        return result

    monkeypatch.setattr(
        _hierarchical_solver, "extract_results", inject_material_residual
    )
    result = solve_hierarchical_opf(
        inputs,
        HierarchicalPolicy(ac_window_steps=1, initialization_policy="flat_only"),
    )

    assert not result.completed
    assert result.completed_intervals == 0
    assert result.executed_intervals == ()
    attempt = result.ac_attempts[0]
    assert attempt.audit is not None
    assert attempt.audit.outcome == "unusable_primal"
    assert not attempt.audit.accepted_primal
    assert attempt.audit.residuals[
        "curtailment_nonnegativity_pu_abs"
    ] == pytest.approx(0.01)
