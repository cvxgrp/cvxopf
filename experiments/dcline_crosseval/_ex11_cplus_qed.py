"""
EX11: build C+ (C* made fully Pypower-feasible) and test the QED.

Pure main-env script (like _ex6_proper_constraint_residual.py): NO pypower
import. Run: uv run --active python _ex11_cplus_qed.py

============================================================================
!!! NOT SOLID / PARTIAL RESULT -- the QED does NOT fire from this script.
============================================================================
This attempts to prove P* suboptimal by CONSTRUCTING a fully-Pypower-feasible,
cheaper point C+ from C* (nudge link0 p_out -0.99->+0.01 to satisfy the
with-loss0 law, rebalance 1 MW on a gen). It FAILS as a static construction:
the nudge injects +1 MW at the converter (to-)bus, and a static rebalance on a
generator at a DIFFERENT bus cannot close nodal balance -- the run shows
C1 = 1.0 MW residual at bus 3. loss0 is real power consumed at the converter
and couples through the AC power flow, so it cannot be offset by a static
algebraic nudge. (The inline cost readout was also buggy: 11536, wrong.)

Correct approach is EX12: don't CONSTRUCT C+, SOLVE for it -- re-solve with
link0 loss0 IMPOSED to get a genuinely Pypower-feasible point in the C* basin,
then compare its objective to P*. Kept as a documented partial / dead end.
See memory case9-dcline-optima-gap.md (EX11 NOT SOLID + EX12 NEXT).
============================================================================


EX6 showed C* is feasible in Pypower's problem EXCEPT one term: the loss0 on
link0 (C7 residual exactly -1 MW; p_out[0]=-0.99 vs Pypower-required +0.01).
Everything else -- nodal balance, bounds -- was machine-precision.

C+ = C* with the single DC-offset adjustment that removes that one exception:
  - link0 p_out: -0.99 -> +0.01  (satisfies Pypower's WITH-loss0 law exactly)
  - the resulting +1 MW surplus at the to-bus is rebalanced by -1 MW on gen2
    (bus 30), the only real gen with headroom -- gen0 (bus1) and gen1 (bus2)
    are pinned at Pmin=90/10 at C*, so the reduction must land on gen2.

QED: if C+ is FULLY feasible in Pypower's problem (all residuals ~0, INCLUDING
C7=0 on every link -- no exceptions) AND its objective (in Pypower's own cost
model) is below P*=6249.87, then P* is not optimal for Pypower's own problem:
Pypower returned a suboptimal point. Q.E.D.

Feasibility checked EX6-style against cvxopf's Ybus (licensed by the Ybus
agreement, max diff 4.4e-16) + the dcline constants. Objective computed with
inline np.polyval

Run: uv run --active python _ex11_cplus_qed.py).  Writes results/ex11_cplus_qed.txt.
"""
import importlib.util
import json
import warnings
from pathlib import Path

import numpy as np

_here = Path(__file__).resolve().parent
RES = _here / "results"

# --- load C* ---
cv = json.loads((RES / "cstar_full.json").read_text())
e2i = {int(k): int(v) for k, v in cv["ext_to_int"].items()}
Vm = np.array(cv["Vm"]); Va = np.deg2rad(np.array(cv["Va_deg"]))
V = Vm * np.exp(1j * Va)
Pg = np.array(cv["Pg"])            # MW  [gen0,gen1,gen2]
Qg = np.array(cv["Qg"])            # MVAr
p_in = np.array(cv["p_hvdc_in"])   # MW
p_out = np.array(cv["p_hvdc_out"]) # MW

# --- network matrices from cvxopf (no solve) ---
from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf
from cvxopf.network import reindex_case_to_consecutive

case = case9_dcline()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    links = hvdc_from_dcline(case["dcline"])
    b = build_opf(case, formulation="ac", hvdc=links)
d = b.data
baseMVA = d["baseMVA"]
Ybus = np.asarray(d["Ybus"])
Cg = np.asarray(d["Cg"]); Ch_from = np.asarray(d["Ch_from"]); Ch_to = np.asarray(d["Ch_to"])
Pd = np.asarray(d["Pd"]) * baseMVA; Qd = np.asarray(d["Qd"]) * baseMVA
Pgmin = np.asarray(d["Pgmin"]) * baseMVA; Pgmax = np.asarray(d["Pgmax"]) * baseMVA
Qgmin = np.asarray(d["Qgmin"]) * baseMVA; Qgmax = np.asarray(d["Qgmax"]) * baseMVA
VMIN, VMAX = 12, 11
_case_ri, _ = reindex_case_to_consecutive(case9_dcline())
vmin = np.asarray(_case_ri["bus"][:, VMIN], float); vmax = np.asarray(_case_ri["bus"][:, VMAX], float)

# --- dcline constants ---
DC = case["dcline"]; STATUS, PMIN, PMAX, LOSS0, LOSS1 = 2, 9, 10, 15, 16
dc_on = DC[DC[:, STATUS] > 0, :]
Pmin_dc = dc_on[:, PMIN].astype(float); Pmax_dc = dc_on[:, PMAX].astype(float)
L0 = dc_on[:, LOSS0].astype(float); L1 = dc_on[:, LOSS1].astype(float)

# ============================================================
# CONSTRUCT C+ : nudge link0 p_out, rebalance -1 MW on gen2
# ============================================================
p_out_plus = p_out.copy()
p_out_plus[0] = -(1.0 - L1[0]) * p_in[0] + L0[0]   # Pypower with-loss0 law = +0.01
delta = p_out_plus[0] - p_out[0]                    # +1 MW injected at to-bus
Pg_plus = Pg.copy()
Pg_plus[2] = Pg[2] - delta                          # rebalance on gen2 (has headroom)

lines = []
def emit(s): lines.append(s)
emit("# EX11: C+ (C* made fully Pypower-feasible) and the QED test")
emit(f"# baseMVA={baseMVA}  P*_obj=6249.87")
emit("")
emit("## construction")
emit(f"link0 p_out: {p_out[0]:.3f} -> {p_out_plus[0]:.3f}  (Pypower with-loss0 law)")
emit(f"surplus at to-bus delta = {delta:.4f} MW  -> gen2 Pg {Pg[2]:.3f} -> {Pg_plus[2]:.3f}")
emit(f"Pg+  = {np.round(Pg_plus,4).tolist()}   Pmin={Pgmin.tolist()}  Pmax={Pgmax.tolist()}")
emit(f"p_out+ = {np.round(p_out_plus,4).tolist()}")
emit("")

# ============================================================
# FEASIBILITY of C+ in Pypower's problem (EX6-style)
# ============================================================
# nodal balance: Cg@Pg - Pd + Ch_from@p_in + Ch_to@p_out  ==  Re/Im(V conj(Ybus V))
hvdc_p = Ch_from @ p_in + Ch_to @ p_out_plus
p_inj = Cg @ Pg_plus - Pd + hvdc_p                  # MW
q_inj = Cg @ Qg - Qd
S_net = V * np.conj(Ybus @ V) * baseMVA
c1 = float(np.max(np.abs(p_inj - S_net.real)))
c2 = float(np.max(np.abs(q_inj - S_net.imag)))
emit("## feasibility (residuals, MW / MVAr)")
emit(f"C1 nodal real     = {c1:.3e}  (at bus {int(np.argmax(np.abs(p_inj - S_net.real)))})")
emit(f"C2 nodal reactive = {c2:.3e}")
c3 = max(float(np.max(Pgmin - Pg_plus)), float(np.max(Pg_plus - Pgmax)))
c4 = max(float(np.max(Qgmin - Qg)), float(np.max(Qg - Qgmax)))
c5 = max(float(np.nanmax(vmin - Vm)), float(np.nanmax(Vm - vmax)))
c6 = max(float(np.max(Pmin_dc - p_in)), float(np.max(p_in - Pmax_dc)))
emit(f"C3 gen P bounds worst viol   = {c3:.3e}  (<=0 ok)")
emit(f"C4 gen Q bounds worst viol   = {c4:.3e}  (<=0 ok)")
emit(f"C5 voltage bounds worst viol = {c5:.3e}  (<=0 ok)")
emit(f"C6 DC box worst viol         = {c6:.3e}  (<=0 ok)")
# C7: WITH-loss0 coupling on ALL links -- must be 0 everywhere now
c7_resid = p_out_plus - (-(1.0 - L1) * p_in + L0)
emit(f"C7 with-loss0 coupling resid = {np.round(c7_resid,6).tolist()}  (all ~0 => no exception)")
c7 = float(np.max(np.abs(c7_resid)))
emit("")

feasible = (c1 < 1e-6 and c2 < 1e-6 and c3 < 1e-8 and c4 < 1e-8
            and c5 < 1e-8 and c6 < 1e-8 and c7 < 1e-8)
emit(f"FULLY FEASIBLE in Pypower's problem: {feasible}")
emit("")

# ============================================================
# OBJECTIVE of C+ in Pypower's cost model (real gens only; dcline zero-cost)
# ============================================================
# Pypower polynomial gencost: cols are [MODEL, STARTUP, SHUTDOWN, NCOST, c(n-1)..c0].
# case9 uses quadratic (NCOST=3): cost = c2*Pg^2 + c1*Pg + c0. Read from cvxopf's
# own case (same gencost as Pypower's t_case9_dcline), so no pypower import --
# this keeps EX11 a pure main-env script (like EX6). dcline dummy gens zero-cost.
gencost = case["gencost"][:3, :]
NCOST = 3
def _polycost(gc_row, pg):
    n = int(gc_row[NCOST])
    coeffs = gc_row[4:4 + n]            # highest power first
    return float(np.polyval(coeffs, pg))
obj_plus = float(sum(_polycost(gencost[i], Pg_plus[i]) for i in range(3)))
emit("## objective in Pypower's cost model")
emit(f"C+ objective = {obj_plus:.4f}")
emit(f"P* objective = 6249.8659")
emit(f"C+ < P* : {obj_plus < 6249.8659}  (margin {6249.8659 - obj_plus:.2f})")
emit("")

emit("## VERDICT")
if feasible and obj_plus < 6249.8659:
    emit("C+ is FULLY feasible in Pypower's own problem AND cheaper than P*.")
    emit("=> P* is NOT optimal for Pypower's problem; Pypower returned a")
    emit("   suboptimal local point.  Q.E.D.")
elif not feasible:
    emit("C+ is NOT fully feasible -- the single DC-offset nudge was insufficient;")
    emit("the C*/P* difference exceeds loss0. QED does not fire; investigate.")
else:
    emit("C+ feasible but NOT cheaper than P* -- QED does not fire.")

(RES / "ex11_cplus_qed.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
