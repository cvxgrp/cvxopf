# /// script
# requires-python = ">=3.10"
# dependencies = ["pypower==5.1.19", "numpy==2.2.6", "scipy==1.18.0"]
# ///
"""EX6-(B) CONTROL: feed P* (native neutralized-Pypower optimum) through the
identical runpf harness as _ex6b_powerflow.py fed C*. P* should be a runpf
fixed point (~0 drift). If it is, C*s ~1pct drift is a real signal. If P* also
drifts, the harness itself adds drift and neither result is trustworthy.
P* numbers are the user-verified canonical block from LOG.md.
Reads nothing external for P*; writes results/ex6b_control_pstar.txt."""
import importlib.util
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
_spec = importlib.util.spec_from_file_location("genfix", REPO / "scripts" / "generate_pypower_fixtures.py")
gf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gf)
# --- P* canonical numbers (user-verified, LOG.md) ---
P_real_Pg = np.array([90.0, 106.1427, 123.4818])  # buses 1,2,30
P_dummy_Pg = np.array([-1.0, -10.0, -10.0, -0.01, 10.0, 9.5])  # from0,1,3 then to0,1,3
e2i = {1:0,2:1,30:2,4:3,5:4,6:5,7:6,8:7,9:8}
P_Vm = np.array([1.1,1.09714,1.08683,1.09495,1.08335,1.1,1.08877,1.09991,1.07499])
P_Va = np.array([0.0,2.9221,4.3964,-2.4668,-4.1524,0.9545,-1.9143,-0.2293,-4.4668])
# --- build neutralized case (mirror _ex6b_powerflow.py exactly) ---
orig = t_case9_dcline()
if "dclinecost" in orig:
    del orig["dclinecost"]
orig["branch"][:, RATE_A] = 1e5
ppc = gf._dcline_to_gens(orig)
on = orig["dcline"][:, c["BR_STATUS"]] > 0
ndc = int(on.sum())
ppc["gen"][-2*ndc:, QMIN] = 0.0
ppc["gen"][-2*ndc:, QMAX] = 0.0
idr = {int(b): i for i, b in enumerate(ppc["bus"][:, BUS_I])}
# --- install P* dispatch: real gens PV at P* Vm ---
real_bus = {1:0, 2:1, 30:2}
for gi in range(3):
    gb = int(ppc["gen"][gi, GEN_BUS])
    if gb in real_bus:
        ppc["gen"][gi, PG] = P_real_Pg[real_bus[gb]]
        ppc["gen"][gi, VG] = P_Vm[e2i[gb]]
# dummy gens: P* is native Pypower rep -> set dummy Pg directly
for k in range(2*ndc):
    ppc["gen"][3 + k, PG] = P_dummy_Pg[k]
    ppc["gen"][3 + k, QG] = 0.0
# --- set bus voltages to P* as PF start; terminals PQ ---
for bid, ci in e2i.items():
    row = idr[bid]
    ppc["bus"][row, VM] = P_Vm[ci]
    ppc["bus"][row, VA] = P_Va[ci]
term = set(orig["dcline"][on, c["F_BUS"]].astype(int)) | set(orig["dcline"][on, c["T_BUS"]].astype(int))
for bid in term:
    row = idr[bid]
    if ppc["bus"][row, BUS_TYPE] != REF:
        ppc["bus"][row, BUS_TYPE] = PQ
# --- run Pypower power flow with dcline coupling userfcn ---
add_userfcn(ppc, "formulation", gf._make_coupling_userfcn(orig))
opt = ppoption()
opt["VERBOSE"] = 0
opt["OUT_ALL"] = 0
res, success = runpf(ppc, opt)
# --- compare solved voltages back to P* ---
lines = ["# EX6-(B) CONTROL: P* feasibility via Pypower power flow", f"# runpf success = {bool(success)}", "bus_ext   Vm_P*     Vm_pf     dVm       Va_P*     Va_pf     dVa"]
maxdVm = 0.0
maxdVa = 0.0
for row in range(res["bus"].shape[0]):
    ext = int(res["bus"][row, BUS_I])
    ci = e2i[ext]
    vm_pf = res["bus"][row, VM]
    va_pf = res["bus"][row, VA]
    dvm = vm_pf - P_Vm[ci]
    dva = va_pf - P_Va[ci]
    maxdVm = max(maxdVm, abs(dvm))
    maxdVa = max(maxdVa, abs(dva))
    lines.append(f"  {ext:>3}   {P_Vm[ci]:8.5f}  {vm_pf:8.5f}  {dvm:8.5f}  {P_Va[ci]:8.4f}  {va_pf:8.4f}  {dva:8.4f}")
slack_gen_P = res["gen"][0, PG]
lines.append(f"slack gen Pg after PF = {slack_gen_P:.4f} MW (P* slack Pg = {P_real_Pg[0]:.4f})")
lines.append(f"max |dVm| = {maxdVm:.6f} p.u.")
lines.append(f"max |dVa| = {maxdVa:.6f} deg")
lines.append("")
lines.append("CONTROL INTERPRETATION: P* is the native runopf solution of this case.")
lines.append("If P* drift ~0 -> harness is clean -> C* 1pct drift is a REAL signal.")
lines.append("If P* drift ~ C* drift -> harness adds drift -> EX6-(B) untrustworthy.")
text = chr(10).join(lines) + chr(10)
(RES / "ex6b_control_pstar.txt").write_text(text)
print(text)
