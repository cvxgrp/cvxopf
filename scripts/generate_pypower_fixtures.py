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

DC line handling (case9_dcline)
-------------------------------
Pypower's ``toggle_dcline`` is broken under numpy 2.x + a dict ppc across TWO
of its three userfcn stages: the ext2int gen/bus/cost build (float index
arrays, a ``ppc.gencost=`` attribute-set with a wrong ``np.zeros`` call, a
float ``nc`` in ``range()``, an off-by-one gencost pad width) AND the int2ext
results-restoration (a shape-mismatched ``zeros((ndc, 6))`` concatenation).
Monkeypatching that chain was attempted and abandoned -- each fix exposed the
next. Its middle 'formulation' stage (the terminal-coupling constraint) is,
however, clean.

We therefore build the oracle self-contained, touching none of the broken
stages:
  * ``_dcline_to_gens`` converts each in-service DC line to a pair of dummy
    generators (validated gen/bus-equivalent to a real, float-coercion-patched
    ``toggle_dcline`` run in ``scripts/_probe_dcline_transform.py``, Gate
    0b-iii);
  * ``_make_coupling_userfcn`` re-adds pypower's clean terminal-coupling
    constraint ``(1-L1)*Pgf + Pgt == -L0/baseMVA`` as our own 'formulation'
    userfcn, locating dummy-gen columns via ``order['gen']['i2e']``;
  * ``dclinecost`` is dropped before solving, matching pypower's own
    ``t_dcline.py`` (which deletes it) -- so the DC lines are zero-cost
    dispatchable resources and the ext2int cost branch is never needed;
  * the solved terminal quantities are cross-checked against pypower's
    hardcoded expected array from ``t_dcline.py`` (``_check_dcline_against_pypower``).

The resulting fixture is still an APPROXIMATE oracle for cvxopf's HVDC MVP:
pypower's AC dcline carries reactive terminal injections (QF/QT) and models
``loss0`` (row 0), both of which the unity-PF, proportional-loss MVP omits.
See Gate 6b in the milestone plan. NOTE: the returned Pg/Qg include the dummy
DC-line terminal gens (real gens first, then from-gens, then to-gens).

Output
------
tests/fixtures/case9_pypower_reference.json
tests/fixtures/case14_pypower_reference.json
tests/fixtures/case57_pypower_reference.json
tests/fixtures/case9_dcline_pypower_reference.json

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
from scipy.sparse import csr_matrix
from pypower.api import case9, case14, case57, runopf
from pypower.ext2int import ext2int
from pypower.makeYbus import makeYbus
from pypower.idx_bus import VM, VA, BUS_I, BUS_TYPE, PV, REF
from pypower.idx_gen import (
    PG,
    QG,
    GEN_BUS,
    MBASE,
    GEN_STATUS,
    PMAX,
    PMIN,
    QMAX,
    QMIN,
    VG,
)
from pypower.idx_dcline import c
from pypower.isload import isload
from pypower.add_userfcn import add_userfcn
from pypower import ppoption
from pypower.t.t_case9_dcline import t_case9_dcline


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"


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


def _extract_result(result, case_name: str) -> dict:
    """Build the fixture dict from a solved Pypower OPF result."""
    converged = bool(result["success"])
    status = "optimal" if converged else "failed"

    if not converged:
        print(f"FAILED (Pypower did not converge for {case_name})")
        return dict(
            case=case_name,
            solver="pypower-5.1.19",
            status=status,
            objective=None,
            Pg=None,
            Qg=None,
            Vm=None,
            Va_deg=None,
        )

    objective = float(result["f"])
    Pg = result["gen"][:, PG].tolist()  # MW
    Qg = result["gen"][:, QG].tolist()  # MVAr
    Vm = result["bus"][:, VM].tolist()  # p.u.
    Va_deg = result["bus"][:, VA].tolist()  # degrees

    print(f"OK  (f = {objective:.4f} $/hr)")

    return dict(
        case=case_name,
        solver="pypower-5.1.19",
        status=status,
        objective=objective,
        Pg=Pg,
        Qg=Qg,
        Vm=Vm,
        Va_deg=Va_deg,
    )


def _run_case(case_fn, case_name: str) -> dict:
    """Run Pypower AC-OPF on a single case and return a results dict."""
    ppc = case_fn()
    ppopt = _make_ppopt()

    print(f"  Running Pypower AC-OPF for {case_name} ...", end=" ", flush=True)
    result = runopf(ppc, ppopt)
    return _extract_result(result, case_name)


def _case9_pwl():
    """Standard case9 with a mixed piecewise-linear / polynomial gencost.

    Built independently from Pypower's own ``case9`` (NOT from cvxopf's
    generated ``case9_pwl`` case file) so the fixture is an independent oracle
    -- mirrors the deliberate case-file / fixture path separation used for
    ``case9_dcline`` (see scripts/README.md). The gencost is the same mixed
    MODEL=1/MODEL=2 array ``generate_testcases.py::_fabricate_case9_pwl``
    writes: gens 0 and 2 piecewise-linear, gen 1 polynomial. At least one
    polynomial gen is required or Pypower's ``opf_costfcn`` raises on the
    empty polynomial-gen set (numpy 2.x); this is why an all-PWL case is not
    fixture-backed. Both PWL curves are convex.
    """
    ppc = case9()
    ppc["gencost"] = np.array(
        [
            [1, 0, 0, 4, 0, 0, 100, 2500, 200, 5500, 250, 7250],
            [2, 0, 0, 2, 24.035, -403.5, 0, 0, 0, 0, 0, 0],
            [1, 0, 0, 3, 0, 0, 200, 3000, 300, 5000, 0, 0],
        ],
        dtype=float,
    )
    return ppc


# ---------------------------------------------------------------------------
# Hand-built dcline -> dummy-generator transform (replaces toggle_dcline)
# ---------------------------------------------------------------------------
#
# See the "DC line handling" section of the module docstring for why this
# exists (numpy-2.x breakage in toggle_dcline). Validated gen/bus-equivalent
# to a real toggle_dcline run in scripts/_probe_dcline_transform.py (0b-iii).


def _dcline_to_gens(ppc):
    """Hand-built equivalent of Pypower's ``userfcn_dcline_ext2int`` gen build.

    Operates in EXTERNAL indexing (the case as loaded): builds two dummy gens
    per in-service DC line (a "from" extraction gen and a "to" injection gen),
    sets both terminal buses to PV, and appends the sign-flipped from-gen cost
    plus a zero-cost to-gen row to ``gencost``. Returns a NEW ppc dict with the
    ``dcline``/``dclinecost`` tables removed, so plain ``runopf`` sees only
    standard MATPOWER tables (no dcline machinery -> no numpy-2.x bug).

    Faithful to toggle_dcline.py, but external-indexed (GEN_BUS holds external
    bus IDs; PV set on external buses) so Pypower's own ext2int remaps
    consistently. Validated gen/bus-equivalent to real pypower in
    scripts/_probe_dcline_transform.py::validate (Gate 0b-iii).

    One DELIBERATE divergence from real pypower: the from-gen cost sign-flip
    acts on the linear coefficient c1, which is physically correct
    (PG_from = -Pf => cost(Pf) = c1*Pf + c0 == -c1*PG_from + c0). Real pypower's
    ``temp[range(nc, 0, -2)]`` flips the constant c0 instead -- a genuine
    pypower bug (its cost branch cannot even run on numpy 2.x). The fixture
    therefore uses the correct flip by design; treat it as an approximate
    oracle (also dropping loss0) when consuming it.
    """
    ppc = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in ppc.items()}

    dcline = ppc["dcline"]
    on = dcline[:, c["BR_STATUS"]] > 0
    dc = dcline[on, :].copy()
    ndc = dc.shape[0]
    ncol = ppc["gen"].shape[1]

    # PT consistency (loss law): Pt = Pf - (loss0 + loss1*Pf)
    dc[:, c["PT"]] = dc[:, c["PF"]] - (
        dc[:, c["LOSS0"]] + dc[:, c["LOSS1"]] * dc[:, c["PF"]]
    )

    fg = np.zeros((ndc, ncol))
    fg[:, MBASE] = 100
    fg[:, GEN_STATUS] = dc[:, c["BR_STATUS"]]
    fg[:, PMIN] = -np.inf
    fg[:, PMAX] = np.inf
    tg = fg.copy()

    fg[:, GEN_BUS] = dc[:, c["F_BUS"]]  # external from-bus id
    tg[:, GEN_BUS] = dc[:, c["T_BUS"]]  # external to-bus id
    fg[:, PG] = -dc[:, c["PF"]]
    tg[:, PG] = dc[:, c["PT"]]
    fg[:, QG] = dc[:, c["QF"]]
    tg[:, QG] = dc[:, c["QT"]]
    fg[:, VG] = dc[:, c["VF"]]
    tg[:, VG] = dc[:, c["VT"]]

    k = dc[:, c["PMIN"]] >= 0
    fg[k, PMAX] = -dc[k, c["PMIN"]]
    k = dc[:, c["PMAX"]] >= 0
    fg[k, PMIN] = -dc[k, c["PMAX"]]
    k = dc[:, c["PMIN"]] < 0
    tg[k, PMIN] = dc[k, c["PMIN"]]
    k = dc[:, c["PMAX"]] < 0
    tg[k, PMAX] = dc[k, c["PMAX"]]

    fg[:, QMIN] = dc[:, c["QMINF"]]
    fg[:, QMAX] = dc[:, c["QMAXF"]]
    tg[:, QMIN] = dc[:, c["QMINT"]]
    tg[:, QMAX] = dc[:, c["QMAXT"]]

    # Dispatchable-load fudge (verified against real toggle_dcline, Gate 0b-iii):
    # pypower nudges PMAX to -1e-6 on dummy gens that look like loads
    # (PMIN<0, PMAX==0) so they don't trigger constant-power-factor constraints.
    fg[isload(fg), PMAX] = -1e-6
    tg[isload(tg), PMAX] = -1e-6

    # set terminal buses to PV (external ids), preserving the ref bus
    bus = ppc["bus"]
    from_ids = dc[:, c["F_BUS"]].astype(int)
    to_ids = dc[:, c["T_BUS"]].astype(int)
    refmask = bus[:, BUS_TYPE] == REF
    id_to_row = {int(b): i for i, b in enumerate(bus[:, BUS_I])}
    for bid in list(from_ids) + list(to_ids):
        row = id_to_row[int(bid)]
        if not refmask[row]:
            bus[row, BUS_TYPE] = PV

    ppc["gen"] = np.r_[ppc["gen"], fg, tg]

    # gencost: sign-flip from-gen cost, zero-cost to-gen. dclinecost rows are
    # model-2 polynomial, same layout as gencost.
    if "dclinecost" in ppc and len(ppc["dclinecost"]) > 0:
        dcc = ppc["dclinecost"][on, :].copy()
        gencost = ppc["gencost"]
        ngcc = gencost.shape[1]
        ndccc = dcc.shape[1]
        ccc = max(ngcc, ndccc)
        if ccc > ngcc:
            gencost = np.c_[gencost, np.zeros((gencost.shape[0], ccc - ngcc))]
        NCOST = 3  # model, startup, shutdown, n, coeffs...
        from_rows = []
        for kk in range(ndc):
            nc = int(dcc[kk, NCOST])
            temp = dcc[kk, NCOST : NCOST + nc + 1].copy()
            # Flip sign on odd-power coefficients (the linear term) -- see the
            # DELIBERATE-divergence note in this function's docstring.
            temp[range(nc - 1, -1, -2)] = -temp[range(nc - 1, -1, -2)]
            pad = np.zeros(ccc - (NCOST + 1) - len(temp))
            row = np.concatenate([dcc[kk, : NCOST + 1], temp, pad])
            from_rows.append(row)
        fgc = np.array(from_rows)
        tgc = np.tile(
            np.concatenate([np.array([2, 0, 0, 2]), np.zeros(ccc - 4)]),
            (ndc, 1),
        )
        ppc["gencost"] = np.r_[gencost, fgc, tgc]
    else:
        # No dcline cost (the fixture path -- matching pypower's own t_dcline.py,
        # which deletes dclinecost before solving). Append 2*ndc zero-cost rows
        # (pypower's havecost=False else branch) so gen and gencost stay aligned.
        ngcc = ppc["gencost"].shape[1]
        zc = np.tile(
            np.concatenate([np.array([2, 0, 0, 2]), np.zeros(ngcc - 4)]),
            (2 * ndc, 1),
        )
        ppc["gencost"] = np.r_[ppc["gencost"], zc]

    del ppc["dcline"]
    if "dclinecost" in ppc:
        del ppc["dclinecost"]
    return ppc


def _make_coupling_userfcn(orig_ppc):
    """Build a 'formulation'-stage userfcn enforcing the DC-line terminal
    coupling on the dummy generators:

        (1 - L1) * Pgf + Pgt == -L0 / baseMVA

    This is the constraint pypower's own ``userfcn_dcline_formulation`` adds
    (the clean, unbroken stage of ``toggle_dcline``). We register our own copy
    so we never touch the broken ``ext2int``/``int2ext`` stages. Unlike
    pypower's version -- which assumes the dummy gens are the last ``2*ndc``
    rows -- we locate each dummy gen's internal Pg column via
    ``order['gen']['i2e']``, because ``_dcline_to_gens`` appends the dummies
    before ``ext2int`` reorders all gens by bus (so they are not last).
    """
    dcline = orig_ppc["dcline"]
    on = dcline[:, c["BR_STATUS"]] > 0
    L0 = dcline[on, c["LOSS0"]].copy()
    L1 = dcline[on, c["LOSS1"]].copy()
    ndc = int(on.sum())
    ng_orig = orig_ppc["gen"].shape[0]

    def _coupling(om, args):
        ppc = om.get_ppc()
        i2e = ppc["order"]["gen"]["i2e"].astype(int)  # external row -> internal col
        ng_int = ppc["gen"].shape[0]
        rows, cols, data = [], [], []
        for kk in range(ndc):
            from_col = i2e[ng_orig + kk]  # from-gen (external row ng_orig+kk)
            to_col = i2e[ng_orig + ndc + kk]  # to-gen
            rows += [kk, kk]
            cols += [from_col, to_col]
            data += [1.0 - L1[kk], 1.0]
        Adc = csr_matrix((data, (rows, cols)), shape=(ndc, ng_int))
        nL0 = -L0 / ppc["baseMVA"]
        om.add_constraints("dcline", Adc, nL0, nL0, ["Pg"])
        return om

    return _coupling


# pypower's own golden reference for t_case9_dcline, copied from
# pypower/t/t_dcline.py (the "AC OPF (with DC lines)" expected block, in-service
# rows only), columns [PF, PT, QF, QT, VF, VT]. Used as a self-check that our
# self-contained Option-A solve reproduces pypower's published answer.
_CASE9_DCLINE_EXPECTED = np.array(
    [
        [10.0, 8.9, -10.0, 10.0, 1.0674, 1.0935],
        [2.2776, 2.2776, 0.0, 0.0, 1.0818, 1.0665],
        [10.0, 9.5, 0.0563, -10.0, 1.0778, 1.0665],
    ]
)


def _check_dcline_against_pypower(result, orig_ppc):
    """Assert the solved dummy-gen terminal quantities reproduce pypower's
    hardcoded expected array (atol=1e-3, absorbing solver tolerance)."""
    g = result["gen"]
    bus = result["bus"]
    ng_orig = orig_ppc["gen"].shape[0]
    on_rows = np.flatnonzero(orig_ppc["dcline"][:, c["BR_STATUS"]] > 0)
    ndc = len(on_rows)
    id_to_row = {int(b): i for i, b in enumerate(bus[:, BUS_I])}
    got = []
    for kk, dcrow in enumerate(on_rows):
        fb = int(orig_ppc["dcline"][dcrow, c["F_BUS"]])
        tb = int(orig_ppc["dcline"][dcrow, c["T_BUS"]])
        fromg = g[ng_orig + kk]
        tog = g[ng_orig + ndc + kk]
        got.append(
            [
                -fromg[PG],
                tog[PG],
                fromg[QG],
                tog[QG],
                bus[id_to_row[fb], VM],
                bus[id_to_row[tb], VM],
            ]
        )
    got = np.array(got)
    maxdiff = float(np.abs(got - _CASE9_DCLINE_EXPECTED).max())
    if not np.allclose(got, _CASE9_DCLINE_EXPECTED, atol=1e-3):
        raise AssertionError(
            f"case9_dcline solve does not match pypower's expected array "
            f"(max abs diff {maxdiff:.2e}). Terminal [PF,PT,QF,QT,VF,VT]:\n{got}"
        )
    print(
        f"  [self-check] terminal flows match pypower t_dcline.py "
        f"(max abs diff {maxdiff:.2e})"
    )


def _run_dcline_case(case_fn, case_name: str) -> dict:
    """Run Pypower AC-OPF on a case with a dcline table (self-contained solve).

    The DC lines are converted to pairs of dummy generators by the hand-built
    ``_dcline_to_gens`` transform, coupled by a custom 'formulation' userfcn
    (``_make_coupling_userfcn``), and solved with a plain ``runopf`` -- no
    ``toggle_dcline`` (whose ext2int/int2ext stages are broken under numpy 2.x).

    ``dclinecost`` is dropped before the transform, matching pypower's own
    ``t_dcline.py`` (which deletes it), so the DC lines are zero-cost
    dispatchable resources. The solved terminal quantities are cross-checked
    against pypower's hardcoded expected array. Same result schema as
    ``_run_case``, but the returned Pg/Qg include the dummy DC-line terminal
    gens (real gens first, then from-gens, then to-gens).
    """
    ppopt = _make_ppopt()

    orig = case_fn()
    if "dclinecost" in orig:
        del orig["dclinecost"]  # zero-cost dcline, matching pypower t_dcline.py

    print(
        f"  Running Pypower AC-OPF for {case_name} (dcline->gens) ...",
        end=" ",
        flush=True,
    )
    ppc = _dcline_to_gens(orig)
    add_userfcn(ppc, "formulation", _make_coupling_userfcn(orig))
    result = runopf(ppc, ppopt)

    data = _extract_result(result, case_name)
    if data["status"] == "optimal":
        _check_dcline_against_pypower(result, orig)
    return data


# ---------------------------------------------------------------------------
# Ybus agreement fixture (case9_dcline)
# ---------------------------------------------------------------------------
#
# Pins the load-bearing HVDC assumption that DC lines contribute NOTHING to
# Ybus (they are modelled as nodal injections, not admittance branches).
# makeYbus reads only the bus/branch tables, so the dcline/dclinecost tables
# are dropped first -- their presence must not change Ybus, which is exactly
# what the consuming test asserts against cvxopf's own build.data["Ybus"].
#
# makeYbus requires INTERNAL indexing, so ext2int is run first (plain ext2int
# is unaffected by the numpy-2.x toggle_dcline breakage -- that bug lives in
# the dcline userfcns, which are absent once the dcline table is removed).
# The fixture records the external bus IDs in Pypower's internal order so the
# consumer can align cvxopf's internal-indexed Ybus by external ID.


def _run_dcline_ybus(case_fn, case_name: str) -> dict:
    """Compute Pypower's Ybus for a dcline case and return a fixture dict.

    Drops the ``dcline``/``dclinecost`` tables (irrelevant to ``makeYbus``),
    runs ``ext2int``, and calls ``makeYbus``. The dense complex Ybus is split
    into real/imag nested lists; ``bus_ids`` holds the external bus IDs in the
    internal row/column order, so the consuming test can reindex.
    """
    ppc = case_fn()
    for key in ("dcline", "dclinecost"):
        if key in ppc:
            del ppc[key]

    print(f"  Building Pypower Ybus for {case_name} ...", end=" ", flush=True)
    ppc_int = ext2int(ppc)
    Ybus, _Yf, _Yt = makeYbus(ppc_int["baseMVA"], ppc_int["bus"], ppc_int["branch"])
    Ybus = np.asarray(Ybus.todense())

    # external bus IDs in internal order: ext2int stores the original external
    # bus id vector on order["bus"]["e2i"]/["i2e"]; i2e maps internal row -> ext.
    i2e = ppc_int["order"]["bus"]["i2e"].astype(int)
    bus_ids = i2e.tolist()

    print(f"OK  (nb = {Ybus.shape[0]})")
    return dict(
        case=case_name,
        solver="pypower-5.1.19",
        quantity="Ybus",
        bus_ids=bus_ids,
        Ybus_real=np.real(Ybus).tolist(),
        Ybus_imag=np.imag(Ybus).tolist(),
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
        (case9, "case9", "case9_pypower_reference.json"),
        (case14, "case14", "case14_pypower_reference.json"),
        (case57, "case57", "case57_pypower_reference.json"),
        (_case9_pwl, "case9_pwl", "case9_pwl_pypower_reference.json"),
    ]

    failed = False
    for case_fn, case_name, filename in cases:
        data = _run_case(case_fn, case_name)
        if data["status"] != "optimal":
            failed = True
        _write_fixture(data, FIXTURES / filename)
        print()

    # case9_dcline: DC lines are converted to dummy generators by the hand-built
    # _dcline_to_gens transform (toggle_dcline is broken under numpy 2.x), then
    # solved with plain runopf. See the module docstring "DC line handling".
    data = _run_dcline_case(t_case9_dcline, "case9_dcline")
    if data["status"] != "optimal":
        failed = True
    _write_fixture(data, FIXTURES / "case9_dcline_pypower_reference.json")
    print()

    # case9_dcline Ybus: pins that DC lines contribute nothing to Ybus. Not a
    # solve, so it cannot "fail to converge"; written unconditionally.
    ybus_data = _run_dcline_ybus(t_case9_dcline, "case9_dcline")
    _write_fixture(ybus_data, FIXTURES / "case9_dcline_ybus_pypower_reference.json")
    print()

    if failed:
        print(
            "ERROR: one or more cases did not converge. "
            "Fixture files may be incomplete.",
            file=sys.stderr,
        )
        return 1

    print("All fixtures generated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
