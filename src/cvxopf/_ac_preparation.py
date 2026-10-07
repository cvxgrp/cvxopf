"""Reduce exact AC coordinates at the verified IPOPT boundary, then restore them.

This module implements opt-in standalone AC preparation through the existing
``_solve_ac_with_verified_x0`` boundary. It preserves the original CVXPY graph,
variable and Parameter identities, physical units, objective, and result schema.
Device-limit normalization, when selected, is already part of model assembly.
Unlike ``_convex_preparation``, this bridge performs no canonical scaling or
general Jacobian/Hessian equilibration. Sharing the verified boundary does not
enable prepared hierarchical execution; that integration remains deferred.

Coordinate reduction and starts
-------------------------------
The original IPOPT problem has an objective ``F(x)``, constraint functions
``g(x)`` with lower/upper faces ``cl`` and ``cu``, and variable bounds ``lb`` and
``ub``. Exact fixed-box bindings are resolved through the current canonical
inverse data by variable and constraint identity, not by variable names or
arbitrary user equalities. Let ``E`` insert the free coordinates and ``f`` hold
the selected fixed values. The reduced problem evaluates::

    x = E @ y + f
    Freduced(y) = F(x)
    greduced(y) = g(x)[kept]

Only the corresponding defining equality rows are removed. Before the solve,
each selected row must have zero equality bounds, vanish at the adjusted start,
and have a Jacobian row involving only its associated fixed coordinate. Other
constraints and the free-coordinate variable bounds retain their original
meaning. With no selected boxes, the map is an identity.

The shared boundary first verifies the complete assigned canonical start,
including introduced auxiliary coordinates. This bridge records that full
start, replaces selected exact coordinates with their defining values, and
selects the actual reduced ``x0`` delivered to IPOPT. Free start coordinates are
unchanged. Immutable evidence retains assigned, adjusted, and reduced starts,
the original canonical variable layout, and an exact expansion round-trip check.
An assigned value inconsistent with an exact box is therefore an explicit,
recorded adjustment, not an untracked change of initialization.

Derivative and solver integration
---------------------------------
``ReducedOracles`` wraps the installed CVXPY nonlinear oracles rather than
implementing new derivatives. It evaluates the original objective and
constraints at expanded ``x``, selects the free gradient entries, and filters
the Jacobian to retained rows/free columns and the Lagrangian Hessian to
free/free entries. Sparse COO structure indices and their value arrays use the
same masks and remapping, preserving their alignment. Reduced constraint
multipliers are expanded with zeros for dropped equalities before Hessian
evaluation. Forward objective/constraint evaluations refresh the installed
oracles' cached state before derivative callbacks.

The stock IPOPT adapter resolves options and runs the solve using one ephemeral
``oracles`` cache entry containing this wrapper. Prepared warm starts and
non-exact Hessian modes are unsupported. Required adapter capabilities and bound
data are checked before execution; infinite bound faces are allowed, NaNs are
not. No solver method or registry is patched globally, and no experiment code
is imported. Compatibility with the private adapter/oracle interfaces must be
verified against the installed CVXPY version.

Restoration and evidence
------------------------
For native IPOPT statuses ``0``, ``1``, and ``6``, expand the returned primal to
the original dimension before the shared boundary invokes CVXPY inversion.
The original objective is evaluated directly, so no separate substitution
offset is needed. Retained constraint and bound multipliers are expanded for
diagnostic stationarity checks; dropped equality multipliers are reconstructed
from ``grad(F) + J.T @ z - mult_x_L + mult_x_U = 0``. These reconstructed
multipliers are labelled evidence, not native solver outputs, and do not change
the stock adapter's public constraint-dual publication contract.

``OPFBuild.preparation_evidence`` separates immutable native reduced-space
results and elapsed time from original-space constraint/bound residuals,
stationarity, objective reconstruction, and start-mapping checks. These are
diagnostics, not scientific acceptance gates or optimality certificates. Other
native statuses retain failure evidence with restoration marked unavailable;
reduced primals are not published as physical results. Old public values are
cleared after start capture and before the numerical call. The standalone
wrapper maps failure statuses through the stock adapter and clears public
results on exceptions, while current native failure evidence may remain.
"""
from __future__ import annotations

from typing import Any
from time import perf_counter

import cvxpy as cp
import numpy as np
from scipy import sparse

from cvxopf._numerical_preparation import (
    FixedCoordinateMap, PreparationEvidence, clear_prepared_result,
    finite_vector, prepared_options, resolve_fixed_map,
)


class ReducedOracles:
    """Restrict original oracles and COO indices together; no new derivatives."""

    def __init__(self, original: Any, mapping: FixedCoordinateMap) -> None:
        self.original, self.mapping = original, mapping
        rows, cols = original.jacobianstructure()
        self.jrows, self.jcols = np.asarray(rows), np.asarray(cols)
        row_map = np.full(mapping.row_count, -1, dtype=int)
        col_map = np.full(mapping.full_size, -1, dtype=int)
        row_map[mapping.kept] = np.arange(mapping.kept.size)
        col_map[mapping.free] = np.arange(mapping.free.size)
        self.jmask = (row_map[self.jrows] >= 0) & (col_map[self.jcols] >= 0)
        self.jstructure = (row_map[self.jrows[self.jmask]], col_map[self.jcols[self.jmask]])
        rows, cols = original.hessianstructure()
        self.hrows, self.hcols = np.asarray(rows), np.asarray(cols)
        self.hmask = (col_map[self.hrows] >= 0) & (col_map[self.hcols] >= 0)
        self.hstructure = (col_map[self.hrows[self.hmask]], col_map[self.hcols[self.hmask]])

    def _forward(self, y: np.ndarray) -> np.ndarray:
        x = self.mapping.expand(y)
        self.original.objective(x)
        self.original.constraints(x)
        return x

    def objective(self, y: np.ndarray) -> float:
        return float(self.original.objective(self.mapping.expand(y)))

    def constraints(self, y: np.ndarray) -> np.ndarray:
        return np.asarray(self.original.constraints(self.mapping.expand(y)))[self.mapping.kept]

    def gradient(self, y: np.ndarray) -> np.ndarray:
        x = self._forward(y)
        return np.asarray(self.original.gradient(x))[self.mapping.free]

    def jacobianstructure(self) -> tuple[np.ndarray, np.ndarray]:
        return self.jstructure

    def jacobian(self, y: np.ndarray) -> np.ndarray:
        x = self._forward(y)
        return np.asarray(self.original.jacobian(x))[self.jmask]

    def hessianstructure(self) -> tuple[np.ndarray, np.ndarray]:
        return self.hstructure

    def hessian(self, y: np.ndarray, duals: np.ndarray, obj_factor: float) -> np.ndarray:
        x = self._forward(y)
        full_duals = np.zeros(self.mapping.row_count)
        full_duals[self.mapping.kept] = finite_vector(duals, self.mapping.kept.size, "reduced multipliers")
        return np.asarray(self.original.hessian(x, full_duals, obj_factor))[self.hmask]

    def update_params(self, problem: Any) -> None:
        self.original.update_params(problem)


def solve_at_verified_boundary(
    build: Any, solver: Any, data: dict[str, Any], inverse: list[Any],
    verbose: bool, options: dict[str, Any],
) -> dict[str, Any]:
    """Use stock IPOPT option resolution with one ephemeral reduced oracle."""
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    from cvxpy.reductions.solvers.nlp_solvers.nlp_solver import Oracles

    required = ("x0", "cl", "cu", "lb", "ub", "_bounds", "problem")
    if not all(name in data for name in required):
        raise RuntimeError("installed IPOPT adapter lacks required bounds/start capabilities")
    if any(np.isnan(data[name]).any() for name in ("cl", "cu", "lb", "ub")):
        raise ValueError("nonfinite IPOPT bound data")
    mapping = resolve_fixed_map(build._exact_boxes, inverse[:-1], inverse[-1],
                                data["_bounds"].problem.constraints,
                                len(data["x0"]), len(data["cl"]))
    assigned = finite_vector(data["x0"], mapping.full_size, "assigned IPOPT start").copy()
    adjusted = assigned.copy()
    adjusted[mapping.fixed] = mapping.values
    y0 = mapping.select(adjusted)
    if not np.array_equal(mapping.expand(y0), adjusted):
        raise RuntimeError("prepared IPOPT start round trip failed")
    original = Oracles(data["_bounds"].new_problem, verbose=verbose, use_hessian=True)
    wrapper = ReducedOracles(original, mapping)
    original.objective(adjusted)
    g = np.asarray(original.constraints(adjusted))
    jvalues = np.asarray(original.jacobian(adjusted))
    jac = sparse.coo_array((jvalues, (wrapper.jrows, wrapper.jcols)),
                           shape=(mapping.row_count, mapping.full_size)).tocsr()
    for row, col in zip(mapping.dropped, mapping.fixed):
        vector = jac[[row], :].tocoo()
        vector.eliminate_zeros()
        if (data["cl"][row] != 0 or data["cu"][row] != 0 or g[row] != 0
                or vector.nnz != 1 or vector.col[0] != col):
            raise ValueError("selected AC row does not define its fixed coordinate")
    reduced = dict(data, x0=y0.copy(), lb=data["lb"][mapping.free], ub=data["ub"][mapping.free],
                   cl=data["cl"][mapping.kept], cu=data["cu"][mapping.kept])
    layout = []
    for variable in data["problem"].variables():
        offset = inverse[-1].var_offsets[variable.id]
        layout.append((variable.id, tuple(variable.shape), offset, offset + variable.size))
    # Clear old public solution values only after start capture. The original
    # oracle has already captured the graph/Parameters and has its own state.
    clear_prepared_result(build)
    started = perf_counter()
    raw = IPOPT.solve_via_data(solver, reduced, False, verbose, options,
                              solver_cache={"oracles": wrapper})
    native = dict(raw)
    native["status"] = int(raw["status"])
    native["elapsed_seconds"] = perf_counter() - started
    checks: dict[str, Any] = {"restoration_available": False, "start_round_trip": True}

    def evidence() -> PreparationEvidence:
        return PreparationEvidence(mapping, native, checks, np.ones(mapping.free.size),
                                   np.ones(mapping.kept.size), assigned_x0=assigned,
                                   adjusted_x0=adjusted, reduced_x0=y0, start_layout=tuple(layout))

    build._preparation_evidence = evidence()
    if raw["status"] not in (0, 1, 6):
        return raw
    x = mapping.expand(finite_vector(raw["x"], mapping.free.size, "native IPOPT primal"))
    full_g = finite_vector(original.constraints(x), mapping.row_count, "original constraints")
    original.objective(x)
    gradient = finite_vector(original.gradient(x), mapping.full_size, "original gradient")
    jac = sparse.coo_array((finite_vector(original.jacobian(x), wrapper.jrows.size, "original Jacobian"), (wrapper.jrows, wrapper.jcols)),
                           shape=(mapping.row_count, mapping.full_size)).tocsr()
    duals = np.zeros(mapping.row_count)
    duals[mapping.kept] = finite_vector(raw["mult_g"], mapping.kept.size, "native IPOPT constraint multipliers")
    lower, upper = np.zeros(mapping.full_size), np.zeros(mapping.full_size)
    lower[mapping.free] = finite_vector(raw["mult_x_L"], mapping.free.size, "native lower multipliers")
    upper[mapping.free] = finite_vector(raw["mult_x_U"], mapping.free.size, "native upper multipliers")
    residual = gradient + jac.T @ duals - lower + upper
    coefficients = np.array([jac[row, col] for row, col in zip(mapping.dropped, mapping.fixed)])
    duals[mapping.dropped] = -residual[mapping.fixed] / coefficients
    if not np.isfinite(duals).all():
        raise ValueError("nonfinite reconstructed AC multipliers")
    reconstructed_cost = float(original.objective(x))
    if not np.isfinite(reconstructed_cost) or not np.isfinite(float(raw["obj_val"])):
        raise ValueError("nonfinite IPOPT objective restoration")
    violations = np.maximum(data["cl"] - full_g, full_g - data["cu"])
    bound_violations = np.maximum(data["lb"] - x, x - data["ub"])
    checks.update(restoration_available=True,
                  primal_residual=float(np.max(violations, initial=0)),
                  bound_residual=float(np.max(bound_violations, initial=0)),
                  stationarity=float(np.max(np.abs(gradient + jac.T @ duals - lower + upper), initial=0)),
                  objective_reconstruction=abs(reconstructed_cost - float(raw["obj_val"])),
                  reconstructed_equality_duals=duals[mapping.dropped],
                  reconstructed_equality_rows=mapping.dropped)
    if not all(np.isfinite(value) for value in checks.values() if np.isscalar(value)):
        raise ValueError("nonfinite original-space AC evidence")
    build._preparation_evidence = evidence()
    # Public IPOPT inversion uses only x/status/objective, not diagnostic duals.
    return dict(raw, x=x)


def solve_prepared_ac(build: Any, kwargs: dict[str, Any]) -> None:
    from cvxopf._hierarchical_solver import _solve_ac_with_verified_x0
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    from cvxpy.reductions.solution import failure_solution
    from cvxpy.problems.problem import SolverStats

    build._preparation_evidence = None
    try:
        verbose, options = prepared_options(build, kwargs)
        for name in ("canon_backend", "gp", "qcp", "requires_grad", "enforce_dpp", "ignore_dpp", "solver_path"):
            if name in options:
                raise ValueError(f"prepared AC does not support {name}")
        run = _solve_ac_with_verified_x0(build, None, solver_options=dict(options, verbose=verbose))
        if run.exception:
            raise cp.error.SolverError(run.exception)
        evidence = build._preparation_evidence
        if evidence is not None and not evidence.checks["restoration_available"]:
            clear_prepared_result(build)
            status = IPOPT.STATUS_MAP[evidence.native["status"]]
            failure = failure_solution(status, {"solve_time": evidence.native["elapsed_seconds"],
                                                "num_iters": evidence.native.get("num_iters")})
            build.prob._status, build.prob._value, build.prob._solution = status, failure.opt_val, failure
            build.prob._solver_stats = SolverStats.from_dict(failure.attr, cp.IPOPT)
            if status in cp.settings.ERROR:
                raise cp.error.SolverError(f"IPOPT failed with status {evidence.native['status']}")
    except Exception:
        clear_prepared_result(build)
        raise
