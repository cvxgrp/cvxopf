"""EX13b (Direction B, cvxopf side): is P*_smooth's DC routing achievable in
cvxopf, and at what cost?

EX13 (smooth costs) converged on generator dispatch (||dPg||=1.86 MW) but the
two paths route DC power to bus 9 differently:
  C*_smooth (cvxopf):   link1 (7->9, lossless) p_in=2,  link2 (5->9, 5%) p_in=10
  P*_smooth (Pypower):  link1 p_in=10,                  link2 p_in~0
cvxopf's free obj (5262) < Pypower's (5314). This asks: if we PIN cvxopf to
P*_smooth's routing, what objective does it reach?
  - If cvxopf-at-P*-routing obj ~= 5314 (Pypower's value) and cvxopf's FREE obj
    is 5262 < 5314, then P*_smooth's routing is a genuine, feasible, but
    SLIGHTLY MORE EXPENSIVE operating point -- cvxopf's 5262 is really better,
    so Pypower's free P*_smooth is mildly suboptimal ON THE ROUTING AXIS (the
    EX12 pattern repeating on a different nonconvexity).
  - If cvxopf-at-P*-routing obj is ALSO ~5262 (matches its free optimum), the
    two routings are COST-DEGENERATE (a flat ridge) and both solvers are
    optimal, just at different points on the valley floor.

This is Direction B of the mutual-feasibility test (mirror of EX6/EX7b). Same
setup as _ex13_smoothcost_cvxopf.py (smooth gencost + link0 loss0 graft) but
with link1/link2 p_in additionally PINNED to P*_smooth's routing via extra
equalities. Pure main-env re-solve; no sandbox needed for this direction.

Run (main env): uv run --active python _ex13b_routing_cvxopf.py
Writes results/ex13b_routing_cvxopf.txt.
"""
import json
import warnings
from pathlib import Path

import cvxpy as cp
import numpy as np

from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf
from cvxopf.results import extract_results
from cvxopf.testcases.case9_dcline import case9_dcline

_here = Path(__file__).resolve().parent
RES = _here / "results"

CASE9_GENCOST = np.array(
    [
        [2.0, 1500.0, 0.0, 3.0, 0.11, 5.0, 150.0],
        [2.0, 2000.0, 0.0, 3.0, 0.085, 1.2, 600.0],
        [2.0, 3000.0, 0.0, 3.0, 0.1225, 1.0, 335.0],
    ]
)

lines = []


def emit(s=""):
    lines.append(s)


# --- read P*_smooth's routing from the sandbox JSON ---
ps_path = RES / "ex13_pstar_smooth.json"
if not ps_path.exists():
    raise SystemExit("run _ex13_smoothcost_pypower.py first (need ex13_pstar_smooth.json)")
ps = json.loads(ps_path.read_text())
pin_pstar = -np.asarray(ps["from_dummy_Pg"])  # p_in = -(from-gen raw Pg)
P_STAR_OBJ = float(ps["obj"])
C_STAR_FREE_OBJ = 5262.0439  # cvxopf free smooth-cost optimum (EX13)

# --- build cvxopf smooth-cost model + link0 loss0 graft (EX12/EX13 mechanism) ---
case = case9_dcline()
case["gencost"] = CASE9_GENCOST.copy()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    links = hvdc_from_dcline(case["dcline"])
    build = build_opf(case, formulation="ac", hvdc=links)

p_in = build.variables["p_hvdc_in"]
p_out = build.variables["p_hvdc_out"]
n_hvdc = build.data["n_hvdc"]
DC = case["dcline"]
dc_on = DC[DC[:, 2] > 0, :]
L0 = dc_on[:, 15].astype(float)
L1 = dc_on[:, 16].astype(float)
coeff = -(1.0 - L1)

# graft loss0 (locator: equality whose ONLY vars are p_in/p_out)
coupling_idx = next(
    i
    for i, con in enumerate(build.prob.constraints)
    if isinstance(con, cp.constraints.Equality)
    and {v.id for v in con.variables()} == {p_in.id, p_out.id}
)
new_constraints = list(build.prob.constraints)
del new_constraints[coupling_idx]
for k in range(n_hvdc):
    off = L0[k] if k == 0 else 0.0
    new_constraints.append(p_out[k] == coeff[k] * p_in[k] + off)

# --- PIN link1 and link2 p_in to P*_smooth's routing (Direction B) ---
# link0 left free (both paths agree there); pin the two bus-9 links.
pin_con1 = p_in[1] == float(pin_pstar[1])
pin_con2 = p_in[2] == float(pin_pstar[2])
assert pin_con1.is_dcp() and pin_con2.is_dcp(), "pin equalities must be affine/DCP"
new_constraints.extend([pin_con1, pin_con2])

build.prob = cp.Problem(build.prob.objective, new_constraints)
build.solve()

emit("# EX13b (Direction B): cvxopf PINNED to P*_smooth's DC routing")
emit(f"# pinned p_in[1]={pin_pstar[1]:.4f} (link1 7->9 lossless), "
     f"p_in[2]={pin_pstar[2]:.4f} (link2 5->9 5%)")
emit(f"## solve status: {build.prob.status}")
emit()

if build.prob.status not in ("optimal", "optimal_inaccurate"):
    emit("P*_smooth's routing is INFEASIBLE in cvxopf's problem.")
    emit("=> the routings are not mutually reachable; not a flat ridge.")
    (RES / "ex13b_routing_cvxopf.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    raise SystemExit(0)

res = extract_results(build)
obj_pinned = float(build.prob.value)
Pg = np.asarray(res["Pg"])
pin_now = np.asarray(res["p_hvdc_in"])

emit("## objectives")
emit(f"cvxopf FREE optimum (C*_smooth)      = {C_STAR_FREE_OBJ:.4f}")
emit(f"cvxopf PINNED to P*_smooth routing   = {obj_pinned:.4f}")
emit(f"Pypower P*_smooth (its own solve)     = {P_STAR_OBJ:.4f}")
emit()
emit(f"C*_smooth Pg = [90, 133.40, 93.46]  (free, link1=2 link2=10)")
emit(f"pinned    Pg = {np.round(Pg, 4).tolist()}  (link1={pin_now[1]:.3f} link2={pin_now[2]:.3f})")
emit()

delta_free = obj_pinned - C_STAR_FREE_OBJ
delta_vs_pp = obj_pinned - P_STAR_OBJ
emit(f"pinned - cvxopf_free = {delta_free:+.4f}  (how much P*'s routing costs cvxopf)")
emit(f"pinned - Pypower_P*  = {delta_vs_pp:+.4f}  (cvxopf vs Pypower at the SAME routing)")
emit()

emit("## VERDICT")
if abs(delta_free) < 1.0:
    emit(f"DEGENERATE: at P*'s routing cvxopf reaches {obj_pinned:.2f} ~= its free")
    emit(f"optimum {C_STAR_FREE_OBJ:.2f}. The link1<->link2 trade is a FLAT RIDGE;")
    emit("both routings are optimal, solvers just picked different valley-floor")
    emit("points. No suboptimality -- cost-degenerate routing.")
elif delta_free > 1.0:
    emit(f"NOT degenerate: P*'s routing costs cvxopf {delta_free:+.2f} MORE than its")
    emit(f"free optimum. cvxopf's C*_smooth ({C_STAR_FREE_OBJ:.2f}) is genuinely cheaper.")
    if delta_vs_pp < -1.0:
        emit(f"AND at that SAME routing cvxopf ({obj_pinned:.2f}) beats Pypower's P*")
        emit(f"({P_STAR_OBJ:.2f}) by {-delta_vs_pp:.2f} -- so P*_smooth is mildly")
        emit("SUBOPTIMAL on the routing axis too (the EX12 pattern, repeated).")
    else:
        emit(f"At that routing cvxopf ~= Pypower ({obj_pinned:.2f} vs {P_STAR_OBJ:.2f});")
        emit("Pypower is optimal FOR THAT routing but chose the costlier routing.")

(RES / "ex13b_routing_cvxopf.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
