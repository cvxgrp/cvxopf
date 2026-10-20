"""Publish compact E1 evidence from fixtures and recorded tests; never solves.

Run after focused pytest with --junitxml=results/e1/focused.xml. Raw test timing
records are retained as measurements, not used as correctness thresholds.
"""

import json
import platform
from pathlib import Path
import xml.etree.ElementTree as ET

import clarabel
import cvxpy
import numpy as np

from cvxopf import OPFOptions, build_opf, extract_results, audit_socp_relaxation, recover_socp_voltage
from tests.socp_reference_cases import cases, policy


def main():
    root = Path(__file__).resolve().parent
    references = []
    for name, case in cases().items():
        record = json.loads((root.parents[1] / "tests" / "fixtures" / f"{name}_socp_powermodels_reference.json").read_text())
        build = build_opf(case, formulation="socp", options=OPFOptions(enforce_vset=policy(name)["enforce_vset"]))
        result = extract_results(build)
        result.update({key: np.asarray(record["primal"][key]) for key in ("w", "W_re", "W_im", "Pg", "Qg")})
        audit = audit_socp_relaxation(build, result)
        recovery = recover_socp_voltage(build, result)
        assert audit["feasible"] and recovery["available"]
        references.append(dict(case=name, objective_estimate=record["objective"],
            primal_objective=record["primal_objective"], **record["independent_audit"],
            timing=record["timing"], input_sha256=record["input_sha256"],
            max_normalized_edge_gap=audit["edge_gaps"]["relative_summary"]["maximum"],
            max_cycle_rad=recovery["cycle_summary"]["maximum"],
            exact_product_recovery=recovery["exact_product_recovery"],
            recovered_ac_feasible=recovery["ac_feasible"],
            recovered_max_balance_mva=max(recovery["ac_audit"]["residuals"][k]["maximum"] for k in ("p_balance", "q_balance"))))
    xml = ET.parse(root / "results" / "e1" / "focused.xml")
    comparisons = [json.loads(p.attrib["value"]) for p in xml.findall(".//property")
                   if p.attrib["name"] in ("socp_reference", "e1")]
    assert len(comparisons) == 9
    for comparison in comparisons:
        if "max_determinant_violation" not in comparison:
            # Historical summaries recorded determinant violation under the SOC
            # name and did not retain edge primals. Preserve that measurement;
            # do not infer a norm residual or rerun solves to refresh reporting.
            comparison["max_determinant_violation"] = comparison["max_cone_violation"]
            comparison["max_cone_violation"] = None
            comparison["soc_residual_unavailable_reason"] = "Historical summary did not retain edge primals"
    evidence = dict(platform=platform.platform(), machine=platform.machine(),
        python=platform.python_version(), cvxpy=cvxpy.__version__,
        clarabel=clarabel.__version__, numpy=np.__version__,
        audit_units=dict(max_cone_violation="pu^2", max_determinant_violation="pu^4"),
        references=references, python_comparisons=comparisons)
    (root / "E1_EVIDENCE.json").write_text(json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False)+"\n")
    for record in references:
        print(json.dumps(record))


if __name__ == "__main__":
    main()
