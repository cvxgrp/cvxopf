"""Solver-free equivalence checks for the canonical scaling experiment."""

from types import SimpleNamespace

import numpy as np
import pytest
from scipy import sparse

from experiments.socp_conditioning import cone_scaling as c
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import optimality_audit as oa


def fixture():
    layout = (1, 2, [3, 3])
    rng = np.random.default_rng(2)
    A = sparse.csc_matrix(rng.normal(size=(9, 4)) * np.array([0.001, 1, 10, 100]))
    text = "1 equalities, 2 inequalities, 0 exponential cones,\nSOC constraints: [3, 3], PSD constraints: [],\n 3d power cones [], []."
    return dict(
        A=A,
        P=sparse.diags([1.0, 2.0, 3.0, 4.0], format="csc"),
        b=rng.normal(size=9),
        c=rng.normal(size=4),
        dims=text,
    ), layout


def test_scaling_preserves_all_algebraic_identities_and_original_data():
    data, layout = fixture()
    originals = {k: data[k].copy() for k in ("A", "P", "b", "c")}
    R, D = c.scales(data["A"], layout)
    result = c.verify_mapping(data, c.transform(data, R, D), R, D)
    assert max(result.values()) < 1e-11
    for key in ("A", "P"):
        assert (data[key] != originals[key]).nnz == 0
    for key in ("b", "c"):
        np.testing.assert_array_equal(data[key], originals[key])
    np.testing.assert_array_equal(c.scales(data["A"], layout)[0], R)
    assert R[3] == R[4] == R[5]
    assert R[6] == R[7] == R[8]


@pytest.mark.parametrize("dual", [False, True])
def test_cone_membership_in_both_directions(dual):
    data, layout = fixture()
    R, _ = c.scales(data["A"], layout)
    v = np.array([0, 1, 2, 5, 3, 4, 4, 1, 2.0], dtype=float)
    if dual:
        v[0] = -10  # Equality dual is free.
    for scale in (R, 1 / R):
        assert max(oa.cone_errors(scale * v, layout, dual=dual).values()) < 1e-10
        bad = v.copy()
        bad[3] = 1
        assert oa.cone_errors(scale * bad, layout, dual=dual)["soc_violation"] > 0


def test_unequal_soc_row_scales_are_rejected():
    data, layout = fixture()
    R, D = c.scales(data["A"], layout)
    R[4] *= 2
    with pytest.raises(ValueError, match="SOC coordinates"):
        c.transform(data, R, D)


@pytest.mark.parametrize("bad", [0, -1, np.nan, np.inf])
def test_noninvertible_or_nonfinite_scale_rejected(bad):
    data, layout = fixture()
    R, D = c.scales(data["A"], layout)
    D[0] = bad
    with pytest.raises(ValueError, match="strictly positive"):
        c.transform(data, R, D)


def test_zero_rows_and_columns_remain_finite():
    A = sparse.csr_matrix([[0.0, 0.0], [100.0, 0.0]])
    R, D = c.scales(A, (0, 2, []))
    assert R[0] == D[1] == 1
    assert np.all(np.isfinite(R)) and np.all(np.isfinite(D))


def test_primal_slack_dual_result_mapping():
    R, D = np.array([2.0, 4.0]), np.array([3.0, 5.0])
    raw = SimpleNamespace(
        x=[1.0, 2.0],
        s=[6.0, 8.0],
        z=[7.0, 9.0],
        status="Solved",
        obj_val=3.0,
        obj_val_dual=2.9,
        r_prim=1e-8,
        r_dual=2e-8,
        iterations=10,
        solve_time=0.1,
    )
    mapped = c.mapped_solution(raw, R, D)
    np.testing.assert_array_equal(mapped.x, [3.0, 10.0])
    np.testing.assert_array_equal(mapped.s, [3.0, 2.0])
    np.testing.assert_array_equal(mapped.z, [14.0, 36.0])
    assert mapped.obj_val == raw.obj_val
    assert mapped.s @ mapped.z == np.array(raw.s) @ raw.z


def test_real_cvxpy_model_canonical_roundtrip_without_solve():
    build, _, _ = d.build_variant(d.smoke_inputs(), "cones")
    data, _, _ = build.prob.get_problem_data(
        "CLARABEL", canon_backend=build.canonicalization_backend
    )
    layout = oa.cone_layout(str(data["dims"]), data["A"].shape[0])
    R, D = c.scales(data["A"], layout)
    assert max(c.verify_mapping(data, c.transform(data, R, D), R, D).values()) < 1e-11
    assert build.prob.status is None


def test_offline_wider_spectrum_known_diagonal():
    from experiments.socp_conditioning.cone_scaling_analysis import wider_spectrum

    result = wider_spectrum(sparse.diags([1.0, 10.0, 100.0], format="csc"))
    assert result["status"] == "estimated_not_certified"
    assert np.isclose(result["condition_2"], 100.0)
    assert result["singular_triplet_residual"] < 1e-8
