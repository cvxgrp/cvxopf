# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""Generate the Pypower reference solutions for the four DNLP-vs-Pypower cells.

RUN THIS ONCE to (re)generate `reference_pypower.json`. It is NOT needed to run
the demo -- `demo.py` reads the committed JSON. This script runs in an isolated
pypower sandbox (pypower 5.1.19 / numpy 2.2.6); the demo runs in the main cvxopf
environment. The two cannot share a process, which is why the Pypower side is
precomputed and committed (the same pattern the repo's test fixtures use).

The four cells (network = 9-bus; two independent features toggled):
  1. smooth costs, no DC lines   -- from the COMMITTED fixture (not recomputed)
  2. PWL costs, no DC lines      -- from the COMMITTED fixture (not recomputed)
  3. smooth costs, with DC lines -- neutralized dcline OPF (computed here)
  4. PWL costs, with DC lines    -- neutralized dcline OPF, PWL gencost (here)

The two NO-DC cells already have validated Pypower references in
`tests/fixtures/` (`case9_pypower_reference.json` obj 5296.69 and
`case9_pwl_pypower_reference.json` obj 5322.94), exercised by passing tests
(`TestCase9`, `TestCase9Pwl`). We COPY those rather than regenerate them, so the
demo leans on the repo's already-tested oracle for the plain cells.

Cells 3 and 4 have no committed fixture, so we compute them here with the
NEUTRALIZED dcline model, which isolates the solution method (see the report's
"Model agreement" section): branch limits removed, DC-terminal reactive pinned
to zero, terminal buses set PQ, and the real-power dcline coupling
(1-L1)*Pgf + Pgt == -L0/baseMVA imposed as a userfcn.

Run:  uv run generate_pypower_reference.py
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
from pypower.api import runopf
from pypower.idx_brch import RATE_A
from pypower.idx_bus import BUS_I, BUS_TYPE, PQ, VA, VM
from pypower.idx_dcline import c
from pypower.idx_gen import GEN_BUS, PG, QG, QMAX, QMIN
from pypower.t.t_case9_dcline import t_case9_dcline
from pypower.add_userfcn import add_userfcn

_here = Path(__file__).resolve().parent
_repo = _here.parent.parent

# reuse the fixture-generation helpers (dcline->dummy-gens transform, coupling
# userfcn, ppopt) from the repo's committed fixture script.
_spec = importlib.util.spec_from_file_location(
    "genfix", _repo / "scripts" / "generate_pypower_fixtures.py"
)
gf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gf)

# case9 SMOOTH quadratic gencost (source: cvxopf.testcases.case9), hardcoded to
# keep this script pypower-pure. layout [MODEL=2, STARTUP, SHUTDOWN, NCOST=3,
# c2, c1, c0].
SMOOTH_GENCOST = np.array(
    [
        [2.0, 1500.0, 0.0, 3.0, 0.11, 5.0, 150.0],
        [2.0, 2000.0, 0.0, 3.0, 0.085, 1.2, 600.0],
        [2.0, 3000.0, 0.0, 3.0, 0.1225, 1.0, 335.0],
    ]
)


def _neutralized_case(gencost):
    """9-bus + neutralized dc lines, with the given real-gen gencost."""
    orig = t_case9_dcline()
    orig["gencost"] = np.asarray(gencost, dtype=float).copy()
    orig.pop("dclinecost", None)
    orig["branch"][:, RATE_A] = 1e5  # branch limits off
    ppc = gf._dcline_to_gens(orig)
    on = orig["dcline"][:, c["BR_STATUS"]] > 0
    ndc = int(on.sum())
    ppc["gen"][-2 * ndc :, QMIN] = 0.0  # terminal reactive pinned to zero
    ppc["gen"][-2 * ndc :, QMAX] = 0.0
    term = set(orig["dcline"][on, c["F_BUS"]].astype(int)) | set(
        orig["dcline"][on, c["T_BUS"]].astype(int)
    )
    idr = {int(b): i for i, b in enumerate(ppc["bus"][:, BUS_I])}
    for bid in term:
        row = idr[bid]
        if ppc["bus"][row, BUS_TYPE] != 3:  # leave the ref bus alone
            ppc["bus"][row, BUS_TYPE] = PQ
    add_userfcn(ppc, "formulation", gf._make_coupling_userfcn(orig))
    return ppc, ndc


def _from_fixture(name):
    """Read a committed no-DC Pypower reference (already test-validated)."""
    fx = json.loads((_repo / "tests" / "fixtures" / name).read_text())
    return {
        "objective": float(fx["objective"]),
        "Pg": [float(x) for x in fx["Pg"]],
        "Qg": [float(x) for x in fx.get("Qg", [])],
        "_source": f"tests/fixtures/{name} (committed, test-validated)",
    }


def _solve_neutralized(gencost):
    ppc, ndc = _neutralized_case(gencost)
    res = runopf(ppc, gf._make_ppopt())
    assert bool(res["success"]), "neutralized solve failed"
    gen = res["gen"]
    # dummy gen layout: 3 real, then ndc from-terminals, then ndc to-terminals.
    # p_in = -(from-terminal raw Pg), p_out = +(to-terminal raw Pg).
    p_in = (-gen[3 : 3 + ndc, PG]).tolist()
    p_out = gen[3 + ndc : 3 + 2 * ndc, PG].tolist()
    return {
        "objective": float(res["f"]),
        "Pg": gen[:3, PG].tolist(),
        "Qg": gen[:3, QG].tolist(),
        "p_hvdc_in": p_in,
        "p_hvdc_out": p_out,
        "gen_bus": gen[:, GEN_BUS].astype(int).tolist(),
    }


reference = {
    "_note": (
        "Pypower reference solutions for the four DNLP-vs-Pypower cells. "
        "Regenerate with `uv run generate_pypower_reference.py`. The two no-DC "
        "cells are copied from the committed, test-validated fixtures; the two "
        "DC cells are computed here with the neutralized dcline model (branches "
        "off, terminal Q=0, PQ terminals, real-power coupling userfcn) so only "
        "the solution method differs."
    ),
    "smooth_no_dc": _from_fixture("case9_pypower_reference.json"),
    "pwl_no_dc": _from_fixture("case9_pwl_pypower_reference.json"),
    "smooth_dc": _solve_neutralized(SMOOTH_GENCOST),
    "pwl_dc": _solve_neutralized(t_case9_dcline()["gencost"]),
}

(_here / "reference_pypower.json").write_text(json.dumps(reference, indent=2))
print("wrote", _here / "reference_pypower.json")
for k in ("smooth_no_dc", "pwl_no_dc", "smooth_dc", "pwl_dc"):
    print(f"  {k:14s} obj = {reference[k]['objective']:.4f}")
"""Expected: smooth_no_dc 5296.69 and pwl_no_dc 5322.94 (from the committed
fixtures), smooth_dc 5314.28 and pwl_dc 6249.87 (computed here). The demo
compares all four against the live cvxopf solves."""
