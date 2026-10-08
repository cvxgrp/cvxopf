"""The diagnostic changes one stopping guard, not its acceptance tolerances."""

import pytest

from experiments.socp_conditioning import minimum_step as m


def test_exactly_one_setting_changes():
    baseline = m.BASE_OPTIONS("CLARABEL")
    actual = m.options("CLARABEL")
    assert actual.pop("min_terminate_step_length") == 1e-8
    assert actual == baseline
    assert actual["max_iter"] == 5000
    assert actual["tol_gap_abs"] == actual["reduced_tol_gap_abs"] == 1e-10


@pytest.mark.parametrize("solver", ["COPT", "MOSEK"])
def test_no_other_solver_or_extra_arm(solver):
    with pytest.raises(ValueError, match="CLARABEL only"):
        m.options(solver)
