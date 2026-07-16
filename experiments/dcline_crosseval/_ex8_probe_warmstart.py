# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""EX8 PROBE: does warm-starting the neutralized-Pypower solve actually take?

============================================================================
!!! SUPERSEDED / MISLEADING -- this probe's "PASS" was CIRCULAR. It seeded P*
!!! and got P* back and concluded the warm-start works -- but the seed was
!!! actually IGNORED by runopf; a cold solve also lands on P*, so "P+ ~ P*"
!!! proves nothing. The committing message (ac6e827 "...warm-start seeding
!!! validated") is WRONG. Disproof: `_ex8_probe_iters.py` shows cold and
!!! seed-at-optimum give a BYTE-IDENTICAL 24-iter IPOPT history. Kept as a
!!! documented negative result. See EX9_REPORT.md + memory
!!! case9-dcline-optima-gap (EX8 VOID note).
============================================================================


Gating diagnostic before the real two-arm EX8. Seeds P* (the neutralized-Pypower
optimum) back into ITS OWN problem via the standard pypower warm-start pattern
(bus VM/VA + gen PG/QG written into the case tables before runopf; VG left at
default per EX8 decision), then checks that P+ comes back AT P*.

Logic: starting the solver at the optimum must yield ~zero drift and the same
objective. If it does NOT, the warm-start isn't propagating and the whole EX8
design is invalid -- STOP and rethink. If it does, the two-arm test (P* and C*
seeds -> P+ and C+) is licensed.

Alignment note: pstar_full.json arrays are already in the ppc PRE-SOLVE table
order. _dcline_to_gens appends dummy gens as [3 real, ndc from, ndc to] in case
order (line 306, np.r_[gen, fg, tg]); buses are never reordered. So the seed is
written directly, no reindex. This probe IS the check on that assumption: a
wrong ordering blows up the drift.

Sandbox script (isolated pypower env). Run: uv run _ex8_probe_warmstart.py
Writes results/ex8_probe_warmstart.txt.
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

# --- load P* ---
pv = json.loads((RES / "pstar_full.json").read_text())
real_Pg = np.array(pv["real_Pg"])
real_Qg = np.array(pv["real_Qg"])
from_dummy_Pg = np.array(pv["from_dummy_Pg"])
to_dummy_Pg = np.array(pv["to_dummy_Pg"])
dummy_Qg = np.array(pv["dummy_Qg"])
bus_vm_star = np.array(pv["Vm"])
bus_va_star = np.array(pv["Va_deg"])

# gen seed in ppc table order: [3 real, ndc from, ndc to]
gen_pg_star = np.concatenate([real_Pg, from_dummy_Pg, to_dummy_Pg])
gen_qg_star = np.concatenate([real_Qg, dummy_Qg])

seed = {
    "bus_vm": bus_vm_star,
    "bus_va": bus_va_star,
    "gen_pg": gen_pg_star,
    "gen_qg": gen_qg_star,
}

lines = []
def emit(s):
    lines.append(s)

emit("# EX8 PROBE: warm-start neutralized Pypower at P*, expect P+ ~ P*")
emit(f"# P*_obj target = {P_STAR_OBJ}")
emit("")

# --- cold-start baseline (sanity: reproduce P* obj) ---
res_cold, _ = ex1.solve_neutralized()
emit(f"cold-start success = {bool(res_cold['success'])}  obj = {float(res_cold['f']):.4f}")

# --- warm-start at P* ---
res_warm, _ = ex1.solve_neutralized(seed=seed)
ok = bool(res_warm["success"])
obj_warm = float(res_warm["f"])
emit(f"warm-start success = {ok}  obj = {obj_warm:.4f}")
emit("")

# --- drift P+ vs P* (per block, ppc table order) ---
vm_plus = res_warm["bus"][:, VM]
va_plus = res_warm["bus"][:, VA]
pg_plus = res_warm["gen"][:, PG]
qg_plus = res_warm["gen"][:, QG]

# verify bus row order matches P* (guardrail against a silent reindex)
bus_ids_warm = res_warm["bus"][:, BUS_I].astype(int).tolist()
emit(f"bus id order (warm) = {bus_ids_warm}")
emit(f"bus id order (P*)   = {pv['bus_ids']}")
emit(f"bus order matches P*: {bus_ids_warm == pv['bus_ids']}")
emit("")

d_vm = float(np.linalg.norm(vm_plus - bus_vm_star))
d_va = float(np.linalg.norm(va_plus - bus_va_star))
d_pg = float(np.linalg.norm(pg_plus - gen_pg_star))
d_qg = float(np.linalg.norm(qg_plus - gen_qg_star))
d_obj = abs(obj_warm - P_STAR_OBJ)
emit("## drift P+ vs P* (per block, L2)")
emit(f"||Vm+  - Vm* ||  = {d_vm:.3e}")
emit(f"||Va+  - Va* ||  = {d_va:.3e}  (deg)")
emit(f"||Pg+  - Pg* ||  = {d_pg:.3e}  (MW)")
emit(f"||Qg+  - Qg* ||  = {d_qg:.3e}  (MVAr)")
emit(f"|obj+ - obj*|    = {d_obj:.3e}")
emit("")

# --- verdict ---
small = (d_vm < 1e-3 and d_va < 1e-2 and d_pg < 1e-2 and
         d_qg < 1e-2 and d_obj < 1e-1)
emit("## PROBE VERDICT")
if small and ok:
    emit("PASS: warm-start propagates -- P+ ~ P* (near-zero drift, same obj).")
    emit("The two-arm EX8 (P* and C* seeds) is licensed.")
else:
    emit("FAIL: warm-start did NOT return P* from its own optimum. Either the")
    emit("seed isn't propagating (mechanism) or the row ordering is wrong. STOP")
    emit("and diagnose before building the two-arm test.")

(RES / "ex8_probe_warmstart.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
