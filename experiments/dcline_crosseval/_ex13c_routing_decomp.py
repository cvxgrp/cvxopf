"""EX13c: decompose WHY cvxopf's smooth-cost routing (floor lossless link1,
max lossy link2) beats Pypower's (max lossless link1, idle link2).

Endpoint swap RULED OUT (_ex13_probe_endpoints.py): both models solve the same
physical problem. link1 (7->9, lossless) and link2 (5->9, 5%) both deliver to
bus 9 but WITHDRAW at different buses (7 vs 5). cvxopf prefers withdrawing at
bus 5 via the lossy link. This asks: is that a real network-cost optimum?

Reads apart three already-solved smooth-cost points (NO re-solve here except to
recover C+_pinned's full state, which EX13b already solved):
  C*_smooth  : cvxopf free optimum (link1=2, link2=10),  obj 5262.04
  C_pinned   : cvxopf pinned to P*'s routing (link1=10, link2~0), obj 5280.44
  P*_smooth  : Pypower's own solve (link1=10, link2~0),   obj 5314.28

Accounting (all MW), per solved point, from its own Vm/Va + cvxopf's Ybus
(licensed by the 4.4e-16 Ybus agreement):
  - total generation  sum(Pg)
  - total load         sum(Pd)  (fixed)
  - AC branch losses   sum over branches of I^2*r == sum(p_inj_bus) contributions
    computed as total_gen + net_DC_injection - total_load
  - DC transport loss  sum(p_in + p_out) over links  (>=0)
The key contrast: does C*'s 0.5 MW DC loss BUY a reduction in AC losses / cheaper
generation that more than pays for itself? Decompose 5262 vs 5280 (same model,
cvxopf both) to isolate the routing effect cleanly, then note where Pypower's
extra 34 (5280->5314 at the SAME routing) is the stuck-solver residual.

Run (main env): uv run --active python _ex13c_routing_decomp.py
Writes results/ex13c_routing_decomp.txt.
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


# ---------------------------------------------------------------------------
# Build the smooth-cost model once; reuse its data + a loss0-grafted problem to
# recover each point's full state. We solve C*_free and C_pinned here (fast) and
# read P*_smooth from the sandbox JSON.
# ---------------------------------------------------------------------------
case = case9_dcline()
case["gencost"] = CASE9_GENCOST.copy()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    links = hvdc_from_dcline(case["dcline"])
    build = build_opf(case, formulation="ac", hvdc=links)

p_in = build.variables["p_hvdc_in"]
p_out = build.variables["p_hvdc_out"]
n_hvdc = build.data["n_hvdc"]
d = build.data
baseMVA = d["baseMVA"]
Ybus = np.asarray(d["Ybus"])
Pd = np.asarray(d["Pd"]) * baseMVA
Cg = np.asarray(d["Cg"])
Chf = np.asarray(d["Ch_from"])
Cht = np.asarray(d["Ch_to"])
DC = case["dcline"]
dc_on = DC[DC[:, 2] > 0, :]
L0 = dc_on[:, 15].astype(float)
L1 = dc_on[:, 16].astype(float)
coeff = -(1.0 - L1)

# graft loss0 (EX12/EX13 mechanism)
coupling_idx = next(
    i
    for i, con in enumerate(build.prob.constraints)
    if isinstance(con, cp.constraints.Equality)
    and {v.id for v in con.variables()} == {p_in.id, p_out.id}
)
base_constraints = list(build.prob.constraints)
del base_constraints[coupling_idx]
for k in range(n_hvdc):
    off = L0[k] if k == 0 else 0.0
    base_constraints.append(p_out[k] == coeff[k] * p_in[k] + off)
objective = build.prob.objective


def _solve_variant(extra=()):
    build.prob = cp.Problem(objective, list(base_constraints) + list(extra))
    build.solve()
    r = extract_results(build)
    return r, float(build.prob.value)


# C*_smooth (free) and C_pinned (P*'s routing)
ps = json.loads((RES / "ex13_pstar_smooth.json").read_text())
pin_pstar = -np.asarray(ps["from_dummy_Pg"])
res_free, obj_free = _solve_variant()
res_pin, obj_pin = _solve_variant(
    extra=[p_in[1] == float(pin_pstar[1]), p_in[2] == float(pin_pstar[2])]
)


def _account(res, label):
    """Energy/cost accounting for a solved cvxopf point."""
    Pg = np.asarray(res["Pg"])
    pin = np.asarray(res["p_hvdc_in"])
    pout = np.asarray(res["p_hvdc_out"])
    Vm = np.asarray(res["Vm"])
    Va = np.deg2rad(np.asarray(res["Va_deg"]))
    V = Vm * np.exp(1j * Va)
    # AC network real power absorbed = sum of bus real injections from Ybus
    S = V * np.conj(Ybus @ V) * baseMVA
    ac_losses = float(np.sum(S.real))  # sum of nodal real injections = total AC loss
    dc_loss = float(np.sum(pin + pout))  # per-link p_in+p_out >= 0 is the loss
    total_gen = float(np.sum(Pg))
    total_load = float(np.sum(Pd))
    cost = float(sum(np.polyval(CASE9_GENCOST[i, 4:7], Pg[i]) for i in range(3)))
    return {
        "label": label,
        "Pg": Pg,
        "pin": pin,
        "ac_losses": ac_losses,
        "dc_loss": dc_loss,
        "total_gen": total_gen,
        "total_load": total_load,
        "cost": cost,
    }


free = _account(res_free, "C*_smooth (free: link1=2, link2=10)")
pin = _account(res_pin, "C_pinned (P* routing: link1=10, link2~0)")

emit("# EX13c: routing decomposition -- why does cvxopf source at bus 5 (5% loss)?")
emit("# endpoint swap ruled out; both links deliver to bus 9, withdraw at 7 vs 5.")
emit(f"# baseMVA={baseMVA}  total load={free['total_load']:.3f} MW")
emit()
emit(f"{'point':<40} {'sum Pg':>9} {'AC loss':>9} {'DC loss':>8} {'cost':>10}")
for a in (free, pin):
    emit(
        f"{a['label']:<40} {a['total_gen']:>9.3f} {a['ac_losses']:>9.3f} "
        f"{a['dc_loss']:>8.3f} {a['cost']:>10.4f}"
    )
emit()
emit("## per-generator Pg (MW)")
emit(f"{'point':<40} {'gen0(b1)':>9} {'gen1(b2)':>9} {'gen2(b30)':>9}")
for a in (free, pin):
    emit(f"{a['label']:<40} {a['Pg'][0]:>9.3f} {a['Pg'][1]:>9.3f} {a['Pg'][2]:>9.3f}")
emit()

# ---------------------------------------------------------------------------
# The decomposition: free vs pinned, same solver, same model. dCost = dGen-cost;
# dGen = dAC_loss + dDC_loss (load fixed). Show the routing buys/pays what.
# ---------------------------------------------------------------------------
d_cost = pin["cost"] - free["cost"]
d_gen = pin["total_gen"] - free["total_gen"]
d_ac = pin["ac_losses"] - free["ac_losses"]
d_dc = pin["dc_loss"] - free["dc_loss"]
emit("## routing decomposition (pinned - free; same solver/model)")
emit(f"d(cost)     = {d_cost:+.4f}   (P*'s routing costs cvxopf this much more)")
emit(f"d(total gen)= {d_gen:+.4f} MW")
emit(f"d(AC loss)  = {d_ac:+.4f} MW  (does sourcing at bus 5 cut AC losses?)")
emit(f"d(DC loss)  = {d_dc:+.4f} MW  (free pays MORE DC loss via the 5% link)")
emit()
emit("## interpretation")
if d_ac < 0:
    emit(f"C*'s routing INCREASES DC loss by {-d_dc:.3f} MW but CUTS AC loss by")
    emit(f"{-d_ac:.3f} MW -- sourcing at bus 5 relieves the AC network more than")
    emit("the 5% DC penalty costs. Net cheaper. The inverted routing is a GENUINE")
    emit("network optimum (mechanism 1), not just a solver artifact.")
else:
    emit("C*'s routing does NOT cut AC loss -- the advantage is elsewhere")
    emit("(generation mix / cost curvature). Inspect per-gen Pg above.")
emit()

# Pypower residual at the SAME routing (stuck-solver piece, from EX13b/JSON)
P_STAR = float(ps["obj"])
emit("## Pypower residual (mechanism 3, stuck solver)")
emit(f"cvxopf at P*'s routing = {obj_pin:.4f};  Pypower at P*'s routing = {P_STAR:.4f}")
emit(f"Pypower leaves {P_STAR - obj_pin:+.4f} on the table AT ITS OWN ROUTING --")
emit("the nondifferentiable-corner pathology, same as EX12. So the DC-alone cell")
emit("combines: (1) a real network-optimal inverted routing cvxopf finds, and")
emit("(3) Pypower additionally stuck short even at the routing it did pick.")

(RES / "ex13c_routing_decomp.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
