# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""EX6-(B): is C* (cvxopf's optimum) feasible in Pypower's network?

Reconstruction-free feasibility test. Instead of hand-building the nodal
injection (which bit us with sign errors 3x), we:
  1. Build the neutralized dcline case (branches off, dummy Q=0, PQ terminals).
  2. Fix generation to C*'s dispatch:
       - real gens: Pg = C*.Pg
       - dummy gens: from-gen Pg = -p_in (= -PF), to-gen Pg = -p_out (= PT)
         (EX3-verified mapping for Pypower's representation)
  3. Run Pypower's OWN power flow (runpf) starting from C*'s voltages, with
     all terminal + gen buses set to PQ so the specified injections are held
     and the voltages are SOLVED. Then compare the solved voltages back to
     C*'s voltages.

If Pypower's power flow, given C*'s injections, reproduces C*'s voltages
(small mismatch, solved Vm/Va ~ C*), then C* is a consistent operating point
in Pypower's network -> feasible -> divergence is local optima.
If it cannot (diverges, or solves to different voltages), the network/constraint
models differ.

NOTE: real-gen reactive is not in C*, so we make real-gen buses PV at C*'s Vm
(voltage held, Q free) -- matching how OPF treats them -- and only the SLACK
absorbs real-power imbalance. A near-zero slack P adjustment => C*'s real-power
dispatch is network-consistent in Pypower.

Reads results/cstar.json. Writes results/ex6b_powerflow.txt.
Run: uv run experiments/dcline_crosseval/_ex6b_powerflow.py
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
from pypower.idx_dcline import c
from pypower.idx_brch import RATE_A
from pypower.idx_bus import BUS_TYPE, BUS_I, PQ, PV, REF, VM, VA
from pypower.idx_gen import GEN_BUS, PG, QG, VG, QMIN, QMAX
from pypower.t.t_case9_dcline import t_case9_dcline
from pypower.add_userfcn import add_userfcn
from pypower.api import runpf, ppoption

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RES = HERE / "results"
_spec = importlib.util.spec_from_file_location(
    "genfix", REPO / "scripts" / "generate_pypower_fixtures.py"
)
gf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gf)

cstar = json.load(open(RES / "cstar.json"))
C_Pg = np.array(cstar["Pg"])
C_Vm = np.array(cstar["Vm"])
C_Va_deg = np.array(cstar["Va_deg"])
C_pin = np.array(cstar["p_hvdc_in"])
C_pout = np.array(cstar["p_hvdc_out"])
e2i = {int(k): v for k, v in cstar["ext_to_int"].items()}
i2e = {v: k for k, v in e2i.items()}

# ---- build neutralized case (branches off, dummy Q=0, PQ terminals) ----
orig = t_case9_dcline()
if "dclinecost" in orig:
    del orig["dclinecost"]
orig["branch"][:, RATE_A] = 1e5
ppc = gf._dcline_to_gens(orig)
on = orig["dcline"][:, c["BR_STATUS"]] > 0
ndc = int(on.sum())
ppc["gen"][-2 * ndc:, QMIN] = 0.0
ppc["gen"][-2 * ndc:, QMAX] = 0.0

idr = {int(b): i for i, b in enumerate(ppc["bus"][:, BUS_I])}

# ---- fix real-gen dispatch to C*; set their buses PV at C*'s Vm ----
# real gens are the first 3 rows (buses 1,2,30); dummies are last 2*ndc.
for gi in range(ppc["gen"].shape[0]):
    gb = int(ppc["gen"][gi, GEN_BUS])   # internal bus index in ppc pre-ext2int?
# _dcline_to_gens returns external-id case; gen GEN_BUS holds external ids.
real_bus = {1: 0, 2: 1, 30: 2}
for gi in range(3):
    gb_ext = int(ppc["gen"][gi, GEN_BUS])
    if gb_ext in real_bus:
        ppc["gen"][gi, PG] = C_Pg[real_bus[gb_ext]]
        ppc["gen"][gi, VG] = C_Vm[e2i[gb_ext]]

# ---- fix dummy-gen dispatch to C*'s DC flows (EX3 mapping) ----
# from-dummy Pg = -PF = -p_in ; to-dummy Pg = PT = -p_out
# dummy rows are indices 3..3+2*ndc; _dcline_to_gens appends fg (all from) then
# tg (all to): rows 3..3+ndc-1 are from, 3+ndc..3+2*ndc-1 are to.
for k in range(ndc):
    ppc["gen"][3 + k, PG] = -C_pin[k]          # from-gen = -PF
    ppc["gen"][3 + ndc + k, PG] = -C_pout[k]   # to-gen  = PT (= -p_out)
    ppc["gen"][3 + k, QG] = 0.0
    ppc["gen"][3 + ndc + k, QG] = 0.0

# ---- set bus voltages to C* as the PF starting point ----
for bid, ci in e2i.items():
    row = idr[bid]
    ppc["bus"][row, VM] = C_Vm[ci]
    ppc["bus"][row, VA] = C_Va_deg[ci]

# terminals PQ (as in neutralized model)
term = set(orig["dcline"][on, c["F_BUS"]].astype(int)) | \
       set(orig["dcline"][on, c["T_BUS"]].astype(int))
for bid in term:
    row = idr[bid]
    if ppc["bus"][row, BUS_TYPE] != REF:
        ppc["bus"][row, BUS_TYPE] = PQ

# ---- run Pypower's own power flow (with dcline userfcn coupling) ----
add_userfcn(ppc, "formulation", gf._make_coupling_userfcn(orig))
opt = ppoption()
opt["VERBOSE"] = 0
opt["OUT_ALL"] = 0
res, success = runpf(ppc, opt)

# ---- compare solved voltages to C* ----
lines = ["# EX6-(B): C* feasibility via Pypower power flow",
         f"# runpf success = {bool(success)}",
         "bus_ext   Vm_C*     Vm_pf     dVm       Va_C*     Va_pf     dVa"]
maxdVm = 0.0
maxdVa = 0.0
for row in range(res["bus"].shape[0]):
    ext = int(res["bus"][row, BUS_I])
    ci = e2i[ext]
    vm_pf = res["bus"][row, VM]
    va_pf = res["bus"][row, VA]
    dvm = vm_pf - C_Vm[ci]
    dva = va_pf - C_Va_deg[ci]
    maxdVm = max(maxdVm, abs(dvm))
    maxdVa = max(maxdVa, abs(dva))
    lines.append(f"  {ext:>3}   {C_Vm[ci]:8.5f}  {vm_pf:8.5f}  {dvm:8.5f}  "
                 f"{C_Va_deg[ci]:8.4f}  {va_pf:8.4f}  {dva:8.4f}")
# slack real-power output vs C*: how much P did the slack have to change?
slack_gen_P = res["gen"][0, PG]
lines.append(f"slack gen Pg after PF = {slack_gen_P:.4f} MW (C* slack Pg = {C_Pg[0]:.4f})")
lines.append(f"max |dVm| = {maxdVm:.6f} p.u.")
lines.append(f"max |dVa| = {maxdVa:.6f} deg")
lines.append("")
lines.append("INTERPRETATION: runpf success + dVm/dVa ~0 + slack Pg ~ C* slack")
lines.append(" => C* is a consistent operating point in Pypower's network")
lines.append(" => C* FEASIBLE in Pypower => divergence is LOCAL OPTIMA.")
lines.append("Large dVm/dVa or slack Pg shift => network/constraint models differ")
lines.append(" => C* INFEASIBLE in Pypower => genuinely different problem.")
text = "\n".join(lines) + "\n"
(RES / "ex6b_powerflow.txt").write_text(text)
print(text)
