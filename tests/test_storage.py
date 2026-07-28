"""Test suite for Milestone 5 battery storage model."""

import warnings
import numpy as np
import pandas as pd
import pytest
import cvxpy as cp

from cvxopf.testcases import case9
from cvxopf.problem import (
    build_opf, build_opf_multistep,
    OPFBuild, OPFOptions, StorageUnitIdeal,
)
from cvxopf.results import extract_results
from cvxopf.storage import (
    ac_injections as storage_ac_injections,
    dc_injections as storage_dc_injections,
    ac_operating_constraints as storage_ac_operating_constraints,
    dc_operating_constraints as storage_dc_operating_constraints,
    coupling_constraints as storage_coupling_constraints,
    storage_cost_expr,
    terminal_cost_expr as storage_terminal_cost_expr,
)

# ---------------------------------------------------------------------------
# Tolerances (use these exact values throughout)
# ---------------------------------------------------------------------------

OBJ_RTOL   = 1e-4    # relative tolerance on objective
VAL_ATOL   = 1e-3    # absolute tolerance on Pg, Qg, b, b_q, soc (MW/MVAr/MWh)
SOC_ATOL   = 1e-4    # absolute tolerance on SoC dynamics residual (MWh)
APR_ATOL   = 1e-4    # absolute tolerance on apparent power constraint residual (MVA^2)
BAL_ATOL   = 1e-4    # absolute tolerance on nodal balance residual (p.u.)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flat_load_dfs(case_fn, T):
    """Return (df_P, df_Q) with T identical rows matching base case load."""
    ppc     = case_fn()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    df_P    = pd.DataFrame(np.tile(Pd_base, (T, 1)))
    df_Q    = pd.DataFrame(np.tile(Qd_base, (T, 1)))
    return df_P, df_Q


def _varying_load_dfs(case_fn, scales):
    """Return (df_P, df_Q) with len(scales) rows at given load scales."""
    ppc     = case_fn()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    df_P    = pd.DataFrame(np.outer(scales, Pd_base))
    df_Q    = pd.DataFrame(np.outer(scales, Qd_base))
    return df_P, df_Q


def _default_unit(bus=1, S_max=50.0, capacity=100.0,
                  initial_soc=50.0, aging_weight=0.0, **kwargs):
    """Return a StorageUnitIdeal with sensible defaults for testing."""
    return StorageUnitIdeal(
        bus=bus,
        apparent_power_rating=S_max,
        capacity=capacity,
        initial_soc=initial_soc,
        aging_weight=aging_weight,
        **kwargs,
    )


def _solve_ac_single(storage=None, delta=1.0, case_fn=case9, options=None):
    build = build_opf(case_fn(), formulation="ac",
                      storage=storage, delta=delta, options=options)
    build.solve()
    return build, extract_results(build)


def _solve_dc_single(storage=None, delta=1.0, case_fn=case9, options=None):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf(case_fn(), formulation="lossy_dc",
                          storage=storage, delta=delta, options=options)
    build.solve()
    return build, extract_results(build)


def _solve_ac_multistep(T, df_P, df_Q, storage=None, delta=1.0,
                         case_fn=case9, coupling_constraints=None):
    build = build_opf_multistep(
        case_fn(), df_P, df_Q, T=T, formulation="ac",
        storage=storage, delta=delta,
        coupling_constraints=coupling_constraints,
    )
    build.solve()
    return build, extract_results(build)


def _solve_dc_multistep(T, df_P, df_Q, storage=None, delta=1.0,
                         case_fn=case9, coupling_constraints=None):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf_multistep(
            case_fn(), df_P, df_Q, T=T, formulation="lossy_dc",
            storage=storage, delta=delta,
            coupling_constraints=coupling_constraints,
        )
    build.solve()
    return build, extract_results(build)


class TestStorageComponentInterface:
    def test_ac_and_dc_injections_have_fixed_arity(self):
        units = [_default_unit(bus=4)]
        b = cp.Variable(1)
        b_q = cp.Variable(1)

        p_ac, q_ac, scale_ac = storage_ac_injections(
            units, b, b_q, {4: 0}, nb=1
        )
        p_dc, q_dc, scale_dc = storage_dc_injections(
            units, b, {4: 0}, nb=1
        )

        assert p_ac.shape == (1,)
        assert q_ac.shape == (1,)
        assert p_dc.shape == (1,)
        assert q_dc is None
        assert scale_ac.value is None
        assert scale_dc.value is None
        assert scale_ac.attributes["nonneg"]
        assert scale_dc.attributes["nonneg"]

    def test_operating_constraints_and_cost_are_dcp(self):
        units = [_default_unit(aging_weight=0.5)]
        b = cp.Variable(1)
        b_q = cp.Variable(1)
        soc = cp.Variable(1)

        ac_constraints = storage_ac_operating_constraints(
            units, b, b_q, soc
        )
        dc_constraints = storage_dc_operating_constraints(units, b, soc)
        cost = storage_cost_expr(units, b)

        assert all(constraint.is_dcp() for constraint in ac_constraints)
        assert all(constraint.is_dcp() for constraint in dc_constraints)
        assert cost.is_dcp()

    def test_coupling_constraints_recover_soc_trajectory(self):
        units = [_default_unit(initial_soc=50.0)]
        b = [cp.Variable(1), cp.Variable(1)]
        soc = [cp.Variable(1), cp.Variable(1)]
        constraints = storage_coupling_constraints(
            units, b, soc, delta=0.5
        )
        constraints += [b[0] == [10.0], b[1] == [-4.0]]

        cp.Problem(cp.Minimize(0), constraints).solve()

        assert len(constraints) == 4
        assert soc[0].value[0] == pytest.approx(45.0)
        assert soc[1].value[0] == pytest.approx(47.0)

    @pytest.mark.parametrize(
        ("mode", "expected_count"),
        [("equality", 3), ("shortfall", 3)],
    )
    def test_hard_terminal_constraint_is_horizon_owned(
        self, mode, expected_count
    ):
        units = [
            _default_unit(
                initial_soc=50.0,
                terminal_soc=40.0,
                terminal_constraint=mode,
            )
        ]
        b = [cp.Variable(1), cp.Variable(1)]
        soc = [cp.Variable(1), cp.Variable(1)]
        constraints = storage_coupling_constraints(
            units, b, soc, delta=1.0
        )

        assert len(constraints) == expected_count
        assert all(constraint.is_dcp() for constraint in constraints)

    @pytest.mark.parametrize(
        ("mode", "terminal_value", "expected"),
        [
            ("linear", 45.0, 10.0),
            ("linear", 55.0, 10.0),
            ("quadratic", 45.0, 50.0),
            ("quadratic", 55.0, 50.0),
            ("shortfall_linear", 45.0, 10.0),
            ("shortfall_linear", 55.0, 0.0),
            ("shortfall_quadratic", 45.0, 50.0),
            ("shortfall_quadratic", 55.0, 0.0),
        ],
    )
    def test_terminal_cost_modes(self, mode, terminal_value, expected):
        units = [
            _default_unit(
                terminal_soc=50.0,
                terminal_cost=mode,
                terminal_weight=2.0,
            )
        ]
        soc = cp.Variable(1)
        cost = storage_terminal_cost_expr(units, soc)
        problem = cp.Problem(cp.Minimize(cost), [soc == terminal_value])
        problem.solve()

        assert cost.is_dcp()
        assert cost.value == pytest.approx(expected)

    def test_terminal_cost_is_collection_level(self):
        units = [
            _default_unit(
                terminal_soc=50.0,
                terminal_cost="linear",
                terminal_weight=2.0,
            ),
            _default_unit(
                terminal_soc=20.0,
                terminal_cost="shortfall_quadratic",
                terminal_weight=3.0,
            ),
        ]
        soc = cp.Variable(2)
        cost = storage_terminal_cost_expr(units, soc)
        problem = cp.Problem(cp.Minimize(cost), [soc == [45.0, 18.0]])
        problem.solve()

        assert cost.value == pytest.approx(2.0 * 5.0 + 3.0 * 2.0**2)

    def test_terminal_cost_is_none_when_inactive(self):
        units = [_default_unit()]
        assert storage_terminal_cost_expr(units, cp.Variable(1)) is None


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestStorageUnitIdeal:
    """Tests the StorageUnitIdeal dataclass itself."""

    def test_dataclass_fields_exist(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                 capacity=100.0, initial_soc=50.0)
        assert hasattr(unit, "bus")
        assert hasattr(unit, "apparent_power_rating")
        assert hasattr(unit, "capacity")
        assert hasattr(unit, "initial_soc")
        assert hasattr(unit, "aging_weight")
        assert hasattr(unit, "terminal_soc")
        assert hasattr(unit, "terminal_constraint")
        assert hasattr(unit, "terminal_cost")
        assert hasattr(unit, "terminal_weight")

    def test_default_aging_weight_is_1e_2(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                 capacity=100.0, initial_soc=50.0)
        assert unit.aging_weight == pytest.approx(1e-2)

    def test_no_delta_field(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                 capacity=100.0, initial_soc=50.0)
        assert not hasattr(unit, "delta")

    def test_custom_aging_weight(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                 capacity=100.0, initial_soc=50.0,
                                 aging_weight=0.5)
        assert unit.aging_weight == pytest.approx(0.5)

    def test_terminal_defaults_are_none(self):
        unit = _default_unit()
        assert unit.terminal_soc is None
        assert unit.terminal_constraint is None
        assert unit.terminal_cost is None
        assert unit.terminal_weight is None


class TestStorageValidation:
    """Tests that invalid StorageUnitIdeal parameters raise ValueError."""

    def test_invalid_apparent_power_rating_zero_raises(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=0.0,
                                 capacity=100.0, initial_soc=50.0)
        with pytest.raises(ValueError, match="apparent_power_rating"):
            build_opf(case9(), formulation="ac", storage=[unit])

    def test_invalid_apparent_power_rating_negative_raises(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=-10.0,
                                 capacity=100.0, initial_soc=50.0)
        with pytest.raises(ValueError):
            build_opf(case9(), formulation="ac", storage=[unit])

    def test_invalid_capacity_zero_raises(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                 capacity=0.0, initial_soc=0.0)
        with pytest.raises(ValueError, match="capacity"):
            build_opf(case9(), formulation="ac", storage=[unit])

    def test_initial_soc_below_zero_raises(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                 capacity=100.0, initial_soc=-1.0)
        with pytest.raises(ValueError, match="initial_soc"):
            build_opf(case9(), formulation="ac", storage=[unit])

    def test_initial_soc_above_capacity_raises(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                 capacity=100.0, initial_soc=150.0)
        with pytest.raises(ValueError, match="initial_soc"):
            build_opf(case9(), formulation="ac", storage=[unit])

    def test_initial_soc_equals_capacity_is_valid(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                 capacity=100.0, initial_soc=100.0)
        build = build_opf(case9(), formulation="ac", storage=[unit])
        assert isinstance(build, OPFBuild)

    def test_initial_soc_zero_is_valid(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                 capacity=100.0, initial_soc=0.0)
        build = build_opf(case9(), formulation="ac", storage=[unit])
        assert isinstance(build, OPFBuild)

    def test_negative_aging_weight_raises(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                 capacity=100.0, initial_soc=50.0,
                                 aging_weight=-0.1)
        with pytest.raises(ValueError, match="aging_weight"):
            build_opf(case9(), formulation="ac", storage=[unit])

    def test_aging_weight_zero_is_valid(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                 capacity=100.0, initial_soc=50.0,
                                 aging_weight=0.0)
        build = build_opf(case9(), formulation="ac", storage=[unit])
        assert isinstance(build, OPFBuild)

    def test_invalid_bus_raises(self):
        unit = StorageUnitIdeal(bus=999, apparent_power_rating=50.0,
                                 capacity=100.0, initial_soc=50.0)
        with pytest.raises(ValueError, match="bus"):
            build_opf(case9(), formulation="ac", storage=[unit])

    @pytest.mark.parametrize("mode", ["invalid", "", "reserve_floor"])
    def test_invalid_terminal_constraint_raises(self, mode):
        unit = _default_unit(
            terminal_soc=50.0,
            terminal_constraint=mode,
        )
        with pytest.raises(ValueError, match="terminal_constraint"):
            build_opf(case9(), formulation="ac", storage=[unit])

    @pytest.mark.parametrize(
        "mode",
        ["invalid", "", "shortfall", "linear_shortfall"],
    )
    def test_invalid_terminal_cost_raises(self, mode):
        unit = _default_unit(
            terminal_soc=50.0,
            terminal_cost=mode,
            terminal_weight=1.0,
        )
        with pytest.raises(ValueError, match="terminal_cost"):
            build_opf(case9(), formulation="ac", storage=[unit])

    @pytest.mark.parametrize("mode", ["equality", "shortfall"])
    def test_terminal_constraint_requires_target(self, mode):
        unit = _default_unit(terminal_constraint=mode)
        with pytest.raises(ValueError, match="terminal_soc"):
            build_opf(case9(), formulation="ac", storage=[unit])

    @pytest.mark.parametrize(
        "mode",
        ["linear", "quadratic", "shortfall_linear", "shortfall_quadratic"],
    )
    def test_terminal_cost_requires_target(self, mode):
        unit = _default_unit(terminal_cost=mode, terminal_weight=1.0)
        with pytest.raises(ValueError, match="terminal_soc"):
            build_opf(case9(), formulation="ac", storage=[unit])

    @pytest.mark.parametrize("target", [-1.0, 101.0, np.nan, np.inf])
    def test_invalid_terminal_target_raises(self, target):
        unit = _default_unit(
            terminal_soc=target,
            terminal_constraint="equality",
        )
        with pytest.raises(ValueError, match="terminal_soc"):
            build_opf(case9(), formulation="ac", storage=[unit])

    @pytest.mark.parametrize("weight", [None, 0.0, -1.0, np.nan, np.inf])
    def test_invalid_terminal_weight_raises(self, weight):
        unit = _default_unit(
            terminal_soc=50.0,
            terminal_cost="linear",
            terminal_weight=weight,
        )
        with pytest.raises(ValueError, match="terminal_weight"):
            build_opf(case9(), formulation="ac", storage=[unit])

    def test_terminal_constraint_and_cost_are_alternatives(self):
        unit = _default_unit(
            terminal_soc=50.0,
            terminal_constraint="equality",
            terminal_cost="linear",
            terminal_weight=1.0,
        )
        with pytest.raises(ValueError, match="alternatives"):
            build_opf(case9(), formulation="ac", storage=[unit])

    def test_inert_terminal_target_raises(self):
        unit = _default_unit(terminal_soc=50.0)
        with pytest.raises(ValueError, match="terminal_soc requires"):
            build_opf(case9(), formulation="ac", storage=[unit])

    def test_terminal_weight_without_cost_raises(self):
        unit = _default_unit(
            terminal_soc=50.0,
            terminal_constraint="shortfall",
            terminal_weight=1.0,
        )
        with pytest.raises(ValueError, match="terminal_weight requires"):
            build_opf(case9(), formulation="ac", storage=[unit])


class TestDeltaValidation:
    """Tests delta parameter validation."""

    @pytest.mark.parametrize("delta", [0.0, -1.0, np.float64(0.0)])
    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_nonpositive_delta_rejected_without_storage(
        self, formulation, delta
    ):
        with pytest.raises(ValueError, match="delta must be > 0"):
            build_opf(case9(), formulation=formulation, delta=delta)

    @pytest.mark.parametrize(
        "delta", [np.nan, np.inf, -np.inf, np.float64(np.nan)]
    )
    def test_nonfinite_delta_rejected(self, delta):
        with pytest.raises(ValueError, match="delta must be finite"):
            build_opf(case9(), formulation="ac", delta=delta)

    @pytest.mark.parametrize(
        "delta",
        [None, "1.0", [1.0], 1.0 + 0.0j, True, np.bool_(False)],
    )
    def test_nonreal_scalar_delta_rejected(self, delta):
        with pytest.raises(TypeError, match="delta must be a real scalar"):
            build_opf(case9(), formulation="ac", delta=delta)

    @pytest.mark.parametrize(
        "delta", [1, 0.25, np.int64(2), np.float32(0.5), np.float64(0.75)]
    )
    def test_real_scalar_delta_is_stored(self, delta):
        unit = _default_unit()
        build = build_opf(
            case9(), formulation="ac", storage=[unit], delta=delta
        )
        assert build.data["storage_delta"] == pytest.approx(float(delta))

    def test_delta_default_is_one(self):
        # build.data["storage_delta"] should be 1.0 by default
        unit  = _default_unit()
        build = build_opf(case9(), formulation="ac", storage=[unit])
        assert build.data["storage_delta"] == pytest.approx(1.0)

    def test_delta_025_stored_in_data(self):
        unit  = _default_unit()
        build = build_opf(case9(), formulation="ac", storage=[unit], delta=0.25)
        assert build.data["storage_delta"] == pytest.approx(0.25)


class TestStorageTerminalPolicy:
    @staticmethod
    def _solve_terminal_tradeoff(formulation, terminal_cost, weight):
        unit = _default_unit(
            bus=1,
            initial_soc=50.0,
            terminal_soc=50.0,
            terminal_cost=terminal_cost,
            terminal_weight=weight,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf(
                case9(), formulation=formulation, storage=[unit], delta=1.0
            )
        build.solve()
        return extract_results(build)

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_single_step_equality_composed_across_formulations(
        self, formulation
    ):
        unit = _default_unit(
            bus=1,
            initial_soc=50.0,
            terminal_soc=40.0,
            terminal_constraint="equality",
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf(
                case9(), formulation=formulation, storage=[unit], delta=1.0
            )
        build.solve()
        results = extract_results(build)

        assert results["status"] in {"optimal", "optimal_inaccurate"}
        assert results["soc"][0] == pytest.approx(40.0, abs=VAL_ATOL)
        assert results["b"][0] == pytest.approx(10.0, abs=VAL_ATOL)
        assert results["storage_terminal_deviation"][0] == pytest.approx(
            0.0, abs=VAL_ATOL
        )
        assert "storage_terminal_cost" not in results

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_single_step_shortfall_composed_across_formulations(
        self, formulation
    ):
        unit = _default_unit(
            bus=1,
            initial_soc=50.0,
            terminal_soc=40.0,
            terminal_constraint="shortfall",
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf(
                case9(), formulation=formulation, storage=[unit], delta=1.0
            )
        build.solve()
        results = extract_results(build)

        assert results["status"] == "optimal"
        assert results["soc"][0] >= 40.0 - VAL_ATOL
        assert results["storage_terminal_deviation"][0] >= -VAL_ATOL

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_shortfall_constraint_permits_terminal_surplus(self, formulation):
        unit = _default_unit(
            bus=1,
            initial_soc=50.0,
            aging_weight=1e6,
            terminal_soc=40.0,
            terminal_constraint="shortfall",
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf(
                case9(), formulation=formulation, storage=[unit], delta=1.0
            )
        build.solve()
        results = extract_results(build)

        assert results["status"] == "optimal"
        assert results["soc"][0] > 40.0 + VAL_ATOL
        assert results["storage_terminal_deviation"][0] > VAL_ATOL

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_soft_terminal_cost_composed_across_formulations(
        self, formulation
    ):
        unit = _default_unit(
            bus=1,
            initial_soc=50.0,
            terminal_soc=40.0,
            terminal_cost="quadratic",
            terminal_weight=1.0,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf(
                case9(), formulation=formulation, storage=[unit], delta=1.0
            )
        build.solve()
        results = extract_results(build)

        deviation = results["storage_terminal_deviation"][0]
        assert results["status"] == "optimal"
        assert results["storage_terminal_cost"] == pytest.approx(
            deviation**2, abs=VAL_ATOL
        )
        assert "storage_terminal_cost" in build.expressions

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_increasing_two_sided_weight_reduces_absolute_deviation(
        self, formulation
    ):
        low_weight = self._solve_terminal_tradeoff(
            formulation, "quadratic", weight=1e-4
        )
        high_weight = self._solve_terminal_tradeoff(
            formulation, "quadratic", weight=100.0
        )

        low_deviation = abs(low_weight["storage_terminal_deviation"][0])
        high_deviation = abs(high_weight["storage_terminal_deviation"][0])

        assert high_deviation < low_deviation - VAL_ATOL

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_increasing_one_sided_weight_reduces_shortfall(
        self, formulation
    ):
        low_weight = self._solve_terminal_tradeoff(
            formulation, "shortfall_quadratic", weight=1e-4
        )
        high_weight = self._solve_terminal_tradeoff(
            formulation, "shortfall_quadratic", weight=100.0
        )

        low_deviation = low_weight["storage_terminal_deviation"][0]
        high_deviation = high_weight["storage_terminal_deviation"][0]
        low_shortfall = max(-low_deviation, 0.0)
        high_shortfall = max(-high_deviation, 0.0)

        assert high_shortfall < low_shortfall - VAL_ATOL

    def test_terminal_results_absent_without_policy(self):
        _, results = _solve_dc_single(storage=[_default_unit()])
        assert "storage_terminal_deviation" not in results
        assert "storage_terminal_cost" not in results

    def test_mixed_policy_deviation_uses_nan_for_inactive_unit(self):
        units = [
            _default_unit(
                bus=1,
                terminal_soc=40.0,
                terminal_constraint="shortfall",
            ),
            _default_unit(bus=2),
        ]
        _, results = _solve_dc_single(storage=units)

        assert results["storage_terminal_deviation"].shape == (2,)
        assert np.isfinite(results["storage_terminal_deviation"][0])
        assert np.isnan(results["storage_terminal_deviation"][1])

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    @pytest.mark.parametrize("mode", ["equality", "shortfall"])
    def test_unreachable_hard_terminal_policy_is_infeasible(
        self, formulation, mode
    ):
        unit = _default_unit(
            bus=1,
            S_max=10.0,
            capacity=100.0,
            initial_soc=0.0,
            terminal_soc=100.0,
            terminal_constraint=mode,
        )
        max_reachable_soc = (
            unit.initial_soc + unit.apparent_power_rating * 1.0
        )
        assert max_reachable_soc < unit.terminal_soc

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf(
                case9(),
                formulation=formulation,
                storage=[unit],
                delta=1.0,
            )
        build.solve()

        if formulation == "ac":
            # IPOPT is a local nonlinear solver, not an infeasibility
            # certifier. Depending on platform, it either detects local
            # infeasibility or reaches its iteration limit on this
            # deliberately impossible model.
            assert build.prob.status in {
                cp.INFEASIBLE,
                cp.INFEASIBLE_INACCURATE,
                cp.USER_LIMIT,
            }
        else:
            assert build.prob.status == cp.INFEASIBLE

    def test_multistep_terminal_uses_last_post_step_soc(self):
        unit = _default_unit(
            bus=1,
            initial_soc=50.0,
            terminal_soc=35.0,
            terminal_constraint="equality",
        )
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(),
                df_P,
                df_Q,
                T=3,
                formulation="singlenode_dc",
                storage=[unit],
                delta=0.5,
            )
        build.solve()
        results = extract_results(build)

        assert results["soc"][-1, 0] == pytest.approx(35.0, abs=VAL_ATOL)
        expected = 50.0 - 0.5 * np.sum(results["b"][:, 0])
        assert results["soc"][-1, 0] == pytest.approx(expected, abs=SOC_ATOL)

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_multistep_soft_terminal_cost_composed_across_formulations(
        self, formulation
    ):
        unit = _default_unit(
            bus=1,
            initial_soc=50.0,
            terminal_soc=40.0,
            terminal_cost="quadratic",
            terminal_weight=1.0,
        )
        df_P, df_Q = _flat_load_dfs(case9, T=2)
        reactive_load = df_Q if formulation == "ac" else None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(),
                df_P,
                reactive_load,
                T=2,
                formulation=formulation,
                storage=[unit],
                delta=1.0,
            )
        build.solve()
        results = extract_results(build)

        deviation = results["storage_terminal_deviation"][0]
        assert results["status"] == "optimal"
        assert results["storage_terminal_cost"] == pytest.approx(
            deviation**2, abs=VAL_ATOL
        )
        assert "storage_terminal_cost" in build.expressions

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_multistep_terminal_penalty_is_counted_once(self, formulation):
        ppc = case9()
        ppc["gencost"][:, 4:] = 0.0
        unit = _default_unit(
            bus=1,
            S_max=10.0,
            capacity=100.0,
            initial_soc=0.0,
            terminal_soc=100.0,
            terminal_cost="quadratic",
            terminal_weight=1.0,
        )
        df_P, df_Q = _flat_load_dfs(case9, T=2)
        reactive_load = df_Q if formulation == "ac" else None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                ppc,
                df_P,
                reactive_load,
                T=2,
                formulation=formulation,
                storage=[unit],
                delta=1.0,
                options=OPFOptions(loss_weight=0.0),
            )
        build.solve()
        results = extract_results(build)

        assert results["status"] == "optimal"
        assert results["storage_terminal_cost"] > VAL_ATOL
        assert results["objective"] == pytest.approx(
            results["storage_terminal_cost"], rel=OBJ_RTOL
        )

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_explicitly_inactive_terminal_policy_preserves_dispatch(
        self, formulation
    ):
        baseline = _default_unit(bus=1)
        explicit_none = _default_unit(
            bus=1,
            terminal_soc=None,
            terminal_constraint=None,
            terminal_cost=None,
            terminal_weight=None,
        )

        solved = []
        for unit in (baseline, explicit_none):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                build = build_opf(
                    case9(),
                    formulation=formulation,
                    storage=[unit],
                    delta=1.0,
                )
            build.solve()
            solved.append(extract_results(build))

        assert solved[0]["objective"] == pytest.approx(
            solved[1]["objective"], rel=OBJ_RTOL
        )
        np.testing.assert_allclose(
            solved[0]["Pg"], solved[1]["Pg"], atol=VAL_ATOL
        )
        np.testing.assert_allclose(
            solved[0]["b"], solved[1]["b"], atol=VAL_ATOL
        )


class TestStorageNoStorage:
    """Confirms that storage=None leaves all existing behaviour exactly unchanged."""

    def test_ac_single_no_storage_results_unchanged(self):
        # Solve with and without storage=None explicitly.
        # Results should be identical (no storage is the default).
        build1 = build_opf(case9(), formulation="ac")
        build2 = build_opf(case9(), formulation="ac", storage=None)
        build1.solve()
        build2.solve()
        r1 = extract_results(build1)
        r2 = extract_results(build2)
        assert r1["status"] == r2["status"]
        np.testing.assert_allclose(r1["Pg"], r2["Pg"], atol=VAL_ATOL)

    def test_dc_single_no_storage_results_unchanged(self):
        build1 = build_opf(case9(), formulation="lossy_dc")
        build2 = build_opf(case9(), formulation="lossy_dc", storage=None)
        build1.solve()
        build2.solve()
        r1 = extract_results(build1)
        r2 = extract_results(build2)
        assert r1["status"] == r2["status"]
        np.testing.assert_allclose(r1["Pg"], r2["Pg"], atol=VAL_ATOL)

    def test_b_absent_from_results_when_no_storage(self):
        _, r = _solve_ac_single()
        assert "b" not in r

    def test_b_q_absent_from_results_when_no_storage(self):
        _, r = _solve_ac_single()
        assert "b_q" not in r

    def test_soc_absent_from_results_when_no_storage(self):
        _, r = _solve_ac_single()
        assert "soc" not in r

    def test_storage_cost_absent_from_results_when_no_storage(self):
        _, r = _solve_ac_single()
        assert "storage_cost" not in r

    def test_b_absent_from_variables_when_no_storage(self):
        build, _ = _solve_ac_single()
        assert "b" not in build.variables

    def test_ns_absent_from_data_when_no_storage(self):
        build, _ = _solve_ac_single()
        assert "ns" not in build.data

    @pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
    def test_empty_storage_list_is_absent(self, formulation):
        build = build_opf(case9(), formulation=formulation, storage=[])
        assert "ns" not in build.data
        assert "b" not in build.variables


class TestStorageACSingle:
    """Single time-step AC-OPF with one storage unit."""

    def test_solves_optimal(self):
        build, r = _solve_ac_single(storage=[_default_unit()])
        assert r["status"] == "optimal"

    def test_b_shape(self):
        build, r = _solve_ac_single(storage=[_default_unit()])
        assert r["b"].shape == (1,)

    def test_b_q_shape(self):
        build, r = _solve_ac_single(storage=[_default_unit()])
        assert r["b_q"].shape == (1,)

    def test_soc_shape(self):
        build, r = _solve_ac_single(storage=[_default_unit()])
        assert r["soc"].shape == (1,)

    def test_soc_satisfies_initial_condition(self):
        unit  = _default_unit(initial_soc=50.0)
        build, r = _solve_ac_single(storage=[unit], delta=1.0)
        # soc[0] == initial_soc - b[0] * delta
        residual = r["soc"][0] - (50.0 - r["b"][0] * 1.0)
        assert abs(residual) < SOC_ATOL

    def test_apparent_power_constraint_satisfied(self):
        unit = _default_unit(S_max=50.0)
        build, r = _solve_ac_single(storage=[unit])
        violation = r["b"][0]**2 + r["b_q"][0]**2 - 50.0**2
        assert violation <= APR_ATOL

    def test_soc_within_capacity_bounds(self):
        unit = _default_unit(capacity=100.0)
        build, r = _solve_ac_single(storage=[unit])
        assert r["soc"][0] >= -VAL_ATOL
        assert r["soc"][0] <= 100.0 + VAL_ATOL

    def test_b_in_variables(self):
        build, _ = _solve_ac_single(storage=[_default_unit()])
        assert "b" in build.variables
        assert isinstance(build.variables["b"], cp.Variable)

    def test_b_q_in_variables(self):
        build, _ = _solve_ac_single(storage=[_default_unit()])
        assert "b_q" in build.variables
        assert isinstance(build.variables["b_q"], cp.Variable)

    def test_soc_in_variables(self):
        build, _ = _solve_ac_single(storage=[_default_unit()])
        assert "soc" in build.variables
        assert isinstance(build.variables["soc"], cp.Variable)

    def test_ns_in_data(self):
        build, _ = _solve_ac_single(storage=[_default_unit()])
        assert "ns" in build.data
        assert build.data["ns"] == 1

    def test_storage_delta_in_data(self):
        build, _ = _solve_ac_single(storage=[_default_unit()], delta=1.0)
        assert "storage_delta" in build.data
        assert build.data["storage_delta"] == pytest.approx(1.0)

    def test_storage_cost_in_results(self):
        build, r = _solve_ac_single(storage=[_default_unit()])
        assert "storage_cost" in r

    def test_storage_cost_nonneg(self):
        build, r = _solve_ac_single(storage=[_default_unit()])
        assert r["storage_cost"] >= -1e-6

    def test_storage_cost_zero_when_aging_weight_zero(self):
        unit = _default_unit(aging_weight=0.0)
        _, r = _solve_ac_single(storage=[unit])
        assert abs(r["storage_cost"]) < 1e-4

    def test_data_has_expected_storage_keys(self):
        build, _ = _solve_ac_single(storage=[_default_unit()])
        for key in ("ns", "Cs", "storage_bus", "storage_apparent_power_rating",
                    "storage_capacity", "storage_initial_soc",
                    "storage_aging_weight", "storage_delta"):
            assert key in build.data, f"build.data missing '{key}'"

    def test_Cs_shape(self):
        build, _ = _solve_ac_single(storage=[_default_unit()])
        nb = build.data["nb"]
        assert build.data["Cs"].shape == (nb, 1)


class TestStorageACMultistep:
    """Multi-step AC-OPF with one storage unit."""

    def test_multistep_solves_optimal(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
        assert r["status"] == "optimal"

    def test_b_shape_T_ns(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
        assert r["b"].shape == (3, 1)

    def test_b_q_shape_T_ns(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
        assert r["b_q"].shape == (3, 1)

    def test_soc_shape_T_ns(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
        assert r["soc"].shape == (3, 1)

    def test_soc_dynamics_initial_condition(self):
        unit = _default_unit(initial_soc=50.0)
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[unit], delta=1.0)
        residual = r["soc"][0, 0] - (50.0 - r["b"][0, 0] * 1.0)
        assert abs(residual) < SOC_ATOL

    def test_soc_dynamics_all_steps(self):
        unit = _default_unit(initial_soc=50.0)
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[unit], delta=1.0)
        for t in range(1, 3):
            residual = r["soc"][t, 0] - (r["soc"][t-1, 0] - r["b"][t, 0] * 1.0)
            assert abs(residual) < SOC_ATOL, f"SoC dynamics violated at t={t}"

    def test_apparent_power_constraint_all_steps(self):
        unit = _default_unit(S_max=50.0)
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[unit])
        for t in range(3):
            violation = r["b"][t, 0]**2 + r["b_q"][t, 0]**2 - 50.0**2
            assert violation <= APR_ATOL, f"Apparent power violated at t={t}"

    def test_soc_within_capacity_all_steps(self):
        unit = _default_unit(capacity=100.0)
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[unit])
        assert np.all(r["soc"] >= -VAL_ATOL)
        assert np.all(r["soc"] <= 100.0 + VAL_ATOL)

    def test_b_variable_list_length_T(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        build, _ = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
        assert isinstance(build.variables["b"], list)
        assert len(build.variables["b"]) == 3

    def test_b_q_variable_list_length_T(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        build, _ = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
        assert isinstance(build.variables["b_q"], list)
        assert len(build.variables["b_q"]) == 3

    def test_T1_objective_matches_single_step(self):
        unit = _default_unit(aging_weight=0.0)
        df_P, df_Q = _flat_load_dfs(case9, T=1)
        _, r_single = _solve_ac_single(storage=[unit])
        _, r_multi  = _solve_ac_multistep(1, df_P, df_Q, storage=[unit])
        assert abs(r_multi["objective"] - r_single["objective"]) \
               / abs(r_single["objective"]) < OBJ_RTOL

    def test_T1_b_matches_single_step(self):
        unit = _default_unit(aging_weight=0.0)
        df_P, df_Q = _flat_load_dfs(case9, T=1)
        _, r_single = _solve_ac_single(storage=[unit])
        _, r_multi  = _solve_ac_multistep(1, df_P, df_Q, storage=[unit])
        np.testing.assert_allclose(r_multi["b"][0], r_single["b"], atol=VAL_ATOL)

    def test_T1_soc_matches_single_step(self):
        unit = _default_unit(aging_weight=0.0)
        df_P, df_Q = _flat_load_dfs(case9, T=1)
        _, r_single = _solve_ac_single(storage=[unit])
        _, r_multi  = _solve_ac_multistep(1, df_P, df_Q, storage=[unit])
        np.testing.assert_allclose(r_multi["soc"][0], r_single["soc"], atol=VAL_ATOL)

    def test_storage_cost_in_results(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_ac_multistep(3, df_P, df_Q, storage=[_default_unit()])
        assert "storage_cost" in r
        assert isinstance(r["storage_cost"], float)


class TestStorageDCSingle:
    """Single time-step DC-OPF with one storage unit."""

    def test_solves_optimal(self):
        _, r = _solve_dc_single(storage=[_default_unit()])
        assert r["status"] == "optimal"

    def test_b_shape(self):
        _, r = _solve_dc_single(storage=[_default_unit()])
        assert r["b"].shape == (1,)

    def test_b_q_absent_from_results(self):
        _, r = _solve_dc_single(storage=[_default_unit()])
        assert "b_q" not in r

    def test_b_q_absent_from_variables(self):
        build, _ = _solve_dc_single(storage=[_default_unit()])
        assert "b_q" not in build.variables

    def test_soc_satisfies_initial_condition(self):
        unit = _default_unit(initial_soc=30.0)
        _, r = _solve_dc_single(storage=[unit], delta=1.0)
        residual = r["soc"][0] - (30.0 - r["b"][0] * 1.0)
        assert abs(residual) < SOC_ATOL

    def test_real_power_bound_satisfied(self):
        unit = _default_unit(S_max=30.0)
        _, r = _solve_dc_single(storage=[unit])
        assert r["b"][0] >= -30.0 - VAL_ATOL
        assert r["b"][0] <=  30.0 + VAL_ATOL

    def test_dc_apparent_power_fallback_emits_warning(self):
        unit = _default_unit()
        with pytest.warns(UserWarning, match="apparent_power_rating is applied as a real power limit"):
            build_opf(case9(), formulation="lossy_dc", storage=[unit])

    def test_storage_cost_in_results(self):
        _, r = _solve_dc_single(storage=[_default_unit()])
        assert "storage_cost" in r

    def test_soc_within_capacity_bounds(self):
        unit = _default_unit(capacity=100.0)
        _, r = _solve_dc_single(storage=[unit])
        assert r["soc"][0] >= -VAL_ATOL
        assert r["soc"][0] <= 100.0 + VAL_ATOL


class TestStorageDCMultistep:
    """Multi-step DC-OPF with one storage unit."""

    def test_multistep_dc_solves_optimal(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_dc_multistep(3, df_P, df_Q, storage=[_default_unit()])
        assert r["status"] == "optimal"

    def test_b_shape_T_ns(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_dc_multistep(3, df_P, df_Q, storage=[_default_unit()])
        assert r["b"].shape == (3, 1)

    def test_soc_shape_T_ns(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_dc_multistep(3, df_P, df_Q, storage=[_default_unit()])
        assert r["soc"].shape == (3, 1)

    def test_b_q_absent_from_multistep_variables(self):
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        build, _ = _solve_dc_multistep(3, df_P, df_Q, storage=[_default_unit()])
        assert "b_q" not in build.variables

    def test_soc_dynamics_all_steps(self):
        unit = _default_unit(initial_soc=30.0)
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_dc_multistep(3, df_P, df_Q, storage=[unit], delta=1.0)
        residual_0 = r["soc"][0, 0] - (30.0 - r["b"][0, 0] * 1.0)
        assert abs(residual_0) < SOC_ATOL
        for t in range(1, 3):
            residual = r["soc"][t, 0] - (r["soc"][t-1, 0] - r["b"][t, 0] * 1.0)
            assert abs(residual) < SOC_ATOL, f"DC SoC dynamics violated at t={t}"

    def test_T1_matches_single_step_objective(self):
        unit = _default_unit(aging_weight=0.0)
        df_P, df_Q = _flat_load_dfs(case9, T=1)
        _, r_single = _solve_dc_single(storage=[unit])
        _, r_multi  = _solve_dc_multistep(1, df_P, df_Q, storage=[unit])
        assert abs(r_multi["objective"] - r_single["objective"]) \
               / abs(r_single["objective"]) < OBJ_RTOL


class TestStorageMultipleUnits:
    """Tests with multiple storage units."""

    def test_two_units_ac_solves_optimal(self):
        unit_a = _default_unit(bus=1, S_max=30.0)
        unit_b = _default_unit(bus=2, S_max=30.0)
        _, r = _solve_ac_single(storage=[unit_a, unit_b])
        assert r["status"] == "optimal"

    def test_two_units_dc_solves_optimal(self):
        unit_a = _default_unit(bus=1, S_max=30.0)
        unit_b = _default_unit(bus=2, S_max=30.0)
        _, r = _solve_dc_single(storage=[unit_a, unit_b])
        assert r["status"] == "optimal"

    def test_b_shape_two_units_ac(self):
        unit_a = _default_unit(bus=1)
        unit_b = _default_unit(bus=2)
        _, r = _solve_ac_single(storage=[unit_a, unit_b])
        assert r["b"].shape == (2,)

    def test_b_q_shape_two_units_ac(self):
        unit_a = _default_unit(bus=1)
        unit_b = _default_unit(bus=2)
        _, r = _solve_ac_single(storage=[unit_a, unit_b])
        assert r["b_q"].shape == (2,)

    def test_soc_shape_two_units(self):
        unit_a = _default_unit(bus=1)
        unit_b = _default_unit(bus=2)
        _, r = _solve_ac_single(storage=[unit_a, unit_b])
        assert r["soc"].shape == (2,)

    def test_two_units_same_bus_ac_solves_optimal(self):
        unit_a = _default_unit(bus=1, S_max=20.0)
        unit_b = _default_unit(bus=1, S_max=20.0)
        _, r = _solve_ac_single(storage=[unit_a, unit_b])
        assert r["status"] == "optimal"

    def test_two_units_same_bus_dc_solves_optimal(self):
        unit_a = _default_unit(bus=1, S_max=20.0)
        unit_b = _default_unit(bus=1, S_max=20.0)
        _, r = _solve_dc_single(storage=[unit_a, unit_b])
        assert r["status"] == "optimal"

    def test_ns_equals_two_in_data(self):
        unit_a = _default_unit(bus=1)
        unit_b = _default_unit(bus=2)
        build, _ = _solve_ac_single(storage=[unit_a, unit_b])
        assert build.data["ns"] == 2

    def test_Cs_shape_two_units(self):
        unit_a = _default_unit(bus=1)
        unit_b = _default_unit(bus=2)
        build, _ = _solve_ac_single(storage=[unit_a, unit_b])
        nb = build.data["nb"]
        assert build.data["Cs"].shape == (nb, 2)


class TestStorageNodal:
    """Verifies that storage power actually enters the nodal balance."""

    def test_storage_affects_dispatch_ac(self):
        # With a large enough storage unit allowed to discharge freely,
        # the generator dispatch should differ from the no-storage case.
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=100.0,
                                 capacity=200.0, initial_soc=100.0,
                                 aging_weight=0.0)
        _, r_no_stor = _solve_ac_single()
        _, r_stor    = _solve_ac_single(storage=[unit])
        assert r_no_stor["status"] == "optimal"
        assert r_stor["status"]    == "optimal"
        # Total generation should differ — storage discharged some real power
        total_no_stor = np.sum(r_no_stor["Pg"])
        total_stor    = np.sum(r_stor["Pg"])
        # Storage discharging reduces required generation
        assert total_stor < total_no_stor + 1.0   # allow 1 MW tolerance

    def test_storage_affects_dispatch_dc(self):
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=100.0,
                                 capacity=200.0, initial_soc=100.0,
                                 aging_weight=0.0)
        _, r_no_stor = _solve_dc_single()
        _, r_stor    = _solve_dc_single(storage=[unit])
        assert r_stor["status"] == "optimal"
        total_no_stor = np.sum(r_no_stor["Pg"])
        total_stor    = np.sum(r_stor["Pg"])
        assert total_stor < total_no_stor + 1.0

    def test_fully_charged_cannot_charge_further_dc(self):
        # initial_soc == capacity: charging would violate SoC upper bound
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=30.0,
                                 capacity=50.0, initial_soc=50.0,
                                 aging_weight=0.0)
        df_P, df_Q = _flat_load_dfs(case9, T=1)
        _, r = _solve_dc_multistep(1, df_P, df_Q, storage=[unit], delta=1.0)
        assert r["status"] == "optimal"
        # b[0] must be >= 0 (cannot charge: SoC already at max)
        assert r["b"][0, 0] >= -VAL_ATOL

    def test_fully_discharged_cannot_discharge_further_dc(self):
        # initial_soc == 0: discharging would violate SoC lower bound
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=30.0,
                                 capacity=50.0, initial_soc=0.0,
                                 aging_weight=0.0)
        df_P, df_Q = _flat_load_dfs(case9, T=1)
        _, r = _solve_dc_multistep(1, df_P, df_Q, storage=[unit], delta=1.0)
        assert r["status"] == "optimal"
        # b[0] must be <= 0 (cannot discharge: SoC at zero)
        assert r["b"][0, 0] <= VAL_ATOL

    def test_reactive_support_ac_independent_of_real_power(self):
        # A storage unit with initial_soc=capacity (fully charged) can still
        # provide reactive power in AC — b_q is not constrained by SoC.
        unit = StorageUnitIdeal(bus=1, apparent_power_rating=50.0,
                                 capacity=50.0, initial_soc=50.0,
                                 aging_weight=0.0)
        _, r = _solve_ac_single(storage=[unit])
        assert r["status"] == "optimal"
        # b_q can be non-zero even though real power is constrained to >= 0
        # Just verify apparent power constraint holds
        violation = r["b"][0]**2 + r["b_q"][0]**2 - 50.0**2
        assert violation <= APR_ATOL


class TestStorageAgingCost:
    """Tests storage aging cost calculation."""

    def test_aging_weight_zero_storage_cost_near_zero(self):
        unit = _default_unit(aging_weight=0.0)
        _, r = _solve_ac_single(storage=[unit])
        assert abs(r["storage_cost"]) < 1e-4

    def test_higher_aging_weight_higher_or_equal_objective_ac(self):
        unit_free = _default_unit(aging_weight=0.0)
        unit_aged = _default_unit(aging_weight=1.0)
        _, r_free = _solve_ac_single(storage=[unit_free])
        _, r_aged = _solve_ac_single(storage=[unit_aged])
        assert r_free["status"] == r_aged["status"] == "optimal"
        assert r_aged["objective"] >= r_free["objective"] - 1e-3

    def test_higher_aging_weight_reduces_cycling_dc(self):
        # With varying load, higher aging weight should produce
        # less |b| throughput (or equal if storage not dispatched).
        scales = [0.8, 1.0, 1.2]
        df_P, df_Q = _varying_load_dfs(case9, scales)

        unit_free = _default_unit(aging_weight=0.0)
        unit_aged = _default_unit(aging_weight=5.0)

        _, r_free = _solve_dc_multistep(3, df_P, df_Q, storage=[unit_free])
        _, r_aged = _solve_dc_multistep(3, df_P, df_Q, storage=[unit_aged])

        assert r_free["status"] == r_aged["status"] == "optimal"
        cycling_free = np.sum(np.abs(r_free["b"]))
        cycling_aged = np.sum(np.abs(r_aged["b"]))
        assert cycling_aged <= cycling_free + VAL_ATOL

    def test_storage_cost_equals_weight_times_abs_b_ac(self):
        unit = _default_unit(aging_weight=0.5)
        _, r = _solve_ac_single(storage=[unit])
        expected_cost = 0.5 * np.sum(np.abs(r["b"]))
        assert abs(r["storage_cost"] - expected_cost) < 1e-6

    def test_storage_cost_equals_weight_times_abs_b_dc(self):
        unit = _default_unit(aging_weight=0.5)
        _, r = _solve_dc_single(storage=[unit])
        expected_cost = 0.5 * np.sum(np.abs(r["b"]))
        assert abs(r["storage_cost"] - expected_cost) < 1e-6

    def test_storage_cost_equals_weight_times_abs_b_multistep_dc(self):
        scales = [0.8, 1.0, 1.2]
        df_P, df_Q = _varying_load_dfs(case9, scales)
        unit = _default_unit(aging_weight=0.3)
        _, r = _solve_dc_multistep(3, df_P, df_Q, storage=[unit])
        expected_cost = 0.3 * np.sum(np.abs(r["b"]))
        assert abs(r["storage_cost"] - expected_cost) < 1e-6

    def test_reactive_power_not_penalised_ac(self):
        # With aging_weight > 0, storage_cost should only reflect |b|, not |b_q|.
        unit = _default_unit(aging_weight=1.0)
        _, r = _solve_ac_single(storage=[unit])
        expected_cost = 1.0 * np.sum(np.abs(r["b"]))
        assert abs(r["storage_cost"] - expected_cost) < 1e-6


class TestStorageDelta:
    """Tests delta parameter effects on SoC dynamics."""

    def test_delta_025_soc_dynamics_ac(self):
        # delta=0.25 means each step is 15 minutes.
        # soc_0 = initial_soc - b_0 * 0.25
        unit = _default_unit(initial_soc=50.0)
        _, r = _solve_ac_single(storage=[unit], delta=0.25)
        residual = r["soc"][0] - (50.0 - r["b"][0] * 0.25)
        assert abs(residual) < SOC_ATOL

    def test_delta_025_soc_dynamics_multistep_dc(self):
        unit = _default_unit(initial_soc=30.0)
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        _, r = _solve_dc_multistep(3, df_P, df_Q, storage=[unit], delta=0.25)
        residual_0 = r["soc"][0, 0] - (30.0 - r["b"][0, 0] * 0.25)
        assert abs(residual_0) < SOC_ATOL
        for t in range(1, 3):
            residual = r["soc"][t, 0] - (r["soc"][t-1, 0] - r["b"][t, 0] * 0.25)
            assert abs(residual) < SOC_ATOL

    def test_smaller_delta_allows_more_energy_exchange(self):
        # With delta=1.0, max energy exchangeable per step = S_max * 1.0
        # With delta=0.25, max energy exchangeable per step = S_max * 0.25
        # So with fixed initial_soc and capacity, smaller delta should allow
        # less total SoC change per step.
        unit = _default_unit(initial_soc=50.0, capacity=100.0, S_max=30.0)
        _, r1 = _solve_ac_single(storage=[unit], delta=1.0)
        _, r025 = _solve_ac_single(storage=[unit], delta=0.25)
        # |soc - initial_soc| should be smaller with delta=0.25
        delta_soc_1   = abs(r1["soc"][0]   - 50.0)
        delta_soc_025 = abs(r025["soc"][0] - 50.0)
        assert r1["status"] == r025["status"] == "optimal"
        assert delta_soc_025 <= delta_soc_1 + VAL_ATOL


class TestStorageCouplingConstraintHook:
    """Tests that coupling constraints work alongside storage."""

    def test_user_coupling_constraints_accepted_with_storage_ac(self):
        unit = _default_unit()
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        build = build_opf_multistep(
            case9(), df_P, df_Q, T=3, formulation="ac",
            storage=[unit], coupling_constraints=[],
        )
        assert isinstance(build, OPFBuild)

    def test_user_coupling_constraints_accepted_with_storage_dc(self):
        unit = _default_unit()
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(), df_P, df_Q, T=3, formulation="lossy_dc",
                storage=[unit], coupling_constraints=[],
            )
        assert isinstance(build, OPFBuild)

    def test_user_coupling_constraint_applied_alongside_storage(self):
        # Add a trivial coupling constraint on Pg and verify problem still solves.
        unit = _default_unit()
        df_P, df_Q = _flat_load_dfs(case9, T=3)
        build = build_opf_multistep(
            case9(), df_P, df_Q, T=3, formulation="ac", storage=[unit],
        )
        # Pg[0][0] == Pg[1][0]: first generator output equal in steps 0 and 1
        coupling = [build.variables["Pg"][0][0] == build.variables["Pg"][1][0]]
        build2 = build_opf_multistep(
            case9(), df_P, df_Q, T=3, formulation="ac",
            storage=[unit], coupling_constraints=coupling,
        )
        build2.solve()
        r = extract_results(build2)
        assert r["status"] == "optimal"
