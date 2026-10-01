"""Frozen Tracy D1 requests and AC attempt execution, with no outer solves."""

from dataclasses import dataclass, replace
import gzip
from importlib.metadata import version
import json
import inspect
from datetime import datetime, timezone
from pathlib import Path
import time

import numpy as np

from cvxopf import build_opf_multistep, extract_results
from cvxopf import _hierarchical_solver as starts
from cvxopf._ac_start_mapping import pack_start, stepwise_values
from cvxopf.hierarchical import (
    HierarchicalPolicy,
    HierarchicalSolveConfig,
    LayerSolveConfig,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_gzip_json,
    atomic_immutable_json,
    atomic_json,
)
from .annual_results import load_results
from .prepare import HERE, ROOT, digest
from .run_stage_b import context as base_context, jsonable
from .select_stage_d import select_periods
from .stage_b import Arm, audit_result, inputs_for_arm, verified_inputs

ORDERS = ((1, 3, 6, 12), (3, 12, 1, 6), (6, 1, 12, 3), (12, 6, 3, 1))
SCALES = (1e-4, 1e-3, 1e-2)
ROLES = (
    "primary",
    "causal_1",
    "causal_2",
    "causal_3",
    "flat",
    "target_free",
    "copied_target_free",
    "target_free_1",
    "target_free_2",
    "target_free_3",
)
SOLVER_OPTIONS = dict(verbose=True, warm_start=False, mu_strategy="adaptive", tol=1e-7)
RSS_MIB = 16384.0


@dataclass(frozen=True)
class ShiftState:
    delta: float
    storage_device_ids: tuple[str, ...]


def context():
    import cyipopt

    if (ROOT / "ipopt.opt").exists():
        raise ValueError("local ipopt.opt would override the frozen solver settings")
    record = base_context("STAGE_D_PROTOCOL.md")
    record["ipopt_version"] = list(cyipopt.IPOPT_VERSION)
    record["ipopt_default_max_iter"] = (
        3000  # Upstream OPTIONS.html#OPT_max_iter; no override supplied.
    )
    record["ipopt_interface_sha256"] = digest(Path(inspect.getfile(starts.IPOPT)))
    record["packages"].update(
        {name: version(name) for name in ("cyipopt", "sparsediffpy")}
    )
    record["selection_sha256"] = digest(HERE / "stage_d_selection/selection.json")
    return record


def read(path):
    path = Path(path)
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as stream:
            return json.load(stream)
    return json.loads(path.read_text())


def reference(path, root):
    return dict(path=str(path.relative_to(root)), sha256=digest(path))


def referenced(root, ref):
    path = root / ref["path"]
    if digest(path) != ref["sha256"]:
        raise ValueError(f"artifact hash mismatch: {path}")
    return read(path)


def specification():
    data = load_results()
    selection = read(HERE / "stage_d_selection/selection.json")
    shards = read(HERE / "SHARD_BOUNDARIES.json")
    from .shard_boundaries import derive

    if derive(data) != shards:
        raise ValueError("shard state/source no longer matches annual archive")
    derived = select_periods(
        data["inputs"].net_load_mw,
        [b["index"] for b in shards["manifest"]["boundaries"]],
    )
    if selection["source_hashes"] != data["hashes"] or selection[
        "selection_source_sha256"
    ] != digest(HERE / "select_stage_d.py"):
        raise ValueError("selection provenance changed")
    dc = data["runs"]["lossy_dc"]
    trajectories = []
    for i, (selected, expected) in enumerate(
        zip(selection["periods"], derived, strict=True)
    ):
        if any(selected[k] != v for k, v in expected.items()):
            raise ValueError("period derivation changed")
        t = selected["start"]
        np.testing.assert_array_equal(
            selected["initial_soc_mwh"], dc["boundary_soc_mwh"][t]
        )
        for w in ORDERS[i]:
            trajectories.append(
                dict(
                    id=len(trajectories),
                    period=i,
                    label=selected["regime"],
                    start=t,
                    W=w,
                    initial_soc_mwh=selected["initial_soc_mwh"],
                    targets=[
                        dc["boundary_soc_mwh"][t + j + w].tolist() for j in range(6)
                    ],
                )
            )
    return dict(
        schema=1,
        trajectories=trajectories,
        roles=list(ROLES),
        scales=list(SCALES),
        storage_device_ids=selection["storage_device_ids"],
        source_hashes=data["hashes"],
        rss_mib=RSS_MIB,
        wall_limit=None,
        solver_options=SOLVER_OPTIONS,
        annual_execution_authorized=False,
    )


def request_kwargs(prepared, request):
    t, w = request["global_hour"], request["W"]
    kwargs = inputs_for_arm(prepared, Arm("Stage D", t, t + w, 1 / 3, 0.01, "ac"))
    ids = [s.device_id for s in kwargs["storage"]]
    if ids != request["storage_device_ids"]:
        raise ValueError("storage request identity mismatch")
    initial, target = request["initial_soc_mwh"], request["target_soc_mwh"]
    if len(initial) != len(ids) or len(target) != len(ids):
        raise ValueError("storage request shape mismatch")
    kwargs["storage"] = [
        replace(
            s,
            initial_soc=float(initial[j]),
            terminal_soc=None if request["role"] == "target_free" else float(target[j]),
            terminal_constraint=None
            if request["role"] == "target_free"
            else "equality",
        )
        for j, s in enumerate(kwargs["storage"])
    ]
    kwargs["automatic_sparse_dispatch"] = False
    return kwargs


def prepare_start(build, kwargs, request, root):
    """Reuse package transformations; no secondary graph or historical starts."""
    role = request["role"]
    # Capture the fresh builder's cold point before any shift/assignment. The
    # causal source is still retained for subsequent target-free recovery.
    flat = starts._complete_start(build) if role == "flat" else None
    if request["causal_source"] is not None:
        source = referenced(root, request["causal_source"])
        causal = {k: np.array(v) for k, v in source["causal_start"].items()}
    elif request["previous"] is None:
        causal = starts._complete_start(build)
    else:
        preceding = referenced(root, request["previous"])
        state = ShiftState(kwargs["delta"], tuple(request["storage_device_ids"]))
        _, causal = starts._shifted_start(
            {k: np.array(v) for k, v in preceding["logical_solution"].items()},
            build,
            state,
            HierarchicalPolicy(ac_window_steps=kwargs["T"]),
            dict(
                zip(
                    request["storage_device_ids"],
                    request["initial_soc_mwh"],
                    strict=True,
                )
            ),
        )
    assigned = flat if flat is not None else causal
    if role == "copied_target_free" or role.startswith("target_free_"):
        source = referenced(root, request["target_free_source"])
        assigned = pack_start(
            {k: np.array(v) for k, v in source["logical_solution"].items()},
            build,
            request["initial_soc_mwh"],
        )
    if role.startswith("causal_") or role.startswith("target_free_"):
        k = int(role.rsplit("_", 1)[1])
        code = 2 if role.startswith("causal_") else 1
        _, assigned = starts._perturbed_start(
            assigned,
            build,
            scale=SCALES[k - 1],
            seed=17000000 + 100 * request["global_hour"] + 10 * code + k,
        )
    starts._assign_start(build, assigned)
    return causal, assigned


def worker(directory, root):
    """One fresh process, one attempt, archival before parent advancement."""
    from .stage_d_continuation import execution_context

    start = time.monotonic()
    binding, request = read(root / "binding.json"), read(directory / "request.json")
    expected_context = execution_context(root)
    if (directory / "execution-context.json").exists():
        if read(directory / "execution-context.json") != expected_context:
            raise ValueError("attempt execution context differs from continuation")
    elif expected_context != binding["context"]:
        raise ValueError("continued worker lacks execution context")
    if context() != expected_context:
        raise ValueError("worker execution context changed")
    timings = {}
    current_phase, phase_started = None, start

    def phase(name):
        nonlocal current_phase, phase_started
        now = time.monotonic()
        if current_phase is not None:
            timings[current_phase] = now - phase_started
        current_phase, phase_started = name, now
        event = dict(
            phase=name,
            elapsed_seconds=now - start,
            utc=datetime.now(timezone.utc).isoformat(),
        )
        with (directory / "phases.jsonl").open("a") as stream:
            stream.write(json.dumps(event) + "\n")
        atomic_json(directory / "phase.json", event)

    phase("preparation")
    prepared = verified_inputs()
    kwargs = request_kwargs(prepared, request)
    phase("construction")
    construction_start = time.monotonic()
    build = build_opf_multistep(**kwargs)
    construction_seconds = time.monotonic() - construction_start
    causal, assigned = prepare_start(build, kwargs, request, root)
    atomic_immutable_json(
        directory / "start.json",
        jsonable(dict(causal_start=causal, assigned_start=assigned)),
    )

    def observe(evidence):
        payload = dict(
            iteration=request["global_hour"],
            complete_x0=evidence.complete_x0,
            layout=[dict(x) for x in evidence.layout],
            layout_signature=evidence.layout_signature,
            model_coordinate_count=evidence.model_coordinate_count,
            auxiliary_coordinate_count=evidence.auxiliary_coordinate_count,
            object_ids_before=dict(evidence.object_ids_before),
            object_ids_after=dict(evidence.object_ids_after),
        )
        atomic_gzip_json(directory / "x0.json.gz", jsonable(payload))
        phase("native_solve")

    phase("canonicalization_and_solve")
    config = HierarchicalSolveConfig(
        ac=LayerSolveConfig("IPOPT", options=SOLVER_OPTIONS)
    )
    solved = starts._solve_ac_with_verified_x0(build, config, start_observer=observe)
    phase("extraction_and_audit")
    # Audit the same list/scalar representation the parent reads from JSON.
    # Otherwise NumPy's memory-layout-dependent reductions can disagree after
    # serialization, especially for large infeasible iterates.
    result = jsonable(extract_results(build))
    named_costs = {
        name: float(value.value)
        for name, value in build.expressions.items()
        if name.endswith("_cost") and value.value is not None
    }
    audit = audit_result(result, kwargs, named_costs)
    # Transformation/capture errors are implementation defects, not alternate-start opportunities.
    if solved.evidence is None:
        raise RuntimeError(f"IPOPT x0 was not captured: {solved.exception}")
    accepted = solved.exception is None and audit["passed"]
    logical = (
        stepwise_values(build, starts._solution_values(build)) if accepted else None
    )
    if context() != expected_context:
        raise ValueError("worker execution context changed during solve")
    payload = jsonable(
        dict(
            iteration=request["global_hour"],
            request=request,
            execution_context=expected_context,
            accepted=accepted,
            result=result,
            named_costs=named_costs,
            audit=audit,
            exception=solved.exception,
            logical_solution=logical,
            next_soc_mwh=np.asarray(result["soc"])[0] if accepted else None,
            construction_seconds=construction_seconds,
            solve_seconds=solved.elapsed_seconds,
            solver_iterations=getattr(build.prob.solver_stats, "num_iters", None),
            native_solver_seconds=getattr(build.prob.solver_stats, "solve_time", None),
            pre_archive_seconds=time.monotonic() - start,
        )
    )
    phase("archive")
    atomic_gzip_json(directory / "result.json.gz", payload)
    phase("finished")
    atomic_immutable_json(
        directory / "completion.json",
        dict(
            result_sha256=digest(directory / "result.json.gz"),
            x0_sha256=digest(directory / "x0.json.gz"),
            start_sha256=digest(directory / "start.json"),
            phase_seconds=timings,
            worker_seconds=time.monotonic() - start,
        ),
    )
