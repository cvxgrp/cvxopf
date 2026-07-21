# /// script
# requires-python = ">=3.13"
# dependencies = ["pypower==5.1.19", "numpy==2.2.6", "scipy==1.18.0"]
# ///
"""Throwaway probe: validate a hand-built dcline->dummy-gen transform against
Pypower's own ``toggle_dcline`` output (Milestone 7, Gate 0b-iii).

NOT committed as part of the fixture pipeline and NOT imported anywhere. Run
directly (``uv run scripts/_probe_dcline_transform.py``) to prove that
``_dcline_to_gens`` reproduces Pypower's gen/bus transformation before wiring
the hand-built path into ``generate_pypower_fixtures.py``.

Why this exists: ``userfcn_dcline_ext2int`` is broken under numpy 2.x + a dict
ppc (float index arrays; ``ppc.gencost=`` attr-set + ``zeros(a,b)``; float
``nc`` in ``range()``; off-by-one gencost pad width). Rather than coax the
broken original, we reproduce its *intent* by hand in ``_dcline_to_gens`` and
prove equivalence in ``validate()``.

0b-iii validation results (see ``validate()``):
  * BUS table  -- EXACT match vs real (float-coercion-patched) pypower.
  * GEN table  -- EXACT match (as a row multiset; ext2int reorders gens),
                  including the ``isload`` PMAX=-1e-6 dispatchable-load fudge.
  * GENCOST    -- DELIBERATE divergence: real pypower's sign-flip
                  ``temp[range(nc, 0, -2)]`` flips the wrong coefficient (the
                  constant ``c0`` instead of the linear ``c1``) -- a genuine
                  pypower bug. The hand-built flips ``c1``, which is physically
                  correct (``PG_from = -Pf`` => ``cost = -c1*PG_from + c0``).
                  This is documented and NOT asserted against pypower; the
                  committed fixture (0b-iv) uses the correct flip by design.
"""

import numpy as np
from numpy import zeros, r_, inf
from numpy import flatnonzero as find
from pypower.t.t_case9_dcline import t_case9_dcline
from pypower.idx_dcline import c
from pypower.idx_gen import (
    GEN_BUS, PG, QG, QMAX, QMIN, VG, MBASE, GEN_STATUS, PMAX, PMIN,
)
from pypower.idx_bus import BUS_I, BUS_TYPE, PV, REF
from pypower.isload import isload


def _dcline_to_gens(ppc):
    """Hand-built equivalent of Pypower's ``userfcn_dcline_ext2int`` gen build.

    Operates in EXTERNAL indexing (the case as loaded): builds two dummy gens
    per in-service DC line (a "from" extraction gen and a "to" injection gen),
    sets both terminal buses to PV, and appends the sign-flipped from-gen cost
    plus a zero-cost to-gen row to ``gencost``. Returns a NEW ppc dict with the
    ``dcline``/``dclinecost`` tables removed, so plain ``runopf`` sees only
    standard MATPOWER tables (no dcline machinery -> no numpy-2.x bug).

    Faithful to toggle_dcline.py lines 120-233, but external-indexed (GEN_BUS
    holds external bus IDs; PV set on external buses) so Pypower's own ext2int
    remaps consistently. Verified gen/bus-equivalent to real pypower in
    ``validate()`` (Gate 0b-iii).
    """
    ppc = {k: (v.copy() if isinstance(v, np.ndarray) else v)
           for k, v in ppc.items()}

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

    fg[:, GEN_BUS] = dc[:, c["F_BUS"]]      # external from-bus id
    tg[:, GEN_BUS] = dc[:, c["T_BUS"]]      # external to-bus id
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
    # Without this, the bus-5 from-gen (PMAX==0) diverges from pypower.
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
            # Flip sign on odd-power coefficients (the linear term), because
            # PG_from = -Pf so cost(Pf)=c1*Pf+c0 == -c1*PG_from + c0.
            # NOTE (0b-iii): this DELIBERATELY diverges from real pypower, whose
            # ``temp[range(nc, 0, -2)]`` flips the wrong element -- the constant
            # c0 instead of the linear c1 -- a genuine pypower bug. We flip c1,
            # which is physically correct. See validate() + module docstring.
            temp[range(nc - 1, -1, -2)] = -temp[range(nc - 1, -1, -2)]
            # pypower's own pad math here is off-by-one (produces ccc+1-wide
            # rows appended to a ccc-wide gencost -- a further bug on this path).
            # We pad to a consistent ccc total: (NCOST+1) header + temp + pad.
            pad = np.zeros(ccc - (NCOST + 1) - len(temp))
            row = np.concatenate([dcc[kk, : NCOST + 1], temp, pad])
            from_rows.append(row)
        fgc = np.array(from_rows)
        tgc = np.tile(
            np.concatenate([np.array([2, 0, 0, 2]), np.zeros(ccc - 4)]),
            (ndc, 1),
        )
        ppc["gencost"] = np.r_[gencost, fgc, tgc]

    del ppc["dcline"]
    if "dclinecost" in ppc:
        del ppc["dclinecost"]
    return ppc


def _patched_ext2int(ppc, args):
    """Exact copy of pypower's ``userfcn_dcline_ext2int`` GEN/BUS build with
    ONLY value-preserving ``.astype(int)`` coercions at the numpy-2.x
    float-index sites. The buggy/non-functional gencost branch is avoided by
    deleting ``dclinecost`` before the call (``havecost=False`` -> zero-cost
    else path), so this reference validates the GEN and BUS tables only.

    Registered onto ``pypower.toggle_dcline`` in ``validate()`` so the real
    ext2int pipeline drives it -- the reference is genuine pypower logic, made
    runnable, not a second reimplementation.
    """
    cc = c
    ppc["order"]["ext"]["dcline"] = ppc["dcline"]
    ppc["order"]["ext"]["status"] = {}
    ppc["order"]["ext"]["status"]["on"] = find(ppc["dcline"][:, cc["BR_STATUS"]] > 0)
    ppc["order"]["ext"]["status"]["off"] = find(ppc["dcline"][:, cc["BR_STATUS"]] <= 0)
    dc = ppc["dcline"][ppc["order"]["ext"]["status"]["on"], :]
    ndc = dc.shape[0]
    o = ppc["order"]
    dc[:, cc["F_BUS"]] = o["bus"]["e2i"][dc[:, cc["F_BUS"]].astype(int)]   # PATCH
    dc[:, cc["T_BUS"]] = o["bus"]["e2i"][dc[:, cc["T_BUS"]].astype(int)]   # PATCH
    ppc["dcline"] = dc
    dc[:, cc["PT"]] = dc[:, cc["PF"]] - (
        dc[:, cc["LOSS0"]] + dc[:, cc["LOSS1"]] * dc[:, cc["PF"]]
    )
    fg = zeros((ndc, ppc["gen"].shape[1]))
    fg[:, MBASE] = 100
    fg[:, GEN_STATUS] = dc[:, cc["BR_STATUS"]]
    fg[:, PMIN] = -inf
    fg[:, PMAX] = inf
    tg = fg.copy()
    fg[:, GEN_BUS] = dc[:, cc["F_BUS"]]
    tg[:, GEN_BUS] = dc[:, cc["T_BUS"]]
    fg[:, PG] = -dc[:, cc["PF"]]
    tg[:, PG] = dc[:, cc["PT"]]
    fg[:, QG] = dc[:, cc["QF"]]
    tg[:, QG] = dc[:, cc["QT"]]
    fg[:, VG] = dc[:, cc["VF"]]
    tg[:, VG] = dc[:, cc["VT"]]
    k = find(dc[:, cc["PMIN"]] >= 0)
    if len(k) > 0:
        fg[k, PMAX] = -dc[k, cc["PMIN"]]
    k = find(dc[:, cc["PMAX"]] >= 0)
    if len(k) > 0:
        fg[k, PMIN] = -dc[k, cc["PMAX"]]
    k = find(dc[:, cc["PMIN"]] < 0)
    if len(k) > 0:
        tg[k, PMIN] = dc[k, cc["PMIN"]]
    k = find(dc[:, cc["PMAX"]] < 0)
    if len(k) > 0:
        tg[k, PMAX] = dc[k, cc["PMAX"]]
    fg[:, QMIN] = dc[:, cc["QMINF"]]
    fg[:, QMAX] = dc[:, cc["QMAXF"]]
    tg[:, QMIN] = dc[:, cc["QMINT"]]
    tg[:, QMAX] = dc[:, cc["QMAXT"]]
    fg[isload(fg), PMAX] = -1e-6
    tg[isload(tg), PMAX] = -1e-6
    refbus = find(ppc["bus"][:, BUS_TYPE] == REF)
    ppc["bus"][dc[:, cc["F_BUS"]].astype(int), BUS_TYPE] = PV   # PATCH
    ppc["bus"][dc[:, cc["T_BUS"]].astype(int), BUS_TYPE] = PV   # PATCH
    ppc["bus"][refbus, BUS_TYPE] = REF
    ppc["gen"] = r_[ppc["gen"], fg, tg]
    return ppc


def _sorted_rows(a):
    """Canonical row order so gen tables compare as multisets (ext2int reorders
    gens by bus, and the two paths append the dummy gens at different stages)."""
    return a[np.lexsort(a.T[::-1])]


def validate():
    """Gate 0b-iii: prove ``_dcline_to_gens`` matches real (float-coercion-
    patched) pypower on the GEN and BUS tables, and document the deliberate
    GENCOST divergence. Raises AssertionError on any GEN/BUS mismatch.
    """
    import pypower.toggle_dcline as td
    from pypower.ext2int import ext2int

    # Real reference: drop dclinecost so the userfcn takes the havecost=False
    # path, isolating the value-preserving GEN/BUS build from the buggy cost
    # branch. Register the float-coercion-patched userfcn so real ext2int runs.
    real = t_case9_dcline()
    del real["dclinecost"]
    td.userfcn_dcline_ext2int = _patched_ext2int
    td.toggle_dcline(real, "on")
    real = ext2int(real)

    # Hand-built (external indexing) -> standard ext2int.
    mine = ext2int(_dcline_to_gens(t_case9_dcline()))

    bus_ok = np.allclose(real["bus"], mine["bus"], equal_nan=True)
    gen_ok = np.allclose(
        _sorted_rows(real["gen"]), _sorted_rows(mine["gen"]), equal_nan=True
    )
    print("Gate 0b-iii validation")
    print("  BUS  exact match     :", bus_ok)
    print("  GEN  multiset match  :", gen_ok)
    assert bus_ok, "BUS table mismatch vs real pypower"
    assert gen_ok, "GEN table mismatch vs real pypower"
    print("  GENCOST              : deliberate divergence -- pypower flips c0 "
          "(bug); hand-built flips c1 (correct). Not asserted; see docstring.")
    print("  RESULT               : PASSED")


def main():
    validate()

    mine = _dcline_to_gens(t_case9_dcline())
    print("\nhand-built gen shape :", mine["gen"].shape)
    print("hand-built gencost   :", mine["gencost"].shape)
    print("buses set PV         :",
          np.where(mine["bus"][:, BUS_TYPE] == PV)[0].tolist())
    ng0 = 3  # case9 has 3 real gens
    print("\nappended dummy gens (GEN_BUS, PG, QG, PMIN, PMAX, QMIN, QMAX):")
    for r in mine["gen"][ng0:, :]:
        print("  ", r[[GEN_BUS, PG, QG, PMIN, PMAX, QMIN, QMAX]])
    print("\nappended gencost rows:")
    for r in mine["gencost"][ng0:, :]:
        print("  ", r)


if __name__ == "__main__":
    main()
