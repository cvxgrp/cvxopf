"""Branch-local gates for the M14c vectorized lossy-DC formulation."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any
import warnings

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

import cvxopf.hvdc as hvdc_module
import cvxopf.load as load_module
from cvxopf import (
    NondispatchableUnit,
    StorageUnitIdeal,
    build_opf,
    build_opf_multistep,
    extract_results,
)
from cvxopf.generator import gen_from_matpower
from cvxopf.hvdc import HVDCLink
from cvxopf.load import Load
from cvxopf.testcases import case9


ATOL = 2e-5
RTOL = 1e-9


def _legacy_frames(steps: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    case = case9()
    multipliers = np.linspace(0.9, 1.1, steps)
    return (
        pd.DataFrame(multipliers[:, None] * case["bus"][:, 2]),
        pd.DataFrame(multipliers[:, None] * case["bus"][:, 3]),
    )


def _build_pair(steps: int, **kwargs: Any):
    active, reactive = _legacy_frames(steps)
    builds = []
    for assembly in ("stepwise", "vectorized"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            builds.append(
                build_opf_multistep(
                    case9(),
                    active,
                    reactive,
                    T=steps,
                    formulation="lossy_dc",
                    temporal_assembly=assembly,
                    **kwargs,
                )
            )
    return tuple(builds)


def _solve(build):
    build.solve(tol_gap_abs=1e-10, tol_gap_rel=1e-10, tol_feas=1e-10)
    return extract_results(build)


def _assert_numeric_equal(left: object, right: object) -> None:
    left_array = np.asarray(left)
    right_array = np.asarray(right)
    assert left_array.shape == right_array.shape
    if np.issubdtype(left_array.dtype, np.number):
        np.testing.assert_allclose(
            left_array.astype(float),
            right_array.astype(float),
            atol=ATOL,
            rtol=RTOL,
            equal_nan=True,
        )
    else:
        np.testing.assert_array_equal(left_array, right_array)


def _assert_complete_results_equal(
    stepwise: dict[str, Any], vectorized: dict[str, Any]
) -> None:
    assert stepwise.keys() == vectorized.keys()
    for name in stepwise:
        # Zero-resistance branch cycles make individual lossy-DC branch flows
        # genuinely nonunique. Both representations are audited independently.
        if name == "p_flows":
            continue
        left = stepwise[name]
        right = vectorized[name]
        if left is None or right is None:
            assert left is right, name
        elif name == "status":
            assert left == right
        else:
            _assert_numeric_equal(left, right)


def _maximum_balance_residual(build, results: dict[str, Any]) -> float:
    flows = np.asarray(results["p_flows"], dtype=float) / build.data["baseMVA"]
    injection = np.asarray(results["p_net"], dtype=float) / build.data["baseMVA"]
    return float(np.max(np.abs(flows @ build.data["A"].T + injection)))


def _assert_branch_limits(build, results: dict[str, Any]) -> None:
    flows = np.abs(np.asarray(results["p_flows"], dtype=float))
    ratings = np.asarray(build.data["f_max"], dtype=float) * build.data["baseMVA"]
    assert np.all(flows <= ratings[np.newaxis, :] + 1e-5)


def _polynomial_generation_cost(gencost: np.ndarray, pg_mw: np.ndarray) -> float:
    total = 0.0
    for generator_index, row in enumerate(gencost):
        assert int(row[0]) == 2
        count = int(row[3])
        total += float(
            np.sum(np.polyval(row[4 : 4 + count], pg_mw[:, generator_index]))
        )
    return total


def test_vectorized_network_uses_time_last_objects_leaf_bounds_and_scipy():
    stepwise, vectorized = _build_pair(4)

    assert vectorized.temporal_assembly == "vectorized"
    assert vectorized.canonicalization_backend == "SCIPY"
    assert vectorized.variables["p_flows"].shape == (9, 4)
    assert vectorized.variables["Pg"].shape == (3, 4)
    assert vectorized.expressions["p_net"].shape == (9, 4)
    assert vectorized.prob.is_dcp()
    assert vectorized.variables["p_flows"].attributes["bounds"] is not None
    assert vectorized.variables["Pg"].attributes["bounds"] is not None
    assert len(vectorized.prob.variables()) < len(stepwise.prob.variables())
    assert len(vectorized.prob.constraints) < len(stepwise.prob.constraints)


def test_vectorized_t1_matches_single_step_and_keeps_time_axis():
    case = case9()
    single = build_opf(case, formulation="lossy_dc")
    active = pd.DataFrame([case["bus"][:, 2]])
    reactive = pd.DataFrame([case["bus"][:, 3]])
    with pytest.warns(UserWarning, match="reactive power is not used"):
        vectorized = build_opf_multistep(
            case,
            active,
            reactive,
            T=1,
            formulation="lossy_dc",
            temporal_assembly="vectorized",
        )

    single_results = _solve(single)
    vector_results = _solve(vectorized)
    assert vector_results["Pg"].shape == (1, 3)
    assert vector_results["p_flows"].shape == (1, 9)
    assert vector_results["p_net"].shape == (1, 9)
    _assert_numeric_equal(single_results["objective"], vector_results["objective"])
    for name in ("Pg", "p_net", "p_load", "q_load"):
        _assert_numeric_equal(single_results[name], vector_results[name][0])
    assert _maximum_balance_residual(vectorized, vector_results) <= 1e-5


def test_short_horizon_complete_results_and_physical_balance_match():
    stepwise, vectorized = _build_pair(3)
    step_results = _solve(stepwise)
    vector_results = _solve(vectorized)

    _assert_complete_results_equal(step_results, vector_results)
    assert _maximum_balance_residual(stepwise, step_results) <= 1e-5
    assert _maximum_balance_residual(vectorized, vector_results) <= 1e-5
    _assert_branch_limits(stepwise, step_results)
    _assert_branch_limits(vectorized, vector_results)


def test_constant_only_generator_cost_is_broadcast_over_horizon():
    generators = gen_from_matpower(case9()["gen"], case9()["gencost"])
    for index, unit in enumerate(generators):
        unit.cost_type = "polynomial"
        unit.cost_coeffs = (float(index + 1),)
        unit.cost_points = None
    delta = 0.5
    stepwise, vectorized = _build_pair(3, generators=generators, delta=delta)
    step_results = _solve(stepwise)
    vector_results = _solve(vectorized)

    _assert_complete_results_equal(step_results, vector_results)
    expected_constant_cost = delta * 3 * sum(range(1, len(generators) + 1))
    assert float(vectorized.expressions["generator_cost"].value) == pytest.approx(
        expected_constant_cost,
        abs=1e-8,
    )


def test_static_fallbacks_avoid_horizon_owned_parameters_and_constants(monkeypatch):
    steps = 100
    loads = [
        Load(
            bus=5,
            p_load_mw=90.0,
            q_load_mvar=30.0,
            device_id="load",
            shedding_cost_per_mwh=1_000.0,
            max_shed_fraction=0.2,
        )
    ]
    renewable = [
        NondispatchableUnit(
            bus=8,
            p_available=20.0,
            apparent_power_rating=25.0,
            device_id="renewable",
        )
    ]
    links = [
        HVDCLink(
            from_bus=4,
            to_bus=9,
            p_min_mw=0.0,
            p_max_mw=10.0,
            loss_percent=2.0,
            device_id="hvdc",
        )
    ]
    observed_hvdc_box_shapes: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    observed_load_eligible: list[tuple[tuple[int, ...], bool]] = []
    observed_load_cost_shapes: list[tuple[int, ...]] = []
    original_coefficients = hvdc_module.loss_branch_coefficients
    original_load_channels = load_module.served_and_shed_expressions
    original_load_cost = load_module.shedding_cost_rate

    def record_coefficient_inputs(links, lower, upper, **kwargs):
        observed_hvdc_box_shapes.append(
            (np.asarray(lower).shape, np.asarray(upper).shape)
        )
        return original_coefficients(links, lower, upper, **kwargs)

    def reject_stepwise_parameters(*_args, **_kwargs):
        raise AssertionError("vectorized load preparation created stepwise parameters")

    def record_load_channels(
        p_load_mw,
        q_load_mvar,
        p_eligible_mw,
        fraction,
        sheddable_indices,
        nload,
        **kwargs,
    ):
        eligible = np.asarray(p_eligible_mw.value)
        observed_load_eligible.append((eligible.shape, eligible.flags.owndata))
        return original_load_channels(
            p_load_mw,
            q_load_mvar,
            p_eligible_mw,
            fraction,
            sheddable_indices,
            nload,
            **kwargs,
        )

    def record_load_cost(p_load_shed, cost_per_mwh, **kwargs):
        observed_load_cost_shapes.append(np.asarray(cost_per_mwh).shape)
        return original_load_cost(p_load_shed, cost_per_mwh, **kwargs)

    monkeypatch.setattr(
        hvdc_module, "loss_branch_coefficients", record_coefficient_inputs
    )
    monkeypatch.setattr(
        load_module._PreparedLoadParameters,
        "create",
        reject_stepwise_parameters,
    )
    monkeypatch.setattr(
        load_module,
        "served_and_shed_expressions",
        record_load_channels,
    )
    monkeypatch.setattr(load_module, "shedding_cost_rate", record_load_cost)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf_multistep(
            case9(),
            T=steps,
            formulation="lossy_dc",
            temporal_assembly="vectorized",
            loads=loads,
            nondispatchable=renewable,
            hvdc=links,
        )

    assert build.data["load_p_temporal_class"] == "static"
    assert build.data["load_q_temporal_class"] == "static"
    assert build.data["nd_available_temporal_class"] == "static"
    assert build.data["hvdc_temporal_class"] == "static"
    assert np.asarray(build.data["load_p_source_mw"]).shape == (1,)
    assert np.asarray(build.data["nd_available_source_mw"]).shape == (1,)
    assert np.asarray(build.data["hvdc_p_min_source_mw"]).shape == (1,)
    assert not np.asarray(build.data["nd_available"]).flags.owndata
    expected_static_pd = (
        np.asarray(build.data["load_p_source_mw"])
        @ np.asarray(build.data["Cload"]).T
        / float(build.data["baseMVA"])
    )
    pd_series = np.asarray(build.data["Pd_series"])
    np.testing.assert_allclose(
        pd_series,
        np.broadcast_to(expected_static_pd[np.newaxis, :], pd_series.shape),
    )
    assert pd_series.shape == (steps, int(build.data["nb"]))
    assert not pd_series.flags.owndata
    assert pd_series.strides[0] == 0
    assert observed_hvdc_box_shapes == [((1,), (1,))]
    assert observed_load_eligible == [((1, 1), False)]
    assert observed_load_cost_shapes == [(1, 1)]
    assert {parameter.name() for parameter in build.prob.parameters()}.isdisjoint(
        {
            "load_p_mw",
            "load_p_eligible_mw",
            "load_eligibility_mask",
            "load_q_mvar",
        }
    )
    assert np.shares_memory(
        np.asarray(build.expressions["p_load"].value),
        np.asarray(build.data["load_p_source_mw"]),
    )
    assert np.shares_memory(
        np.asarray(build.expressions["q_load"].value),
        np.asarray(build.data["load_q_source_mvar"]),
    )
    for name in ("load_shed_fraction", "p_nd", "p_hvdc_in"):
        bounds = build.variables[name].attributes["bounds"]
        assert bounds is not None
        assert all(not np.asarray(face).flags.owndata for face in bounds)


def _component_inputs(steps: int) -> dict[str, Any]:
    loads = [
        Load(
            bus=5,
            p_load_mw=90.0,
            q_load_mvar=30.0,
            device_id="load-5",
            shedding_cost_per_mwh=1_000.0,
            max_shed_fraction=0.2,
        ),
        Load(
            bus=7,
            p_load_mw=100.0,
            q_load_mvar=35.0,
            device_id="load-7",
        ),
    ]
    storage = [
        StorageUnitIdeal(
            bus=6,
            apparent_power_rating=20.0,
            capacity=50.0,
            initial_soc=25.0,
            aging_weight=0.01,
            terminal_soc=25.0,
            terminal_constraint="equality",
            device_id="storage-6",
        )
    ]
    nondispatchable = [
        NondispatchableUnit(
            bus=8,
            p_available=20.0,
            apparent_power_rating=25.0,
            device_id="renewable-8",
        )
    ]
    links = [
        HVDCLink(
            from_bus=4,
            to_bus=9,
            p_min_mw=0.0,
            p_max_mw=10.0,
            loss_percent=2.0,
            cost_coeffs=(0.0, 0.1, 0.0),
            device_id="hvdc-4-9",
        )
    ]
    return {
        "loads": loads,
        "df_load_p": pd.DataFrame(
            np.column_stack(
                [np.linspace(80.0, 95.0, steps), np.linspace(110.0, 90.0, steps)]
            ),
            columns=["load-5", "load-7"],
        ),
        "df_load_q": pd.DataFrame(
            np.tile([30.0, 35.0], (steps, 1)),
            columns=["load-5", "load-7"],
        ),
        "storage": storage,
        "nondispatchable": nondispatchable,
        "df_nd": pd.DataFrame(
            np.resize(np.array([30.0, 0.0, 10.0]), steps),
            columns=["renewable-8"],
        ),
        "hvdc": links,
        "df_hvdc_min": pd.DataFrame(np.zeros(steps), columns=["hvdc-4-9"]),
        "df_hvdc_max": pd.DataFrame(
            np.linspace(10.0, 6.0, steps), columns=["hvdc-4-9"]
        ),
    }


def test_full_component_matrix_matches_complete_public_contract():
    steps = 3
    delta = 0.5
    kwargs = _component_inputs(steps)
    builds = []
    for assembly in ("stepwise", "vectorized"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            builds.append(
                build_opf_multistep(
                    case9(),
                    T=steps,
                    formulation="lossy_dc",
                    temporal_assembly=assembly,
                    delta=delta,
                    **kwargs,
                )
            )
    stepwise, vectorized = builds
    step_results = _solve(stepwise)
    vector_results = _solve(vectorized)

    _assert_complete_results_equal(step_results, vector_results)
    expected_interval_pd = (
        kwargs["df_load_p"].to_numpy(dtype=float)
        @ np.asarray(vectorized.data["Cload"]).T
        / float(vectorized.data["baseMVA"])
    )
    interval_pd = np.asarray(vectorized.data["Pd_series"])
    np.testing.assert_allclose(interval_pd, expected_interval_pd)
    assert vectorized.data["load_p_temporal_class"] == "interval"
    assert interval_pd.flags.owndata
    pg = np.asarray(vector_results["Pg"], dtype=float)
    power = np.asarray(vector_results["b"], dtype=float)
    flows_pu = (
        np.asarray(vector_results["p_flows"], dtype=float) / vectorized.data["baseMVA"]
    )
    p_shed = np.asarray(vector_results["p_load_shed"], dtype=float)
    p_hvdc = np.asarray(vector_results["p_hvdc_in"], dtype=float)
    generation_cost = delta * _polynomial_generation_cost(
        np.asarray(vectorized.data["gencost"], dtype=float), pg
    )
    storage_cost = delta * float(
        np.sum(
            np.asarray(vectorized.data["storage_aging_weight"], dtype=float)
            * np.sum(np.abs(power), axis=0)
        )
    )
    sheddable = np.asarray(vectorized.data["sheddable_load_indices"], dtype=int)
    shedding_cost = delta * float(
        np.sum(
            p_shed
            * np.asarray(vectorized.data["load_shedding_cost_per_mwh"], dtype=float)[
                sheddable
            ]
        )
    )
    hvdc_cost = 0.0
    for link_index, link in enumerate(kwargs["hvdc"]):
        c0, c1, c2 = link.cost_coeffs
        hvdc_cost += delta * float(
            np.sum(
                c0
                + c1 * np.abs(p_hvdc[:, link_index])
                + c2 * p_hvdc[:, link_index] ** 2
            )
        )
    dc_loss_cost = delta * float(
        vectorized.data["loss_weight"]
        * np.sum(np.asarray(vectorized.data["r"]) * np.sum(flows_pu**2, axis=0))
    )
    reconstructed_objective = (
        generation_cost + storage_cost + shedding_cost + hvdc_cost + dc_loss_cost
    )
    assert float(vector_results["objective"]) == pytest.approx(
        reconstructed_objective,
        abs=1e-5,
    )
    assert _maximum_balance_residual(vectorized, vector_results) <= 1e-5
    _assert_branch_limits(vectorized, vector_results)
    soc = np.asarray(vector_results["soc"], dtype=float)
    initial = np.asarray(vectorized.data["storage_initial_soc"], dtype=float)
    predecessor = np.vstack([initial, soc[:-1]])
    np.testing.assert_allclose(soc, predecessor - delta * power, atol=1e-5)
    np.testing.assert_allclose(soc[-1], [25.0], atol=1e-5)
    rating = np.asarray(vectorized.data["storage_apparent_power_rating"])
    capacity = np.asarray(vectorized.data["storage_capacity"])
    assert np.all(np.abs(power) <= rating[np.newaxis, :] + 1e-5)
    assert np.all(soc >= -1e-5)
    assert np.all(soc <= capacity[np.newaxis, :] + 1e-5)
    assert np.min(vector_results["curtailment"]) >= -1e-5
    availability = kwargs["df_nd"].to_numpy(dtype=float)
    nd_rating = np.asarray(vectorized.data["nd_apparent_power_rating"], dtype=float)
    p_nd = np.asarray(vector_results["p_nd"], dtype=float)
    assert np.all(p_nd >= -1e-5)
    assert np.all(p_nd <= np.minimum(availability, nd_rating) + 1e-5)
    np.testing.assert_allclose(
        vector_results["curtailment"], availability - p_nd, atol=1e-5
    )
    assert np.min(vector_results["hvdc_loss"]) >= -1e-5
    hvdc_lower = kwargs["df_hvdc_min"].to_numpy(dtype=float)
    hvdc_upper = kwargs["df_hvdc_max"].to_numpy(dtype=float)
    assert np.all(p_hvdc >= hvdc_lower - 1e-5)
    assert np.all(p_hvdc <= hvdc_upper + 1e-5)
    coefficient = -1.0 / (1.0 - kwargs["hvdc"][0].loss_percent / 100.0)
    np.testing.assert_allclose(
        vector_results["p_hvdc_out"], coefficient * p_hvdc, atol=1e-5
    )
    case = case9()
    assert np.all(pg >= case["gen"][:, 9] - 1e-5)
    assert np.all(pg <= case["gen"][:, 8] + 1e-5)
    fractions = np.asarray(vector_results["load_shed_fraction"], dtype=float)
    max_fraction = np.asarray(vectorized.data["load_max_shed_fraction"])[sheddable]
    assert np.all(fractions >= -1e-5)
    assert np.all(fractions <= max_fraction + 1e-5)
    reconstructed_load = np.asarray(vector_results["p_load_served"], dtype=float).copy()
    reconstructed_load[:, 0] += np.asarray(vector_results["p_load_shed"], dtype=float)[
        :, 0
    ]
    np.testing.assert_allclose(reconstructed_load, vector_results["p_load"], atol=1e-5)
    expected_ens_by_load = delta * np.sum(p_shed, axis=0)
    np.testing.assert_allclose(
        vector_results["energy_not_served_by_load"],
        expected_ens_by_load,
        atol=1e-5,
    )
    assert float(vector_results["energy_not_served"]) == pytest.approx(
        float(np.sum(expected_ens_by_load)),
        abs=1e-5,
    )


@pytest.mark.parametrize(
    ("terminal_constraint", "terminal_cost", "terminal_weight"),
    [
        ("equality", None, None),
        ("shortfall", None, None),
        (None, "quadratic", 2.0),
    ],
)
def test_storage_terminal_modes_match(
    terminal_constraint: str | None,
    terminal_cost: str | None,
    terminal_weight: float | None,
):
    delta = 0.5
    storage = [
        StorageUnitIdeal(
            bus=6,
            apparent_power_rating=10.0,
            capacity=40.0,
            initial_soc=20.0,
            terminal_soc=20.0,
            terminal_constraint=terminal_constraint,
            terminal_cost=terminal_cost,
            terminal_weight=terminal_weight,
            device_id="storage",
        )
    ]
    stepwise, vectorized = _build_pair(2, storage=storage, delta=delta)
    step_results = _solve(stepwise)
    vector_results = _solve(vectorized)
    _assert_complete_results_equal(step_results, vector_results)
    if terminal_cost is not None:
        pg = np.asarray(vector_results["Pg"], dtype=float)
        power = np.asarray(vector_results["b"], dtype=float)
        flows_pu = (
            np.asarray(vector_results["p_flows"], dtype=float)
            / vectorized.data["baseMVA"]
        )
        generation_cost = delta * _polynomial_generation_cost(
            np.asarray(vectorized.data["gencost"], dtype=float), pg
        )
        storage_cost = delta * float(
            np.sum(
                np.asarray(vectorized.data["storage_aging_weight"], dtype=float)
                * np.sum(np.abs(power), axis=0)
            )
        )
        dc_loss_cost = delta * float(
            vectorized.data["loss_weight"]
            * np.sum(
                np.asarray(vectorized.data["r"], dtype=float)
                * np.sum(flows_pu**2, axis=0)
            )
        )
        deviation = float(vector_results["soc"][-1, 0] - storage[0].terminal_soc)
        terminal_penalty = float(terminal_weight) * deviation**2
        reconstructed_objective = (
            generation_cost + storage_cost + dc_loss_cost + terminal_penalty
        )
        assert float(vector_results["objective"]) == pytest.approx(
            reconstructed_objective,
            abs=1e-5,
        )


def test_hvdc_directional_loss_branches_match_stepwise_priority():
    steps = 4
    link = HVDCLink(
        from_bus=4,
        to_bus=9,
        p_min_mw=-10.0,
        p_max_mw=10.0,
        loss_percent=5.0,
        cost_coeffs=(0.0, 0.1, 0.0),
        device_id="hvdc",
    )
    lower = pd.DataFrame([0.0, -10.0, -5.0, 0.0], columns=["hvdc"])
    upper = pd.DataFrame([10.0, -1.0, 5.0, 0.0], columns=["hvdc"])
    results = []
    for assembly in ("stepwise", "vectorized"):
        active, reactive = _legacy_frames(steps)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(),
                active,
                reactive,
                T=steps,
                formulation="lossy_dc",
                hvdc=[link],
                df_hvdc_min=lower,
                df_hvdc_max=upper,
                temporal_assembly=assembly,
            )
        results.append(_solve(build))

    _assert_complete_results_equal(results[0], results[1])
    p_in = np.asarray(results[1]["p_hvdc_in"], dtype=float)[:, 0]
    p_out = np.asarray(results[1]["p_hvdc_out"], dtype=float)[:, 0]
    coefficients = np.array([-1.0 / 0.95, -0.95, -1.0, -1.0 / 0.95])
    np.testing.assert_allclose(p_out, coefficients * p_in, atol=1e-5)


def test_piecewise_linear_generator_cost_matches_stepwise():
    case = case9()
    generators = gen_from_matpower(case["gen"], case["gencost"])
    first = generators[0]
    first.cost_type = "piecewise_linear"
    first.cost_coeffs = None
    first.cost_points = (
        (first.p_min_mw, 0.0),
        ((first.p_min_mw + first.p_max_mw) / 2.0, 500.0),
        (first.p_max_mw, 1_200.0),
    )
    stepwise, vectorized = _build_pair(2, generators=generators)
    _assert_complete_results_equal(_solve(stepwise), _solve(vectorized))


def test_infeasible_and_unsolved_schemas_match_and_keep_fixed_load_inputs():
    case = case9()
    case["gen"][:, 8] = 0.0
    case["gen"][:, 9] = 0.0
    active = pd.DataFrame([case["bus"][:, 2]])
    builds = []
    for assembly in ("stepwise", "vectorized"):
        builds.append(
            build_opf_multistep(
                case,
                active,
                T=1,
                formulation="lossy_dc",
                temporal_assembly=assembly,
            )
        )
    stepwise, vectorized = builds
    unsolved = [extract_results(build) for build in builds]
    assert unsolved[0].keys() == unsolved[1].keys()
    for build in builds:
        build.solve()
    failed = [extract_results(build) for build in builds]

    assert failed[0].keys() == failed[1].keys()
    assert failed[0]["status"] in {"infeasible", "infeasible_inaccurate"}
    assert failed[1]["status"] in {"infeasible", "infeasible_inaccurate"}
    for result in failed:
        assert result["Pg"] is None
        assert result["p_flows"] is None
        assert result["p_net"] is None
        assert np.isnan(result["objective"])
        np.testing.assert_array_equal(result["p_load"], active.to_numpy())
        np.testing.assert_array_equal(result["p_load_served"], active.to_numpy())


def test_solver_exception_retains_stable_production_schema(monkeypatch):
    stepwise, vectorized = _build_pair(2)

    def fail_solve(_problem, **_kwargs):
        raise cp.error.SolverError("synthetic solver exception")

    monkeypatch.setattr(cp.Problem, "solve", fail_solve)
    for build in (stepwise, vectorized):
        with pytest.raises(cp.error.SolverError, match="synthetic"):
            build.solve()
    step_results = extract_results(stepwise)
    vector_results = extract_results(vectorized)

    _assert_complete_results_equal(step_results, vector_results)
    assert step_results["status"] is None
    assert vector_results["status"] is None
    assert step_results["Pg"] is None
    assert vector_results["Pg"] is None
    assert np.isnan(step_results["objective"])
    assert np.isnan(vector_results["objective"])


def test_partial_unusable_primal_retains_stable_production_schema():
    stepwise, vectorized = _build_pair(2)
    _solve(stepwise)
    _solve(vectorized)
    stepwise.variables["Pg"][0].value = None
    vectorized.variables["Pg"].value = None

    step_results = extract_results(stepwise)
    vector_results = extract_results(vectorized)

    _assert_complete_results_equal(step_results, vector_results)
    assert step_results["status"] == cp.OPTIMAL
    assert vector_results["status"] == cp.OPTIMAL
    assert step_results["Pg"] is None
    assert vector_results["Pg"] is None
    assert step_results["p_net"] is None
    assert vector_results["p_net"] is None
    assert np.isfinite(step_results["objective"])
    assert np.isfinite(vector_results["objective"])
    assert step_results["objective"] == pytest.approx(
        vector_results["objective"], abs=ATOL
    )


@pytest.mark.parametrize("formulation", ["ac", "singlenode_dc"])
def test_unqualified_vectorized_formulations_are_rejected(formulation: str):
    active, reactive = _legacy_frames(1)
    context = nullcontext()
    with context:
        with pytest.raises(NotImplementedError, match="only.*lossy_dc"):
            build_opf_multistep(
                case9(),
                active,
                reactive,
                T=1,
                formulation=formulation,
                temporal_assembly="vectorized",
            )


def test_vectorized_solve_rejects_conflicting_canonicalization_backend():
    _, vectorized = _build_pair(1)
    with pytest.raises(ValueError, match="require SCIPY"):
        vectorized.solve(canon_backend=cp.CPP_CANON_BACKEND)
