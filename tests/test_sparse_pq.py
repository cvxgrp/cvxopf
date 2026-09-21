"""
Tests for OPFOptions.sparse_pq — sparse vs dense P/Q variable formulations.

Verifies:
  - Default is sparse_pq=True
  - Correct variable keys and shapes for both paths
  - Numerical equivalence of sparse and dense results
  - Multi-step behaviour
  - No effect on DC formulation
"""

import cvxpy as cp
import numpy as np
import pytest

from cvxopf.testcases import case9, case14, case118
from cvxopf.problem import build_opf, build_opf_multistep, OPFOptions
from cvxopf.results import extract_results


# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------

OBJ_RTOL = 1e-4   # 0.01% relative tolerance on objective
VAL_ATOL = 1e-3   # absolute tolerance for Pg, Vm, Va comparisons


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_ac(case_fn, sparse_pq):
    return build_opf(case_fn(), formulation="ac",
                     options=OPFOptions(sparse_pq=sparse_pq))


def _solve_ac(case_fn, sparse_pq):
    build = _build_ac(case_fn, sparse_pq)
    build.solve()
    return build, extract_results(build)


def _flat_load_dfs(case_fn, T):
    ppc     = case_fn()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    import pandas as pd
    df_P = pd.DataFrame(np.tile(Pd_base, (T, 1)))
    df_Q = pd.DataFrame(np.tile(Qd_base, (T, 1)))
    return df_P, df_Q


ASSEMBLY_HORIZONS = [
    ("single", 1), ("stepwise", 1), ("stepwise", 3),
    ("vectorized", 1), ("vectorized", 3),
]


def _build_pq_case(case_fn, sparse, assembly, horizon):
    if assembly == "single":
        return _build_ac(case_fn, sparse_pq=sparse)
    df_P, df_Q = _flat_load_dfs(case_fn, horizon)
    scales = np.linspace(0.8, 1.2, horizon)
    return build_opf_multistep(
        case_fn(), df_P.mul(scales, axis=0), df_Q.mul(scales, axis=0),
        T=horizon, options=OPFOptions(sparse_pq=sparse),
        temporal_assembly=assembly,
    )


@pytest.mark.parametrize("case_fn", [case9, case14, case118])
@pytest.mark.parametrize("sparse", [True, False])
@pytest.mark.parametrize("assembly,horizon", ASSEMBLY_HORIZONS)
def test_pq_definitions_are_batched_over_all_spatial_entries(
    case_fn, sparse, assembly, horizon,
):
    """Count P/Q defining objects, not total graph objects or scalar equations.

    The former per-entry time-vectorized loop also had counts independent of
    T, so horizon-invariance alone does not enforce spatial vectorization.
    """
    build = _build_pq_case(case_fn, sparse, assembly, horizon)
    nnz = len(build.data["rows"])
    shape = (nnz, horizon) if assembly == "vectorized" else (nnz,)
    keys = ("P_vec", "Q_vec") if sparse else ("P", "Q")
    for key in keys:
        variables = build.variables[key]
        if assembly != "stepwise":
            variables = [variables]
        assert len(variables) == (horizon if assembly == "stepwise" else 1)
        for variable in variables:
            definitions = [
                constraint for constraint in build.prob.constraints
                if isinstance(constraint, cp.constraints.Equality)
                and {v.id for v in constraint.args[0].variables()} == {variable.id}
                and not constraint.args[1].is_affine()
            ]
            assert len(definitions) == 1
            assert definitions[0].shape == shape
            if not sparse:
                zeros = [
                    constraint for constraint in build.prob.constraints
                    if isinstance(constraint, cp.constraints.Equality)
                    and {v.id for v in constraint.args[0].variables()} == {variable.id}
                    and constraint.args[1].is_constant()
                ]
                assert len(zeros) == 1
                zero_count = len(build.data["Z"][0])
                assert zeros[0].shape == (
                    (zero_count, horizon) if assembly == "vectorized"
                    else (zero_count,)
                )


@pytest.mark.parametrize("case_fn,sparse", [
    (case9, True), (case9, False), (case14, True), (case14, False),
    (case118, True),
])
@pytest.mark.parametrize("assembly,horizon", ASSEMBLY_HORIZONS)
def test_gather_flows_match_complex_power(case_fn, sparse, assembly, horizon):
    """Audit repeated-index gathers against complex Ybus power, including shunts.

    This exercises the DNLP derivative path behind CVXPY issue #3442 and
    checks the solved P/Q entries independently of the trigonometric model.
    """
    build = _build_pq_case(case_fn, sparse, assembly, horizon)
    build.solve(max_iter=400)
    assert build.prob.status == "optimal"
    rows, cols = build.data["rows"], build.data["cols"]
    assert len(np.unique(rows)) < len(rows)
    assert np.any(rows == cols)
    result = extract_results(build)
    voltage = np.atleast_2d(result["Vm"]) * np.exp(
        1j * np.deg2rad(np.atleast_2d(result["Va_deg"]))
    )
    for t, phasor in enumerate(voltage):
        power = phasor[:, None] * np.conj(build.data["Ybus"] * phasor[None, :])
        keys = ("P_vec", "Q_vec") if sparse else ("P", "Q")
        for key, expected in zip(keys, (power.real, power.imag), strict=True):
            variable = build.variables[key]
            if assembly == "stepwise":
                actual = variable[t].value
            elif assembly == "vectorized":
                actual = variable.value[:, t]
            else:
                actual = variable.value
            if sparse:
                expected = expected[rows, cols]
            else:
                actual = actual.reshape(expected.shape)
            np.testing.assert_allclose(actual, expected, atol=1e-6)
        for key, expected in (("p_net", power.real), ("q_net", power.imag)):
            np.testing.assert_allclose(
                np.atleast_2d(result[key])[t],
                build.data["baseMVA"] * expected.sum(axis=1), atol=1e-4,
            )


# ---------------------------------------------------------------------------
# Default option value
# ---------------------------------------------------------------------------

class TestSparsePQDefault:

    def test_default_sparse_pq_is_true(self):
        opts = OPFOptions()
        assert opts.sparse_pq is True

    def test_default_build_uses_sparse_keys(self):
        build = build_opf(case9(), formulation="ac")
        assert "P_vec" in build.variables
        assert "Q_vec" in build.variables
        assert "P"     not in build.variables
        assert "Q"     not in build.variables


# ---------------------------------------------------------------------------
# Variable keys
# ---------------------------------------------------------------------------

class TestSparsePQKeys:

    def test_sparse_true_has_P_vec_Q_vec(self):
        build = _build_ac(case9, sparse_pq=True)
        assert "P_vec" in build.variables
        assert "Q_vec" in build.variables

    def test_sparse_true_has_no_P_Q(self):
        build = _build_ac(case9, sparse_pq=True)
        assert "P" not in build.variables
        assert "Q" not in build.variables

    def test_sparse_false_has_P_Q(self):
        build = _build_ac(case9, sparse_pq=False)
        assert "P" in build.variables
        assert "Q" in build.variables

    def test_sparse_false_has_no_P_vec_Q_vec(self):
        build = _build_ac(case9, sparse_pq=False)
        assert "P_vec" not in build.variables
        assert "Q_vec" not in build.variables

    def test_common_keys_present_in_both(self):
        common = {"theta", "v", "p", "q", "Pg", "Qg"}
        for sparse_pq in [True, False]:
            build = _build_ac(case9, sparse_pq=sparse_pq)
            assert common.issubset(set(build.variables.keys()))


# ---------------------------------------------------------------------------
# Variable shapes
# ---------------------------------------------------------------------------

class TestSparsePQShapes:

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_P_vec_shape_is_nnz(self, case_fn, nb, ng):
        build = _build_ac(case_fn, sparse_pq=True)
        nnz   = len(build.data["rows"])
        assert build.variables["P_vec"].shape == (nnz,)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_Q_vec_shape_is_nnz(self, case_fn, nb, ng):
        build = _build_ac(case_fn, sparse_pq=True)
        nnz   = len(build.data["rows"])
        assert build.variables["Q_vec"].shape == (nnz,)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_P_shape_is_nb_nb_dense(self, case_fn, nb, ng):
        build = _build_ac(case_fn, sparse_pq=False)
        assert build.variables["P"].shape == (nb, nb)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_Q_shape_is_nb_nb_dense(self, case_fn, nb, ng):
        build = _build_ac(case_fn, sparse_pq=False)
        assert build.variables["Q"].shape == (nb, nb)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_nnz_less_than_nb_squared(self, case_fn, nb, ng):
        """Sparse formulation must have fewer P/Q variables than dense."""
        build_s = _build_ac(case_fn, sparse_pq=True)
        nnz     = len(build_s.data["rows"])
        assert nnz < nb * nb

    def test_data_contains_rows_cols_G_vec_B_vec_Rp(self):
        build = _build_ac(case9, sparse_pq=True)
        for key in ("rows", "cols", "G_vec", "B_vec", "Rp"):
            assert key in build.data, f"build.data missing '{key}'"

    def test_Rp_shape(self):
        build = _build_ac(case9, sparse_pq=True)
        nb    = build.data["nb"]
        nnz   = len(build.data["rows"])
        assert build.data["Rp"].shape == (nb, nnz)


# ---------------------------------------------------------------------------
# Numerical equivalence: sparse vs dense
# ---------------------------------------------------------------------------

class TestSparsePQNumerics:

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_objective_sparse_matches_dense(self, case_fn):
        _, r_sparse = _solve_ac(case_fn, sparse_pq=True)
        _, r_dense  = _solve_ac(case_fn, sparse_pq=False)
        assert r_sparse["status"] == "optimal"
        assert r_dense["status"]  == "optimal"
        assert abs(r_sparse["objective"] - r_dense["objective"]) \
               / abs(r_dense["objective"]) < OBJ_RTOL

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_Pg_sparse_matches_dense(self, case_fn):
        _, r_sparse = _solve_ac(case_fn, sparse_pq=True)
        _, r_dense  = _solve_ac(case_fn, sparse_pq=False)
        np.testing.assert_allclose(r_sparse["Pg"], r_dense["Pg"], atol=VAL_ATOL)

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_Qg_sparse_matches_dense(self, case_fn):
        _, r_sparse = _solve_ac(case_fn, sparse_pq=True)
        _, r_dense  = _solve_ac(case_fn, sparse_pq=False)
        np.testing.assert_allclose(r_sparse["Qg"], r_dense["Qg"], atol=VAL_ATOL)

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_Vm_sparse_matches_dense(self, case_fn):
        _, r_sparse = _solve_ac(case_fn, sparse_pq=True)
        _, r_dense  = _solve_ac(case_fn, sparse_pq=False)
        np.testing.assert_allclose(r_sparse["Vm"], r_dense["Vm"], atol=VAL_ATOL)

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_Va_sparse_matches_dense(self, case_fn):
        _, r_sparse = _solve_ac(case_fn, sparse_pq=True)
        _, r_dense  = _solve_ac(case_fn, sparse_pq=False)
        np.testing.assert_allclose(
            r_sparse["Va_deg"], r_dense["Va_deg"], atol=VAL_ATOL
        )


# ---------------------------------------------------------------------------
# Multi-step
# ---------------------------------------------------------------------------

class TestSparsePQMultistep:

    def test_multistep_sparse_variable_lists_have_P_vec_Q_vec(self):
        T          = 3
        df_P, df_Q = _flat_load_dfs(case9, T)
        build      = build_opf_multistep(
            case9(), df_P, df_Q, T=T,
            options=OPFOptions(sparse_pq=True),
        )
        assert "P_vec" in build.variables
        assert "Q_vec" in build.variables
        assert isinstance(build.variables["P_vec"], list)
        assert len(build.variables["P_vec"]) == T

    def test_multistep_dense_variable_lists_have_P_Q(self):
        T          = 3
        df_P, df_Q = _flat_load_dfs(case9, T)
        build      = build_opf_multistep(
            case9(), df_P, df_Q, T=T,
            options=OPFOptions(sparse_pq=False),
        )
        assert "P" in build.variables
        assert "Q" in build.variables
        assert isinstance(build.variables["P"], list)
        assert len(build.variables["P"]) == T

    def test_multistep_objective_sparse_matches_dense(self):
        T          = 3
        df_P, df_Q = _flat_load_dfs(case9, T)

        build_s = build_opf_multistep(
            case9(), df_P, df_Q, T=T,
            options=OPFOptions(sparse_pq=True),
        )
        build_s.solve()
        r_sparse = extract_results(build_s)

        build_d = build_opf_multistep(
            case9(), df_P, df_Q, T=T,
            options=OPFOptions(sparse_pq=False),
        )
        build_d.solve()
        r_dense = extract_results(build_d)

        assert r_sparse["status"] == "optimal"
        assert r_dense["status"]  == "optimal"
        assert abs(r_sparse["objective"] - r_dense["objective"]) \
               / abs(r_dense["objective"]) < OBJ_RTOL

    def test_multistep_Pg_sparse_matches_dense(self):
        T          = 3
        df_P, df_Q = _flat_load_dfs(case9, T)

        build_s = build_opf_multistep(
            case9(), df_P, df_Q, T=T,
            options=OPFOptions(sparse_pq=True),
        )
        build_s.solve()
        r_sparse = extract_results(build_s)

        build_d = build_opf_multistep(
            case9(), df_P, df_Q, T=T,
            options=OPFOptions(sparse_pq=False),
        )
        build_d.solve()
        r_dense = extract_results(build_d)

        np.testing.assert_allclose(r_sparse["Pg"], r_dense["Pg"], atol=VAL_ATOL)


# ---------------------------------------------------------------------------
# DC formulation: sparse_pq has no effect
# ---------------------------------------------------------------------------

class TestSparsePQDCNoEffect:

    def test_dc_sparse_pq_true_still_uses_Pg_p_flows(self):
        """sparse_pq=True is silently ignored for DC formulation."""
        build = build_opf(case9(), formulation="lossy_dc",
                          options=OPFOptions(sparse_pq=True))
        assert "Pg"      in build.variables
        assert "p_flows" in build.variables
        assert "P_vec"   not in build.variables
        assert "P"       not in build.variables

    def test_dc_sparse_pq_false_still_uses_Pg_p_flows(self):
        build = build_opf(case9(), formulation="lossy_dc",
                          options=OPFOptions(sparse_pq=False))
        assert "Pg"      in build.variables
        assert "p_flows" in build.variables
