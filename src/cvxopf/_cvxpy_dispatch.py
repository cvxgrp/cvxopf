"""Scoped compatibility policy for CVXPY's process-global density dispatch."""

from contextlib import contextmanager
from functools import wraps
from threading import RLock

import cvxpy as cp


_dispatch_lock = RLock()


@contextmanager
def sparse_dispatch_policy(automatic: bool):
    """Serialize cvxopf entry points and restore the caller's threshold.

    Independent CVXPY calls in other threads do not share this lock. Use
    separate processes when mixing those calls with cvxopf AC operations.
    """
    with _dispatch_lock:
        previous = cp.settings.SPARSE_DENSITY_THRESHOLD
        try:
            if not automatic:
                cp.settings.SPARSE_DENSITY_THRESHOLD = 0.0
            yield
        finally:
            cp.settings.SPARSE_DENSITY_THRESHOLD = previous


def with_build_dispatch_policy(builder):
    """Apply the AC policy before any leaf or expression is constructed."""
    @wraps(builder)
    def wrapped(*args, **kwargs):
        automatic = kwargs.get("formulation", "ac") != "ac" or (
            kwargs.get("automatic_sparse_dispatch", False)
        )
        with sparse_dispatch_policy(automatic):
            build = builder(*args, **kwargs)
        build.automatic_sparse_dispatch = automatic
        return build

    return wrapped
