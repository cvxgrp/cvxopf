# /// script
# requires-python = ">=3.10"
# dependencies = ["pypower==5.1.19", "numpy==2.2.6", "scipy==1.18.0"]
# ///
"""EX10: REAL Pypower warm-start via the pips x0 hook -- does Pypower hold C*?

EX8 was void: runopf discards the case-table seed (pipsopf_solver builds
x0=(ll+uu)/2 then flat-starts Va, so bus/gen dispatch never reaches x0). The
real hook is to intercept x0 at the pips() call and overwrite the variable
blocks. Layout measured in _ex10_probe_x0layout.py (pinned pypower 5.1.19):

  x0 (len 38): Va[0:9] Vm[9:18] Pg[18:27] Qg[27:36] y[36:38]

We seed Va/Vm/Pg/Qg (Pg/Qg include the 6 dcline dummy-gen rows) and leave the
PWL y-block at pypower's default (it is a cost-epigraph slack pips settles).

Two arms into the SAME neutralized-Pypower solve:
  P-arm CONTROL:     seed P* (Pypower's own optimum) -> must converge in ~0-1
                     pips iters. This is the non-circular proof the hook TOOK
                     (cold start always takes 24 iters; EX8's seed left it at
                     24 -> void). If P* does not exit fast, the hook failed.
  C-arm DECISIVE:    seed C* (cvxopf's optimum) -> does Pypower HOLD C* (stays,
                     few iters, obj ~5490) or LEAVE it (drifts to P*, obj ~6250)?

  HOLDS  -> both solvers agree C* is a valid optimum; only the cold-start basin
            differs -> points at formulation/canonicalization as the
            differentiator (see memory dnlp-canonicalization-tractability).
  LEAVES -> C* may be a cvxopf-formulation artifact; thesis complicated.

Guard: assert len(x0)==38 before seeding -> loud failure if pypower internals
drift, never a silent no-op (the EX8 failure mode).

Known floor: C* is infeasible in Pypower by the 1 MW loss0 on link0, so the
C-arm starts slightly infeasible; a small restoration drift is expected
regardless of basin. Reported, not hidden. The DECISIVE signal is the objective
it converges TO (5490 vs 6250) and the iter count, not the raw drift.

Sandbox script. Run: uv run _ex10_warmstart_pips.py
Writes results/ex10_warmstart_pips.txt.
"""
import importlib.util
import inspect
import json
from pathlib import Path

import numpy as np
import pypower.pipsopf_solver as ppsolver

_here = Path(__file__).resolve().parent
RES = _here / "results"

_spec = importlib.util.spec_from_file_location("ex", _here / "_ex_crosseval.py")
ex1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex1)

# confirmed layout (EX10 probe, pinned pypower 5.1.19)
EXPECTED_NX = 38
VA_SL = slice(0, 9)
VM_SL = slice(9, 18)
PG_SL = slice(18, 27)
QG_SL = slice(27, 36)

P_STAR_OBJ = 6249.8659
C_STAR_OBJ = 5490.1038

_orig_pips = ppsolver.pips
_seed = {}          # set per-arm before solving
_iters = {}         # captured per-arm


def _seeding_pips(f_fcn, x0, A, l, u, xmin, xmax, gh_fcn, hess_fcn, opt):
    assert len(x0) == EXPECTED_NX, (
        f"x0 len {len(x0)} != {EXPECTED_NX}; pypower layout drifted, refusing "
        f"to seed blindly"
    )
    # ext2int sorts gens by bus, so pips-internal gen order != our append order
    # [3 real, 3 from, 3 to]. Read the gen i2e map off the solver frame and
    # permute Pg/Qg into internal order. Bus order is identity here -> Va/Vm
    # need no perm (asserted, not assumed).
    om = inspect.currentframe().f_back.f_locals["om"]
    gen_i2e = om.ppc["order"]["gen"]["i2e"].astype(int)
    bus_i2e = om.ppc["order"]["bus"]["i2e"].astype(int)
    assert list(bus_i2e) == [1, 2, 30, 4, 5, 6, 7, 8, 9], (
        f"bus order not identity: {bus_i2e.tolist()}; Va/Vm would need permuting"
    )
    x0 = x0.copy()
    x0[VA_SL] = _seed["va_rad"]
    x0[VM_SL] = _seed["vm"]
    x0[PG_SL] = _seed["pg_pu"][gen_i2e]
    x0[QG_SL] = _seed["qg_pu"][gen_i2e]
    # y[36:38] left at pypower default
    sol = _orig_pips(f_fcn, x0, A, l, u, xmin, xmax, gh_fcn, hess_fcn, opt)
    _iters["n"] = int(sol["output"]["iterations"])
    # objective trajectory: hist[k]['obj'] per iter; hist[0] = iter-0 (at seed).
    # iter-0 obj near the seed's own objective => the seed's primal landed.
    hist = sol["output"].get("hist", [])
    _iters["obj0"] = float(hist[0]["obj"]) if hist else float("nan")
    _iters["traj"] = [round(float(h["obj"]), 2) for h in hist]
    return sol


def run_arm(va_deg, vm, pg_mw, qg_mw, baseMVA=100.0):
    _seed.clear(); _iters.clear()
    _seed["va_rad"] = np.deg2rad(np.asarray(va_deg, float))
    _seed["vm"] = np.asarray(vm, float)
    _seed["pg_pu"] = np.asarray(pg_mw, float) / baseMVA
    _seed["qg_pu"] = np.asarray(qg_mw, float) / baseMVA
    ppsolver.pips = _seeding_pips
    try:
        res, _ = ex1.solve_neutralized()
    finally:
        ppsolver.pips = _orig_pips
    from pypower.idx_gen import PG
    from pypower.idx_bus import BUS_I
    return {
        "success": bool(res["success"]),
        "obj": float(res["f"]),
        "iters": _iters.get("n", -1),
        "obj0": _iters.get("obj0", float("nan")),
        "traj": _iters.get("traj", []),
        "dummy_pg": np.round(res["gen"][3:, PG], 4).tolist(),
        "real_pg": np.round(res["gen"][:3, PG], 4).tolist(),
        "bus_ids": res["bus"][:, BUS_I].astype(int).tolist(),
    }


# --- seeds ---
pv = json.loads((RES / "pstar_full.json").read_text())
cv = json.loads((RES / "cstar_full.json").read_text())

# P* real+dummy gens: real_Pg/Qg then from/to dummy Pg (dummy Q=0). Order matches
# ppc gen table [3 real, 3 from, 3 to] (verified EX7a/EX8).
P_pg = np.concatenate([pv["real_Pg"], pv["from_dummy_Pg"], pv["to_dummy_Pg"]])
P_qg = np.concatenate([pv["real_Qg"], pv["dummy_Qg"]])
# C* dummy gens: reverse-decode Pgf=-p_in, Pgt=-p_out; dummy Q=0.
c_pin = np.array(cv["p_hvdc_in"]); c_pout = np.array(cv["p_hvdc_out"])
C_pg = np.concatenate([cv["Pg"], -c_pin, -c_pout])
C_qg = np.concatenate([cv["Qg"], np.zeros(2 * len(c_pin))])

lines = []
def emit(s):
    lines.append(s)

emit("# EX10: real Pypower warm-start via pips x0 hook")
emit(f"# cold start always takes 24 iters; P*_obj={P_STAR_OBJ} C*_obj={C_STAR_OBJ}")
emit("")

# --- P-arm: hook-works control ---
P = run_arm(pv["Va_deg"], pv["Vm"], P_pg, P_qg)
emit("## P-arm CONTROL (seed P*, expect ~0-1 iters if hook took)")
emit(f"success={P['success']}  obj={P['obj']:.4f}  pips_iters={P['iters']}")
emit(f"iter-0 obj={P['obj0']:.2f}  (near P*={P_STAR_OBJ}? => seed primal landed)")
emit(f"obj trajectory={P['traj']}")
emit(f"real Pg={P['real_pg']}  dummy Pg={P['dummy_pg']}")
emit(f"|obj-P*|={abs(P['obj']-P_STAR_OBJ):.3e}")
hook_ok = P["iters"] >= 0 and P["iters"] < 24
emit(f"HOOK TOOK: {hook_ok} (iters {P['iters']} < 24 cold)")
emit("")

# --- C-arm: decisive ---
C = run_arm(cv["Va_deg"], cv["Vm"], C_pg, C_qg)
emit("## C-arm DECISIVE (seed C*: does Pypower HOLD it?)")
emit(f"success={C['success']}  obj={C['obj']:.4f}  pips_iters={C['iters']}")
emit(f"iter-0 obj={C['obj0']:.2f}  (near C*={C_STAR_OBJ}? => seed primal landed)")
emit(f"obj trajectory={C['traj']}")
emit(f"real Pg={C['real_pg']}  dummy Pg={C['dummy_pg']}")
emit(f"|obj-C*|={abs(C['obj']-C_STAR_OBJ):.3e}  |obj-P*|={abs(C['obj']-P_STAR_OBJ):.3e}")
emit("")

# --- verdict ---
emit("## VERDICT")
if not hook_ok:
    emit("HOOK FAILED -- P* did not converge fast; do not interpret the C-arm.")
else:
    holds = abs(C["obj"] - C_STAR_OBJ) < abs(C["obj"] - P_STAR_OBJ)
    if holds:
        emit(f"Pypower HOLDS C* (converged to {C['obj']:.2f} ~ C*={C_STAR_OBJ}).")
        emit("Both solvers agree C* is a valid optimum; only the cold-start basin")
        emit("differs -> supports formulation/canonicalization as the differentiator.")
    else:
        emit(f"Pypower LEAVES C* (converged to {C['obj']:.2f} ~ P*={P_STAR_OBJ}).")
        emit("C* is not a Pypower optimum -> may be a cvxopf-formulation artifact;")
        emit("canonicalization thesis complicated.")

(RES / "ex10_warmstart_pips.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
