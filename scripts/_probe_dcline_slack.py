# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""THROWAWAY: if br4 (bus6->bus7) rateA is relaxed 40 -> 150 (standard case9
value), does the Pypower dcline oracle converge to cvxopf's dispatch?

Proves the Gate 6b oracle concept: with no binding branch limit, cvxopf (which
does not enforce limits, M4 stub) and Pypower solve the same problem. Run:
    uv run scripts/_probe_dcline_slack.py
"""

import importlib.util
from pathlib import Path

import numpy as np
from pypower.idx_brch import RATE_A
from pypower.idx_gen import PG
from pypower.t.t_case9_dcline import t_case9_dcline
from pypower.add_userfcn import add_userfcn

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "genfix", _here / "generate_pypower_fixtures.py"
)
genfix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(genfix)

BR4 = 4  # bus6->bus7 branch row index


def solve_pypower(slack: bool):
    orig = t_case9_dcline()
    if "dclinecost" in orig:
        del orig["dclinecost"]
    if slack:
        orig["branch"][BR4, RATE_A] = 150.0  # standard case9 value
    ppc = genfix._dcline_to_gens(orig)
    add_userfcn(ppc, "formulation", genfix._make_coupling_userfcn(orig))
    res = genfix.runopf(ppc, genfix._make_ppopt())
    return res


if __name__ == "__main__":
    for slack in (False, True):
        res = solve_pypower(slack)
        tag = "slack br4=150" if slack else "tight br4=40 "
        ok = bool(res["success"])
        obj = float(res["f"])
        real = np.round(res["gen"][:3, PG], 2)
        print(f"pypower {tag}: success={ok} obj={obj:9.2f} realPg={real}")
    print()
    print("cvxopf AC (free import, no limits): obj=5490.10 realPg=[90. 10. 220.16]")
    print("  -> compare to pypower slack-br4 row above")
