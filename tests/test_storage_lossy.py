"""Tests for lossy storage (plans/milestone-18-lossy-storage.md, Section 11)."""

import warnings

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
)
from cvxopf import storage_lossy
from cvxopf.testcases import case9

SPLIT_ATOL = storage_lossy.SPLIT_TOLERANCE_MW
ENERGY_ATOL = 1e-5  # MWh
OBJ_RTOL = 1e-6

CONVEX_FORMULATIONS = ["lossy_dc", "singlenode_dc", "socp"]
ALL_FORMULATIONS = [*CONVEX_FORMULATIONS, "ac"]


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

    def test_nondefault_numerical_preparation_rejected(self):
        options = OPFOptions(
            numerical_preparation=NumericalPreparation(normalize_device_limits=True)
        )
        with pytest.raises(ValueError, match="numerical-preparation"):
            build_opf(case9(), formulation="socp", lossy_storage=[_unit()],
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


@pytest.mark.parametrize("formulation", ALL_FORMULATIONS)
def test_lossless_parameters_reduce_to_ideal_storage(formulation):
    lossless = _unit(charge_efficiency=1.0, discharge_efficiency=1.0,
                     self_discharge_per_month=0.0)
    ideal = StorageUnitIdeal(bus=5, apparent_power_rating=40.0, capacity=100.0,
                             initial_soc=50.0, device_id="battery")
    _, lossy_results = _solve_multistep(formulation, SCALES, lossy_storage=[lossless])
    _, ideal_results = _solve_multistep(formulation, SCALES, storage=[ideal])
    np.testing.assert_allclose(lossy_results["objective"], ideal_results["objective"],
                               rtol=OBJ_RTOL)
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


@pytest.mark.parametrize("formulation", ALL_FORMULATIONS)
@pytest.mark.parametrize("assembly", ["stepwise", "vectorized"])
def test_heterogeneous_fleet_with_renewables(formulation, assembly):
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
    build, results = _solve_multistep(
        formulation, SCALES, temporal_assembly=assembly, delta=delta,
        lossy_storage=units, nondispatchable=[renewable],
        df_nd=pd.DataFrame({"wind": available}),
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
    assert np.max(results["ls_split"]) <= SPLIT_ATOL
    assert results["curtailment"].max() > 1
    np.testing.assert_allclose(results["curtailment"][:, 0], available-results["p_nd"][:, 0], atol=1e-5)
    # Both renewable-rich and renewable-poor intervals participate in storage use.
    assert charge[:2].max() > 1 and discharge[2:].max() > 1
    if formulation == "socp":
        audit = audit_socp_relaxation(build, results)
        assert audit["complete"] and audit["feasible"], audit["residuals"]


@pytest.mark.parametrize("formulation", ALL_FORMULATIONS)
@pytest.mark.parametrize("assembly", ["stepwise", "vectorized"])
def test_solved_mixed_ideal_and_lossy_fleet(formulation, assembly):
    ideal = StorageUnitIdeal(bus=5, apparent_power_rating=40, capacity=100,
                             initial_soc=50, terminal_soc=45,
                             terminal_constraint="equality", device_id="ideal")
    lossy = _unit(bus=6, initial_soc=35, terminal_soc=40,
                  terminal_constraint="equality", device_id="lossy")
    delta = 0.5
    build, results = _solve_multistep(
        formulation, SCALES, temporal_assembly=assembly, delta=delta,
        storage=[ideal], lossy_storage=[lossy],
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
    if formulation == "socp":
        audit = audit_socp_relaxation(build, results)
        assert audit["complete"] and audit["feasible"], audit["residuals"]


# ---------------------------------------------------------------------------
# Gate 3: limitation of the relaxation
# ---------------------------------------------------------------------------

def test_split_operation_without_curtailment_headroom():
    """A full battery must absorb uncurtailable surplus by split operation.

    Generator minimums total 30 MW while demand is 28 MW and there is no
    curtailable resource. Pure charging is impossible at capacity, so the
    only feasible way to absorb 2 MW is simultaneous charge and discharge,
    dissipated as conversion loss. This documents the stated limitation:
    with no mode constraint, circulation is bounded only by the net rating.
    """
    full = _unit(initial_soc=100.0, self_discharge_per_month=0.0)
    build = build_opf(case9(), formulation="singlenode_dc", lossy_storage=[full],
                      loads=[Load(bus=5, p_load_mw=28.0, device_id="demand")])
    build.solve()
    with pytest.raises(SimultaneousOperationError, match="'battery'") as caught:
        extract_results(build)
    results = caught.value.results
    assert build.prob.status == "optimal"
    assert results["ls_split"][0] > 1.0
    np.testing.assert_allclose(results["ls_b"], [-2.0], atol=1e-4)


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
