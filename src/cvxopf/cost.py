"""
Generator cost expression builders.

Constructs CVXPY cost expressions from MATPOWER gencost arrays.
"""

import warnings

import numpy as np
import cvxpy as cp

# MATPOWER gencost column indices
MODEL = 0
NCOST = 3


def _lower_convex_hull(x: np.ndarray, f: np.ndarray) -> tuple:
    """
    Return the lower convex envelope breakpoints of a PWL cost curve.

    Given breakpoints ``(x, f)`` with strictly increasing ``x``, returns the
    subset of vertices lying on the lower convex boundary, so that a
    max-of-affine-pieces expression over the retained segments reproduces the
    convex hull of the original points.

    Because ``x`` is strictly increasing, the lower hull is a left-to-right
    monotone chain (Andrew's algorithm, lower half): walk the points and pop
    any vertex that would make a non-convex (clockwise) turn. Handles the
    degenerate 2-point and collinear cases naturally -- no external dependency.

    Parameters
    ----------
    x : np.ndarray, shape (n,)
        Breakpoint powers (MW), strictly increasing.
    f : np.ndarray, shape (n,)
        Costs at each breakpoint ($/hr).

    Returns
    -------
    xv, fv : np.ndarray
        Breakpoints on the lower convex boundary, from the leftmost to the
        rightmost point.
    """
    x = np.asarray(x, dtype=float)
    f = np.asarray(f, dtype=float)

    # Monotone chain, lower hull only. hull holds indices into (x, f).
    hull = []
    for i in range(len(x)):
        # Pop while the last turn is not convex (cross product <= 0 means the
        # middle point is on or above the chord, so it is not a lower vertex).
        while len(hull) >= 2:
            a, b = hull[-2], hull[-1]
            cross = (x[b] - x[a]) * (f[i] - f[a]) - (f[b] - f[a]) * (x[i] - x[a])
            if cross <= 0:
                hull.pop()
            else:
                break
        hull.append(i)

    idx = np.array(hull)
    return x[idx], f[idx]


def _pwl_cost_expr(x: np.ndarray, f: np.ndarray, Pg_MW) -> cp.Expression:
    """
    Build a convex piecewise-linear cost expression for one generator.

    A convex PWL cost with breakpoints ``(x, f)`` is the pointwise maximum of
    its segment supporting lines. Each segment ``i`` contributes the affine
    piece ``f[i] + m[i] * (Pg - x[i])`` where ``m[i]`` is the segment slope;
    ``cp.maximum`` of these pieces reproduces the curve exactly when it is
    convex and is DCP-clean (convexity moves into a max of affine
    expressions).

    If the supplied breakpoints are **not** convex (segment slopes are not
    non-decreasing), the curve is replaced by its lower convex envelope
    (convex hull of the breakpoints) via ``_lower_convex_hull`` and a
    ``UserWarning`` is emitted. This is a deliberate, documented
    approximation: a nonconvex PWL cost cannot be represented in a convex
    program, so the convex hull is used instead.

    Parameters
    ----------
    x : np.ndarray, shape (n,)
        Breakpoint powers (MW), strictly increasing.
    f : np.ndarray, shape (n,)
        Costs at each breakpoint ($/hr).
    Pg_MW : cp.Expression
        Scalar generator real power output in MW for this generator.

    Returns
    -------
    cost : cp.Expression
        Scalar CVXPY expression for this generator's PWL cost.
    """
    x = np.asarray(x, dtype=float)
    f = np.asarray(f, dtype=float)

    # Segment slopes m[i] = (f[i+1] - f[i]) / (x[i+1] - x[i]).
    m = np.diff(f) / np.diff(x)

    # Convexity check: slopes must be non-decreasing (tolerance matches the
    # is_convex prototype). A nonconvex curve is replaced by its lower convex
    # hull, with a warning.
    if not np.all(np.diff(m) >= -1e-12):
        warnings.warn(
            "poly_cost_expr: piecewise-linear cost is nonconvex (segment "
            "slopes are not non-decreasing); using the lower convex hull of "
            "the given breakpoints instead.",
            UserWarning,
        )
        x, f = _lower_convex_hull(x, f)
        m = np.diff(f) / np.diff(x)

    # Each affine piece anchored at its left breakpoint, extended over the
    # whole domain: f[i] + m[i] * (Pg - x[i]). Their pointwise maximum is the
    # convex curve. A single segment (2 breakpoints, or a hull that collapsed
    # to one line) is affine on its own -- cp.maximum needs >= 2 args, so
    # return the lone piece directly.
    pieces = [f[i] + m[i] * (Pg_MW - x[i]) for i in range(len(m))]
    if len(pieces) == 1:
        return pieces[0]
    return cp.maximum(*pieces)


def poly_cost_expr(gencost: np.ndarray, Pg_MW) -> cp.Expression:
    """
    Build a generator cost expression from a MATPOWER gencost array.

    Supports MODEL=2 (polynomial) and MODEL=1 (piecewise linear). The cost is
    expressed in units consistent with the gencost coefficients (typically
    $/hr when Pg is in MW).

    For MODEL=2, the expression is constructed as an explicit sum of monomial
    terms (constant * Pg^p) rather than via Horner's method, so that CVXPY's
    DCP checker can verify convexity when the problem is a convex QP. Horner's
    method produces (affine * affine) products when leading coefficients are
    zero, which CVXPY cannot verify as DCP even though the polynomial is in
    fact convex.

    For MODEL=1, the piecewise-linear curve is built as the pointwise maximum
    of its segment supporting lines (see ``_pwl_cost_expr``). A convex curve
    is reproduced exactly; a nonconvex curve is approximated by its lower
    convex hull with a ``UserWarning``.

    Parameters
    ----------
    gencost : np.ndarray, shape (ng, ...)
        MATPOWER gencost array. Each row corresponds to one generator.
        MODEL=2 rows carry ``n`` polynomial coefficients (highest power
        first) after the ``NCOST`` field; MODEL=1 rows carry ``n``
        ``(x, f)`` breakpoint pairs (``2*n`` values) after ``NCOST``.
    Pg_MW : cp.Variable or list of cp.Expression, length ng
        Generator real power output in MW (not per-unit). May be a
        cp.Variable of shape (ng,), or a list of scalar CVXPY expressions,
        one per generator.

    Returns
    -------
    cost : cp.Expression
        Scalar CVXPY expression representing the total generation cost.

    Raises
    ------
    ValueError
        If gencost has a MODEL value other than 1 or 2.
    """
    cost = 0
    for k in range(gencost.shape[0]):
        model = int(gencost[k, MODEL])
        if model == 1:
            n    = int(gencost[k, NCOST])
            pts  = gencost[k, 4 : 4 + 2 * n]
            x    = pts[0::2]
            f    = pts[1::2]
            cost = cost + _pwl_cost_expr(x, f, Pg_MW[k])
            continue
        if model != 2:
            raise ValueError(
                f"Generator {k}: unrecognised gencost MODEL={model}. "
                "Expected 1 or 2."
            )
        n      = int(gencost[k, NCOST])
        coeffs = gencost[k, 4 : 4 + n]
        x      = Pg_MW[k]
        expr   = 0
        degree = n - 1
        for i, c in enumerate(coeffs):
            cf = float(c)
            p  = degree - i          # power for this coefficient
            if cf == 0.0:
                continue
            if p == 0:
                expr = expr + cf
            elif p == 1:
                expr = expr + cf * x
            elif p == 2:
                expr = expr + cf * cp.square(x)
            else:
                expr = expr + cf * cp.power(x, p)
        cost = cost + expr
    return cost
