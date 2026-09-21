"""No-scientific-solve tests for the frozen M17-S3b causal runner."""

from types import SimpleNamespace

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import OPFBuild
from experiments.hierarchical_battery_resilience import (
    causal_recovery_runner as recovery,
)
from experiments.hierarchical_battery_resilience.scenario import (
    load_frozen_scenario,
)


def _named_build(steps, *, pg_bounds=(-100.0, 100.0)):
    variables = []
    data = {}
    for step in range(steps):
        pg = cp.Variable(1, bounds=list(pg_bounds), name=f"Pg_{step}")
        b = cp.Variable(1, bounds=[-20.0, 20.0], name=f"b_{step}")
        b_q = cp.Variable(1, bounds=[-20.0, 20.0], name=f"b_q_{step}")
        soc = cp.Variable(1, bounds=[0.0, 1_000.0], name=f"soc_{step}")
        variables.extend((pg, b, b_q, soc))
    problem = cp.Problem(
        cp.Minimize(sum(cp.sum_squares(variable) for variable in variables))
    )
    return OPFBuild(problem, {}, data, "ac", True)


def _preceding_values(steps=5):
    values = {}
    for step in range(steps):
        values[f"Pg_{step}"] = np.array([10.0 + step])
        values[f"b_{step}"] = np.array([-(step + 1.0)])
        values[f"b_q_{step}"] = np.array([step + 0.5])
        values[f"soc_{step}"] = np.array([100.0 + step])
    return values


def test_attempt_registry_freezes_nine_unique_ordered_slots():
    slots = recovery.attempt_registry(35)

    assert len(slots) == 9
    assert [slot.ordinal for slot in slots] == list(range(9))
    assert [slot.role for slot in slots[:3]] == [
        "primary_controlling",
        "target_free",
        "copied_target_free",
    ]
    assert [slot.transformation for slot in slots[:3]] == [
        "shifted_preceding",
        "shifted_preceding",
        "copy_target_free",
    ]
    assert [slot.source_kind for slot in slots[3:]] == [
        "target_free",
        "target_free",
        "target_free",
        "causal",
        "causal",
        "causal",
    ]
    assert [slot.scale for slot in slots[3:]] == [
        1e-4,
        1e-3,
        1e-2,
        1e-4,
        1e-3,
        1e-2,
    ]


def test_seed_rule_is_unique_over_complete_frozen_trajectory():
    seeds = [
        slot.seed
        for iteration in range(96)
        for slot in recovery.attempt_registry(iteration)
        if slot.seed is not None
    ]

    assert len(seeds) == 96 * 6
    assert len(set(seeds)) == len(seeds)
    assert recovery.perturbation_seed(35, "target_free", 1) == 17_003_511
    assert recovery.perturbation_seed(35, "causal", 3) == 17_003_523


@pytest.mark.parametrize("steps", [5, 4, 1])
def test_shift_uses_global_overlap_and_reconstructs_soc(steps):
    destination = _named_build(steps)
    raw, projected = recovery.shifted_causal_start(
        _preceding_values(),
        destination,
        realized_soc_mwh={"battery": 500.0},
        storage_device_ids=("battery",),
        delta_hours=1.0,
        soc_tolerance=1e-9,
    )

    expected_b = [-2.0, -3.0, -4.0, -5.0, 0.0][:steps]
    expected_soc = np.asarray([502.0, 505.0, 509.0, 514.0, 514.0])[:steps]
    assert [projected[f"b_{step}"][0] for step in range(steps)] == pytest.approx(
        expected_b
    )
    assert [projected[f"soc_{step}"][0] for step in range(steps)] == pytest.approx(
        expected_soc
    )
    assert projected[f"Pg_{steps - 1}"] == pytest.approx(
        [10.0 + min(steps, 4)]
    )
    assert set(raw) == set(projected)


def test_shift_projects_nonstate_leaf_values_but_never_soc():
    destination = _named_build(5, pg_bounds=(-1.0, 1.0))
    raw, projected = recovery.shifted_causal_start(
        _preceding_values(),
        destination,
        realized_soc_mwh={"battery": 500.0},
        storage_device_ids=("battery",),
        delta_hours=1.0,
        soc_tolerance=1e-9,
    )

    assert raw["Pg_0"] == pytest.approx([11.0])
    assert projected["Pg_0"] == pytest.approx([1.0])
    assert raw["soc_0"] == pytest.approx(projected["soc_0"])


def test_shift_rejects_reconstructed_soc_outside_destination_bounds():
    destination = _named_build(5)

    with pytest.raises(ValueError, match="violates destination bounds"):
        recovery.shifted_causal_start(
            _preceding_values(),
            destination,
            realized_soc_mwh={"battery": 999.0},
            storage_device_ids=("battery",),
            delta_hours=1.0,
            soc_tolerance=1e-9,
        )


def test_perturbation_is_deterministic_projected_and_fortran_ordered():
    destination = _named_build(1, pg_bounds=(-1.0, 1.0))
    center = {
        name: np.zeros(variable.shape)
        for name, variable in recovery._variables_by_name(destination).items()
    }

    raw_a, projected_a = recovery.perturb_start(
        center, destination, scale=1e-2, seed=17_000_011
    )
    raw_b, projected_b = recovery.perturb_start(
        center, destination, scale=1e-2, seed=17_000_011
    )

    assert raw_a.keys() == raw_b.keys()
    for name in raw_a:
        assert raw_a[name] == pytest.approx(raw_b[name])
        assert projected_a[name] == pytest.approx(projected_b[name])
        variable = recovery._variables_by_name(destination)[name]
        assert projected_a[name] == pytest.approx(variable.project(raw_a[name]))


def _fake_record(scenario, outer, target, slot, accepted):
    record = recovery._empty_attempt(
        0,
        5,
        outer,
        {"battery_bus_7": 500.0},
        target,
        slot,
        "executed",
        "synthetic",
    )
    record.solver_executed = True
    record.x0_verified = True
    record.audit = SimpleNamespace(accepted_primal=accepted)
    record.starting_values = {"x": [0.0]}
    record.solution_values = {"x": np.array([0.0])} if accepted else None
    return record


def test_window_stops_after_primary_acceptance_and_retains_all_slots(monkeypatch):
    scenario = load_frozen_scenario()
    outer = SimpleNamespace(outer_plan_id="outer-000")
    target = {"battery_bus_7": 600.0}
    calls = []

    monkeypatch.setattr(recovery, "_build_ac", lambda *_args: object())
    monkeypatch.setattr(recovery, "_assign_complete_start", lambda *_args: None)
    def fake_attempt(_scenario, **kwargs):
        calls.append(kwargs["slot"].ordinal)
        return _fake_record(
            scenario, outer, target, kwargs["slot"], accepted=True
        )

    monkeypatch.setattr(recovery, "_attempt_from_build", fake_attempt)
    records, accepted = recovery._execute_window(
        scenario,
        iteration=0,
        stop=5,
        outer_plan=outer,
        realized_soc={"battery_bus_7": 500.0},
        target_soc=target,
        preceding_solution=None,
    )

    assert calls == [0]
    assert len(records) == 9
    assert records[0].slot_state == "executed"
    assert all(
        record.slot_state == "not_needed_after_acceptance"
        for record in records[1:]
    )
    assert accepted is records[0]
    assert accepted.supplied_executed_action


def test_window_uses_target_free_copy_before_perturbations(monkeypatch):
    scenario = load_frozen_scenario()
    outer = SimpleNamespace(outer_plan_id="outer-000")
    target = {"battery_bus_7": 600.0}
    calls = []

    monkeypatch.setattr(recovery, "_build_ac", lambda *_args: object())
    monkeypatch.setattr(recovery, "_assign_complete_start", lambda *_args: None)

    def fake_attempt(_scenario, **kwargs):
        ordinal = kwargs["slot"].ordinal
        calls.append(ordinal)
        return _fake_record(
            scenario,
            outer,
            target,
            kwargs["slot"],
            accepted=ordinal in {1, 2},
        )

    monkeypatch.setattr(recovery, "_attempt_from_build", fake_attempt)
    records, accepted = recovery._execute_window(
        scenario,
        iteration=0,
        stop=5,
        outer_plan=outer,
        realized_soc={"battery_bus_7": 500.0},
        target_soc=target,
        preceding_solution=None,
    )

    assert calls == [0, 1, 2]
    assert [record.slot_state for record in records[:3]] == [
        "executed",
        "executed",
        "executed",
    ]
    assert all(
        record.slot_state == "not_needed_after_acceptance"
        for record in records[3:]
    )
    assert accepted is records[2]
    assert accepted.supplied_executed_action
    assert records[1].source_attempt_id is None


def test_window_construction_failure_retains_all_nine_slots(monkeypatch):
    scenario = load_frozen_scenario()
    outer = SimpleNamespace(outer_plan_id="outer-001")

    def fail_build(*_args):
        raise ValueError("synthetic source mismatch")

    monkeypatch.setattr(recovery, "_build_ac", fail_build)
    records, accepted = recovery._execute_window(
        scenario,
        iteration=1,
        stop=6,
        outer_plan=outer,
        realized_soc={"battery_bus_7": 500.0},
        target_soc={"battery_bus_7": 600.0},
        preceding_solution={"x": np.array([0.0])},
        preceding_attempt_id="s3b-000-00-primary_controlling",
    )

    assert accepted is None
    assert len(records) == 9
    assert records[0].slot_state == "construction_error"
    assert all(record.slot_state == "source_unavailable" for record in records[1:])
    assert not any(record.solver_executed for record in records)


def test_failed_target_free_executes_only_five_causal_attempts(monkeypatch):
    scenario = load_frozen_scenario()
    outer = SimpleNamespace(outer_plan_id="outer-001")
    target = {"battery_bus_7": 600.0}
    calls = []

    monkeypatch.setattr(recovery, "_build_ac", lambda *_args: object())
    monkeypatch.setattr(recovery, "_assign_complete_start", lambda *_args: None)
    monkeypatch.setattr(
        recovery,
        "shifted_causal_start",
        lambda *_args, **_kwargs: (
            {"x": np.array([0.0])},
            {"x": np.array([0.0])},
        ),
    )
    monkeypatch.setattr(
        recovery,
        "perturb_start",
        lambda center, *_args, **_kwargs: (center, center),
    )

    def fake_attempt(_scenario, **kwargs):
        calls.append(kwargs["slot"].ordinal)
        return _fake_record(
            scenario, outer, target, kwargs["slot"], accepted=False
        )

    monkeypatch.setattr(recovery, "_attempt_from_build", fake_attempt)
    records, accepted = recovery._execute_window(
        scenario,
        iteration=1,
        stop=6,
        outer_plan=outer,
        realized_soc={"battery_bus_7": 500.0},
        target_soc=target,
        preceding_solution={"x": np.array([0.0])},
        preceding_attempt_id="s3b-000-00-primary_controlling",
    )

    assert accepted is None
    assert calls == [0, 1, 6, 7, 8]
    assert len(records) == 9
    assert records[2].slot_state == "source_unavailable"
    assert all(records[index].slot_state == "source_unavailable" for index in (3, 4, 5))


def test_shifted_success_denominator_includes_construction_failures():
    outer = SimpleNamespace(outer_plan_id="outer")
    target = {"battery_bus_7": 600.0}
    failed = recovery._empty_attempt(
        1,
        6,
        outer,
        {"battery_bus_7": 500.0},
        target,
        recovery.attempt_registry(1)[0],
        "construction_error",
        "synthetic shifted-start construction failure",
    )
    accepted = recovery._empty_attempt(
        2,
        7,
        outer,
        {"battery_bus_7": 500.0},
        target,
        recovery.attempt_registry(2)[0],
        "executed",
        "synthetic accepted solve",
    )
    accepted.solver_executed = True
    accepted.supplied_executed_action = True
    accepted.audit = SimpleNamespace(wall_time_seconds=1.0)

    summary = recovery._recovery_summary([], {}, [failed, accepted])

    assert summary["shifted_primary_success_count"] == 1
    assert summary["shifted_primary_opportunity_count"] == 2
    assert summary["shifted_primary_success_fraction"] == pytest.approx(0.5)
    assert summary["actual_ac_solver_call_count"] == 1


@pytest.mark.parametrize("steps", [5, 4, 1])
def test_real_window_x0_is_intercepted_before_ipopt(steps):
    scenario = load_frozen_scenario()
    realized = {"battery_bus_7": 500.0}
    target = {"battery_bus_7": 500.0}
    build = recovery._build_ac(scenario, 0, steps, realized, target)
    outer = SimpleNamespace(outer_plan_id="outer-000")

    record = recovery._attempt_from_build(
        scenario,
        iteration=0,
        stop=steps,
        outer_plan=outer,
        target_soc=target,
        slot=recovery.attempt_registry(0)[0],
        source_attempt_id=None,
        build=build,
        raw_start=None,
        assigned_start=None,
        target_free=False,
        intercept_before_ipopt=True,
    )

    assert not record.solver_executed
    assert record.x0_verified
    assert record.model_x0_count > 0
    assert record.auxiliary_x0_count > 0
    assert len(record.solver_x0) == (
        record.model_x0_count + record.auxiliary_x0_count
    )
    if steps == 5:
        assert record.model_x0_count == 745
        assert record.auxiliary_x0_count == 185
    assert record.audit.exception.startswith("X0InterceptionComplete:")
    assert not record.audit.accepted_primal


def test_target_free_and_hard_builds_have_identical_model_names_and_shapes():
    scenario = load_frozen_scenario()
    realized = {"battery_bus_7": 500.0}
    hard = recovery._build_ac(
        scenario, 0, 5, realized, {"battery_bus_7": 600.0}
    )
    target_free = recovery._build_ac(scenario, 0, 5, realized, None)

    hard_variables = recovery._variables_by_name(hard)
    target_free_variables = recovery._variables_by_name(target_free)
    assert hard_variables.keys() == target_free_variables.keys()
    assert {
        name: variable.shape for name, variable in hard_variables.items()
    } == {
        name: variable.shape for name, variable in target_free_variables.items()
    }


def test_x0_construction_failure_is_retained_without_solver_execution(monkeypatch):
    scenario = load_frozen_scenario()
    realized = {"battery_bus_7": 500.0}
    target = {"battery_bus_7": 500.0}
    build = recovery._build_ac(scenario, 0, 1, realized, target)

    def fail_x0(*_args, **_kwargs):
        raise ValueError("synthetic canonicalization failure")

    monkeypatch.setattr(recovery, "_run_build_with_verified_x0", fail_x0)
    record = recovery._attempt_from_build(
        scenario,
        iteration=0,
        stop=1,
        outer_plan=SimpleNamespace(outer_plan_id="outer-000"),
        target_soc=target,
        slot=recovery.attempt_registry(0)[0],
        source_attempt_id=None,
        build=build,
        raw_start=None,
        assigned_start=None,
        target_free=False,
    )

    assert record.slot_state == "construction_error"
    assert not record.solver_executed
    assert record.audit is None
    assert record.reason.startswith("x0_construction_error:ValueError:")
