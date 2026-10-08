"""Prepare a convex canonical problem locally, then restore its original space.

This module is the stock-CLARABEL solver bridge for opt-in numerical preparation
in SOCP, lossy DC, and single-node DC builds. It acts after model construction:
public CVXPY variables, component ownership, physical units, objective weights,
and result projections are unchanged. Device-limit normalization, when selected,
is already part of the assembled model; this bridge does not construct device
constraints. AC preparation belongs to ``_ac_preparation``.

Canonical transformations
-------------------------
The starting problem uses CLARABEL's convention::

    minimize    0.5 * x.T @ P @ x + c.T @ x
    subject to  A @ x + s = b,  s in K

``P`` is kept symmetric for the algebra; the stock adapter applies its own
upper-triangle delivery convention. Supported cones are zero, nonnegative, and
second-order cones. Unsupported cones, inconsistent dimensions, and nonfinite
data are rejected before the numerical call.

Exact fixed-box bindings are resolved by variable and constraint identities in
the current CVXPY inverse chain, not by names or arbitrary one-coefficient rows.
With ``E`` inserting the free coordinates and ``f`` holding the selected fixed
values, substitute ``x = E @ y + f``::

    Pfree = E.T @ P @ E
    cfree = E.T @ (c + P @ f)
    Afree = (A @ E)[kept, :]
    bfree = (b - A @ f)[kept]
    offset = c.T @ f + 0.5 * f.T @ P @ f

Only the identity-resolved defining equalities are dropped, after checking that
each row fixes exactly its associated coordinate. Other bounds, user equalities,
and cone rows remain. With no selected boxes, the coordinate map is an identity.

Optional ``joint5`` scaling sets ``y = D @ xhat`` and scales retained rows by
``R``. Here ``D`` and ``R`` denote positive diagonal matrices, stored as vectors.
Five simultaneous square-root infinity-norm passes balance ``P``, ``A``, ``c``,
and ``b``; cumulative scales are clipped to ``[1e-6, 1e6]`` and zero maxima are
left unchanged. Each SOC block shares one row scale, preserving its cone.
The solver receives ``D @ Pfree @ D``, ``R @ Afree @ D``, ``D @ cfree``, and
``R @ bfree``. There is no global objective multiplier. Without ``joint5``, both
scale vectors are ones.

Restoration and evidence
------------------------
For native ``Solved`` or ``AlmostSolved`` results, restore the free primal as
``y = D @ xhat``, retained slacks as ``s = shat / R``, and retained multipliers
as ``z = R * zhat`` (elementwise for the stored vectors). Reinsert fixed primal
coordinates and zero slacks for dropped equalities. Reconstruct their equality
multipliers from original-space stationarity rather than treating them as native
solver outputs. Add the substitution offset to both native objective values;
CVXPY's separate original objective constant is restored by the unchanged
inverse chain when the full-dimensional result is unpacked.

``OPFBuild.preparation_evidence`` keeps immutable native transformed-space
diagnostics separate from original-space primal, stationarity, complementarity,
cone, and objective-reconstruction checks. These checks are diagnostics, not
scientific acceptance gates or claims of improved convergence. Other native
statuses never publish transformed primals, duals, or infeasibility certificates
as physical results. Old public results are cleared before each attempt and on
exceptions; current native failure evidence may remain available.

Private CVXPY integration is confined to ``solve_prepared_convex``. Each call
constructs a fresh reduction chain using the build's canonicalization backend,
so changed Parameters are recanonicalized; prepared solver caching and warm
starts are disabled. The original inverse chain is retained for restoration.
No experiment implementation is imported and no solver method or registry is
patched globally. Compatibility with these private interfaces must be verified
against the installed CVXPY version.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import cvxpy as cp
import numpy as np
from scipy import sparse

from cvxopf._numerical_preparation import (
    FixedCoordinateMap, PreparationEvidence, clear_prepared_result,
    finite_vector, prepared_options, resolve_fixed_map,
)


def inf(value: Any) -> float:
    return float(np.max(np.abs(value), initial=0))


def cone_layout(dims: Any, rows: int) -> tuple[int, int, tuple[int, ...]]:
    if dims.exp or dims.psd or dims.p3d or getattr(dims, "pnd", ()):
        raise ValueError("prepared convex solving supports zero/nonnegative/SOC cones only")
    layout = (int(dims.zero), int(dims.nonneg), tuple(dims.soc))
    if layout[0] + layout[1] + sum(layout[2]) != rows:
        raise ValueError("invalid canonical cone dimensions")
    return layout


def validate_data(data: dict[str, Any]) -> None:
    A, P, b, c = (data[name] for name in ("A", "P", "b", "c"))
    m, n = A.shape
    if P.shape != (n, n) or b.shape != (m,) or c.shape != (n,):
        raise ValueError("canonical matrix/vector dimensions disagree")
    if not all(np.isfinite(v).all() for v in (A.data, P.data, b, c)):
        raise ValueError("nonfinite canonical data")
    if (P != P.T).nnz:
        raise ValueError("preparation requires symmetric quadratic data")
    cone_layout(data["dims"], m)


def substitute(data: dict[str, Any], mapping: FixedCoordinateMap) -> tuple[dict[str, Any], float]:
    validate_data(data)
    A, P, c = data["A"].tocsr(), data["P"].tocsc(), data["c"]
    if A.shape != (mapping.row_count, mapping.full_size):
        raise ValueError("coordinate map dimensions disagree")
    # Only the identity-resolved rows may be removed, and each must actually
    # define its associated selected coordinate and no other one.
    for row, col, value in zip(mapping.dropped, mapping.fixed, mapping.values):
        vector = A[[row], :].tocoo()
        vector.eliminate_zeros()
        if (row >= data["dims"].zero or vector.nnz != 1 or vector.col[0] != col
                or vector.data[0] == 0 or data["b"][row] != vector.data[0] * value):
            raise ValueError("selected equality is not its defining fixed box")
    f = mapping.expand(np.zeros(mapping.free.size))
    offset = float(c @ f + 0.5 * f @ (P @ f))
    dims = deepcopy(data["dims"])
    dims.zero -= mapping.dropped.size
    reduced = dict(data, P=P[mapping.free][:, mapping.free].tocsc(),
                   c=(c + P @ f)[mapping.free],
                   A=A[mapping.kept][:, mapping.free].tocsc(),
                   b=(data["b"] - A @ f)[mapping.kept], dims=dims)
    validate_data(reduced)
    if not np.isfinite(offset):
        raise ValueError("nonfinite substitution objective offset")
    return reduced, offset


def joint_scales(data: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Exactly five simultaneous square-root infinity passes, anchored objective."""
    validate_data(data)
    A, P = data["A"].tocsc(copy=True), data["P"].tocsc(copy=True)
    c, b = data["c"].copy(), data["b"].copy()
    R, D = np.ones(A.shape[0]), np.ones(A.shape[1])
    zero, nonneg, soc = cone_layout(data["dims"], A.shape[0])

    def maximum(matrix: Any, axis: int) -> np.ndarray:
        if not matrix.shape[axis]:
            return np.zeros(matrix.shape[1 - axis])
        return np.asarray(abs(matrix).max(axis=axis).toarray()).ravel()

    for _ in range(5):
        variable = np.maximum.reduce((maximum(P, 1), maximum(A, 0), abs(c)))
        constraint = np.maximum(maximum(A, 1), abs(b))
        start = zero + nonneg
        for size in soc:
            constraint[start:start + size] = np.max(constraint[start:start + size])
            start += size
        next_D = np.clip(D / np.sqrt(np.where(variable > 0, variable, 1)), 1e-6, 1e6)
        next_R = np.clip(R / np.sqrt(np.where(constraint > 0, constraint, 1)), 1e-6, 1e6)
        d, r = next_D / D, next_R / R
        P = (sparse.diags(d) @ P @ sparse.diags(d)).tocsc()
        A = (sparse.diags(r) @ A @ sparse.diags(d)).tocsc()
        c, b, D, R = d * c, r * b, next_D, next_R
    return D, R


def scaled_data(data: dict[str, Any], D: np.ndarray, R: np.ndarray) -> dict[str, Any]:
    finite_vector(D, data["c"].size, "variable scales")
    finite_vector(R, data["b"].size, "row scales")
    if np.any(D <= 0) or np.any(R <= 0):
        raise ValueError("scales must be positive")
    result = dict(data, P=(sparse.diags(D) @ data["P"] @ sparse.diags(D)).tocsc(),
                  A=(sparse.diags(R) @ data["A"] @ sparse.diags(D)).tocsc(),
                  b=R * data["b"], c=D * data["c"])
    validate_data(result)
    return result


def cone_error(vector: np.ndarray, layout: tuple[int, int, tuple[int, ...]], *, dual: bool) -> float:
    zero, nonneg, soc = layout
    error = 0.0 if dual else inf(vector[:zero])
    error = max(error, float(np.max(-vector[zero:zero + nonneg], initial=0)))
    start = zero + nonneg
    for size in soc:
        block = vector[start:start + size]
        error = max(error, float(np.linalg.norm(block[1:]) - block[0]))
        start += size
    return max(error, 0.0)


def restore(
    data: dict[str, Any], mapping: FixedCoordinateMap, raw: Any,
    D: np.ndarray, R: np.ndarray, offset: float,
) -> tuple[Any, dict[str, Any]]:
    x = mapping.expand(D * finite_vector(raw.x, D.size, "native primal"))
    s, z = np.zeros(mapping.row_count), np.zeros(mapping.row_count)
    s[mapping.kept] = finite_vector(raw.s, R.size, "native slack") / R
    z[mapping.kept] = finite_vector(raw.z, R.size, "native dual") * R
    gradient = data["P"] @ x + data["c"] + data["A"].T @ z
    coefficients = np.array([data["A"][row, col] for row, col in zip(mapping.dropped, mapping.fixed)])
    z[mapping.dropped] = -gradient[mapping.fixed] / coefficients
    primal_cost, dual_cost = float(raw.obj_val) + offset, float(raw.obj_val_dual) + offset
    if not all(np.isfinite(v).all() for v in (x, s, z, primal_cost, dual_cost)):
        raise ValueError("nonfinite restored solver result")
    layout = cone_layout(data["dims"], mapping.row_count)
    checks = dict(primal_residual=inf(data["A"] @ x + s - data["b"]),
                  stationarity=inf(data["P"] @ x + data["c"] + data["A"].T @ z),
                  complementarity=abs(float(s @ z)), primal_cone_error=cone_error(s, layout, dual=False),
                  dual_cone_error=cone_error(z, layout, dual=True),
                  objective_reconstruction=abs(0.5 * x @ (data["P"] @ x) + data["c"] @ x - primal_cost),
                  reconstructed_equality_duals=z[mapping.dropped].copy(),
                  reconstructed_equality_rows=mapping.dropped.copy(), restoration_available=True)
    if not all(np.isfinite(value) for value in checks.values() if np.isscalar(value)):
        raise ValueError("nonfinite original-space restoration evidence")
    restored = SimpleNamespace(status=raw.status, x=x, s=s, z=z,
                               obj_val=primal_cost, obj_val_dual=dual_cost,
                               iterations=raw.iterations, solve_time=raw.solve_time)
    return restored, checks


def solve_prepared_convex(build: Any, kwargs: dict[str, Any]) -> None:
    from cvxpy.reductions.solvers.conic_solvers.clarabel_conif import CLARABEL
    from cvxpy.problems.problem import SolverStats
    from cvxpy.reductions.solution import failure_solution

    build._preparation_evidence = None
    clear_prepared_result(build)
    try:
        verbose, options = prepared_options(build, kwargs)
        backend = options.pop("canon_backend", build.canonicalization_backend)
        if backend != build.canonicalization_backend:
            raise ValueError("prepared convex solving requires the build's canonical backend")
        for name in ("gp", "qcp", "requires_grad", "enforce_dpp", "ignore_dpp", "solver_path"):
            if name in options:
                raise ValueError(f"prepared execution does not support {name}")
        if not hasattr(build.prob, "_construct_chain"):
            raise RuntimeError("CVXPY fresh solving-chain capability unavailable")
        chain = build.prob._construct_chain(solver=cp.CLARABEL, canon_backend=backend,
                                           ignore_dpp=True, solver_opts=options)
        if type(chain.solver) is not CLARABEL:
            raise RuntimeError("expected stock CLARABEL adapter")
        data, inverse = chain.apply(build.prob)
        data = dict(data, P=data.get("P", sparse.csc_array((data["c"].size,) * 2)))
        validate_data(data)
        # Stuffing inverse owns canonical variable offsets; final adapter data
        # owns row order. The original inverse chain remains untouched.
        stuffing = inverse[-2]
        constraints = inverse[-1]["eq_constr"] + inverse[-1]["other_constr"]
        mapping = resolve_fixed_map(build._exact_boxes, inverse[:-1], stuffing,
                                    constraints, data["c"].size, data["b"].size)
        reduced, offset = substitute(data, mapping)
        D, R = (joint_scales(reduced) if build.numerical_preparation.canonical_scaling == "joint5"
                else (np.ones(mapping.free.size), np.ones(mapping.kept.size)))
        transformed = scaled_data(reduced, D, R)
        raw = chain.solver.solve_via_data(transformed, False, verbose, options, solver_cache=None)
        native = {name: getattr(raw, name, None) for name in (
            "x", "s", "z", "obj_val", "obj_val_dual", "r_prim", "r_dual",
            "iterations", "solve_time")}
        native.update(status=str(raw.status), cvxpy_objective_offset=float(inverse[-1]["offset"]),
                      absolute_gap=abs(float(raw.obj_val) - float(raw.obj_val_dual)))
        # Native vectors are retained even on failure, but never published as
        # engineering primals, duals or scaled infeasibility certificates.
        for name in ("x", "s", "z"):
            if native[name] is not None:
                native[name] = np.asarray(native[name], dtype=float)
        checks = dict(restoration_available=False)
        build._preparation_evidence = PreparationEvidence(mapping, native, checks, D, R, offset)
        if str(raw.status) not in ("Solved", "AlmostSolved"):
            status = CLARABEL.STATUS_MAP.get(str(raw.status), cp.settings.SOLVER_ERROR)
            solution = failure_solution(status, {"solve_time": raw.solve_time, "num_iters": raw.iterations})
            build.prob._status, build.prob._value, build.prob._solution = status, solution.opt_val, solution
            build.prob._solver_stats = SolverStats.from_dict(solution.attr, cp.CLARABEL)
            if status in cp.settings.ERROR:
                raise cp.error.SolverError(f"CLARABEL failed with {raw.status}")
            return
        result, checks = restore(data, mapping, raw, D, R, offset)
        build.prob.unpack_results(result, chain, inverse)
        build._preparation_evidence = PreparationEvidence(mapping, native, checks, D, R, offset)
    except Exception:
        clear_prepared_result(build)
        raise
