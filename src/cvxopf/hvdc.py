"""
HVDC transmission link model for AC-OPF and DC-OPF.

Models each HVDC link as a pair of generator-like signed nodal injections
(p_in at from_bus, p_out at to_bus). Convention B: positive = injection into
the grid. Both terminals enter the nodal balance with '+'.

The sole internal representation is a per-step box p_in ∈ [p_min_t, p_max_t].
p_min_mw and p_max_mw are required fields on HVDCLink; they define the box
directly. There are no named modes — any upstream box-generating helpers
(e.g. scheduled/band/downward/free convenience functions) are out-of-scope
for this module and would live in user code.

Loss model: affine branch selected pre-construction from the box's zero-crossing:
  p_min_t[k] >= 0  (from->to):  p_out[k] = -(1 - loss_frac[k]) * p_in[k]
  p_max_t[k] <= 0  (to->from):  p_out[k] = -(1 + loss_frac[k]) * p_in[k]
  zero-straddling:               p_out[k] = -p_in[k]  (lossless, UserWarning)

Component-method interface: hvdc.py exposes named builder methods that
constructors call and compose. Variables (p_in, p_out) are created in the
problem builder scope and passed into these methods — hvdc.py never
instantiates cp.Variable itself.

Import chain:
  hvdc.py  →  data.py, cvxpy, numpy, warnings, dataclasses
  ac_problem.py → hvdc.py
  dc_problem.py → hvdc.py
  problem.py    → hvdc.py  (re-exports HVDCLink, hvdc_from_dcline)
"""

from __future__ import annotations

from dataclasses import dataclass, field
import warnings

import numpy as np
import cvxpy as cp

from cvxopf.data import align_device_dataframe


# ---------------------------------------------------------------------------
# HVDCLink dataclass
# ---------------------------------------------------------------------------


@dataclass
class HVDCLink:
    """
    Parameters for a single HVDC transmission link.

    An HVDC link is modelled as a pair of signed nodal injections p_in
    (at from_bus) and p_out (at to_bus). Convention B: positive = injection
    into the grid. The box p_in ∈ [p_min_mw, p_max_mw] is the sole internal
    representation.

    Attributes
    ----------
    from_bus : int
        Sending-terminal bus ID (external MATPOWER numbering).
    to_bus : int
        Receiving-terminal bus ID (external MATPOWER numbering).
    p_min_mw : float
        Lower bound on the from-bus injection p_in (MW). Must be <= p_max_mw.
        A degenerate box (p_min_mw == p_max_mw) pins p_in via coincident
        bounds — not a separate equality constraint.
    p_max_mw : float
        Upper bound on p_in (MW). Together with p_min_mw, may describe
        forward-only, reverse-only, bidirectional, or zero-pinned operation.
    loss_percent : float
        Proportional loss as a percentage (0–100). loss_frac = loss_percent/100.
        Fixed converter loss (LOSS0) is not modelled; see Milestone 15.
    cost_coeffs : tuple of float
        Polynomial cost (c0, c1, c2) in lowest-first order. Cost acts on the
        transfer magnitude: c2*|p_in|^2 + c1*|p_in| + c0. Default (0,0,0).
    device_id : str or None
        Stable external identity used to align time-series columns. Required
        only when ``df_hvdc_min`` and ``df_hvdc_max`` are supplied.
    """

    from_bus: int
    to_bus: int
    p_min_mw: float
    p_max_mw: float
    loss_percent: float = 0.0
    cost_coeffs: tuple = field(default_factory=lambda: (0.0, 0.0, 0.0))
    device_id: str | None = None


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
        numeric_fields = {
            "p_min_mw": lnk.p_min_mw,
            "p_max_mw": lnk.p_max_mw,
            "loss_percent": lnk.loss_percent,
        }
        for name, value in numeric_fields.items():
            if not np.isfinite(value):
                raise ValueError(
                    f"HVDC link {i}: {name} must be finite, got {value}"
                )
        if lnk.from_bus == lnk.to_bus:
            raise ValueError(
                f"HVDC link {i}: from_bus and to_bus must differ, got {lnk.from_bus}"
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
        if lnk.p_min_mw > lnk.p_max_mw:
            raise ValueError(
                f"HVDC link {i}: p_min_mw ({lnk.p_min_mw}) must be <= "
                f"p_max_mw ({lnk.p_max_mw})"
            )
        if not 0 <= lnk.loss_percent <= 100:
            raise ValueError(
                f"HVDC link {i}: loss_percent must be between 0 and 100, "
                f"got {lnk.loss_percent}"
            )
        try:
            coeffs = tuple(lnk.cost_coeffs)
        except TypeError as exc:
            raise ValueError(
                f"HVDC link {i}: cost_coeffs must contain exactly "
                f"(c0, c1, c2), got {lnk.cost_coeffs!r}"
            ) from exc
        if len(coeffs) != 3:
            raise ValueError(
                f"HVDC link {i}: cost_coeffs must contain exactly "
                f"(c0, c1, c2), got {lnk.cost_coeffs!r}"
            )
        try:
            numeric_coeffs = np.asarray(coeffs, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"HVDC link {i}: cost_coeffs must contain only finite "
                "numeric values"
            ) from exc
        c0, c1, c2 = numeric_coeffs
        if not np.all(np.isfinite(numeric_coeffs)):
            raise ValueError(
                f"HVDC link {i}: cost_coeffs must contain only finite values"
            )
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
    Ch_to = np.zeros((nb, n_hvdc))
    for k, lnk in enumerate(links):
        Ch_from[ext_to_int[lnk.from_bus], k] = 1.0
        Ch_to[ext_to_int[lnk.to_bus], k] = 1.0
    return Ch_from, Ch_to


def _prepare_data(
    links: list,
    nb: int,
    ext_to_int: dict,
    ext_bus_ids: set,
) -> dict:
    """Validate and prepare formulation-independent HVDC data."""
    _validate_hvdc(links, ext_bus_ids)
    Ch_from, Ch_to = _make_hvdc_incidence_matrices(
        links, nb, ext_to_int
    )
    return {
        "n_hvdc": len(links),
        "Ch_from": Ch_from,
        "Ch_to": Ch_to,
    }


# ---------------------------------------------------------------------------
# Static box (trivial vectorizer — reads p_min_mw/p_max_mw directly)
# ---------------------------------------------------------------------------


def _hvdc_static_box(links: list) -> tuple:
    """
    Return (p_min, p_max) as (n_hvdc,) numpy arrays in MW.

    Reads p_min_mw and p_max_mw directly from each HVDCLink. Used by the
    single-step builder and as the tile source for multistep when
    df_hvdc_min/df_hvdc_max are not provided.
    """
    p_min = np.array([lnk.p_min_mw for lnk in links], dtype=float)
    p_max = np.array([lnk.p_max_mw for lnk in links], dtype=float)
    return p_min, p_max


def _parse_hvdc_timeseries(frame, links: list, T: int, frame_name: str) -> np.ndarray:
    """Align an externally keyed HVDC frame to link-list order."""
    return align_device_dataframe(frame, links, T, frame_name)


# ---------------------------------------------------------------------------
# CVXPY component methods
# ---------------------------------------------------------------------------


def dc_injections(
    links: list,
    p_in: cp.Variable,
    p_out: cp.Variable,
    ext_to_int: dict,
    *,
    incidence: tuple[np.ndarray, np.ndarray] | None = None,
) -> tuple:
    """
    Build the real nodal-balance addend for HVDC links in a DC network.

    p_in and p_out are created by the calling problem builder and passed in;
    this function does not instantiate any cp.Variable.

    Returns
    -------
    p_injection_expr : cp.Expression
        inv_baseMVA * (Ch_from @ p_in + Ch_to @ p_out). Both terminals
        enter with '+' (Convention B: positive = injection into grid).
    q_injection_expr : None
        HVDC has no reactive channel in the current model.
    inv_baseMVA : cp.Parameter
        Scalar parameter, unset. Caller must bind before solving:
          inv_baseMVA.value = 1.0 / baseMVA
    """
    nb = len(ext_to_int)
    Ch_from, Ch_to = (
        _make_hvdc_incidence_matrices(links, nb, ext_to_int)
        if incidence is None
        else incidence
    )
    inv_baseMVA = cp.Parameter(nonneg=True, name="hvdc_inv_baseMVA")
    injection_expr = inv_baseMVA * (Ch_from @ p_in + Ch_to @ p_out)
    return injection_expr, None, inv_baseMVA


def ac_injections(
    links: list,
    p_in: cp.Variable,
    p_out: cp.Variable,
    ext_to_int: dict,
    *,
    incidence: tuple[np.ndarray, np.ndarray] | None = None,
) -> tuple:
    """
    Build HVDC network injections for an AC network.

    The current unity-power-factor model has no reactive terminal channel.
    This separate AC entry point is retained for future reactive-control
    models and for symmetry with the other device components.
    """
    return dc_injections(
        links, p_in, p_out, ext_to_int, incidence=incidence
    )


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

    Returns exactly three constraints:
      1. Lower box bound: p_min_t <= p_in
      2. Upper box bound: p_in <= p_max_t
         A degenerate entry (p_min_t[k] == p_max_t[k]) pins p_in[k] via
         coincident bounds — NOT a separate equality constraint.
      3. Loss-branch equality: p_out == coeff_vec * p_in
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


def coupling_constraints(
    links: list,
    p_in_list: list,
    p_out_list: list,
    delta: float = 1.0,
) -> list:
    """HVDC links are memoryless under the current model."""
    return []


def hvdc_cost_expr(links: list, p_in: cp.Variable) -> cp.Expression:
    """
    Total HVDC cost over all links.

    Cost acts on the transfer magnitude so it is symmetric in flow direction.
    (c0, c1, c2) is lowest-first — the package-wide user-facing convention.
    Use cp.multiply for the linear term to avoid CvxpyDeprecationWarning.
    Written as an explicit monomial sum (not Horner) for DCP checker compatibility.
    """
    total = 0
    for k, link in enumerate(links):
        c0, c1, c2 = link.cost_coeffs
        total = (
            total
            + c2 * cp.square(p_in[k])
            + cp.multiply(c1, cp.abs(p_in[k]))
            + c0
        )
    return total


# ---------------------------------------------------------------------------
# Import from MATPOWER dcline table
# ---------------------------------------------------------------------------

# MATPOWER dcline column indices (0-based)
_FBUS = 0
_TBUS = 1
_STATUS = 2
_PF = 3  # sending-terminal scheduled setpoint (MW) — carried for reference only
_PMIN = 9
_PMAX = 10
_LOSS0 = 15  # fixed converter loss (MW) — not modelled in MVP
_LOSS1 = 16  # proportional loss fraction (per-unit)


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

    [Pmin, Pmax] map directly to p_min_mw/p_max_mw (the canonical box bounds).
    Pf is dropped (non-binding reference only in the MVP optimizer context).

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

        fbus = int(row[_FBUS])
        tbus = int(row[_TBUS])
        p_min = float(row[_PMIN])
        p_max = float(row[_PMAX])
        loss0 = float(row[_LOSS0])
        loss1 = float(row[_LOSS1])

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

        links.append(
            HVDCLink(
                from_bus=fbus,
                to_bus=tbus,
                p_min_mw=p_min,
                p_max_mw=p_max,
                loss_percent=loss1 * 100.0,
                cost_coeffs=cost,
            )
        )

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
