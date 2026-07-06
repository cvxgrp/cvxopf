"""
Generator cost expression builders.

Constructs CVXPY cost expressions from MATPOWER gencost arrays.
"""

import numpy as np
import cvxpy as cp

# MATPOWER gencost column indices
MODEL = 0
NCOST = 3


def poly_cost_expr(gencost: np.ndarray, Pg_MW) -> cp.Expression:
    """
    Build a polynomial cost expression from a MATPOWER gencost array.

    Supports MODEL=2 (polynomial) only. The cost is expressed in units
    consistent with the gencost coefficients (typically $/hr when Pg is
    in MW).

    The expression is constructed as an explicit sum of monomial terms
    (constant * Pg^p) rather than via Horner's method, so that CVXPY's
    DCP checker can verify convexity when the problem is a convex QP.
    Horner's method produces (affine * affine) products when leading
    coefficients are zero, which CVXPY cannot verify as DCP even though
    the polynomial is in fact convex.

    Parameters
    ----------
    gencost : np.ndarray, shape (ng, ...)
        MATPOWER gencost array. Each row corresponds to one generator.
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
    NotImplementedError
        If any row has MODEL=1 (piecewise linear). Planned for a future
        milestone.
    ValueError
        If gencost has a MODEL value other than 1 or 2.
    """
    cost = 0
    for k in range(gencost.shape[0]):
        model = int(gencost[k, MODEL])
        if model == 1:
            raise NotImplementedError(
                f"Generator {k}: gencost MODEL=1 (piecewise linear) is not "
                "yet supported. Only MODEL=2 (polynomial) is implemented."
            )
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