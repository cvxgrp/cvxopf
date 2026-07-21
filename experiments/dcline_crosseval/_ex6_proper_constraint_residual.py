"""EX6-proper: constraint-by-constraint feasibility of C* in neutralized Pypower.

Single environment (main cvxopf env, no pypower). Licensed by the Ybus-agreement
result (ybus_compare.txt, max abs diff 4.4e-16): cvxopf and Pypower build the
identical network Ybus, so we test C* against cvxopf's Ybus and the dcline
constants directly.

Each constraint of the neutralized-Pypower set is checked separately with a
labeled residual, so the output NAMES which constraint C* violates and by how
much. Guardrail first (LOG B.4): component-reconstructed injections are
cross-checked against cvxopf's stored p_net/q_net before anything downstream is
trusted; a sign/units bug is caught there, not silently propagated.

Units: p_in/p_out/Pg/Qg/p_net/q_net all in MW or MVAr (engineering units).
The DC coupling law in MW (derived from Pypower's per-unit userfcn
  (1-L1)*Pgf + Pgt = -L0/baseMVA, with Pgf=-p_in, Pgt=-p_out in MW):
  p_out = -(1-L1)*p_in + L0        [L0 in MW]

Reads results/cstar_full.json. Writes results/ex6_residual.txt.
"""
import warnings
import json
from pathlib import Path

import numpy as np

from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf

HERE = Path(__file__).resolve().parent
RES = HERE / "results"

# --- load C* full dispatch ---
cv = json.loads((RES / "cstar_full.json").read_text())
e2i = {int(k): int(v) for k, v in cv["ext_to_int"].items()}
Vm = np.array(cv["Vm"])
Va = np.deg2rad(np.array(cv["Va_deg"]))
V = Vm * np.exp(1j * Va)
Pg = np.array(cv["Pg"])
Qg = np.array(cv["Qg"])
p_net = np.array(cv["p_net"])
q_net = np.array(cv["q_net"])
p_in = np.array(cv["p_hvdc_in"])
p_out = np.array(cv["p_hvdc_out"])

# --- rebuild cvxopf problem (NO solve) to get network matrices ---
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
# vmin/vmax: not stored in build.data; reindex the case and read the bus table
# (same reindexing _parse_case applies, so rows align with cvxopf internal order)
from cvxopf.network import reindex_case_to_consecutive
VMIN, VMAX = 12, 11
_case_ri, _e2i_ri = reindex_case_to_consecutive(case9_dcline())
assert _e2i_ri == e2i, "reindex ordering mismatch; C5 rows would misalign"
_bus_ri = _case_ri["bus"]
vmin = np.asarray(_bus_ri[:, VMIN], dtype=float)
vmax = np.asarray(_bus_ri[:, VMAX], dtype=float)

# --- dcline constants (in-service rows, in dcline-table order) ---
DC = case["dcline"]
STATUS, PMIN, PMAX, LOSS0, LOSS1 = 2, 9, 10, 15, 16
on = DC[:, STATUS] > 0
dc_on = DC[on, :]
Pmin_dc = dc_on[:, PMIN].astype(float)
Pmax_dc = dc_on[:, PMAX].astype(float)
L0 = dc_on[:, LOSS0].astype(float)     # MW
L1 = dc_on[:, LOSS1].astype(float)     # fraction

lines = []
def emit(s):
    lines.append(s)

emit("# EX6-proper: constraint-by-constraint feasibility of C* in neutralized Pypower")
emit(f"# baseMVA={baseMVA}  nb={len(Vm)}  n_hvdc={len(p_in)}")
emit("")

# --- GUARDRAIL: reconstructed injections vs cvxopf's own p_net/q_net ---
hvdc_p = Ch_from @ p_in + Ch_to @ p_out          # MW, Convention B (both +)
p_recon = Cg @ Pg - Pd + hvdc_p                  # MW
q_recon = Cg @ Qg - Qd                           # MW (DC terminals Q=0)
g_p = float(np.max(np.abs(p_recon - p_net)))
g_q = float(np.max(np.abs(q_recon - q_net)))
emit("## GUARDRAIL (LOG B.4): component reconstruction vs cvxopf p_net/q_net")
emit(f"max|p_recon - p_net| = {g_p:.3e} MW")
emit(f"max|q_recon - q_net| = {g_q:.3e} MVAr")
guardrail_ok = g_p < 1e-6 and g_q < 1e-6
emit(f"GUARDRAIL: {'PASS' if guardrail_ok else 'FAIL -- signs/units wrong, STOP'}")
emit("")
if not guardrail_ok:
    (RES / "ex6_residual.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    raise SystemExit("guardrail failed; do not trust downstream residuals")

# --- C1: nodal REAL balance  Re(V conj(Y V))*baseMVA == p_net ---
S_net = V * np.conj(Ybus @ V) * baseMVA          # MVA
c1 = np.abs(S_net.real - p_net)
emit("## C1 nodal real balance: Re(V conj(Ybus V))*baseMVA vs p_net")
emit(f"max|resid| = {float(c1.max()):.3e} MW  (at bus internal idx {int(c1.argmax())})")
emit("")

# --- C2: nodal REACTIVE balance ---
c2 = np.abs(S_net.imag - q_net)
emit("## C2 nodal reactive balance: Im(V conj(Ybus V))*baseMVA vs q_net")
emit(f"max|resid| = {float(c2.max()):.3e} MVAr  (at bus internal idx {int(c2.argmax())})")
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

# --- C7: DC coupling law (Pypower, WITH loss0)  p_out = -(1-L1)*p_in + L0 ---
p_out_required = -(1.0 - L1) * p_in + L0
c7 = p_out - p_out_required
emit("## C7 DC coupling law (Pypower, WITH loss0):  p_out = -(1-L1)*p_in + L0 [MW]")
emit(f"p_out (C*)      = {np.round(p_out,4).tolist()}")
emit(f"p_out required  = {np.round(p_out_required,4).tolist()}")
emit(f"residual (C* - required) per link = {np.round(c7,4).tolist()}")
emit(f"max|resid| = {float(np.max(np.abs(c7))):.4f} MW")
emit("")

emit("## VERDICT")
emit("Expected: C1/C2 ~0 (Ybus agrees + cvxopf self-consistent); C3-C6 satisfied;")
emit("C7 nonzero ONLY on link0 by ~loss0=1 MW (the documented dropped term).")
emit("If that holds: C* is feasible in neutralized Pypower EXCEPT the loss0 term")
emit("on link0 -- a ~1 MW injection difference, NOT a 760-unit-objective driver.")
emit("=> the loss0 drop does NOT explain the dispatch/objective gap; the gap must")
emit("   be different local optima of the (near-)same problem. Proceed to EX7.")

(RES / "ex6_residual.txt").write_text("\n".join(lines) + "\n")
print("\n".join(lines))