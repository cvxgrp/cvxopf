# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""Four-way cross-evaluation: is cvxopf's and (neutralized) Pypower's
case9_dcline the SAME optimization problem, or different basins?

EX1 neutralized Pypower model (branches off, dummy-gen Q=0, terminals PQ).
EX-later steps consume P* recorded here. Pypower-side only; cvxopf point is
fed in as a literal (recorded separately) to keep this script pypower-pure.

Run: uv run _ex_crosseval.py
"""
import importlib.util
from pathlib import Path

import numpy as np
from pypower.idx_dcline import c
from pypower.idx_brch import RATE_A
from pypower.idx_bus import BUS_TYPE, BUS_I, PQ, VM, VA
from pypower.idx_gen import PG, QG, QMIN, QMAX
from pypower.t.t_case9_dcline import t_case9_dcline
from pypower.add_userfcn import add_userfcn

_here = Path(__file__).resolve().parent
_repo = _here.parent.parent  # experiments/dcline_crosseval/ -> repo root
_spec = importlib.util.spec_from_file_location(
    "genfix", _repo / "scripts" / "generate_pypower_fixtures.py"
)
gf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gf)


def solve_neutralized():
    """EX1: branches off + dummy-gen Q pinned 0 + terminals PQ."""
    orig = t_case9_dcline()
    if "dclinecost" in orig:
        del orig["dclinecost"]
    orig["branch"][:, RATE_A] = 1e5                     # branches off
    ppc = gf._dcline_to_gens(orig)
    on = orig["dcline"][:, c["BR_STATUS"]] > 0
    ndc = int(on.sum())
    ppc["gen"][-2 * ndc:, QMIN] = 0.0                   # dummy Q = 0
    ppc["gen"][-2 * ndc:, QMAX] = 0.0
    term = set(orig["dcline"][on, c["F_BUS"]].astype(int)) | \
           set(orig["dcline"][on, c["T_BUS"]].astype(int))
    idr = {int(b): i for i, b in enumerate(ppc["bus"][:, BUS_I])}
    for bid in term:
        row = idr[bid]
        if ppc["bus"][row, BUS_TYPE] != 3:              # not the ref bus
            ppc["bus"][row, BUS_TYPE] = PQ              # terminals PQ
    add_userfcn(ppc, "formulation", gf._make_coupling_userfcn(orig))
    return gf.runopf(ppc, gf._make_ppopt()), orig


if __name__ == "__main__":
    res, orig = solve_neutralized()
    ok = bool(res["success"])
    print("EX1 neutralized Pypower solve: success =", ok)
    if ok:
        print("EX1 objective:", round(float(res["f"]), 4))
        print("EX1 real Pg  :", np.round(res["gen"][:3, PG], 4).tolist())
        print("EX1 dummy Pg :", np.round(res["gen"][3:, PG], 4).tolist())
        print("EX1 dummy Qg :", np.round(res["gen"][3:, QG], 4).tolist())
        # bus Vm/Va in external-id order
        ids = res["bus"][:, BUS_I].astype(int)
        print("EX1 bus ids  :", ids.tolist())
        print("EX1 Vm       :", np.round(res["bus"][:, VM], 5).tolist())
        print("EX1 Va(deg)  :", np.round(res["bus"][:, VA], 4).tolist())
