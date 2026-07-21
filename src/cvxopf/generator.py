"""
Dispatchable generator model for AC-OPF, DC-OPF, and single-node dispatch.

This module is the first-class component for conventional dispatchable
generators, following the pattern established by hvdc.py (Milestone 7). It
co-locates the dataclass, validation, incidence, operating constraints,
coupling-constraint slot, injection builder, and cost expression, and imports
only cvxpy, numpy, stdlib, and the re-exported poly_cost_expr from cost.py.

Generators are the primary cvxopf generation API. build_opf(generators=...)
takes a list of DispatchableGenerator; when generators=None, the constructor
falls back to gen_from_matpower(case), so standard MATPOWER/Pypower cases keep
working unchanged. Unlike storage/nondispatchable/HVDC (where None means "none
present"), None here means "read from the case" -- a system always has
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

from dataclasses import dataclass, field

import numpy as np
import cvxpy as cp

from cvxopf.cost import poly_cost_expr


# MATPOWER column indices (gen and gencost tables)
_GEN_BUS = 0
_QMAX = 3
_QMIN = 4
_VG = 5
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
    cost_coeffs : tuple of float
        Polynomial cost (c0, c1, c2), lowest-first (package-wide convention,
        matching HVDCLink). Acts on Pg in MW: c2*Pg^2 + c1*Pg + c0.
        Default (0, 0, 0).
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
    cost_coeffs: tuple = field(default_factory=lambda: (0.0, 0.0, 0.0))
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
        c0, c1, c2 = g.cost_coeffs
        if c2 < 0:
            raise ValueError(
                f"Generator {i}: cost_coeffs c2 must be >= 0 (convex "
                f"quadratic), got {c2}"
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
    Build a MATPOWER model-2 gencost array (ng, 7) from cost_coeffs.

    cost_coeffs is lowest-first (c0, c1, c2); the row stores highest-power-first
    [MODEL, STARTUP, SHUTDOWN, NCOST=3, c2, c1, c0]. Inverse of the reading in
    gen_from_matpower; consumed by gen_cost_expr / poly_cost_expr. startup and
    shutdown are carried through for round-trip fidelity (inert in the current
    continuous OPF).
    """
    ng = len(gens)
    gencost = np.zeros((ng, 7))
    for k, g in enumerate(gens):
        c0, c1, c2 = g.cost_coeffs
        gencost[k, _COST_MODEL] = 2
        gencost[k, _COST_STARTUP] = g.startup
        gencost[k, _COST_SHUTDOWN] = g.shutdown
        gencost[k, _COST_NCOST] = 3
        gencost[k, 4] = c2
        gencost[k, 5] = c1
        gencost[k, 6] = c0
    return gencost


def injections(gens: list, Pg: cp.Variable, ext_to_int: dict) -> tuple:
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

    Model-2 (polynomial) gencost rows are read highest-power-first and reversed
    to lowest-first (c0, c1, c2). Model-1 (piecewise-linear) rows are not
    representable as cost_coeffs; for those, cost_coeffs is left (0, 0, 0) and
    the constructor should use the gencost array verbatim (future extension).
    """
    gens = []
    ng = gen.shape[0]
    for k in range(ng):
        row = gen[k]
        cost = (0.0, 0.0, 0.0)
        startup = 0.0
        shutdown = 0.0
        if gencost is not None and int(gencost[k, _COST_MODEL]) == 2:
            startup = float(gencost[k, _COST_STARTUP])
            shutdown = float(gencost[k, _COST_SHUTDOWN])
            n = int(gencost[k, _COST_NCOST])
            coeffs_hi_first = [float(gencost[k, 4 + j]) for j in range(n)]
            coeffs_lo_first = list(reversed(coeffs_hi_first))
            while len(coeffs_lo_first) < 3:
                coeffs_lo_first.append(0.0)
            cost = tuple(coeffs_lo_first[:3])
        gens.append(
            DispatchableGenerator(
                bus=int(row[_GEN_BUS]),
                p_max_mw=float(row[_PMAX]),
                p_min_mw=float(row[_PMIN]),
                q_max_mvar=float(row[_QMAX]),
                q_min_mvar=float(row[_QMIN]),
                cost_coeffs=cost,
                startup=startup,
                shutdown=shutdown,
                status=int(row[_GEN_STATUS]),
                vg=float(row[_VG]),
            )
        )
    return gens
