# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""RESEARCH PROBE (throwaway): can we turn OFF the terminal PV voltage pin in
Pypower's dcline model and still solve? _dcline_to_gens sets terminal buses to
PV; this probe reverts them to their original type (PQ) after the transform and
checks whether runopf still converges.

Run: uv run _probe_nopv.py
"""
import importlib.util
from pathlib import Path

import numpy as np
from pypower.idx_dcline import c
from pypower.idx_bus import BUS_TYPE, BUS_I, PQ
from pypower.idx_gen import PG
from pypower.t.t_case9_dcline import t_case9_dcline
from pypower.add_userfcn import add_userfcn

_here = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "genfix", _here / "scripts" / "generate_pypower_fixtures.py"
)
genfix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(genfix)


def solve(unpin_voltage: bool):
    orig = t_case9_dcline()
    if "dclinecost" in orig:
        del orig["dclinecost"]
    ppc = genfix._dcline_to_gens(orig)
    if unpin_voltage:
        # revert the terminal buses (from/to of in-service dclines) to PQ.
        on = orig["dcline"][:, c["BR_STATUS"]] > 0
        term_ext = set(orig["dcline"][on, c["F_BUS"]].astype(int)) | \
                   set(orig["dcline"][on, c["T_BUS"]].astype(int))
        id_to_row = {int(b): i for i, b in enumerate(ppc["bus"][:, BUS_I])}
        for bid in term_ext:
            row = id_to_row[bid]
            # do not touch the ref bus
            if ppc["bus"][row, BUS_TYPE] != 3:
                ppc["bus"][row, BUS_TYPE] = PQ
    add_userfcn(ppc, "formulation", genfix._make_coupling_userfcn(orig))
    return genfix.runopf(ppc, genfix._make_ppopt())


if __name__ == "__main__":
    for unpin, tag in [(False, "PV pin ON (baseline)"), (True, "PV pin OFF (terminals PQ)")]:
        try:
            r = solve(unpin)
            ok = bool(r["success"])
            if ok:
                print(f"{tag:30s}: success obj={float(r['f']):9.2f} realPg={np.round(r['gen'][:3, PG], 2)}")
            else:
                print(f"{tag:30s}: DID NOT CONVERGE (success=False)")
        except Exception as e:
            print(f"{tag:30s}: EXCEPTION {type(e).__name__}: {e}")
