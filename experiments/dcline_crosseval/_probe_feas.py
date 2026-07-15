"""Research probe: is Pypower's case9_dcline solution feasible in cvxopf's AC
model? Evaluate the nodal power-balance residual (esp. reactive) at Pypower's
solved voltages, using cvxopf's own Ybus. A reactive residual concentrated at
the dcline terminal buses would indicate the unity-PF device model is the cause.
"""
import json, warnings
import numpy as np
from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf

case = case9_dcline()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    links = hvdc_from_dcline(case["dcline"])
    b = build_opf(case, formulation="ac", hvdc=links)

d = b.data
e2i = d["ext_to_int"]
nb = d["nb"]
bMVA = d["baseMVA"]
Ybus = np.asarray(d["Ybus"].todense()) if hasattr(d["Ybus"], "todense") else np.asarray(d["Ybus"])

fix = json.load(open("tests/fixtures/case9_dcline_pypower_reference.json"))
# fixture bus arrays are in pypower internal/loadcase order = external id order
# of the case bus table. Map each case bus row -> cvxopf internal index.
ext_ids = case["bus"][:, 0].astype(int)  # external ids in fixture row order
Vm = np.array(fix["Vm"]); Va = np.deg2rad(np.array(fix["Va_deg"]))

# place into cvxopf internal order
Vm_i = np.zeros(nb); Va_i = np.zeros(nb)
for row, eid in enumerate(ext_ids):
    Vm_i[e2i[eid]] = Vm[row]
    Va_i[e2i[eid]] = Va[row]
V = Vm_i * np.exp(1j * Va_i)

# Required injection at each bus from cvxopf's Ybus (per-unit)
S_req = V * np.conj(Ybus @ V)   # per-unit

# Suppliable in cvxopf: gen injections (real-gen only) - load + HVDC real inj
Cg = np.asarray(d["Cg"].todense()) if hasattr(d["Cg"], "todense") else np.asarray(d["Cg"])
Pg = np.array(fix["Pg"][:3]) / bMVA
Qg = np.array(fix["Qg"][:3]) / bMVA
Pd = d["Pd"]; Qd = d["Qd"]
# HVDC real injections from pypower dummy gens: p_in = PF (from-gen Pg = -PF),
# p_out = -(to-gen Pg)?? Use cvxopf convention: inj = Ch_from@p_in + Ch_to@p_out
# pypower from-gen Pg[3:6] = -PF, to-gen Pg[6:9] = PT.
def _dense(m):
    return np.asarray(m.todense()) if hasattr(m, "todense") else np.asarray(m)
Ch_from = _dense(d["Ch_from"])
Ch_to = _dense(d["Ch_to"])
# cvxopf convention: p_in = +PF (from-gen Pg = -PF, so p_in = -from_gen);
# p_out = -PT (to-gen Pg = +PT, so p_out = -to_gen).
p_in = -np.array(fix["Pg"][3:6])   # = +PF
p_out = -np.array(fix["Pg"][6:9])  # = -PT
# cvxopf hvdc real injection (per-unit)
hvdc_P = (Ch_from @ p_in + Ch_to @ p_out) / bMVA

P_supply = Cg @ Pg - Pd + hvdc_P
Q_supply = Cg @ Qg - Qd   # cvxopf has NO hvdc reactive term

P_res = S_req.real - P_supply
Q_res = S_req.imag - Q_supply

i2e = {v: k for k, v in e2i.items()}
terminal_ext = {30, 4, 5, 9}
print("bus(ext)  P_resid(MW)  Q_resid(MVAr)  terminal?")
for i in range(nb):
    eid = i2e[i]
    t = "<== dcline terminal" if eid in terminal_ext else ""
    print(f"  {eid:>3}      {P_res[i]*bMVA:9.3f}    {Q_res[i]*bMVA:9.3f}   {t}")
print(f"max |P_resid| = {np.abs(P_res).max()*bMVA:.3f} MW")
print(f"max |Q_resid| = {np.abs(Q_res).max()*bMVA:.3f} MVAr")
