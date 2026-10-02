"""Reusable small matched-pair cases and numerical checks; no experiment imports.

This is test support, not a public bound-certification API. Full input equality
is deliberately conservative, and unsupported caller coupling is rejected.
"""

from copy import deepcopy
from dataclasses import asdict, is_dataclass
import hashlib
import json

import numpy as np
import pandas as pd

from cvxopf import (
    HVDCLink, Load, NondispatchableUnit, OPFOptions, StorageUnitIdeal,
    audit_socp_relaxation, build_opf, build_opf_multistep, extract_results,
    recover_socp_voltage,
)
from cvxopf.network import make_branch_admittance, make_ybus_matpower, reindex_case_to_consecutive
from tests.socp_reference_cases import two_bus
from cvxopf.testcases import case9, case14


AC_SETTINGS = dict(max_iter=500, max_cpu_time=60., tol=1e-8, acceptable_tol=1e-8)
SOCP_SETTINGS = dict(max_iter=200, time_limit=20., tol_gap_abs=1e-9,
                     tol_gap_rel=1e-9, tol_feas=1e-9)
PAIR_NAMES = ("case9", "case14", "mixed_vectorized", "mixed_stepwise")
DEVICE_KEYS = ("Pg", "Qg", "b", "b_q", "soc", "p_nd", "q_nd",
               "p_hvdc_in", "p_hvdc_out", "load_shed_fraction")


def json_value(value):
    """Stable, inspectable identity for the exact comparison inputs."""
    if isinstance(value, pd.DataFrame):
        return dict(columns=json_value(value.columns.tolist()),
                    index=json_value(value.index.tolist()), values=json_value(value.to_numpy()))
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, np.ndarray):
        return json_value(value.tolist())
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(json_value(value), sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def fixture(name):
    if name in ("case9", "case14"):
        return dict(case=(case9 if name == "case9" else case14)(),
                    kwargs=dict(options=OPFOptions(), delta=1.))
    if name not in PAIR_NAMES:
        raise ValueError(f"Unknown matched case: {name}")
    case = two_bus()
    case["gen"][0, 8] = 50.
    return dict(case=case, kwargs=dict(
        T=3, temporal_assembly=name.removeprefix("mixed_"), delta=.5,
        options=OPFOptions(enforce_vset=True),
        storage=[
            StorageUnitIdeal(bus=2, apparent_power_rating=12, capacity=12,
                initial_soc=6, terminal_soc=6, terminal_constraint="equality",
                aging_weight=.1, device_id="hard"),
            StorageUnitIdeal(bus=2, apparent_power_rating=3, capacity=4,
                initial_soc=2, terminal_soc=3, terminal_cost="quadratic",
                terminal_weight=7, aging_weight=.1, device_id="soft"),
        ],
        loads=[Load(bus=2, p_load_mw=20, q_load_mvar=2,
                    device_id="demand", shedding_cost_per_mwh=1000)],
        df_load_p=pd.DataFrame({"demand": [20., 90., 30.]}),
        df_load_q=pd.DataFrame({"demand": [2., 9., 3.]}),
        nondispatchable=[NondispatchableUnit(bus=2, p_available=8,
            apparent_power_rating=10, device_id="renewable")],
        df_nd=pd.DataFrame({"renewable": [8., 0., 4.]}),
        hvdc=[HVDCLink(from_bus=1, to_bus=2, p_min_mw=-2, p_max_mw=-1,
            loss_percent=3, cost_coeffs=(.2, .1, .01), device_id="link")],
    ))


def validate_matching(ac, socp):
    """Reject mismatches before an objective comparison or solver invocation.

    Only the declared small fixture input types are supported. Equality includes
    every supplied field, not a hand-picked subset of physical settings.
    """
    for spec in (ac, socp):
        kwargs = spec["kwargs"]
        if kwargs.get("coupling_constraints") is not None:
            raise ValueError("Caller coupling is unsupported for matched evidence")
        options = kwargs.get("options", OPFOptions())
        if options.sparsity_tol != 0:
            raise ValueError("Matched AC/SOCP evidence requires sparsity_tol == 0")
        if options.loss_weight != 1:
            raise ValueError("DC-only loss_weight is not a matched SOCP objective")
        if "formulation" in kwargs:
            raise ValueError("Formulation is owned by the matched-pair builder")
    if digest(ac) != digest(socp):
        raise ValueError("AC and SOCP mathematical inputs/policies do not match")
    return digest(ac)


def build_pair(ac, socp=None):
    socp = deepcopy(ac) if socp is None else socp
    identity = validate_matching(ac, socp)
    builds = []
    for formulation, spec in (("ac", ac), ("socp", socp)):
        builder = build_opf_multistep if "T" in spec["kwargs"] else build_opf
        builds.append(builder(deepcopy(spec["case"]), formulation=formulation,
                              **deepcopy(spec["kwargs"])))
    return (*builds, identity)


def lift_ac(build, result):
    """Lift the actual AC voltage; preserve every public device coordinate."""
    lifted = extract_results(build)
    voltage = np.asarray(result["Vm"]) * np.exp(1j*np.deg2rad(result["Va_deg"]))
    if not np.all(np.isfinite(voltage)):
        raise ValueError("Nonfinite AC voltage")
    pairs = build.data["voltage_product_pairs"]
    z = voltage[..., pairs[:, 0]] * voltage[..., pairs[:, 1]].conj()
    lifted.update(status=result["status"], objective=result["objective"],
                  w=abs(voltage)**2, W_re=z.real, W_im=z.imag)
    for key in DEVICE_KEYS:
        if key in result:
            lifted[key] = None if result[key] is None else np.array(result[key], copy=True)
    return lifted


def objective_components(spec, result):
    """Independent arithmetic in engineering units, never CVXPY expressions.

    Scope is the declared polynomial-cost fixtures. Reject unsupported cost
    models rather than pretending this is a general objective evaluator.
    """
    kwargs, case = spec["kwargs"], spec["case"]
    T, delta = kwargs.get("T", 1), kwargs.get("delta", 1.)
    pg = np.asarray(result["Pg"]).reshape(T, -1)
    costs = case["gencost"]
    if not np.all(costs[:, 0] == 2) or not np.all(costs[:, 3] == 3):
        raise ValueError("E2 numeric cost audit supports quadratic MATPOWER costs only")
    on = case["gen"][:, 7] != 0
    out = dict(generator_cost=float(delta*np.sum(costs[on, 4]*pg[:, on]**2
                        + costs[on, 5]*pg[:, on] + costs[on, 6])))
    storage = kwargs.get("storage", [])
    if storage:
        b = np.asarray(result["b"]).reshape(T, -1)
        soc = np.asarray(result["soc"]).reshape(T, -1)
        out["storage_cost"] = float(delta*np.sum(abs(b)*[u.aging_weight for u in storage]))
        terminal = 0.
        for k, unit in enumerate(storage):
            if unit.terminal_cost is None:
                continue
            deviation = soc[-1, k]-unit.terminal_soc
            penalty = {"linear": abs(deviation), "quadratic": deviation**2,
                "shortfall_linear": max(-deviation, 0.),
                "shortfall_quadratic": max(-deviation, 0.)**2}[unit.terminal_cost]
            terminal += unit.terminal_weight*penalty
        if any(u.terminal_cost is not None for u in storage):
            out["storage_terminal_cost"] = float(terminal)
    if kwargs.get("hvdc"):
        pin = np.asarray(result["p_hvdc_in"]).reshape(T, -1)
        c = np.asarray([u.cost_coeffs for u in kwargs["hvdc"]])
        out["hvdc_cost"] = float(delta*np.sum(c[:, 0]+c[:, 1]*abs(pin)+c[:, 2]*pin**2))
    loads = kwargs.get("loads", [])
    sheddable = [k for k, u in enumerate(loads) if u.shedding_cost_per_mwh is not None]
    if sheddable:
        p = kwargs["df_load_p"][[u.device_id for u in loads]].to_numpy()
        shed = np.maximum(p[:, sheddable], 0)*np.asarray(result["load_shed_fraction"]).reshape(T, -1)
        out["load_shedding_cost"] = float(delta*np.sum(shed*
            [loads[k].shedding_cost_per_mwh for k in sheddable]))
    if not all(np.isfinite(v) for v in out.values()):
        raise ValueError("Nonfinite objective component")
    return out


def check_accounting(spec, build, result):
    components = objective_components(spec, result)
    for key, value in components.items():
        np.testing.assert_allclose(build.expressions[key].value, value, rtol=1e-8, atol=1e-6,
                                   err_msg=key)
    np.testing.assert_allclose(result["objective"], sum(components.values()), rtol=1e-8, atol=1e-6)
    return components


def check_containment(spec, ac, socp, result):
    """Direct AC physics and full-device lifted feasibility, without solving."""
    assert result["status"] in ("optimal", "optimal_inaccurate")
    lifted = lift_ac(socp, result)
    relaxed = audit_socp_relaxation(socp, lifted)
    assert relaxed["complete"] and relaxed["feasible"], relaxed
    recovered = recover_socp_voltage(socp, lifted)
    assert recovered["exact_product_recovery"] and recovered["ac_feasible"], recovered
    voltage = (np.asarray(result["Vm"])*np.exp(1j*np.deg2rad(result["Va_deg"]))).reshape(
        spec["kwargs"].get("T", 1), -1)
    case, _ = reindex_case_to_consecutive(spec["case"])
    adm = make_branch_admittance(case)
    nodal = voltage*np.conj(voltage @ make_ybus_matpower(case).T)*case["baseMVA"]
    vf, vt = voltage[:, adm.from_bus], voltage[:, adm.to_bus]
    sf = vf*np.conj(adm.yff*vf+adm.yft*vt)*case["baseMVA"]
    st = vt*np.conj(adm.ytf*vf+adm.ytt*vt)*case["baseMVA"]
    for actual, power in ((relaxed["nodal_power_mva"], nodal),
                          (relaxed["branch_from_mva"], sf), (relaxed["branch_to_mva"], st)):
        np.testing.assert_allclose(actual, power, rtol=0, atol=1e-4)
    for key, power in (("p_net", nodal.real), ("q_net", nodal.imag),
                       ("branch_p_from", sf.real), ("branch_q_from", sf.imag),
                       ("branch_p_to", st.real), ("branch_q_to", st.imag)):
        np.testing.assert_allclose(np.asarray(result[key]).reshape(power.shape), power, rtol=0, atol=1e-4)
    # Explicitly verify actual AC voltages were recovered up to the one-island
    # gauge; all E2 fixtures are connected. Device fields are copied, not repaired.
    candidate = recovered["candidate"]
    vr = (np.asarray(candidate["Vm"])*np.exp(1j*np.deg2rad(candidate["Va_deg"]))).reshape(voltage.shape)
    np.testing.assert_allclose(vr/vr[:, :1], voltage/voltage[:, :1], rtol=0, atol=1e-8)
    for key in DEVICE_KEYS:
        if result.get(key) is not None:
            np.testing.assert_array_equal(lifted[key], result[key])
    costs = check_accounting(spec, ac, result)
    assert costs == objective_components(spec, lifted)
    return dict(lifted=lifted, relaxation_audit=relaxed, recovery=recovered,
                objective_components=costs)


def check_pair(spec, ac, socp):
    ar, sr = extract_results(ac), extract_results(socp)
    containment = check_containment(spec, ac, socp, ar)
    assert sr["status"] in ("optimal", "optimal_inaccurate")
    audit = audit_socp_relaxation(socp, sr)
    assert audit["complete"] and audit["feasible"], audit
    components = check_accounting(spec, socp, sr)
    recovery = recover_socp_voltage(socp, sr)
    allowance = 2e-5+2e-6*abs(ar["objective"])
    gap = ar["objective"]-sr["objective"]
    assert gap >= -allowance, ("Unexplained reversed objective gap", gap, allowance)
    if "storage" in spec["kwargs"]:
        for result in (ar, sr):
            assert np.max(abs(result["b"][:, 0])) > 1e-3
            assert result["energy_not_served"] > 1e-3
            np.testing.assert_allclose(result["soc"][-1, 0], 6., atol=1e-5, rtol=0)
            p = spec["kwargs"]["df_load_p"].to_numpy()
            ens = spec["kwargs"]["delta"]*np.sum(p*result["load_shed_fraction"])
            np.testing.assert_allclose(result["energy_not_served"], ens, rtol=1e-8, atol=1e-6)
    return dict(ac_result=ar, socp_result=sr, containment=containment,
                socp_audit=audit, socp_recovery=recovery, socp_components=components,
                objective_gap=gap, gap_allowance=allowance,
                dual_certificate=None, objective_label="numerical relaxation optimum estimate")
