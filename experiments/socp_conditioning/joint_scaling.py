"""Joint objective/constraint scaling; four declared CLARABEL smoke solves.

Balance row maxima of the symmetric data layout [P, A.T, c; A, 0, b;
c.T, b.T, 0], without forming it. The last coordinate stays fixed at one:
there is no homogeneous-coordinate or global objective rescaling. This data
layout is not the barrier-dependent KKT matrix and may be singular by design.
"""

import numpy as np
from scipy import sparse
from unittest.mock import patch

from experiments.socp_conditioning import cone_scaling as c
from experiments.socp_conditioning import diagnostic as d

BASE_CONTEXT = c.context
BASE_WORKER = c.worker


def maximum(A, axis):
    return np.asarray(abs(A).max(axis=axis).toarray()).ravel()


def joint_scales(data, layout):
    """Five anchored symmetric Ruiz-style infinity-norm passes.

    Both row and column updates are computed from the same current data, then
    applied together. The square-root damping accounts for two-sided scaling.
    Bounds apply to cumulative positive scales, not to problem coefficients.
    """
    A, P = data["A"].tocsc(copy=True), data["P"].tocsc(copy=True)
    q, b = data["c"].copy(), data["b"].copy()
    R, D = np.ones(A.shape[0]), np.ones(A.shape[1])
    for _ in range(c.PASSES):
        variable = np.maximum.reduce((maximum(P, 1), maximum(A, 0), abs(q)))
        constraint = np.maximum(maximum(A, 1), abs(b))
        start = sum(layout[:2])
        for size in layout[2]:
            constraint[start : start + size] = np.max(constraint[start : start + size])
            start += size
        next_D = np.clip(
            D / np.sqrt(np.where(variable > 0, variable, 1)), c.SCALE_MIN, c.SCALE_MAX
        )
        next_R = np.clip(
            R / np.sqrt(np.where(constraint > 0, constraint, 1)),
            c.SCALE_MIN,
            c.SCALE_MAX,
        )
        step_D, step_R = next_D / D, next_R / R
        A = (sparse.diags(step_R) @ A @ sparse.diags(step_D)).tocsc()
        P = (sparse.diags(step_D) @ P @ sparse.diags(step_D)).tocsc()
        q, b = step_D * q, step_R * b
        R, D = next_R, next_D
    c.validate_scales(data["A"], layout, R, D)
    return R, D


def context():
    base = BASE_CONTEXT()
    return base | {
        "experiment": "joint_cone_preserving_canonical_scaling",
        "scaling_rule": "five simultaneous infinity-norm square-root passes on P/A/c/b; SOC row maxima shared; last augmented coordinate fixed at one",
        "global_objective_multiplier": 1.0,
        "additional_sources": base["additional_sources"]
        | {str(d.HERE.relative_to(d.ROOT) / "joint_scaling.py"): d.sha(__file__)},
    }


def worker(directory, reference, label):
    return BASE_WORKER(directory, reference, label, scaling_strategy=joint_scales)


if __name__ == "__main__":
    with patch.object(c, "context", context), patch.object(c, "worker", worker):
        c.main(worker_module=__spec__.name)
