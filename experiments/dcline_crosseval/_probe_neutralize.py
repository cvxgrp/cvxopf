# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""RESEARCH PROBE (throwaway): prove the case9_dcline cvxopf-vs-Pypower gap is
exactly (branch limits) + (terminal reactive).

Neutralize both in the Pypower oracle and check convergence to cvxopf's ~5490:
  Toggle A: relax all branch rateA (cvxopf ignores branch limits, M4 stub)
  Toggle B: zero the dcline dummy-gen Q bounds (Qmin=Qmax=0) -> unity PF
            (leave the PV voltage pin in place, case57-style; expect only the
             terminal Vm to differ, not dispatch/objective)

If obj/dispatch converge to cvxopf's, the two-cause mechanism is proven.
Run: uv run scripts/_probe_neutralize.py  (path-agnostic; imports genfix)
"""
import importlib.util
from pathlib import Path

import numpy as np
from pypower.idx_dcline import c
from pypower.idx_brch import RATE_A
from pypower.idx_gen import PG, QMIN, QMAX
from pypower.t.t_case9_dcline import t_case9_dcline
from pypower.add_userfcn import add_userfcn

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "genfix", _here / "scripts" / "generate_pypower_fixtures.py"
)
genfix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(genfix)


def solve(relax_branches: bool, zero_q: bool):
    orig = t_case9_dcline()
    if "dclinecost" in orig:
        del orig["dclinecost"]
    if relax_branches:
        orig["branch"][:, RATE_A] = 1e5
    ppc = genfix._dcline_to_gens(orig)
    if zero_q:
        ndc = int((orig["dcline"][:, c["BR_STATUS"]] > 0).sum())
        ppc["gen"][-2 * ndc:, QMIN] = 0.0
        ppc["gen"][-2 * ndc:, QMAX] = 0.0
    add_userfcn(ppc, "formulation", genfix._make_coupling_userfcn(orig))
    return genfix.runopf(ppc, genfix._make_ppopt())


if __name__ == "__main__":
    print("cvxopf AC (free import): obj=5490.10 realPg=[90. 10. 220.16]")
    print()
    for rb, zq, tag in [
        (False, False, "baseline (limits on, Q free)"),
        (True,  False, "A: branches relaxed only"),
        (False, True,  "B: reactive zeroed only"),
        (True,  True,  "A+B: branches relaxed + reactive zeroed"),
    ]:
        r = solve(rb, zq)
        ok = bool(r["success"])
        if ok:
            print(f"{tag:42s}: obj={float(r['f']):9.2f} realPg={np.round(r['gen'][:3, PG], 2)}")
        else:
            print(f"{tag:42s}: INFEASIBLE / no converge")
