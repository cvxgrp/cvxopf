"""EX7b: constraint-by-constraint feasibility of P* in cvxopf's constraint set.

Symmetric mirror of _ex6_proper_constraint_residual.py. EX6 showed C* (cvxopf's
optimum) is feasible in the neutralized-Pypower set EXCEPT the loss0 term on
link0. EX7b closes the loop from the other side: take P* (the neutralized-Pypower
optimum, results/pstar_full.json) and check it against cvxopf's constraint set.

Expected result (symmetric to EX6): P* satisfies every cvxopf constraint to
machine precision EXCEPT cvxopf's DC coupling law on link0, off by exactly
loss0=1 MW -- because cvxopf DROPS loss0 (p_out = -(1-L1)*p_in, no +L0) while
P* was solved WITH it. The residual sign is opposite to EX6's.

Single environment (main cvxopf env, no live pypower). Licensed by the
Ybus-agreement result; additionally we double-check cvxopf's Ybus against P*'s
OWN recorded Pypower Ybus (pstar_full.json) so the network side is self-contained.

Decode notes (unique to EX7b):
- P* stores DC terminals as dummy generators, not p_in/p_out. In Pypower's
  dcline userfcn the from-gen injects Pgf=-p_in and the to-gen injects
  Pgt=-p_out. So p_in = -from_dummy_Pg, p_out = -to_dummy_Pg. The from/to
  split is VERIFIED against gen_bus (from-buses vs to-buses), not trusted.
- P*'s bus rows are in Pypower external order; we map them into cvxopf's
  internal order via cvxopf's ext_to_int. (They happen to coincide here, but
  we assert rather than assume.)

Units: p_in/p_out/Pg/Qg/p_net/q_net all in MW or MVAr (engineering units).
cvxopf DC coupling law in MW (loss0 DROPPED):  p_out = -(1-L1)*p_in

Reads results/pstar_full.json. Writes results/ex7b_residual.txt.
"""
import warnings
import json
from pathlib import Path

import numpy as np

from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf
from cvxopf.network import reindex_case_to_consecutive

HERE = Path(__file__).resolve().parent
RES = HERE / "results"

# --- load P* full dispatch (Pypower external-id order) ---
pv = json.loads((RES / "pstar_full.json").read_text())
bus_ids = np.array(pv["bus_ids"], dtype=int)          # external ids
Vm_ext = np.array(pv["Vm"])
Va_ext = np.deg2rad(np.array(pv["Va_deg"]))
real_Pg = np.array(pv["real_Pg"])
real_Qg = np.array(pv["real_Qg"])
from_dummy_Pg = np.array(pv["from_dummy_Pg"])
to_dummy_Pg = np.array(pv["to_dummy_Pg"])
gen_bus = np.array(pv["gen_bus"], dtype=int)
ndc = int(pv["ndc"])

# --- rebuild cvxopf problem (NO solve) to get network matrices + ext_to_int ---
case = case9_dcline()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    links = hvdc_from_dcline(case["dcline"])
    b = build_opf(case, formulation="ac", hvdc=links)
d = b.data
baseMVA = d["baseMVA"]
Ybus = np.asarray(d["Ybus"])
Cg = np.asarray(d["Cg"])
Ch_from = np.asarray(d["Ch_from"])
Ch_to = np.asarray(d["Ch_to"])
Pd = np.asarray(d["Pd"]) * baseMVA   # d["Pd"] is per-unit; back to MW
Qd = np.asarray(d["Qd"]) * baseMVA
Pgmin = np.asarray(d["Pgmin"]) * baseMVA
Pgmax = np.asarray(d["Pgmax"]) * baseMVA
Qgmin = np.asarray(d["Qgmin"]) * baseMVA
Qgmax = np.asarray(d["Qgmax"]) * baseMVA
e2i = {int(k): int(v) for k, v in d["ext_to_int"].items()}

# vmin/vmax from the reindexed bus table (same reindexing _parse_case applies)
VMIN, VMAX = 12, 11
_case_ri, _e2i_ri = reindex_case_to_consecutive(case9_dcline())
assert _e2i_ri == e2i, "reindex ordering mismatch; C5 rows would misalign"
_bus_ri = _case_ri["bus"]
vmin = np.asarray(_bus_ri[:, VMIN], dtype=float)
vmax = np.asarray(_bus_ri[:, VMAX], dtype=float)

lines = []
def emit(s):
    lines.append(s)

emit("# EX7b: constraint-by-constraint feasibility of P* in cvxopf's set")
emit(f"# baseMVA={baseMVA}  nb={len(Vm_ext)}  n_hvdc={ndc}  P*_obj={pv['obj']:.4f}")
emit("")

# --- map P* bus rows (external order) into cvxopf internal order ---
nb = len(Vm_ext)
perm = np.array([e2i[int(bid)] for bid in bus_ids])   # internal idx for each P* row
assert sorted(perm.tolist()) == list(range(nb)), "bus id set mismatch"
Vm = np.empty(nb); Va = np.empty(nb)
Vm[perm] = Vm_ext; Va[perm] = Va_ext
V = Vm * np.exp(1j * Va)
emit("## bus reindex P*(external) -> cvxopf(internal)")
emit(f"P* bus_ids       = {bus_ids.tolist()}")
emit(f"cvxopf ext_to_int= {e2i}")
emit(f"identity permutation? {bool(np.all(perm == np.arange(nb)))}")
emit("")

# --- decode dummy gens -> cvxopf p_in/p_out, VERIFYING the from/to split ---
# dcline in-service rows give (from_bus, to_bus) per link; gen_bus rows 3.. are
# the appended from-gens then to-gens. Verify buses before trusting the slice.
DC = case["dcline"]
F_BUS, T_BUS, STATUS, PMIN, PMAX, LOSS0, LOSS1 = 0, 1, 2, 9, 10, 15, 16
on = DC[:, STATUS] > 0
dc_on = DC[on, :]
from_bus_dc = dc_on[:, F_BUS].astype(int)
to_bus_dc = dc_on[:, T_BUS].astype(int)
n_real = len(real_Pg)
from_gen_bus = gen_bus[n_real : n_real + ndc]
to_gen_bus = gen_bus[n_real + ndc : n_real + 2 * ndc]
assert np.array_equal(from_gen_bus, from_bus_dc), (
    f"from-gen bus {from_gen_bus} != dcline from {from_bus_dc}"
)
assert np.array_equal(to_gen_bus, to_bus_dc), (
    f"to-gen bus {to_gen_bus} != dcline to {to_bus_dc}"
)
# Pypower userfcn: Pgf = -p_in, Pgt = -p_out  =>  p_in = -Pgf, p_out = -Pgt
p_in = -from_dummy_Pg
p_out = -to_dummy_Pg
emit("## dummy-gen decode (VERIFIED via gen_bus)")
emit(f"from_gen_bus={from_gen_bus.tolist()} == dcline from {from_bus_dc.tolist()}")
emit(f"to_gen_bus  ={to_gen_bus.tolist()} == dcline to   {to_bus_dc.tolist()}")
emit(f"p_in  = -from_dummy_Pg = {np.round(p_in,4).tolist()}")
emit(f"p_out = -to_dummy_Pg   = {np.round(p_out,4).tolist()}")
emit("")

Pg = real_Pg
Qg = real_Qg

# --- YBUS DOUBLE-CHECK: cvxopf Ybus vs P*'s recorded Pypower Ybus ---
# P*'s Ybus is on Pypower internal order with map pypower_Ybus_i2e; reindex it
# into cvxopf internal order and compare.
Ypp = np.array(pv["pypower_Ybus_real"]) + 1j * np.array(pv["pypower_Ybus_imag"])
pp_i2e = np.array(pv["pypower_Ybus_i2e"], dtype=int)   # pp internal idx -> ext id
# build permutation pp_internal -> cvxopf_internal
pp_to_cv = np.array([e2i[int(pp_i2e[i])] for i in range(nb)])
Ypp_cv = np.empty((nb, nb), dtype=complex)
Ypp_cv[np.ix_(pp_to_cv, pp_to_cv)] = Ypp
ybus_diff = float(np.max(np.abs(Ypp_cv - Ybus)))
emit("## YBUS double-check: cvxopf Ybus vs P*'s recorded Pypower Ybus")
emit(f"max|Ypp(reindexed) - Ybus_cvxopf| = {ybus_diff:.3e}")
emit(f"YBUS: {'AGREE' if ybus_diff < 1e-9 else 'DISAGREE -- network mismatch, STOP'}")
emit("")

# --- GUARDRAIL: gen-side reconstruction vs Ybus-side injections ---
# P* has no independent p_net/q_net witness (unlike C*), so the guardrail is
# gen-side vs Ybus-side self-consistency at P*'s own operating point.
# IMPORTANT sign convention: in P* the DC terminals are DUMMY GENERATORS whose
# raw injected power is Pgf=-p_in (from-bus) and Pgt=-p_out (to-bus). The nodal
# balance therefore adds the RAW dummy Pg (= -p_in, -p_out), NOT cvxopf's
# Convention-B grid injection (+p_in, +p_out). Using +p_in/+p_out here flips the
# sign and manufactures a spurious 2x residual at the terminal buses.
hvdc_p = Ch_from @ from_dummy_Pg + Ch_to @ to_dummy_Pg   # MW (raw dummy Pg = -p_in, -p_out)
p_gen_side = Cg @ Pg - Pd + hvdc_p               # MW
q_gen_side = Cg @ Qg - Qd                         # MVAr (DC terminals Q=0)
S_net = V * np.conj(Ybus @ V) * baseMVA          # MVA (Ybus-side)
g_p = float(np.max(np.abs(p_gen_side - S_net.real)))
g_q = float(np.max(np.abs(q_gen_side - S_net.imag)))
emit("## GUARDRAIL: gen-side injection vs Ybus-side Re/Im(V conj(Ybus V))")
emit(f"max|p_gen_side - Ybus_p| = {g_p:.3e} MW  (at internal idx {int(np.argmax(np.abs(p_gen_side - S_net.real)))})")
emit(f"max|q_gen_side - Ybus_q| = {g_q:.3e} MVAr")
# Using the RAW dummy Pg injections (= -p_in, -p_out), P*'s own nodal balance
# closes to machine precision -- Pypower's dummy gens already satisfy their
# coupling law WITH loss0, so the balance is exact. loss0 does NOT surface here;
# it surfaces in C7 as the difference between P*'s p_out and cvxopf's
# loss0-dropped law. Expect this guardrail ~1e-9.
guardrail_ok = g_p < 1e-6 and g_q < 1e-6
emit(f"GUARDRAIL: {'PASS' if guardrail_ok else 'FAIL -- signs/units wrong, STOP'}")
emit("")

# --- dcline constants ---
Pmin_dc = dc_on[:, PMIN].astype(float)
Pmax_dc = dc_on[:, PMAX].astype(float)
L0 = dc_on[:, LOSS0].astype(float)     # MW
L1 = dc_on[:, LOSS1].astype(float)     # fraction

# --- C1: nodal REAL balance (cvxopf side): gen-side p vs Ybus-side ---
# In cvxopf, nodal balance is p_gen_side == Ybus_p. Residual = difference.
c1 = np.abs(p_gen_side - S_net.real)
emit("## C1 nodal real balance (cvxopf): gen-side p vs Re(V conj(Ybus V))")
emit(f"max|resid| = {float(c1.max()):.3e} MW  (at internal idx {int(c1.argmax())})")
emit(f"per-bus resid = {np.round(c1,4).tolist()}")
emit("")

# --- C2: nodal REACTIVE balance ---
c2 = np.abs(q_gen_side - S_net.imag)
emit("## C2 nodal reactive balance (cvxopf): gen-side q vs Im(V conj(Ybus V))")
emit(f"max|resid| = {float(c2.max()):.3e} MVAr  (at internal idx {int(c2.argmax())})")
emit("")

# --- C3: gen P bounds ---
c3_lo = Pgmin - Pg
c3_hi = Pg - Pgmax
emit("## C3 gen P bounds  Pgmin <= Pg <= Pgmax  (MW)")
emit(f"max lower viol = {float(np.max(c3_lo)):.3e}  max upper viol = {float(np.max(c3_hi)):.3e}")
emit(f"Pg = {np.round(Pg,4).tolist()}")
emit("")

# --- C4: gen Q bounds ---
c4_lo = Qgmin - Qg
c4_hi = Qg - Qgmax
emit("## C4 gen Q bounds  Qgmin <= Qg <= Qgmax  (MVAr)")
emit(f"max lower viol = {float(np.max(c4_lo)):.3e}  max upper viol = {float(np.max(c4_hi)):.3e}")
emit(f"Qg = {np.round(Qg,4).tolist()}")
emit("")

# --- C5: voltage bounds ---
c5_lo = vmin - Vm
c5_hi = Vm - vmax
emit("## C5 voltage bounds  vmin <= Vm <= vmax")
emit(f"max lower viol = {float(np.nanmax(c5_lo)):.3e}  max upper viol = {float(np.nanmax(c5_hi)):.3e}")
emit("")

# --- C6: DC box  Pmin <= p_in <= Pmax ---
c6_lo = Pmin_dc - p_in
c6_hi = p_in - Pmax_dc
emit("## C6 DC box  Pmin <= p_in <= Pmax  (MW)")
emit(f"p_in = {np.round(p_in,4).tolist()}")
emit(f"Pmin = {Pmin_dc.tolist()}  Pmax = {Pmax_dc.tolist()}")
emit(f"max lower viol = {float(np.max(c6_lo)):.3e}  max upper viol = {float(np.max(c6_hi)):.3e}")
emit("")

# --- C7: cvxopf DC coupling law (loss0 DROPPED)  p_out = -(1-L1)*p_in ---
p_out_required = -(1.0 - L1) * p_in
c7 = p_out - p_out_required
emit("## C7 cvxopf DC coupling law (loss0 DROPPED):  p_out = -(1-L1)*p_in [MW]")
emit(f"p_out (P*)      = {np.round(p_out,4).tolist()}")
emit(f"p_out required  = {np.round(p_out_required,4).tolist()}")
emit(f"residual (P* - required) per link = {np.round(c7,4).tolist()}")
emit(f"loss0 per link  = {L0.tolist()}  (dropped by cvxopf)")
emit(f"max|resid| = {float(np.max(np.abs(c7))):.4f} MW")
emit("")

emit("## VERDICT")
emit("Expected (symmetric to EX6): C1/C2 ~0 to machine precision (P*'s dummy")
emit("gens satisfy their own coupling law WITH loss0, so nodal balance closes);")
emit("C3-C6 satisfied; C7 nonzero ONLY on link0 by ~loss0=1 MW, sign OPPOSITE")
emit("to EX6 (P* runs p_out=+0.01 where cvxopf's loss0-dropped law wants -0.99).")
emit("If that holds: P* is feasible in cvxopf's set EXCEPT the loss0 term on")
emit("link0 -- confirming from BOTH sides that C* and P* are mutually near-")
emit("feasible, i.e. different local optima of the (near-)same nonconvex AC-OPF.")

(RES / "ex7b_residual.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
