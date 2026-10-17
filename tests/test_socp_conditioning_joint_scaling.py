"""Joint-data scaling semantics without optimization calls."""

import numpy as np
from scipy import sparse

from experiments.socp_conditioning import cone_scaling as c
from experiments.socp_conditioning import joint_scaling as j
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import optimality_audit as oa


def data():
    return dict(
        A=sparse.eye(3, format="csc"),
        P=sparse.eye(3, format="csc"),
        c=np.ones(3),
        b=np.ones(3),
    )


def test_joint_scale_responds_to_each_non_A_block():
    base = data()
    R, D = j.joint_scales(base, (0, 3, []))
    np.testing.assert_array_equal(R, np.ones(3))
    np.testing.assert_array_equal(D, np.ones(3))
    for key in ("P", "c", "b"):
        changed = base | {key: base[key] * 1e4}
        rr, dd = j.joint_scales(changed, (0, 3, []))
        assert not (np.array_equal(R, rr) and np.array_equal(D, dd))
        if key == "b":
            assert np.max(rr) < 0.01
        else:
            assert np.max(dd) < 0.1


def test_joint_P_scaling_does_not_inflate_large_curvature():
    base = data() | {
        "A": sparse.eye(3, format="csc") * 1e-3,
        "P": sparse.eye(3, format="csc") * 1e4,
    }
    _, D = j.joint_scales(base, (0, 3, []))
    assert np.max(base["P"].diagonal() * D**2) <= 1.000001


def test_zero_data_has_identity_scaling():
    base = data() | {
        "A": sparse.csc_matrix((3, 3)),
        "P": sparse.csc_matrix((3, 3)),
        "b": np.zeros(3),
        "c": np.zeros(3),
    }
    R, D = j.joint_scales(base, (0, 3, []))
    np.testing.assert_array_equal(R, np.ones(3))
    np.testing.assert_array_equal(D, np.ones(3))


def test_real_canonical_data_equivalence_and_no_mutation():
    build, _, _ = d.build_variant(d.smoke_inputs(), "cones")
    source, _, _ = build.prob.get_problem_data(
        "CLARABEL", canon_backend=build.canonicalization_backend
    )
    before = {k: source[k].copy() for k in ("A", "P", "b", "c")}
    layout = oa.cone_layout(str(source["dims"]), source["A"].shape[0])
    R, D = j.joint_scales(source, layout)
    assert (
        max(c.verify_mapping(source, c.transform(source, R, D), R, D).values()) < 1e-11
    )
    np.testing.assert_array_equal(j.joint_scales(source, layout)[0], R)
    for key in ("A", "P"):
        assert (source[key] != before[key]).nnz == 0
    for key in ("b", "c"):
        np.testing.assert_array_equal(source[key], before[key])
    assert np.all((R >= c.SCALE_MIN) & (R <= c.SCALE_MAX))
    assert np.all((D >= c.SCALE_MIN) & (D <= c.SCALE_MAX))
    assert build.prob.status is None
