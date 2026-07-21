"""EX6-c step 1: re-solve cvxopf case9_dcline, store FULL dispatch incl Qg."""
import warnings, json
from pathlib import Path
import numpy as np
from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf
from cvxopf.results import extract_results
RES = Path(__file__).resolve().parent / "results"
case = case9_dcline()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    links = hvdc_from_dcline(case["dcline"])
    b = build_opf(case, formulation="ac", hvdc=links)
    b.solve()
r = extract_results(b)
d = b.data
obj = float(r["objective"])
assert abs(obj - 5490.10378400849) < 1e-3, f"resolved {obj} != C* 5490.10"
keys = ["Pg","Qg","Vm","Va_deg","p_net","q_net","p_hvdc_in","p_hvdc_out"]
out = {"obj": obj}
for k in keys:
    out[k] = [float(x) for x in r[k]]
out["ext_to_int"] = {int(k): int(v) for k, v in d["ext_to_int"].items()}
(RES / "cstar_full.json").write_text(json.dumps(out, indent=2))
print("EX6c obj:", round(obj, 4))
print("EX6c Qg:", np.round(out["Qg"], 4).tolist())
print("EX6c p_net:", np.round(out["p_net"], 4).tolist())
print("EX6c q_net:", np.round(out["q_net"], 4).tolist())
print("wrote", RES / "cstar_full.json")
