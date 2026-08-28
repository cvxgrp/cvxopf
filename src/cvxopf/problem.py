"""
CVXPY problem builders for AC-OPF and DC-OPF.

Public API
----------
build_opf(case, *, formulation, options)
    Single time-step OPF. Returns OPFBuild.

build_opf_multistep(case, df_P=None, df_Q=None, *, T, formulation, options,
                    coupling_constraints, loads=None, df_load_p=None,
                    df_load_q=None)
    T time-step OPF as a single cp.Problem. Returns OPFBuild.

Deprecated (will be removed in a future release)
-------------------------------------------------
build_acopf(case, *, options)
build_acopf_multistep(case, df_P, df_Q, *, T, options, coupling_constraints)
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd
import cvxpy as cp

# Import storage and nondispatchable types for public API
from cvxopf.storage import StorageUnitIdeal
from cvxopf.nondispatchable import (
    NondispatchableUnit,
    _parse_nd_timeseries,
)
from cvxopf.hvdc import (
    HVDCLink,
    _hvdc_static_box,
    _parse_hvdc_timeseries,
)
from cvxopf.generator import DispatchableGenerator, _case_with_generators
from cvxopf.load import Load
from cvxopf._component_adapters import LoadInputs
from cvxopf.data import align_device_dataframe, load_timeseries_from_dataframe


TemporalAssembly = Literal["stepwise", "vectorized"]
CanonicalizationBackend = Literal["CPP", "SCIPY", "DNLP_IPOPT"]


# ---------------------------------------------------------------------------
# Options dataclass
# ---------------------------------------------------------------------------


@dataclass
class OPFOptions:
    """
    Formulation and solver options for build_opf / build_opf_multistep.

    Attributes
    ----------
    enforce_vset : bool
        If True, pin PV and slack bus voltage magnitudes to the Vg setpoint
        declared in the gen table. AC only. Default False.
    sparsity_tol : float
        Entries of Ybus with |G| <= tol AND |B| <= tol are treated as
        structural zeros and excluded from DNLP trig constraints.
        AC only. Default 0.0 (exact sparsity).
    init_flat : bool
        If True, initialise theta = 0 and v = 1 (flat start) before
        returning. AC only. Default True.
    enforce_branch_limits : bool
        If True, enforce MATPOWER rateA as an apparent-power limit at both
        terminals of every in-service branch with a finite positive rating.
        AC only. Requires sparsity_tol=0. Default True. Set False as an
        explicit compatibility escape hatch when ratings should remain inert.
    loss_weight : float
        Weighting factor lambda for line losses in the lossy DC objective:
            minimize delta * sum_t (G_t + loss_weight * L_t)
        where G is generation cost and L = sum_e r_e * p_flows_e^2.
        The loss proxy is dimensionless on the system base, so loss_weight
        supplies its objective-rate units. Default 1.0 is a unit-normalized
        regularizer, not a calibrated physical loss price.
        Reference: Convex Optimization with Smart Grid Examples,
        https://doi.org/10.2172/3018252
        DC only. Default 1.0.
    branch_limit_sentinel : float
        Substitute value (MW) used when a branch has rateA=0 in the
        MATPOWER case (meaning no limit is defined). A UserWarning is
        emitted for each affected branch. DC only. Default 1e6 MW.
    sparse_pq : bool
        If True (default), represent P and Q as flat (nnz,) CVXPY variables
        P_vec and Q_vec over the Ybus sparsity pattern, eliminating
        nb^2 - nnz trivially-zero variables and their P[Z]==0 / Q[Z]==0
        fixing constraints. Nodal injections are recovered via a
        precomputed (nb, nnz) scatter matrix Rp: p = Rp @ P_vec.
        If False, use legacy dense (nb, nb) variables P and Q with
        explicit zero-fixing constraints. Use False for research comparison
        and timing measurements against the sparse path.
        AC only. Default True.

    Notes
    -----
    None of the above fields affect the 'singlenode_dc' formulation.
    OPFOptions is accepted for API consistency but all fields are ignored
    when formulation='singlenode_dc'.
    """

    enforce_vset: bool = False
    sparsity_tol: float = 0.0
    init_flat: bool = True
    enforce_branch_limits: bool = True
    loss_weight: float = 1.0
    branch_limit_sentinel: float = 1e6
    sparse_pq: bool = True


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


@dataclass
class OPFBuild:
    """
    Container returned by the problem builders.

    Attributes
    ----------
    prob : cp.Problem
        The CVXPY problem. Call build.solve() to solve.
    variables : dict
        Named CVXPY variables.

        AC single-step keys (sparse_pq=True, default):
            theta, v, P_vec, Q_vec, p, q, Pg, Qg

        AC single-step keys (sparse_pq=False):
            theta, v, P, Q, p, q, Pg, Qg

        AC branch-terminal flow variables are retained in ``expressions``
        rather than this mapping.

        AC stepwise multi-step: each value is a list of length T.
        Vectorized multi-step: each value is one cp.Variable whose final axis
        is time. ``temporal_assembly`` distinguishes the two representations.

        DC single-step keys:
            p_flows, Pg

        DC stepwise multi-step: each value is a list of length T.
        Vectorized multi-step uses one time-last cp.Variable.

        Singlenode DC single-step keys:
            Pg

        Singlenode DC stepwise multi-step: each value is a list of length T.
        Vectorized multi-step uses one time-last cp.Variable.

        When storage is present:
            b (real power, MW), b_q (reactive power, MVAr, AC only),
            soc (state of charge, MWh)
        When one or more loads are sheddable:
            load_shed_fraction (dimensionless interruption fractions)

    data : dict
        Pre-computed numpy arrays and metadata.

        AC keys: baseMVA, nb, ng, nl, ref, pv, ext_to_int,
                 Ybus, G, B, E, Z, Pd, Qd, Cg,
                 Pgmin, Pgmax, Qgmin, Qgmax,
                 branch_from_bus_internal, branch_to_bus_internal,
                 branch_from_bus_external, branch_to_bus_external,
                 branch_status, branch_rate_a_mva, and
                 constrained_branch_indices. Branch metadata retains original
                 MATPOWER branch-table row order.
        DC keys: baseMVA, nb, ng, nl, ext_to_int,
                 A, Cg, r, f_max, Pd, gen_bus,
                 Pgmin, Pgmax, loss_weight
        Singlenode DC keys: baseMVA, nb (= 1), source_nb, ng, ext_to_int,
                 Pd_total, Pgmin, Pgmax, gencost
        Multi-step additionally: T, Pd_series (and Qd_series for AC).
        Presence of T is the explicit single- versus multi-step discriminator.
        For singlenode_dc, Pd_series has shape (T,) — one scalar per step,
        not (T, nb).
        When storage is present: ns, Cs, storage_bus,
                 storage_apparent_power_rating, storage_capacity,
                 storage_initial_soc, storage_device_ids,
                 storage_device_id_is_explicit, storage_delta,
                 storage_aging_weight,
                 storage_terminal_soc, storage_terminal_constraint,
                 storage_terminal_cost, storage_terminal_weight
        Imported first-class loads always add: nload, nsheddable, Cload,
                 load_device_ids, load_bus_external, load_bus_internal,
                 load_has_reactive, load_is_sheddable,
                 sheddable_load_indices, sheddable_load_device_ids,
                 load_max_shed_fraction, and
                 load_shedding_cost_per_mwh.

    formulation : str
        The formulation used to build this problem.
        One of: "ac", "lossy_dc", "singlenode_dc".

    is_convex : bool
        True for convex formulations (lossy_dc, singlenode_dc); False for
        nonconvex (ac). Controls solver defaults in solve().
    expressions : dict
        Named modeled CVXPY expressions used for solved-value reporting.
        Per-step reporting expressions are stored as one expression for a
        single-step build, lists of length T for stepwise multistep, and one
        time-last expression for vectorized multistep.
        AC branch-terminal real and reactive powers are retained in per unit
        as ``branch_p_from_pu``, ``branch_q_from_pu``,
        ``branch_p_to_pu``, and ``branch_q_to_pu``. Each value has shape
        ``(nl,)`` in a single-step build; each multistep value is a list of T
        expressions with shape ``(nl,)``. Result extraction scales the real
        channels to MW and reactive channels to MVAr, and derives
        ``branch_s_from`` and ``branch_s_to`` in MVA.
        Integrated stage costs (``generator_cost``, conditional
        ``storage_cost`` and ``hvdc_cost``, and lossy-DC ``dc_loss_cost``)
        are scalar horizon totals in both modes. Horizon-boundary expressions,
        including ``storage_terminal_cost``, are scalar expressions published
        once and are not multiplied by ``delta``.
        Loads publish per-step ``p_load``, ``q_load``, and ``p_load_served``
        expressions in engineering units. AC also publishes ``q_load_served``;
        DC formulations retain reactive input only for portable reporting.
        When shedding is configured, per-step expressions additionally include
        ``p_load_shed``, conditional AC ``q_load_shed``,
        ``load_shed_fraction``, and ``p_load_shed_total``. The integrated
        stage cost is ``load_shedding_cost``; horizon expressions are
        ``energy_not_served_by_load`` and ``energy_not_served``.
    temporal_assembly : {"stepwise", "vectorized"}
        Temporal graph representation retained as build provenance. Existing
        single- and multistep builders use ``"stepwise"`` until the M14
        horizon-vectorized implementation is selected explicitly.
    """

    prob: cp.Problem
    variables: dict[str, Any]
    data: dict[str, Any]
    formulation: str
    is_convex: bool
    expressions: dict[str, Any] = field(default_factory=dict)
    temporal_assembly: TemporalAssembly = "stepwise"

    @property
    def canonicalization_backend(self) -> CanonicalizationBackend:
        """Return the backend required by this formulation/assembly pair."""
        if not self.is_convex:
            return "DNLP_IPOPT"
        if self.temporal_assembly == "vectorized":
            return "SCIPY"
        return "CPP"

    def solve(self, **kwargs: Any) -> None:
        """
        Solve the OPF problem with appropriate solver defaults.

        For convex formulations (is_convex=True):
            solver=cp.CLARABEL, nlp=False (default)
        For nonconvex formulations (is_convex=False):
            solver=cp.IPOPT, nlp=True (default)

        Any keyword argument accepted by cp.Problem.solve() can be passed
        to override these defaults.

        Notes
        -----
        The nlp=True argument invokes CVXPY's DNLP canonicalization and
        bypasses the DCP check. It is required for AC-OPF (nonconvex) and
        must not be set for convex formulations.

        Examples
        --------
        build.solve()                  # uses formulation defaults
        build.solve(verbose=True)      # show solver output
        """
        if self.is_convex:
            kwargs.setdefault("solver", cp.CLARABEL)
            kwargs.setdefault("nlp", False)
            if self.canonicalization_backend == "SCIPY":
                backend = kwargs.setdefault("canon_backend", cp.SCIPY_CANON_BACKEND)
                if backend != cp.SCIPY_CANON_BACKEND:
                    raise ValueError(
                        "vectorized convex builds require SCIPY canonicalization"
                    )
        else:
            kwargs.setdefault("solver", cp.IPOPT)
            kwargs.setdefault("nlp", True)
            # IPOPT prints its banner and iteration log at the C level,
            # unaffected by CVXPY's `verbose` flag. Translate our own verbose
            # setting into IPOPT's own suppression options so `verbose=False`
            # actually silences IPOPT (banner via `sb`, log via `print_level`).
            # setdefault keeps these user-overridable (e.g. an explicit
            # print_level wins). When verbose=True, inject nothing so IPOPT's
            # output prints alongside CVXPY's.
            if not kwargs.get("verbose", False):
                kwargs.setdefault("print_level", 0)
                kwargs.setdefault("sb", "yes")
        kwargs.setdefault("verbose", False)
        self.prob.solve(**kwargs)


def _finalize_temporal_assembly(
    build: OPFBuild, temporal_assembly: TemporalAssembly
) -> OPFBuild:
    """Bind the selected temporal representation to build provenance."""
    build.temporal_assembly = temporal_assembly
    return build


# ---------------------------------------------------------------------------
# Dispatch tables (populated after imports to avoid circular imports)
# ---------------------------------------------------------------------------


def _get_single_builders() -> dict[str, Callable[..., OPFBuild]]:
    from cvxopf.ac_problem import _build_ac_single
    from cvxopf.dc_problem import _build_lossy_dc_single
    from cvxopf.singlenode_dc_problem import _build_singlenode_dc_single

    return {
        "ac": _build_ac_single,
        "lossy_dc": _build_lossy_dc_single,
        "singlenode_dc": _build_singlenode_dc_single,
    }


def _get_multistep_builders() -> dict[str, Callable[..., OPFBuild]]:
    from cvxopf.ac_problem import _build_ac_multistep
    from cvxopf.dc_problem import _build_lossy_dc_multistep
    from cvxopf.singlenode_dc_problem import _build_singlenode_dc_multistep

    return {
        "ac": _build_ac_multistep,
        "lossy_dc": _build_lossy_dc_multistep,
        "singlenode_dc": _build_singlenode_dc_multistep,
    }


def _validate_temporal_delta(delta: float) -> None:
    """Validate the global time-step duration at the public API boundary."""
    if isinstance(delta, (bool, np.bool_)) or not isinstance(delta, Real):
        raise TypeError(
            "delta must be a real scalar time-step duration in hours, "
            f"got {type(delta).__name__}"
        )
    if not np.isfinite(delta):
        raise ValueError(f"delta must be finite, got {delta}")
    if delta <= 0:
        raise ValueError(f"delta must be > 0, got {delta}")


def _normalize_multistep_load_inputs(
    case: dict[str, Any],
    df_P: pd.DataFrame | None,
    df_Q: pd.DataFrame | None,
    loads: list[Load] | None,
    df_load_p: pd.DataFrame | None,
    df_load_q: pd.DataFrame | None,
    T: int,
    formulation: str,
) -> tuple[LoadInputs, bool]:
    """Select and normalize exactly one public multistep load-input mode."""
    explicit = loads is not None
    if not explicit:
        if df_load_p is not None or df_load_q is not None:
            raise ValueError(
                "df_load_p/df_load_q require explicit loads; provide "
                "loads=[...] (or loads=[] for an explicit empty load set)"
            )
        if df_P is None:
            raise ValueError(
                "imported-load mode requires df_P; alternatively provide "
                "explicit loads and df_load_p/df_load_q"
            )
        if formulation == "ac" and df_Q is None:
            raise ValueError("imported-load AC mode requires df_Q")
        p_pu, q_pu = load_timeseries_from_dataframe(df_P, df_Q, case)
        if p_pu.shape[0] != T:
            raise ValueError(
                f"T={T} but df_P has {p_pu.shape[0]} rows; they must match."
            )
        base_mva = float(case["baseMVA"])
        return LoadInputs(p_pu * base_mva, q_pu * base_mva), False

    if df_P is not None or df_Q is not None:
        raise ValueError(
            "explicit-load mode does not accept legacy df_P/df_Q; use "
            "df_load_p/df_load_q keyed by Load.device_id"
        )
    assert loads is not None
    if df_load_p is None:
        p_mw = np.tile([unit.p_load_mw for unit in loads], (T, 1))
    else:
        p_mw = align_device_dataframe(df_load_p, loads, T, "df_load_p")
    if df_load_q is None:
        q_mvar = np.tile(
            [0.0 if unit.q_load_mvar is None else unit.q_load_mvar for unit in loads],
            (T, 1),
        )
    else:
        q_mvar = align_device_dataframe(df_load_q, loads, T, "df_load_q")
    has_reactive = np.asarray(
        [unit.q_load_mvar is not None for unit in loads], dtype=bool
    )
    if df_load_q is not None:
        has_reactive[:] = True
    return LoadInputs(p_mw, q_mvar, has_reactive), True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_opf(
    case: dict[str, Any],
    *,
    formulation: str = "ac",
    options: OPFOptions | None = None,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    hvdc: list[HVDCLink] | None = None,
    generators: list[DispatchableGenerator] | None = None,
    loads: list[Load] | None = None,
) -> OPFBuild:
    """
    Build a single time-step OPF problem.

    Parameters
    ----------
    case : dict
        MATPOWER-format case dict. Need not be pre-reindexed. When
        ``generators`` is supplied explicitly, ``gen`` and ``gencost`` may be
        omitted; temporary tables are generated without mutating this dict.
    formulation : str
        "ac"
            Full AC-OPF via DNLP (nonconvex). Solved by IPOPT.
        "lossy_dc"
            Lossy DC OPF (convex QP). Solved by CLARABEL.
            Reference: Convex Optimization with Smart Grid Examples,
            https://doi.org/10.2172/3018252
        "singlenode_dc"
            Single-node (copper-plate) DC dispatch. No network, no branch
            flows, no reactive power. Collapses all buses to one node and
            enforces scalar real power balance. Convex QP solved by CLARABEL.
            Accepts storage= and nondispatchable= in the same way as
            'lossy_dc'. Multistep df_Q is retained for load reporting but is
            not used in DC optimization.
    options : OPFOptions, optional
        Formulation and solver options. Defaults to OPFOptions().
    storage : list[StorageUnitIdeal] | None, optional
        List of energy storage units. If None, no storage is modelled.
        Each unit is a StorageUnitIdeal dataclass instance.
    delta : float, optional
        Time step duration in hours (default 1.0). Integrates every stage-cost
        rate over the interval, is passed to every component's temporal
        coupling hook, and is used by storage SoC dynamics. Horizon-boundary
        costs are not scaled. Must be a finite, strictly positive real scalar.
    nondispatchable : list[NondispatchableUnit] | None, optional
        List of nondispatchable generator units (wind, solar, etc.).
        If None, no nondispatchable generation is modelled.
        Each unit is a NondispatchableUnit dataclass instance.
    generators : list[DispatchableGenerator] | None, optional
        Dispatchable generators. If None, convert the case dict's
        ``gen``/``gencost`` tables to DispatchableGenerator objects at build
        time. Unlike other device arguments, None means MATPOWER fallback,
        not absence.
    loads : list[Load] | None, optional
        First-class loads. ``None`` imports one fixed load per MATPOWER bus;
        an explicit sequence replaces MATPOWER ``PD``/``QD`` demand. An empty
        sequence deliberately selects a zero-load model.

    Returns
    -------
    OPFBuild
        Call build.solve() to solve with appropriate defaults.
    """
    if options is None:
        options = OPFOptions()
    if generators is not None and len(generators) == 0:
        raise ValueError(
            "generators must contain at least one DispatchableGenerator; "
            "use generators=None to load generators from the case."
        )

    _validate_temporal_delta(delta)

    builders = _get_single_builders()
    if formulation not in builders:
        raise ValueError(
            f"Unknown formulation '{formulation}'. Supported: {sorted(builders.keys())}"
        )
    normalized_case = (
        _case_with_generators(case, generators) if generators is not None else case
    )
    return _finalize_temporal_assembly(
        builders[formulation](
            normalized_case,
            options,
            storage,
            delta,
            nondispatchable,
            hvdc=hvdc,
            generators=generators,
            loads=loads,
        ),
        "stepwise",
    )


def build_opf_multistep(
    case: dict[str, Any],
    df_P: pd.DataFrame | None = None,
    df_Q: pd.DataFrame | None = None,
    *,
    T: int,
    formulation: str = "ac",
    options: OPFOptions | None = None,
    coupling_constraints: list[cp.Constraint] | None = None,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    df_nd: pd.DataFrame | None = None,
    hvdc: list[HVDCLink] | None = None,
    df_hvdc_min: pd.DataFrame | None = None,
    df_hvdc_max: pd.DataFrame | None = None,
    generators: list[DispatchableGenerator] | None = None,
    loads: list[Load] | None = None,
    df_load_p: pd.DataFrame | None = None,
    df_load_q: pd.DataFrame | None = None,
    temporal_assembly: TemporalAssembly = "stepwise",
) -> OPFBuild:
    """
    Build a T-step OPF problem as a single cp.Problem.

    Parameters
    ----------
    case : dict
        MATPOWER-format case dict. When ``generators`` is supplied explicitly,
        ``gen`` and ``gencost`` may be omitted.
    df_P : pd.DataFrame | None, shape (T, nb)
        Legacy positional active load time series in MW. Required when
        ``loads is None`` and rejected when explicit loads are supplied.
    df_Q : pd.DataFrame | None, shape (T, nb)
        Reactive load time series in MVAr. It enters optimization only for
        formulation="ac". For formulation="lossy_dc" or
        formulation="singlenode_dc", it is retained as reactive load input
        metadata and reporting but is not used in optimization; a UserWarning
        is emitted.
    loads : list[Load] | None, optional
        ``None`` selects legacy MATPOWER-load mode using ``df_P``/``df_Q``.
        A supplied sequence selects explicit first-class loads, including an
        empty sequence for a zero-load model.
    df_load_p : pd.DataFrame | None, optional
        Explicit-load active trajectories in MW. Columns must exactly match
        unique ``Load.device_id`` values and are aligned to device order. If
        omitted, each load's static ``p_load_mw`` is tiled across the horizon.
    df_load_q : pd.DataFrame | None, optional
        Explicit-load reactive trajectories in MVAr with the same identity
        contract. May define a trajectory when static ``q_load_mvar`` is
        ``None``. DC formulations retain this input for reporting, warn, and
        do not use it in optimization. If omitted, static reactive values
        (with ``None`` represented numerically as zero) are tiled.
    T : int
        Number of time steps. Must equal the row count of every supplied load
        trajectory; static explicit-load fallback is tiled to this length.
    temporal_assembly : {"stepwise", "vectorized"}, optional
        Temporal graph representation. ``"stepwise"`` preserves the existing
        per-interval builder and remains the compatibility default. The
        ``"vectorized"`` selector is reserved by M14 and is rejected until
        its horizon-level implementation is available.
    formulation : str
        Same options as build_opf, including "singlenode_dc"
        (single-node copper-plate DC dispatch; df_Q reporting-only).
    options : OPFOptions, optional
        Formulation and solver options. Defaults to OPFOptions().
    coupling_constraints : list of cp.Constraint, optional
        Additional constraints linking variables across time steps (e.g.,
        battery SoC dynamics). Appended to the problem without modification.
        Default: empty list.
    storage : list[StorageUnitIdeal] | None, optional
        List of energy storage units. If None, no storage is modelled.
        Each unit is a StorageUnitIdeal dataclass instance. Storage SoC
        dynamics are automatically added as coupling constraints.
    delta : float, optional
        Time step duration in hours (default 1.0). Integrates every stage-cost
        rate over each interval, is passed to every component's temporal
        coupling hook, and is used by storage SoC dynamics. Horizon-boundary
        costs are not scaled. Must be a finite, strictly positive real scalar.
    nondispatchable : list[NondispatchableUnit] | None, optional
        List of nondispatchable generator units (wind, solar, etc.).
        If None, no nondispatchable generation is modelled.
        Each unit is a NondispatchableUnit dataclass instance.
    df_nd : pd.DataFrame | None, optional
        Nondispatchable available power time series in MW.
        Shape (T, nnd) where nnd = len(nondispatchable).
        Columns must exactly match the units' unique, nonempty ``device_id``
        values; arbitrary input order is aligned to device-list order.
        If None and nondispatchable is not None, the p_available field
        from each NondispatchableUnit is tiled across all T steps.
    generators : list[DispatchableGenerator] | None, optional
        Dispatchable generators. If None, convert the case dict's
        ``gen``/``gencost`` tables at build time.

    Returns
    -------
    OPFBuild
        build.variables contains lists of length T for each variable type.
    """
    if options is None:
        options = OPFOptions()
    if temporal_assembly not in {"stepwise", "vectorized"}:
        raise ValueError("temporal_assembly must be 'stepwise' or 'vectorized'")
    if temporal_assembly == "vectorized":
        raise NotImplementedError(
            "temporal_assembly='vectorized' is reserved for the M14b "
            "horizon-level implementation"
        )
    if coupling_constraints is None:
        coupling_constraints = []
    if generators is not None and len(generators) == 0:
        raise ValueError(
            "generators must contain at least one DispatchableGenerator; "
            "use generators=None to load generators from the case."
        )

    _validate_temporal_delta(delta)

    builders = _get_multistep_builders()
    if formulation not in builders:
        raise ValueError(
            f"Unknown formulation '{formulation}'. Supported: {sorted(builders.keys())}"
        )

    load_inputs, explicit_load_mode = _normalize_multistep_load_inputs(
        case, df_P, df_Q, loads, df_load_p, df_load_q, T, formulation
    )
    if formulation in {"lossy_dc", "singlenode_dc"} and (
        (not explicit_load_mode and df_Q is not None)
        or (explicit_load_mode and df_load_q is not None)
    ):
        source = "df_load_q" if explicit_load_mode else "df_Q"
        warnings.warn(
            f"{source} is retained as reactive load input metadata for "
            f"formulation={formulation!r}, but reactive power is not used "
            "in the DC optimization.",
            UserWarning,
            stacklevel=2,
        )

    # Normalize ND availability once at the public API boundary.
    if nondispatchable:
        if df_nd is None:
            warnings.warn(
                "df_nd not provided; tiling p_available from each "
                "NondispatchableUnit across all T steps.",
                UserWarning,
                stacklevel=2,
            )
            nd_available = np.tile(
                [unit.p_available for unit in nondispatchable], (T, 1)
            )
        else:
            nd_available = _parse_nd_timeseries(df_nd, T, nondispatchable)
        df_nd = pd.DataFrame(nd_available)
    elif df_nd is not None:
        warnings.warn(
            "df_nd is ignored because no nondispatchable units were provided.",
            UserWarning,
            stacklevel=2,
        )
        df_nd = None

    # HVDC frame handling: tile static box or validate provided frames.
    if hvdc and formulation != "singlenode_dc":
        if df_hvdc_min is None and df_hvdc_max is None:
            warnings.warn(
                "df_hvdc_min/df_hvdc_max not provided; tiling static box from "
                "HVDCLink bounds across all T steps.",
                UserWarning,
                stacklevel=2,
            )
            p_min_static, p_max_static = _hvdc_static_box(hvdc)
            df_hvdc_min = pd.DataFrame(np.tile(p_min_static, (T, 1)))
            df_hvdc_max = pd.DataFrame(np.tile(p_max_static, (T, 1)))
        elif df_hvdc_min is None or df_hvdc_max is None:
            raise ValueError("df_hvdc_min and df_hvdc_max must be provided together.")
        else:
            mins = _parse_hvdc_timeseries(df_hvdc_min, hvdc, T, "df_hvdc_min")
            maxs = _parse_hvdc_timeseries(df_hvdc_max, hvdc, T, "df_hvdc_max")
            if np.any(mins > maxs):
                bad = np.argwhere(mins > maxs)
                t_bad, k_bad = bad[0]
                raise ValueError(
                    f"df_hvdc_min[{t_bad},{k_bad}] = {mins[t_bad, k_bad]:.4g} > "
                    f"df_hvdc_max[{t_bad},{k_bad}] = {maxs[t_bad, k_bad]:.4g}; "
                    f"box invariant p_min <= p_max violated."
                )
            aligned_ids = [link.device_id for link in hvdc]
            df_hvdc_min = pd.DataFrame(mins, columns=aligned_ids)
            df_hvdc_max = pd.DataFrame(maxs, columns=aligned_ids)
    elif not hvdc and (df_hvdc_min is not None or df_hvdc_max is not None):
        warnings.warn(
            "df_hvdc_min/df_hvdc_max are ignored because no HVDC links were provided.",
            UserWarning,
            stacklevel=2,
        )
        df_hvdc_min = None
        df_hvdc_max = None

    normalized_case = (
        _case_with_generators(case, generators) if generators is not None else case
    )
    return _finalize_temporal_assembly(
        builders[formulation](
            normalized_case,
            df_P,
            df_Q,
            T,
            options,
            coupling_constraints,
            storage,
            delta,
            nondispatchable,
            df_nd,
            hvdc=hvdc,
            df_hvdc_min=df_hvdc_min,
            df_hvdc_max=df_hvdc_max,
            generators=generators,
            loads=loads,
            load_inputs=load_inputs,
            load_participates_when_empty=explicit_load_mode,
        ),
        temporal_assembly,
    )


# ---------------------------------------------------------------------------
# Deprecated aliases
# ---------------------------------------------------------------------------


def build_acopf(
    case: dict[str, Any],
    *,
    options: OPFOptions | None = None,
) -> OPFBuild:
    """
    Deprecated. Use build_opf(case, formulation='ac') instead.

    .. deprecated::
        build_acopf will be removed in a future release.
        Use build_opf(case, formulation='ac', options=options) instead.
    """
    warnings.warn(
        "build_acopf is deprecated and will be removed in a future release. "
        "Use build_opf(case, formulation='ac') instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return build_opf(case, formulation="ac", options=options)


def build_acopf_multistep(
    case: dict[str, Any],
    df_P: pd.DataFrame,
    df_Q: pd.DataFrame,
    *,
    T: int,
    options: OPFOptions | None = None,
    coupling_constraints: list[cp.Constraint] | None = None,
) -> OPFBuild:
    """
    Deprecated. Use build_opf_multistep(..., formulation='ac') instead.

    .. deprecated::
        build_acopf_multistep will be removed in a future release.
        Use build_opf_multistep(..., formulation='ac') instead.
    """
    warnings.warn(
        "build_acopf_multistep is deprecated and will be removed in a "
        "future release. "
        "Use build_opf_multistep(..., formulation='ac') instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return build_opf_multistep(
        case,
        df_P,
        df_Q,
        T=T,
        formulation="ac",
        options=options,
        coupling_constraints=coupling_constraints,
    )
