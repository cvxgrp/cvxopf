"""Narrow private prepared-AC window seam; public M17 guards stay unchanged.

Owns no device equations, numerical coordinate maps, recovery ladder, or audit.
The experiment supplies reviewed requests and uses OPFBuild.solve directly.
"""
from copy import deepcopy
from dataclasses import replace
from typing import Mapping

import numpy as np

from cvxopf._ac_start_mapping import pack_start, stepwise_values
from cvxopf import _hierarchical_solver as shared
from cvxopf.hierarchical import _device_ids, _validate_frame, HierarchicalPolicy
from cvxopf.generator import DispatchableGenerator
from cvxopf.load import Load
from cvxopf.nondispatchable import NondispatchableUnit
from cvxopf.storage import StorageUnitIdeal


def physical_snapshot(kwargs):
    """Validate the explicit ideal-storage/all-sheddable/no-HVDC capability.

    Reuse neutral frame/identity validation, not a rejected HierarchicalInputs.
    Unknown builder keywords (including caller temporal constraints) fail closed.
    Builders remain responsible for component/network physical validation.
    """
    allowed = {"case", "T", "formulation", "temporal_assembly", "delta", "options",
               "generators", "loads", "storage", "nondispatchable", "df_load_p",
               "df_load_q", "df_nd", "automatic_sparse_dispatch"}
    if set(kwargs) - allowed:
        raise ValueError("unsupported rollout inputs/intertemporal coupling")
    if kwargs["formulation"] != "ac" or kwargs["temporal_assembly"] != "vectorized":
        raise ValueError("prepared rollout requires vectorized full AC")
    if type(kwargs["T"]) is not int or kwargs["T"] < 1 or not isinstance(kwargs["case"], Mapping):
        raise ValueError("invalid rollout coverage/case")
    delta = kwargs["delta"]
    if isinstance(delta, bool) or not np.isfinite(delta) or delta <= 0:
        raise ValueError("invalid interval duration")
    if kwargs.get("automatic_sparse_dispatch") is not False:
        raise ValueError("rollout requires production sparse dispatch disabled")
    options = deepcopy(kwargs["options"])
    prep = options.numerical_preparation
    if (not options.init_flat or not options.enforce_branch_limits
            or not prep.normalize_device_limits or not prep.exact_fixed_boxes
            or not prep.cost_coordinates or prep.canonical_scaling != "none"):
        raise ValueError("unqualified rollout numerical/physical options")
    fleets = {}
    for key, cls in (("generators", DispatchableGenerator), ("loads", Load),
                     ("storage", StorageUnitIdeal), ("nondispatchable", NondispatchableUnit)):
        units = tuple(deepcopy(kwargs.get(key, ())))
        if not units or any(type(x) is not cls for x in units):
            raise ValueError(f"unsupported rollout {key} fleet")
        fleets[key] = units
    ids = {key: _device_ids(units, family=key, required=key != "generators")
           for key, units in fleets.items()}
    if any(x.shedding_cost_per_mwh is None for x in fleets["loads"]):
        raise ValueError("rollout audit requires all loads sheddable")
    if any(x.terminal_constraint != "equality" or x.terminal_soc is None
           or x.terminal_cost is not None or x.terminal_weight is not None for x in fleets["storage"]):
        raise ValueError("rollout requires original hard final obligations, without terminal costs")
    frames = {}
    for key, fleet in (("df_load_p", "loads"), ("df_load_q", "loads"), ("df_nd", "nondispatchable")):
        frames[key] = _validate_frame(kwargs.get(key), name=key, horizon_steps=kwargs["T"],
                                      expected_columns=ids[fleet], required=True)
    indices = [x.index for x in frames.values()]
    if not indices[0].is_unique or any(not indices[0].equals(x) for x in indices[1:]):
        raise ValueError("rollout input row identities differ")
    if np.any(frames["df_load_p"].to_numpy() < 0) or np.any(frames["df_nd"].to_numpy() < 0):
        raise ValueError("study audit requires nonnegative demand and availability")
    return shared._ExecutionInputs(case=deepcopy(dict(kwargs["case"])), horizon_steps=kwargs["T"],
        delta=float(delta), **fleets, **frames, hvdc=(), df_hvdc_min=None, df_hvdc_max=None,
        options=options, storage_device_ids=ids["storage"], inner_temporal_assembly="vectorized")


def window_kwargs(snapshot, request, *, hard_target=True, preparation=None):
    """One storage API replacement and the hierarchy's authoritative slicer."""
    if (request.storage_device_ids != snapshot.storage_device_ids
            or not 0 <= request.local_start < request.local_stop <= snapshot.horizon_steps):
        raise ValueError("window does not match physical snapshot")
    policy = HierarchicalPolicy(ac_window_steps=request.local_stop - request.local_start)
    initial = dict(zip(request.storage_device_ids, request.initial_soc_mwh, strict=True))
    target = dict(zip(request.storage_device_ids, request.target_soc_mwh, strict=True)) if hard_target else None
    units = shared._inner_storage(snapshot, policy, initial, target)
    if preparation is not None:
        snapshot = replace(snapshot, options=replace(snapshot.options, numerical_preparation=preparation))
    build = shared._build_window(snapshot, "ac", request.local_start, request.local_stop,
                                 units, temporal_assembly="vectorized")
    # These are the same owned physical inputs used by the shared slicer. Audits
    # consume engineering frames, never builder balance expressions.
    kwargs = dict(case=snapshot.case, T=request.local_stop - request.local_start,
        formulation="ac", temporal_assembly="vectorized", options=snapshot.options,
        generators=list(snapshot.generators), loads=list(snapshot.loads), storage=list(units),
        nondispatchable=list(snapshot.nondispatchable), delta=snapshot.delta,
        df_load_p=snapshot.df_load_p.iloc[request.local_start:request.local_stop].copy(),
        df_load_q=snapshot.df_load_q.iloc[request.local_start:request.local_stop].copy(),
        df_nd=snapshot.df_nd.iloc[request.local_start:request.local_stop].copy(),
        automatic_sparse_dispatch=False)
    return build, kwargs


def bound_shedding_start(build, values):
    """Start-only cap projection; fractions are subset rows, not full-load rows.

    Affine interruption bounds are not CVXPY leaf bounds. Read destination
    demand/caps through component metadata, including zero/negative eligibility.
    Never transfer priced cost leaves; preparation rebuilds their current weights.
    """
    values = {k: np.asarray(v, float).copy() for k, v in values.items()}
    if "load_shed_fraction" not in values:
        return values
    data = build.data
    indices = np.asarray(data["sheddable_load_indices"], int)
    p = np.asarray(build.expressions["p_load"].value, float)
    caps = np.asarray(data["load_max_shed_fraction"], float)[indices]
    upper = caps[:, None] * (p[indices] > 0)
    fraction = values["load_shed_fraction"]
    if fraction.shape != upper.shape or not np.isfinite(fraction).all():
        raise ValueError("shedding start subset/time shape or finiteness mismatch")
    values["load_shed_fraction"] = np.clip(fraction, 0, upper)
    return values


def starting_values(build, snapshot, request, *, source_slot, scale=None, seed=None,
                    preceding=None, target_free=None, replay=None):
    """Apply the shared start machinery for a single externally scheduled slot.

    preceding/target_free contain historical logical (time-suffixed) physical
    variables. Their audit/provenance is checked at the experiment boundary.
    replay is the original raw/assigned start, not a returned iterate.
    """
    if replay is not None:
        raw, assigned = [{k: np.asarray(v, float).copy() for k, v in replay[key].items()}
                         for key in ("raw_start", "assigned_start")]
    elif source_slot in (2, 3, 4, 5):
        if target_free is None:
            raise ValueError("accepted target-free source unavailable")
        assigned = pack_start(target_free, build, request.initial_soc_mwh)
        raw = {k: v.copy() for k, v in assigned.items()}
    else:
        if source_slot >= 6 and preceding is None:
            raise ValueError("preceding controller unavailable")
        if preceding is None:
            raw = shared._complete_start(build)
            assigned = {k: v.copy() for k, v in raw.items()}
        else:
            raw, assigned = shared._shifted_start(preceding, build, snapshot,
                HierarchicalPolicy(ac_window_steps=request.local_stop - request.local_start),
                dict(zip(request.storage_device_ids, request.initial_soc_mwh, strict=True)))
    if replay is None and source_slot >= 3:
        if scale is None or seed is None:
            raise ValueError("perturbation requires frozen scale and seed")
        raw, assigned = shared._perturbed_start(assigned, build, scale=scale, seed=seed)
    assigned = bound_shedding_start(build, assigned)
    shared._assign_start(build, assigned)
    return raw, assigned


def physical_solution(build):
    return stepwise_values(build, shared._solution_values(build))
