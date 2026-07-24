"""
Dispatchable generator model for AC-OPF, DC-OPF, and single-node dispatch.

This module is the first-class component for conventional dispatchable
generators, following the pattern established by hvdc.py (Milestone 7). It
co-locates the dataclass, validation, incidence, operating constraints,
coupling-constraint slot, injection builder, and cost expression, and imports
only cvxpy, numpy, stdlib, and the re-exported poly_cost_expr from cost.py.

DispatchableGenerator is the target primary generation API. Constructor
integration is staged in Milestone 16: once wired, build_opf(generators=...)
will take a list of DispatchableGenerator and generators=None will fall back to
gen_from_matpower(case). Unlike storage/nondispatchable/HVDC (where None means
"none present"), None will mean "read from the case" -- a system always has
generators.

Units: generator variables (Pg, Qg) are in per-unit internally, matching the
conventional-generator convention used throughout cvxopf (unlike storage and
nondispatchable, which are in engineering units). Because Pg is already
per-unit, the injection Cg @ Pg needs no baseMVA scaling -- injections returns
(Cg @ Pg, None); the second slot exists only to match the HVDC interface shape.

Cost: cost expressions receive Pg in MW (baseMVA * Pg). gen_cost_expr wraps
poly_cost_expr; the DCP-critical explicit-monomial construction lives in
poly_cost_expr (cost.py) and is re-exported here so cost.py stays the single
source of truth for the cost math.

Import chain:
  generator.py             ->  cvxpy, numpy, stdlib, cost.poly_cost_expr
  problem.py               ->  generator.py (re-exports DispatchableGenerator,
                               gen_from_matpower)
  ac_problem.py            ->  generator.py
  dc_problem.py            ->  generator.py
  singlenode_dc_problem.py ->  generator.py
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import cvxpy as cp

from cvxopf.cost import poly_cost_expr


# MATPOWER column indices (gen and gencost tables)
_GEN_BUS = 0
_PG = 1
_QG = 2
_QMAX = 3
_QMIN = 4
_VG = 5
_MBASE = 6
_GEN_STATUS = 7
_PMAX = 8
_PMIN = 9

_COST_MODEL = 0
_COST_STARTUP = 1
_COST_SHUTDOWN = 2
_COST_NCOST = 3


@dataclass
class DispatchableGenerator:
    """
    Parameters for a single dispatchable (conventional) generator.

    The single generator representation across the package: both
    gen_from_matpower (parse from MATPOWER arrays) and make_singlenode_case
    (build a minimal case) converge on it.

    Attributes
    ----------
    bus : int
        Bus ID in external (MATPOWER) numbering. Remapped to internal 0-based
        index during problem construction via ext_to_int.
    p_max_mw : float
        Real power upper bound Pmax (MW).
    p_min_mw : float
        Real power lower bound Pmin (MW). Default 0.0.
    q_max_mvar : float
        Reactive power upper bound Qmax (MVAr). AC only. Default 0.0.
    q_min_mvar : float
        Reactive power lower bound Qmin (MVAr). AC only. Default 0.0.
    cost_type : {"polynomial", "piecewise_linear"}
        Cost-function type. Default "polynomial".
    cost_coeffs : tuple of float or None
        Polynomial coefficients, lowest-power first. For example,
        ``(c0, c1, c2)`` represents ``c2*Pg^2 + c1*Pg + c0`` with Pg in MW.
        Used only when ``cost_type="polynomial"``. None means zero cost.
    cost_points : tuple of (float, float) pairs or None
        Piecewise-linear ``(power_MW, cost)`` breakpoints. Used only when
        ``cost_type="piecewise_linear"`` and must contain at least two points
        with strictly increasing power coordinates. Evaluation and any
        lower-convex-hull treatment remain exclusively in ``cost.py``.
    startup : float
        Startup cost ($). Carried for round-trip fidelity with the MATPOWER
        gencost table. Inert in the current continuous OPF (startup/shutdown
        costs are only meaningful under unit commitment, which cvxopf does not
        model). Retained so a future convex relaxation of unit commitment can
        use it without retrofitting the data model. Default 0.0.
    shutdown : float
        Shutdown cost ($). Same status as startup -- carried, currently inert.
        Default 0.0.
    status : int
        In-service flag (1 = in service, 0 = out). Out-of-service generators
        get bounds zeroed and contribute no incidence column. Default 1.
    vg : float
        Voltage setpoint (p.u.), used by AC when enforce_vset is True.
        Default 1.0.
    """

    bus: int
    p_max_mw: float
    p_min_mw: float = 0.0
    q_max_mvar: float = 0.0
    q_min_mvar: float = 0.0
    cost_type: str = "polynomial"
    cost_coeffs: tuple | None = None
    cost_points: tuple | None = None
    startup: float = 0.0
    shutdown: float = 0.0
    status: int = 1
    vg: float = 1.0


def _validate_generators(gens: list, ext_bus_ids: set) -> None:
    """
    Validate a list of DispatchableGenerator objects.

    Raises
    ------
    ValueError
        If any generator fails a check, with an indexed message.
    """
    if not gens:
        return
    for i, g in enumerate(gens):
        if g.bus not in ext_bus_ids:
            raise ValueError(
                f"Generator {i}: bus {g.bus} not in case bus table. "
                f"Valid IDs: {sorted(ext_bus_ids)}"
            )
        if g.p_min_mw > g.p_max_mw:
            raise ValueError(
                f"Generator {i}: p_min_mw ({g.p_min_mw}) must be <= "
                f"p_max_mw ({g.p_max_mw})"
            )
        if g.q_min_mvar > g.q_max_mvar:
            raise ValueError(
                f"Generator {i}: q_min_mvar ({g.q_min_mvar}) must be <= "
                f"q_max_mvar ({g.q_max_mvar})"
            )
        if g.cost_type == "polynomial":
            if g.cost_points is not None:
                raise ValueError(
                    f"Generator {i}: cost_points is only valid when "
                    "cost_type='piecewise_linear'"
                )
            coeffs = (0.0,) if g.cost_coeffs is None else tuple(g.cost_coeffs)
            if not coeffs:
                raise ValueError(f"Generator {i}: cost_coeffs must not be empty")
            if len(coeffs) == 3 and coeffs[2] < 0:
                raise ValueError(
                    f"Generator {i}: cost_coeffs c2 must be >= 0 (convex "
                    f"quadratic), got {coeffs[2]}"
                )
        elif g.cost_type == "piecewise_linear":
            if g.cost_coeffs is not None:
                raise ValueError(
                    f"Generator {i}: cost_coeffs is only valid when "
                    "cost_type='polynomial'"
                )
            if g.cost_points is None or len(g.cost_points) < 2:
                raise ValueError(
                    f"Generator {i}: piecewise-linear cost requires at least "
                    "two cost_points"
                )
            if any(len(point) != 2 for point in g.cost_points):
                raise ValueError(
                    f"Generator {i}: each cost_points entry must be a "
                    "(power, cost) pair"
                )
            powers = np.asarray([point[0] for point in g.cost_points], dtype=float)
            if np.any(np.diff(powers) <= 0):
                raise ValueError(
                    f"Generator {i}: cost_points power coordinates must be "
                    "strictly increasing"
                )
        else:
            raise ValueError(
                f"Generator {i}: unknown cost_type={g.cost_type!r}; expected "
                "'polynomial' or 'piecewise_linear'"
            )


def make_generator_incidence(gens: list, nb: int, ext_to_int: dict) -> np.ndarray:
    """
    Build the generator-to-bus incidence matrix Cg, shape (nb, ng).

    Cg[i, k] = 1.0 if in-service generator k maps to internal bus i, else 0.
    Out-of-service generators (status != 1) produce a zero column, matching
    network.make_incidence_matrix. Returns np.empty((nb, 0)) for no generators.
    """
    ng = len(gens)
    if ng == 0:
        return np.empty((nb, 0))
    Cg = np.zeros((nb, ng))
    for k, g in enumerate(gens):
        if int(g.status) == 1:
            Cg[ext_to_int[g.bus], k] = 1.0
    return Cg


def generator_bounds(gens: list, baseMVA: float) -> tuple:
    """
    Return (Pgmin, Pgmax, Qgmin, Qgmax) as (ng,) per-unit numpy arrays.

    Out-of-service generators (status != 1) have all four bounds zeroed,
    matching the AC/DC parse functions.
    """
    ng = len(gens)
    Pgmin = np.zeros(ng)
    Pgmax = np.zeros(ng)
    Qgmin = np.zeros(ng)
    Qgmax = np.zeros(ng)
    for k, g in enumerate(gens):
        if int(g.status) != 1:
            continue
        Pgmin[k] = g.p_min_mw / baseMVA
        Pgmax[k] = g.p_max_mw / baseMVA
        Qgmin[k] = g.q_min_mvar / baseMVA
        Qgmax[k] = g.q_max_mvar / baseMVA
    return Pgmin, Pgmax, Qgmin, Qgmax


def generator_gencost(gens: list) -> np.ndarray:
    """
    Serialize generator cost data to a MATPOWER gencost array.

    This function translates data only. Polynomial and PWL cost modeling stays
    in cost.poly_cost_expr. Rows are padded to the widest row, as MATPOWER does
    for mixed cost models.
    """
    rows = []
    for g in gens:
        if g.cost_type == "polynomial":
            coeffs = (0.0,) if g.cost_coeffs is None else tuple(g.cost_coeffs)
            payload = list(reversed(coeffs))
            model = 2
            ncost = len(coeffs)
        elif g.cost_type == "piecewise_linear":
            points = () if g.cost_points is None else tuple(g.cost_points)
            payload = [value for point in points for value in point]
            model = 1
            ncost = len(points)
        else:
            raise ValueError(
                f"unknown cost_type={g.cost_type!r}; expected 'polynomial' "
                "or 'piecewise_linear'"
            )
        rows.append(
            [model, g.startup, g.shutdown, ncost, *payload]
        )

    width = max((len(row) for row in rows), default=5)
    gencost = np.zeros((len(rows), width))
    for k, row in enumerate(rows):
        gencost[k, :len(row)] = row
    return gencost


def generator_matpower_gen(gens: list, baseMVA: float) -> np.ndarray:
    """Serialize generators to a MATPOWER gen array with 21 columns."""
    gen = np.zeros((len(gens), 21))
    for k, g in enumerate(gens):
        gen[k, _GEN_BUS] = g.bus
        gen[k, _PG] = 0.0
        gen[k, _QG] = 0.0
        gen[k, _QMAX] = g.q_max_mvar
        gen[k, _QMIN] = g.q_min_mvar
        gen[k, _VG] = g.vg
        gen[k, _MBASE] = baseMVA
        gen[k, _GEN_STATUS] = g.status
        gen[k, _PMAX] = g.p_max_mw
        gen[k, _PMIN] = g.p_min_mw
    return gen


def _case_with_generators(case: dict, gens: list) -> dict:
    """
    Return a shallow case copy with ``gen`` and ``gencost`` built from gens.

    This is the normalization seam for the first-class generator API. It lets
    callers supply a network-only MATPOWER-style case (bus, branch, baseMVA)
    together with ``generators=`` while preserving the existing case
    validation and reindexing path internally. The input case is not mutated.
    """
    ext_bus_ids = set(np.asarray(case["bus"])[:, 0].astype(int).tolist())
    _validate_generators(gens, ext_bus_ids)
    normalized = dict(case)
    normalized["gen"] = generator_matpower_gen(gens, float(case["baseMVA"]))
    normalized["gencost"] = generator_gencost(gens)
    return normalized


def injections(
    gens: list,
    Pg: cp.Variable,
    ext_to_int: dict,
    *,
    nb: int | None = None,
) -> tuple:
    """
    Build the generator nodal-balance addend Cg @ Pg.

    Pg is created by the calling problem builder and passed in; this function
    does not instantiate any cp.Variable.

    Returns
    -------
    injection_expr : cp.Expression
        Cg @ Pg, shape (nb,). Per-unit -- no baseMVA scaling (Pg is already
        per-unit, unlike storage/ND engineering-unit variables).
    scaling : None
        Always None for generators; present to match the HVDC injection
        interface (which returns an inv_baseMVA cp.Parameter).
    """
    if nb is None:
        nb = len(ext_to_int)
    Cg = make_generator_incidence(gens, nb, ext_to_int)
    return Cg @ Pg, None


def ac_operating_constraints(Pg: cp.Variable, Pgmin, Pgmax) -> list:
    """
    AC per-generator real-power bounds: Pgmin <= Pg <= Pgmax (affine, DCP).

    Reactive bounds (Qg) are applied by the AC constructor on the Qg variable
    directly (no DC analogue), so they are not part of this method.
    """
    return [Pg >= Pgmin, Pg <= Pgmax]


def dc_operating_constraints(Pg: cp.Variable, Pgmin, Pgmax) -> list:
    """
    DC per-generator real-power bounds. Identical to the AC region (both are
    the affine box Pgmin <= Pg <= Pgmax), so pass-through to
    ac_operating_constraints. The ac_*/dc_* fork exists so the interface shape
    matches components where AC and DC diverge.

    Under Milestone 16, DC uses a per-generator Pg (ng,) wired into flow
    conservation via Cg @ Pg (see injections), rather than a nodal p_gen (nb,)
    with gen_bus-indexed bounds and nogen zeroing. The nodal form is redundant:
    Cg has no column at non-generator buses, so zero injection there is
    automatic.
    """
    return ac_operating_constraints(Pg, Pgmin, Pgmax)


def coupling_constraints(*args, **kwargs) -> list:
    """
    Cross-step coupling constraints for generators. Empty today.

    Generators are memoryless in the current model. This slot exists so future
    ramp limits / min-up-down have a defined home without re-architecting the
    component interface (Milestone 16 contract).
    """
    return []


def gen_cost_expr(gencost: np.ndarray, Pg_MW) -> cp.Expression:
    """
    Total generation cost expression. Thin wrapper over poly_cost_expr.

    Pg_MW is generator real power in MW (baseMVA * Pg), matching AC/DC. The
    DCP-critical explicit-monomial construction lives in poly_cost_expr
    (cost.py) and is kept there so cost.py is the single source of truth.
    """
    return poly_cost_expr(gencost, Pg_MW)


def gen_from_matpower(gen: np.ndarray, gencost: np.ndarray) -> list:
    """
    Build DispatchableGenerator objects from MATPOWER gen/gencost tables.

    Fallback path when build_opf(generators=None): the case dict's gen/gencost
    arrays are converted to the canonical component list. Inverse of
    generator_gencost plus the bus/bound vectorizers. Order is preserved so Cg
    columns and gencost rows line up positionally with the case.

    Model-2 polynomial rows are read highest-power-first and reversed to the
    component's lowest-power-first convention. Model-1 rows are converted to
    explicit ``(power, cost)`` breakpoint pairs. ``generator_gencost`` is the
    inverse serialization.
    """
    gens = []
    ng = gen.shape[0]
    for k in range(ng):
        row = gen[k]
        cost_type = None
        cost_coeffs = None
        cost_points = None
        startup = 0.0
        shutdown = 0.0
        if gencost is not None:
            startup = float(gencost[k, _COST_STARTUP])
            shutdown = float(gencost[k, _COST_SHUTDOWN])
            n = int(gencost[k, _COST_NCOST])
            model = int(gencost[k, _COST_MODEL])
            if model == 2:
                coeffs_hi_first = tuple(float(v) for v in gencost[k, 4:4 + n])
                cost_type = "polynomial"
                cost_coeffs = tuple(reversed(coeffs_hi_first))
            elif model == 1:
                payload = gencost[k, 4:4 + 2 * n]
                cost_type = "piecewise_linear"
                cost_points = tuple(
                    (float(payload[2 * j]), float(payload[2 * j + 1]))
                    for j in range(n)
                )
            else:
                raise ValueError(
                    f"Generator {k}: unrecognised gencost MODEL={model}. "
                    "Expected 1 or 2."
                )
        gens.append(
            DispatchableGenerator(
                bus=int(row[_GEN_BUS]),
                p_max_mw=float(row[_PMAX]),
                p_min_mw=float(row[_PMIN]),
                q_max_mvar=float(row[_QMAX]),
                q_min_mvar=float(row[_QMIN]),
                cost_type=cost_type or "polynomial",
                cost_coeffs=cost_coeffs,
                cost_points=cost_points,
                startup=startup,
                shutdown=shutdown,
                status=int(row[_GEN_STATUS]),
                vg=float(row[_VG]),
            )
        )
    return gens
