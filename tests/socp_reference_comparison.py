"""Shared E1 construction and independent checks; no optimization on import."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from cvxopf import (NumericalPreparation, OPFOptions, audit_socp_relaxation,
                   build_opf, build_opf_multistep, recover_socp_voltage)
from tests.socp_reference_audit import audit
from tests.socp_reference_cases import (
    analytic_two_bus, cases, input_hash, payload, policy, SETTINGS, verify_reference_sources,
)

FIXTURES = Path(__file__).parent / "fixtures"
OBJ_RTOL, OBJ_ATOL = 2e-6, 2e-5
ASSEMBLIES = ("single", "vectorized", "stepwise")
POLICIES = {
    "baseline": NumericalPreparation(),
    "prepared": NumericalPreparation(normalize_device_limits=True,
                                     exact_fixed_boxes=True, canonical_scaling="joint5"),
}


def reference(name):
    """Require the unchanged, provenance-checked external oracle."""
    record = json.loads((FIXTURES / f"{name}_socp_powermodels_reference.json").read_text())
    case = cases()[name]
    assert record["schema_version"] == 1 and record["case_name"] == name
    assert record["input_sha256"] == input_hash(case)
    assert record["case"] == payload(case) and record["policy"] == policy(name)
    ref = record["reference"]
    assert ref["implementation"] == "PowerModels.SOCWRConicPowerModel"
    assert ref["settings"] == SETTINGS and ref["parser_verified"] is True
    assert ref["packages"]["PowerModels"]["version"] == "0.21.5"
    assert ref["packages"]["PowerModels"]["git_tree_sha1"] == "b8e410e1d827b621e82e7e670967f0efc5845c30"
    verify_reference_sources(ref)
    assert record["termination"] == "OPTIMAL" and record["primal_status"] == "FEASIBLE_POINT"
    return case, record


def construct(name, assembly, treatment="baseline"):
    if assembly not in ASSEMBLIES:
        raise ValueError("unknown E1 assembly")
    case, record = reference(name)
    options = OPFOptions(enforce_vset=policy(name)["enforce_vset"],
                         numerical_preparation=POLICIES[treatment])
    if assembly == "single":
        build = build_opf(case, formulation="socp", options=options)
    else:
        build = build_opf_multistep(case, pd.DataFrame([case["bus"][:, 2]]),
            pd.DataFrame([case["bus"][:, 3]]), T=1, formulation="socp",
            temporal_assembly=assembly, options=options)
    return case, record, build


def compare(name, assembly, build, result):
    """Original E1 gates, using physical primals rather than solver labels alone."""
    case, record = reference(name)
    assert result["status"] == "optimal"
    assert abs(result["objective"]-record["objective"]) <= max(OBJ_ATOL, OBJ_RTOL*abs(record["objective"]))
    primal = {key: np.asarray(result[key] if assembly == "single" else result[key][0])
              for key in ("w", "W_re", "W_im", "Pg", "Qg", "branch_p_from",
                          "branch_q_from", "branch_p_to", "branch_q_to")}
    primal.update(bus_ids=case["bus"][:, 0].astype(int).tolist(),
                  voltage_product_pairs=record["primal"]["voltage_product_pairs"])
    actual_pairs = case["bus"][build.data["voltage_product_pairs"], 0].astype(int).tolist()
    assert actual_pairs == primal["voltage_product_pairs"]
    checked = audit(case, primal, enforce_vset=policy(name)["enforce_vset"])
    assert abs(result["objective"]-checked["objective"]) <= max(1e-7, 1e-10*abs(checked["objective"]))
    assert audit_socp_relaxation(build, result)["feasible"]
    recovery = recover_socp_voltage(build, result)
    assert recovery["available"]
    if name == "two_bus":
        analytic = analytic_two_bus()
        for key in ("w", "W_re", "W_im", "Pg", "Qg"):
            np.testing.assert_allclose(primal[key], analytic[key], rtol=0,
                                       atol=1e-5 if key in ("Pg", "Qg") else 1e-6)
        assert recovery["exact_product_recovery"] and recovery["ac_feasible"]
    return dict(**checked, case=name, assembly=assembly,
        objective_estimate=result["objective"], reference_objective=record["objective"],
        max_cycle_rad=recovery["cycle_summary"]["maximum"],
        exact_product_recovery=recovery["exact_product_recovery"],
        recovered_ac_feasible=recovery["ac_feasible"])
