"""Matched R1/R2/G/B models and independent scientific acceptance.

No solve is performed during construction. The supplied references use MW and
MWh; only generator variables inside the AC builder use per-unit coordinates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Mapping

import cvxpy as cp
import numpy as np

from cvxopf import HierarchicalInputs, HierarchicalPolicy, OPFBuild
from cvxopf.network import (
    make_branch_admittance,
    make_ybus_matpower,
    reindex_case_to_consecutive,
)
from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.s5_speculative_archive import (
    box_residuals,
    result_shapes,
)
from experiments.case118_annual_hierarchy.streaming_archive import _json_value

ARMS = ("R1", "R2", "G", "B")


def array(value, shape, name):
    out = np.asarray(value, dtype=float)
    if out.shape != shape or not np.isfinite(out).all():
        raise ValueError(f"{name}: expected finite array of shape {shape}")
    return out.copy()


@dataclass(frozen=True)
class MatchedWindow:
    """References bound to the exact ordered physical inputs, not bus IDs alone."""

    start: int
    stop: int
    input_sha256: str
    pg_mw: np.ndarray
    battery_mw: np.ndarray
    renewable_mw: np.ndarray
    soc_mwh: np.ndarray  # all W+1 boundaries, including the DC start
    provenance: Mapping[str, object]

    def validate(self, inputs: HierarchicalInputs, policy: HierarchicalPolicy) -> None:
        if not 0 <= self.start < self.stop <= inputs.horizon_steps:
            raise ValueError("invalid matched window")
        if self.input_sha256 != streaming.execution_input_sha256(inputs):
            raise ValueError("matched references belong to different ordered inputs")
        n = self.stop - self.start
        array(self.pg_mw, (n, len(inputs.generators)), "DC generation")
        b = array(self.battery_mw, (n, len(inputs.storage)), "DC battery")
        array(self.renewable_mw, (n, len(inputs.nondispatchable)), "DC renewable")
        soc = array(self.soc_mwh, (n + 1, len(inputs.storage)), "DC SOC")
        if (
            np.max(np.abs(np.diff(soc, axis=0) + inputs.delta * b))
            > policy.tolerances.soc_recurrence_mwh_abs
        ):
            raise ValueError("DC battery schedule does not reconstruct SOC boundaries")
        if inputs.hvdc or any(
            x.shedding_cost_per_mwh is not None for x in inputs.loads
        ):
            raise ValueError("this experiment requires fixed loads and no HVDC")
        if any(g.cost_type != "polynomial" for g in inputs.generators):
            raise ValueError(
                "independent cost accounting currently requires polynomial costs"
            )

    @property
    def identity(self) -> str:
        return object_sha256(_json_value(asdict(self)))


@dataclass(frozen=True)
class ComparisonTolerances:
    """Explicit protocol choices; no hidden study-specific numerical defaults."""

    lock_mw_abs: float
    repair_mwh_abs: float
    cost_abs: float
    cost_rel: float

    def __post_init__(self):
        if any(
            isinstance(v, bool) or not np.isfinite(v) or v < 0
            for v in asdict(self).values()
        ):
            raise ValueError("comparison tolerances must be finite and nonnegative")


@dataclass
class ArmModel:
    build: OPFBuild
    window: MatchedWindow
    arm: str
    hard_target: bool
    repair_budget_mwh: float | None
    common_cost: cp.Expression
    departure: cp.Expression
    physical_names: tuple[str, ...]


def build_arm(inputs, policy, window, arm, *, repair_budget_mwh=None, hard_target=True):
    window.validate(inputs, policy)
    if arm not in ARMS or (not hard_target and arm != "B"):
        raise ValueError("only B permits source-only target-free construction")
    if arm == "R2":
        if (
            repair_budget_mwh is None
            or not np.isfinite(repair_budget_mwh)
            or repair_budget_mwh < 0
        ):
            raise ValueError("R2 requires an explicit finite nonnegative repair budget")
    elif repair_budget_mwh is not None:
        raise ValueError("repair budget belongs only to R2")
    ids = inputs.storage_device_ids
    storage = streaming._inner_storage(
        inputs,
        dict(zip(ids, window.soc_mwh[0])),
        dict(zip(ids, window.soc_mwh[-1])) if hard_target else None,
    )
    build = streaming.build_window(inputs, "ac", window.start, window.stop, storage)
    physical_names = tuple(streaming.variables_by_name(build))
    common_cost = build.prob.objective.expr
    pg = cp.vstack(build.variables["Pg"]) * float(inputs.case["baseMVA"])
    difference = pg - window.pg_mw
    departure = inputs.delta * cp.sum(cp.abs(difference))
    constraints = list(build.prob.constraints)
    if inputs.nondispatchable:
        constraints.append(cp.vstack(build.variables["p_nd"]) == window.renewable_mw)
    if arm != "B":
        constraints.append(cp.vstack(build.variables["b"]) == window.battery_mw)
    if arm in ("R1", "R2"):
        # Explicit epigraph with a stable name supports transfer across arms.
        epigraph = cp.Variable(difference.shape, nonneg=True, name="cf_departure_mw")
        constraints.extend([epigraph >= difference, epigraph >= -difference])
        repair_expr = inputs.delta * cp.sum(epigraph)
        if arm == "R2":
            constraints.append(repair_expr <= repair_budget_mwh)
    objective = repair_expr if arm == "R1" else common_cost
    build.prob = cp.Problem(cp.Minimize(objective), constraints)
    return ArmModel(
        build,
        window,
        arm,
        hard_target,
        repair_budget_mwh,
        common_cost,
        departure,
        physical_names,
    )


def map_start(model: ArmModel, source: Mapping[str, object] | None):
    """Transfer physical coordinates to a leaf-valid start, adding our epigraph.

    Projection affects initialization only, never the retained scientific
    candidate. The worker separately records unprojected source values.
    """
    variables = streaming.variables_by_name(model.build)
    if source is None:
        values = streaming.complete_flat_start(model.build)
    else:
        expected = set(model.physical_names)
        if set(source) - {"cf_departure_mw"} != expected:
            raise ValueError("incumbent physical variable namespace mismatch")
        values = {
            name: np.asarray(
                variables[name].project(
                    array(source[name], variables[name].shape, name)
                )
            )
            for name in expected
        }
    if "cf_departure_mw" in variables:
        pg = np.vstack([values[v.name()] for v in model.build.variables["Pg"]])
        values["cf_departure_mw"] = np.abs(
            pg * model.build.data["baseMVA"] - model.window.pg_mw
        )
    streaming.assign_start(model.build, values)
    return values


def device_costs(inputs, result):
    """Shared independent device-cost reconstruction, in engineering units."""
    pg, b = np.asarray(result["Pg"]), np.asarray(result["b"])
    delta = inputs.delta
    generation = (
        sum(
            float(
                np.polynomial.polynomial.polyval(
                    pg[:, j], g.cost_coeffs or (0.0,)
                ).sum()
            )
            for j, g in enumerate(inputs.generators)
        )
        * delta
    )
    throughput = delta * np.abs(b).sum(axis=0)
    battery_cost = float(
        throughput @ np.array([s.aging_weight for s in inputs.storage])
    )
    return generation, battery_cost, throughput


def cost_and_changes(inputs, window, result):
    pg, b = np.asarray(result["Pg"]), np.asarray(result["b"])
    delta = inputs.delta
    generation, battery_cost, throughput = device_costs(inputs, result)
    dg, db = pg - window.pg_mw, b - window.battery_mw
    net, l1 = dg.sum(axis=1), np.abs(dg).sum(axis=1)
    branch_loss = (
        np.asarray(result["branch_p_from"]) + np.asarray(result["branch_p_to"])
    ).sum(axis=1)
    bus = np.asarray(inputs.case["bus"])
    shunt = (np.asarray(result["Vm"]) ** 2 * bus[:, 4]).sum(axis=1)
    # Subtract the DC aggregate imbalance rather than silently assuming zero.
    loads = (
        inputs.df_load_p.loc[:, [x.device_id for x in inputs.loads]]
        .iloc[window.start : window.stop]
        .to_numpy()
        .sum(axis=1)
    )
    dc_balance = (
        window.pg_mw.sum(axis=1)
        + window.battery_mw.sum(axis=1)
        + window.renewable_mw.sum(axis=1)
        - loads
    )
    nd = (
        np.asarray(result["p_nd"])
        if inputs.nondispatchable
        else np.zeros_like(window.renewable_mw)
    )
    balance_error = (
        net
        + db.sum(axis=1)
        + (nd - window.renewable_mw).sum(axis=1)
        + dc_balance
        - branch_loss
        - shunt
    )
    return {
        "generation_cost": generation,
        "storage_cost": battery_cost,
        "common_cost": generation + battery_cost,
        "terminal_cost": 0.0,
        "throughput_mwh": float(throughput.sum()),
        "throughput_by_device_mwh": throughput.tolist(),
        "departure_mwh": float(delta * l1.sum()),
        "generator_net_change_mw": net.tolist(),
        "generator_l1_change_mw": l1.tolist(),
        "generator_opposing_change_mw": ((l1 - np.abs(net)) / 2).tolist(),
        "battery_net_change_mw": db.sum(axis=1).tolist(),
        "battery_l1_change_mw": np.abs(db).sum(axis=1).tolist(),
        "soc_boundaries_mwh": np.vstack([window.soc_mwh[0], result["soc"]]).tolist(),
        "branch_loss_mwh": float(delta * branch_loss.sum()),
        "shunt_consumption_mwh": float(delta * shunt.sum()),
        "curtailment_mwh": float(
            delta * np.asarray(result.get("curtailment", [])).sum()
        ),
        "served_load_mwh": float(delta * np.asarray(result["p_load_served"]).sum()),
        "aggregate_change_balance_error_mw": balance_error.tolist(),
    }


def network_reporting_residuals(inputs, result):
    """Recompute injections/terminal flows from voltage, including taps/shunts."""
    case, _ = reindex_case_to_consecutive(streaming._copy_case(inputs.case))
    base = float(case["baseMVA"])
    ybus = make_ybus_matpower(case)
    v = np.asarray(result["Vm"]) * np.exp(1j * np.deg2rad(result["Va_deg"]))
    injection = v * np.conj(v @ ybus.T)
    adm = make_branch_admittance(case)
    vf, vt = v[:, adm.from_bus], v[:, adm.to_bus]
    sf = vf * np.conj(vf * adm.yff + vt * adm.yft)
    st = vt * np.conj(vf * adm.ytf + vt * adm.ytt)
    expected = {
        "p_net": injection.real,
        "q_net": injection.imag,
        "branch_p_from": sf.real,
        "branch_q_from": sf.imag,
        "branch_p_to": st.real,
        "branch_q_to": st.imag,
        "branch_s_from": np.abs(sf),
        "branch_s_to": np.abs(st),
    }
    return {
        f"{name}_reconstruction_pu_abs": float(
            np.max(np.abs(np.asarray(result[name]) / base - value))
        )
        for name, value in expected.items()
    }


def audit_result(
    inputs,
    policy,
    window,
    arm,
    result,
    tolerances,
    *,
    repair_budget_mwh=None,
    hard_target=True,
    exception=None,
    reported_common_cost=None,
):
    """Audit public arrays without trusting worker acceptance or a new solve."""
    shapes = result_shapes(inputs, window.stop - window.start)
    unavailable = [
        k
        for k, shape in shapes.items()
        if result.get(k) is None
        or np.asarray(result[k]).shape != shape
        or not np.isfinite(np.asarray(result[k], dtype=float)).all()
    ]
    if unavailable:
        return {
            "accepted": False,
            "missing": unavailable,
            "metrics": None,
            "residuals": {},
            "limits": {},
            "exception": exception,
        }
    storage = streaming._inner_storage(
        inputs,
        dict(zip(inputs.storage_device_ids, window.soc_mwh[0])),
        dict(zip(inputs.storage_device_ids, window.soc_mwh[-1]))
        if hard_target
        else None,
    )
    physical = audit_probe(
        inputs.case,
        SimpleNamespace(formulation="ac"),
        result,
        generators=inputs.generators,
        loads=inputs.loads,
        nondispatchable=inputs.nondispatchable,
        storage=storage,
        delta=inputs.delta,
        tolerances=policy.tolerances,
        include_terminal=hard_target,
    )
    residuals = dict(physical.residuals)
    limits = {
        k: getattr(policy.tolerances, k, policy.tolerances.ac_active_balance_pu_abs)
        for k in residuals
    }
    boxes = box_residuals(inputs, result, window.start)
    residuals.update(boxes)
    limits.update(
        {
            k: policy.tolerances.soc_recurrence_mwh_abs
            if k.endswith("mwh_abs")
            else policy.tolerances.ac_active_balance_pu_abs
            for k in boxes
        }
    )
    network = network_reporting_residuals(inputs, result)
    residuals.update(network)
    limits.update(
        {
            k: policy.tolerances.ac_reactive_balance_pu_abs
            if k.startswith("q_") or "branch_q" in k
            else policy.tolerances.ac_active_balance_pu_abs
            for k in network
        }
    )
    for name, frame in (("p_load", inputs.df_load_p), ("q_load", inputs.df_load_q)):
        expected = (
            np.zeros(shapes[name])
            if frame is None
            else frame.loc[:, [u.device_id for u in inputs.loads]]
            .iloc[window.start : window.stop]
            .to_numpy()
        )
        residuals[name + "_input_mw_abs"] = float(
            np.max(np.abs(np.asarray(result[name]) - expected))
        )
        limits[name + "_input_mw_abs"] = tolerances.lock_mw_abs
    if inputs.nondispatchable:
        residuals["renewable_lock_mw_abs"] = float(
            np.max(np.abs(np.asarray(result["p_nd"]) - window.renewable_mw))
        )
        limits["renewable_lock_mw_abs"] = tolerances.lock_mw_abs
    if arm != "B":
        residuals["battery_lock_mw_abs"] = float(
            np.max(np.abs(np.asarray(result["b"]) - window.battery_mw))
        )
        limits["battery_lock_mw_abs"] = tolerances.lock_mw_abs
    metrics = cost_and_changes(inputs, window, result)
    if arm == "R2":
        if repair_budget_mwh is None:
            raise ValueError("R2 audit missing repair budget")
        residuals["repair_budget_mwh_abs"] = max(
            0.0, metrics["departure_mwh"] - repair_budget_mwh
        )
        limits["repair_budget_mwh_abs"] = tolerances.repair_mwh_abs
    objective = metrics["departure_mwh"] if arm == "R1" else metrics["common_cost"]
    for name, reported, expected, limit in (
        (
            "objective_abs",
            result.get("objective"),
            objective,
            tolerances.repair_mwh_abs
            if arm == "R1"
            else tolerances.cost_abs + tolerances.cost_rel * abs(objective),
        ),
        (
            "common_cost_abs",
            reported_common_cost,
            metrics["common_cost"],
            tolerances.cost_abs + tolerances.cost_rel * abs(metrics["common_cost"]),
        ),
        (
            "storage_cost_abs",
            result.get("storage_cost"),
            metrics["storage_cost"],
            tolerances.cost_abs + tolerances.cost_rel * abs(metrics["storage_cost"]),
        ),
    ):
        residuals[name] = (
            abs(float(reported) - expected)
            if reported is not None and np.isfinite(reported)
            else float("inf")
        )
        limits[name] = limit
    return {
        "accepted": bool(
            physical.accepted_primal
            and exception is None
            and all(np.isfinite(v) and v <= limits[k] for k, v in residuals.items())
        ),
        "missing": list(physical.missing_or_nonfinite_fields),
        "identity_error": physical.identity_error,
        "exception": exception,
        "metrics": metrics,
        "residuals": residuals,
        "limits": limits,
    }
