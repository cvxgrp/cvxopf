"""M19 Stage 6 scientific and formulation verification for load shedding."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from cvxopf import (
    DispatchableGenerator,
    Load,
    NondispatchableUnit,
    StorageUnitIdeal,
    build_opf,
    build_opf_multistep,
    extract_results,
    max_generation_marginal_cost,
)
from cvxopf.testcases import case9, make_singlenode_case


def _solve(build):
    """Solve while suppressing the documented non-DPP parameter warning."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=(
                "You are solving a parameterized problem that is not DPP.*"
            ),
            category=UserWarning,
            module="cvxopf.problem",
        )
        build.solve()
    return extract_results(build)


def _single_node_result(generators, demand, voll):
    """Solve one controlled single-node economic-shedding instance."""
    case = make_singlenode_case(0.0, generators)
    load = Load(
        1,
        demand,
        "demand",
        shedding_cost_per_mwh=voll,
    )
    build = build_opf(
        case,
        formulation="singlenode_dc",
        generators=generators,
        loads=[load],
    )
    return _solve(build)


def test_quadratic_single_node_phase_transition_and_invariance():
    """Verify the exact-penalty result under its controlled assumptions.

    This case has continuous dispatch, one convex nondecreasing quadratic
    generator, feasible full service, zero minimum output, and no network,
    storage, or intertemporal value. The generator-only maximum slope is thus
    a valid conservative sufficient bound, not a general network certificate.
    """
    generator = DispatchableGenerator(
        1,
        100.0,
        cost_coeffs=(0.0, 2.0, 0.05),
    )
    bound = max_generation_marginal_cost([generator])
    assert bound == pytest.approx(12.0)

    below = _single_node_result([generator], 80.0, 9.0)
    above = _single_node_result([generator], 80.0, bound + 0.1)
    twice = _single_node_result([generator], 80.0, 2.0 * bound)
    five_times = _single_node_result([generator], 80.0, 5.0 * bound)

    # Below the threshold, C'(P)=2 + 0.1 P = 9 gives P=70 MW.
    assert below["Pg"][0] == pytest.approx(70.0, abs=1e-4)
    assert below["p_load_shed"][0] == pytest.approx(10.0, abs=1e-4)
    assert below["energy_not_served"] == pytest.approx(10.0, abs=1e-4)
    for result in (above, twice, five_times):
        assert result["Pg"][0] == pytest.approx(80.0, abs=1e-4)
        assert result["p_load_shed"][0] == pytest.approx(0.0, abs=1e-4)
        assert result["energy_not_served"] == pytest.approx(0.0, abs=1e-4)
    assert twice["Pg"] == pytest.approx(five_times["Pg"], abs=1e-5)
    assert twice["objective"] == pytest.approx(
        five_times["objective"], abs=1e-4
    )


def test_multiple_generator_pwl_value_function_threshold():
    """Use the fleet value-function slope, not one raw generator curve."""
    generators = [
        DispatchableGenerator(
            1,
            50.0,
            cost_type="piecewise_linear",
            cost_points=((0.0, 0.0), (50.0, 250.0)),
        ),
        DispatchableGenerator(
            1,
            50.0,
            cost_type="piecewise_linear",
            cost_points=((0.0, 0.0), (50.0, 500.0)),
        ),
    ]
    bound = max_generation_marginal_cost(generators)
    assert bound == pytest.approx(10.0)

    below = _single_node_result(generators, 80.0, 9.0)
    above = _single_node_result(generators, 80.0, bound + 0.1)
    high = _single_node_result(generators, 80.0, 3.0 * bound)

    np.testing.assert_allclose(below["Pg"], [50.0, 0.0], atol=1e-4)
    assert below["p_load_shed"][0] == pytest.approx(30.0, abs=1e-4)
    assert below["energy_not_served"] == pytest.approx(30.0, abs=1e-4)
    for result in (above, high):
        np.testing.assert_allclose(result["Pg"], [50.0, 30.0], atol=1e-4)
        assert result["p_load_shed"][0] == pytest.approx(0.0, abs=1e-4)
        assert result["energy_not_served"] == pytest.approx(0.0, abs=1e-4)
    np.testing.assert_allclose(above["Pg"], high["Pg"], atol=1e-4)


def test_shedding_restores_supply_limited_feasibility():
    generator = DispatchableGenerator(1, 60.0, cost_coeffs=(0.0, 10.0))
    case = make_singlenode_case(0.0, [generator])
    fixed = build_opf(
        case,
        formulation="singlenode_dc",
        generators=[generator],
        loads=[Load(1, 100.0, "demand")],
    )
    sheddable = build_opf(
        case,
        formulation="singlenode_dc",
        generators=[generator],
        loads=[
            Load(
                1,
                100.0,
                "demand",
                shedding_cost_per_mwh=5000.0,
            )
        ],
    )

    fixed_result = _solve(fixed)
    shed_result = _solve(sheddable)

    assert fixed_result["status"] == "infeasible"
    assert shed_result["status"] == "optimal"
    assert shed_result["Pg"][0] == pytest.approx(60.0, abs=1e-4)
    assert shed_result["p_load_shed"][0] == pytest.approx(40.0, abs=1e-4)
    assert shed_result["energy_not_served"] == pytest.approx(40.0, abs=1e-4)
    assert 0 <= shed_result["p_load_shed"][0] <= 100.0


def test_ac_adequacy_shedding_relaxes_active_and_reactive_load_together():
    case = case9()
    case["gen"][:, 8] = [100.0, 75.0, 75.0]
    loads = [
        Load(
            5,
            90.0,
            "load-5",
            q_load_mvar=30.0,
            shedding_cost_per_mwh=5000.0,
        ),
        Load(7, 100.0, "load-7", q_load_mvar=35.0),
        Load(9, 125.0, "load-9", q_load_mvar=50.0),
    ]
    result = _solve(build_opf(case, formulation="ac", loads=loads))

    assert result["status"] == "optimal"
    assert result["p_load_shed"][0] > 60.0
    assert result["q_load_shed"][0] > 0.0
    assert result["q_load_shed"][0] / result["p_load_shed"][0] == (
        pytest.approx(30.0 / 90.0, abs=1e-7)
    )
    assert result["p_load_served"][0] + result["p_load_shed"][0] == (
        pytest.approx(90.0, abs=1e-6)
    )
    assert result["q_load_served"][0] + result["q_load_shed"][0] == (
        pytest.approx(30.0, abs=1e-6)
    )
    assert result["energy_not_served"] == pytest.approx(
        result["p_load_shed"][0], abs=1e-6
    )


def _two_bus_congestion_case():
    """Return a two-bus, one-line MATPOWER case with a 50 MW DC limit."""
    source = case9()
    case = {
        "baseMVA": 100.0,
        "bus": source["bus"][:2].copy(),
        "gen": source["gen"][:1].copy(),
        "branch": source["branch"][:1].copy(),
        "gencost": source["gencost"][:1].copy(),
    }
    case["bus"][:, 0] = [1, 2]
    case["bus"][:, 1] = [3, 1]
    case["bus"][:, 2:4] = 0.0
    case["gen"][0, 0] = 1
    case["gen"][0, 8] = 200.0
    case["gen"][0, 9] = 0.0
    case["branch"][0, [0, 1, 2, 3, 4, 5, 8, 9, 10]] = [
        1,
        2,
        0.01,
        0.1,
        0.0,
        50.0,
        0.0,
        0.0,
        1.0,
    ]
    return case


def test_lossy_dc_congestion_makes_load_location_relevant():
    case = _two_bus_congestion_case()
    generator = DispatchableGenerator(1, 200.0, cost_coeffs=(0.0, 10.0))

    def solve_at(bus):
        load = Load(
            bus,
            80.0,
            f"load-{bus}",
            shedding_cost_per_mwh=1000.0,
        )
        build = build_opf(
            case,
            formulation="lossy_dc",
            generators=[generator],
            loads=[load],
        )
        return _solve(build)

    local = solve_at(1)
    remote = solve_at(2)

    assert local["p_load_shed"][0] == pytest.approx(0.0, abs=1e-4)
    assert local["p_flows"][0] == pytest.approx(0.0, abs=1e-4)
    assert remote["p_flows"][0] == pytest.approx(50.0, abs=1e-4)
    assert remote["p_load_served"][0] == pytest.approx(50.0, abs=1e-4)
    assert remote["p_load_shed"][0] == pytest.approx(30.0, abs=1e-4)
    assert local["energy_not_served"] == pytest.approx(0.0, abs=1e-4)
    assert remote["energy_not_served"] == pytest.approx(30.0, abs=1e-4)


def test_ac_congestion_makes_load_location_relevant():
    case = _two_bus_congestion_case()
    generator = DispatchableGenerator(
        1,
        200.0,
        q_max_mvar=200.0,
        q_min_mvar=-200.0,
        cost_coeffs=(0.0, 10.0),
    )

    def solve_at(bus):
        load = Load(
            bus,
            80.0,
            f"load-{bus}",
            q_load_mvar=0.0,
            shedding_cost_per_mwh=1000.0,
        )
        build = build_opf(
            case,
            formulation="ac",
            generators=[generator],
            loads=[load],
        )
        return _solve(build)

    local = solve_at(1)
    remote = solve_at(2)

    assert local["p_load_shed"][0] == pytest.approx(0.0, abs=1e-4)
    assert local["branch_s_from"][0] == pytest.approx(0.0, abs=1e-4)
    assert remote["branch_s_from"][0] == pytest.approx(50.0, abs=1e-4)
    assert remote["p_load_shed"][0] > 30.0
    assert remote["p_load_served"][0] + remote["p_load_shed"][0] == (
        pytest.approx(80.0, abs=1e-5)
    )
    assert remote["energy_not_served"] == pytest.approx(
        remote["p_load_shed"][0], abs=1e-5
    )


def test_multistep_storage_nd_terminal_policy_and_load_aggregation():
    generator = DispatchableGenerator(1, 60.0, cost_coeffs=(0.0, 10.0))
    case = make_singlenode_case(0.0, [generator])
    loads = [
        Load(1, 20.0, "fixed"),
        Load(
            1,
            20.0,
            "flex",
            shedding_cost_per_mwh=5000.0,
        ),
    ]
    load_profile = pd.DataFrame(
        {"fixed": [20.0, 20.0], "flex": [20.0, 80.0]}
    )
    nondispatchable = [
        NondispatchableUnit(1, 20.0, 20.0, "solar")
    ]
    nd_profile = pd.DataFrame({"solar": [20.0, 0.0]})

    def solve(terminal):
        storage = StorageUnitIdeal(
            1,
            40.0,
            capacity=40.0,
            initial_soc=40.0,
            aging_weight=0.01,
            terminal_soc=20.0 if terminal else None,
            terminal_constraint="equality" if terminal else None,
        )
        build = build_opf_multistep(
            case,
            T=2,
            formulation="singlenode_dc",
            generators=[generator],
            loads=loads,
            df_load_p=load_profile,
            storage=[storage],
            nondispatchable=nondispatchable,
            df_nd=nd_profile,
            delta=1.0,
        )
        return _solve(build)

    unconstrained = solve(False)
    terminal = solve(True)

    np.testing.assert_allclose(terminal["p_nd"][:, 0], [20.0, 0.0], atol=1e-4)
    np.testing.assert_allclose(terminal["curtailment"][:, 0], 0.0, atol=1e-4)
    assert terminal["b"][0, 0] == pytest.approx(0.0, abs=1e-4)
    assert terminal["soc"][0, 0] == pytest.approx(40.0, abs=1e-4)
    assert terminal["b"][1, 0] == pytest.approx(20.0, abs=1e-4)
    assert terminal["soc"][1, 0] == pytest.approx(20.0, abs=1e-4)
    np.testing.assert_allclose(
        terminal["p_load_served"][:, 0], [20.0, 20.0], atol=1e-4
    )
    np.testing.assert_allclose(
        terminal["p_load_shed"][:, 0], [0.0, 20.0], atol=1e-4
    )
    np.testing.assert_allclose(
        terminal["Pg"][:, 0]
        + terminal["p_nd"][:, 0]
        + terminal["b"][:, 0],
        np.sum(terminal["p_load_served"], axis=1),
        atol=1e-4,
    )
    assert terminal["energy_not_served"] == pytest.approx(20.0, abs=1e-4)
    assert terminal["storage_terminal_deviation"][0] == pytest.approx(
        0.0, abs=1e-4
    )
    assert unconstrained["energy_not_served"] == pytest.approx(0.0, abs=1e-4)
    assert unconstrained["soc"][-1, 0] == pytest.approx(0.0, abs=1e-4)
