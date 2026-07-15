# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""THROWAWAY probe: does zeroing the dcline reactive bounds (and loss0) make
the Pypower oracle converge to cvxopf's unity-PF HVDC dispatch?

Reuses generate_pypower_fixtures.py's self-contained transform. Not committed
long-term; deletes cleanly. Run:
    uv run scripts/_probe_dcline_unitypf.py
"""

import importlib.util
from pathlib import Path

import numpy as np
from pypower.idx_dcline import c
from pypower.idx_gen import QMIN, QMAX, PG
from pypower.idx_bus import VM
from pypower.t.t_case9_dcline import t_case9_dcline
from pypower.add_userfcn import add_userfcn

# import the committed fixture-generation module by path
_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "genfix", _here / "generate_pypower_fixtures.py"
)
genfix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(genfix)


def solve_variant(zero_q: bool, zero_loss0: bool):
    """Solve the dcline oracle with optional reactive-bound / loss0 zeroing."""
    orig = t_case9_dcline()
    if "dclinecost" in orig:
        del orig["dclinecost"]
    if zero_loss0:
        orig["dcline"][:, c["LOSS0"]] = 0.0
    if zero_q:
        for col in ("QMINF", "QMAXF", "QMINT", "QMAXT"):
            orig["dcline"][:, c[col]] = 0.0

    ppc = genfix._dcline_to_gens(orig)
    if zero_q:
        # dummy gens are the last 2*ndc rows as appended by _dcline_to_gens
        ndc = int((orig["dcline"][:, c["BR_STATUS"]] > 0).sum())
        ppc["gen"][-2 * ndc:, QMIN] = 0.0
        ppc["gen"][-2 * ndc:, QMAX] = 0.0
    add_userfcn(ppc, "formulation", genfix._make_coupling_userfcn(orig))
    result = genfix.runopf(ppc, genfix._make_ppopt())
    return result


def report(tag, result):
    ok = bool(result["success"])
    if not ok:
        print(f"{tag:28s}: INFEASIBLE / did not converge")
        return
    g = result["gen"]
    bus = result["bus"]
    obj = float(result["f"])
    real_pg = np.round(g[:3, PG], 2)
    vm = np.round(bus[:, VM], 4)
    print(f"{tag:28s}: obj={obj:9.2f}  realPg={real_pg}  Vm={vm}")


if __name__ == "__main__":
    print("cvxopf AC (free import): obj=5490.10  realPg=[90. 10. 220.59]")
    print("                         (p_in=[1, 2, 10])")
    print()
    report("baseline (Qfree, loss0 on)", solve_variant(False, False))
    report("zero-Q (loss0 on)", solve_variant(True, False))
    report("zero-Q + zero-loss0", solve_variant(True, True))
