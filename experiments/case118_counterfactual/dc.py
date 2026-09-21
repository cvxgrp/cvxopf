"""Matched convex DC comparisons with independent engineering-unit audits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import time

import cvxpy as cp
import numpy as np

from cvxopf import extract_results
from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
)
from .data import restore_window
from .model import ComparisonTolerances, device_costs, fixed_schedule
from .worker import jsonable


def storage_for(inputs, window):
    return streaming._inner_storage(
        inputs,
        dict(zip(inputs.storage_device_ids, window.soc_mwh[0])),
        dict(zip(inputs.storage_device_ids, window.soc_mwh[-1])),
    )


def build_dc(inputs, policy, window, arm, *, battery_schedule_mw=None):
    if battery_schedule_mw is not None and arm != "F":
        raise ValueError("prescribed battery schedules require fixed DC power")
    window.validate(inputs, policy)
    if arm not in ("F", "B"):
        raise ValueError("DC phase one supports fixed or free battery power")
    build = streaming.build_window(
        inputs, "lossy_dc", window.start, window.stop, storage_for(inputs, window)
    )
    constraints = list(build.prob.constraints)
    if inputs.nondispatchable:
        constraints.append(cp.vstack(build.variables["p_nd"]) == window.renewable_mw)
    if arm == "F":
        constraints.append(
            cp.vstack(build.variables["b"])
            == fixed_schedule(window, battery_schedule_mw)
        )
    build.prob = cp.Problem(build.prob.objective, constraints)
    return build


def dc_metrics(inputs, result):
    generation, storage, throughput = device_costs(inputs, result)
    # DC's internal flow is per-unit. This proxy is an objective term, not
    # physical AC loss energy and not MW^2 times unscaled resistance.
    loss_proxy = float(
        inputs.delta
        * inputs.options.loss_weight
        * np.sum(
            np.asarray(inputs.case["branch"])[:, 2]
            * (np.asarray(result["p_flows"]) / inputs.case["baseMVA"]) ** 2
        )
    )
    return {
        "generation_cost": generation,
        "storage_cost": storage,
        "common_cost": generation + storage,
        "dc_loss_proxy_cost": loss_proxy,
        "native_objective": generation + storage + loss_proxy,
        "throughput_mwh": float(throughput.sum()),
        "throughput_by_device_mwh": throughput.tolist(),
    }


def audit_dc(
    inputs,
    policy,
    window,
    arm,
    result,
    tolerances,
    *,
    exception=None,
    reported_loss_cost=None,
    battery_schedule_mw=None,
):
    if battery_schedule_mw is not None and arm != "F":
        raise ValueError("prescribed battery schedules require fixed DC power")
    if arm not in ("F", "B"):
        raise ValueError("unknown DC arm")
    n = window.stop - window.start
    shapes = {
        "Pg": (n, len(inputs.generators)),
        "b": (n, len(inputs.storage)),
        "soc": (n, len(inputs.storage)),
        "p_flows": (n, len(inputs.case["branch"])),
        "p_net": (n, len(inputs.case["bus"])),
        "p_load": (n, len(inputs.loads)),
        "q_load": (n, len(inputs.loads)),
        "p_load_served": (n, len(inputs.loads)),
    }
    if inputs.nondispatchable:
        shapes.update(
            {k: (n, len(inputs.nondispatchable)) for k in ("p_nd", "curtailment")}
        )
    missing = [
        k
        for k, shape in shapes.items()
        if result.get(k) is None
        or np.shape(result[k]) != shape
        or not np.isfinite(np.asarray(result[k], dtype=float)).all()
    ]
    if missing:
        return {
            "accepted": False,
            "missing": missing,
            "metrics": None,
            "residuals": {},
            "limits": {},
            "exception": exception,
        }
    physical = audit_probe(
        inputs.case,
        SimpleNamespace(formulation="lossy_dc"),
        result,
        generators=inputs.generators,
        loads=inputs.loads,
        nondispatchable=inputs.nondispatchable,
        storage=storage_for(inputs, window),
        delta=inputs.delta,
        tolerances=policy.tolerances,
        branch_limit_sentinel=inputs.options.branch_limit_sentinel,
    )
    residuals = dict(physical.residuals)
    limits = {k: getattr(policy.tolerances, k, 1e-4) for k in residuals}
    base = float(inputs.case["baseMVA"])

    def check(name, value, limit):
        residuals[name] = float(np.max(np.abs(value)))
        limits[name] = limit

    def box(name, value, lower, upper, scale, limit):
        check(
            name, np.maximum(np.maximum(lower - value, value - upper), 0) / scale, limit
        )

    pg, b, soc = (np.asarray(result[k]) for k in ("Pg", "b", "soc"))
    box(
        "generator_p_box_pu_abs",
        pg,
        np.array([g.p_min_mw if g.status else 0 for g in inputs.generators]),
        np.array([g.p_max_mw if g.status else 0 for g in inputs.generators]),
        base,
        policy.tolerances.dc_nodal_balance_pu_abs,
    )
    rating = np.array([s.apparent_power_rating for s in inputs.storage])
    box(
        "storage_power_box_pu_abs",
        b,
        -rating,
        rating,
        base,
        policy.tolerances.dc_nodal_balance_pu_abs,
    )
    box(
        "storage_soc_box_mwh_abs",
        soc,
        0,
        np.array([s.capacity for s in inputs.storage]),
        1,
        policy.tolerances.soc_recurrence_mwh_abs,
    )
    for name, frame in (("p_load", inputs.df_load_p), ("q_load", inputs.df_load_q)):
        expected = (
            np.zeros(shapes[name])
            if frame is None
            else frame.loc[:, [u.device_id for u in inputs.loads]]
            .iloc[window.start : window.stop]
            .to_numpy()
        )
        check(
            name + "_input_mw_abs",
            np.asarray(result[name]) - expected,
            tolerances.lock_mw_abs,
        )
    check(
        "load_service_mw_abs",
        np.asarray(result["p_load_served"]) - result["p_load"],
        tolerances.lock_mw_abs,
    )
    if arm == "F":
        check(
            "battery_lock_mw_abs",
            b - fixed_schedule(window, battery_schedule_mw),
            tolerances.lock_mw_abs,
        )
    if inputs.nondispatchable:
        p = np.asarray(result["p_nd"])
        available = (
            np.array([u.p_available for u in inputs.nondispatchable])
            if inputs.df_nd is None
            else inputs.df_nd.loc[:, [u.device_id for u in inputs.nondispatchable]]
            .iloc[window.start : window.stop]
            .to_numpy()
        )
        box(
            "renewable_box_pu_abs",
            p,
            0,
            np.minimum(
                available, [u.apparent_power_rating for u in inputs.nondispatchable]
            ),
            base,
            policy.tolerances.dc_nodal_balance_pu_abs,
        )
        check("renewable_lock_mw_abs", p - window.renewable_mw, tolerances.lock_mw_abs)
        check(
            "curtailment_identity_mw_abs",
            p + result["curtailment"] - available,
            tolerances.lock_mw_abs,
        )
    metrics = dc_metrics(inputs, result)
    for name, reported, expected in (
        ("objective_abs", result.get("objective"), metrics["native_objective"]),
        ("storage_cost_abs", result.get("storage_cost"), metrics["storage_cost"]),
        ("dc_loss_cost_abs", reported_loss_cost, metrics["dc_loss_proxy_cost"]),
    ):
        value = (
            float(reported) - expected
            if reported is not None and np.isfinite(reported)
            else float("inf")
        )
        check(name, value, tolerances.cost_abs + tolerances.cost_rel * abs(expected))
    return {
        "accepted": bool(
            physical.accepted_primal
            and exception is None
            and all(np.isfinite(v) and v <= limits[k] for k, v in residuals.items())
        ),
        "missing": list(physical.missing_or_nonfinite_fields),
        "identity_error": physical.identity_error,
        "exception": exception,
        "residuals": residuals,
        "limits": limits,
        "metrics": metrics,
    }


def execute_dc(directory, inputs, policy, request):
    window = restore_window(request["window"])
    events = []

    def phase(name):
        events.append({"phase": name, "monotonic_seconds": time.monotonic()})
        atomic_json(
            directory / "phase.json",
            {"invocation": request["invocation"], "events": events},
        )

    if (directory / "result.json").exists() or (directory / "phase.json").exists():
        raise FileExistsError("DC attempt already contains evidence")
    phase("before_dc_build")
    build = build_dc(
        inputs,
        policy,
        window,
        request["arm"],
        battery_schedule_mw=request.get("battery_schedule_mw"),
    )
    phase("after_dc_build")
    exception = None
    phase("before_dc_solve")
    try:
        build.solve(solver="CLARABEL", nlp=False, **request["dc_options"])
    except Exception as exc:
        exception = f"{type(exc).__name__}: {exc}"
    phase("after_dc_solve")
    result = extract_results(build)
    loss = build.expressions["dc_loss_cost"].value
    loss = None if loss is None else float(loss)
    payload = jsonable(
        {
            "kind": "matched_dc_attempt",
            "arm": request["arm"],
            "invocation": request["invocation"],
            "window_identity": window.identity,
            "request_sha256": object_sha256(request),
            "result": result,
            "reported_loss_cost": loss,
            "exception": exception,
            "audit": audit_dc(
                inputs,
                policy,
                window,
                request["arm"],
                result,
                ComparisonTolerances(**request["tolerances"]),
                exception=exception,
                reported_loss_cost=loss,
                battery_schedule_mw=request.get("battery_schedule_mw"),
            ),
            "solver_num_iters": getattr(build.prob.solver_stats, "num_iters", None),
        }
    )
    atomic_immutable_json(directory / "result.json", payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    request = json.loads((args.directory / "request.json").read_text())
    from .runner import execution_identity

    if execution_identity() != request["execution"]:
        raise ValueError("DC worker source differs from coordinator")
    fixture = load_s4_fixture()
    execute_dc(args.directory, fixture.inputs, fixture.policy, request)
    if execution_identity() != request["execution"]:
        raise ValueError("DC worker source changed during solve")


if __name__ == "__main__":
    main()
