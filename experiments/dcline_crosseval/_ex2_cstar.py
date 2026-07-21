"""EX2: record cvxopf's case9_dcline optimum (C*) to /tmp/cstar.json."""
import warnings, json
import numpy as np
from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf
from cvxopf.results import extract_results

case = case9_dcline()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    links = hvdc_from_dcline(case["dcline"])
    b = build_opf(case, formulation="ac", hvdc=links)
    b.solve()
r = extract_results(b)
d = b.data
out = {
    "obj": float(r["objective"]),
    "Pg": [float(x) for x in r["Pg"]],
    "Vm": [float(x) for x in r["Vm"]],
    "Va_deg": [float(x) for x in r["Va_deg"]],
    "p_hvdc_in": [float(x) for x in r["p_hvdc_in"]],
    "p_hvdc_out": [float(x) for x in r["p_hvdc_out"]],
    "ext_to_int": {int(k): int(v) for k, v in d["ext_to_int"].items()},
}
json.dump(out, open("/tmp/cstar.json", "w"), indent=2)
print("EX2 cvxopf obj:", round(out["obj"], 4))
print("EX2 Pg:", np.round(out["Pg"], 4).tolist())
print("EX2 p_in:", np.round(out["p_hvdc_in"], 4).tolist(),
      "p_out:", np.round(out["p_hvdc_out"], 4).tolist())
print("EX2 Vm:", np.round(out["Vm"], 5).tolist())
