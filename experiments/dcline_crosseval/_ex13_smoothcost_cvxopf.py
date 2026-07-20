"""EX13 (cvxopf side): neutralized case9_dcline AC solve with case9's SMOOTH
quadratic generator costs + link0 loss0 imposed (EX12 known-direction graft).

Companion to _ex13_smoothcost_pypower.py. Together they test the user's
hypothesis that the PWL cost is what tripped Pypower into the suboptimal P*
(EX12). With SMOOTH costs, do the two model+solve paths converge on roughly the
same generator dispatch and DC-line flows?  If yes -> PWL cost was the tripwire
-> localizes the DNLP-tractability effect to the nonsmooth regime (CLARIFIES,
does not refute, [[dnlp-canonicalization-tractability-thesis]]).

One-variable design: loss0 is imposed on BOTH sides (not neutralized) so the
only change vs the PWL baseline is the cost representation. Pypower carries
loss0 natively; here we graft it via the EX12 mechanism (replace link0's
lossless coupling with the with-loss0 affine law, links 1/2 unchanged).

cvxopf's AC path enforces no branch limits (M4 stub) == the neutralized Pypower
rateA=1e6 regime, so the paths are like-for-like.

Run (main env): uv run --active python _ex13_smoothcost_cvxopf.py
Writes results/ex13_cvxopf_smooth.txt.
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

# --- case9 SMOOTH quadratic gencost (same array as the pypower-side script) ---
# layout: [MODEL=2, STARTUP, SHUTDOWN, NCOST=3, c2, c1, c0]
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
# 1. Build cvxopf's AC-OPF for case9_dcline with SMOOTH gencost swapped in.
# ---------------------------------------------------------------------------
case = case9_dcline()
assert case["gencost"].shape[0] == 3, "expected 3 real-gen gencost rows"
case["gencost"] = CASE9_GENCOST.copy()  # swap mixed PWL/poly -> smooth quadratic

with warnings.catch_warnings():
    warnings.simplefilter("ignore")  # loss0-dropped UserWarning is expected
    links = hvdc_from_dcline(case["dcline"])
    build = build_opf(case, formulation="ac", hvdc=links)

p_in = build.variables["p_hvdc_in"]
p_out = build.variables["p_hvdc_out"]
n_hvdc = build.data["n_hvdc"]

_STATUS, _PMIN, _PMAX, _LOSS0, _LOSS1 = 2, 9, 10, 15, 16
DC = case["dcline"]
dc_on = DC[DC[:, _STATUS] > 0, :]
L0 = dc_on[:, _LOSS0].astype(float)
L1 = dc_on[:, _LOSS1].astype(float)
coeff = -(1.0 - L1)

emit("# EX13 (cvxopf): SMOOTH-cost neutralized case9_dcline + link0 loss0 imposed")
emit(f"# L0={L0.tolist()}  L1={L1.tolist()}  coeff={coeff.tolist()}")
emit("# gencost: case9 smooth quadratic (MODEL=2), swapped in for the mixed PWL/poly")
emit()

# ---------------------------------------------------------------------------
# 2. Graft loss0 (EX12 mechanism): replace the coupling equality whose ONLY
#    vars are p_in/p_out (NOT nodal balance, which also contains both -- the
#    EX12 locator trap). link0 gets the +L0 term; links 1/2 unchanged.
# ---------------------------------------------------------------------------
coupling_idx = None
for i, con in enumerate(build.prob.constraints):
    if not isinstance(con, cp.constraints.Equality):
        continue
    ids = {v.id for v in con.variables()}
    if ids == {p_in.id, p_out.id}:
        coupling_idx = i
        break
assert coupling_idx is not None, "could not locate the HVDC loss-coupling constraint"

new_couplings = []
for k in range(n_hvdc):
    offset = L0[k] if k == 0 else 0.0
    rhs = coeff[k] * p_in[k] + offset
    con = p_out[k] == rhs
    assert con.is_dcp() and p_out[k].curvature == "AFFINE" and rhs.curvature == "AFFINE"
    new_couplings.append(con)

new_constraints = list(build.prob.constraints)
del new_constraints[coupling_idx]
new_constraints.extend(new_couplings)
build.prob = cp.Problem(build.prob.objective, new_constraints)

build.solve()
emit(f"## solve status: {build.prob.status}   objective(cvxopf): {build.prob.value:.4f}")
emit()

res = extract_results(build)
Pg_cvx = np.asarray(res["Pg"])
pin_cvx = np.asarray(res["p_hvdc_in"])
pout_cvx = np.asarray(res["p_hvdc_out"])

# ---------------------------------------------------------------------------
# 3. In-band cost-readout guard (smooth quadratic; reproduce cvxopf's own obj).
#    With MODEL=2 rows this is np.polyval on cols 4:4+NCOST (highest-first).
# ---------------------------------------------------------------------------
_NCOST = 3


def _polycost(gc_row, pg):
    n = int(gc_row[_NCOST])
    return float(np.polyval(gc_row[4 : 4 + n], pg))


obj_readout = float(sum(_polycost(CASE9_GENCOST[i], Pg_cvx[i]) for i in range(3)))
emit("## cost-readout guard (must match cvxopf solve objective)")
emit(f"_polycost at C*_smooth dispatch = {obj_readout:.4f}  (cvxopf obj {build.prob.value:.4f})")
emit(f"readout valid: {abs(obj_readout - build.prob.value) < 1.0}")
emit()

# ---------------------------------------------------------------------------
# 4. HEAD-TO-HEAD vs Pypower P*_smooth (read the sandbox JSON if present).
#    Pypower dummy gens are RAW: from-gen Pg = -p_in, to-gen Pg = +p_out
#    (EX7b sign gotcha). Decode with the gen_bus guard.
# ---------------------------------------------------------------------------
ps_path = RES / "ex13_pstar_smooth.json"
emit("## head-to-head: C*_smooth (cvxopf) vs P*_smooth (neutralized Pypower)")
if not ps_path.exists():
    emit(f"(!) {ps_path.name} not found -- run _ex13_smoothcost_pypower.py first for")
    emit("    the Pypower side. Reporting cvxopf side only.")
    emit(f"C*_smooth Pg    = {np.round(Pg_cvx, 4).tolist()}")
    emit(f"C*_smooth p_in  = {np.round(pin_cvx, 4).tolist()}")
    emit(f"C*_smooth p_out = {np.round(pout_cvx, 4).tolist()}")
else:
    ps = json.loads(ps_path.read_text())
    Pg_p = np.asarray(ps["real_Pg"])
    # decode Pypower p_in/p_out from raw dummy Pg (from-gen -Pg, to-gen +Pg)
    pin_p = -np.asarray(ps["from_dummy_Pg"])
    pout_p = np.asarray(ps["to_dummy_Pg"])
    emit(f"P*_smooth obj (Pypower) = {ps['obj']:.4f}   cvxopf obj = {build.prob.value:.4f}")
    emit()
    emit(f"{'':>10} {'Pg0':>9} {'Pg1':>9} {'Pg2':>9} | {'pin0':>7} {'pin1':>7} {'pin2':>7}")
    emit(
        f"{'C*_smooth':>10} {Pg_cvx[0]:>9.3f} {Pg_cvx[1]:>9.3f} {Pg_cvx[2]:>9.3f} | "
        f"{pin_cvx[0]:>7.3f} {pin_cvx[1]:>7.3f} {pin_cvx[2]:>7.3f}"
    )
    emit(
        f"{'P*_smooth':>10} {Pg_p[0]:>9.3f} {Pg_p[1]:>9.3f} {Pg_p[2]:>9.3f} | "
        f"{pin_p[0]:>7.3f} {pin_p[1]:>7.3f} {pin_p[2]:>7.3f}"
    )
    dPg = float(np.linalg.norm(Pg_cvx - Pg_p))
    dpin = float(np.linalg.norm(pin_cvx - pin_p))
    emit(f"||dPg|| = {dPg:.4f} MW    ||dp_in|| = {dpin:.4f} MW")
    emit()
    emit("## reference: PWL baseline divergence (EX12) for contrast")
    emit("  PWL C* Pg [90, 10, 220.16] p_in [1, 2, 10]  vs  P* Pg [90, 106.14, 123.48] p_in [1, 10, 10]")
    emit("  PWL ||dPg|| was 135.6 MW (link1 2 vs 10) -- sharp divergence.")
    emit()
    emit("## VERDICT")
    if dPg < 5.0 and dpin < 2.0:
        emit(f"CONVERGE: smooth-cost paths agree (||dPg||={dPg:.3f}, ||dp_in||={dpin:.3f}),")
        emit("a world apart from the PWL 135.6 MW divergence. => The PWL cost was the")
        emit("tripwire for Pypower's suboptimal P*. Localizes the DNLP-tractability")
        emit("effect to the nonsmooth-cost regime; hypothesis CONFIRMED.")
    else:
        emit(f"DIVERGE: paths still differ under smooth costs (||dPg||={dPg:.3f}).")
        emit("The gap is NOT purely the PWL cost representation; inspect the dispatch.")

(RES / "ex13_cvxopf_smooth.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
