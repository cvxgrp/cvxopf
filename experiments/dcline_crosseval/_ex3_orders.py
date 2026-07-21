"""EX3 pre-check: dump gen ordering + bus mapping from BOTH sides so the
round-trip map is built on verified facts, not assumptions.
cvxopf side only (pure project env). Writes /tmp/ex3_cvx.json.
"""
import warnings, json
import numpy as np
from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf

case = case9_dcline()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    links = hvdc_from_dcline(case["dcline"])
    b = build_opf(case, formulation="ac", hvdc=links)

d = b.data
# cvxopf gen -> bus (external id). gen table col 0 is bus id.
gen_bus_ext = case["gen"][:, 0].astype(int).tolist()
# link endpoints (external ids) in cvxopf link order
link_ends = [(int(l.from_bus), int(l.to_bus)) for l in links]
out = {
    "gen_bus_ext": gen_bus_ext,
    "ext_to_int": {int(k): int(v) for k, v in d["ext_to_int"].items()},
    "link_ends": link_ends,
    "ng": int(d["ng"]),
    "nb": int(d["nb"]),
}
json.dump(out, open("/tmp/ex3_cvx.json", "w"), indent=2)
print("EX3 cvxopf gen_bus_ext:", gen_bus_ext)
print("EX3 cvxopf link_ends:", link_ends)
print("EX3 ext_to_int:", out["ext_to_int"])
