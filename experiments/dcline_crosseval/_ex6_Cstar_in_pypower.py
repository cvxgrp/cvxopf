# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""EX6: is C* (cvxopf's optimum) FEASIBLE in Pypower's neutralized constraint
set? Build the neutralized Pypower model, place C*'s (Vm, Va, Pg, DC flows)
into it, and evaluate the AC power-balance residual at every bus using
Pypower's OWN Ybus (via makeYbus). Report per-bus P and Q residual (MW/MVAr).

Feasible (residual ~0 everywhere) -> C* is in Pypower's set -> divergence is
local optima. Residual spikes -> constraint-set difference; the bus location
says which constraint.

Reads C* from results/cstar.json. Writes results/ex6_Cstar_in_pypower.txt.
Run: uv run experiments/dcline_crosseval/_ex6_Cstar_in_pypower.py
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
from pypower.idx_dcline import c
from pypower.idx_brch import RATE_A
from pypower.idx_bus import BUS_TYPE, BUS_I, PQ, VM, VA, PD, QD
from pypower.idx_gen import GEN_BUS, PG, QG, QMIN, QMAX
from pypower.t.t_case9_dcline import t_case9_dcline
from pypower.add_userfcn import add_userfcn
from pypower.makeYbus import makeYbus
from pypower.ext2int import ext2int

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RES = HERE / "results"
_spec = importlib.util.spec_from_file_location(
    "genfix", REPO / "scripts" / "generate_pypower_fixtures.py"
)
gf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gf)

cstar = json.load(open(RES / "cstar.json"))
C_Pg = np.array(cstar["Pg"])          # real gens, buses [1,2,30]
C_Vm = np.array(cstar["Vm"])          # internal order
C_Va = np.deg2rad(np.array(cstar["Va_deg"]))
C_pin = np.array(cstar["p_hvdc_in"])  # PF
C_pout = np.array(cstar["p_hvdc_out"])  # -PT
e2i = {int(k): v for k, v in cstar["ext_to_int"].items()}

# Build neutralized model (branches off, dummy Q=0, terminals PQ), same as EX1.
orig = t_case9_dcline()
if "dclinecost" in orig:
    del orig["dclinecost"]
orig["branch"][:, RATE_A] = 1e5
ppc = gf._dcline_to_gens(orig)
on = orig["dcline"][:, c["BR_STATUS"]] > 0
ndc = int(on.sum())
ppc["gen"][-2 * ndc:, QMIN] = 0.0
ppc["gen"][-2 * ndc:, QMAX] = 0.0
term = set(orig["dcline"][on, c["F_BUS"]].astype(int)) | \
       set(orig["dcline"][on, c["T_BUS"]].astype(int))
idr = {int(b): i for i, b in enumerate(ppc["bus"][:, BUS_I])}
for bid in term:
    row = idr[bid]
    if ppc["bus"][row, BUS_TYPE] != 3:
        ppc["bus"][row, BUS_TYPE] = PQ

# Convert to internal indexing so makeYbus + bus order line up.
ppci = ext2int(ppc)
baseMVA = ppci["baseMVA"]
Ybus, _, _ = makeYbus(baseMVA, ppci["bus"], ppci["branch"])
Ybus = np.asarray(Ybus.todense())
nb = ppci["bus"].shape[0]

# Map C* voltages into ppci internal bus order via external id.
# ppci bus order: use its BUS_I (already consecutive internal ids 0..nb-1),
# and ppci['order']['bus']['i2e'] to get external id per internal row.
i2e_pp = ppci["order"]["bus"]["i2e"]
V = np.zeros(nb, dtype=complex)
for row in range(nb):
    ext = int(i2e_pp[row])
    ci = e2i[ext]            # cvxopf internal index for that external bus
    V[row] = C_Vm[ci] * np.exp(1j * C_Va[ci])

# Scheduled injection at each bus (per unit) under C*: gens - loads.
# Real gens: place C_Pg by GEN_BUS; dummy gens: from C* DC flows.
Sbus_sched = np.zeros(nb, dtype=complex)
# loads
Sbus_sched -= (ppci["bus"][:, PD] + 1j * ppci["bus"][:, QD]) / baseMVA
# gens (ppci gen already internal; first 3 real, last 6 dummy in append order)
gen = ppci["gen"]
ng_total = gen.shape[0]
# real gens: match by bus to C_Pg (buses 1,2,30 -> C index 0,1,2)
real_bus_to_c = {e2i[b]: k for k, b in enumerate([1, 2, 30])}
# Build per-gen (P,Q) in pu for C*: real gens from C_Pg (Q unknown -> use 0? No:
# feasibility of REAL power balance is the key; Q at real gens is free in OPF,
# so we test P-balance strictly and report Q residual as 'reactive demand'.)
# For a clean test: set real-gen Q to whatever C* Q is -- but cvxopf Qg not in
# cstar. So evaluate P-balance strictly; Q-residual = reactive that WOULD be
# needed (informational).
Pg_pu = np.zeros(ng_total); Qg_pu = np.zeros(ng_total)
for gi in range(ng_total):
    gb = int(gen[gi, GEN_BUS])
    ext = int(i2e_pp[gb])
    if gi < 3:
        # real gen: find which C index (by external bus)
        # gen order after ext2int may reorder; match by external bus id
        cidx = {1: 0, 2: 1, 30: 2}.get(ext, None)
        if cidx is not None:
            Pg_pu[gi] = C_Pg[cidx] / baseMVA
    else:
        pass  # dummy gens handled below by DC flow injection
# DC dummy injections: from-dummy Pg = -PF = -p_in ; to-dummy Pg = -p_out.
# Identify dummy gens by being the last 2*ndc rows of the ORIGINAL append,
# but ext2int reorders gens; instead inject at terminal buses directly.
for k, (fb, tb) in enumerate([(30, 4), (7, 9), (5, 9)]):
    # from-terminal injection into grid = -PF (withdrawal) = -C_pin[k]
    fb_int = [r for r in range(nb) if int(i2e_pp[r]) == fb][0]
    tb_int = [r for r in range(nb) if int(i2e_pp[r]) == tb][0]
    Sbus_sched[fb_int] += (-C_pin[k]) / baseMVA
    Sbus_sched[tb_int] += (C_pout[k] * -1.0) / baseMVA  # to-dummy Pg = -p_out
# add real gen injections
for gi in range(ng_total):
    gb = int(gen[gi, GEN_BUS])
    Sbus_sched[gb] += Pg_pu[gi] + 1j * Qg_pu[gi]

# Actual injection implied by voltages via Pypower Ybus.
Sbus_actual = V * np.conj(Ybus @ V)

P_res = (Sbus_actual.real - Sbus_sched.real) * baseMVA
Q_res = (Sbus_actual.imag - Sbus_sched.imag) * baseMVA

lines = ["# EX6: C* feasibility in neutralized Pypower constraint set",
         "# P_res = Ybus-implied P minus scheduled P (MW); Q_res likewise (MVAr)",
         "# (real-gen Q left free -> Q_res is informational, not a violation)",
         "bus_ext   P_res(MW)   Q_res(MVAr)"]
for row in range(nb):
    ext = int(i2e_pp[row])
    lines.append(f"  {ext:>3}     {P_res[row]:10.4f}   {Q_res[row]:10.4f}")
lines.append(f"max |P_res| = {np.abs(P_res).max():.4f} MW")
lines.append(f"max |Q_res| = {np.abs(Q_res).max():.4f} MVAr")
lines.append("")
lines.append("INTERPRETATION: if max|P_res| ~0, C*'s real-power balance is")
lines.append("feasible in Pypower -> divergence is local optima. If P_res spikes")
lines.append("at specific buses, that locates the constraint-set difference.")
text = "\n".join(lines) + "\n"
(RES / "ex6_Cstar_in_pypower.txt").write_text(text)
print(text)
