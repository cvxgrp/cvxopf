"""
Generator cost expression builders.

Constructs CVXPY cost expressions from MATPOWER gencost arrays.
"""

import numpy as np
import cvxpy as cp

# MATPOWER gencost column indices
MODEL = 0
NCOST = 3


def poly_cost_expr(gencost: np.ndarray, Pg_MW: cp.Variable) -> cp.Expression:
    """
    Build a polynomial cost expression from a MATPOWER gencost array.

    Supports MODEL=2 (polynomial) only. Polynomial evaluation uses Horner's
    method. The cost is expressed in units consistent with the gencost
    coefficients (typically $/hr when Pg is in MW).

    Parameters
    ----------
    gencost : np.ndarray, shape (ng, ...)
        MATPOWER gencost array. Each row corresponds to one generator.
    Pg_MW : cp.Variable, shape (ng,)
        Generator real power output in MW (not per-unit).

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
        expr   = 0
        for c in coeffs:
            expr = expr * Pg_MW[k] + float(c)
        cost = cost + expr
    return cost
