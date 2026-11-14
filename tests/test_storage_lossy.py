"""Tests for lossy storage (plans/milestone-18-lossy-storage.md, Section 11)."""

import warnings
from dataclasses import replace

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import (
    Load,
    NondispatchableUnit,
    NumericalPreparation,
    OPFOptions,
    SimultaneousOperationError,
    StorageUnitIdeal,
    StorageUnitLossy,
    audit_socp_relaxation,
    build_opf,
    build_opf_multistep,
    extract_results,
    gen_from_matpower,
)
from cvxopf import storage_lossy
from cvxopf.testcases import case9
from cvxopf.network import make_ybus_matpower, reindex_case_to_consecutive

SPLIT_ATOL = storage_lossy.SPLIT_TOLERANCE_MW
ENERGY_ATOL = 1e-5  # MWh
OBJ_RTOL = 1e-6
BALANCE_ATOL = 1e-4  # MVA, reconstructed from voltage and original device inputs

CONVEX_FORMULATIONS = ["lossy_dc", "singlenode_dc", "socp"]
ALL_FORMULATIONS = [*CONVEX_FORMULATIONS, "ac"]
FLEET_PREPARATIONS = [
    (f, a, p) for f in ALL_FORMULATIONS for a in ("stepwise", "vectorized") for p in (False, True)
] + [("ac", a, "cost") for a in ("stepwise", "vectorized")] + [
    ("ac", "vectorized", "component_first")
]


def _base_preparation(formulation, enabled):
    return NumericalPreparation(
        normalize_device_limits=bool(enabled) and formulation in {"ac", "socp"},
        exact_fixed_boxes=bool(enabled),
        canonical_scaling="joint5" if enabled and formulation != "ac" else "none",
        cost_coordinates=enabled in ("cost", "component_first"),
        objective_assembly="component_first" if enabled == "component_first" else "hourly",
    )


def _unit(**fields):
    base = dict(
        bus=5,
        apparent_power_rating=40.0,
        capacity=100.0,
        initial_soc=50.0,
        charge_efficiency=0.95,
        discharge_efficiency=0.9,
        self_discharge_per_month=0.01,
        device_id="battery",
    )
    base.update(fields)
    return StorageUnitLossy(**base)


def _load_frames(scales):
    ppc = case9()
    df_P = pd.DataFrame(np.outer(scales, ppc["bus"][:, 2]))
    df_Q = pd.DataFrame(np.outer(scales, ppc["bus"][:, 3]))
    return df_P, df_Q


def _check_ac_balance(results, scales, lossy, *, ideal=(), renewable=()):
    """Independent complex nodal balance; never read model residuals."""
    case, _ = reindex_case_to_consecutive(case9())
    voltage = results["Vm"]*np.exp(1j*np.deg2rad(results["Va_deg"]))
    network = case["baseMVA"]*voltage*np.conj(voltage @ make_ybus_matpower(case).T)
    injection = -np.asarray(scales)[:, None]*(case["bus"][:, 2]+1j*case["bus"][:, 3])
    for k, gen in enumerate(case["gen"]):
        injection[:, int(gen[0])] += results["Pg"][:, k]+1j*results["Qg"][:, k]
    for units, p_key, q_key in ((lossy, "ls_b", "ls_b_q"), (ideal, "b", "b_q"),
                                (renewable, "p_nd", "q_nd")):
        for k, unit in enumerate(units):
            injection[:, unit.bus-1] += results[p_key][:, k]+1j*results[q_key][:, k]
    np.testing.assert_allclose(network, injection, rtol=0., atol=BALANCE_ATOL)


def _solve_multistep(formulation, scales, *, temporal_assembly="vectorized",
                     delta=1.0, **devices):
    df_P, df_Q = _load_frames(scales)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf_multistep(
            case9(), df_P, df_Q if formulation in {"ac", "socp"} else None,
            T=len(scales), formulation=formulation, delta=delta,
            temporal_assembly=temporal_assembly, **devices,
        )
        build.solve()
    return build, extract_results(build)


@pytest.mark.parametrize("formulation", ALL_FORMULATIONS)
@pytest.mark.parametrize("assembly", ["single", "stepwise", "vectorized"])
@pytest.mark.parametrize("fleet", ["absent", "empty", "lossy", "mixed"])
def test_explicit_disabled_policy_preserves_default_graph_and_schema(
    monkeypatch, formulation, assembly, fleet
):
    devices = {}
    if fleet != "absent":
        devices["lossy_storage"] = [] if fleet == "empty" else [_unit()]
    if fleet == "mixed":
        devices["storage"] = [StorageUnitIdeal(bus=5, apparent_power_rating=10,
                                              capacity=40, initial_soc=20)]
    builds = []
    for options in ({}, {"options": OPFOptions(numerical_preparation=NumericalPreparation())}):
        if assembly == "single":
            builds.append(build_opf(case9(), formulation=formulation, **devices, **options))
        else:
            df_P, df_Q = _load_frames([1.])
            builds.append(build_opf_multistep(
                case9(), df_P, df_Q, T=1, formulation=formulation,
                temporal_assembly=assembly, **devices, **options
            ))
    left, right = builds
    assert [(v.name(), v.shape, repr(v.attributes)) for v in left.prob.variables()] == [
        (v.name(), v.shape, repr(v.attributes)) for v in right.prob.variables()
    ]
    assert str(left.prob.objective) == str(right.prob.objective)
    assert [str(c) for c in left.prob.constraints] == [str(c) for c in right.prob.constraints]
    assert extract_results(left).keys() == extract_results(right).keys()
    calls = []
    monkeypatch.setattr(cp.Problem, "solve", lambda prob, **kw: calls.append(kw))
    for build in builds:
        ids = tuple(id(v) for v in build.prob.variables())
        build.solve()
        assert not build._exact_boxes and build.preparation_evidence is None
        assert tuple(id(v) for v in build.prob.variables()) == ids
    assert calls[0] == calls[1]


# ---------------------------------------------------------------------------
# Gate 1: validation, algebra, and convexity
# ---------------------------------------------------------------------------

class TestValidation:
    @pytest.mark.parametrize("field, value", [
        ("charge_efficiency", 0.0),
        ("charge_efficiency", 1.1),
        ("discharge_efficiency", 0.0),
        ("discharge_efficiency", np.nan),
        ("self_discharge_per_month", -0.1),
        ("self_discharge_per_month", 1.0),
        ("aging_weight", 0.0),
        ("capacity", -1.0),
    ])
    def test_invalid_field_rejected(self, field, value):
        with pytest.raises(ValueError, match=field):
            build_opf(case9(), formulation="singlenode_dc",
                      lossy_storage=[_unit(**{field: value})])

    @pytest.mark.parametrize("formulation", CONVEX_FORMULATIONS)
    @pytest.mark.parametrize("assembly", ["single", "stepwise", "vectorized"])
    def test_convex_cost_coordinates_remain_rejected(self, assembly, formulation):
        options = OPFOptions(
            numerical_preparation=NumericalPreparation(cost_coordinates=True)
        )
        with pytest.raises(ValueError, match="AC only"):
            if assembly == "single":
                build_opf(case9(), formulation=formulation, lossy_storage=[_unit()], options=options)
            else:
                df_P, df_Q = _load_frames([1., 1.])
                build_opf_multistep(case9(), df_P, df_Q, T=2, formulation=formulation,
                                   temporal_assembly=assembly, lossy_storage=[_unit()],
                                   options=options)


class TestAlgebra:
    @pytest.mark.parametrize("shape", [(2,), (2, 3)])
    def test_device_terms_are_dcp(self, shape):
        units = [_unit(), _unit(bus=6, device_id="b2")]
        p_ch, p_dis, b_q = cp.Variable(shape), cp.Variable(shape), cp.Variable(shape)
        soc = cp.Variable(shape)
        constraints = storage_lossy.operating_constraints(units, p_ch, p_dis, soc, b_q)
        assert all(constraint.is_dcp() for constraint in constraints)
        assert storage_lossy.throughput_cost_rate(units, p_ch, p_dis).is_convex()

    def test_coupling_constraints_are_dcp(self):
        units = [_unit(terminal_soc=60.0, terminal_constraint="equality")]
        p_ch, p_dis, soc = cp.Variable((1, 3)), cp.Variable((1, 3)), cp.Variable((1, 4))
        constraints = storage_lossy.vectorized_coupling_constraints(
            units, p_ch, p_dis, soc, 0.5
        )
        assert all(constraint.is_dcp() for constraint in constraints)

    def test_retention_is_monthly_rate_over_interval(self):
        build = build_opf(case9(), formulation="singlenode_dc", delta=0.5,
                          lossy_storage=[_unit(self_discharge_per_month=0.04)])
        np.testing.assert_allclose(build.data["ls_retention"],
                                   [0.96 ** (0.5 / 730.0)])

    def test_operating_set_is_ideal_limit_on_net_power(self):
        units = [_unit()]
        p_ch, p_dis, soc = cp.Variable(1), cp.Variable(1), cp.Variable(1)
        constraints = storage_lossy.operating_constraints(units, p_ch, p_dis, soc)
        soc.value = np.array([50.0])
        for charge, discharge, feasible in [(40.0, 0.0, True), (0.0, 40.0, True),
                                            (60.0, 30.0, True), (0.0, 41.0, False),
                                            (45.0, 0.0, False)]:
            p_ch.value, p_dis.value = np.array([charge]), np.array([discharge])
            assert all(bool(c.value()) for c in constraints) is feasible

    def test_exactness_perturbation_preserves_soc_and_reduces_throughput(self):
        units = [_unit()]
        eta = 0.95 * 0.9
        p_ch, p_dis, eps = np.array([10.0]), np.array([8.0]), 1.0
        before = storage_lossy._stored_energy_change(units, p_ch, p_dis, 1.0).value
        after = storage_lossy._stored_energy_change(
            units, p_ch - eps, p_dis - eta * eps, 1.0
        ).value
        np.testing.assert_allclose(after, before)
        reduction = (p_ch + p_dis) - ((p_ch - eps) + (p_dis - eta * eps))
        np.testing.assert_allclose(reduction, (1 + eta) * eps)


# ---------------------------------------------------------------------------
# Gate 2: solved behavior
# ---------------------------------------------------------------------------

SCALES = [0.6, 0.8, 1.2, 1.0]


@pytest.mark.parametrize("formulation", CONVEX_FORMULATIONS)
@pytest.mark.parametrize("delta", [1.0, 0.5])
def test_energy_accounting_closes(formulation, delta):
    build, results = _solve_multistep(formulation, SCALES, delta=delta,
                                      lossy_storage=[_unit()])
    assert build.prob.status == "optimal"
    soc = results["ls_soc"]
    previous = np.vstack([build.data["ls_initial_soc"][np.newaxis, :], soc[:-1]])
    net_energy_in = delta * (results["ls_p_ch"] - results["ls_p_dis"])
    np.testing.assert_allclose(
        soc - previous,
        net_energy_in - results["ls_conversion_loss"] - results["ls_self_discharge"],
        atol=ENERGY_ATOL,
    )
    np.testing.assert_allclose(results["ls_b"], results["ls_p_dis"] - results["ls_p_ch"])


@pytest.mark.parametrize("formulation", CONVEX_FORMULATIONS)
def test_stepwise_and_vectorized_agree(formulation):
    _, stepwise = _solve_multistep(formulation, SCALES, temporal_assembly="stepwise",
                                   lossy_storage=[_unit()])
    _, vectorized = _solve_multistep(formulation, SCALES, lossy_storage=[_unit()])
    np.testing.assert_allclose(stepwise["objective"], vectorized["objective"],
                               rtol=OBJ_RTOL)
    np.testing.assert_allclose(stepwise["ls_soc"], vectorized["ls_soc"], atol=1e-4)


@pytest.mark.parametrize("formulation,prepared", [(f, False) for f in ALL_FORMULATIONS]
                         + [("ac", "cost"), ("ac", "component_first")])
def test_lossless_parameters_reduce_to_ideal_storage(formulation, prepared):
    lossless = _unit(charge_efficiency=1.0, discharge_efficiency=1.0,
                     self_discharge_per_month=0.0)
    ideal = StorageUnitIdeal(bus=5, apparent_power_rating=40.0, capacity=100.0,
                             initial_soc=50.0, device_id="battery")
    options = OPFOptions(numerical_preparation=_base_preparation(formulation, prepared))
    _, lossy_results = _solve_multistep(formulation, SCALES, lossy_storage=[lossless], options=options)
    _, ideal_results = _solve_multistep(formulation, SCALES, storage=[ideal], options=options)
    assert lossy_results["status"] == ideal_results["status"] == "optimal"
    np.testing.assert_allclose(lossy_results["objective"], ideal_results["objective"],
                               rtol=OBJ_RTOL)
    if not prepared:
        np.testing.assert_allclose(lossy_results["ls_b"], ideal_results["b"], atol=1e-3)


@pytest.mark.parametrize("formulation", ALL_FORMULATIONS)
def test_no_split_operation_with_zero_cost_curtailment(formulation):
    renewable = NondispatchableUnit(bus=7, p_available=0.0,
                                    apparent_power_rating=300.0, device_id="wind")
    df_nd = pd.DataFrame({"wind": [280.0, 250.0, 20.0, 0.0]})
    build, results = _solve_multistep(
        formulation, SCALES, lossy_storage=[_unit()],
        nondispatchable=[renewable], df_nd=df_nd,
    )
    assert build.prob.status == "optimal"
    assert np.max(results["curtailment"]) > 1.0  # curtailment headroom is used
    assert np.max(results["ls_split"]) <= SPLIT_ATOL


def test_terminal_policies_compose():
    hard = _unit(terminal_soc=70.0, terminal_constraint="equality")
    _, results = _solve_multistep("lossy_dc", SCALES, lossy_storage=[hard])
    np.testing.assert_allclose(results["ls_soc"][-1], [70.0], atol=1e-4)
    np.testing.assert_allclose(results["ls_terminal_deviation"], [0.0], atol=1e-4)

    soft = _unit(terminal_soc=70.0, terminal_cost="shortfall_linear",
                 terminal_weight=5.0)
    build, results = _solve_multistep("lossy_dc", SCALES, lossy_storage=[soft])
    assert "ls_terminal_cost" in build.expressions
    expected = 5.0 * max(0.0, -results["ls_terminal_deviation"][0])
    np.testing.assert_allclose(results["ls_terminal_cost"], expected, atol=1e-4)


@pytest.mark.parametrize("formulation,assembly,prepared", FLEET_PREPARATIONS)
def test_heterogeneous_fleet_with_renewables(formulation, assembly, prepared):
    """Solved energy, capability, identity and terminal checks (Gates 2/5)."""
    units = [
        _unit(bus=6, capacity=65, initial_soc=20, apparent_power_rating=25,
              charge_efficiency=0.83, discharge_efficiency=0.91,
              self_discharge_per_month=0.04, aging_weight=0.03,
              terminal_soc=30, terminal_cost="shortfall_linear", terminal_weight=5,
              device_id="zeta"),
        _unit(terminal_soc=70, terminal_constraint="equality", device_id="alpha"),
    ]
    renewable = NondispatchableUnit(bus=7, p_available=0,
                                    apparent_power_rating=300, device_id="wind")
    available = np.array([280., 250., 20., 0.])
    delta = 0.5
    devices = dict(lossy_storage=units, nondispatchable=[renewable],
                   df_nd=pd.DataFrame({"wind": available}))
    build, results = _solve_multistep(
        formulation, SCALES, temporal_assembly=assembly, delta=delta,
        options=OPFOptions(numerical_preparation=_base_preparation(formulation, prepared)),
        **devices,
    )
    assert build.prob.status == "optimal"
    np.testing.assert_array_equal(results["ls_device_ids"], ["zeta", "alpha"])
    charge, discharge, soc = (results[key] for key in ("ls_p_ch", "ls_p_dis", "ls_soc"))
    assert charge.shape == discharge.shape == soc.shape == (4, 2)
    assert charge.min() >= -1e-5 and discharge.min() >= -1e-5
    previous = np.array([u.initial_soc for u in units])
    conversion, leakage = [], []
    for t in range(4):
        retention = np.array([(1-u.self_discharge_per_month)**(delta/730) for u in units])
        loss = delta * np.array([
            (1-u.charge_efficiency)*charge[t, k]
            + (1/u.discharge_efficiency-1)*discharge[t, k]
            for k, u in enumerate(units)
        ])
        self_discharge = (1-retention)*previous
        expected = previous + delta*(charge[t]-discharge[t]) - loss - self_discharge
        np.testing.assert_allclose(soc[t], expected, atol=ENERGY_ATOL)
        conversion.append(loss)
        leakage.append(self_discharge)
        previous = soc[t]
    np.testing.assert_allclose(results["ls_conversion_loss"], conversion, atol=ENERGY_ATOL)
    np.testing.assert_allclose(results["ls_self_discharge"], leakage, atol=ENERGY_ATOL)
    np.testing.assert_allclose(results["ls_b"], discharge-charge, atol=1e-7)
    assert np.all(soc >= -ENERGY_ATOL)
    assert np.all(soc <= np.array([u.capacity for u in units]) + ENERGY_ATOL)
    capability = abs(results["ls_b"])
    if formulation in {"ac", "socp"}:
        capability = np.hypot(results["ls_b"], results["ls_b_q"])
    assert np.all(capability <= np.array([u.apparent_power_rating for u in units]) + 1e-5)
    np.testing.assert_allclose(soc[-1, 1], 70, atol=ENERGY_ATOL)
    np.testing.assert_allclose(results["ls_terminal_deviation"], soc[-1]-[30, 70], atol=ENERGY_ATOL)
    np.testing.assert_allclose(results["ls_terminal_cost"], 5*max(30-soc[-1, 0], 0), atol=1e-4)
    expected_cost = delta * np.sum((charge+discharge)*[u.aging_weight for u in units])
    np.testing.assert_allclose(results["ls_cost"], expected_cost, atol=1e-5)
    # Case9 uses quadratic generation costs; reconstruct in physical MW,
    # integrating stage rates once, with the terminal cost outside the integral.
    coefficients = case9()["gencost"][:, 4:7]
    generation = np.sum(coefficients[:, 0]*results["Pg"]**2
                        + coefficients[:, 1]*results["Pg"] + coefficients[:, 2])
    network_cost = (delta*build.data["loss_weight"]*np.sum(
                        build.data["r"]*(results["p_flows"]/case9()["baseMVA"])**2
                    )
                    if formulation == "lossy_dc" else 0.)
    assert results["objective"] == pytest.approx(
        delta*generation + expected_cost + results["ls_terminal_cost"] + network_cost,
        abs=1e-5,
    )
    if prepared:
        baseline, _ = _solve_multistep(
            formulation, SCALES, temporal_assembly=assembly, delta=delta, **devices
        )
        assert results["objective"] == pytest.approx(baseline.prob.value, rel=OBJ_RTOL)
    assert np.max(results["ls_split"]) <= SPLIT_ATOL
    assert results["curtailment"].max() > 1
    np.testing.assert_allclose(results["curtailment"][:, 0], available-results["p_nd"][:, 0], atol=1e-5)
    # Both renewable-rich and renewable-poor intervals participate in storage use.
    assert charge[:2].max() > 1 and discharge[2:].max() > 1
    if formulation == "ac":
        _check_ac_balance(results, SCALES, units, renewable=[renewable])
    if formulation == "socp":
        audit = audit_socp_relaxation(build, results)
        assert audit["complete"] and audit["feasible"], audit["residuals"]


@pytest.mark.parametrize("formulation,assembly,prepared", FLEET_PREPARATIONS)
def test_solved_mixed_ideal_and_lossy_fleet(formulation, assembly, prepared):
    ideal = StorageUnitIdeal(bus=5, apparent_power_rating=40, capacity=100,
                             initial_soc=50, terminal_soc=45,
                             terminal_constraint="equality", device_id="ideal")
    lossy = _unit(bus=6, initial_soc=35, terminal_soc=40,
                  terminal_constraint="equality", device_id="lossy")
    delta = 0.5
    build, results = _solve_multistep(
        formulation, SCALES, temporal_assembly=assembly, delta=delta,
        storage=[ideal], lossy_storage=[lossy],
        options=OPFOptions(numerical_preparation=_base_preparation(formulation, prepared)),
    )
    assert build.prob.status == "optimal"
    np.testing.assert_array_equal(results["storage_device_ids"], ["ideal"])
    np.testing.assert_array_equal(results["ls_device_ids"], ["lossy"])
    for key in ("b", "soc", "ls_b", "ls_soc", "ls_p_ch", "ls_p_dis"):
        assert results[key].shape == (4, 1)
    previous_ideal = np.vstack([[50.], results["soc"][:-1]])
    previous_lossy = np.vstack([[35.], results["ls_soc"][:-1]])
    np.testing.assert_allclose(results["soc"], previous_ideal-delta*results["b"], atol=ENERGY_ATOL)
    expected = (1-lossy.self_discharge_per_month)**(delta/730)*previous_lossy + delta*(
        lossy.charge_efficiency*results["ls_p_ch"]-results["ls_p_dis"]/lossy.discharge_efficiency
    )
    np.testing.assert_allclose(results["ls_soc"], expected, atol=ENERGY_ATOL)
    np.testing.assert_allclose(results["soc"][-1], [45.], atol=ENERGY_ATOL)
    np.testing.assert_allclose(results["ls_soc"][-1], [40.], atol=ENERGY_ATOL)
    np.testing.assert_allclose(results["storage_cost"], delta*ideal.aging_weight*np.abs(results["b"]).sum(), atol=1e-5)
    np.testing.assert_allclose(results["ls_cost"], delta*lossy.aging_weight*(results["ls_p_ch"]+results["ls_p_dis"]).sum(), atol=1e-5)
    assert results["ls_split"].max() <= SPLIT_ATOL
    if formulation == "ac":
        _check_ac_balance(results, SCALES, [lossy], ideal=[ideal])
    if formulation == "socp":
        audit = audit_socp_relaxation(build, results)
        assert audit["complete"] and audit["feasible"], audit["residuals"]


# ---------------------------------------------------------------------------
# Gate 3: limitation of the relaxation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prepared", [False, True])
def test_split_operation_without_curtailment_headroom(prepared):
    """A full battery must absorb uncurtailable surplus by split operation.

    Generator minimums total 30 MW while demand is 28 MW and there is no
    curtailable resource. Pure charging is impossible at capacity, so the
    only feasible way to absorb 2 MW is simultaneous charge and discharge,
    dissipated as conversion loss. This documents the stated limitation:
    with no mode constraint, circulation is bounded only by the net rating.
    """
    full = _unit(initial_soc=100.0, self_discharge_per_month=0.0)
    build = build_opf(case9(), formulation="singlenode_dc", lossy_storage=[full],
                      options=OPFOptions(numerical_preparation=_base_preparation("singlenode_dc", prepared)),
                      loads=[Load(bus=5, p_load_mw=28.0, device_id="demand")])
    build.solve()
    with pytest.raises(SimultaneousOperationError, match="'battery'") as caught:
        extract_results(build)
    results = caught.value.results
    assert build.prob.status == "optimal"
    assert results["ls_split"][0] > 1.0
    np.testing.assert_allclose(results["ls_b"], [-2.0], atol=1e-4)


@pytest.mark.parametrize("assembly,component_first", [("single", False), ("stepwise", False),
                                                      ("vectorized", False), ("vectorized", True)])
def test_cost_coordinates_retain_split_operation_exception(assembly, component_first):
    # Unlike DC, ordinary AC can burn surplus in reactive network circulation.
    # This controlled fixture removes line charging and limits reactive support
    # so that the full battery really must dissipate the uncurtailable surplus.
    case = case9()
    case["branch"][:, 4] = 0.
    generators = [replace(g, q_min_mvar=0., q_max_mvar=1.)
                  for g in gen_from_matpower(case["gen"], case["gencost"])]
    full = _unit(initial_soc=100., self_discharge_per_month=0.)
    policy = NumericalPreparation(normalize_device_limits=True, exact_fixed_boxes=True,
        cost_coordinates=True, objective_assembly="component_first" if component_first else "hourly")
    kwargs = dict(formulation="ac", lossy_storage=[full], generators=generators,
        loads=[Load(bus=5, p_load_mw=28., device_id="demand")],
        options=OPFOptions(numerical_preparation=policy))
    build = (build_opf(case, **kwargs) if assembly == "single" else
             build_opf_multistep(case, T=1, temporal_assembly=assembly, **kwargs))
    reactive = build.variables["ls_b_q"]
    reactive = reactive if isinstance(reactive, list) else [reactive]
    build.prob = cp.Problem(build.prob.objective,
                           [*build.prob.constraints, *(q == 0. for q in reactive)])
    build.solve()
    assert build.prob.status == "optimal"
    with pytest.raises(SimultaneousOperationError) as caught:
        extract_results(build)
    result = caught.value.results
    assert build.prob.status == "optimal"
    assert result["ls_split"].max() > SPLIT_ATOL
    np.testing.assert_allclose(result["ls_b"], result["ls_p_dis"]-result["ls_p_ch"])
    expected = (result["ls_p_ch"]+result["ls_p_dis"]).sum()*full.aging_weight
    assert result["ls_cost"] == pytest.approx(expected)
    assert build.preparation_evidence.checks["cost_accounting"]["linear_throughput_physical_cost"] == pytest.approx(expected)


class TestSimultaneousOperationCheck:
    def _results(self, split):
        return {"ls_split": np.asarray(split), "ls_device_ids": np.array(["a", "b"])}

    def test_at_or_below_tolerance_passes(self):
        storage_lossy._check_simultaneous_operation(
            self._results([[0.0, SPLIT_ATOL], [1e-7, 0.0]])
        )
        storage_lossy._check_simultaneous_operation({"ls_split": None})

    def test_above_tolerance_raises_with_location_and_results(self):
        results = self._results([[0.0, 0.0], [0.5, 2e-3]])
        with pytest.raises(SimultaneousOperationError,
                           match=r"2 unit-interval.*'a' at interval 1") as caught:
            storage_lossy._check_simultaneous_operation(results)
        assert caught.value.results is results


# ---------------------------------------------------------------------------
# Result schema and detection by presence
# ---------------------------------------------------------------------------

def test_absent_when_not_configured():
    build = build_opf(case9(), formulation="lossy_dc")
    assert "n_ls" not in build.data
    build.solve()
    assert not any(key.startswith("ls_") for key in extract_results(build))


@pytest.mark.parametrize("formulation, reactive", [
    ("ac", True), ("socp", True), ("lossy_dc", False), ("singlenode_dc", False),
])
def test_unsolved_schema(formulation, reactive):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf(case9(), formulation=formulation, lossy_storage=[_unit()])
    results = extract_results(build)
    assert ("ls_b_q" in build.variables) is reactive
    assert results["ls_split"] is None and results["ls_soc"] is None
    assert np.isnan(results["ls_cost"])
    assert list(results["ls_device_ids"]) == ["battery"]


# ---------------------------------------------------------------------------
# AC (requires IPOPT)
# ---------------------------------------------------------------------------

def test_ac_single_step_solves_without_split_operation():
    build = build_opf(case9(), formulation="ac", lossy_storage=[_unit()])
    build.solve()
    results = extract_results(build)
    assert build.prob.status == "optimal"
    assert np.max(results["ls_split"]) <= SPLIT_ATOL
    apparent = results["ls_b"] ** 2 + results["ls_b_q"] ** 2
    assert np.all(apparent <= 40.0 ** 2 + 1e-4)


def test_solved_socp_lossy_injection_is_included_in_public_audit():
    """Regression for the omitted roughly 40 MW Case9 injection."""
    build = build_opf(case9(), formulation="socp", lossy_storage=[_unit()])
    build.solve()
    results = extract_results(build)
    assert build.prob.status == "optimal" and results["ls_b"][0] > 1
    report = audit_socp_relaxation(build)
    assert report["available"] and report["complete"] and report["feasible"]
    for key in ("p_balance", "q_balance", "lossy_storage_transition"):
        assert report["residuals"][key]["passed"]
