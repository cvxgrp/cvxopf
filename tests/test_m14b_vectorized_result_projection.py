"""Focused result-compatibility gates for M14b time-last horizons."""

from types import MappingProxyType

import cvxpy as cp
import numpy as np
import pytest

from cvxopf._temporal_assembly import (
    ResultProjectionRegistry,
    ResultProjectionSpec,
)
from cvxopf.problem import OPFBuild
from cvxopf.results import _solved_expression_value, extract_results


def _interval(name: str, native_shape: tuple[int, ...]) -> ResultProjectionSpec:
    return ResultProjectionSpec(name, native_shape, native_shape, "interval")


def _horizon(name: str, native_shape: tuple[int, ...]) -> ResultProjectionSpec:
    return ResultProjectionSpec(name, native_shape, native_shape, "horizon")


def _assign(variable: cp.Variable, values: np.ndarray) -> cp.Variable:
    variable.value = np.asarray(values, dtype=float)
    return variable


def _lossy_vectorized_build(
    *, solved_values: bool = True, horizon: int = 3
) -> OPFBuild:
    pg = cp.Variable((2, horizon), name="Pg")
    flow = cp.Variable((1, horizon), name="p_flows")
    battery = cp.Variable((1, horizon), name="b")
    soc = cp.Variable((1, horizon + 1), name="soc")
    renewable = cp.Variable((1, horizon), name="p_nd")
    hvdc_in = cp.Variable((1, horizon), name="p_hvdc_in")
    hvdc_out = cp.Variable((1, horizon), name="p_hvdc_out")
    shed_fraction = cp.Variable((1, horizon), name="load_shed_fraction")

    if solved_values:
        _assign(pg, np.arange(2 * horizon).reshape(2, horizon) / 100.0)
        _assign(flow, np.arange(horizon).reshape(1, horizon) / 100.0)
        _assign(battery, np.array([[1.0, -2.0, 3.0]])[:, :horizon])
        _assign(soc, np.array([[10.0, 11.0, 9.0, 12.0]])[:, : horizon + 1])
        _assign(renewable, np.array([[4.0, 5.0, 6.0]])[:, :horizon])
        _assign(hvdc_in, np.array([[-2.0, -3.0, -4.0]])[:, :horizon])
        _assign(hvdc_out, np.array([[1.8, 2.7, 3.6]])[:, :horizon])
        _assign(shed_fraction, np.array([[0.1, 0.0, 0.2]])[:, :horizon])

    p_load = cp.Constant(
        np.array([[10.0, 11.0, 12.0], [20.0, 21.0, 22.0]])[:, :horizon]
    )
    q_load = cp.Constant(np.array([[1.0, 1.1, 1.2], [2.0, 2.1, 2.2]])[:, :horizon])
    p_shed = cp.multiply(
        shed_fraction,
        cp.Constant(np.array([[10.0, 11.0, 12.0]])[:, :horizon]),
    )
    p_served = cp.vstack((p_load[0, :] - p_shed[0, :], p_load[1, :]))
    p_net = cp.vstack((pg[0, :] - flow[0, :], pg[1, :] + flow[0, :]))
    expressions = {
        "p_net": p_net,
        "storage_cost": cp.sum(cp.abs(battery)),
        "p_load": p_load,
        "q_load": q_load,
        "p_load_served": p_served,
        "p_load_shed": p_shed,
        "load_shed_fraction": shed_fraction,
        "p_load_shed_total": cp.sum(p_shed, axis=0),
        "energy_not_served_by_load": cp.sum(p_shed, axis=1),
        "energy_not_served": cp.sum(p_shed),
        "load_shedding_cost": cp.sum(p_shed),
    }
    variables = {
        "Pg": pg,
        "p_flows": flow,
        "b": battery,
        "soc": soc,
        "p_nd": renewable,
        "p_hvdc_in": hvdc_in,
        "p_hvdc_out": hvdc_out,
        "load_shed_fraction": shed_fraction,
    }
    variable_projections = {
        name: _interval(name, tuple(variable.shape[:-1]))
        for name, variable in variables.items()
        if name != "soc"
    }
    variable_projections["soc"] = ResultProjectionSpec(
        "soc", (1,), (1,), "post_step_boundaries"
    )
    expression_projections = {
        "p_net": _interval("p_net", (2,)),
        "storage_cost": _horizon("storage_cost", ()),
        "p_load": _interval("p_load", (2,)),
        "q_load": _interval("q_load", (2,)),
        "p_load_served": _interval("p_load_served", (2,)),
        "p_load_shed": _interval("p_load_shed", (1,)),
        "load_shed_fraction": _interval("load_shed_fraction", (1,)),
        "p_load_shed_total": _interval("p_load_shed_total", ()),
        "energy_not_served_by_load": _horizon("energy_not_served_by_load", (1,)),
        "energy_not_served": _horizon("energy_not_served", ()),
        "load_shedding_cost": _horizon("load_shedding_cost", ()),
    }
    data = {
        "baseMVA": 100.0,
        "T": horizon,
        "ns": 1,
        "storage_device_ids": np.array(["battery"], dtype=object),
        "storage_device_id_is_explicit": np.array([True]),
        "storage_terminal_soc": np.array([12.0]),
        "nnd": 1,
        "nd_available": np.array([[7.0], [8.0], [9.0]])[:horizon],
        "n_hvdc": 1,
        "nload": 2,
        "nsheddable": 1,
    }
    problem = cp.Problem(cp.Minimize(cp.sum_squares(pg)))
    return OPFBuild(
        problem,
        variables,
        data,
        "lossy_dc",
        True,
        expressions=expressions,
        temporal_assembly="vectorized",
        result_projections=ResultProjectionRegistry(
            variables=variable_projections,
            expressions=expression_projections,
        ),
    )


def _ac_vectorized_build(*, solved_values: bool = True) -> OPFBuild:
    horizon = 1
    nb = 2
    pg = cp.Variable((1, horizon), name="Pg")
    qg = cp.Variable((1, horizon), name="Qg")
    voltage = cp.Variable((nb, 1, horizon), name="v")
    angle = cp.Variable((nb, 1, horizon), name="theta")
    if solved_values:
        _assign(pg, np.array([[0.5]]))
        _assign(qg, np.array([[0.1]]))
        _assign(voltage, np.array([[[1.0]], [[1.02]]]))
        _assign(angle, np.array([[[0.0]], [[0.1]]]))
    expressions = {
        "p_net": cp.vstack((pg[0, :], -pg[0, :])),
        "q_net": cp.vstack((qg[0, :], -qg[0, :])),
        "branch_p_from_pu": pg,
        "branch_q_from_pu": qg,
        "branch_p_to_pu": -0.95 * pg,
        "branch_q_to_pu": -0.9 * qg,
    }
    projections = ResultProjectionRegistry(
        variables={
            "Pg": _interval("Pg", (1,)),
            "Qg": _interval("Qg", (1,)),
            "v": ResultProjectionSpec("v", (nb, 1), (nb,), "interval"),
            "theta": ResultProjectionSpec("theta", (nb, 1), (nb,), "interval"),
        },
        expressions={
            name: _interval(name, (nb,) if name in {"p_net", "q_net"} else (1,))
            for name in expressions
        },
    )
    return OPFBuild(
        cp.Problem(cp.Minimize(cp.sum_squares(pg))),
        {"Pg": pg, "Qg": qg, "v": voltage, "theta": angle},
        {"baseMVA": 100.0, "T": horizon},
        "ac",
        False,
        expressions=expressions,
        temporal_assembly="vectorized",
        result_projections=projections,
    )


def test_vectorized_lossy_results_restore_complete_public_time_first_contract():
    build = _lossy_vectorized_build()

    results = extract_results(build)

    assert results["Pg"].shape == (3, 2)
    assert results["p_flows"].shape == (3, 1)
    assert results["p_net"].shape == (3, 2)
    assert results["b"].shape == (3, 1)
    assert results["soc"].shape == (3, 1)
    np.testing.assert_array_equal(results["soc"][:, 0], [11.0, 9.0, 12.0])
    np.testing.assert_array_equal(results["storage_terminal_deviation"], [0.0])
    assert results["p_nd"].shape == (3, 1)
    np.testing.assert_array_equal(results["curtailment"][:, 0], [3.0, 3.0, 3.0])
    assert results["p_hvdc_in"].shape == (3, 1)
    np.testing.assert_allclose(results["hvdc_loss"][:, 0], [0.2, 0.3, 0.4])
    assert results["p_load"].shape == (3, 2)
    assert results["p_load_shed"].shape == (3, 1)
    assert results["p_load_shed_total"].shape == (3,)
    assert results["energy_not_served_by_load"].shape == (1,)
    assert np.ndim(results["energy_not_served"]) == 0
    assert np.ndim(results["storage_cost"]) == 0
    assert np.ndim(results["load_shedding_cost"]) == 0


def test_vectorized_unavailable_primal_keeps_constants_and_schema_policy():
    build = _lossy_vectorized_build(solved_values=False)

    results = extract_results(build)

    assert results["Pg"] is None
    assert results["p_flows"] is None
    assert results["p_net"] is None
    assert results["b"] is None
    assert results["soc"] is None
    assert results["storage_terminal_deviation"] is None
    assert results["p_nd"] is None
    assert results["curtailment"] is None
    assert results["p_hvdc_in"] is None
    assert results["hvdc_loss"] is None
    assert results["p_load"].shape == (3, 2)
    assert results["q_load"].shape == (3, 2)
    assert results["p_load_served"] is None
    assert np.isnan(results["storage_cost"])
    assert np.isnan(results["load_shedding_cost"])


def test_vectorized_t1_result_axes_remain_explicit():
    build = _lossy_vectorized_build(horizon=1)

    results = extract_results(build)

    assert results["Pg"].shape == (1, 2)
    assert results["p_flows"].shape == (1, 1)
    assert results["b"].shape == (1, 1)
    assert results["soc"].shape == (1, 1)
    assert results["p_load_shed_total"].shape == (1,)


def test_vectorized_ac_projection_flattens_native_column_without_shape_inference():
    nb = 2
    build = _ac_vectorized_build()

    results = extract_results(build)

    assert results["Vm"].shape == (1, nb)
    assert results["Va_deg"].shape == (1, nb)
    assert results["branch_p_from"].shape == (1, 1)
    assert results["branch_s_to"].shape == (1, 1)


@pytest.mark.parametrize("malformed", [False, True])
def test_unavailable_vectorized_ac_still_validates_branch_projection(malformed):
    build = _ac_vectorized_build(solved_values=False)
    projections = dict(build.result_projections.expressions)
    if malformed:
        projections["branch_p_from_pu"] = _interval("branch_p_from_pu", (2,))
    else:
        del projections["branch_p_from_pu"]
    build.result_projections = ResultProjectionRegistry(
        variables=build.result_projections.variables,
        expressions=projections,
    )

    with pytest.raises(ValueError, match="branch_p_from_pu"):
        extract_results(build)


def test_vectorized_extraction_rejects_missing_or_malformed_projection():
    build = _lossy_vectorized_build()
    build.result_projections = ResultProjectionRegistry(
        variables={
            name: projection
            for name, projection in build.result_projections.variables.items()
            if name != "Pg"
        },
        expressions=build.result_projections.expressions,
    )
    with pytest.raises(ValueError, match="Pg.*no declared"):
        extract_results(build)

    build = _lossy_vectorized_build()
    malformed = dict(build.result_projections.variables)
    malformed["Pg"] = _interval("Pg", (3,))
    build.result_projections = ResultProjectionRegistry(
        variables=malformed,
        expressions=build.result_projections.expressions,
    )
    with pytest.raises(ValueError, match="declares source shape"):
        extract_results(build)

    build = _lossy_vectorized_build(solved_values=False)
    malformed = dict(build.result_projections.variables)
    malformed["Pg"] = _interval("Pg", (3,))
    build.result_projections = ResultProjectionRegistry(
        variables=malformed,
        expressions=build.result_projections.expressions,
    )
    with pytest.raises(ValueError, match="declares source shape"):
        extract_results(build)


@pytest.mark.parametrize("name", ["p_net", "storage_cost"])
def test_vectorized_extraction_rejects_missing_required_expression(name):
    build = _lossy_vectorized_build()
    del build.expressions[name]

    with pytest.raises(
        ValueError,
        match=rf"missing required vectorized result expression {name!r}",
    ):
        extract_results(build)


def test_vectorized_extraction_rejects_missing_required_variable():
    build = _lossy_vectorized_build()
    del build.variables["Pg"]

    with pytest.raises(
        ValueError,
        match="missing required vectorized result variable 'Pg'",
    ):
        extract_results(build)


def test_stepwise_scalar_expression_preserves_required_key_contract():
    build = OPFBuild(
        cp.Problem(cp.Minimize(cp.Constant(0.0))),
        {},
        {"baseMVA": 100.0},
        "lossy_dc",
        True,
    )

    with pytest.raises(KeyError, match="required_cost"):
        _solved_expression_value(build, "required_cost")


def test_vectorized_extraction_does_not_construct_cvxpy_variables(monkeypatch):
    build = _lossy_vectorized_build()
    original_variables = MappingProxyType(dict(build.variables))

    def forbidden(*args, **kwargs):
        raise AssertionError("result extraction must not create CVXPY variables")

    monkeypatch.setattr(cp, "Variable", forbidden)
    results = extract_results(build)

    assert results["Pg"].shape == (3, 2)
    assert all(
        build.variables[name] is value for name, value in original_variables.items()
    )
