"""One supervised counterfactual solve with complete starts and immutable output."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import time

import numpy as np

from cvxopf import LayerSolveConfig, extract_results
from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.s5_speculative_archive import invocation
from experiments.case118_annual_hierarchy.s5_speculative_attempt import (
    PreparedAttempt,
    load_retained_start,
    start_payload,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
    sha256_path,
)
from .data import restore_window
from .model import ComparisonTolerances, audit_result, build_arm, map_start


def jsonable(value):
    if isinstance(value, np.ndarray):
        return jsonable(value.tolist())
    if isinstance(value, np.generic):
        return jsonable(value.item())
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def referenced_json(ref):
    path = Path(ref["path"])
    if sha256_path(path) != ref["sha256"]:
        raise ValueError("counterfactual source artifact changed")
    return json.loads(path.read_text())


def execute_attempt(directory, inputs, policy, solve_config, request):
    """Testable numerical seam; the process coordinator supplies all budgets."""
    if any(
        (directory / name).exists()
        for name in ("phase.json", "start.json", "result.json")
    ):
        raise FileExistsError(
            "counterfactual attempt directory already contains evidence"
        )
    spec = invocation(request["invocation"])
    window = restore_window(request["window"])
    arm = request["arm"]
    if spec.window.iteration != window.start:
        raise ValueError("attempt does not match the selected window")
    if arm != "B" and spec.source_slot in (1, 2, 3, 4, 5):
        raise ValueError("fixed-battery attempts require explicit start perturbations")
    request_hash = object_sha256(request)
    events = []

    def phase(name):
        events.append({"phase": name, "monotonic_seconds": time.monotonic()})
        atomic_json(
            directory / "phase.json", {"invocation": asdict(spec), "events": events}
        )

    phase("before_ac_build")
    model = build_arm(
        inputs,
        policy,
        window,
        arm,
        repair_budget_mwh=request["repair_budget_mwh"],
        hard_target=spec.hard_target,
    )
    source_id = None
    source = None
    if request["source"] is not None:
        source = referenced_json(request["source"])
        if (
            source["window_identity"] != window.identity
            or not source["audit"]["accepted"]
        ):
            raise ValueError("initialization source lacks matching accepted evidence")
        source_id = invocation(source["invocation"]).attempt_id
    center = map_start(model, None if source is None else source["solution_values"])
    assigned = center
    raw = {name: value.copy() for name, value in center.items()}
    if source is not None:
        raw.update(
            {
                name: np.asarray(source["solution_values"][name], dtype=float).copy()
                for name in model.physical_names
            }
        )
    replay = None
    if spec.replay_of is not None:
        ref = request["replay"]
        referenced_json(ref)
        replay = load_retained_start(Path(ref["path"]))
        old_request = referenced_json(request["replay_request"])
        contract_keys = (
            "window",
            "arm",
            "repair_budget_mwh",
            "tolerances",
            "ac_options",
            "execution",
        )
        if (
            replay.invocation.attempt_id != spec.replay_of
            or replay.invocation.window != spec.window
            or replay.invocation.source_slot != spec.source_slot
            or replay.request_sha256 != object_sha256(old_request)
            or any(old_request[k] != request[k] for k in contract_keys)
        ):
            raise ValueError("replay belongs to a different arm, model, or invocation")
        raw, assigned = dict(replay.raw), dict(replay.assigned)
        source_id = replay.source_attempt_id
    elif spec.source_slot >= 3:
        raw, assigned = streaming.perturbed_start(
            center, model.build, scale=spec.scale, seed=spec.seed
        )
    if spec.source_slot in (2, 3, 4, 5):
        if (
            source is None
            or invocation(source["invocation"]).hard_target
            or source["arm"] != "B"
        ):
            raise ValueError(
                "B recovery requires this arm's accepted target-free source"
            )
    streaming.assign_start(model.build, assigned)
    prepared = PreparedAttempt(
        spec,
        request_hash,
        model.build,
        dict(zip(inputs.storage_device_ids, window.soc_mwh[0])),
        dict(zip(inputs.storage_device_ids, window.soc_mwh[-1])),
        window.stop,
        raw,
        assigned,
        "attempt" if source_id else "generated_flat",
        source_id,
        None if replay is None else replay.evidence,
    )
    phase("after_ac_build")

    def retain(evidence):
        atomic_immutable_json(
            directory / "start.json", start_payload(prepared, evidence)
        )
        # Start the solve clock only once canonicalization and retention finish.
        phase("before_ac_solve")

    run = streaming.solve_ac_with_verified_x0(
        model.build,
        solve_config,
        start_observer=retain,
        replay_start=prepared.replay_start,
    )
    if events[-1]["phase"] == "before_ac_solve":
        phase("after_ac_solve")
    result = extract_results(model.build)
    cost = model.common_cost.value
    cost = None if cost is None else float(cost)
    audit = audit_result(
        inputs,
        policy,
        window,
        arm,
        result,
        ComparisonTolerances(**request["tolerances"]),
        repair_budget_mwh=request["repair_budget_mwh"],
        hard_target=spec.hard_target,
        exception=run.exception,
        reported_common_cost=cost,
    )
    variables = streaming.variables_by_name(model.build)
    values = {name: v.value for name, v in variables.items()}
    stats = model.build.prob.solver_stats
    payload = jsonable(
        {
            "kind": "counterfactual_attempt",
            "invocation": asdict(spec),
            "arm": arm,
            "window_identity": window.identity,
            "request_sha256": request_hash,
            "result": result,
            "common_cost_expression": cost,
            "audit": audit,
            "solution_values": values,
            "exception": run.exception,
            "solve_wall_seconds": run.elapsed_seconds,
            "solver_num_iters": getattr(stats, "num_iters", None),
            "start_sha256": sha256_path(directory / "start.json")
            if (directory / "start.json").exists()
            else None,
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
        raise ValueError("worker execution source differs from coordinator")
    fixture = load_s4_fixture()
    config = replace(
        fixture.solve_config, ac=LayerSolveConfig("IPOPT", request["ac_options"])
    )
    execute_attempt(args.directory, fixture.inputs, fixture.policy, config, request)
    if not (args.directory / "start.json").exists():
        raise RuntimeError(
            "construction/canonicalization failed before native solve; see result.json"
        )
    if execution_identity() != request["execution"]:
        raise ValueError("execution source changed during worker solve")


if __name__ == "__main__":
    main()
