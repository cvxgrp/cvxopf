"""
EX12: SOLVE for C+ (C* basin with link0 loss0 imposed) and fire the QED.

Run: uv run --active python _ex12_cplus_solve.py
Writes results/ex12_cplus.txt.

============================================================================
What this settles
============================================================================
EX6 showed C* is feasible in (neutralized) Pypower's problem to machine
precision EXCEPT one term: the loss0 on link0 (C7 with-loss0 residual = -1 MW,
cvxopf drops loss0 by design). EX11 tried to CLOSE that gap by a static nudge
of p_out[0] + a hand rebalance of 1 MW on gen2 -- and FAILED, because loss0 is
real power consumed AT the converter to-bus and couples through the AC power
flow; a static rebalance on a gen at a different bus leaves a 1 MW nodal
residual (EX11 C1 = 1.0 MW at bus 3).

EX12 does it correctly: don't CONSTRUCT C+, SOLVE for it. Rebuild cvxopf's own
AC-OPF for case9_dcline with link0's loss coupling REPLACED by Pypower's
with-loss0 law (p_out[0] = coeff0*p_in[0] + L0[0]), leave links 1/2 unchanged,
and let IPOPT redistribute the 1 MW through the real network flow. The result
is C+. If C+ is FULLY feasible in neutralized Pypower's problem (all EX6
residuals ~0, INCLUDING C7 on every link) AND its objective (in Pypower's own
cost model) is below P* = 6249.87, then Pypower returned a suboptimal point:
P* is not optimal for its own problem.  Q.E.D.

============================================================================
Regime (matches the whole investigation)
============================================================================
NEUTRALIZED: branch limits ELIMINATED. cvxopf's AC path enforces no branch
limits (Milestone 4 is a stub); the Pypower side of the comparison is
`rateA=1e6`. The EX6 feasibility battery below therefore does NOT re-impose
branch limits -- that is intentional, not an omission. C+ is being compared
against neutralized Pypower (P* = 6249.87), the correct like-for-like analog
of cvxopf's model (see HANDOFF.md B.1, memory case9-dcline-optima-gap.md).

============================================================================
DCP discipline (per session primer)
============================================================================
The assembled AC-OPF is nonconvex BY DESIGN (sin/cos power flow) and solved via
DNLP/IPOPT with nlp=True, which bypasses CVXPY's DCP check -- so is_dcp() on
the whole problem is False and that is expected, NOT a failure. What EX12
certifies is the ONE thing it grafts: the three replacement loss-coupling
equalities. Each is a scalar AFFINE equality; we CONSTRUCT it from atoms and
VERIFY it in isolation (constraint.is_dcp() True, both sides curvature AFFINE)
BEFORE grafting -- localising correctness to the touched term. Construct and
check; never reason-and-ship.

No src/ changes: the graft replaces the coupling constraint on a copy of the
built problem, reassigns build.prob, and calls build.solve() (which sets
solver=cp.IPOPT, nlp=True for AC -- so we never call build.prob.solve()
directly, per CLAUDE.md).
"""
import json
import warnings
from pathlib import Path

import cvxpy as cp
import numpy as np

from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.network import reindex_case_to_consecutive
from cvxopf.problem import build_opf
from cvxopf.results import extract_results
from cvxopf.testcases.case9_dcline import case9_dcline

_here = Path(__file__).resolve().parent
RES = _here / "results"

P_STAR = 6249.8659

lines = []


def emit(s=""):
    lines.append(s)


# ---------------------------------------------------------------------------
# 1. Build cvxopf's AC-OPF for case9_dcline (normal build; no branch limits).
# ---------------------------------------------------------------------------
case = case9_dcline()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")  # loss0-dropped UserWarning is expected
    links = hvdc_from_dcline(case["dcline"])
    build = build_opf(case, formulation="ac", hvdc=links)

p_in = build.variables["p_hvdc_in"]
p_out = build.variables["p_hvdc_out"]
n_hvdc = build.data["n_hvdc"]

# dcline constants (external table order == active-link order == hvdc order)
_STATUS, _PMIN, _PMAX, _LOSS0, _LOSS1 = 2, 9, 10, 15, 16
DC = case["dcline"]
dc_on = DC[DC[:, _STATUS] > 0, :]
L0 = dc_on[:, _LOSS0].astype(float)
L1 = dc_on[:, _LOSS1].astype(float)
# Loss-branch coefficient cvxopf uses on each fixed-direction (from->to) link:
#   p_out[k] = -(1 - loss_frac[k]) * p_in[k]     (hvdc.py:250)
coeff = -(1.0 - L1)

emit("# EX12: SOLVE for C+ (C* basin with link0 loss0 imposed) and fire the QED")
emit(f"# n_hvdc={n_hvdc}  L0={L0.tolist()}  L1={L1.tolist()}  coeff={coeff.tolist()}")
emit(f"# regime: NEUTRALIZED (no branch limits)   P* = {P_STAR}")
emit()

# ---------------------------------------------------------------------------
# 2. Locate the existing vectorized loss-coupling constraint in build.prob.
#    It is the single equality of the form  p_out == multiply(coeff_vec, p_in)
#    (hvdc.py:268) -- identify it by the variable ids on both sides.
# ---------------------------------------------------------------------------
p_in_id = p_in.id
p_out_id = p_out.id


def _var_ids(expr):
    return {v.id for v in expr.variables()}


# CAUTION: the NODAL BALANCE constraint (p == ... Ch_from@p_in + Ch_to@p_out
# ...) ALSO involves both p_in and p_out. The coupling is the equality whose
# ONLY variables are p_in and p_out -- match on that, not just "involves both"
# (probe _ex12_probe_locate.py showed the loose match hits nodal balance first,
# and deleting nodal balance makes the problem locally infeasible).
coupling_idx = None
for i, con in enumerate(build.prob.constraints):
    if not isinstance(con, cp.constraints.Equality):
        continue
    ids = _var_ids(con.args[0]) | _var_ids(con.args[1])
    if ids == {p_in_id, p_out_id}:
        coupling_idx = i
        break

if coupling_idx is None:
    emit("FATAL: could not locate the HVDC loss-coupling constraint.")
    (RES / "ex12_cplus.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    raise SystemExit(1)

emit(f"located loss-coupling constraint at build.prob.constraints[{coupling_idx}]")
emit(f"  original: {build.prob.constraints[coupling_idx]}")
emit()

# ---------------------------------------------------------------------------
# 3. CONSTRUCT the replacement equalities and VERIFY EACH in isolation (DCP).
#    link0: p_out[0] == coeff[0]*p_in[0] + L0[0]   (Pypower with-loss0 law)
#    link k>0: p_out[k] == coeff[k]*p_in[k]        (unchanged)
# ---------------------------------------------------------------------------
new_couplings = []
emit("## DCP verification of the grafted equalities (in isolation)")
for k in range(n_hvdc):
    offset = L0[k] if k == 0 else 0.0
    rhs = coeff[k] * p_in[k] + offset
    con = p_out[k] == rhs
    lhs_curv = p_out[k].curvature
    rhs_curv = rhs.curvature
    ok = con.is_dcp() and lhs_curv == "AFFINE" and rhs_curv == "AFFINE"
    emit(
        f"  link{k}: p_out[{k}] == {coeff[k]:+.4f}*p_in[{k}]"
        f"{'' if offset == 0.0 else f' + {offset:g}'}"
        f"  | is_dcp={con.is_dcp()} lhs={lhs_curv} rhs={rhs_curv} -> {'OK' if ok else 'FAIL'}"
    )
    if not ok:
        emit("FATAL: grafted equality did not certify as affine/DCP.")
        (RES / "ex12_cplus.txt").write_text("\n".join(lines) + "\n")
        print("\n".join(lines))
        raise SystemExit(1)
    new_couplings.append(con)
emit()

# ---------------------------------------------------------------------------
# 4. Graft: replace the vector coupling with the three scalar equalities,
#    reassign build.prob, and solve via build.solve() (IPOPT, nlp=True).
# ---------------------------------------------------------------------------
new_constraints = list(build.prob.constraints)
del new_constraints[coupling_idx]
new_constraints.extend(new_couplings)
build.prob = cp.Problem(build.prob.objective, new_constraints)

build.solve()
emit(f"## solve status: {build.prob.status}   objective(cvxopf): {build.prob.value:.4f}")
emit()

res = extract_results(build)
Pg_plus = np.asarray(res["Pg"])          # MW
Qg_plus = np.asarray(res["Qg"])          # MVAr
Vm_plus = np.asarray(res["Vm"])
Va_plus = np.deg2rad(np.asarray(res["Va_deg"]))
pin_plus = np.asarray(res["p_hvdc_in"])   # MW
pout_plus = np.asarray(res["p_hvdc_out"]) # MW

# ---------------------------------------------------------------------------
# 5. FEASIBILITY of C+ in neutralized Pypower (EX6-style, NO branch limits).
#    Residuals against cvxopf's own Ybus (licensed by 4.4e-16 Ybus agreement)
#    + dcline constants. C7 uses the WITH-loss0 law on every link.
# ---------------------------------------------------------------------------
d = build.data
baseMVA = d["baseMVA"]
Ybus = np.asarray(d["Ybus"])
Cg = np.asarray(d["Cg"])
Ch_from = np.asarray(d["Ch_from"])
Ch_to = np.asarray(d["Ch_to"])
Pd = np.asarray(d["Pd"]) * baseMVA
Qd = np.asarray(d["Qd"]) * baseMVA
Pgmin = np.asarray(d["Pgmin"]) * baseMVA
Pgmax = np.asarray(d["Pgmax"]) * baseMVA
Qgmin = np.asarray(d["Qgmin"]) * baseMVA
Qgmax = np.asarray(d["Qgmax"]) * baseMVA
Pmin_dc = dc_on[:, _PMIN].astype(float)
Pmax_dc = dc_on[:, _PMAX].astype(float)

_VMIN, _VMAX = 12, 11
_case_ri, _ = reindex_case_to_consecutive(case9_dcline())
vmin = np.asarray(_case_ri["bus"][:, _VMIN], float)
vmax = np.asarray(_case_ri["bus"][:, _VMAX], float)

V = Vm_plus * np.exp(1j * Va_plus)
hvdc_p = Ch_from @ pin_plus + Ch_to @ pout_plus
p_inj = Cg @ Pg_plus - Pd + hvdc_p        # MW
q_inj = Cg @ Qg_plus - Qd                 # MVAr
S_net = V * np.conj(Ybus @ V) * baseMVA

c1 = float(np.max(np.abs(p_inj - S_net.real)))
c1_bus = int(np.argmax(np.abs(p_inj - S_net.real)))
c2 = float(np.max(np.abs(q_inj - S_net.imag)))
c3 = max(float(np.max(Pgmin - Pg_plus)), float(np.max(Pg_plus - Pgmax)))
c4 = max(float(np.max(Qgmin - Qg_plus)), float(np.max(Qg_plus - Qgmax)))
c5 = max(float(np.nanmax(vmin - Vm_plus)), float(np.nanmax(Vm_plus - vmax)))
c6 = max(float(np.max(Pmin_dc - pin_plus)), float(np.max(pin_plus - Pmax_dc)))
c7_resid = pout_plus - (coeff * pin_plus + L0)  # WITH-loss0 law on all links
c7 = float(np.max(np.abs(c7_resid)))

emit("## feasibility of C+ in neutralized Pypower (NO branch limits)")
emit(f"C1 nodal real     = {c1:.3e} MW   (worst at bus {c1_bus})")
emit(f"C2 nodal reactive = {c2:.3e} MVAr")
emit(f"C3 gen P bounds worst viol   = {c3:.3e}  (<=0 ok)")
emit(f"C4 gen Q bounds worst viol   = {c4:.3e}  (<=0 ok)")
emit(f"C5 voltage bounds worst viol = {c5:.3e}  (<=0 ok)")
emit(f"C6 DC box worst viol         = {c6:.3e}  (<=0 ok)")
emit(f"C7 WITH-loss0 coupling resid = {np.round(c7_resid, 8).tolist()}  (all ~0 => no exception)")
emit()

feasible = (
    c1 < 1e-6
    and c2 < 1e-6
    and c3 < 1e-6
    and c4 < 1e-6
    and c5 < 1e-6
    and c6 < 1e-6
    and c7 < 1e-6
)
emit(f"FULLY FEASIBLE in neutralized Pypower: {feasible}")
emit()

# ---------------------------------------------------------------------------
# 6. OBJECTIVE of C+ in Pypower's cost model (real gens only; dcline zero-cost).
#    CRITICAL: case9_dcline gencost MIXES cost models -- do NOT assume
#    quadratic (that was the EX11 bug, reproduced here at first: reading PWL
#    (x,f) breakpoint pairs as polynomial coeffs gives the spurious 11536).
#    Dispatch on MODEL (col 0):
#      MODEL=2 (polynomial): cols 4:4+NCOST are highest-power-first coeffs.
#      MODEL=1 (piecewise-linear): cols 4:4+2*NCOST are (x0,f0,x1,f1,...)
#        breakpoint pairs; cost is linear interpolation between them (Pypower
#        convention). np.interp is exact on the segment endpoints and matches
#        Pypower's convex PWL evaluation on this monotone-increasing cost.
#    Verified against C* (known obj 5490.10) by _ex12_probe_cost.py.
#    case9_dcline gencost: row0 MODEL=1 (4 pts), row1 MODEL=2 (linear),
#    row2 MODEL=1 (3 pts).
# ---------------------------------------------------------------------------
_MODEL, _NCOST = 0, 3
gencost = case["gencost"][:3, :]


def _gencost(gc_row, pg):
    model = int(gc_row[_MODEL])
    n = int(gc_row[_NCOST])
    if model == 2:  # polynomial, highest-power-first coeffs
        return float(np.polyval(gc_row[4 : 4 + n], pg))
    if model == 1:  # piecewise-linear (x0,f0,x1,f1,...) breakpoints
        pts = gc_row[4 : 4 + 2 * n].reshape(n, 2)
        return float(np.interp(pg, pts[:, 0], pts[:, 1]))
    raise ValueError(f"unsupported gencost MODEL={model}")


obj_plus = float(sum(_gencost(gencost[i], Pg_plus[i]) for i in range(3)))
emit("## objective of C+ in Pypower's cost model")
emit(f"C+ objective (Pypower cost) = {obj_plus:.4f}")
emit(f"P* objective                = {P_STAR}")
emit(f"C+ < P* : {obj_plus < P_STAR}   (margin {P_STAR - obj_plus:.4f})")
emit()

# ---------------------------------------------------------------------------
# 7. DISPATCH DISTANCE from C* -- the "mild shift vs slide-to-P*" test.
# ---------------------------------------------------------------------------
cstar = json.loads((RES / "cstar_full.json").read_text())
Pg_c = np.asarray(cstar["Pg"])
pin_c = np.asarray(cstar["p_hvdc_in"])
pout_c = np.asarray(cstar["p_hvdc_out"])

# In-band guard: the cost readout MUST reproduce C*'s known objective 5490.10
# from C*'s own dispatch. This is the check that catches the mixed cost-model
# trap (EX11 read PWL breakpoints as poly coeffs -> spurious 11536). If this
# fails, the C+ objective below is untrustworthy and the QED is void.
obj_cstar_readout = float(sum(_gencost(gencost[i], Pg_c[i]) for i in range(3)))
emit("## cost-readout self-check (must reproduce C* obj 5490.10)")
emit(f"_gencost at C* dispatch = {obj_cstar_readout:.4f}  (C* known = 5490.10)")
_readout_ok = abs(obj_cstar_readout - 5490.10) < 1.0
emit(f"cost readout valid: {_readout_ok}")
emit()
# P* dispatch (from HANDOFF/EX7a): Pg [90, 106.14, 123.48], p_in [1, 10, 10]
Pg_p = np.array([90.0, 106.14, 123.48])
pin_p = np.array([1.0, 10.0, 10.0])

emit("## dispatch: C+ vs C* vs P*  (the hypothesis test)")
emit(f"           {'Pg0':>10} {'Pg1':>10} {'Pg2':>10} | {'pin0':>7} {'pin1':>7} {'pin2':>7}")
emit(
    f"C*     {Pg_c[0]:>10.3f} {Pg_c[1]:>10.3f} {Pg_c[2]:>10.3f} | "
    f"{pin_c[0]:>7.3f} {pin_c[1]:>7.3f} {pin_c[2]:>7.3f}"
)
emit(
    f"C+     {Pg_plus[0]:>10.3f} {Pg_plus[1]:>10.3f} {Pg_plus[2]:>10.3f} | "
    f"{pin_plus[0]:>7.3f} {pin_plus[1]:>7.3f} {pin_plus[2]:>7.3f}"
)
emit(
    f"P*     {Pg_p[0]:>10.3f} {Pg_p[1]:>10.3f} {Pg_p[2]:>10.3f} | "
    f"{pin_p[0]:>7.3f} {pin_p[1]:>7.3f} {pin_p[2]:>7.3f}"
)
emit(f"||Pg(C+) - Pg(C*)|| = {float(np.linalg.norm(Pg_plus - Pg_c)):.4f} MW")
emit(f"||Pg(C+) - Pg(P*)|| = {float(np.linalg.norm(Pg_plus - Pg_p)):.4f} MW")
emit(f"link1 p_in: C*={pin_c[1]:.3f} (box min 2)  C+={pin_plus[1]:.3f}  P*={pin_p[1]:.3f} (box max 10)")
emit()

# ---------------------------------------------------------------------------
# 8. VERDICT
# ---------------------------------------------------------------------------
emit("## VERDICT")
if feasible and obj_plus < P_STAR:
    emit("C+ is FULLY feasible in neutralized Pypower's own problem AND cheaper than P*.")
    emit("=> P* is NOT optimal for Pypower's problem; Pypower returned a suboptimal")
    emit("   local point. The gap is a genuine local-optimum / DNLP-tractability")
    emit("   effect, not a model difference.  Q.E.D.")
elif not feasible:
    emit("C+ is NOT fully feasible -- imposing loss0 alone did not reconcile C+ with")
    emit("neutralized Pypower. QED does not fire; inspect the worst residual above.")
else:
    emit("C+ feasible but NOT cheaper than P* -- QED does not fire. If C+ dispatch")
    emit("slid toward P*, that contradicts the 'mild shift' hypothesis; inspect §7.")

(RES / "ex12_cplus.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
