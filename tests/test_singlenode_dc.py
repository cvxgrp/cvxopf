"""
Tests for the singlenode_dc formulation.

This file accumulates tests across multiple steps of the implementation plan.
"""

import numpy as np
import pytest

from cvxopf.testcases import make_singlenode_case


# Shared test data
GENS = [
    {"P_max_MW": 150.0, "cost_coeffs": (10.0, 2.0, 0.01)},
    {"P_max_MW": 100.0, "cost_coeffs": (5.0,  3.0, 0.02), "P_min_MW": 20.0},
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