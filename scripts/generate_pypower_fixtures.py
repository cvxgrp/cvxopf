# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
#   "scipy==1.18.0",
# ]
# ///
"""
Generate Pypower reference fixture files for cvxopf validation tests.

This script runs Pypower's AC-OPF solver on the case9 and case14 test cases
and writes the results to JSON files in tests/fixtures/. The fixture files
are committed to the repository and consumed by the validation test suite
without requiring Pypower to be installed in the main package environment.

Usage
-----
    uv run scripts/generate_pypower_fixtures.py

Requirements
------------
This script is self-contained and manages its own dependencies via uv inline
script metadata (see header above). Do NOT run it with the main package
Python environment. It intentionally pins numpy==2.2.6 to work around the
use of numpy.in1d in pypower==5.1.19, which was removed in numpy 2.3.

When to re-run
--------------
- A new MATPOWER test case is added to the package.
- A suspected bug in an existing fixture needs to be ruled out.

Do NOT run this script as part of CI. CI consumes the committed fixture
files; it does not regenerate them.

Output
------
tests/fixtures/case9_pypower_reference.json
tests/fixtures/case14_pypower_reference.json
tests/fixtures/case57_pypower_reference.json

Fixture schema
--------------
Each JSON file contains a single object with the following keys:

    case        str     name of the test case ("case9" or "case14")
    solver      str     always "pypower-5.1.19"
    status      str     "optimal" if converged, "failed" otherwise
    objective   float   optimal cost in $/hr
    Pg          list    generator real outputs in MW, length ng
    Qg          list    generator reactive outputs in MVAr, length ng
    Vm          list    bus voltage magnitudes in p.u., length nb
    Va_deg      list    bus voltage angles in degrees, length nb
"""

import json
import sys
from pathlib import Path

import numpy as np
from pypower.api import case9, case14, case57, runopf
from pypower.idx_bus import VM, VA
from pypower.idx_gen import PG, QG
from pypower import ppoption


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES  = REPO_ROOT / "tests" / "fixtures"


# ---------------------------------------------------------------------------
# Pypower OPF options: suppress printed output
# ---------------------------------------------------------------------------

def _make_ppopt():
    ppopt = ppoption.ppoption()
    ppopt["VERBOSE"] = 0
    ppopt["OUT_ALL"] = 0
    return ppopt


# ---------------------------------------------------------------------------
# Run OPF and extract results
# ---------------------------------------------------------------------------

def _run_case(case_fn, case_name: str) -> dict:
    """
    Run Pypower AC-OPF on a single case and return a results dict.

    Parameters
    ----------
    case_fn : callable
        Pypower case function (e.g. pypower.api.case9).
    case_name : str
        Human-readable name for the fixture metadata field.

    Returns
    -------
    dict
        Fixture dict ready for JSON serialisation.
    """
    ppc   = case_fn()
    ppopt = _make_ppopt()

    print(f"  Running Pypower AC-OPF for {case_name} ...", end=" ", flush=True)
    result = runopf(ppc, ppopt)

    converged = bool(result["success"])
    status    = "optimal" if converged else "failed"

    if not converged:
        print(f"FAILED (Pypower did not converge for {case_name})")
        return dict(
            case      = case_name,
            solver    = "pypower-5.1.19",
            status    = status,
            objective = None,
            Pg        = None,
            Qg        = None,
            Vm        = None,
            Va_deg    = None,
        )

    objective = float(result["f"])
    Pg        = result["gen"][:, PG].tolist()    # MW
    Qg        = result["gen"][:, QG].tolist()    # MVAr
    Vm        = result["bus"][:, VM].tolist()    # p.u.
    Va_deg    = result["bus"][:, VA].tolist()    # degrees

    print(f"OK  (f = {objective:.4f} $/hr)")

    return dict(
        case      = case_name,
        solver    = "pypower-5.1.19",
        status    = status,
        objective = objective,
        Pg        = Pg,
        Qg        = Qg,
        Vm        = Vm,
        Va_deg    = Va_deg,
    )


# ---------------------------------------------------------------------------
# Write fixture
# ---------------------------------------------------------------------------

def _write_fixture(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Written: {path.relative_to(REPO_ROOT)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"numpy version : {np.__version__}")
    print(f"Fixture output: {FIXTURES.relative_to(REPO_ROOT)}")
    print()

    cases = [
        (case9,  "case9",  "case9_pypower_reference.json"),
        (case14, "case14", "case14_pypower_reference.json"),
        (case57, "case57", "case57_pypower_reference.json"),
    ]

    failed = False
    for case_fn, case_name, filename in cases:
        data = _run_case(case_fn, case_name)
        if data["status"] != "optimal":
            failed = True
        _write_fixture(data, FIXTURES / filename)
        print()

    if failed:
        print("ERROR: one or more cases did not converge. "
              "Fixture files may be incomplete.", file=sys.stderr)
        return 1

    print("All fixtures generated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
