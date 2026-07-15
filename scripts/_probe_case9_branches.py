# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""THROWAWAY: does the STANDARD case9 (no dcline) have active branch limits
in Pypower's AC-OPF solution? Also prints the rateA column from the raw case.
Run: uv run scripts/_probe_case9_branches.py
"""

import numpy as np
from pypower.api import case9, runopf, ppoption
from pypower.idx_brch import PF, QF, PT, QT, RATE_A


def _ppopt():
    o = ppoption()
    o["VERBOSE"] = 0
    o["OUT_ALL"] = 0
    return o


if __name__ == "__main__":
    raw = case9()
    print("raw case9 branch rateA column:")
    print("  ", np.round(raw["branch"][:, RATE_A], 1).tolist())

    res = runopf(case9(), _ppopt())
    br = res["branch"]
    print(f"solve success: {bool(res['success'])}  objective: {float(res['f']):.2f}")
    print("branch apparent-power flows vs rateA:")
    any_binding = False
    for i in range(br.shape[0]):
        sf = float((br[i, PF] ** 2 + br[i, QF] ** 2) ** 0.5)
        st = float((br[i, PT] ** 2 + br[i, QT] ** 2) ** 0.5)
        smax = max(sf, st)
        rate = float(br[i, RATE_A])
        binding = rate > 0 and smax >= rate - 1e-2
        any_binding = any_binding or binding
        flag = "  <== BINDING" if binding else ("  (rateA=0: unlimited)" if rate == 0 else "")
        fb = int(br[i, 0]); tb = int(br[i, 1])
        print(f"  br{i} bus{fb:>2}->bus{tb:>2}: |S|={smax:8.2f}  rateA={rate:7.1f}{flag}")
    print(f"ANY BINDING LIMIT: {any_binding}")
