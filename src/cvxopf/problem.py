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
    """
    enforce_vset:           bool  = False
    sparsity_tol:           float = 0.0
    init_flat:              bool  = True
    enforce_branch_limits:  bool  = False
    loss_weight:            float = 1.0
    branch_limit_sentinel:  float = 1e6


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

        AC single-step keys:
            theta, v, P, Q, p, q, Pg, Qg

        AC multi-step: each value is a list of length T.

        DC single-step keys:
            p_flows, p_gen

        DC multi-step: each value is a list of length T.

    data : dict
        Pre-computed numpy arrays and metadata.

        AC keys: baseMVA, nb, ng, ref, pv, ext_to_int,
                 Ybus, G, B, E, Z, Pd, Qd, Cg,
                 Pgmin, Pgmax, Qgmin, Qgmax
        DC keys: baseMVA, nb, ng, nl, ext_to_int,
                 A, Cg, r, f_max, Pd, gen_bus,
                 Pgmin, Pgmax, loss_weight
        Multi-step additionally: T, Pd_series (and Qd_series for AC)

    formulation : str
        The formulation used to build this problem.
        One of: "ac", "lossy_dc".

    is_convex : bool
        True for convex formulations (lossy_dc); False for nonconvex (ac).
        Controls solver defaults in solve().
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
        kwargs.setdefault("verbose", False)
        self.prob.solve(**kwargs)


# ---------------------------------------------------------------------------
# Dispatch tables (populated after imports to avoid circular imports)
# ---------------------------------------------------------------------------

def _get_single_builders():
    from cvxopf.ac_problem import _build_ac_single
    from cvxopf.dc_problem import _build_lossy_dc_single
    return {
        "ac":       _build_ac_single,
        "lossy_dc": _build_lossy_dc_single,
    }


def _get_multistep_builders():
    from cvxopf.ac_problem import _build_ac_multistep
    from cvxopf.dc_problem import _build_lossy_dc_multistep
    return {
        "ac":       _build_ac_multistep,
        "lossy_dc": _build_lossy_dc_multistep,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_opf(
    case: dict,
    *,
    formulation: str = "ac",
    options: OPFOptions | None = None,
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
    options : OPFOptions, optional
        Formulation and solver options. Defaults to OPFOptions().

    Returns
    -------
    OPFBuild
        Call build.solve() to solve with appropriate defaults.
    """
    if options is None:
        options = OPFOptions()

    builders = _get_single_builders()
    if formulation not in builders:
        raise ValueError(
            f"Unknown formulation '{formulation}'. "
            f"Supported: {sorted(builders.keys())}"
        )
    return builders[formulation](case, options)


def build_opf_multistep(
    case: dict,
    df_P: pd.DataFrame,
    df_Q: pd.DataFrame,
    *,
    T: int,
    formulation: str = "ac",
    options: OPFOptions | None = None,
    coupling_constraints: list[cp.Constraint] | None = None,
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
        For formulation="lossy_dc", df_Q is accepted but ignored and a
        UserWarning is emitted.
    T : int
        Number of time steps. Must equal df_P.shape[0].
    formulation : str
        Same options as build_opf.
    options : OPFOptions, optional
        Formulation and solver options. Defaults to OPFOptions().
    coupling_constraints : list of cp.Constraint, optional
        Additional constraints linking variables across time steps (e.g.,
        battery SoC dynamics). Appended to the problem without modification.
        Default: empty list.

    Returns
    -------
    OPFBuild
        build.variables contains lists of length T for each variable type.
    """
    if options is None:
        options = OPFOptions()
    if coupling_constraints is None:
        coupling_constraints = []

    builders = _get_multistep_builders()
    if formulation not in builders:
        raise ValueError(
            f"Unknown formulation '{formulation}'. "
            f"Supported: {sorted(builders.keys())}"
        )
    return builders[formulation](
        case, df_P, df_Q, T, options, coupling_constraints
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