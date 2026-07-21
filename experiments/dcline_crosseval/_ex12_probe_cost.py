"""Probe: why does the Pypower-cost readout give 11536 (the EX11 bug)?

Sanity: evaluate the SAME polynomial cost at C* (Pg=[90,10,220.163]); it must
reproduce C*'s known objective 5490.10. If it gives 11536-ish, the polyval /
coeff-slice handling is wrong (not the dispatch). Prints the raw gencost rows.
"""
import json
from pathlib import Path

import numpy as np

from cvxopf.testcases.case9_dcline import case9_dcline

case = case9_dcline()
gc = case["gencost"]
print("gencost shape:", gc.shape)
for i, row in enumerate(gc):
    print(f"  row{i}: {row.tolist()}")

cstar = json.loads((Path(__file__).resolve().parent / "results/cstar_full.json").read_text())
Pg_c = np.asarray(cstar["Pg"])
print("C* Pg:", Pg_c.tolist(), " known C* obj = 5490.10")

_NCOST = 3


def _polycost(gc_row, pg):
    n = int(gc_row[_NCOST])
    coeffs = gc_row[4 : 4 + n]
    return float(np.polyval(coeffs, pg))


total = 0.0
for i in range(3):
    c = _polycost(gc[i], Pg_c[i])
    print(f"  gen{i}: NCOST={int(gc[i][_NCOST])} coeffs={gc[i][4:4+int(gc[i][_NCOST])].tolist()} "
          f"Pg={Pg_c[i]:.3f} -> cost={c:.4f}")
    total += c
print("SUM over real gens =", round(total, 4), " (should be ~5490.10 if readout correct)")
