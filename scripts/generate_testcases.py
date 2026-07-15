# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pypower==5.1.19",
#   "numpy==2.2.6",
# ]
# ///
"""
Generate static Python test case files for cvxopf from Pypower source data.

Writes one file per case to src/cvxopf/testcases/. Each file follows the
same format as the hand-written case9.py and case14.py.

For case9 and case14, the generated data is compared against the existing
hand-written files. A warning is raised if any array differs. The user is
then prompted to overwrite or keep the existing files.

Usage
-----
    uv run scripts/generate_testcases.py

Output
------
    src/cvxopf/testcases/case9.py    (may overwrite if user confirms)
    src/cvxopf/testcases/case14.py   (may overwrite if user confirms)
    src/cvxopf/testcases/case30.py
    src/cvxopf/testcases/case39.py
    src/cvxopf/testcases/case57.py
    src/cvxopf/testcases/case118.py
    src/cvxopf/testcases/case240.py
"""

import sys
import importlib
from pathlib import Path

import numpy as np
import textwrap

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "src" / "cvxopf" / "testcases"


# ---------------------------------------------------------------------------
# Pypower case functions
# ---------------------------------------------------------------------------

CASES = [
    ("case9", "pypower.case9", "case9"),
    ("case14", "pypower.case14", "case14"),
    ("case30", "pypower.case30", "case30"),
    ("case39", "pypower.case39", "case39"),
    ("case57", "pypower.case57", "case57"),
    ("case118", "pypower.case118", "case118"),
    ("case9_dcline", "pypower.t.t_case9_dcline", "t_case9_dcline"),
    ("case30pwl", "pypower.case30pwl", "case30pwl"),
    # Fabricated: standard case9 topology with a mixed piecewise-linear /
    # polynomial gencost, transformed post-load by _fabricate_case9_pwl.
    ("case9_pwl", "pypower.case9", "case9"),
]

# Cases with existing hand-written files to check consistency against
EXISTING = {"case9", "case14"}


# ---------------------------------------------------------------------------
# Post-load fabrication transforms
# ---------------------------------------------------------------------------
#
# A few test cases are not faithful imports but deliberate modifications of a
# stock Pypower case, applied after loading and before source emission. Keyed
# by case name in FABRICATORS.


def _fabricate_case9_pwl(ppc: dict) -> dict:
    """Standard case9 topology with a mixed piecewise-linear / polynomial cost.

    Replaces case9's all-polynomial gencost with the mixed gencost lifted from
    ``t_case9_dcline`` (gens 0 and 2 piecewise-linear, gen 1 polynomial). This
    is the Pypower oracle case for cvxopf's PWL cost support: it exercises the
    MODEL=1 path while keeping at least one polynomial generator, so Pypower's
    ``opf_costfcn`` (which computes ``baseMVA * polycost(gencost[ipol], ...)``
    and raises ``TypeError`` on an empty polynomial-gen set under numpy 2.x)
    can still solve it. An all-PWL case such as ``case30pwl`` cannot be solved
    by ``pypower==5.1.19`` for that reason, so it is shipped as an example
    rather than a fixture-backed test.

    Both PWL curves are convex, so cvxopf reproduces them exactly (no convex
    hull approximation) and matches Pypower's solution.
    """
    ppc = dict(ppc)
    ppc["gencost"] = np.array(
        [
            [1, 0, 0, 4, 0, 0, 100, 2500, 200, 5500, 250, 7250],
            [2, 0, 0, 2, 24.035, -403.5, 0, 0, 0, 0, 0, 0],
            [1, 0, 0, 3, 0, 0, 200, 3000, 300, 5000, 0, 0],
        ],
        dtype=float,
    )
    return ppc


FABRICATORS = {
    "case9_pwl": _fabricate_case9_pwl,
}


# ---------------------------------------------------------------------------
# Array formatting
# ---------------------------------------------------------------------------


def _fmt_float(v: float) -> str:
    """Format a float for embedding in a numpy array literal."""
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    # Use repr to preserve full precision
    s = repr(float(v))
    return s


def _fmt_row(row: np.ndarray) -> str:
    return "[" + ", ".join(_fmt_float(v) for v in row) + "]"


def _fmt_array(arr: np.ndarray, indent: int = 8) -> str:
    """Format a 2D numpy array as a multi-line literal."""
    pad = " " * indent
    rows = [pad + _fmt_row(arr[i]) for i in range(arr.shape[0])]
    return "[\n" + ",\n".join(rows) + ",\n" + " " * (indent - 4) + "]"


def _fmt_1d_array(arr: np.ndarray, indent: int = 8) -> str:
    """Format a 1D numpy array as a single-line literal."""
    pad = " " * indent
    return "[" + ", ".join(_fmt_float(v) for v in arr) + "]"


# ---------------------------------------------------------------------------
# Source generation
# ---------------------------------------------------------------------------


def _pypower_source_url(name: str) -> str:
    return f"https://rwl.github.io/PYPOWER/api/pypower.{name}-module.html"


def _generate_source(name: str, ppc: dict) -> str:
    """Generate the Python source for a single case file."""
    has_areas = (
        "areas" in ppc
        and ppc["areas"] is not None
        and np.asarray(ppc["areas"]).size > 0
    )
    has_dcline = (
        "dcline" in ppc
        and ppc["dcline"] is not None
        and np.asarray(ppc["dcline"]).size > 0
    )

    bus_comment = (
        "# bus_i  type  Pd  Qd  Gs  Bs  area  Vm  Va  baseKV  zone  Vmax  Vmin"
    )
    gen_comment = "# bus  Pg  Qg  Qmax  Qmin  Vg  mBase  status  Pmax  Pmin  ..."
    branch_comment = "# fbus  tbus  r  x  b  rateA  rateB  rateC  ratio  angle  status  angmin  angmax"
    gencost_comment = (
        "# model  startup  shutdown  n  coefficients (highest power first) ... c0"
    )
    dcline_comment = "# fbus  tbus  status  Pf  Pt  Qf  Qt  Vf  Vt  Pmin  Pmax  QminF  QmaxF  QminT  QmaxT  loss0  loss1"
    dclinecost_comment = (
        "# model  startup  shutdown  n  coefficients (highest power first) ... c0"
    )

    bus = np.asarray(ppc["bus"], dtype=float)
    gen = np.asarray(ppc["gen"], dtype=float)
    branch = np.asarray(ppc["branch"], dtype=float)
    gencost = np.asarray(ppc["gencost"], dtype=float)

    nb = bus.shape[0]
    ng = gen.shape[0]
    nl = branch.shape[0]

    lines = []
    lines.append(f'"""')
    lines.append(f"Power flow data for {name} test case.")
    lines.append(f"Adapted from pypower implementation: {_pypower_source_url(name)}")
    lines.append(f'"""')
    lines.append(f"")
    lines.append(f"import numpy as np")
    lines.append(f"")
    lines.append(f"")
    lines.append(f"def {name}() -> dict:")
    lines.append(f'    """')
    lines.append(f"    Return power flow data for the {name} test case.")
    lines.append(f"")
    lines.append(f"    Returns")
    lines.append(f"    -------")
    lines.append(f"    ppc : dict")
    lines.append(f"        MATPOWER-format case dict with keys:")
    key_list = "version, baseMVA, bus, gen, branch"
    if has_areas:
        key_list += ", areas"
    key_list += ", gencost"
    if has_dcline:
        key_list += ", dcline, dclinecost"
    lines.append(f"        {key_list}.")
    lines.append(f"")
    lines.append(f"    Network summary")
    lines.append(f"    ---------------")
    lines.append(f"    Buses      : {nb}")
    lines.append(f"    Generators : {ng}")
    lines.append(f"    Branches   : {nl}")
    lines.append(f'    """')
    lines.append(f'    ppc = {{"version": "2"}}')
    lines.append(f"")
    lines.append(f'    ppc["baseMVA"] = {_fmt_float(float(ppc["baseMVA"]))}')
    lines.append(f"")
    lines.append(f"    {bus_comment}")
    lines.append(f'    ppc["bus"] = np.array({_fmt_array(bus)})')
    lines.append(f"")
    lines.append(f"    {gen_comment}")
    lines.append(f'    ppc["gen"] = np.array({_fmt_array(gen)})')
    lines.append(f"")
    lines.append(f"    {branch_comment}")
    lines.append(f'    ppc["branch"] = np.array({_fmt_array(branch)})')
    lines.append(f"")

    if has_areas:
        areas = np.asarray(ppc["areas"], dtype=float)
        if areas.ndim == 1:
            areas = areas.reshape(1, -1)
        lines.append(f'    ppc["areas"] = np.array({_fmt_array(areas)})')
        lines.append(f"")

    lines.append(f"    {gencost_comment}")
    lines.append(f'    ppc["gencost"] = np.array({_fmt_array(gencost)})')
    lines.append(f"")

    if has_dcline:
        dcline = np.asarray(ppc["dcline"], dtype=float)
        dclinecost = np.asarray(ppc["dclinecost"], dtype=float)
        lines.append(f"    {dcline_comment}")
        lines.append(f'    ppc["dcline"] = np.array({_fmt_array(dcline)})')
        lines.append(f"")
        lines.append(f"    {dclinecost_comment}")
        lines.append(f'    ppc["dclinecost"] = np.array({_fmt_array(dclinecost)})')
        lines.append(f"")

    lines.append(f"    return ppc")
    lines.append(f"")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Consistency check against existing hand-written files
# ---------------------------------------------------------------------------


def _check_consistency(name: str, ppc_new: dict) -> bool:
    """
    Compare generated data against the existing hand-written file.
    Returns True if consistent, False if any array differs.
    """
    # Dynamically import the existing hand-written module
    spec_path = CASES_DIR / f"{name}.py"
    if not spec_path.exists():
        return True  # no existing file, nothing to compare

    import importlib.util

    spec = importlib.util.spec_from_file_location(name, spec_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, name)
    ppc_old = fn()

    keys_to_check = ["baseMVA", "bus", "gen", "branch", "gencost"]
    if "areas" in ppc_new and ppc_new["areas"] is not None:
        keys_to_check.append("areas")
    if (
        "dcline" in ppc_new
        and ppc_new["dcline"] is not None
        and np.asarray(ppc_new["dcline"]).size > 0
    ):
        keys_to_check.extend(["dcline", "dclinecost"])

    all_match = True
    for key in keys_to_check:
        v_new = np.asarray(ppc_new.get(key, []), dtype=float)
        v_old = np.asarray(ppc_old.get(key, []), dtype=float)
        if v_new.shape != v_old.shape or not np.allclose(v_new, v_old, rtol=1e-9):
            print(f"  ⚠  {name}['{key}']: DIFFERS")
            if key == "baseMVA":
                print(f"     old={v_old}  new={v_new}")
            else:
                print(f"     old shape={v_old.shape}  new shape={v_new.shape}")
                diff_mask = ~np.isclose(v_new, v_old, rtol=1e-9)
                n_diff = int(diff_mask.sum())
                print(f"     {n_diff} element(s) differ")
            all_match = False

    if all_match:
        print(f"  ✓  {name}: consistent with existing hand-written file")

    return all_match


# ---------------------------------------------------------------------------
# Write file
# ---------------------------------------------------------------------------


def _write(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    print(f"  Written: {path.relative_to(REPO_ROOT)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"numpy version : {np.__version__}")
    print(f"Output dir    : {CASES_DIR.relative_to(REPO_ROOT)}")
    print()

    failed = False

    for name, module_path, fn_name in CASES:
        print(f"Processing {name} ...")

        # Load from Pypower
        mod = importlib.import_module(module_path)
        fn = getattr(mod, fn_name)
        ppc = fn()

        # Apply a post-load fabrication transform if this case has one
        if name in FABRICATORS:
            ppc = FABRICATORS[name](ppc)

        # Generate source
        source = _generate_source(name, ppc)
        dest = CASES_DIR / f"{name}.py"

        if name in EXISTING:
            consistent = _check_consistency(name, ppc)
            if not consistent:
                print(f"\n  Data differs from existing hand-written {name}.py.")
                answer = input(f"  Overwrite {dest.name}? [y/N] ").strip().lower()
                if answer == "y":
                    _write(dest, source)
                    print(f"  Note: if {name}.py was overwritten, review and")
                    print(f"  update tests/test_network.py and")
                    print(f"  tests/test_vs_pypower_reference.py accordingly.")
                else:
                    print(f"  Keeping existing {dest.name}.")
            else:
                print(f"  Skipping {dest.name} (no changes).")
        else:
            _write(dest, source)

        print()

    print("Done.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
