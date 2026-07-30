"""
Lossy DC OPF problem construction helpers.

This module implements the lossy DC optimal power flow formulation from:

    Convex Optimization with Smart Grid Examples,
    https://doi.org/10.2172/3018252

Formulation
-----------
Variables:
    p_flows  (nl,)  branch real power flows, p.u.
    Pg       (ng,)  per-generator real generation, p.u.

Objective:
    minimize  G + loss_weight * L

    where
        G = sum_k (c0_k + c1_k * Pg_k + c2_k * Pg_k^2)   generation cost
        L = sum_e r_e * p_flows_e^2                         line losses

Constraints:
    A @ p_flows + Cg @ Pg == Pd    flow conservation at every bus
    |p_flows[e]| <= f_max[e]       branch flow limits
    Pgmin <= Pg <= Pgmax

This is a convex QP; the default solver is CLARABEL (nlp=False).

This module is not part of the public API; use problem.py instead.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import cvxpy as cp

from cvxopf.network import (
    reindex_case_to_consecutive,
    make_branch_node_incidence_matrix,
)
from cvxopf.data import validate_case, load_timeseries_from_dataframe
from cvxopf.generator import (
    DispatchableGenerator,
    gen_from_matpower,
)
from cvxopf._component_adapter import (
    DCNetworkState,
    HorizonContext,
    PreparationContext,
    StepContext,
    bind_injection_scale,
)
from cvxopf._component_adapters import (
    GENERATOR_ADAPTER,
    NONDISPATCHABLE_ADAPTER,
    STORAGE_ADAPTER,
    NondispatchableInputs,
)
from cvxopf.storage import (
    StorageUnitIdeal,
)
from cvxopf.nondispatchable import (
    NondispatchableUnit,
)
from cvxopf.hvdc import (
    HVDCLink,
    _prepare_data as hvdc_prepare_data,
    _build_metadata as hvdc_build_metadata,
    _hvdc_static_box,
    dc_injections as hvdc_dc_injections,
    dc_operating_constraints as hvdc_dc_operating_constraints,
    coupling_constraints as hvdc_coupling_constraints,
    hvdc_cost_expr,
)

if TYPE_CHECKING:
    from cvxopf.problem import OPFBuild

# ---------------------------------------------------------------------------
# MATPOWER column indices
# ---------------------------------------------------------------------------

PD         = 2
BR_R       = 2
BR_STATUS  = 10
RATE_A     = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_dc_case(
    case: dict,
    options,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    hvdc: list[HVDCLink] | None = None,
    generators: list[DispatchableGenerator] | None = None,
    horizon_steps: int = 1,
    nd_available_mw: np.ndarray | None = None,
    is_multistep: bool = False,
) -> dict:
    """
    Validate, reindex, and extract all numpy data needed for DC OPF.
    Returns a flat dict consumed by the DC single-step and multistep builders.
    """
    validate_case(case)
    if generators is None:
        generators = gen_from_matpower(case["gen"], case["gencost"])
    case, ext_to_int = reindex_case_to_consecutive(case)

    baseMVA = float(case["baseMVA"])
    bus     = case["bus"]
    branch  = case["branch"]
    nb      = bus.shape[0]
    nl      = branch.shape[0]

    A  = make_branch_node_incidence_matrix(case)

    # branch resistances (p.u.)
    r = branch[:, BR_R].astype(float) / 1.0   # already dimensionless p.u.

    # branch flow limits (p.u.), with sentinel substitution for rateA=0
    f_max = np.zeros(nl)
    for e in range(nl):
        rate = float(branch[e, RATE_A])
        if rate == 0.0:
            warnings.warn(
                f"Branch {e} "
                f"(bus {int(branch[e, 0])} -> {int(branch[e, 1])}) "
                f"has rateA=0; substituting "
                f"branch_limit_sentinel={options.branch_limit_sentinel} MW.",
                UserWarning,
                stacklevel=4,
            )
            f_max[e] = options.branch_limit_sentinel / baseMVA
        else:
            f_max[e] = rate / baseMVA

    # nodal load (p.u.)
    Pd = bus[:, PD].astype(float) / baseMVA

    # External bus IDs for validation — use ext_to_int keys (external MATPOWER
    # numbering) rather than the already-reindexed bus table.
    ext_bus_ids = set(ext_to_int.keys())

    preparation = PreparationContext(
        base_mva=baseMVA,
        nb=nb,
        ext_to_int=ext_to_int,
        ext_bus_ids=frozenset(ext_bus_ids),
        horizon_steps=horizon_steps,
        delta=delta,
        is_multistep=is_multistep,
    )
    generator_data = GENERATOR_ADAPTER.prepare(
        generators, None, preparation
    )
    
    # Parse storage if present
    storage_data = {}
    if storage:
        storage_data = STORAGE_ADAPTER.prepare(
            storage, None, preparation
        )

    # Parse nondispatchable if present
    nd_data = {}
    if nondispatchable:
        if nd_available_mw is None:
            nd_available_mw = np.array(
                [[unit.p_available for unit in nondispatchable]],
                dtype=float,
            )
        nd_data = NONDISPATCHABLE_ADAPTER.prepare(
            nondispatchable,
            NondispatchableInputs(nd_available_mw),
            preparation,
        )

    # Parse HVDC links if present
    hvdc_data = {}
    if hvdc:
        hvdc_data = hvdc_prepare_data(
            hvdc, nb, ext_to_int, ext_bus_ids
        )

    return dict(
        case=case, baseMVA=baseMVA,
        nb=nb, nl=nl,
        ext_to_int=ext_to_int,
        ext_bus_ids=ext_bus_ids,
        A=A,
        r=r, f_max=f_max,
        Pd=Pd,
        loss_weight=options.loss_weight,
        **generator_data,
        **storage_data,
        **nd_data,
        **hvdc_data,
    )


def _make_dc_step_constraints(
    p_flows, Pg, generator_injection,
    A, Pd, f_max, generator_operating_constraints,
    ns: int = 0,
    storage_injection=None,
    storage_operating_constraints=(),
    nnd: int = 0,
    nd_injection=None,
    nd_operating_constraints=(),
    n_hvdc: int = 0,
    hvdc_injection_expr=None,
    links=None,
    p_in_t=None,
    p_out_t=None,
    p_min_hvdc_t=None,
    p_max_hvdc_t=None,
    step: int = 0,
) -> tuple[list, cp.Expression]:
    """Build one DC step's constraints and modeled net bus injection."""
    # Section 1: Nodal real power balance
    storage_term = storage_injection if ns > 0 else 0
    nd_term = nd_injection if nnd > 0 else 0
    hvdc_term = hvdc_injection_expr if n_hvdc > 0 else 0
    p_net = (
        generator_injection + storage_term + nd_term + hvdc_term - Pd
    )
    constr = [A @ p_flows + p_net == 0]

    # Section 2: Branch flow limits
    constr.append(cp.abs(p_flows) <= f_max)

    # Section 3: Generator bounds
    constr += list(generator_operating_constraints)

    # Section 5: Storage real power bounds (omitted when ns == 0)
    if ns > 0:
        constr += list(storage_operating_constraints)

    # Section 5b: Nondispatchable real power bounds (omitted when nnd == 0)
    if nnd > 0:
        constr += list(nd_operating_constraints)

    # Section 5c: HVDC operating constraints (omitted when n_hvdc == 0)
    if n_hvdc > 0:
        constr += hvdc_dc_operating_constraints(
            links, p_in_t, p_out_t, p_min_hvdc_t, p_max_hvdc_t, step
        )

    return constr, p_net


def _make_dc_step_cost(
    generator_cost,
    r, p_flows, loss_weight,
) -> cp.Expression:
    """Build the per-step DC cost expression."""
    L     = cp.sum(cp.multiply(r, cp.square(p_flows)))
    return generator_cost + cp.multiply(loss_weight, L)


# ---------------------------------------------------------------------------
# Public builders (called from problem.py dispatch)
# ---------------------------------------------------------------------------

def _build_lossy_dc_single(
    case: dict,
    options,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    *,
    hvdc=None,
    generators: list[DispatchableGenerator] | None = None,
) -> "OPFBuild":
    """Build a single time-step lossy DC OPF problem."""
    from cvxopf.problem import OPFBuild

    # Emit warning if storage is present in DC formulation
    if storage:
        warnings.warn(
            "Storage apparent_power_rating is applied as a real power limit "
            "only for formulation='lossy_dc'. Reactive power is not modelled "
            "in the DC formulation.",
            UserWarning,
            stacklevel=3,
        )

    d = _parse_dc_case(
        case, options, storage, delta, nondispatchable, hvdc, generators
    )

    p_flows = cp.Variable(d["nl"], name="p_flows")
    step_context = StepContext(
        "lossy_dc", 0, d["baseMVA"], d["ext_to_int"], DCNetworkState()
    )
    generator_binding = GENERATOR_ADAPTER.formulations["lossy_dc"]
    assert generator_binding.variable_specs is not None
    generator_variables = {
        spec.name: cp.Variable(
            spec.shape, name=spec.name, **spec.attributes
        )
        for spec in generator_binding.variable_specs(
            d["generators"], d, step_context
        )
    }
    Pg = generator_variables["Pg"]

    # Create storage variables if present
    b_t = soc_t = None
    storage_inj = None
    if "ns" in d:
        storage_binding = STORAGE_ADAPTER.formulations["lossy_dc"]
        assert storage_binding.variable_specs is not None
        assert storage_binding.injections is not None
        storage_variables = {
            spec.name: cp.Variable(
                spec.shape, name=spec.name, **spec.attributes
            )
            for spec in storage_binding.variable_specs(
                storage, d, step_context
            )
        }
        b_t = storage_variables["b"]
        soc_t = storage_variables["soc"]
        storage_injection = storage_binding.injections(
            storage, d, storage_variables, step_context
        )
        bind_injection_scale(storage_injection, d["baseMVA"])
        storage_inj = storage_injection.p_pu

    # Create nondispatchable variables if present
    p_nd_t = None
    nd_inj = None
    if "nnd" in d:
        nd_binding = NONDISPATCHABLE_ADAPTER.formulations["lossy_dc"]
        assert nd_binding.variable_specs is not None
        assert nd_binding.injections is not None
        nd_variables = {
            spec.name: cp.Variable(
                spec.shape, name=spec.name, **spec.attributes
            )
            for spec in nd_binding.variable_specs(
                nondispatchable, d, step_context
            )
        }
        p_nd_t = nd_variables["p_nd"]
        nd_injection = nd_binding.injections(
            nondispatchable,
            d,
            nd_variables,
            step_context,
        )
        bind_injection_scale(nd_injection, d["baseMVA"])
        nd_inj = nd_injection.p_pu

    # Create HVDC variables if present
    p_in = p_out = None
    hvdc_inj_expr = None
    if "n_hvdc" in d:
        n_hvdc = d["n_hvdc"]
        p_in  = cp.Variable((n_hvdc,), name="p_hvdc_in")
        p_out = cp.Variable((n_hvdc,), name="p_hvdc_out")
        hvdc_inj_expr, hvdc_q_inj, inv_bMVA = hvdc_dc_injections(
            hvdc,
            p_in,
            p_out,
            d["ext_to_int"],
            incidence=(d["Ch_from"], d["Ch_to"]),
        )
        assert hvdc_q_inj is None
        inv_bMVA.value = 1.0 / d["baseMVA"]
        p_min_hvdc, p_max_hvdc = _hvdc_static_box(hvdc)

    assert generator_binding.injections is not None
    assert generator_binding.operating_constraints is not None
    assert generator_binding.network_constraints is not None
    assert generator_binding.step_cost is not None
    generator_injection = generator_binding.injections(
        d["generators"], d, generator_variables, step_context
    )
    bind_injection_scale(generator_injection, d["baseMVA"])
    generator_operating = generator_binding.operating_constraints(
        d["generators"], d, generator_variables, step_context
    )
    nd_operating = ()
    if "nnd" in d:
        assert nd_binding.operating_constraints is not None
        nd_operating = nd_binding.operating_constraints(
            nondispatchable, d, nd_variables, step_context
        )
    storage_operating = ()
    if "ns" in d:
        assert storage_binding.operating_constraints is not None
        storage_operating = storage_binding.operating_constraints(
            storage, d, storage_variables, step_context
        )

    constr, p_net_expr = _make_dc_step_constraints(
        p_flows, Pg, generator_injection.p_pu,
        d["A"], d["Pd"], d["f_max"],
        generator_operating,
        ns=d.get("ns", 0),
        storage_injection=storage_inj,
        storage_operating_constraints=storage_operating,
        nnd=d.get("nnd", 0),
        nd_injection=nd_inj,
        nd_operating_constraints=nd_operating,
        n_hvdc=d.get("n_hvdc", 0),
        hvdc_injection_expr=hvdc_inj_expr,
        links=hvdc,
        p_in_t=p_in,
        p_out_t=p_out,
        p_min_hvdc_t=p_min_hvdc if "n_hvdc" in d else None,
        p_max_hvdc_t=p_max_hvdc if "n_hvdc" in d else None,
        step=0,
    )
    constr.extend(
        generator_binding.network_constraints(
            d["generators"], d, generator_variables, step_context
        )
    )

    generator_cost = generator_binding.step_cost(
        d["generators"], d, generator_variables, step_context
    )
    cost = _make_dc_step_cost(
        generator_cost,
        d["r"], p_flows, d["loss_weight"],
    )

    # Add storage aging cost if present
    storage_cost = None
    if "ns" in d:
        assert storage_binding.step_cost is not None
        storage_cost = storage_binding.step_cost(
            storage, d, storage_variables, step_context
        )
        cost = cost + storage_cost

    # Add HVDC cost if present
    if "n_hvdc" in d:
        cost = cost + hvdc_cost_expr(hvdc, p_in)

    storage_terminal_cost = None
    if "ns" in d:
        assert storage_binding.horizon is not None
        storage_horizon = storage_binding.horizon(
            storage,
            d,
            {"b": [b_t], "soc": [soc_t]},
            HorizonContext("lossy_dc", 1, delta),
        )
        storage_terminal_cost = storage_horizon.terminal_cost
        if storage_terminal_cost is not None:
            cost = cost + storage_terminal_cost
        constr.extend(storage_horizon.constraints)
    assert generator_binding.horizon is not None
    constr.extend(
        generator_binding.horizon(
            d["generators"],
            d,
            {"Pg": [Pg]},
            HorizonContext("lossy_dc", 1, delta),
        ).constraints
    )
    if "nnd" in d:
        assert nd_binding.horizon is not None
        constr.extend(
            nd_binding.horizon(
                nondispatchable,
                d,
                {"p_nd": [p_nd_t]},
                HorizonContext("lossy_dc", 1, delta),
            ).constraints
        )

    prob      = cp.Problem(cp.Minimize(cost), constr)
    variables = dict(p_flows=p_flows, Pg=Pg)

    # Add storage variables if present
    if "ns" in d:
        variables["b"] = b_t
        variables["soc"] = soc_t

    # Add nondispatchable variables if present
    if "nnd" in d:
        variables["p_nd"] = p_nd_t

    # Add HVDC variables if present
    if "n_hvdc" in d:
        variables["p_hvdc_in"]  = p_in
        variables["p_hvdc_out"] = p_out

    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], nl=d["nl"],
        ext_to_int=d["ext_to_int"],
        A=d["A"],
        r=d["r"], f_max=d["f_max"],
        Pd=d["Pd"],
        loss_weight=d["loss_weight"],
    )
    data.update(GENERATOR_ADAPTER.metadata(d, "lossy_dc"))

    # Add storage data if present
    if "ns" in d:
        data.update(STORAGE_ADAPTER.metadata(d, "lossy_dc"))

    # Add nondispatchable data if present
    if "nnd" in d:
        data.update(NONDISPATCHABLE_ADAPTER.metadata(d, "lossy_dc"))

    # Add HVDC data if present
    if "n_hvdc" in d:
        data.update(hvdc_build_metadata(d))

    expressions = {"p_net": p_net_expr}
    if storage_cost is not None:
        expressions["storage_cost"] = storage_cost
    if storage_terminal_cost is not None:
        expressions["storage_terminal_cost"] = storage_terminal_cost

    return OPFBuild(
        prob=prob, variables=variables, data=data,
        formulation="lossy_dc", is_convex=True,
        expressions=expressions,
    )


def _build_lossy_dc_multistep(
    case: dict,
    df_P: pd.DataFrame,
    df_Q: pd.DataFrame,
    T: int,
    options,
    coupling_constraints: list,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    df_nd: pd.DataFrame | None = None,
    *,
    hvdc=None,
    df_hvdc_min=None,
    df_hvdc_max=None,
    generators: list[DispatchableGenerator] | None = None,
) -> "OPFBuild":
    """Build a T-step lossy DC OPF problem as a single cp.Problem."""
    from cvxopf.problem import OPFBuild

    if df_Q is not None:
        warnings.warn(
            "df_Q is ignored for formulation='lossy_dc'. "
            "Reactive power is not modelled in the DC formulation.",
            UserWarning,
            stacklevel=3,
        )

    # Emit warning if storage is present in DC formulation
    if storage:
        warnings.warn(
            "Storage apparent_power_rating is applied as a real power limit "
            "only for formulation='lossy_dc'. Reactive power is not modelled "
            "in the DC formulation.",
            UserWarning,
            stacklevel=3,
        )

    # Use df_P only for load; construct a dummy df_Q with zeros for the
    # shared timeseries loader (which expects matching shapes).
    df_Q_dummy = pd.DataFrame(
        np.zeros_like(df_P.to_numpy()), columns=df_P.columns
    )

    d = _parse_dc_case(
        case,
        options,
        storage,
        delta,
        nondispatchable,
        hvdc,
        generators,
        horizon_steps=T,
        nd_available_mw=(
            None if df_nd is None else df_nd.to_numpy(dtype=float)
        ),
        is_multistep=True,
    )
    Pd_series, _ = load_timeseries_from_dataframe(df_P, df_Q_dummy, case)

    if Pd_series.shape[0] != T:
        raise ValueError(
            f"T={T} but df_P has {Pd_series.shape[0]} rows; they must match."
        )

    p_flows_list    = []
    Pg_list         = []
    b_list          = []
    soc_list        = []
    p_nd_list       = []
    p_hvdc_in_list  = []
    p_hvdc_out_list = []
    p_net_expr_list = []
    all_constr      = []
    total_cost      = 0
    storage_cost    = 0

    for t in range(T):
        p_flows_t = cp.Variable(d["nl"], name=f"p_flows_{t}")
        step_context = StepContext(
            "lossy_dc",
            t,
            d["baseMVA"],
            d["ext_to_int"],
            DCNetworkState(),
        )
        generator_binding = GENERATOR_ADAPTER.formulations["lossy_dc"]
        assert generator_binding.variable_specs is not None
        generator_variables_t = {
            spec.name: cp.Variable(
                spec.shape,
                name=f"{spec.name}_{t}",
                **spec.attributes,
            )
            for spec in generator_binding.variable_specs(
                d["generators"], d, step_context
            )
        }
        Pg_t = generator_variables_t["Pg"]

        # Create storage variables if present
        b_t = soc_t = None
        storage_inj_t = None
        if "ns" in d:
            storage_binding = STORAGE_ADAPTER.formulations["lossy_dc"]
            assert storage_binding.variable_specs is not None
            assert storage_binding.injections is not None
            storage_variables_t = {
                spec.name: cp.Variable(
                    spec.shape,
                    name=f"{spec.name}_{t}",
                    **spec.attributes,
                )
                for spec in storage_binding.variable_specs(
                    storage, d, step_context
                )
            }
            b_t = storage_variables_t["b"]
            soc_t = storage_variables_t["soc"]
            storage_injection_t = storage_binding.injections(
                storage, d, storage_variables_t, step_context
            )
            bind_injection_scale(storage_injection_t, d["baseMVA"])
            storage_inj_t = storage_injection_t.p_pu

        # Create nondispatchable variables if present
        p_nd_t = None
        nd_inj_t = None
        if "nnd" in d:
            nd_binding = NONDISPATCHABLE_ADAPTER.formulations["lossy_dc"]
            assert nd_binding.variable_specs is not None
            assert nd_binding.injections is not None
            nd_variables_t = {
                spec.name: cp.Variable(
                    spec.shape,
                    name=f"{spec.name}_{t}",
                    **spec.attributes,
                )
                for spec in nd_binding.variable_specs(
                    nondispatchable, d, step_context
                )
            }
            p_nd_t = nd_variables_t["p_nd"]
            nd_injection_t = nd_binding.injections(
                nondispatchable,
                d,
                nd_variables_t,
                step_context,
            )
            bind_injection_scale(nd_injection_t, d["baseMVA"])
            nd_inj_t = nd_injection_t.p_pu

        # Create HVDC variables if present
        p_in_t = p_out_t = None
        hvdc_inj_expr_t = None
        p_min_hvdc_t = p_max_hvdc_t = None
        if "n_hvdc" in d:
            n_hvdc = d["n_hvdc"]
            p_in_t  = cp.Variable((n_hvdc,), name=f"p_hvdc_in_{t}")
            p_out_t = cp.Variable((n_hvdc,), name=f"p_hvdc_out_{t}")
            hvdc_inj_expr_t, hvdc_q_inj_t, inv_bMVA_t = hvdc_dc_injections(
                hvdc,
                p_in_t,
                p_out_t,
                d["ext_to_int"],
                incidence=(d["Ch_from"], d["Ch_to"]),
            )
            assert hvdc_q_inj_t is None
            inv_bMVA_t.value = 1.0 / d["baseMVA"]
            p_min_hvdc_t = df_hvdc_min.iloc[t].values.astype(float)
            p_max_hvdc_t = df_hvdc_max.iloc[t].values.astype(float)

        assert generator_binding.injections is not None
        assert generator_binding.operating_constraints is not None
        assert generator_binding.network_constraints is not None
        assert generator_binding.step_cost is not None
        generator_injection_t = generator_binding.injections(
            d["generators"], d, generator_variables_t, step_context
        )
        bind_injection_scale(generator_injection_t, d["baseMVA"])
        generator_operating_t = generator_binding.operating_constraints(
            d["generators"], d, generator_variables_t, step_context
        )
        nd_operating_t = ()
        if "nnd" in d:
            assert nd_binding.operating_constraints is not None
            nd_operating_t = nd_binding.operating_constraints(
                nondispatchable, d, nd_variables_t, step_context
            )
        storage_operating_t = ()
        if "ns" in d:
            assert storage_binding.operating_constraints is not None
            storage_operating_t = storage_binding.operating_constraints(
                storage, d, storage_variables_t, step_context
            )

        step_constr, p_net_expr_t = _make_dc_step_constraints(
            p_flows_t, Pg_t, generator_injection_t.p_pu,
            d["A"], Pd_series[t], d["f_max"],
            generator_operating_t,
            ns=d.get("ns", 0),
            storage_injection=storage_inj_t,
            storage_operating_constraints=storage_operating_t,
            nnd=d.get("nnd", 0),
            nd_injection=nd_inj_t,
            nd_operating_constraints=nd_operating_t,
            n_hvdc=d.get("n_hvdc", 0),
            hvdc_injection_expr=hvdc_inj_expr_t,
            links=hvdc,
            p_in_t=p_in_t,
            p_out_t=p_out_t,
            p_min_hvdc_t=p_min_hvdc_t,
            p_max_hvdc_t=p_max_hvdc_t,
            step=t,
        )
        step_constr.extend(
            generator_binding.network_constraints(
                d["generators"], d, generator_variables_t, step_context
            )
        )
        generator_cost_t = generator_binding.step_cost(
            d["generators"], d, generator_variables_t, step_context
        )
        step_cost = _make_dc_step_cost(
            generator_cost_t,
            d["r"], p_flows_t, d["loss_weight"],
        )

        # Add storage aging cost if present
        if "ns" in d:
            assert storage_binding.step_cost is not None
            step_storage_cost = storage_binding.step_cost(
                storage, d, storage_variables_t, step_context
            )
            storage_cost = storage_cost + step_storage_cost
            step_cost = step_cost + step_storage_cost

        # Add HVDC cost if present
        if "n_hvdc" in d:
            step_cost = step_cost + hvdc_cost_expr(hvdc, p_in_t)

        all_constr.extend(step_constr)
        total_cost  = total_cost + step_cost
        p_flows_list.append(p_flows_t)
        Pg_list.append(Pg_t)
        p_net_expr_list.append(p_net_expr_t)

        # Add storage variables to lists
        if "ns" in d:
            b_list.append(b_t)
            soc_list.append(soc_t)

        # Add nondispatchable variables to lists
        if "nnd" in d:
            p_nd_list.append(p_nd_t)

        # Add HVDC variables to lists
        if "n_hvdc" in d:
            p_hvdc_in_list.append(p_in_t)
            p_hvdc_out_list.append(p_out_t)

    storage_terminal_cost = None
    if "ns" in d:
        assert storage_binding.horizon is not None
        storage_horizon = storage_binding.horizon(
            storage,
            d,
            {"b": b_list, "soc": soc_list},
            HorizonContext("lossy_dc", T, delta),
        )
        all_constr.extend(storage_horizon.constraints)
        storage_terminal_cost = storage_horizon.terminal_cost
    assert generator_binding.horizon is not None
    all_constr.extend(
        generator_binding.horizon(
            d["generators"],
            d,
            {"Pg": Pg_list},
            HorizonContext("lossy_dc", T, delta),
        ).constraints
    )
    if "nnd" in d:
        assert nd_binding.horizon is not None
        all_constr.extend(
            nd_binding.horizon(
                nondispatchable,
                d,
                {"p_nd": p_nd_list},
                HorizonContext("lossy_dc", T, delta),
            ).constraints
        )
    if "n_hvdc" in d:
        all_constr.extend(
            hvdc_coupling_constraints(
                hvdc, p_hvdc_in_list, p_hvdc_out_list, delta=delta
            )
        )

    if storage_terminal_cost is not None:
        total_cost = total_cost + storage_terminal_cost

    all_constr.extend(coupling_constraints)
    prob = cp.Problem(cp.Minimize(total_cost), all_constr)

    variables = dict(p_flows=p_flows_list, Pg=Pg_list)

    # Add storage variables if present
    if "ns" in d:
        variables["b"] = b_list
        variables["soc"] = soc_list

    # Add nondispatchable variables if present
    if "nnd" in d:
        variables["p_nd"] = p_nd_list

    # Add HVDC variables if present
    if "n_hvdc" in d:
        variables["p_hvdc_in"]  = p_hvdc_in_list
        variables["p_hvdc_out"] = p_hvdc_out_list

    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], nl=d["nl"],
        ext_to_int=d["ext_to_int"],
        A=d["A"],
        r=d["r"], f_max=d["f_max"],
        loss_weight=d["loss_weight"],
        T=T,
        Pd_series=Pd_series,
    )
    data.update(GENERATOR_ADAPTER.metadata(d, "lossy_dc"))

    # Add storage data if present
    if "ns" in d:
        data.update(STORAGE_ADAPTER.metadata(d, "lossy_dc"))

    # Add nondispatchable data if present
    if "nnd" in d:
        data.update(NONDISPATCHABLE_ADAPTER.metadata(d, "lossy_dc"))

    # Add HVDC data if present
    if "n_hvdc" in d:
        data.update(hvdc_build_metadata(d))

    expressions = {"p_net": p_net_expr_list}
    if "ns" in d:
        expressions["storage_cost"] = storage_cost
    if storage_terminal_cost is not None:
        expressions["storage_terminal_cost"] = storage_terminal_cost

    return OPFBuild(
        prob=prob, variables=variables, data=data,
        formulation="lossy_dc", is_convex=True,
        expressions=expressions,
    )
