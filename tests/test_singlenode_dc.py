"""
Tests for the singlenode_dc formulation.

This file accumulates tests across multiple steps of the implementation plan.
"""

import numpy as np
import pytest
import pandas as pd

from cvxopf.singlenode_dc_problem import _parse_singlenode_dc_case
from cvxopf.problem import OPFOptions, build_opf, build_opf_multistep, OPFBuild
from cvxopf.testcases import case9, make_singlenode_case
from cvxopf.storage import StorageUnitIdeal
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.results import extract_results
import cvxpy as cp
import numpy as np
import pytest


# Shared test data
GENS = [
    {"P_max_MW": 150.0, "cost_coeffs": (10.0, 2.0, 0.01)},
    {"P_max_MW": 100.0, "cost_coeffs": (5.0,  3.0, 0.02), "P_min_MW": 20.0},
]

SIMPLE_GENS = [
    {"P_max_MW": 200.0, "cost_coeffs": (0.0, 1.0, 0.01)},
    {"P_max_MW": 200.0, "cost_coeffs": (0.0, 2.0, 0.02)},
]


class TestMakeSinglenodeCase:
    """Step 1: Test the make_singlenode_case convenience constructor."""

    def test_returns_dict_with_required_keys(self):
        result = make_singlenode_case(250.0, GENS)
        assert isinstance(result, dict)
        assert "baseMVA" in result
        assert "bus" in result
        assert "gen" in result
        assert "gencost" in result
        assert "branch" in result

    def test_baseMVA_stored_correctly(self):
        result = make_singlenode_case(250.0, GENS, baseMVA=100.0)
        assert result["baseMVA"] == 100.0

    def test_bus_shape_single_row(self):
        result = make_singlenode_case(250.0, GENS)
        assert result["bus"].shape == (1, 13)

    def test_bus_type_is_3(self):
        result = make_singlenode_case(250.0, GENS)
        assert result["bus"][0, 1] == 3

    def test_bus_PD_set_correctly(self):
        result = make_singlenode_case(250.0, GENS)
        assert result["bus"][0, 2] == 250.0

    def test_bus_id_is_1(self):
        result = make_singlenode_case(250.0, GENS)
        assert result["bus"][0, 0] == 1

    def test_gen_shape_one_row_per_generator(self):
        result = make_singlenode_case(250.0, GENS)
        assert result["gen"].shape == (2, 21)

    def test_gen_status_is_1(self):
        result = make_singlenode_case(250.0, GENS)
        assert np.all(result["gen"][:, 7] == 1)

    def test_gen_pmax_stored_correctly(self):
        result = make_singlenode_case(250.0, GENS)
        assert result["gen"][0, 8] == 150.0

    def test_gen_pmin_default_zero(self):
        result = make_singlenode_case(250.0, GENS)
        assert result["gen"][0, 9] == 0.0

    def test_gen_pmin_custom(self):
        result = make_singlenode_case(250.0, GENS)
        assert result["gen"][1, 9] == 20.0

    def test_gen_bus_is_1(self):
        result = make_singlenode_case(250.0, GENS)
        assert np.all(result["gen"][:, 0] == 1)

    def test_gencost_shape(self):
        result = make_singlenode_case(250.0, GENS)
        assert result["gencost"].shape == (2, 7)

    def test_gencost_model_is_2(self):
        result = make_singlenode_case(250.0, GENS)
        assert np.all(result["gencost"][:, 0] == 2)

    def test_gencost_ncost_is_3(self):
        result = make_singlenode_case(250.0, GENS)
        assert np.all(result["gencost"][:, 3] == 3)

    def test_gencost_coeffs_stored_correctly(self):
        result = make_singlenode_case(250.0, GENS)
        # MATPOWER format: c2, c1, c0 (quadratic, linear, constant)
        assert result["gencost"][0, 4] == 0.01  # c2
        assert result["gencost"][0, 5] == 2.0   # c1
        assert result["gencost"][0, 6] == 10.0  # c0

    def test_branch_shape_zero_rows(self):
        result = make_singlenode_case(250.0, GENS)
        assert result["branch"].shape == (0, 13)

    def test_single_generator(self):
        single_gen = [{"P_max_MW": 100.0, "cost_coeffs": (5.0, 1.0, 0.01)}]
        result = make_singlenode_case(50.0, single_gen)
        assert result["gen"].shape == (1, 21)
        assert result["gencost"].shape == (1, 7)

    def test_custom_baseMVA(self):
        result = make_singlenode_case(100.0, GENS, baseMVA=50.0)
        assert result["baseMVA"] == 50.0
        assert result["gen"][0, 6] == 50.0  # MBASE column


class TestParseSinglenodeDcCase:
    """Step 2: Test the _parse_singlenode_dc_case function."""

    def test_parse_from_make_singlenode_case(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert isinstance(d, dict)

    def test_parse_from_case9(self):
        d = _parse_singlenode_dc_case(case9(), OPFOptions(), None, 1.0, None)
        assert isinstance(d, dict)

    def test_pd_total_scalar(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert isinstance(d["Pd_total"], float)

    def test_pd_total_from_make_singlenode_case(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        expected = 250.0 / 100.0  # P_load_MW / baseMVA
        assert d["Pd_total"] == pytest.approx(expected)

    def test_pd_total_from_case9(self):
        d = _parse_singlenode_dc_case(case9(), OPFOptions(), None, 1.0, None)
        case9_bus = case9()["bus"]
        expected = np.sum(case9_bus[:, 2]) / 100.0  # sum(PD) / baseMVA
        assert d["Pd_total"] == pytest.approx(expected)

    def test_ng_correct_from_make_singlenode_case(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert d["ng"] == 2

    def test_ng_correct_from_case9(self):
        d = _parse_singlenode_dc_case(case9(), OPFOptions(), None, 1.0, None)
        assert d["ng"] == 3

    def test_nb_correct_from_make_singlenode_case(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert d["nb"] == 1

    def test_nb_correct_from_case9(self):
        d = _parse_singlenode_dc_case(case9(), OPFOptions(), None, 1.0, None)
        assert d["nb"] == 9

    def test_pgmin_shape(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert d["Pgmin"].shape == (d["ng"],)

    def test_pgmax_shape(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert d["Pgmax"].shape == (d["ng"],)

    def test_pgmax_in_per_unit(self):
        d = _parse_singlenode_dc_case(case9(), OPFOptions(), None, 1.0, None)
        # case9 baseMVA is 100, so Pgmax values should be < 5.0 (not in MW)
        assert np.all(d["Pgmax"] < 5.0)

    def test_no_A_in_result(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert "A" not in d

    def test_no_nl_in_result(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert "nl" not in d

    def test_no_nogen_buses_in_result(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert "nogen_buses" not in d

    def test_no_loss_weight_in_result(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert "loss_weight" not in d

    def test_no_gen_bus_in_result(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert "gen_bus" not in d

    def test_ns_absent_when_no_storage(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert "ns" not in d

    def test_nnd_absent_when_no_nondispatchable(self):
        case = make_singlenode_case(250.0, GENS)
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, None)
        assert "nnd" not in d

    def test_storage_data_present_when_storage_given(self):
        # Use single-generator case for simplicity
        single_gen = [{"P_max_MW": 100.0, "cost_coeffs": (5.0, 1.0, 0.01)}]
        case = make_singlenode_case(100.0, single_gen)
        storage = [StorageUnitIdeal(bus=1, apparent_power_rating=50.0, 
                                    capacity=100.0, initial_soc=50.0, aging_weight=0.0)]
        d = _parse_singlenode_dc_case(case, OPFOptions(), storage, 1.0, None)
        assert "ns" in d
        assert d["ns"] == 1

    def test_nondispatchable_data_present_when_nd_given(self):
        # Use single-generator case for simplicity
        single_gen = [{"P_max_MW": 100.0, "cost_coeffs": (5.0, 1.0, 0.01)}]
        case = make_singlenode_case(100.0, single_gen)
        nd_unit = [NondispatchableUnit(bus=1, p_available=80.0, apparent_power_rating=100.0)]
        d = _parse_singlenode_dc_case(case, OPFOptions(), None, 1.0, nd_unit)
        assert "nnd" in d
        assert d["nnd"] == 1

    def test_build_opf_multistep_singlenode_dc_raises_not_implemented(self):
        # Create minimal df_P and df_Q with 1 row and 9 columns
        df_P = pd.DataFrame(np.zeros((1, 9)))
        df_Q = pd.DataFrame(np.zeros((1, 9)))
        with pytest.raises(NotImplementedError, match="singlenode_dc multistep builder not yet implemented"):
            build_opf_multistep(case9(), df_P, df_Q, T=1, formulation="singlenode_dc")


class TestSinglenodeDcSingleReturnType:
    """Step 3: Return type and structure of the single-step builder."""

    def test_returns_opfbuild(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert isinstance(build, OPFBuild)

    def test_formulation_field(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert build.formulation == "singlenode_dc"

    def test_is_convex_true(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert build.is_convex is True

    def test_variables_has_Pg(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert "Pg" in build.variables

    def test_variables_no_p_flows(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert "p_flows" not in build.variables

    def test_variables_no_p_gen(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert "p_gen" not in build.variables

    def test_data_has_Pd_total(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert "Pd_total" in build.data

    def test_data_Pd_total_is_float(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert isinstance(build.data["Pd_total"], float)

    def test_data_no_A(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert "A" not in build.data

    def test_data_no_nl(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert "nl" not in build.data

    def test_data_no_loss_weight(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert "loss_weight" not in build.data

    def test_data_no_ns_when_no_storage(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert "ns" not in build.data

    def test_data_no_nnd_when_no_nd(self):
        build = build_opf(case9(), formulation="singlenode_dc")
        assert "nnd" not in build.data


def _solve_single(case_fn=case9, options=None, storage=None, delta=1.0,
                  nondispatchable=None):
    build = build_opf(case_fn(), formulation="singlenode_dc",
                      options=options, storage=storage, delta=delta,
                      nondispatchable=nondispatchable)
    build.solve()
    return build, extract_results(build)


class TestSinglenodeDcSingleSolve:
    """Step 3: Solve behaviour on a full MATPOWER case (case9)."""

    def test_solve_status_optimal_case9(self):
        _, results = _solve_single()
        assert results["status"] == "optimal"

    def test_solve_uses_clarabel(self):
        build, _ = _solve_single()
        assert "clarabel" in build.prob.solver_stats.solver_name.lower()

    def test_objective_is_positive(self):
        _, results = _solve_single()
        assert results["objective"] > 0

    def test_objective_is_float(self):
        _, results = _solve_single()
        assert isinstance(results["objective"], float)

    def test_Pg_shape_case9(self):
        _, results = _solve_single()
        assert results["Pg"].shape == (3,)

    def test_Pg_nonneg(self):
        _, results = _solve_single()
        assert np.all(results["Pg"] >= -1e-4)

    def test_Pg_within_Pgmax(self):
        build, results = _solve_single()
        assert np.all(results["Pg"] <= build.data["Pgmax"] * build.data["baseMVA"] + 1e-4)

    def test_p_net_is_float(self):
        _, results = _solve_single()
        assert isinstance(results["p_net"], float)

    def test_p_net_near_zero(self):
        _, results = _solve_single()
        assert abs(results["p_net"]) < 0.1

    def test_Vm_absent(self):
        _, results = _solve_single()
        assert "Vm" not in results

    def test_Qg_absent(self):
        _, results = _solve_single()
        assert "Qg" not in results

    def test_p_flows_absent(self):
        _, results = _solve_single()
        assert "p_flows" not in results

    def test_b_absent_when_no_storage(self):
        _, results = _solve_single()
        assert "b" not in results

    def test_p_nd_absent_when_no_nd(self):
        _, results = _solve_single()
        assert "p_nd" not in results


class TestSinglenodeDcSingleFromMakeSinglenodeCase:
    """Step 3: Analytically verifiable results from make_singlenode_case."""

    def _solve(self, P_load_MW, gens=SIMPLE_GENS):
        build = build_opf(make_singlenode_case(P_load_MW, gens),
                          formulation="singlenode_dc")
        build.solve()
        return build, extract_results(build)

    def test_solves_optimal(self):
        _, results = self._solve(100.0)
        assert results["status"] == "optimal"

    def test_Pg_shape(self):
        _, results = self._solve(100.0)
        assert results["Pg"].shape == (2,)

    def test_power_balance_satisfied(self):
        _, results = self._solve(100.0)
        assert abs(np.sum(results["Pg"]) - 100.0) < 0.1

    def test_cheaper_generator_dispatched_more(self):
        _, results = self._solve(100.0)
        # generator 0 has lower marginal cost
        assert results["Pg"][0] >= results["Pg"][1] - 1.0

    def test_zero_load_zero_generation(self):
        _, results = self._solve(0.0)
        assert np.sum(results["Pg"]) < 0.1

    def test_infeasible_returns_status(self):
        # load exceeds sum of Pmax (200 + 200 = 400)
        _, results = self._solve(500.0)
        assert results["Pg"] is None


class TestSinglenodeDcSingleStorage:
    """Step 3: Single-step with a storage unit."""

    STORAGE = [StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                capacity=100.0, initial_soc=50.0,
                                aging_weight=0.0)]

    def _solve(self):
        build = build_opf(make_singlenode_case(100.0, SIMPLE_GENS),
                          formulation="singlenode_dc",
                          storage=self.STORAGE, delta=1.0)
        build.solve()
        return build, extract_results(build)

    def test_storage_solves_optimal(self):
        _, results = self._solve()
        assert results["status"] == "optimal"

    def test_b_in_results(self):
        _, results = self._solve()
        assert "b" in results

    def test_soc_in_results(self):
        _, results = self._solve()
        assert "soc" in results

    def test_b_q_absent(self):
        _, results = self._solve()
        assert "b_q" not in results

    def test_storage_cost_in_results(self):
        _, results = self._solve()
        assert "storage_cost" in results

    def test_ns_in_data(self):
        build, _ = self._solve()
        assert "ns" in build.data
        assert build.data["ns"] == 1

    def test_b_shape(self):
        _, results = self._solve()
        assert results["b"].shape == (1,)

    def test_soc_shape(self):
        _, results = self._solve()
        assert results["soc"].shape == (1,)

    def test_soc_initial_condition(self):
        _, results = self._solve()
        initial_soc = 50.0
        delta = 1.0
        assert abs(results["soc"][0] - (initial_soc - results["b"][0] * delta)) < 1e-4

    def test_real_power_bound_satisfied(self):
        _, results = self._solve()
        S_max = 50.0
        assert results["b"][0] >= -S_max - 1e-4
        assert results["b"][0] <= S_max + 1e-4

    def test_soc_within_capacity(self):
        _, results = self._solve()
        capacity = 100.0
        assert results["soc"][0] >= -1e-3
        assert results["soc"][0] <= capacity + 1e-3

    def test_ns_absent_when_no_storage(self):
        build = build_opf(make_singlenode_case(100.0, SIMPLE_GENS),
                          formulation="singlenode_dc")
        assert "ns" not in build.data


class TestSinglenodeDcSingleNondispatchable:
    """Step 3: Single-step with a nondispatchable unit."""

    ND = [NondispatchableUnit(bus=1, p_available=80.0, apparent_power_rating=100.0)]

    def _solve(self):
        build = build_opf(make_singlenode_case(100.0, SIMPLE_GENS),
                          formulation="singlenode_dc",
                          nondispatchable=self.ND)
        build.solve()
        return build, extract_results(build)

    def test_nd_solves_optimal(self):
        _, results = self._solve()
        assert results["status"] == "optimal"

    def test_p_nd_in_results(self):
        _, results = self._solve()
        assert "p_nd" in results

    def test_curtailment_in_results(self):
        _, results = self._solve()
        assert "curtailment" in results

    def test_q_nd_absent(self):
        _, results = self._solve()
        assert "q_nd" not in results

    def test_nnd_in_data(self):
        build, _ = self._solve()
        assert "nnd" in build.data
        assert build.data["nnd"] == 1

    def test_p_nd_nonneg(self):
        _, results = self._solve()
        assert results["p_nd"][0] >= -1e-4

    def test_p_nd_le_p_available(self):
        _, results = self._solve()
        assert results["p_nd"][0] <= 80.0 + 1e-4

    def test_curtailment_nonneg(self):
        _, results = self._solve()
        assert results["curtailment"][0] >= -1e-4

    def test_nd_reduces_conventional_generation(self):
        _, results_nd = self._solve()
        build_no_nd = build_opf(make_singlenode_case(100.0, SIMPLE_GENS),
                                formulation="singlenode_dc")
        build_no_nd.solve()
        results_no_nd = extract_results(build_no_nd)
        assert np.sum(results_nd["Pg"]) < np.sum(results_no_nd["Pg"]) + 1.0

    def test_nnd_absent_when_no_nd(self):
        build = build_opf(make_singlenode_case(100.0, SIMPLE_GENS),
                          formulation="singlenode_dc")
        assert "nnd" not in build.data