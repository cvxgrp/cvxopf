"""Matched standalone windows: inputs, audits and one-attempt archival.

No numerical work on import. The supervisor owns launching and protocol revisions.
"""

from dataclasses import replace
from datetime import datetime, timezone
import inspect
import os
from importlib.metadata import version
from pathlib import Path
import time

import numpy as np

from cvxopf import (build_opf_multistep, extract_results, audit_socp_relaxation,
                    recover_socp_voltage)
from cvxopf.socp_diagnostics import SOCPAuditTolerances
from cvxopf import _hierarchical_solver as starts
from cvxopf._ac_start_mapping import stepwise_values
from cvxopf.hierarchical import HierarchicalSolveConfig, LayerSolveConfig
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_gzip_json, atomic_immutable_json, atomic_json,
)
from .annual_results import load_results
from .prepare import HERE, ROOT, digest
from .run_stage_b import context as base_context, jsonable, convergence_diagnostics
from .stage_b import Arm, inputs_for_arm, verified_inputs, audit_result
from .stage_d import ROLES, SCALES, SOLVER_OPTIONS, prepare_start, read
from .stage_d_physics import audit_reactive_channels
from tests.socp_matched import digest as input_digest, validate_matching


FORMULATIONS = ("singlenode_dc", "lossy_dc", "socp", "ac")
DEFAULT_PROTOCOL = dict(
    schema=1, rss_mib=16384., wall_seconds=1800., total_worker_seconds=43200.,
    max_launches=70, poll_seconds=1.,
    ac_options=dict(SOLVER_OPTIONS),
    convex_options=dict(tol_gap_abs=1e-10, tol_gap_rel=1e-10,
                        tol_feas=1e-10, max_iter=5000, max_threads=1),
)
RELAXATION_TOLERANCES = SOCPAuditTolerances(energy=1e-4, fraction=1e-8)


def referenced(root, ref):
    if set(ref) != {"path", "sha256"}:
        raise ValueError("invalid artifact reference")
    path = (root / ref["path"]).resolve()
    if root.resolve() not in path.parents or digest(path) != ref["sha256"]:
        raise ValueError("artifact reference escapes run or has changed")
    return read(path)


def validate_protocol(p):
    """Mutable execution controls only; model, gates, ladder and RSS stay fixed."""
    if set(p) != set(DEFAULT_PROTOCOL) or p["schema"] != 1:
        raise ValueError("unsupported protocol schema/fields")
    if isinstance(p["rss_mib"], bool) or p["rss_mib"] != 16384:
        raise ValueError("RSS ceiling is fixed at 16 GiB")
    for key in ("wall_seconds", "total_worker_seconds", "max_launches", "poll_seconds"):
        if isinstance(p[key], bool) or not np.isfinite(p[key]) or p[key] <= 0:
            raise ValueError(f"invalid protocol budget: {key}")
    if not isinstance(p["max_launches"], int):
        raise ValueError("max_launches must be an integer")
    ac = p["ac_options"]
    if not isinstance(ac, dict) or not set(SOLVER_OPTIONS) <= set(ac) or set(ac)-set(SOLVER_OPTIONS)-{"max_iter"}:
        raise ValueError("unsupported AC controls")
    if ac["verbose"] is not True or ac["warm_start"] is not False or ac["mu_strategy"] != "adaptive":
        raise ValueError("AC execution/capture policy cannot change")
    if isinstance(ac["tol"], bool) or not np.isfinite(ac["tol"]) or ac["tol"] <= 0:
        raise ValueError("invalid AC tolerance")
    if "max_iter" in ac and (type(ac["max_iter"]) is not int or ac["max_iter"] <= 0):
        raise ValueError("invalid AC iteration cap")
    convex = p["convex_options"]
    if not isinstance(convex, dict) or set(convex) != set(DEFAULT_PROTOCOL["convex_options"]):
        raise ValueError("unsupported convex controls")
    for key, value in convex.items():
        if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
            raise ValueError("invalid convex solver setting")
        if key in ("max_iter", "max_threads") and type(value) is not int:
            raise ValueError("integer solver control required")
    if convex["max_threads"] != 1:
        raise ValueError("single-thread execution required")
    return p


def make_study(selection):
    ids, capacity = selection["storage_device_ids"], np.asarray(selection["capacity_mwh"], float)
    if len(ids) != 27 or len(set(ids)) != 27 or capacity.shape != (27,) or not np.isfinite(capacity).all() or np.any(capacity <= 0):
        raise ValueError("invalid selected fleet")
    if len(selection["periods"]) != 4:
        raise ValueError("four windows required")
    arms = []
    for period in selection["periods"]:
        chosen = period["candidates"][0]
        if chosen["stop"]-chosen["start"] != 24 or not 0 <= chosen["start"] <= 8736:
            raise ValueError("invalid complete 24-hour window")
        legs = [("energy_neutral", .5, .5)]
        if period["regime"] == "Large deficit":
            legs.append(("deficit_depletion", .6, .25))
        for leg, initial, terminal in legs:
            for formulation in FORMULATIONS:
                arms.append(dict(id=len(arms), window=period["regime"], leg=leg,
                    start=chosen["start"], stop=chosen["stop"], rho=1/3,
                    throughput=.01, formulation=formulation,
                    initial_soc_mwh=(initial*capacity).tolist(),
                    terminal_soc_mwh=(terminal*capacity).tolist()))
    if len(arms) != 20:
        raise ValueError("exactly one deficit-depletion leg required")
    return dict(schema=1, arms=arms, storage_device_ids=ids, roles=list(ROLES),
                scales=list(SCALES), source_hashes=selection["source_hashes"])


def specification():
    selection = read(HERE / "e3_selection/selection.json")
    data = load_results()
    if selection["source_hashes"] != data["hashes"] or selection["storage_device_ids"] != data["tables"]["batteries"].device_id.tolist():
        raise ValueError("selected archive/identities changed")
    np.testing.assert_array_equal(selection["capacity_mwh"], data["tables"]["batteries"].capacity_mwh)
    return make_study(selection)


def context():
    import cyipopt
    if (ROOT / "ipopt.opt").exists():
        raise ValueError("ipopt.opt override is forbidden")
    c = base_context("E3_PROTOCOL.md")
    c.update(ipopt_version=list(cyipopt.IPOPT_VERSION),
             ipopt_interface_sha256=digest(Path(inspect.getfile(starts.IPOPT))),
             ipopt_default_max_iter=3000,
             extra_packages={n: version(n) for n in ("cyipopt", "sparsediffpy")},
             selection_sha256=digest(HERE / "e3_selection/selection.json"),
             boundary_decisions_sha256=digest(HERE / "E3_BOUNDARY_DECISIONS.md"))
    sources = sorted((ROOT / "src/cvxopf").rglob("*.py")) + list(HERE.glob("*.py")) + [
        ROOT / "uv.lock", ROOT / "tests/socp_matched.py", ROOT / "tests/socp_reference_cases.py"]
    c["code_sha256"] = {str(p.relative_to(ROOT)): digest(p) for p in sources}
    return c


def verify_context(expected):
    if not expected["clean"] or context() != expected:
        raise ValueError("source/environment changed; no cross-source resume")


def kwargs_for_arm(prepared, arm, ids, role="primary"):
    kwargs = inputs_for_arm(prepared, Arm(**{k: arm[k] for k in
        ("window", "start", "stop", "rho", "throughput", "formulation")}))
    if [u.device_id for u in kwargs["storage"]] != ids:
        raise ValueError("storage identity mismatch")
    kwargs["storage"] = [replace(u, initial_soc=float(initial),
        terminal_soc=None if role == "target_free" else float(terminal),
        terminal_constraint=None if role == "target_free" else "equality")
        for u, initial, terminal in zip(kwargs["storage"], arm["initial_soc_mwh"],
            arm["terminal_soc_mwh"], strict=True)]
    kwargs.update(temporal_assembly="vectorized", automatic_sparse_dispatch=False)
    return kwargs


def audit(build, result, kwargs, named):
    """Same Tracy accounting gate; explicit numeric lifted-network extension."""
    if kwargs["formulation"] != "socp":
        return audit_result(result, kwargs, named), None
    relaxation = audit_socp_relaxation(build, result, tolerances=RELAXATION_TOLERANCES)

    def network(a, kw, injection, check, box):
        if not relaxation["available"] or not relaxation["complete"]:
            check("lifted_audit_available", float("inf"), 0)
            return
        for name, item in relaxation["residuals"].items():
            check("socp_"+name, item["maximum"], item["tolerance"])
        nodal = relaxation["nodal_power_mva"]
        for name, expected in (("p_net", nodal.real), ("q_net", nodal.imag)):
            check(name+"_lifted_reporting", float(np.max(abs(a[name]-expected))), 1e-4)
        audit_reactive_channels(a, kw, check, box,
                                relaxation["branch_from_mva"], relaxation["branch_to_mva"])

    common = audit_result(result, kwargs, named, network_audit=network)
    return common, relaxation


def encode(value):
    """Keep complex numerical diagnostic maps explicit in JSON."""
    if isinstance(value, np.ndarray) and np.iscomplexobj(value):
        return dict(real=jsonable(value.real), imag=jsonable(value.imag))
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    return jsonable(value)


def worker(directory, root):
    began = time.monotonic()
    binding, request = read(root / "binding.json"), read(directory / "request.json")
    verify_context(binding["context"])
    if read(directory / "launch.json")["pid"] != os.getpid():
        raise ValueError("worker must be launched by the resource supervisor")
    revision = referenced(root, request["protocol"])
    protocol = validate_protocol(revision["protocol"])
    for source in (request["causal_source"], request["target_free_source"]):
        if source is not None:
            referenced(root, source)
    arm = binding["study"]["arms"][request["arm_id"]]
    timings = {}
    phase_started, current_phase = began, None

    def phase(name):
        nonlocal phase_started, current_phase
        now = time.monotonic()
        if current_phase:
            timings[current_phase] = now-phase_started
        phase_started, current_phase = now, name
        atomic_json(directory / "phase.json", dict(phase=name,
            utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=now-began))

    record = dict(iteration=request["arm_id"], request=request,
                  execution_context=binding["context"],
                  classification="exception", result=None, audit=None)
    try:
        phase("preparation")
        kwargs = kwargs_for_arm(verified_inputs(), arm, binding["study"]["storage_device_ids"], request["role"])
        neutral = dict(case=kwargs["case"], kwargs={k: v for k, v in kwargs.items()
                       if k not in {"case", "formulation"}})
        record["mathematical_input_sha256"] = (validate_matching(neutral, neutral)
            if arm["formulation"] in {"ac", "socp"} else input_digest(neutral))
        phase("construction")
        build = build_opf_multistep(**kwargs)
        record["input_identities"] = dict(storage=[u.device_id for u in kwargs["storage"]],
            loads=[u.device_id for u in kwargs["loads"]],
            renewables=[u.device_id for u in kwargs["nondispatchable"]])
        record["problem_size"] = dict(variables=build.prob.size_metrics.num_scalar_variables,
            equalities=build.prob.size_metrics.num_scalar_eq_constr,
            inequalities=build.prob.size_metrics.num_scalar_leq_constr,
            constraint_objects=len(build.prob.constraints))
        error = None
        if arm["formulation"] == "ac":
            start_request = dict(role=request["role"], global_hour=arm["start"],
                initial_soc_mwh=arm["initial_soc_mwh"], storage_device_ids=binding["study"]["storage_device_ids"],
                previous=None, causal_source=request["causal_source"],
                target_free_source=request["target_free_source"])
            causal, assigned = prepare_start(build, kwargs, start_request, root)
            atomic_immutable_json(directory / "start.json", jsonable(dict(causal_start=causal, assigned_start=assigned)))

            def observe(evidence):
                atomic_gzip_json(directory / "x0.json.gz", jsonable(dict(
                    iteration=request["arm_id"],
                    complete_x0=evidence.complete_x0, layout=[dict(x) for x in evidence.layout],
                    layout_signature=evidence.layout_signature,
                    model_coordinate_count=evidence.model_coordinate_count,
                    auxiliary_coordinate_count=evidence.auxiliary_coordinate_count,
                    object_ids_before=dict(evidence.object_ids_before),
                    object_ids_after=dict(evidence.object_ids_after))))
                phase("native_solve")

            phase("canonicalization_and_solve")
            config = HierarchicalSolveConfig(ac=LayerSolveConfig("IPOPT", options=protocol["ac_options"]))
            solved = starts._solve_ac_with_verified_x0(build, config, start_observer=observe)
            if solved.evidence is None:
                raise RuntimeError(f"missing verified complete x0: {solved.exception}")
            error = solved.exception
        else:
            phase("canonicalization_and_solve")
            try:
                build.solve(solver="CLARABEL", canon_backend=build.canonicalization_backend,
                    warm_start=False, verbose=True, **protocol["convex_options"])
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            record["convergence"] = convergence_diagnostics(build)
        phase("extraction_and_audit")
        result = jsonable(extract_results(build))
        named = {k: float(e.value) if e.value is not None else None
                 for k, e in build.expressions.items() if k.endswith("_cost")}
        common, relaxation = audit(build, result, kwargs, named)
        record.update(result=result, named_costs=named, audit=common, exception=error,
            relaxation_audit=relaxation,
            recovery=None if arm["formulation"] != "socp" else
                recover_socp_voltage(build, result, tolerances=RELAXATION_TOLERANCES),
            renewable_available_mw=kwargs["df_nd"].to_numpy(),
            boundary_soc_mwh=None if result.get("soc") is None else
                np.vstack((arm["initial_soc_mwh"], result["soc"])),
            logical_solution=None)
        accepted = error is None and common["passed"]
        record["classification"] = "accepted" if accepted else "rejected"
        if accepted and arm["formulation"] == "ac":
            record["logical_solution"] = stepwise_values(build, starts._solution_values(build))
        stats = build.prob.solver_stats
        record["solver_stats"] = None if stats is None else dict(
            iterations=stats.num_iters, native_seconds=stats.solve_time,
            compilation_seconds=build.prob.compilation_time)
        verify_context(binding["context"])
    except Exception as exc:
        record.update(classification="exception", exception=f"{type(exc).__name__}: {exc}")
    phase("archive")
    atomic_gzip_json(directory / "result.json.gz", encode(record))
    phase("finished")
    artifacts = {name: digest(directory / name) for name in
                 ("request.json", "result.json.gz", "start.json", "x0.json.gz") if (directory / name).exists()}
    atomic_immutable_json(directory / "completion.json", dict(
        classification=record["classification"], artifacts=artifacts,
        worker_seconds=time.monotonic()-began, phase_seconds=timings))
    return 0
