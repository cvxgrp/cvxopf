"""Non-solving controls for four original optional-shedding conditions."""

from copy import deepcopy
from unittest.mock import patch

import numpy as np
import pytest

from experiments.socp_conditioning import four_conditions as f


@pytest.mark.parametrize("method", f.METHODS)
def test_only_full_relative_gap_changes(method):
    assert f.options(method) == f.b.options(method) | dict(tol_gap_rel=1e-6)
    assert f.options(method)["tol_feas"] == 1e-10
    assert f.options(method)["tol_gap_abs"] == 1e-10
    assert f.options(method)["reduced_tol_gap_rel"] == 1e-10


def test_four_original_energy_neutral_windows():
    assert [v[0] for v in f.CASES.values()] == [2, 6, 14, 18]
    assert all(v[3] - v[2] == 24 for v in f.CASES.values())
    assert f.CASES["ramp_up"][1] == "Surplus to deficit"
    assert f.CASES["ramp_down"][1] == "Deficit to surplus"


def test_preparation_preserves_optional_shedding_without_solving():
    kwargs = f.d.smoke_inputs()
    # A true exactly fixed coordinate permits the same substitution path.
    kwargs["df_nd"].iloc[0, 0] = 0
    digest = f.d.input_digest(kwargs)
    with patch("cvxopf.problem.OPFBuild.solve", side_effect=AssertionError("no solve")):
        build, _, data, _, reduced, _, R, D, scaled, check = f.prepare(kwargs)
    assert f.d.input_digest(kwargs) == digest
    assert "load_shedding_cost" in build.expressions
    assert "load_shed_fraction" in build.variables
    assert len(reduced.fixed) > 0
    assert data["A"].shape[1] > scaled["A"].shape[1]
    assert np.all(R > 0) and np.all(D > 0)
    assert max(check.values()) < 1e-11


def test_native_success_does_not_override_physical_or_accounting_checks():
    r = dict(
        exception=None,
        solver_exception=None,
        native=dict(
            status="Solved",
            gap_abs=1e-7,
            gap_rel=1e-7,
            res_primal=1e-12,
            res_dual=1e-12,
            ktratio=1e-12,
        ),
        solver_options=f.options("qdldl"),
        audit=dict(passed=True),
        objective_reconstruction_error=1e-6,
    )
    assert f.accepted(r)
    for update in (
        dict(audit=dict(passed=False)),
        dict(objective_reconstruction_error=1e-3),
        dict(native=r["native"] | dict(status="AlmostSolved")),
        dict(solver_exception="failed"),
    ):
        assert not f.accepted(deepcopy(r) | update)
