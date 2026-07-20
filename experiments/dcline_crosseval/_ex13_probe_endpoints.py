"""Probe: did we swap a DC-line endpoint between the cvxopf and Pypower specs?

The EX13 routing flip (cvxopf floors lossless link1, maxes lossy link2; Pypower
the reverse) would be MANUFACTURED by a from/to swap on either side. Before
theorising about source-bus economics, verify both model specs map the SAME
physical endpoints as the raw dcline table.

Raw t_case9_dcline dcline table (active rows), from EX13 dump:
  row0: F=30 T=4  [1,10]  loss1=0.01
  row1: F=7  T=9  [2,10]  loss1=0.0   (lossless)
  row3: F=5  T=9  [0,10]  loss1=0.05  (5% lossy)

Checks:
  A. cvxopf hvdc_from_dcline: each HVDCLink.from_bus/to_bus == raw F_BUS/T_BUS.
  B. cvxopf incidence: Ch_from has +1 at from_bus's internal row, Ch_to at
     to_bus's -- i.e. p_in injects at from_bus, p_out at to_bus.
  C. Pypower _dcline_to_gens: from-dummy GEN_BUS == F_BUS, to-dummy == T_BUS
     (read from the EX13 pstar_smooth.json gen_bus we already dumped).

Pure main-env for A/B; C reads the committed sandbox JSON (no pypower import).
Run: uv run --active python _ex13_probe_endpoints.py
"""
import json
import warnings
from pathlib import Path

import numpy as np

from cvxopf.hvdc import hvdc_from_dcline
from cvxopf.problem import build_opf
from cvxopf.testcases.case9_dcline import case9_dcline

_here = Path(__file__).resolve().parent
RES = _here / "results"

case = case9_dcline()
DC = case["dcline"]
_F, _T, _STATUS, _PMIN, _PMAX, _LOSS1 = 0, 1, 2, 9, 10, 16
dc_on = DC[DC[:, _STATUS] > 0, :]
print("raw active dcline rows (F_BUS, T_BUS, PMIN, PMAX, loss1):")
for k, r in enumerate(dc_on):
    print(f"  link{k}: F={int(r[_F])} T={int(r[_T])} "
          f"[{r[_PMIN]:.0f},{r[_PMAX]:.0f}] loss1={r[_LOSS1]}")
print()

# --- A: cvxopf HVDCLink endpoints ---
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    links = hvdc_from_dcline(DC)
print("A. cvxopf HVDCLink endpoints:")
ok_A = True
for k, lnk in enumerate(links):
    match = (lnk.from_bus == int(dc_on[k, _F])) and (lnk.to_bus == int(dc_on[k, _T]))
    ok_A = ok_A and match
    print(f"  link{k}: from_bus={lnk.from_bus} to_bus={lnk.to_bus} "
          f"loss%={lnk.loss_percent}  matches raw: {match}")
print(f"  A OK: {ok_A}")
print()

# --- B: cvxopf incidence Ch_from / Ch_to place p_in@from_bus, p_out@to_bus ---
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    build = build_opf(case, formulation="ac", hvdc=links)
d = build.data
e2i = {int(k): int(v) for k, v in d["ext_to_int"].items()}
Chf = np.asarray(d["Ch_from"])
Cht = np.asarray(d["Ch_to"])
print("B. cvxopf incidence (nonzero row per link column):")
ok_B = True
for k in range(len(links)):
    frow = int(np.argmax(np.abs(Chf[:, k])))
    trow = int(np.argmax(np.abs(Cht[:, k])))
    want_f = e2i[int(dc_on[k, _F])]
    want_t = e2i[int(dc_on[k, _T])]
    match = (frow == want_f) and (trow == want_t)
    ok_B = ok_B and match
    print(f"  link{k}: Ch_from row={frow}(={Chf[frow,k]:+.0f}) expect {want_f} | "
          f"Ch_to row={trow}(={Cht[trow,k]:+.0f}) expect {want_t}  matches: {match}")
print(f"  B OK: {ok_B}")
print()

# --- C: Pypower dummy-gen buses (from committed pstar_smooth.json) ---
ps_path = RES / "ex13_pstar_smooth.json"
print("C. Pypower dummy-gen endpoints (from ex13_pstar_smooth.json):")
if not ps_path.exists():
    print("  (!) ex13_pstar_smooth.json missing -- run _ex13_smoothcost_pypower.py")
else:
    ps = json.loads(ps_path.read_text())
    gen_bus = ps["gen_bus"]            # [real x3, from-dummy x ndc, to-dummy x ndc]
    ndc = int(ps["ndc"])
    from_bus_pp = gen_bus[3 : 3 + ndc]
    to_bus_pp = gen_bus[3 + ndc : 3 + 2 * ndc]
    print(f"  gen_bus = {gen_bus}  (ndc={ndc})")
    ok_C = True
    for k in range(ndc):
        match = (from_bus_pp[k] == int(dc_on[k, _F])) and (to_bus_pp[k] == int(dc_on[k, _T]))
        ok_C = ok_C and match
        print(f"  link{k}: from-dummy bus={from_bus_pp[k]} to-dummy bus={to_bus_pp[k]} "
              f"expect F={int(dc_on[k,_F])} T={int(dc_on[k,_T])}  matches: {match}")
    print(f"  C OK: {ok_C}")
    print()
    print("VERDICT:", "NO endpoint swap on any side" if (ok_A and ok_B and ok_C)
          else "ENDPOINT MISMATCH -- the routing flip may be a bookkeeping artifact")
