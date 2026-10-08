"""Exact canonical cleanup and fixed three-arm routing, with no solves."""

import numpy as np
import pytest
from scipy import sparse

from experiments.socp_conditioning import joint_fixed_boxes as f
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import joint_scaling as j


def dims(eq, nonneg):
    return f"{eq} equalities, {nonneg} inequalities, 0 exponential cones,\nSOC constraints: [3], PSD constraints: [],\n 3d power cones [], []."


def fixture():
    old = dict(
        A=sparse.csc_matrix([[0, 1], [-1, 0], [1, 0], [0, -1], [0, 0], [1, 0], [0, 1]]),
        b=np.array([2.0, -3, 3, 0, 10, 0, 0]),
        c=np.ones(2),
        P=sparse.eye(2, format="csc"),
        dims=dims(1, 3),
    )
    new = old | dict(
        A=sparse.csc_matrix([[0, 1], [1, 0], [0, -1], [0, 0], [1, 0], [0, 1]]),
        b=np.array([2.0, 3, 0, 10, 0, 0]),
        dims=dims(2, 1),
    )
    variables = [
        dict(name="Pg", shape=[1], offset=0),
        dict(name="w", shape=[1], offset=1),
    ]
    return old, new, variables


def test_exact_reduction():
    old, new, variables = fixture()
    result = f.check_cleanup(old, new, variables)
    assert result["fixed_coordinates"] == 1
    assert result["removed_inequalities"] == 2


@pytest.mark.parametrize("row", [0, 1, 2, 3, 4])
def test_changed_constraint_rejected(row):
    old, new, variables = fixture()
    new["b"][row] += 0.01
    with pytest.raises((ValueError, AssertionError)):
        f.check_cleanup(old, new, variables)


def test_near_coincident_not_equal():
    old, new, variables = fixture()
    old["b"][2] += 1e-12
    with pytest.raises(ValueError):
        f.check_cleanup(old, new, variables)


def test_objective_change_rejected():
    old, new, variables = fixture()
    new["c"] = new["c"] + 0.1
    with pytest.raises(AssertionError):
        f.check_cleanup(old, new, variables)


def test_real_names_and_shapes_remain_bound():
    v = [dict(name="Pg", shape=[3], offset=0), dict(name="var12", shape=[2], offset=3)]
    assert f.variable_schema(v) == f.variable_schema([v[0], v[1] | dict(name="var400")])
    assert f.variable_schema(v) != f.variable_schema([v[0] | dict(name="Qg"), v[1]])


def test_frozen_rule_and_three_cases(monkeypatch, tmp_path):
    assert d.sha(j.__file__) == f.FROZEN_RULE_SHA256
    calls = []
    monkeypatch.setattr(
        f, "BASE_WORKER", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    for label in f.ARMS:
        f.worker(tmp_path, label, label)
    assert [x[1]["case_name"] for x in calls] == ["surplus", "surplus", "deficit"]
    assert all(
        x[1]["model_variant"] == "fixed_boxes"
        and x[1]["scaling_strategy"] is j.joint_scales
        for x in calls
    )
    assert [x[0][2].startswith("fixed") for x in calls] == [False, True, False]
