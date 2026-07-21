# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""EX8: two-arm warm-start control test in neutralized Pypower.

============================================================================
!!! VOID / NEGATIVE RESULT -- DO NOT INTERPRET THIS SCRIPT'S OUTPUT AS A
!!! BASIN FINDING. KEPT DELIBERATELY AS DOCUMENTED PROOF OF A DEAD END.
============================================================================
This experiment does NOT work, and the reason IS the finding. pypower's runopf
DISCARDS the case-table warm-start seed (bus VM/VA + gen PG/QG) and cold-inits
its own x0 -- that seeding pattern is a `runpf` (power-flow) idiom, not an
`runopf` (OPF) one. PROOF: cold-start and seed-at-optimum produce a
BYTE-IDENTICAL 24-iteration IPOPT history, both starting from obj 19271.925
(see `_ex8_probe_iters.py`). So both "arms" below are just the same cold solve
landing on P*; the P-arm's large "drift" and the P+ arm's zero "drift" are
artifacts of (output - seed), measuring NOTHING about basins.

The real warm-start experiment moved to the cvxopf/CVXPY side, where
variable.value IS passed to IPOPT as x0 (see `_ex9_warmstart_cvxopf.py`,
EX9_REPORT.md). A working Pypower-side warm-start needs the pips/opf_setup x0
hook, not this pattern -- that is the next experiment.

Why keep a script that doesn't work: negative results are first-class evidence.
This file is the reproducible artifact behind "the runopf case-table warm-start
silently no-ops," so the dead end is not re-derived later. See EX9_REPORT.md and
the memory case9-dcline-optima-gap (EX8 VOID note).
============================================================================

[Original design, retained for context:]
Seeds BOTH P* (neutralized-Pypower optimum) and C* (cvxopf optimum) as starting
points into the SAME neutralized-Pypower solve, gets P+ and C+, and compares the
two arms symmetrically:

  variable drift:  ||P* - P+||  vs  ||C* - C+||   (per block + stacked)
  value drift:     |p* - p+|    vs  |c* - c+|     (objective)

Control logic (probe already PASSED, exact-zero drift at P*): the P+ arm is the
trivial control (start at the optimum, expect ~0). The C+ arm is informative --
if C* is a genuine local optimum of the (near-)same problem, C+ ~ C* and the
objective barely moves; if C* just sits in P*'s basin, C+ drifts toward P* and
the objective climbs from ~5490 toward ~6250.

KNOWN FLOOR (loss0 confound): C* is infeasible in Pypower by exactly the 1 MW
loss0 term on link0 (C* p_out[0]=-0.99; Pypower requires -(1-L1)*p_in+L0=+0.01).
So the C+ arm starts slightly infeasible and IPOPT restores feasibility -> some
drift is guaranteed regardless of basin. We REPORT this floor explicitly here; a
follow-up variant seeds C* nudged to satisfy loss0 to separate restoration-drift
from basin-drift.

Seed alignment (validated by the probe): cstar/pstar arrays are already in ppc
pre-solve table order; gens are [3 real, ndc from-dummy, ndc to-dummy]. DC
dummy PG reverse-decode: Pgf=-p_in, Pgt=-p_out.

Sandbox script (isolated pypower env). Run: uv run _ex8_warmstart_twoarm.py
Writes results/ex8_warmstart_twoarm.txt.
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
from pypower.idx_bus import BUS_I, VM, VA
from pypower.idx_gen import PG, QG

_here = Path(__file__).resolve().parent
RES = _here / "results"

_spec = importlib.util.spec_from_file_location(
    "ex_crosseval", _here / "_ex_crosseval.py"
)
ex1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex1)

P_STAR_OBJ = 6249.8659
C_STAR_OBJ = 5490.1038


def _seed_from(real_pg, real_qg, from_dummy_pg, to_dummy_pg, dummy_qg, vm, va):
    return {
        "bus_vm": np.asarray(vm, float),
        "bus_va": np.asarray(va, float),
        "gen_pg": np.concatenate([real_pg, from_dummy_pg, to_dummy_pg]),
        "gen_qg": np.concatenate([real_qg, dummy_qg]),
    }


def _extract(res):
    """Pull the four solved blocks in ppc table order."""
    return {
        "vm": np.array(res["bus"][:, VM]),
        "va": np.array(res["bus"][:, VA]),
        "pg": np.array(res["gen"][:, PG]),
        "qg": np.array(res["gen"][:, QG]),
        "obj": float(res["f"]),
        "ids": res["bus"][:, BUS_I].astype(int).tolist(),
    }


def _drift(seed_blocks, out):
    """Per-block L2 drift between a seed point and a solved point."""
    vm0, va0, pg0, qg0 = seed_blocks
    d = {
        "vm": float(np.linalg.norm(out["vm"] - vm0)),
        "va": float(np.linalg.norm(out["va"] - va0)),
        "pg": float(np.linalg.norm(out["pg"] - pg0)),
        "qg": float(np.linalg.norm(out["qg"] - qg0)),
    }
    # stacked (raw units; per-block reported separately so this isn't misread)
    d["stacked"] = float(np.linalg.norm(
        np.concatenate([out["vm"] - vm0, out["va"] - va0,
                        out["pg"] - pg0, out["qg"] - qg0])
    ))
    return d


# --- load P* and C* ---
pv = json.loads((RES / "pstar_full.json").read_text())
cv = json.loads((RES / "cstar_full.json").read_text())

# P* seed blocks (dummy Pg recorded directly)
p_real_pg = np.array(pv["real_Pg"]); p_real_qg = np.array(pv["real_Qg"])
p_from = np.array(pv["from_dummy_Pg"]); p_to = np.array(pv["to_dummy_Pg"])
p_dqg = np.array(pv["dummy_Qg"])
p_vm = np.array(pv["Vm"]); p_va = np.array(pv["Va_deg"])
seed_P = _seed_from(p_real_pg, p_real_qg, p_from, p_to, p_dqg, p_vm, p_va)

# C* seed blocks: reverse-decode DC dummy PG (Pgf=-p_in, Pgt=-p_out); Q dummy=0
c_real_pg = np.array(cv["Pg"]); c_real_qg = np.array(cv["Qg"])
c_pin = np.array(cv["p_hvdc_in"]); c_pout = np.array(cv["p_hvdc_out"])
c_from = -c_pin; c_to = -c_pout
c_dqg = np.zeros(2 * len(c_pin))
c_vm = np.array(cv["Vm"]); c_va = np.array(cv["Va_deg"])
seed_C = _seed_from(c_real_pg, c_real_qg, c_from, c_to, c_dqg, c_vm, c_va)

lines = []
def emit(s):
    lines.append(s)

emit("# EX8: two-arm warm-start control (neutralized Pypower)")
emit(f"# P*_obj={P_STAR_OBJ}  C*_obj={C_STAR_OBJ}")
emit("")
emit("## DC dispatch at the two seeds (the link1 swing this hinges on)")
emit(f"P* p_in = {np.round(-p_from,4).tolist()}  p_out = {np.round(-p_to,4).tolist()}")
emit(f"C* p_in = {np.round(c_pin,4).tolist()}  p_out = {np.round(c_pout,4).tolist()}")
emit("")

# --- ARM 1: P+ (control) ---
resP, _ = ex1.solve_neutralized(seed=seed_P)
outP = _extract(resP)
dP = _drift((seed_P["bus_vm"], seed_P["bus_va"], seed_P["gen_pg"], seed_P["gen_qg"]), outP)
emit("## ARM 1 -- P+ (control: seed at P*, expect ~0)")
emit(f"success={bool(resP['success'])}  obj={outP['obj']:.4f}  ids_match={outP['ids']==pv['bus_ids']}")
emit(f"drift ||Vm||={dP['vm']:.3e} ||Va||={dP['va']:.3e} ||Pg||={dP['pg']:.3e} ||Qg||={dP['qg']:.3e}")
emit(f"stacked drift = {dP['stacked']:.3e}")
emit(f"|obj+ - P*_obj| = {abs(outP['obj']-P_STAR_OBJ):.3e}")
emit("")

# --- ARM 2: C+ (informative) ---
resC, _ = ex1.solve_neutralized(seed=seed_C)
outC = _extract(resC)
dC = _drift((seed_C["bus_vm"], seed_C["bus_va"], seed_C["gen_pg"], seed_C["gen_qg"]), outC)
emit("## ARM 2 -- C+ (informative: seed at C*)")
# C* ext_to_int keys are external ids in internal-index order -> that IS the
# expected bus id order; compare against it.
c_bus_ids = [int(k) for k, _ in sorted(cv["ext_to_int"].items(), key=lambda kv: kv[1])]
emit(f"success={bool(resC['success'])}  obj={outC['obj']:.4f}  ids_match={outC['ids']==c_bus_ids}")
emit(f"drift ||Vm||={dC['vm']:.3e} ||Va||={dC['va']:.3e} ||Pg||={dC['pg']:.3e} ||Qg||={dC['qg']:.3e}")
emit(f"stacked drift = {dC['stacked']:.3e}")
emit(f"|obj+ - C*_obj| = {abs(outC['obj']-C_STAR_OBJ):.3e}")
emit(f"C+ obj vs P*_obj = {outC['obj']:.4f} vs {P_STAR_OBJ} (did C+ climb toward P*?)")
emit("")

# --- C+ solved DC dispatch (did link1 swing from 2 toward 10?) ---
ndc = len(c_pin)
cplus_from = outC["pg"][3:3+ndc]; cplus_to = outC["pg"][3+ndc:3+2*ndc]
emit("## C+ solved DC dispatch")
emit(f"C+ p_in = {np.round(-cplus_from,4).tolist()}  p_out = {np.round(-cplus_to,4).tolist()}")
emit(f"(C* seed was p_in={np.round(c_pin,4).tolist()})")
emit("")

# --- SYMMETRY COMPARISON ---
emit("## SYMMETRY: is ||P*-P+|| ~ ||C*-C+||  and  |p*-p+| ~ |c*-c+| ?")
emit(f"variable (stacked): P-arm {dP['stacked']:.3e}  vs  C-arm {dC['stacked']:.3e}")
emit(f"objective          : P-arm {abs(outP['obj']-P_STAR_OBJ):.3e}  vs  C-arm {abs(outC['obj']-C_STAR_OBJ):.3e}")
emit("")
emit("## READ (known loss0 floor applies to the C-arm)")
emit("If C-arm drift ~ P-arm drift (both tiny): C* is a genuine local optimum of")
emit("the near-same problem -> different-local-optima verdict holds from the")
emit("warm-start side too. If C-arm drift is LARGE and C+ obj climbs toward")
emit("~6250: C* is NOT a Pypower optimum (sits in P*'s basin) -- would complicate")
emit("the verdict. The loss0 nudge follow-up separates restoration- from")
emit("basin-drift.")

(RES / "ex8_warmstart_twoarm.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
