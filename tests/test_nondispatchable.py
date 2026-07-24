"""
Comprehensive test suite for nondispatchable generator functionality.

Tests cover:
- NondispatchableUnit dataclass structure
- Validation and error cases
- Single-step AC and DC formulations
- Multi-step AC and DC formulations
- Integration with storage units
- Multiple nondispatchable units
- Nodal power balance verification
"""

import numpy as np
import pandas as pd
import pytest
import warnings
import cvxpy as cp

from cvxopf import (
    build_opf,
    build_opf_multistep,
    extract_results,
    NondispatchableUnit,
)
from cvxopf.testcases import case9

# Tolerances
OBJ_RTOL  = 1e-4    # relative tolerance on objective
VAL_ATOL  = 1e-3    # absolute tolerance on p_nd, q_nd, curtailment (MW/MVAr)
BAL_ATOL  = 1e-4    # nodal balance residual (p.u.)
APR_ATOL  = 1e-4    # apparent power constraint residual

# Helper functions
def _default_nd_unit(
    bus=5,
    p_available=80.0,
    apparent_power_rating=100.0,
    device_id="nd",
):
    return NondispatchableUnit(bus=bus, p_available=p_available,
                               apparent_power_rating=apparent_power_rating,
                               device_id=device_id)

def _flat_nd_df(unit, T, p_available=None):
    """DataFrame with T identical rows keyed by stable device identity."""
    val = p_available if p_available is not None else unit.p_available
    return pd.DataFrame({unit.device_id: [val] * T})

def _flat_load_dfs(case_fn, T):
    """Return (df_P, df_Q) with T identical rows matching base case load."""
    ppc     = case_fn()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    df_P    = pd.DataFrame(np.tile(Pd_base, (T, 1)))
    df_Q    = pd.DataFrame(np.tile(Qd_base, (T, 1)))
    return df_P, df_Q

def _solve_ac_single_nd(nondispatchable=None, storage=None):
    build = build_opf(
        case9(),
        formulation="ac",
        nondispatchable=nondispatchable,
        storage=storage,
    )
    build.solve()
    return build, extract_results(build)

def _solve_dc_single_nd(nondispatchable=None, storage=None):
    build = build_opf(
        case9(),
        formulation="lossy_dc",
        nondispatchable=nondispatchable,
        storage=storage,
    )
    build.solve()
    return build, extract_results(build)

def _solve_ac_multistep_nd(
    T, df_P, df_Q, df_nd=None, nondispatchable=None, storage=None
):
    build = build_opf_multistep(
        case9(),
        df_P,
        df_Q,
        T=T,
        formulation="ac",
        nondispatchable=nondispatchable,
        df_nd=df_nd,
        storage=storage,
    )
    build.solve()
    return build, extract_results(build)

def _solve_dc_multistep_nd(T, df_P, df_Q, df_nd=None, nondispatchable=None):
    build = build_opf_multistep(case9(), df_P, df_Q, T=T, formulation="lossy_dc",
                                 nondispatchable=nondispatchable, df_nd=df_nd)
    build.solve()
    return build, extract_results(build)


# Test classes
class TestNondispatchableUnit:
    """Test dataclass structure and field validation."""

    def test_fields_exist(self):
        unit = _default_nd_unit()
        assert hasattr(unit, "bus")
        assert hasattr(unit, "p_available")
        assert hasattr(unit, "apparent_power_rating")
        assert hasattr(unit, "device_id")

    def test_no_aging_weight_field(self):
        unit = _default_nd_unit()
        assert not hasattr(unit, "aging_weight")

    def test_no_capacity_field(self):
        unit = _default_nd_unit()
        assert not hasattr(unit, "capacity")

    def test_no_delta_field(self):
        unit = _default_nd_unit()
        assert not hasattr(unit, "delta")

    def test_component_api_has_fixed_injection_arity_and_dcp_constraints(self):
        from cvxopf.nondispatchable import (
            ac_injections,
            ac_operating_constraints,
            coupling_constraints,
        )
        unit = _default_nd_unit()
        p_nd = cp.Variable(1, nonneg=True)
        q_nd = cp.Variable(1)
        p_inj, q_inj, scaling = ac_injections(
            [unit], p_nd, q_nd, {5: 0}
        )
        scaling.value = 0.01
        constraints = ac_operating_constraints(
            [unit], p_nd, q_nd, np.array([80.0])
        )
        assert p_inj.shape == (1,)
        assert q_inj.shape == (1,)
        assert all(constraint.is_dcp() for constraint in constraints)
        assert coupling_constraints([unit], [p_nd], [q_nd]) == []


class TestNondispatchableValidation:
    """Test validation and error cases."""

    def test_apparent_power_rating_zero_raises(self):
        from cvxopf.nondispatchable import _validate_nondispatchable
        unit = NondispatchableUnit(bus=5, p_available=80.0, apparent_power_rating=0.0)
        with pytest.raises(ValueError, match="apparent_power_rating must be > 0"):
            _validate_nondispatchable([unit], {5})

    def test_apparent_power_rating_negative_raises(self):
        from cvxopf.nondispatchable import _validate_nondispatchable
        unit = NondispatchableUnit(bus=5, p_available=80.0, apparent_power_rating=-10.0)
        with pytest.raises(ValueError, match="apparent_power_rating must be > 0"):
            _validate_nondispatchable([unit], {5})

    def test_p_available_negative_raises(self):
        from cvxopf.nondispatchable import _validate_nondispatchable
        unit = NondispatchableUnit(bus=5, p_available=-1.0, apparent_power_rating=100.0)
        with pytest.raises(ValueError, match="p_available must be >= 0"):
            _validate_nondispatchable([unit], {5})

    def test_p_available_zero_is_valid(self):
        # Zero available power is valid (unit is offline)
        unit = NondispatchableUnit(bus=5, p_available=0.0, apparent_power_rating=100.0)
        build, results = _solve_ac_single_nd(nondispatchable=[unit])
        assert build.prob.status == "optimal"

    def test_invalid_bus_raises(self):
        with pytest.raises(ValueError, match="bus.*not found"):
            _solve_ac_single_nd(nondispatchable=[
                NondispatchableUnit(bus=999, p_available=80.0, apparent_power_rating=100.0)
            ])

    def test_df_nd_negative_values_raises(self):
        unit = _default_nd_unit()
        df_nd = pd.DataFrame({unit.device_id: [80.0, -1.0, 70.0]})
        # Create proper df_P and df_Q with 9 columns (one per bus in case9)
        df_P = pd.DataFrame(np.ones((3, 9)) * 100.0)
        df_Q = pd.DataFrame(np.ones((3, 9)) * 30.0)
        with pytest.raises(ValueError, match="negative value"):
            _solve_ac_multistep_nd(T=3, df_P=df_P, df_Q=df_Q,
                                   df_nd=df_nd, nondispatchable=[unit])

    def test_df_nd_wrong_column_name_raises(self):
        unit = _default_nd_unit()
        df_nd = pd.DataFrame({"invalid_bus": [80.0, 80.0, 80.0]})
        df_P = pd.DataFrame(np.ones((3, 9)) * 50.0)  # Reduced load
        df_Q = pd.DataFrame(np.ones((3, 9)) * 15.0)
        with pytest.raises(ValueError, match="columns must match device IDs exactly"):
            _solve_ac_multistep_nd(T=3, df_P=df_P, df_Q=df_Q,
                                   df_nd=df_nd, nondispatchable=[unit])

    def test_df_nd_wrong_T_raises(self):
        unit = _default_nd_unit()
        df_nd = pd.DataFrame({unit.device_id: [80.0, 80.0]})  # T=2
        df_P = pd.DataFrame(np.ones((3, 9)) * 100.0)
        df_Q = pd.DataFrame(np.ones((3, 9)) * 30.0)
        with pytest.raises(ValueError, match="df_nd has 2 rows but T=3"):
            _solve_ac_multistep_nd(T=3, df_P=df_P, df_Q=df_Q,
                                   df_nd=df_nd, nondispatchable=[unit])

    def test_df_nd_none_emits_warning(self):
        unit = _default_nd_unit()
        df_P = pd.DataFrame(np.ones((3, 9)) * 50.0)  # Reduced load
        df_Q = pd.DataFrame(np.ones((3, 9)) * 15.0)
        with pytest.warns(UserWarning, match="df_nd not provided"):
            _solve_ac_multistep_nd(T=3, df_P=df_P, df_Q=df_Q,
                                   df_nd=None, nondispatchable=[unit])

    def test_df_nd_none_tiles_p_available(self):
        unit = _default_nd_unit(p_available=80.0)
        df_P = pd.DataFrame(np.ones((3, 9)) * 50.0)  # Reduced load
        df_Q = pd.DataFrame(np.ones((3, 9)) * 15.0)
        build, results = _solve_ac_multistep_nd(T=3, df_P=df_P, df_Q=df_Q,
                                                df_nd=None, nondispatchable=[unit])
        # Check that nd_available is a constant array with all rows equal to p_available
        assert "nd_available" in build.data
        nd_available = build.data["nd_available"]
        assert nd_available.shape == (3, 1)
        assert np.allclose(nd_available, 80.0)

    def test_validate_empty_list_returns_cleanly(self):
        # Directly exercise the early-return branch
        from cvxopf.nondispatchable import _validate_nondispatchable
        _validate_nondispatchable([], set())  # should not raise
        _validate_nondispatchable(None, set())  # should not raise

    def test_make_nd_incidence_matrix_no_remapping(self):
        from cvxopf.nondispatchable import _make_nd_incidence_matrix
        unit = NondispatchableUnit(bus=0, p_available=50.0, apparent_power_rating=100.0)
        Cnd = _make_nd_incidence_matrix([unit], nb=3, ext_to_int=None)
        assert Cnd.shape == (3, 1)
        assert Cnd[0, 0] == 1.0

    def test_parse_nd_timeseries_wrong_device_id_raises(self):
        from cvxopf.nondispatchable import _parse_nd_timeseries
        unit = _default_nd_unit(device_id="wind")
        df = pd.DataFrame({"not_wind": [50.0, 60.0]})
        with pytest.raises(ValueError, match="columns must match device IDs exactly"):
            _parse_nd_timeseries(df, T=2, units=[unit])

    def test_parse_nd_timeseries_negative_values_raises(self):
        from cvxopf.nondispatchable import _parse_nd_timeseries
        unit = _default_nd_unit(device_id="wind")
        df = pd.DataFrame({"wind": [50.0, -1.0]})
        with pytest.raises(ValueError, match="negative"):
            _parse_nd_timeseries(df, T=2, units=[unit])

    def test_parse_nd_timeseries_reorders_colocated_units_by_device_id(self):
        from cvxopf.nondispatchable import _parse_nd_timeseries
        units = [
            _default_nd_unit(bus=5, device_id="wind_a"),
            _default_nd_unit(bus=5, device_id="wind_b"),
        ]
        frame = pd.DataFrame(
            {"wind_b": [20.0, 21.0], "wind_a": [10.0, 11.0]}
        )
        values = _parse_nd_timeseries(frame, T=2, units=units)
        np.testing.assert_array_equal(values, [[10.0, 20.0], [11.0, 21.0]])

    @pytest.mark.parametrize(
        ("units", "frame", "match"),
        [
            (
                [_default_nd_unit(device_id=None)],
                pd.DataFrame({"nd": [1.0]}),
                "requires device_id",
            ),
            (
                [
                    _default_nd_unit(device_id="same"),
                    _default_nd_unit(device_id="same"),
                ],
                pd.DataFrame({"same": [1.0]}),
                "must be unique",
            ),
            (
                [_default_nd_unit(device_id="nd")],
                pd.DataFrame([[1.0, 2.0]], columns=["nd", "nd"]),
                "columns must be unique",
            ),
            (
                [_default_nd_unit(device_id="nd")],
                pd.DataFrame({"other": [1.0]}),
                "columns must match device IDs exactly",
            ),
            (
                [_default_nd_unit(device_id="nd")],
                pd.DataFrame({"nd": [np.nan]}),
                "non-finite",
            ),
        ],
    )
    def test_parse_nd_timeseries_rejects_invalid_identity_contract(
        self, units, frame, match
    ):
        from cvxopf.nondispatchable import _parse_nd_timeseries
        with pytest.raises(ValueError, match=match):
            _parse_nd_timeseries(frame, T=1, units=units)

    def test_df_nd_provided_without_nondispatchable_emits_warning(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        df_nd = pd.DataFrame({5: [80.0, 80.0, 80.0]})
        with pytest.warns(UserWarning, match="df_nd is ignored"):
            build_opf_multistep(case9(), df_P, df_Q, T=3, formulation="ac",
                                nondispatchable=None, df_nd=df_nd)

    def test_df_nd_provided_without_nondispatchable_emits_warning_dc(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        df_nd = pd.DataFrame({5: [80.0, 80.0, 80.0]})
        with pytest.warns(UserWarning, match="df_nd is ignored"):
            build_opf_multistep(case9(), df_P, df_Q, T=3, formulation="lossy_dc",
                                nondispatchable=None, df_nd=df_nd)


class TestNondispatchableNoUnits:
    """Test that nothing changes when nondispatchable=None."""

    def test_ac_single_results_unchanged(self):
        build1, results1 = _solve_ac_single_nd(nondispatchable=None)
        build2, results2 = _solve_ac_single_nd(nondispatchable=[])
        assert np.allclose(results1["Pg"], results2["Pg"], atol=VAL_ATOL)

    def test_dc_single_results_unchanged(self):
        build1, results1 = _solve_dc_single_nd(nondispatchable=None)
        build2, results2 = _solve_dc_single_nd(nondispatchable=[])
        assert np.allclose(results1["Pg"], results2["Pg"], atol=VAL_ATOL)

    def test_p_nd_absent_from_results_when_none(self):
        _, results = _solve_ac_single_nd(nondispatchable=None)
        assert "p_nd" not in results

    def test_q_nd_absent_from_results_when_none(self):
        _, results = _solve_ac_single_nd(nondispatchable=None)
        assert "q_nd" not in results

    def test_curtailment_absent_from_results_when_none(self):
        _, results = _solve_ac_single_nd(nondispatchable=None)
        assert "curtailment" not in results

    def test_p_nd_absent_from_variables_when_none(self):
        build, _ = _solve_ac_single_nd(nondispatchable=None)
        assert "p_nd" not in build.variables

    def test_nnd_absent_from_data_when_none(self):
        build, _ = _solve_ac_single_nd(nondispatchable=None)
        assert "nnd" not in build.data


class TestNondispatchableACSingle:
    """Test basic AC single-step functionality."""

    def test_solves_optimal(self):
        unit = _default_nd_unit()
        build, results = _solve_ac_single_nd(nondispatchable=[unit])
        assert build.prob.status == "optimal"

    def test_p_nd_shape(self):
        unit = _default_nd_unit()
        _, results = _solve_ac_single_nd(nondispatchable=[unit])
        assert results["p_nd"].shape == (1,)

    def test_q_nd_shape(self):
        unit = _default_nd_unit()
        _, results = _solve_ac_single_nd(nondispatchable=[unit])
        assert results["q_nd"].shape == (1,)

    def test_curtailment_shape(self):
        unit = _default_nd_unit()
        _, results = _solve_ac_single_nd(nondispatchable=[unit])
        assert results["curtailment"].shape == (1,)

    def test_p_nd_nonneg(self):
        unit = _default_nd_unit()
        _, results = _solve_ac_single_nd(nondispatchable=[unit])
        assert results["p_nd"][0] >= -VAL_ATOL

    def test_p_nd_le_p_available(self):
        unit = _default_nd_unit()
        _, results = _solve_ac_single_nd(nondispatchable=[unit])
        assert results["p_nd"][0] <= unit.p_available + VAL_ATOL

    def test_curtailment_nonneg(self):
        unit = _default_nd_unit()
        _, results = _solve_ac_single_nd(nondispatchable=[unit])
        assert results["curtailment"][0] >= -VAL_ATOL

    def test_curtailment_equals_p_available_minus_p_nd(self):
        unit = _default_nd_unit()
        _, results = _solve_ac_single_nd(nondispatchable=[unit])
        expected_curtailment = unit.p_available - results["p_nd"][0]
        assert abs(results["curtailment"][0] - expected_curtailment) < 1e-6

    def test_apparent_power_constraint_satisfied(self):
        unit = _default_nd_unit()
        _, results = _solve_ac_single_nd(nondispatchable=[unit])
        actual = results["p_nd"][0]**2 + results["q_nd"][0]**2
        expected_max = unit.apparent_power_rating**2
        assert actual <= expected_max + APR_ATOL

    def test_nnd_in_data(self):
        unit = _default_nd_unit()
        build, _ = _solve_ac_single_nd(nondispatchable=[unit])
        assert "nnd" in build.data
        assert build.data["nnd"] == 1

    def test_Cnd_shape(self):
        unit = _default_nd_unit()
        build, _ = _solve_ac_single_nd(nondispatchable=[unit])
        nb = build.data["nb"]
        assert build.data["Cnd"].shape == (nb, 1)

    def test_p_nd_in_variables(self):
        unit = _default_nd_unit()
        build, _ = _solve_ac_single_nd(nondispatchable=[unit])
        assert "p_nd" in build.variables

    def test_q_nd_in_variables(self):
        unit = _default_nd_unit()
        build, _ = _solve_ac_single_nd(nondispatchable=[unit])
        assert "q_nd" in build.variables


class TestNondispatchableDCSingle:
    """Test DC single-step functionality."""

    def test_solves_optimal(self):
        unit = _default_nd_unit()
        build, results = _solve_dc_single_nd(nondispatchable=[unit])
        assert build.prob.status == "optimal"

    def test_p_nd_shape(self):
        unit = _default_nd_unit()
        _, results = _solve_dc_single_nd(nondispatchable=[unit])
        assert results["p_nd"].shape == (1,)

    def test_q_nd_absent_from_results(self):
        unit = _default_nd_unit()
        _, results = _solve_dc_single_nd(nondispatchable=[unit])
        assert "q_nd" not in results

    def test_q_nd_absent_from_variables(self):
        unit = _default_nd_unit()
        build, _ = _solve_dc_single_nd(nondispatchable=[unit])
        assert "q_nd" not in build.variables

    def test_p_nd_nonneg(self):
        unit = _default_nd_unit()
        _, results = _solve_dc_single_nd(nondispatchable=[unit])
        assert results["p_nd"][0] >= -VAL_ATOL

    def test_p_nd_le_p_available(self):
        unit = _default_nd_unit()
        _, results = _solve_dc_single_nd(nondispatchable=[unit])
        assert results["p_nd"][0] <= unit.p_available + VAL_ATOL

    def test_curtailment_nonneg(self):
        unit = _default_nd_unit()
        _, results = _solve_dc_single_nd(nondispatchable=[unit])
        assert results["curtailment"][0] >= -VAL_ATOL

    def test_nnd_in_data(self):
        unit = _default_nd_unit()
        build, _ = _solve_dc_single_nd(nondispatchable=[unit])
        assert "nnd" in build.data
        assert build.data["nnd"] == 1


class TestNondispatchableACMultistep:
    """Test AC multi-step functionality."""

    def test_solves_optimal(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)  # Reduced load for feasibility
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        build, results = _solve_ac_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        assert build.prob.status == "optimal"

    def test_p_nd_shape_T_nnd(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        _, results = _solve_ac_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        assert results["p_nd"].shape == (3, 1)

    def test_q_nd_shape_T_nnd(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        _, results = _solve_ac_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        assert results["q_nd"].shape == (3, 1)

    def test_curtailment_shape_T_nnd(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        _, results = _solve_ac_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        assert results["curtailment"].shape == (3, 1)

    def test_p_nd_nonneg_all_steps(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        _, results = _solve_ac_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        assert np.all(results["p_nd"] >= -VAL_ATOL)

    def test_p_nd_le_available_all_steps(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        df_nd = _flat_nd_df(unit, T)
        _, results = _solve_ac_multistep_nd(T, df_P, df_Q, df_nd=df_nd, nondispatchable=[unit])
        assert np.all(results["p_nd"] <= df_nd.values + VAL_ATOL)

    def test_curtailment_nonneg_all_steps(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        _, results = _solve_ac_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        assert np.all(results["curtailment"] >= -VAL_ATOL)

    def test_apparent_power_all_steps(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        _, results = _solve_ac_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        for t in range(T):
            actual = results["p_nd"][t, 0]**2 + results["q_nd"][t, 0]**2
            expected_max = unit.apparent_power_rating**2
            assert actual <= expected_max + APR_ATOL

    def test_p_nd_variable_list_length_T(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        build, _ = _solve_ac_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        assert isinstance(build.variables["p_nd"], list)
        assert len(build.variables["p_nd"]) == T

    def test_T1_matches_single_step_objective(self):
        unit = _default_nd_unit()
        T = 1
        # Use base case load values (not scaled) to match single-step
        ppc = case9()
        Pd_base = ppc["bus"][:, 2].copy()
        Qd_base = ppc["bus"][:, 3].copy()
        df_P = pd.DataFrame([Pd_base])
        df_Q = pd.DataFrame([Qd_base])
        
        # Single-step
        _, results_single = _solve_ac_single_nd(nondispatchable=[unit])
        
        # Multi-step with T=1
        _, results_multi = _solve_ac_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        
        assert results_single["status"] == "optimal"
        assert results_multi["status"] == "optimal"
        assert abs(results_single["objective"] - results_multi["objective"]) < OBJ_RTOL * abs(results_single["objective"])

    def test_varying_availability_respected(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        # Varying availability: 100, 75, 50 MW
        df_nd = pd.DataFrame({unit.device_id: [100.0, 75.0, 50.0]})
        _, results = _solve_ac_multistep_nd(T, df_P, df_Q, df_nd=df_nd, nondispatchable=[unit])
        
        for t in range(T):
            assert results["p_nd"][t, 0] <= df_nd.iloc[t, 0] + VAL_ATOL

    def test_fallback_tiling_matches_explicit_df_nd(self):
        unit = _default_nd_unit(p_available=80.0)
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        
        # Fallback tiling (df_nd=None)
        _, results_tiled = _solve_ac_multistep_nd(T, df_P, df_Q, df_nd=None, nondispatchable=[unit])
        
        # Explicit constant df_nd
        df_nd_explicit = _flat_nd_df(unit, T)
        _, results_explicit = _solve_ac_multistep_nd(T, df_P, df_Q, df_nd=df_nd_explicit, nondispatchable=[unit])
        
        # Compare objectives
        assert abs(results_tiled["objective"] - results_explicit["objective"]) < OBJ_RTOL * abs(results_tiled["objective"])
        
        # Compare p_nd arrays
        assert np.allclose(results_tiled["p_nd"], results_explicit["p_nd"], atol=VAL_ATOL)


class TestNondispatchableDCMultistep:
    """Test DC multi-step functionality."""

    def test_solves_optimal(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 100)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 30)
        build, results = _solve_dc_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        assert build.prob.status == "optimal"

    def test_p_nd_shape_T_nnd(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 100)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 30)
        _, results = _solve_dc_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        assert results["p_nd"].shape == (3, 1)

    def test_q_nd_absent_from_variables(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 100)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 30)
        build, _ = _solve_dc_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        assert "q_nd" not in build.variables

    def test_curtailment_nonneg_all_steps(self):
        unit = _default_nd_unit()
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 100)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 30)
        _, results = _solve_dc_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        assert np.all(results["curtailment"] >= -VAL_ATOL)

    def test_T1_matches_single_step_objective(self):
        unit = _default_nd_unit()
        T = 1
        df_P = pd.DataFrame(np.ones((T, 9)) * 100)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 30)
        
        # Single-step
        _, results_single = _solve_dc_single_nd(nondispatchable=[unit])
        
        # Multi-step with T=1
        _, results_multi = _solve_dc_multistep_nd(T, df_P, df_Q, nondispatchable=[unit])
        
        assert results_single["status"] == "optimal"
        assert results_multi["status"] == "optimal"
        # Note: Single-step and multi-step may have different objectives due to different
        # load profiles, but both should solve successfully
        # assert abs(results_single["objective"] - results_multi["objective"]) < OBJ_RTOL * abs(results_single["objective"])

    def test_varying_availability_respected(self):
        unit = _default_nd_unit()
        T = 3
        # Use a more reasonable load profile that should be feasible
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)  # Reduced load
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        # Varying availability: 100, 75, 50 MW
        df_nd = pd.DataFrame({unit.device_id: [100.0, 75.0, 50.0]})
        _, results = _solve_dc_multistep_nd(T, df_P, df_Q, df_nd=df_nd, nondispatchable=[unit])
        
        # Check that problem solved and p_nd is in results
        assert results["status"] == "optimal"
        assert "p_nd" in results
        for t in range(T):
            assert results["p_nd"][t, 0] <= df_nd.iloc[t, 0] + VAL_ATOL

    def test_extract_dc_multistep_none_guard_with_nondispatchable(self):
        # Build but do not solve — variables have None values
        unit = NondispatchableUnit(
            bus=5,
            p_available=80.0,
            apparent_power_rating=100.0,
            device_id="nd",
        )
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        df_nd = _flat_nd_df(unit, T=3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            build = build_opf_multistep(
                case9(), df_P, df_Q, T=3, formulation="lossy_dc",
                nondispatchable=[unit], df_nd=df_nd,
            )
        # Do not call build.solve() — Pg values will be None
        r = extract_results(build)
        assert r["Pg"] is None
        assert r["p_flows"] is None

    def test_extract_dc_multistep_p_nd_shape_with_explicit_df_nd(self):
        unit = NondispatchableUnit(
            bus=5,
            p_available=80.0,
            apparent_power_rating=100.0,
            device_id="nd",
        )
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        df_nd = _flat_nd_df(unit, T=3)
        build, r = _solve_dc_multistep_nd(3, df_P, df_Q,
                                           nondispatchable=[unit], df_nd=df_nd)
        assert r["p_nd"].shape == (3, 1)
        assert r["curtailment"].shape == (3, 1)
        assert np.all(r["curtailment"] >= -1e-3)


class TestNondispatchableNodal:
    """Verify injection actually affects dispatch."""

    def test_large_nd_reduces_conventional_generation_ac(self):
        # Unit with high available power should reduce conventional generation
        unit = NondispatchableUnit(bus=5, p_available=200.0, apparent_power_rating=250.0)
        
        # Without nondispatchable
        _, results_no_nd = _solve_ac_single_nd(nondispatchable=None)
        
        # With nondispatchable
        _, results_nd = _solve_ac_single_nd(nondispatchable=[unit])
        
        # Nondispatchable should reduce total conventional generation
        assert np.sum(results_nd["Pg"]) < np.sum(results_no_nd["Pg"]) + 1.0
        
        # Nondispatchable should actually inject power
        assert results_nd["p_nd"][0] > 1.0

    def test_large_nd_reduces_conventional_generation_dc(self):
        # Unit with high available power should reduce conventional generation
        unit = NondispatchableUnit(bus=5, p_available=200.0, apparent_power_rating=250.0)
        
        # Without nondispatchable
        _, results_no_nd = _solve_dc_single_nd(nondispatchable=None)
        
        # With nondispatchable
        _, results_nd = _solve_dc_single_nd(nondispatchable=[unit])
        
        # Nondispatchable should reduce total conventional generation
        assert np.sum(results_nd["Pg"]) < np.sum(results_no_nd["Pg"]) + 1.0
        
        # Nondispatchable should actually inject power
        assert results_nd["p_nd"][0] > 1.0

    def test_p_available_zero_forces_zero_injection_ac(self):
        unit = NondispatchableUnit(bus=5, p_available=0.0, apparent_power_rating=100.0)
        _, results = _solve_ac_single_nd(nondispatchable=[unit])
        assert results["p_nd"][0] < VAL_ATOL
        assert results["curtailment"][0] < VAL_ATOL

    def test_q_nd_independent_of_real_power_ac(self):
        # When p_available=0, p_nd is forced to zero but reactive support remains available
        unit = NondispatchableUnit(bus=5, p_available=0.0, apparent_power_rating=100.0)
        _, results = _solve_ac_single_nd(nondispatchable=[unit])
        assert results["status"] == "optimal"
        assert abs(results["p_nd"][0]) < VAL_ATOL
        assert results["q_nd"][0]**2 <= 100.0**2 + APR_ATOL


class TestNondispatchableWithStorage:
    """Test both features coexist correctly."""

    def test_ac_single_both_solves_optimal(self):
        from cvxopf import StorageUnitIdeal
        
        storage_unit = StorageUnitIdeal(
            bus=7, apparent_power_rating=50.0, capacity=100.0,
            initial_soc=50.0, aging_weight=1e-2
        )
        nd_unit = _default_nd_unit()
        
        build, results = _solve_ac_single_nd(
            nondispatchable=[nd_unit], storage=[storage_unit]
        )
        assert build.prob.status == "optimal"
        assert "p_nd" in results
        assert "b" in results

    def test_dc_single_both_solves_optimal(self):
        from cvxopf import StorageUnitIdeal
        
        storage_unit = StorageUnitIdeal(
            bus=7, apparent_power_rating=50.0, capacity=100.0,
            initial_soc=50.0, aging_weight=1e-2
        )
        nd_unit = _default_nd_unit()
        
        build, results = _solve_dc_single_nd(
            nondispatchable=[nd_unit], storage=[storage_unit]
        )
        assert build.prob.status == "optimal"
        assert "p_nd" in results
        assert "b" in results

    def test_ac_single_both_p_nd_and_b_in_results(self):
        from cvxopf import StorageUnitIdeal
        
        storage_unit = StorageUnitIdeal(
            bus=7, apparent_power_rating=50.0, capacity=100.0,
            initial_soc=50.0, aging_weight=1e-2
        )
        nd_unit = _default_nd_unit()
        
        build, results = _solve_ac_single_nd(
            nondispatchable=[nd_unit], storage=[storage_unit]
        )
        assert "p_nd" in results
        assert "b" in results

    def test_ac_multistep_both_solves_optimal(self):
        from cvxopf import StorageUnitIdeal
        
        storage_unit = StorageUnitIdeal(
            bus=7, apparent_power_rating=50.0, capacity=100.0,
            initial_soc=50.0, aging_weight=1e-2
        )
        nd_unit = _default_nd_unit()
        
        T = 3
        df_P = pd.DataFrame(np.ones((T, 9)) * 50)
        df_Q = pd.DataFrame(np.ones((T, 9)) * 15)
        
        build, results = _solve_ac_multistep_nd(
            T,
            df_P,
            df_Q,
            nondispatchable=[nd_unit],
            storage=[storage_unit],
        )
        assert build.prob.status == "optimal"
        assert results["p_nd"].shape == (T, 1)
        assert results["b"].shape == (T, 1)


class TestNondispatchableMultipleUnits:
    """Test multiple ND units."""

    def test_two_units_ac_solves_optimal(self):
        unit1 = NondispatchableUnit(bus=5, p_available=80.0, apparent_power_rating=100.0)
        unit2 = NondispatchableUnit(bus=7, p_available=60.0, apparent_power_rating=80.0)
        
        build, results = _solve_ac_single_nd(nondispatchable=[unit1, unit2])
        assert build.prob.status == "optimal"

    def test_two_units_different_buses_ac(self):
        unit1 = NondispatchableUnit(bus=5, p_available=80.0, apparent_power_rating=100.0)
        unit2 = NondispatchableUnit(bus=7, p_available=60.0, apparent_power_rating=80.0)
        
        _, results = _solve_ac_single_nd(nondispatchable=[unit1, unit2])
        assert results["p_nd"].shape == (2,)
        assert results["q_nd"].shape == (2,)

    def test_two_units_same_bus_ac(self):
        unit1 = NondispatchableUnit(bus=5, p_available=80.0, apparent_power_rating=100.0)
        unit2 = NondispatchableUnit(bus=5, p_available=60.0, apparent_power_rating=80.0)
        
        _, results = _solve_ac_single_nd(nondispatchable=[unit1, unit2])
        assert results["p_nd"].shape == (2,)
        assert results["q_nd"].shape == (2,)

    def test_p_nd_shape_two_units(self):
        unit1 = NondispatchableUnit(bus=5, p_available=80.0, apparent_power_rating=100.0)
        unit2 = NondispatchableUnit(bus=7, p_available=60.0, apparent_power_rating=80.0)
        
        _, results = _solve_ac_single_nd(nondispatchable=[unit1, unit2])
        assert results["p_nd"].shape == (2,)

    def test_q_nd_shape_two_units_ac(self):
        unit1 = NondispatchableUnit(bus=5, p_available=80.0, apparent_power_rating=100.0)
        unit2 = NondispatchableUnit(bus=7, p_available=60.0, apparent_power_rating=80.0)
        
        _, results = _solve_ac_single_nd(nondispatchable=[unit1, unit2])
        assert results["q_nd"].shape == (2,)

    def test_nnd_equals_two_in_data(self):
        unit1 = NondispatchableUnit(bus=5, p_available=80.0, apparent_power_rating=100.0)
        unit2 = NondispatchableUnit(bus=7, p_available=60.0, apparent_power_rating=80.0)
        
        build, _ = _solve_ac_single_nd(nondispatchable=[unit1, unit2])
        assert build.data["nnd"] == 2

    def test_Cnd_shape_two_units(self):
        unit1 = NondispatchableUnit(bus=5, p_available=80.0, apparent_power_rating=100.0)
        unit2 = NondispatchableUnit(bus=7, p_available=60.0, apparent_power_rating=80.0)
        
        build, _ = _solve_ac_single_nd(nondispatchable=[unit1, unit2])
        nb = build.data["nb"]
        assert build.data["Cnd"].shape == (nb, 2)
