"""
HVDC transmission link model for AC-OPF and DC-OPF.

Models each HVDC link as a pair of generator-like signed nodal injections
(p_in at from_bus, p_out at to_bus). Convention B: positive = injection into
the grid. Both terminals enter the nodal balance with '+'.

The sole internal representation is a per-step box p_in ∈ [p_min_t, p_max_t].
The four named modes (scheduled/band/downward/free) are upstream helpers that
compute this box; no mode branching occurs inside the CVXPY component methods.

Loss model: affine branch selected pre-construction from the box's zero-crossing:
  p_min_t[k] >= 0  (from->to):  p_out[k] = -(1 - loss_frac[k]) * p_in[k]
  p_max_t[k] <= 0  (to->from):  p_out[k] = -(1 + loss_frac[k]) * p_in[k]
  zero-straddling:               p_out[k] = -p_in[k]  (lossless, UserWarning)

Import chain:
  hvdc.py  →  cvxpy, numpy, warnings, dataclasses (no cvxopf imports)
  ac_problem.py → hvdc.py
  dc_problem.py → hvdc.py
  problem.py    → hvdc.py  (re-exports HVDCLink, hvdc_from_dcline)
"""

from __future__ import annotations

from dataclasses import dataclass, field
import warnings

import numpy as np
import cvxpy as cp


# ---------------------------------------------------------------------------
# HVDCLink dataclass
# ---------------------------------------------------------------------------

_VALID_MODES = {"scheduled", "band", "downward", "free"}


@dataclass
class HVDCLink:
    """
    Parameters for a single HVDC transmission link.

    An HVDC link is modelled as a pair of signed nodal injections p_in
    (at from_bus) and p_out (at to_bus). Both are cp.Variable objects for
    every box shape, including degenerate (scheduled) boxes. The box
    p_in ∈ [p_min_t, p_max_t] is the sole internal representation; the
    four modes are upstream helpers that fill the box.

    Attributes
    ----------
    from_bus : int
        Sending-terminal bus ID (external MATPOWER numbering).
    to_bus : int
        Receiving-terminal bus ID (external MATPOWER numbering).
    p_max_mw : float
        Upper bound on the from-bus injection p_in (MW). Must be > 0.
    p_min_mw : float or None
        Lower bound on p_in (MW). None applies a per-mode default:
          band/free: -p_max_mw (symmetric); downward: 0; scheduled: p_scheduled_mw.
    p_scheduled_mw : float
        Sending-terminal setpoint (MW). In 'scheduled' mode this produces a
        degenerate box [p_sched, p_sched] (pinned by coincident bounds, NOT a
        separate equality). In other modes it is a non-binding reference.
    bandwidth_mw : float
        Half-width of the band around p_scheduled_mw for 'band' mode (MW >= 0).
    mode : str
        One of 'scheduled', 'band', 'downward', 'free'. Controls how
        _hvdc_static_box maps link fields to (p_min, p_max). Default 'band'.
    loss_percent : float
        Proportional loss as a percentage (0–100). loss_frac = loss_percent/100.
        Fixed converter loss (LOSS0) is not modelled; see Milestone 15.
    cost_coeffs : tuple of float
        Polynomial cost (c0, c1, c2) in lowest-first order. Cost acts on the
        transfer magnitude: c2*|p_in|^2 + c1*|p_in| + c0. Default (0,0,0).
    """
    from_bus:        int
    to_bus:          int
    p_max_mw:        float
    p_min_mw:        float | None   = None
    p_scheduled_mw:  float          = 0.0
    bandwidth_mw:    float          = 0.0
    mode:            str            = "band"
    loss_percent:    float          = 0.0
    cost_coeffs:     tuple          = field(default_factory=lambda: (0.0, 0.0, 0.0))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_hvdc(links: list, ext_bus_ids: set) -> None:
    """
    Validate a list of HVDCLink objects.

    Raises
    ------
    ValueError
        If any link fails a check, with an indexed message.
    """
    if not links:
        return
    for i, lnk in enumerate(links):
        if lnk.from_bus == lnk.to_bus:
            raise ValueError(
                f"HVDC link {i}: from_bus and to_bus must differ, "
                f"got {lnk.from_bus}"
            )
        if lnk.from_bus not in ext_bus_ids:
            raise ValueError(
                f"HVDC link {i}: from_bus {lnk.from_bus} not in case bus table. "
                f"Valid IDs: {sorted(ext_bus_ids)}"
            )
        if lnk.to_bus not in ext_bus_ids:
            raise ValueError(
                f"HVDC link {i}: to_bus {lnk.to_bus} not in case bus table. "
                f"Valid IDs: {sorted(ext_bus_ids)}"
            )
        if lnk.p_max_mw <= 0:
            raise ValueError(
                f"HVDC link {i}: p_max_mw must be > 0, got {lnk.p_max_mw}"
            )
        if lnk.p_min_mw is not None and lnk.p_min_mw > lnk.p_max_mw:
            raise ValueError(
                f"HVDC link {i}: p_min_mw ({lnk.p_min_mw}) must be <= "
                f"p_max_mw ({lnk.p_max_mw})"
            )
        if lnk.bandwidth_mw < 0:
            raise ValueError(
                f"HVDC link {i}: bandwidth_mw must be >= 0, got {lnk.bandwidth_mw}"
            )
        if lnk.loss_percent < 0:
            raise ValueError(
                f"HVDC link {i}: loss_percent must be >= 0, got {lnk.loss_percent}"
            )
        if lnk.mode not in _VALID_MODES:
            raise ValueError(
                f"HVDC link {i}: mode must be one of {sorted(_VALID_MODES)}, "
                f"got '{lnk.mode}'"
            )
        c0, c1, c2 = lnk.cost_coeffs
        if c2 < 0:
            raise ValueError(
                f"HVDC link {i}: cost_coeffs c2 must be >= 0 (convex quadratic), "
                f"got {c2}"
            )
        if c1 < 0:
            raise ValueError(
                f"HVDC link {i}: cost_coeffs c1 must be >= 0 (nonneg magnitude cost), "
                f"got {c1}"
            )


# ---------------------------------------------------------------------------
# Incidence matrices
# ---------------------------------------------------------------------------

def _make_hvdc_incidence_matrices(
    links: list,
    nb: int,
    ext_to_int: dict,
) -> tuple:
    """
    Build (Ch_from, Ch_to), each shape (nb, n_hvdc).

    Ch_from[i, k] = 1 if link k's from_bus maps to internal bus i.
    Ch_to[i, k]   = 1 if link k's to_bus maps to internal bus i.
    Returns a pair of np.empty((nb, 0)) arrays for an empty link list.
    """
    n_hvdc = len(links)
    if n_hvdc == 0:
        return np.empty((nb, 0)), np.empty((nb, 0))
    Ch_from = np.zeros((nb, n_hvdc))
    Ch_to   = np.zeros((nb, n_hvdc))
    for k, lnk in enumerate(links):
        Ch_from[ext_to_int[lnk.from_bus], k] = 1.0
        Ch_to[ext_to_int[lnk.to_bus],   k] = 1.0
    return Ch_from, Ch_to


# ---------------------------------------------------------------------------
# Static box computation (mode -> box, the upstream helper)
# ---------------------------------------------------------------------------

def _hvdc_static_box(links: list) -> tuple:
    """
    Map each HVDCLink's mode + fields to a static per-link box (p_min, p_max).

    This is the sole home of the mode->box mapping. Returns two (n_hvdc,)
    numpy arrays in MW. The result is what the builder uses directly for a
    single-step problem, and what multistep tiling is based on when
    df_hvdc_min/df_hvdc_max are not provided.

    p_min_mw=None defaults per mode:
      band/free: -p_max_mw (symmetric)
      downward:  0
      scheduled: p_scheduled_mw (degenerate box)
    """
    n = len(links)
    p_min = np.empty(n)
    p_max = np.empty(n)
    for k, lnk in enumerate(links):
        pmax = lnk.p_max_mw
        psched = lnk.p_scheduled_mw
        mode = lnk.mode

        if mode == "scheduled":
            p_min[k] = psched
            p_max[k] = psched

        elif mode == "downward":
            if psched >= 0:
                p_min[k] = 0.0
                p_max[k] = psched
            else:
                p_min[k] = psched
                p_max[k] = 0.0

        elif mode == "band":
            pmin_default = lnk.p_min_mw if lnk.p_min_mw is not None else -pmax
            bw = lnk.bandwidth_mw
            lo = max(psched - bw, pmin_default)
            hi = min(psched + bw, pmax)
            p_min[k] = lo
            p_max[k] = hi

        else:  # free
            p_min[k] = lnk.p_min_mw if lnk.p_min_mw is not None else -pmax
            p_max[k] = pmax

    return p_min, p_max


# ---------------------------------------------------------------------------
# CVXPY component methods
# ---------------------------------------------------------------------------

def hvdc_injections(links: list, ext_to_int: dict) -> tuple:
    """
    Create p_in/p_out variables and the scaled nodal-balance addend.

    Returns
    -------
    injection_expr : cp.Expression
        inv_baseMVA * (Ch_from @ p_in + Ch_to @ p_out). Both terminals
        enter with '+' (Convention B: positive = injection into grid).
        inv_baseMVA is a cp.Parameter that the caller must set before solve:
          inv_baseMVA.value = 1.0 / baseMVA
    p_in : cp.Variable, shape (n_hvdc,)
        From-bus signed nodal injection (MW, engineering units).
    p_out : cp.Variable, shape (n_hvdc,)
        To-bus signed nodal injection (MW, engineering units).
    inv_baseMVA : cp.Parameter
        Scalar parameter, unset. Caller binds before solving.
    """
    nb = len(ext_to_int)
    n_hvdc = len(links)
    Ch_from, Ch_to = _make_hvdc_incidence_matrices(links, nb, ext_to_int)

    p_in  = cp.Variable((n_hvdc,), name="p_hvdc_in")
    p_out = cp.Variable((n_hvdc,), name="p_hvdc_out")
    inv_baseMVA = cp.Parameter(name="hvdc_inv_baseMVA")

    injection_expr = inv_baseMVA * (Ch_from @ p_in + Ch_to @ p_out)
    return injection_expr, p_in, p_out, inv_baseMVA


def dc_operating_constraints(
    links: list,
    p_in: cp.Variable,
    p_out: cp.Variable,
    p_min_t: np.ndarray,
    p_max_t: np.ndarray,
    step: int = 0,
) -> list:
    """
    Build the HVDC operating constraint list for one time step.

    Returns exactly two constraints:
      1. Box bound: p_min_t <= p_in <= p_max_t
         A degenerate entry (p_min_t[k] == p_max_t[k]) pins p_in[k] via
         coincident bounds — NOT a separate equality constraint.
      2. Loss-branch equality: p_out == coeff_vec * p_in
         coeff_vec[k] is selected per link from the box's zero-crossing:
           p_min_t[k] >= 0: -(1 - loss_frac[k])   (from->to, lossy)
           p_max_t[k] <= 0: -(1 + loss_frac[k])   (to->from, lossy)
           straddling:       -1                     (lossless, UserWarning)

    Parameters
    ----------
    links : list[HVDCLink]
    p_in, p_out : cp.Variable, shape (n_hvdc,)
    p_min_t, p_max_t : np.ndarray, shape (n_hvdc,)
        Per-link box for this step (MW).
    step : int
        Time-step index used in UserWarning messages.
    """
    coeff_vec = np.empty(len(links))
    for k, lnk in enumerate(links):
        loss_frac = lnk.loss_percent / 100.0
        if p_min_t[k] >= 0:
            coeff_vec[k] = -(1.0 - loss_frac)
        elif p_max_t[k] <= 0:
            coeff_vec[k] = -(1.0 + loss_frac)
        else:
            coeff_vec[k] = -1.0
            if loss_frac != 0.0:
                warnings.warn(
                    f"HVDC link {k} at step {step}: box [{p_min_t[k]}, {p_max_t[k]}] "
                    f"straddles zero with loss_percent={lnk.loss_percent}; "
                    f"falling back to lossless branch (p_out = -p_in). "
                    f"Full sign-switching loss model deferred to Milestone 15.",
                    UserWarning,
                    stacklevel=2,
                )

    return [
        p_min_t <= p_in,
        p_in <= p_max_t,
        p_out == cp.multiply(coeff_vec, p_in),
    ]


def ac_operating_constraints(
    links: list,
    p_in: cp.Variable,
    p_out: cp.Variable,
    p_min_t: np.ndarray,
    p_max_t: np.ndarray,
    step: int = 0,
) -> list:
    """
    AC operating constraints for HVDC links.

    HVDC is unity power factor with no reactive coupling, so the AC and DC
    operating regions are identical. This is a pass-through to
    dc_operating_constraints; the ac_*/dc_* fork exists so the interface
    shape matches what storage/SOCP components need (where AC and DC diverge).
    """
    return dc_operating_constraints(links, p_in, p_out, p_min_t, p_max_t, step)


def hvdc_cost_expr(cost_coeffs: tuple, p_in: cp.Variable) -> cp.Expression:
    """
    Per-link HVDC cost expression: c2*|p_in|^2 + c1*|p_in| + c0.

    Cost acts on the transfer magnitude so it is symmetric in flow direction.
    (c0, c1, c2) is lowest-first — the package-wide user-facing convention.
    Use cp.multiply for the linear term to avoid CvxpyDeprecationWarning.
    Written as an explicit monomial sum (not Horner) for DCP checker compatibility.
    """
    c0, c1, c2 = cost_coeffs
    return (
        c2 * cp.square(p_in)
        + cp.multiply(c1, cp.abs(p_in))
        + c0
    )


# ---------------------------------------------------------------------------
# Import from MATPOWER dcline table
# ---------------------------------------------------------------------------

# MATPOWER dcline column indices (0-based)
_FBUS   = 0
_TBUS   = 1
_STATUS = 2
_PF     = 3   # sending-terminal scheduled setpoint (MW)
_PMIN   = 9
_PMAX   = 10
_LOSS0  = 15  # fixed converter loss (MW) — not modelled in MVP
_LOSS1  = 16  # proportional loss fraction (per-unit)


def hvdc_from_dcline(
    dcline_table: np.ndarray,
    dclinecost: np.ndarray | None = None,
) -> list:
    """
    Build a list of HVDCLink objects from a MATPOWER dcline table.

    Column order (verified against pypower.t.t_case9_dcline):
      fbus tbus status Pf Pt Qf Qt Vf Vt Pmin Pmax QminF QmaxF QminT QmaxT
      loss0 loss1

    Inactive rows (status == 0) are skipped.
    Reactive (Qf, Qt, QminF, QmaxF, QminT, QmaxT) and voltage (Vf, Vt)
    columns are dropped — MVP is unity power factor, no HVDC voltage control.

    loss0 (fixed converter loss) is dropped; a UserWarning is emitted if any
    active row has loss0 != 0. Full fixed-loss modelling is Milestone 15.

    Each active row maps to mode="free" with p_in optimized over [Pmin, Pmax].
    Pf is carried as a non-binding reference (p_scheduled_mw) only.

    dclinecost rows are model-2 polynomial, highest-power-first (same layout
    as gencost). HVDCLink.cost_coeffs is lowest-first (c0, c1, c2) — this
    function reverses the order. The reversal is n-dependent:
      n=2 (linear):    [..., c1, c0]  -> (c0, c1, 0.0)
      n=3 (quadratic): [..., c2, c1, c0] -> (c0, c1, c2)
    n > 3 raises ValueError (higher-order terms unsupported).

    Parameters
    ----------
    dcline_table : np.ndarray, shape (ndc, 17)
    dclinecost : np.ndarray or None, shape (ndc, >=7) model-2 rows

    Returns
    -------
    list[HVDCLink]
        One entry per active (status != 0) dcline row.
    """
    links = []
    loss0_nonzero = False

    for row_idx in range(dcline_table.shape[0]):
        row = dcline_table[row_idx]
        if int(row[_STATUS]) == 0:
            continue

        fbus    = int(row[_FBUS])
        tbus    = int(row[_TBUS])
        p_sched = float(row[_PF])
        p_min   = float(row[_PMIN])
        p_max   = float(row[_PMAX])
        loss0   = float(row[_LOSS0])
        loss1   = float(row[_LOSS1])

        if loss0 != 0.0:
            loss0_nonzero = True

        # dclinecost: model-2 polynomial, highest-power-first
        # Layout: [model, startup, shutdown, n, c_{n-1}, ..., c_1, c_0]
        # HVDCLink.cost_coeffs is (c0, c1, c2), lowest-first.
        cost = (0.0, 0.0, 0.0)
        if dclinecost is not None:
            crow = dclinecost[row_idx]
            n = int(crow[3])
            if n > 3:
                raise ValueError(
                    f"dclinecost row {row_idx}: polynomial degree {n} > 3 "
                    f"(higher-order terms unsupported)"
                )
            # coefficients start at index 4, stored highest-power-first
            coeffs_hi_first = [float(crow[4 + j]) for j in range(n)]
            # reverse to lowest-first; pad to length 3
            coeffs_lo_first = list(reversed(coeffs_hi_first))
            while len(coeffs_lo_first) < 3:
                coeffs_lo_first.append(0.0)
            cost = tuple(coeffs_lo_first[:3])

        links.append(HVDCLink(
            from_bus=fbus,
            to_bus=tbus,
            p_max_mw=p_max,
            p_min_mw=p_min,
            p_scheduled_mw=p_sched,
            mode="free",
            loss_percent=loss1 * 100.0,
            cost_coeffs=cost,
        ))

    if loss0_nonzero:
        warnings.warn(
            "hvdc_from_dcline: one or more active dcline rows have loss0 != 0. "
            "Fixed converter loss (LOSS0) is not modelled in the MVP; the imported "
            "model will not match Pypower exactly. Full fixed-loss modelling is "
            "deferred to Milestone 15.",
            UserWarning,
            stacklevel=2,
        )

    return links
