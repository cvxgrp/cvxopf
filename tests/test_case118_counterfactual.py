"""Counterfactual contracts; all native solver calls are intercepted or absent."""

from dataclasses import asdict, replace
import json

import numpy as np
import pytest

from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.p0_fixture import load_p0_fixture
from experiments.case118_annual_hierarchy.s5_speculative_attempt import (
    load_retained_start,
)
from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    Completion,
    WindowKey,
)
from experiments.case118_counterfactual.model import (
    ARMS,
    ComparisonTolerances,
    MatchedWindow,
    audit_result,
    build_arm,
    cost_and_changes,
    map_start,
)
from experiments.case118_counterfactual.runner import (
    FixedBatteryRace,
    choose_candidate,
    comparisons,
    reassess,
)
from experiments.case118_counterfactual.worker import execute_attempt, jsonable

TOL = ComparisonTolerances(1e-5, 1e-5, 1e-6, 1e-10)


@pytest.fixture
def matched():
    f = load_p0_fixture(6)
    # Analytically feasible zero-flow case: no load, charging, or shunts.
    # No optimizer is used to manufacture a passing fixture.
    case = streaming._copy_case(f.inputs.case)
    case["branch"][:, 4] = 0
    case["bus"][:, 4:6] = 0
    inputs = replace(
        f.inputs,
        case=case,
        generators=tuple(
            replace(g, p_min_mw=0, cost_coeffs=(0.0, *g.cost_coeffs[1:]))
            for g in f.inputs.generators
        ),
        df_load_p=f.inputs.df_load_p * 0,
        df_load_q=f.inputs.df_load_q * 0,
    )
    window = MatchedWindow(
        1,
        4,
        streaming.execution_input_sha256(inputs),
        np.zeros((3, len(inputs.generators))),
        np.zeros((3, 1)),
        np.empty((3, 0)),
        np.full((4, 1), 500.0),
        {"test": "analytic"},
    )
    nb, ng, nl, nd = (
        len(case["bus"]),
        len(inputs.generators),
        len(case["branch"]),
        len(inputs.loads),
    )
    result = {
        "status": "optimal",
        "objective": 0.0,
        "storage_cost": 0.0,
        "storage_device_ids": list(inputs.storage_device_ids),
    }
    for names, size in (
        (["Pg", "Qg"], ng),
        (["b", "b_q", "soc"], 1),
        (["Vm", "Va_deg", "p_net", "q_net"], nb),
        (
            [
                "branch_p_from",
                "branch_p_to",
                "branch_q_from",
                "branch_q_to",
                "branch_s_from",
                "branch_s_to",
            ],
            nl,
        ),
        (["p_load", "q_load", "p_load_served", "q_load_served"], nd),
    ):
        result.update({name: np.zeros((3, size)) for name in names})
    result["Vm"][:] = 1.0
    result["soc"][:] = 500.0
    return inputs, f.policy, f.solve_config, window, result


def audit(matched, result=None, arm="G", **kwargs):
    inputs, policy, _, window, original = matched
    return audit_result(
        inputs,
        policy,
        window,
        arm,
        original if result is None else result,
        TOL,
        reported_common_cost=0.0,
        **kwargs,
    )


def test_analytic_feasible_and_nonfinite_unavailable(matched):
    assert audit(matched)["accepted"]
    assert not audit(matched, {"status": None})["accepted"]
    assert not audit(matched, exception="failed local solve")["accepted"]


@pytest.mark.parametrize(
    "field,change",
    [
        ("b", 1.0),
        ("soc", 1.0),
        ("p_load", 1.0),
        ("p_load_served", 1.0),
        ("Pg", 1.0),
        ("Qg", 1.0),
        ("Vm", 0.1),
        ("p_net", 1.0),
        ("branch_s_from", 1.0),
        ("branch_p_from", 1.0),
    ],
)
def test_independent_audit_rejects_tampered_physics(matched, field, change):
    result = {
        k: v.copy() if isinstance(v, np.ndarray) else v for k, v in matched[-1].items()
    }
    result[field][0, 0] += change
    assert not audit(matched, result)["accepted"]


def test_reject_storage_identity_and_cost_tampering(matched):
    result = dict(matched[-1], storage_device_ids=["wrong"])
    assert not audit(matched, result)["accepted"]
    assert not audit(matched, dict(matched[-1], objective=1.0))["accepted"]


def test_reference_identity_and_recurrence(matched):
    inputs, policy, _, window, _ = matched
    with pytest.raises(ValueError, match="ordered inputs"):
        replace(window, input_sha256="wrong").validate(inputs, policy)
    with pytest.raises(ValueError, match="reconstruct"):
        replace(window, battery_mw=np.ones((3, 1))).validate(inputs, policy)
    changed = replace(inputs, generators=tuple(reversed(inputs.generators)))
    with pytest.raises(ValueError, match="ordered inputs"):
        window.validate(changed, policy)


def test_four_arms_constraints_objectives_and_units(matched):
    inputs, policy, _, window, _ = matched
    counts = {}
    for arm in ARMS:
        model = build_arm(
            inputs, policy, window, arm, repair_budget_mwh=1.0 if arm == "R2" else None
        )
        values = map_start(model, None)
        for var in model.build.variables["Pg"]:
            values[var.name()] = np.ones(var.shape) * 0.02  # 2 MW per generator
        streaming.assign_start(model.build, values)
        assert model.departure.value == pytest.approx(3 * len(inputs.generators) * 2)
        assert model.departure.is_convex()
        assert (
            model.common_cost is not model.build.prob.objective.expr
            if arm == "R1"
            else model.common_cost is model.build.prob.objective.expr
        )
        counts[arm] = len(model.build.prob.constraints)
    assert counts["R2"] == counts["R1"] + 1
    assert counts["G"] == counts["B"] + 1  # hourly/device b lock only
    with pytest.raises(ValueError, match="explicit"):
        build_arm(inputs, policy, window, "R2")
    with pytest.raises(ValueError, match="only B"):
        build_arm(inputs, policy, window, "G", hard_target=False)


def test_map_incumbent_every_physical_coordinate_and_epigraph(matched):
    inputs, policy, _, window, _ = matched
    r1 = build_arm(inputs, policy, window, "R1")
    source = map_start(r1, None)
    for arm in ("R2", "G", "B"):
        model = build_arm(
            inputs, policy, window, arm, repair_budget_mwh=100 if arm == "R2" else None
        )
        mapped = map_start(model, source)
        for name in model.physical_names:
            np.testing.assert_array_equal(mapped[name], source[name])
        broken = dict(source)
        broken.pop(next(iter(model.physical_names)))
        with pytest.raises(ValueError, match="namespace"):
            map_start(model, broken)


def test_half_hour_accounting_and_timing_shift_cost(matched):
    inputs, _, _, window, original = matched
    inputs = replace(inputs, delta=0.5)
    result = {
        k: v.copy() if isinstance(v, np.ndarray) else v for k, v in original.items()
    }
    result["Pg"][:] = 2.0
    result["b"][:, 0] = [-2.0, 0.0, 2.0]
    metrics = cost_and_changes(inputs, window, result)
    assert metrics["departure_mwh"] == 3 * len(inputs.generators)
    assert metrics["throughput_mwh"] == 2.0
    assert metrics["storage_cost"] == 2 * inputs.storage[0].aging_weight
    expected = (
        0.5
        * 3
        * sum(
            np.polynomial.polynomial.polyval(2.0, g.cost_coeffs)
            for g in inputs.generators
        )
    )
    assert metrics["generation_cost"] == pytest.approx(expected)
    result["b"][:, 0] = [0.0, -2.0, 2.0]
    assert (
        cost_and_changes(inputs, window, result)["storage_cost"]
        == metrics["storage_cost"]
    )


def test_transfer_projects_tolerated_leaf_residual_without_mutating_candidate(matched):
    inputs, policy, _, window, _ = matched
    model = build_arm(inputs, policy, window, "G")
    source = map_start(model, None)
    v = model.build.variables["v"][0]
    source[v.name()][0] = np.asarray(inputs.case["bus"])[0, 11] + 1e-8
    before = source[v.name()].copy()
    mapped = map_start(build_arm(inputs, policy, window, "B"), source)
    assert mapped[v.name()][0] == np.asarray(inputs.case["bus"])[0, 11]
    np.testing.assert_array_equal(source[v.name()], before)


def test_r2_budget_audited_from_actual_departure_not_epigraph(matched):
    inputs, policy, _, window, result = matched
    # Reference departure is nonzero while the candidate remains AC-feasible.
    window = replace(window, pg_mw=np.ones_like(window.pg_mw))
    rejected = audit_result(
        inputs,
        policy,
        window,
        "R2",
        result,
        TOL,
        repair_budget_mwh=1,
        reported_common_cost=0.0,
    )
    assert not rejected["accepted"]
    assert rejected["residuals"]["repair_budget_mwh_abs"] == window.pg_mw.size - 1


def test_fixed_start_ladder_bounded_replay_and_final():
    race = FixedBatteryRace(WindowKey("example-R1", 20))
    assert not race.needs_help(299)
    assert race.needs_help(300)
    race.complete_batch([Completion(race.primary, "rejected", True, 5.0, 100.0)])
    bounded = []
    for slot in (6, 7, 8):
        spec = race.next_helper()
        bounded.append(spec)
        assert spec.source_slot == slot and spec.budget_seconds == 300
        race.complete_batch([Completion(spec, "rejected", True, float(slot), 50.0)])
    replay = race.next_helper()
    assert replay.order == 9 and replay.replay_of == bounded[0].attempt_id
    assert replay.budget_seconds is None
    race.complete_batch([Completion(replay, "rejected", True, 6.0, 50.0)])
    while not race.exhausted:
        spec = race.next_helper()
        if spec is not None:
            assert (
                spec.source_slot in (7, 8)
                and spec.hard_target
                and spec.budget_seconds is None
            )
            race.complete_batch([Completion(spec, "rejected", True, 7.0, 50.0)])
    assert all(s.source_slot in (0, 6, 7, 8) for s in race.launched.values())
    assert set(range(4, 9)) <= set(race.unavailable)


def test_incumbent_survives_worse_failed_and_equal_solve():
    good = ({"path": "prior"}, {"accepted": True, "metrics": {"common_cost": 10.0}})
    worse = ({"path": "new"}, {"accepted": True, "metrics": {"common_cost": 11.0}})
    assert choose_candidate(worse, good) == ("incumbent", good)
    assert choose_candidate(None, good) == ("incumbent", good)
    assert choose_candidate(good, good) == ("incumbent", good)
    assert choose_candidate(None, None) is None


def test_reassess_r1_under_economic_objective(matched):
    inputs, policy, _, window, result = matched
    window = replace(window, pg_mw=np.ones_like(window.pg_mw))
    r1 = dict(result, objective=float(window.pg_mw.size))
    payload = {"result": r1, "common_cost_expression": 0.0, "exception": None}
    assert reassess(inputs, policy, window, "R2", payload, TOL, window.pg_mw.size)[
        "accepted"
    ]
    assert r1["objective"] == window.pg_mw.size  # source objective untouched


def test_complete_x0_retained_and_solver_failure_is_unresolved(
    matched, monkeypatch, tmp_path
):
    inputs, policy, config, window, _ = matched
    calls = []
    spec = AttemptSpec(WindowKey("test-R1", window.start), 0, 0)
    request = jsonable(
        {
            "invocation": asdict(spec),
            "window": asdict(window),
            "arm": "R1",
            "repair_budget_mwh": None,
            "source": None,
            "replay": None,
            "replay_request": None,
            "tolerances": asdict(TOL),
            "ac_options": dict(config.ac.options),
            "execution": {"test": True},
        }
    )

    def intercepted(self, data, *args, **kwargs):
        retained = load_retained_start(tmp_path / "start.json")
        np.testing.assert_array_equal(retained.evidence.complete_x0, data["x0"])
        calls.append(True)
        raise RuntimeError("intercepted; native solver not called")

    monkeypatch.setattr(streaming.IPOPT, "solve_via_data", intercepted)
    payload = execute_attempt(tmp_path, inputs, policy, config, request)
    assert calls == [True]
    assert payload["audit"]["accepted"] is False
    assert "intercepted" in payload["exception"]
    assert (
        payload["result"]["status"] is None
    )  # finite start/last-iterate arrays are not a solution
    assert json.loads((tmp_path / "result.json").read_text()) == payload
    with pytest.raises(FileExistsError):
        execute_attempt(tmp_path, inputs, policy, config, request)


def test_missing_comparators_not_zero_savings():
    assert comparisons([]) == {
        "R2_minus_G": None,
        "G_minus_B": None,
        "R2_minus_B": None,
    }


def test_all_arms_worker_transfer_and_parent_audit_without_native_solve(
    matched, monkeypatch, tmp_path
):
    """Exercise real canonical starts and extraction using an analytic solution."""
    from experiments.case118_counterfactual import worker
    from experiments.case118_counterfactual.runner import reference

    inputs, policy, config, window, _ = matched
    original = streaming.solve_ac_with_verified_x0
    calls = []

    def intercepted(self, data, *args, **kwargs):
        calls.append(True)
        raise RuntimeError("no native solver: analytic fixture substituted below")

    def analytic_solution(build, config, **kwargs):
        run = original(build, config, **kwargs)
        assert run.evidence is not None
        for var in build.prob.variables():
            var.value = var.project(np.zeros(var.shape))
        for var in build.variables["v"]:
            var.value = np.ones(var.shape)
        for var in build.variables["soc"]:
            var.value = np.full(var.shape, 500.0)
        build.prob._status = "optimal"
        build.prob._value = float(build.prob.objective.expr.value)
        return replace(run, exception=None)

    monkeypatch.setattr(streaming.IPOPT, "solve_via_data", intercepted)
    monkeypatch.setattr(
        worker.streaming, "solve_ac_with_verified_x0", analytic_solution
    )
    source = None
    for arm in ARMS:
        directory = tmp_path / arm
        directory.mkdir()
        spec = AttemptSpec(WindowKey(f"test-{arm}", window.start), 0, 0)
        request = jsonable(
            {
                "invocation": asdict(spec),
                "window": asdict(window),
                "arm": arm,
                "repair_budget_mwh": 1.0 if arm == "R2" else None,
                "source": source,
                "replay": None,
                "replay_request": None,
                "tolerances": asdict(TOL),
                "ac_options": dict(config.ac.options),
                "execution": {"test": True},
            }
        )
        payload = execute_attempt(directory, inputs, policy, config, request)
        assert payload["audit"]["accepted"], payload["audit"]
        independently = audit_result(
            inputs,
            policy,
            window,
            arm,
            payload["result"],
            TOL,
            repair_budget_mwh=request["repair_budget_mwh"],
            reported_common_cost=payload["common_cost_expression"],
        )
        assert jsonable(independently) == payload["audit"]
        assert set(payload["solution_values"]) == set(
            load_retained_start(directory / "start.json").assigned
        )
        source = reference(directory / "result.json")
    assert len(calls) == 4


def test_renewable_lock_and_reactive_freedom(matched):
    from cvxopf import NondispatchableUnit
    import pandas as pd

    inputs, policy, _, window, result = matched
    nd = NondispatchableUnit(
        bus=1, p_available=5.0, apparent_power_rating=10.0, device_id="nd"
    )
    inputs = replace(
        inputs,
        nondispatchable=(nd,),
        df_nd=pd.DataFrame({"nd": [5.0] * 6}, index=inputs.df_load_p.index),
    )
    window = replace(
        window,
        input_sha256=streaming.execution_input_sha256(inputs),
        renewable_mw=np.zeros((3, 1)),
    )
    result = dict(
        result,
        p_nd=np.zeros((3, 1)),
        q_nd=np.zeros((3, 1)),
        curtailment=np.full((3, 1), 5.0),
    )
    assert audit_result(
        inputs, policy, window, "G", result, TOL, reported_common_cost=0.0
    )["accepted"]
    changed = dict(result, p_nd=np.ones((3, 1)))
    audit = audit_result(
        inputs, policy, window, "B", changed, TOL, reported_common_cost=0.0
    )
    assert audit["residuals"]["renewable_lock_mw_abs"] == 1.0
    for arm in ARMS:
        model = build_arm(
            inputs, policy, window, arm, repair_budget_mwh=1.0 if arm == "R2" else None
        )
        lock = next(
            c
            for c in model.build.prob.constraints
            if c.shape == (3, 1)
            and {v.id for v in c.variables()}
            == {v.id for v in model.build.variables["p_nd"]}
        )
        assert not {v.id for v in model.build.variables["q_nd"]} & {
            v.id for v in lock.variables()
        }
