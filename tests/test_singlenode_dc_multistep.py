"""
Tests for the multistep singlenode_dc formulation (Step 4).
"""

import warnings
import numpy as np
import pandas as pd
import pytest

from cvxopf.testcases import case9, make_singlenode_case
from cvxopf.problem import (
    build_opf, build_opf_multistep, OPFBuild, OPFOptions,
    StorageUnitIdeal,
)
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.results import extract_results


SIMPLE_GENS = [
    {"P_max_MW": 200.0, "cost_coeffs": (0.0, 1.0, 0.01)},
    {"P_max_MW": 200.0, "cost_coeffs": (0.0, 2.0, 0.02)},
]

OBJ_RTOL = 1e-4
VAL_ATOL = 1e-3
SOC_ATOL = 1e-4


def _flat_load_dfs(case_fn, T):
    ppc     = case_fn()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    df_P    = pd.DataFrame(np.tile(Pd_base, (T, 1)))
    df_Q    = pd.DataFrame(np.tile(Qd_base, (T, 1)))
    return df_P, df_Q


def _singlenode_load_dfs(P_load_MW, T):
    """Return (df_P, df_Q) with T identical rows for a single-bus case."""
    df_P = pd.DataFrame(np.full((T, 1), P_load_MW))
    df_Q = pd.DataFrame(np.zeros((T, 1)))
    return df_P, df_Q


def _solve_multistep(case, df_P, df_Q, T, storage=None, delta=1.0,
                     nondispatchable=None, df_nd=None,
                     coupling_constraints=None):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf_multistep(
            case, df_P, df_Q, T=T,
            formulation="singlenode_dc",
            storage=storage, delta=delta,
            nondispatchable=nondispatchable, df_nd=df_nd,
            coupling_constraints=coupling_constraints,
        )
    build.solve()
    return build, extract_results(build)


def _solve_single(case, storage=None, delta=1.0, nondispatchable=None):
    build = build_opf(case, formulation="singlenode_dc",
                      storage=storage, delta=delta,
                      nondispatchable=nondispatchable)
    build.solve()
    return build, extract_results(build)


class TestSinglenodeDcMultistepReturnType:

    def _build(self, T=3):
        df_P, df_Q = _flat_load_dfs(case9, T)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            return build_opf_multistep(case9(), df_P, df_Q, T=T,
                                       formulation="singlenode_dc")

    def test_returns_opfbuild(self):
        assert isinstance(self._build(), OPFBuild)

    def test_formulation_field(self):
        assert self._build().formulation == "singlenode_dc"

    def test_is_convex_true(self):
        assert self._build().is_convex is True

    def test_Pg_variable_list_length_T(self):
        build = self._build()
        assert isinstance(build.variables["Pg"], list)
        assert len(build.variables["Pg"]) == 3

    def test_data_contains_T(self):
        assert self._build().data["T"] == 3

    def test_data_contains_Pd_series(self):
        assert "Pd_series" in self._build().data

    def test_Pd_series_shape_is_T(self):
        assert self._build().data["Pd_series"].shape == (3,)

    def test_no_p_flows_in_variables(self):
        assert "p_flows" not in self._build().variables

    def test_no_p_gen_in_variables(self):
        assert "p_gen" not in self._build().variables


class TestSinglenodeDcMultistepInputValidation:

    def test_T_mismatch_raises(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        with pytest.raises(ValueError, match="T=5"):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                build_opf_multistep(case9(), df_P, df_Q, T=5,
                                    formulation="singlenode_dc")

    def test_wrong_nb_columns_raises(self):
        df_P = pd.DataFrame(np.zeros((3, 4)))  # case9 has 9 buses
        df_Q = pd.DataFrame(np.zeros((3, 4)))
        with pytest.raises(ValueError, match="columns"):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                build_opf_multistep(case9(), df_P, df_Q, T=3,
                                    formulation="singlenode_dc")

    def test_df_Q_ignored_emits_warning(self):
        df_P, df_Q = _flat_load_dfs(case9, 2)
        with pytest.warns(UserWarning, match="df_Q is ignored"):
            build_opf_multistep(case9(), df_P, df_Q, T=2,
                                formulation="singlenode_dc")


class TestSinglenodeDcT1Equivalence:

    def test_objective_matches_single_step_case9(self):
        df_P, df_Q = _flat_load_dfs(case9, 1)
        _, r_multi = _solve_multistep(case9(), df_P, df_Q, 1)
        _, r_single = _solve_single(case9())
        assert r_multi["objective"] == pytest.approx(r_single["objective"], rel=OBJ_RTOL)

    def test_Pg_matches_single_step_case9(self):
        df_P, df_Q = _flat_load_dfs(case9, 1)
        _, r_multi = _solve_multistep(case9(), df_P, df_Q, 1)
        _, r_single = _solve_single(case9())
        assert np.allclose(r_multi["Pg"][0], r_single["Pg"], atol=VAL_ATOL)

    def test_p_net_matches_single_step_case9(self):
        df_P, df_Q = _flat_load_dfs(case9, 1)
        _, r_multi = _solve_multistep(case9(), df_P, df_Q, 1)
        _, r_single = _solve_single(case9())
        assert r_multi["p_net"][0] == pytest.approx(r_single["p_net"], abs=VAL_ATOL)

    def test_objective_matches_single_step_singlenode_case(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 1)
        _, r_multi = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                      df_P, df_Q, 1)
        _, r_single = _solve_single(make_singlenode_case(100.0, SIMPLE_GENS))
        assert r_multi["objective"] == pytest.approx(r_single["objective"], rel=OBJ_RTOL)

    def test_Pg_matches_single_step_singlenode_case(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 1)
        _, r_multi = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                      df_P, df_Q, 1)
        _, r_single = _solve_single(make_singlenode_case(100.0, SIMPLE_GENS))
        assert np.allclose(r_multi["Pg"][0], r_single["Pg"], atol=VAL_ATOL)


class TestSinglenodeDcMultistepResultShapes:

    def test_Pg_shape_T1(self):
        df_P, df_Q = _flat_load_dfs(case9, 1)
        _, r = _solve_multistep(case9(), df_P, df_Q, 1)
        assert r["Pg"].shape == (1, 3)

    def test_Pg_shape_T3(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        _, r = _solve_multistep(case9(), df_P, df_Q, 3)
        assert r["Pg"].shape == (3, 3)

    def test_p_net_shape_T1(self):
        df_P, df_Q = _flat_load_dfs(case9, 1)
        _, r = _solve_multistep(case9(), df_P, df_Q, 1)
        assert r["p_net"].shape == (1,)

    def test_p_net_shape_T3(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        _, r = _solve_multistep(case9(), df_P, df_Q, 3)
        assert r["p_net"].shape == (3,)

    def test_p_net_near_zero_all_steps(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        _, r = _solve_multistep(case9(), df_P, df_Q, 3)
        assert np.all(np.abs(r["p_net"]) < 0.5)

    def test_Vm_absent(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        _, r = _solve_multistep(case9(), df_P, df_Q, 3)
        assert "Vm" not in r

    def test_Qg_absent(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        _, r = _solve_multistep(case9(), df_P, df_Q, 3)
        assert "Qg" not in r

    def test_p_flows_absent(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        _, r = _solve_multistep(case9(), df_P, df_Q, 3)
        assert "p_flows" not in r


class TestSinglenodeDcFlatLoad:

    def test_flat_load_total_objective_is_T_times_single(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        _, r_multi = _solve_multistep(case9(), df_P, df_Q, 3)
        _, r_single = _solve_single(case9())
        assert r_multi["objective"] == pytest.approx(3 * r_single["objective"], rel=OBJ_RTOL)

    def test_flat_load_Pg_identical_across_steps(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        _, r = _solve_multistep(case9(), df_P, df_Q, 3)
        assert np.allclose(r["Pg"][0], r["Pg"][1], atol=VAL_ATOL)
        assert np.allclose(r["Pg"][1], r["Pg"][2], atol=VAL_ATOL)

    def test_varying_load_Pg_distinct_across_steps(self):
        ppc     = case9()
        Pd_base = ppc["bus"][:, 2].copy()
        scales  = np.array([0.8, 1.0, 1.2])
        df_P    = pd.DataFrame(np.outer(scales, Pd_base))
        df_Q    = pd.DataFrame(np.zeros((3, 9)))
        _, r = _solve_multistep(case9(), df_P, df_Q, 3)
        assert not np.allclose(r["Pg"][0], r["Pg"][2], atol=1.0)


class TestSinglenodeDcMultistepStorage:

    STORAGE = [StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                capacity=100.0, initial_soc=50.0,
                                aging_weight=0.0)]

    def _solve(self, T=3):
        df_P, df_Q = _singlenode_load_dfs(100.0, T)
        return _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                df_P, df_Q, T, storage=self.STORAGE, delta=1.0)

    def test_storage_solves_optimal(self):
        _, r = self._solve()
        assert r["status"] == "optimal"

    def test_b_shape_T_ns(self):
        _, r = self._solve()
        assert r["b"].shape == (3, 1)

    def test_soc_shape_T_ns(self):
        _, r = self._solve()
        assert r["soc"].shape == (3, 1)

    def test_b_q_absent(self):
        _, r = self._solve()
        assert "b_q" not in r

    def test_soc_initial_condition(self):
        _, r = self._solve()
        initial_soc = 50.0
        delta = 1.0
        assert abs(r["soc"][0, 0] - (initial_soc - r["b"][0, 0] * delta)) < SOC_ATOL

    def test_soc_dynamics_all_steps(self):
        _, r = self._solve()
        delta = 1.0
        for t in range(1, 3):
            assert abs(r["soc"][t, 0] - (r["soc"][t - 1, 0] - r["b"][t, 0] * delta)) < SOC_ATOL

    def test_soc_within_capacity_all_steps(self):
        _, r = self._solve()
        assert np.all(r["soc"] >= -VAL_ATOL)
        assert np.all(r["soc"] <= 100.0 + VAL_ATOL)

    def test_real_power_bound_all_steps(self):
        _, r = self._solve()
        assert np.all(r["b"] >= -50.0 - VAL_ATOL)
        assert np.all(r["b"] <= 50.0 + VAL_ATOL)

    def test_storage_cost_in_results(self):
        _, r = self._solve()
        assert "storage_cost" in r

    def test_T1_objective_matches_single_step(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 1)
        _, r_multi = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                      df_P, df_Q, 1, storage=self.STORAGE, delta=1.0)
        _, r_single = _solve_single(make_singlenode_case(100.0, SIMPLE_GENS),
                                    storage=self.STORAGE, delta=1.0)
        assert r_multi["objective"] == pytest.approx(r_single["objective"], rel=OBJ_RTOL)

    def test_T1_b_matches_single_step(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 1)
        _, r_multi = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                      df_P, df_Q, 1, storage=self.STORAGE, delta=1.0)
        _, r_single = _solve_single(make_singlenode_case(100.0, SIMPLE_GENS),
                                    storage=self.STORAGE, delta=1.0)
        assert np.allclose(r_multi["b"][0], r_single["b"], atol=VAL_ATOL)

    def test_T1_soc_matches_single_step(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 1)
        _, r_multi = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                      df_P, df_Q, 1, storage=self.STORAGE, delta=1.0)
        _, r_single = _solve_single(make_singlenode_case(100.0, SIMPLE_GENS),
                                    storage=self.STORAGE, delta=1.0)
        assert np.allclose(r_multi["soc"][0], r_single["soc"], atol=VAL_ATOL)


class TestSinglenodeDcMultistepNondispatchable:

    ND = [NondispatchableUnit(bus=1, p_available=80.0, apparent_power_rating=100.0)]

    def _df_nd(self, values):
        return pd.DataFrame({1: values})

    def test_nd_solves_optimal(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 3)
        df_nd = self._df_nd([80.0, 80.0, 80.0])
        _, r = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                df_P, df_Q, 3, nondispatchable=self.ND, df_nd=df_nd)
        assert r["status"] == "optimal"

    def test_p_nd_shape_T_nnd(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 3)
        df_nd = self._df_nd([80.0, 80.0, 80.0])
        _, r = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                df_P, df_Q, 3, nondispatchable=self.ND, df_nd=df_nd)
        assert r["p_nd"].shape == (3, 1)

    def test_q_nd_absent(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 3)
        df_nd = self._df_nd([80.0, 80.0, 80.0])
        _, r = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                df_P, df_Q, 3, nondispatchable=self.ND, df_nd=df_nd)
        assert "q_nd" not in r

    def test_curtailment_shape(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 3)
        df_nd = self._df_nd([80.0, 80.0, 80.0])
        _, r = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                df_P, df_Q, 3, nondispatchable=self.ND, df_nd=df_nd)
        assert r["curtailment"].shape == (3, 1)

    def test_curtailment_nonneg_all_steps(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 3)
        df_nd = self._df_nd([80.0, 80.0, 80.0])
        _, r = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                df_P, df_Q, 3, nondispatchable=self.ND, df_nd=df_nd)
        assert np.all(r["curtailment"] >= -VAL_ATOL)

    def test_p_nd_le_available_all_steps(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 3)
        df_nd = self._df_nd([80.0, 80.0, 80.0])
        _, r = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                df_P, df_Q, 3, nondispatchable=self.ND, df_nd=df_nd)
        assert np.all(r["p_nd"] <= df_nd.values + VAL_ATOL)

    def test_varying_availability_respected(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 3)
        df_nd = self._df_nd([100.0, 75.0, 50.0])
        _, r = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                df_P, df_Q, 3, nondispatchable=self.ND, df_nd=df_nd)
        for t in range(3):
            assert r["p_nd"][t, 0] <= df_nd.iloc[t, 0] + VAL_ATOL

    def test_df_nd_none_warning_emitted(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 3)
        with pytest.warns(UserWarning, match="df_nd not provided"):
            build_opf_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                df_P, df_Q, T=3, formulation="singlenode_dc",
                                nondispatchable=self.ND, df_nd=None)

    def test_T1_p_nd_matches_single_step(self):
        df_P, df_Q = _singlenode_load_dfs(100.0, 1)
        df_nd = self._df_nd([80.0])
        _, r_multi = _solve_multistep(make_singlenode_case(100.0, SIMPLE_GENS),
                                      df_P, df_Q, 1, nondispatchable=self.ND, df_nd=df_nd)
        _, r_single = _solve_single(make_singlenode_case(100.0, SIMPLE_GENS),
                                    nondispatchable=self.ND)
        assert np.allclose(r_multi["p_nd"][0], r_single["p_nd"], atol=VAL_ATOL)
