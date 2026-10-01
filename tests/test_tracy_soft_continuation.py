"""No-solve checks for incumbent selection and complete primal handoff."""

import numpy as np
import pytest

from cvxopf import build_opf_multistep
from cvxopf._ac_start_mapping import variables_by_name
from experiments.case118_tracy_2021 import soft_target_continuation as c
from tests.test_tracy_stage_d import fixture


def candidate(cost, sse, feasible=True):
    return dict(operating_cost=cost, sse_mwh2=sse, physically_feasible=feasible)


def test_incumbent_uses_new_objective_and_keeps_better_point():
    a, b = candidate(100, 2), candidate(0, 3)
    assert c.incumbent([a, b], 1) is b
    assert c.incumbent([a, b], 1000) is a
    assert c.incumbent([a, candidate(0, 0, False)], 1000) is a
    assert c.incumbent([a, candidate(100, 2)], 1000) is a
    assert c.best_tracking([a, b]) is a


def test_native_and_archived_primal_roundtrip():
    kwargs, _, result, named = fixture(w=1)
    build = build_opf_multistep(**kwargs)
    payload = dict(result=result, named_costs=named, weight=None)
    values, errors = c.complete_primal(build, payload, [5.0])
    assert set(values) == set(variables_by_name(build))
    assert max(errors.values()) < 1e-10
    assert values["Pg"][0, 0] == .07
    assert values["b_q"].shape == (1, 1)
    assert values["soc"].shape == (1, 2)
    again, _ = c.complete_primal(build, dict(payload, native_primal=values), [5.0])
    for key in values:
        np.testing.assert_array_equal(values[key], again[key])
    bad = dict(values)
    del bad["Qg"]
    with pytest.raises(ValueError, match="primal namespace"):
        c.complete_primal(build, dict(payload, native_primal=bad), [5.0])


def test_physical_feasibility_separate_from_status_and_hard_gate():
    kwargs, _, result, named = fixture(w=1)
    result["status"] = "user_limit"
    checked = c.evaluate(dict(result=result, named_costs=named, weight=None),
                         kwargs, dict(target_soc_mwh=[4.0]))
    assert checked["physically_feasible"]
    assert not checked["audit"]["passed"]
    assert checked["sse_mwh2"] == 1
    assert c.model_kwargs(kwargs, None) is kwargs
    soft = c.model_kwargs(kwargs, 100)
    assert soft["storage"][0].terminal_constraint is None
    assert soft["storage"][0].terminal_cost == "quadratic"
    assert kwargs["storage"][0].terminal_constraint == "equality"
    assert soft["loads"] is kwargs["loads"]


def test_material_difference_not_objective_based():
    r = dict(soc=[[1.]], Vm=[[1.]], Pg=[[10.]], Qg=[[2.]])
    assert not c.materially_different(dict(result=r), dict(result=r))
    assert c.materially_different(dict(result=r), dict(result=dict(r, soc=[[3.]])))
