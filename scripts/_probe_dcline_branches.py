# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""THROWAWAY: are branch flow limits binding at Pypower's dcline solution?

If a line is at its rateA limit, that explains why Pypower will not load the
cheap generator (bus 30) as hard as cvxopf does -- cvxopf does not enforce
branch limits (Milestone 4 stub). Run: uv run scripts/_probe_dcline_branches.py
"""

import importlib.util
from pathlib import Path

import numpy as np
from pypower.idx_dcline import c
from pypower.idx_brch import PF, QF, PT, QT, RATE_A
from pypower.t.t_case9_dcline import t_case9_dcline
from pypower.add_userfcn import add_userfcn

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "genfix", _here / "generate_pypower_fixtures.py"
)
genfix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(genfix)


def solve_baseline():
    orig = t_case9_dcline()
    if "dclinecost" in orig:
        del orig["dclinecost"]
    ppc = genfix._dcline_to_gens(orig)
    add_userfcn(ppc, "formulation", genfix._make_coupling_userfcn(orig))
    return genfix.runopf(ppc, genfix._make_ppopt())


if __name__ == "__main__":
    res = solve_baseline()
    br = res["branch"]
    print("Pypower dcline solution -- branch apparent-power flows vs rateA:")
    for i in range(br.shape[0]):
        sf = float((br[i, PF] ** 2 + br[i, QF] ** 2) ** 0.5)
        st = float((br[i, PT] ** 2 + br[i, QT] ** 2) ** 0.5)
        smax = max(sf, st)
        rate = float(br[i, RATE_A])
        binding = rate > 0 and smax >= rate - 1e-2
        flag = "  <== BINDING" if binding else ("  (rateA=0: unlimited)" if rate == 0 else "")
        fb = int(br[i, 0]); tb = int(br[i, 1])
        print(f"  br{i} bus{fb:>2}->bus{tb:>2}: |S|={smax:8.2f}  rateA={rate:7.1f}{flag}")
