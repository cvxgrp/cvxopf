"""
CVXPY problem builders for AC-OPF and DC-OPF.

Public API
----------
build_opf(case, *, formulation, options)
    Single time-step OPF. Returns OPFBuild.

build_opf_multistep(case, df_P, df_Q, *, T, formulation, options,
                    coupling_constraints)
    T time-step OPF as a single cp.Problem. Returns OPFBuild.

Deprecated (will be removed in a future release)
-------------------------------------------------
build_acopf(case, *, options)
build_acopf_multistep(case, df_P, df_Q, *, T, options, coupling_constraints)
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
import cvxpy as cp

# Import storage and nondispatchable types for public API
from cvxopf.storage import StorageUnitIdeal
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.hvdc import HVDCLink, _hvdc_static_box


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
        If True, enforce per-branch thermal limits via rateA. Not yet
        implemented; raises NotImplementedError. AC only. Default False.
    loss_weight : float
        Weighting factor lambda for line losses in the lossy DC objective:
            minimize G + loss_weight * L
        where G is generation cost and L = sum_e r_e * p_flows_e^2.
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
    enforce_vset:           bool  = False
    sparsity_tol:           float = 0.0
    init_flat:              bool  = True
    enforce_branch_limits:  bool  = False
    loss_weight:            float = 1.0
    branch_limit_sentinel:  float = 1e6
    sparse_pq:              bool  = True


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

        AC multi-step: each value is a list of length T.

        DC single-step keys:
            p_flows, p_gen

        DC multi-step: each value is a list of length T.

        Singlenode DC single-step keys:
            Pg

        Singlenode DC multi-step: each value is a list of length T.

        When storage is present:
            b (real power, MW), b_q (reactive power, MVAr, AC only),
            soc (state of charge, MWh)

    data : dict
        Pre-computed numpy arrays and metadata.

        AC keys: baseMVA, nb, ng, ref, pv, ext_to_int,
                 Ybus, G, B, E, Z, Pd, Qd, Cg,
                 Pgmin, Pgmax, Qgmin, Qgmax
        DC keys: baseMVA, nb, ng, nl, ext_to_int,
                 A, Cg, r, f_max, Pd, gen_bus,
                 Pgmin, Pgmax, loss_weight
        Singlenode DC keys: baseMVA, nb, ng, ext_to_int,
                 Pd_total, Pgmin, Pgmax, gencost
        Multi-step additionally: T, Pd_series (and Qd_series for AC)
        For singlenode_dc, Pd_series has shape (T,) — one scalar per step,
        not (T, nb).
        When storage is present: ns, Cs, storage_bus,
                 storage_apparent_power_rating, storage_capacity,
                 storage_initial_soc, storage_delta, storage_aging_weight

    formulation : str
        The formulation used to build this problem.
        One of: "ac", "lossy_dc", "singlenode_dc".

    is_convex : bool
        True for convex formulations (lossy_dc, singlenode_dc); False for
        nonconvex (ac). Controls solver defaults in solve().
    """
    prob:        cp.Problem
    variables:   dict
    data:        dict
    formulation: str
    is_convex:   bool

    def solve(self, **kwargs) -> None:
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


# ---------------------------------------------------------------------------
# Dispatch tables (populated after imports to avoid circular imports)
# ---------------------------------------------------------------------------

def _get_single_builders():
    from cvxopf.ac_problem import _build_ac_single
    from cvxopf.dc_problem import _build_lossy_dc_single
    from cvxopf.singlenode_dc_problem import _build_singlenode_dc_single
    return {
        "ac":       _build_ac_single,
        "lossy_dc": _build_lossy_dc_single,
        "singlenode_dc": _build_singlenode_dc_single,
    }


def _get_multistep_builders():
    from cvxopf.ac_problem import _build_ac_multistep
    from cvxopf.dc_problem import _build_lossy_dc_multistep
    from cvxopf.singlenode_dc_problem import _build_singlenode_dc_multistep
    return {
        "ac":       _build_ac_multistep,
        "lossy_dc": _build_lossy_dc_multistep,
        "singlenode_dc": _build_singlenode_dc_multistep,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_opf(
    case: dict,
    *,
    formulation: str = "ac",
    options: OPFOptions | None = None,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    hvdc: list[HVDCLink] | None = None,
) -> OPFBuild:
    """
    Build a single time-step OPF problem.

    Parameters
    ----------
    case : dict
        MATPOWER-format case dict. Need not be pre-reindexed.
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
            'lossy_dc'. df_Q is accepted but ignored in build_opf_multistep.
    options : OPFOptions, optional
        Formulation and solver options. Defaults to OPFOptions().
    storage : list[StorageUnitIdeal] | None, optional
        List of energy storage units. If None, no storage is modelled.
        Each unit is a StorageUnitIdeal dataclass instance.
    delta : float, optional
        Time step duration in hours (default 1.0). Used for storage SoC
        dynamics when storage is present. Ignored when storage is None.
        Must be > 0 when storage is present.
    nondispatchable : list[NondispatchableUnit] | None, optional
        List of nondispatchable generator units (wind, solar, etc.).
        If None, no nondispatchable generation is modelled.
        Each unit is a NondispatchableUnit dataclass instance.

    Returns
    -------
    OPFBuild
        Call build.solve() to solve with appropriate defaults.
    """
    if options is None:
        options = OPFOptions()

    # Validate delta when storage is present
    if storage is not None and delta <= 0:
        raise ValueError(f"delta must be > 0, got {delta}")

    builders = _get_single_builders()
    if formulation not in builders:
        raise ValueError(
            f"Unknown formulation '{formulation}'. "
            f"Supported: {sorted(builders.keys())}"
        )
    return builders[formulation](case, options, storage, delta, nondispatchable,
                                  hvdc=hvdc)


def build_opf_multistep(
    case: dict,
    df_P: pd.DataFrame,
    df_Q: pd.DataFrame,
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
) -> OPFBuild:
    """
    Build a T-step OPF problem as a single cp.Problem.

    Parameters
    ----------
    case : dict
        MATPOWER-format case dict.
    df_P : pd.DataFrame, shape (T, nb)
        Active load time series in MW.
    df_Q : pd.DataFrame, shape (T, nb)
        Reactive load time series in MVAr. Used for formulation="ac" only.
        For formulation='lossy_dc' or formulation='singlenode_dc', df_Q is
        accepted but ignored and a UserWarning is emitted.
    T : int
        Number of time steps. Must equal df_P.shape[0].
    formulation : str
        Same options as build_opf, including "singlenode_dc"
        (single-node copper-plate DC dispatch; df_Q ignored).
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
        Time step duration in hours (default 1.0). Used for storage SoC
        dynamics when storage is present. Ignored when storage is None.
        Must be > 0 when storage is present.
    nondispatchable : list[NondispatchableUnit] | None, optional
        List of nondispatchable generator units (wind, solar, etc.).
        If None, no nondispatchable generation is modelled.
        Each unit is a NondispatchableUnit dataclass instance.
    df_nd : pd.DataFrame | None, optional
        Nondispatchable available power time series in MW.
        Shape (T, nnd) where nnd = len(nondispatchable).
        Column names must be external bus IDs (integers).
        If None and nondispatchable is not None, the p_available field
        from each NondispatchableUnit is tiled across all T steps.

    Returns
    -------
    OPFBuild
        build.variables contains lists of length T for each variable type.
    """
    if options is None:
        options = OPFOptions()
    if coupling_constraints is None:
        coupling_constraints = []

    # Validate delta when storage is present
    if storage is not None and delta <= 0:
        raise ValueError(f"delta must be > 0, got {delta}")

    # Validate and handle df_nd tiling fallback
    if nondispatchable is not None and df_nd is None:
        warnings.warn(
            "df_nd not provided; tiling p_available from each NondispatchableUnit "
            "across all T steps.",
            UserWarning,
            stacklevel=2,
        )
        # Create df_nd by tiling p_available from each unit
        df_nd = pd.DataFrame(
            {u.bus: [u.p_available] * T for u in nondispatchable}
        )
    elif nondispatchable is None and df_nd is not None:
        warnings.warn(
            "df_nd is ignored because nondispatchable=None.",
            UserWarning,
            stacklevel=2,
        )

    # HVDC frame handling: tile static box or validate provided frames.
    if hvdc is not None:
        if df_hvdc_min is None or df_hvdc_max is None:
            warnings.warn(
                "df_hvdc_min/df_hvdc_max not provided; tiling static box from "
                "HVDCLink.mode fields across all T steps.",
                UserWarning,
                stacklevel=2,
            )
            p_min_static, p_max_static = _hvdc_static_box(hvdc)
            df_hvdc_min = pd.DataFrame(np.tile(p_min_static, (T, 1)))
            df_hvdc_max = pd.DataFrame(np.tile(p_max_static, (T, 1)))
        else:
            mins = df_hvdc_min.values
            maxs = df_hvdc_max.values
            if np.any(mins > maxs):
                bad = np.argwhere(mins > maxs)
                t_bad, k_bad = bad[0]
                raise ValueError(
                    f"df_hvdc_min[{t_bad},{k_bad}] = {mins[t_bad, k_bad]:.4g} > "
                    f"df_hvdc_max[{t_bad},{k_bad}] = {maxs[t_bad, k_bad]:.4g}; "
                    f"box invariant p_min <= p_max violated."
                )

    builders = _get_multistep_builders()
    if formulation not in builders:
        raise ValueError(
            f"Unknown formulation '{formulation}'. "
            f"Supported: {sorted(builders.keys())}"
        )
    return builders[formulation](
        case, df_P, df_Q, T, options, coupling_constraints,
        storage, delta, nondispatchable, df_nd,
        hvdc=hvdc, df_hvdc_min=df_hvdc_min, df_hvdc_max=df_hvdc_max,
    )


# ---------------------------------------------------------------------------
# Deprecated aliases
# ---------------------------------------------------------------------------

def build_acopf(
    case: dict,
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
    case: dict,
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
        case, df_P, df_Q, T=T, formulation="ac",
        options=options, coupling_constraints=coupling_constraints,
    )