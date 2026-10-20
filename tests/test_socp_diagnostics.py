"""Independent algebraic positive/negative controls; no solver required."""

from copy import deepcopy

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import (
    build_opf, build_opf_multistep, extract_results, OPFOptions, SOCPAuditTolerances,
    audit_socp_relaxation, recover_socp_voltage, StorageUnitIdeal,
    NondispatchableUnit, HVDCLink, Load,
)
from cvxopf.network import make_ybus_matpower, reindex_case_to_consecutive
from cvxopf.testcases import case9


def triangle():
    case = case9()
    case["bus"] = case["bus"][:3].copy()
    case["bus"][:, 2:6] = 0
    case["gen"][:, 9] = 0
    case["branch"] = np.array([
        [1, 2, 0.01, 0.1, 0, 200, 0, 0, 0, 0, 1, -360, 360],
        [2, 3, 0.01, 0.1, 0, 200, 0, 0, 0, 0, 1, -360, 360],
        [3, 1, 0.01, 0.1, 0, 200, 0, 0, 0, 0, 1, -360, 360],
    ], dtype=float)
    return case


def lift(build, voltage):
    r = extract_results(build)
    pairs = build.data["voltage_product_pairs"]
    z = voltage[pairs[:, 0]] * voltage[pairs[:, 1]].conj()
    r.update(w=abs(voltage)**2, W_re=z.real, W_im=z.imag,
             Pg=np.zeros(build.data["ng"]), Qg=np.zeros(build.data["ng"]))
    return r


def test_rank_one_ac_positive_control_and_independent_physics(monkeypatch):
    case = triangle()
    # Nontrivial tap, phase, shunt and charging; loads exactly counteract the
    # direct voltage-derived injections. Signed loads are part of the public API.
    case["branch"][0, 8:10] = [1.03, 4]
    case["branch"][:, 4] = 0.02
    case["bus"][1, 4:6] = [0.1, 0.2]
    v = np.array([1.01, 1.02, 0.98]) * np.exp(1j * np.array([0.1, 0.06, 0.03]))
    indexed, _ = reindex_case_to_consecutive(case)
    s = v * np.conj(make_ybus_matpower(indexed) @ v) * case["baseMVA"]
    loads = [Load(bus=k + 1, p_load_mw=-p.real, q_load_mvar=-p.imag, device_id=str(k)) for k, p in enumerate(s)]
    build = build_opf(case, formulation="socp", loads=loads)
    result = lift(build, v)
    # Deliberately corrupt reported powers and every modeled residual reader.
    # The independent audit must use neither.
    result["p_net"] = np.full(3, 999)
    def forbidden(*args, **kwargs):
        pytest.fail("diagnostics must not read CVXPY constraint residuals or solve")
    for constraint in build.prob.constraints:
        monkeypatch.setattr(constraint, "violation", forbidden)
    monkeypatch.setattr(cp.Problem, "solve", forbidden)
    audit = audit_socp_relaxation(build, result)
    assert audit["feasible"]
    np.testing.assert_allclose(audit["nodal_power_mva"][0], s, atol=1e-12)
    recovery = recover_socp_voltage(build, result)
    assert recovery["exact_product_recovery"] and recovery["ac_feasible"]
    np.testing.assert_allclose(recovery["candidate"]["Va_deg"], np.rad2deg(np.angle(v) - np.angle(v[0])), atol=1e-12)


def test_edge_tight_but_cycle_inconsistent_and_slack_but_cycle_consistent():
    build = build_opf(triangle(), formulation="socp")
    result = lift(build, np.ones(3, dtype=complex))
    z = np.exp(1j * np.array([0.1, 0, 0]))
    result.update(W_re=z.real, W_im=z.imag)
    recovery = recover_socp_voltage(build, result)
    assert recovery["edge_tight"]
    assert not recovery["cycle_summary"]["passed"]
    assert not recovery["exact_product_recovery"]
    assert not recovery["ac_feasible"]
    result.update(W_re=np.full(3, 0.9), W_im=np.zeros(3))
    recovery = recover_socp_voltage(build, result)
    assert recovery["cycle_summary"]["passed"]
    assert not recovery["edge_tight"]
    assert not recovery["exact_product_recovery"]
    # Candidate AC feasibility is independent: flat voltage/zero dispatch is
    # AC feasible even though the supplied slack relaxed point is not balanced.
    assert recovery["ac_feasible"]


@pytest.mark.parametrize("key,value", [("w", None), ("w", np.full(3, np.nan)), ("W_im", [0]), ("Pg", None)])
def test_missing_nonfinite_partial_primal_has_no_feasibility_or_recovery(key, value):
    build = build_opf(triangle(), formulation="socp")
    result = lift(build, np.ones(3))
    result[key] = value
    assert not audit_socp_relaxation(build, result)["available"]
    assert recover_socp_voltage(build, result)["candidate"] is None


def test_undefined_phase_and_negative_voltage_are_not_rounded_into_recovery():
    build = build_opf(triangle(), formulation="socp")
    result = lift(build, np.ones(3))
    result["W_re"][0] = 0
    assert not recover_socp_voltage(build, result)["available"]
    result = lift(build, np.ones(3))
    result["w"][0] = -1e-10
    assert not audit_socp_relaxation(build, result)["feasible"]
    assert not recover_socp_voltage(build, result)["available"]


def test_cone_feasibility_is_not_positive_slack_and_status_is_separate():
    build = build_opf(triangle(), formulation="socp")
    result = lift(build, np.ones(3))
    result["status"] = "optimal_inaccurate"
    assert audit_socp_relaxation(build, result)["feasible"]
    result["W_re"] *= 1.01
    audit = audit_socp_relaxation(build, result)
    assert not audit["feasible"]
    assert not audit["residuals"]["edge_cones"]["passed"]
    assert np.all(audit["edge_gaps"]["signed"] < 0)
    result["W_re"][:] = 0.9
    audit = audit_socp_relaxation(build, result)
    assert audit["residuals"]["edge_cones"]["passed"]
    assert np.all(audit["edge_gaps"]["signed"] > 0)
    for status in ("infeasible", "unbounded", "infeasible_inaccurate", "unbounded_inaccurate"):
        result["status"] = status
        assert not audit_socp_relaxation(build, result)["available"]


def test_all_device_residuals_are_independent_numeric_checks():
    build = build_opf(triangle(), formulation="socp", delta=0.5,
        storage=[StorageUnitIdeal(bus=1, apparent_power_rating=10, capacity=20, initial_soc=12,
                                 terminal_soc=12, terminal_constraint="equality")],
        nondispatchable=[NondispatchableUnit(bus=2, p_available=4, apparent_power_rating=5)],
        hvdc=[HVDCLink(from_bus=1, to_bus=3, p_min_mw=-5, p_max_mw=0, loss_percent=3)],
        loads=[Load(bus=1, p_load_mw=0, q_load_mvar=0, device_id="d", shedding_cost_per_mwh=1000)])
    result = lift(build, np.ones(3))
    result.update(b=np.array([0.]), b_q=np.array([0.]), soc=np.array([12.]),
                  p_nd=np.array([0.]), q_nd=np.array([0.]),
                  p_hvdc_in=np.array([0.]), p_hvdc_out=np.array([0.]), load_shed_fraction=np.array([0.]))
    assert audit_socp_relaxation(build, result)["feasible"]
    for key, value, residual in [
        ("Pg", [1000, 0, 0], "generator_p"), ("Qg", [1000, 0, 0], "generator_q"),
        ("b_q", [11], "storage_capability"), ("soc", [21], "storage_energy"),
        ("soc", [11], "storage_transition"), ("soc", [11], "storage_terminal"),
        ("p_nd", [4.5], "nondispatchable_p"), ("q_nd", [6], "nondispatchable_capability"),
        ("p_hvdc_in", [-6], "hvdc_box"), ("p_hvdc_out", [1], "hvdc_coupling"),
        ("load_shed_fraction", [0.1], "shedding_fraction"),
    ]:
        bad = deepcopy(result)
        bad[key] = np.array(value)
        audit = audit_socp_relaxation(build, bad)
        assert not audit["residuals"][residual]["passed"], (residual, audit)


def test_setpoint_rating_voltage_and_balance_residuals():
    case = triangle()
    case["branch"][:, 5] = 0.1
    build = build_opf(case, formulation="socp", options=OPFOptions(enforce_vset=True))
    result = lift(build, np.array([1, 1.02, 1.01]) * np.exp(1j * np.array([0, .01, .02])))
    audit = audit_socp_relaxation(build, result)
    for key in ("voltage_setpoints", "branch_from", "branch_to", "p_balance", "q_balance"):
        assert not audit["residuals"][key]["passed"]
    result["w"][:] = 4
    assert not audit_socp_relaxation(build, result)["residuals"]["voltage_bounds"]["passed"]


@pytest.mark.parametrize("assembly", ["single", "vectorized", "stepwise"])
def test_empty_pairs_branchless_and_isolated_forest(assembly):
    case = triangle()
    case["branch"] = np.empty((0, 13))
    build = (build_opf(case, formulation="socp", loads=[]) if assembly == "single" else
             build_opf_multistep(case, T=3, formulation="socp", loads=[], temporal_assembly=assembly))
    result = lift(build, np.ones(3))
    if assembly != "single":
        for key in ("w", "W_re", "W_im", "Pg", "Qg"):
            result[key] = np.tile(result[key], (3, 1))
    audit = audit_socp_relaxation(build, result)
    assert audit["feasible"]
    assert result["branch_p_from"].size == 0
    recovery = recover_socp_voltage(build, result)
    assert recovery["roots"] == [0, 1, 2]
    assert recovery["exact_product_recovery"] and recovery["ac_feasible"]


def test_external_coupling_is_not_silently_declared_audited():
    build = build_opf_multistep(triangle(), loads=[], T=1, formulation="socp",
                              coupling_constraints=[cp.Constant(0) <= 1])
    result = lift(build, np.ones(3))
    for key in ("w", "W_re", "W_im", "Pg", "Qg"):
        result[key] = result[key][None, :]
    audit = audit_socp_relaxation(build, result)
    assert audit["available"] and audit["checked_constraints_feasible"]
    assert not audit["complete"] and audit["feasible"] is None


@pytest.mark.parametrize("value", [-1, 0, float("nan"), float("inf"), True])
def test_tolerances_validated(value):
    with pytest.raises(ValueError, match="finite and strictly positive"):
        SOCPAuditTolerances(power=value)


def test_wrong_formulation_rejected():
    with pytest.raises(ValueError, match="formulation='socp'"):
        audit_socp_relaxation(build_opf(case9(), formulation="lossy_dc"))
