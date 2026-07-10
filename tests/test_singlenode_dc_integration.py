"""
End-to-end integration and cross-formulation tests for singlenode_dc (Step 6).
"""

import subprocess
import sys
import warnings

import numpy as np
import pandas as pd
import pytest
import cvxpy as cp

import sys
from pathlib import Path

from cvxopf.testcases import case9, case14, make_singlenode_case
from cvxopf.problem import (
    build_opf, build_opf_multistep, OPFBuild, OPFOptions,
    StorageUnitIdeal,
)
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.results import extract_results, compare_to_reference


OBJ_RTOL  = 1e-4
VAL_ATOL  = 1e-3
SOC_ATOL  = 1e-4

SIMPLE_GENS = [
    {"P_max_MW": 200.0, "cost_coeffs": (0.0, 1.0, 0.01)},
    {"P_max_MW": 200.0, "cost_coeffs": (0.0, 2.0, 0.02)},
]


def _flat_load_dfs(case_fn, T):
    ppc     = case_fn()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    df_P    = pd.DataFrame(np.tile(Pd_base, (T, 1)))
    df_Q    = pd.DataFrame(np.tile(Qd_base, (T, 1)))
    return df_P, df_Q


def _solve_singlenode(case, storage=None, delta=1.0, nondispatchable=None):
    build = build_opf(case, formulation="singlenode_dc",
                      storage=storage, delta=delta,
                      nondispatchable=nondispatchable)
    build.solve()
    return build, extract_results(build)


def _solve_lossy_dc(case, storage=None, delta=1.0, nondispatchable=None):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf(case, formulation="lossy_dc",
                          storage=storage, delta=delta,
                          nondispatchable=nondispatchable)
    build.solve()
    return build, extract_results(build)


def _solve_singlenode_multistep(case, df_P, df_Q, T,
                                 storage=None, delta=1.0,
                                 nondispatchable=None, df_nd=None):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf_multistep(
            case, df_P, df_Q, T=T,
            formulation="singlenode_dc",
            storage=storage, delta=delta,
            nondispatchable=nondispatchable, df_nd=df_nd,
        )
    build.solve()
    return build, extract_results(build)


class TestSinglenodeDcVsLossyDC:

    def test_total_Pg_close_to_lossy_dc_case9(self):
        _, r_sn = _solve_singlenode(case9())
        _, r_dc = _solve_lossy_dc(case9())
        assert abs(np.sum(r_sn["Pg"]) - np.sum(r_dc["Pg"])) < 5.0

    def test_total_Pg_close_to_lossy_dc_case14(self):
        _, r_sn = _solve_singlenode(case14())
        _, r_dc = _solve_lossy_dc(case14())
        assert abs(np.sum(r_sn["Pg"]) - np.sum(r_dc["Pg"])) < 5.0

    def test_singlenode_objective_le_lossydc_objective(self):
        _, r_sn = _solve_singlenode(case9())
        _, r_dc = _solve_lossy_dc(case9())
        assert r_sn["objective"] <= r_dc["objective"] + 1.0

    def test_singlenode_has_no_p_flows(self):
        _, r_sn = _solve_singlenode(case9())
        assert "p_flows" not in r_sn

    def test_lossydc_has_p_flows(self):
        _, r_dc = _solve_lossy_dc(case9())
        assert "p_flows" in r_dc


class TestSinglenodeDcCase14:

    def test_case14_single_step_solves_optimal(self):
        _, r = _solve_singlenode(case14())
        assert r["status"] == "optimal"

    def test_case14_Pg_shape(self):
        _, r = _solve_singlenode(case14())
        assert r["Pg"].shape == (5,)

    def test_case14_Pg_nonneg(self):
        _, r = _solve_singlenode(case14())
        assert np.all(r["Pg"] >= -VAL_ATOL)

    def test_case14_p_net_near_zero(self):
        _, r = _solve_singlenode(case14())
        assert abs(r["p_net"]) < 0.5

    def test_case14_multistep_T3_solves_optimal(self):
        df_P, df_Q = _flat_load_dfs(case14, 3)
        _, r = _solve_singlenode_multistep(case14(), df_P, df_Q, 3)
        assert r["status"] == "optimal"

    def test_case14_multistep_Pg_shape(self):
        df_P, df_Q = _flat_load_dfs(case14, 3)
        _, r = _solve_singlenode_multistep(case14(), df_P, df_Q, 3)
        assert r["Pg"].shape == (3, 5)


class TestSinglenodeDcExtractResultsDispatch:

    def test_extract_ac_still_works(self):
        build = build_opf(case9(), formulation="ac")
        build.solve()
        results = extract_results(build)
        assert results["status"] == "optimal"
        assert "Vm" in results

    def test_extract_lossy_dc_still_works(self):
        _, results = _solve_lossy_dc(case9())
        assert results["status"] == "optimal"
        assert "p_flows" in results

    def test_extract_singlenode_dc_works(self):
        _, results = _solve_singlenode(case9())
        assert results["status"] == "optimal"
        assert "Pg" in results

    def _dummy_build(self):
        return OPFBuild(
            prob=cp.Problem(cp.Minimize(0)),
            variables={},
            data={},
            formulation="unknown",
            is_convex=True,
        )

    def test_unknown_formulation_raises(self):
        with pytest.raises(ValueError):
            extract_results(self._dummy_build())

    def test_singlenode_dc_in_error_message(self):
        with pytest.raises(ValueError) as exc_info:
            extract_results(self._dummy_build())
        assert "singlenode_dc" in str(exc_info.value)


class TestSinglenodeDcStorageIntegration:

    STORAGE = [StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                capacity=100.0, initial_soc=50.0,
                                aging_weight=0.0)]
    ND = [NondispatchableUnit(bus=1, p_available=80.0, apparent_power_rating=100.0)]

    def test_storage_and_nd_together_single_step(self):
        _, r = _solve_singlenode(make_singlenode_case(100.0, SIMPLE_GENS),
                                 storage=self.STORAGE, nondispatchable=self.ND)
        assert r["status"] == "optimal"
        assert "b" in r
        assert "p_nd" in r

    def test_storage_and_nd_together_multistep(self):
        df_P = pd.DataFrame(np.full((3, 1), 100.0))
        df_Q = pd.DataFrame(np.zeros((3, 1)))
        df_nd = pd.DataFrame({1: [80.0, 80.0, 80.0]})
        _, r = _solve_singlenode_multistep(
            make_singlenode_case(100.0, SIMPLE_GENS), df_P, df_Q, 3,
            storage=self.STORAGE, nondispatchable=self.ND, df_nd=df_nd,
        )
        assert r["status"] == "optimal"
        assert r["b"].shape == (3, 1)
        assert r["p_nd"].shape == (3, 1)

    def test_fully_charged_storage_cannot_charge(self):
        storage = [StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                    capacity=100.0, initial_soc=100.0,
                                    aging_weight=0.0)]
        _, r = _solve_singlenode(make_singlenode_case(100.0, SIMPLE_GENS),
                                 storage=storage)
        assert r["b"][0] >= -VAL_ATOL

    def test_fully_discharged_storage_cannot_discharge(self):
        storage = [StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                    capacity=100.0, initial_soc=0.0,
                                    aging_weight=0.0)]
        _, r = _solve_singlenode(make_singlenode_case(100.0, SIMPLE_GENS),
                                 storage=storage)
        assert r["b"][0] <= VAL_ATOL

    def test_aging_weight_reduces_cycling_multistep(self):
        df_P = pd.DataFrame([[200.0 * 0.8], [200.0 * 1.0], [200.0 * 1.2]])
        df_Q = pd.DataFrame(np.zeros((3, 1)))
        case = make_singlenode_case(200.0, SIMPLE_GENS)

        storage_free = [StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                         capacity=100.0, initial_soc=50.0,
                                         aging_weight=0.0)]
        storage_aged = [StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                         capacity=100.0, initial_soc=50.0,
                                         aging_weight=5.0)]
        _, r_free = _solve_singlenode_multistep(case, df_P, df_Q, 3,
                                                storage=storage_free)
        _, r_aged = _solve_singlenode_multistep(case, df_P, df_Q, 3,
                                                storage=storage_aged)
        assert np.sum(np.abs(r_aged["b"])) <= np.sum(np.abs(r_free["b"])) + VAL_ATOL


class TestSinglenodeDcLongHorizon:

    def _sinusoidal_dfs(self, T=24):
        Pd_base = case9()["bus"][:, 2].copy()
        load_scales = 1.0 + 0.2 * np.sin(np.linspace(0, 2 * np.pi, T))
        df_P = pd.DataFrame(np.outer(load_scales, Pd_base))
        df_Q = pd.DataFrame(np.zeros((T, 9)))
        return df_P, df_Q

    def test_T24_solves_optimal(self):
        df_P, df_Q = self._sinusoidal_dfs(24)
        _, r = _solve_singlenode_multistep(case9(), df_P, df_Q, 24)
        assert r["status"] == "optimal"

    def test_T24_Pg_shape(self):
        df_P, df_Q = self._sinusoidal_dfs(24)
        _, r = _solve_singlenode_multistep(case9(), df_P, df_Q, 24)
        assert r["Pg"].shape == (24, 3)

    def test_T24_p_net_near_zero_all_steps(self):
        df_P, df_Q = self._sinusoidal_dfs(24)
        _, r = _solve_singlenode_multistep(case9(), df_P, df_Q, 24)
        assert np.all(np.abs(r["p_net"]) < 0.5)

    def test_T24_with_storage_solves_optimal(self):
        df_P, df_Q = self._sinusoidal_dfs(24)
        storage = [StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                    capacity=100.0, initial_soc=50.0,
                                    aging_weight=0.0)]
        _, r = _solve_singlenode_multistep(case9(), df_P, df_Q, 24,
                                           storage=storage)
        assert r["status"] == "optimal"

    def test_T24_soc_within_capacity_all_steps(self):
        df_P, df_Q = self._sinusoidal_dfs(24)
        storage = [StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                    capacity=100.0, initial_soc=50.0,
                                    aging_weight=0.0)]
        _, r = _solve_singlenode_multistep(case9(), df_P, df_Q, 24,
                                           storage=storage)
        assert np.all(r["soc"] >= -VAL_ATOL)
        assert np.all(r["soc"] <= 100.0 + VAL_ATOL)


class TestSinglenodeDcExampleScript:

    def test_example_script_runs_without_error(self):
        script = Path(__file__).parent.parent / "examples" / "case9_singlenode_dc.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"Example script failed with returncode {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_example_script_prints_optimal(self):
        script = Path(__file__).parent.parent / "examples" / "case9_singlenode_dc.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True,
        )
        assert "optimal" in result.stdout.lower(), (
            f"Expected 'optimal' in output.\nstdout: {result.stdout}"
        )
