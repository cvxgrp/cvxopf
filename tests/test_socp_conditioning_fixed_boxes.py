"""Solver-free qualification of the narrowly scoped exact-box experiment."""

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import generator, nondispatchable
from experiments.socp_conditioning.diagnostic import (
    build_variant,
    exact_box,
    generator_fixed_boxes,
    smoke_inputs,
)


def test_mixed_box_exact_not_near_fixed():
    x = cp.Variable((2, 2))
    constraints = exact_box(x, 0, [[0, 1e-12], [2, 0]])
    assert [c.size for c in constraints] == [2, 2, 2]
    x.value = np.array([[0, 5e-13], [1, 0]])
    assert max(np.max(c.violation()) for c in constraints) == 0
    x.value = np.array([[1, 5e-13], [1, 0]])
    assert np.max(constraints[0].violation()) == 1


@pytest.mark.parametrize("upper,sizes", [(0, [6]), (1, [6, 6])])
def test_all_fixed_or_all_free(upper, sizes):
    assert [c.size for c in exact_box(cp.Variable((2, 3)), 0, upper)] == sizes


def test_invalid_box_rejected():
    with pytest.raises(ValueError):
        exact_box(cp.Variable(1), 1, 0)


def test_generator_reactive_capability_preserved():
    p, q = cp.Variable((1, 2)), cp.Variable((1, 2))
    constraints = generator_fixed_boxes(p, q, 0, 0, -3, 3)
    p.value, q.value = np.zeros((1, 2)), np.array([[2, -2]])
    assert max(np.max(c.violation()) for c in constraints) == 0


def test_build_keeps_objective_schema_and_restores_hooks():
    gen_hook = generator.ac_operating_constraints
    nd_hook = nondispatchable.vectorized_ac_operating_constraints
    build, original, divisor = build_variant(smoke_inputs(), "fixed_boxes")
    assert build.prob.is_dcp() and divisor == 1
    assert original is build.prob.objective.expr
    assert generator.ac_operating_constraints is gen_hook
    assert nondispatchable.vectorized_ac_operating_constraints is nd_hook
    assert build.variables["b_q"].shape == (1, 3)
