"""Bounded incumbent-preserving continuation and original hard-target retries."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np

from cvxopf._ac_start_mapping import variables_by_name
from experiments.case118_annual_hierarchy.run_s4 import _child_rss_mib, _terminate
from . import stage_d as d
from .stage_b import TOLERANCES

MODULE = "experiments.case118_tracy_2021.soft_target_continuation"
ORIGINAL = d.HERE / "results/stage_d/trajectory-15/hour-00/attempt-000/request.json"
SWEEP = d.HERE / "results/soft_target_2452_003"
PER_WORKER_SECONDS = 300
TOTAL_SECONDS = 1500
RSS_MIB = 8192


def write(path, value):
    d.atomic_immutable_json(path, d.jsonable(value))


def model_kwargs(base, weight):
    if weight is None:
        return base  # Exact original hard equality model.
    return dict(base, storage=[replace(s, terminal_constraint=None,
        terminal_cost="quadratic" if weight else None,
        terminal_weight=weight if weight else None,
        terminal_soc=s.terminal_soc if weight else None) for s in base["storage"]])


def evaluate(payload, base, request):
    """Reaudit physics independently of solver status; reconstruct all costs."""
    result = payload["result"]
    weight = payload.get("weight")
    error = np.asarray(result["soc"], dtype=float)[-1] - request["target_soc_mwh"]
    sse = float(np.sum(error ** 2))
    penalty = float((weight or 0) * sse)
    operating = dict(result, objective=float(result["objective"]) - penalty)
    physical = dict(base, storage=[replace(s, terminal_soc=None,
        terminal_constraint=None, terminal_cost=None, terminal_weight=None)
        for s in base["storage"]])
    audit = d.audit_result(operating, physical, payload["named_costs"])
    residuals = audit["residuals"]
    feasible = bool(residuals) and all(np.isfinite(v) and v <= audit["limits"][k]
                                     for k, v in residuals.items())
    cost = sum(audit.get("costs", {}).values())
    terminal_residual = abs(payload["named_costs"].get("storage_terminal_cost", 0) - penalty)
    terminal_limit = TOLERANCES["cost_abs"] + TOLERANCES["cost_rel"] * abs(penalty)
    feasible = feasible and terminal_residual <= terminal_limit
    return dict(physically_feasible=feasible, audit=audit, sse_mwh2=sse,
        max_error_mwh=float(np.max(abs(error))), target_error_mwh=error,
        shedding_mwh=float(result["energy_not_served"]), operating_cost=float(cost),
        terminal_accounting_residual=terminal_residual,
        terminal_accounting_limit=terminal_limit, status=result["status"])


def score(candidate, weight):
    return candidate["operating_cost"] + weight * candidate["sse_mwh2"]


def incumbent(candidates, weight):
    eligible = [c for c in candidates if c["physically_feasible"]]
    if not eligible:
        raise ValueError("no independently feasible incumbent")
    # Stable min preserves the earlier incumbent on exact ties.
    return min(eligible, key=lambda c: score(c, weight))


def best_tracking(candidates):
    return min((c for c in candidates if c["physically_feasible"]),
               key=lambda c: c["sse_mwh2"])


def materially_different(a, b):
    """Predeclared physical-state thresholds, not cross-weight objectives."""
    x, y = a["result"], b["result"]
    rms = lambda key: float(np.sqrt(np.mean((np.asarray(x[key])-y[key])**2)))
    return (rms("soc") > 1.0 or
            float(np.max(abs(np.asarray(x["Vm"])-y["Vm"]))) > .01 or
            rms("Pg") > 1.0 or rms("Qg") > 1.0)


def complete_primal(build, payload, initial):
    """Recover every model coordinate, never using a default/partial start.

    New continuation archives carry native model primals. Historical sweep
    archives only expose engineering-unit results, so lifted Ybus entries are
    reconstructed from Vm/theta and checked through the public result mapping.
    """
    if payload.get("native_primal") is not None:
        values = {k: np.asarray(v, dtype=float) for k, v in payload["native_primal"].items()}
    else:
        r = payload["result"]
        pu = build.data["baseMVA"]
        values = {k: np.asarray(r[k], dtype=float).T for k in
                  ("b", "b_q", "p_nd", "q_nd", "load_shed_fraction")}
        for name, key in {"Pg":"Pg", "Qg":"Qg", "p":"p_net", "q":"q_net",
                "branch_p_from_pu":"branch_p_from", "branch_p_to_pu":"branch_p_to",
                "branch_q_from_pu":"branch_q_from", "branch_q_to_pu":"branch_q_to"}.items():
            values[name] = np.asarray(r[key], dtype=float).T / pu
        values["v"] = np.asarray(r["Vm"], dtype=float).T
        values["theta"] = np.deg2rad(np.asarray(r["Va_deg"], dtype=float).T)
        values["soc"] = np.column_stack((initial, np.asarray(r["soc"]).T))
        rows, cols = build.data["rows"], build.data["cols"]
        angle = values["theta"][rows] - values["theta"][cols]
        vv = values["v"][rows] * values["v"][cols]
        g, b = build.data["G_vec"][:, None], build.data["B_vec"][:, None]
        values["P_vec"] = vv * (g*np.cos(angle) + b*np.sin(angle))
        values["Q_vec"] = vv * (g*np.sin(angle) - b*np.cos(angle))
    if set(values) != set(variables_by_name(build)):
        raise ValueError("incomplete or mismatched primal namespace")
    if not all(np.isfinite(v).all() for v in values.values()):
        raise ValueError("nonfinite start")
    np.testing.assert_allclose(values["soc"][:, 0], initial, rtol=0, atol=1e-10)
    d.starts._assign_start(build, values)
    # extract_results intentionally exposes assigned model values before solve.
    reconstructed = d.extract_results(build)
    errors = {}
    for key in ("Vm", "Va_deg", "Pg", "Qg", "p_net", "q_net", "b", "b_q",
                "soc", "p_nd", "q_nd", "load_shed_fraction", "branch_p_from",
                "branch_q_from", "branch_p_to", "branch_q_to"):
        original = np.asarray(payload["result"][key])
        actual = np.asarray(reconstructed[key])
        np.testing.assert_allclose(actual, original, rtol=1e-10, atol=1e-7)
        errors[key] = float(np.max(abs(actual-original)))
    for key, lifted in (("p", "P_vec"), ("q", "Q_vec")):
        errors[f"ybus_{key}_pu"] = float(np.max(abs(build.data["Rp"] @ values[lifted]-values[key])))
        if errors[f"ybus_{key}_pu"] > 1e-6:
            raise ValueError("reconstructed lifted network injections fail physical tolerance")
    return values, errors


def worker(directory):
    trial = d.read(directory / "request.json")
    manifest = d.read(directory.parent / "manifest.json")
    for path, sha in manifest["sources"].items():
        if d.digest(Path(path)) != sha:
            raise ValueError(f"source changed: {path}")
    before = d.context()
    seed_path = Path(trial["seed"])
    if d.digest(seed_path) != trial["seed_sha256"]:
        raise ValueError("seed changed")
    payload = d.read(seed_path)
    request = d.read(ORIGINAL)
    base = d.request_kwargs(d.verified_inputs(), request)
    seed_audit = evaluate(payload, base, request)
    if not seed_audit["physically_feasible"]:
        raise ValueError("seed failed independent physics audit")
    weight = trial["weight"]
    kwargs = model_kwargs(base, weight)
    build = d.build_opf_multistep(**kwargs)
    values, errors = complete_primal(build, payload, request["initial_soc_mwh"])
    write(directory / "start.json", dict(native_primal=values, roundtrip_errors=errors,
        seed_audit=seed_audit, initially_hard_target_feasible=
        seed_audit["max_error_mwh"] <= TOLERANCES["endpoint_mwh"]))

    def observe(e):
        d.atomic_gzip_json(directory / "x0.json.gz", d.jsonable(dict(iteration=2452,
            complete_x0=e.complete_x0, layout=[dict(x) for x in e.layout],
            layout_signature=e.layout_signature, model_coordinate_count=e.model_coordinate_count,
            auxiliary_coordinate_count=e.auxiliary_coordinate_count,
            object_ids_before=dict(e.object_ids_before), object_ids_after=dict(e.object_ids_after))))

    solved = d.starts._solve_ac_with_verified_x0(build,
        d.HierarchicalSolveConfig(ac=d.LayerSolveConfig("IPOPT", options=d.SOLVER_OPTIONS)),
        start_observer=observe)
    write(directory / "solve-outcome.json", dict(exception=solved.exception,
        seconds=solved.elapsed_seconds, captured_x0=solved.evidence is not None))
    if solved.evidence is None:
        raise RuntimeError(f"start capture failed: {solved.exception}")
    result = d.jsonable(d.extract_results(build))
    named = {k: float(v.value) for k, v in build.expressions.items()
             if k.endswith("_cost") and v.value is not None}
    after = d.context()
    output = dict(iteration=2452, weight=weight, result=result, named_costs=named,
        native_primal=d.starts._solution_values(build), exception=solved.exception,
        solve_seconds=solved.elapsed_seconds, context_before=before, context_after=after)
    checked = evaluate(output, base, request)
    gate = d.audit_result(result, base, named) if weight is None else checked["audit"]
    output.update(evaluation=checked, acceptance_audit=gate,
        accepted=solved.exception is None and before == after and gate["passed"]
        and checked["physically_feasible"])
    d.atomic_gzip_json(directory / "result.json.gz", d.jsonable(output))
    write(directory / "completion.json", dict(result_sha256=d.digest(directory / "result.json.gz")))
    if before != after:
        raise RuntimeError("execution context changed")


def supervise(directory, remaining):
    start = time.monotonic()
    limit = min(PER_WORKER_SECONDS, remaining)
    classification, peak = "exited", 0.0
    with (directory / "worker.log").open("xb") as log, (directory / "resources.jsonl").open("x") as resources:
        process = subprocess.Popen([sys.executable, "-m", MODULE, "--worker", str(directory)],
            cwd=d.ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        write(directory / "launch.json", dict(pid=process.pid, wall_budget_seconds=limit))
        try:
            while process.poll() is None:
                rss = _child_rss_mib(process.pid)
                if rss is None and process.poll() is None:
                    classification = "monitor_failure"
                    break
                peak = max(peak, rss or 0)
                resources.write(json.dumps(dict(elapsed_seconds=time.monotonic()-start, rss_mib=rss))+"\n")
                resources.flush()
                if peak > RSS_MIB or time.monotonic()-start >= limit:
                    classification = "rss_limit" if peak > RSS_MIB else "wall_limit"
                    break
                time.sleep(.5)
        finally:
            if process.poll() is None:
                _terminate(process)
            code = process.wait()
    record = dict(classification=classification, returncode=code,
                  wall_seconds=time.monotonic()-start, peak_rss_mib=peak)
    write(directory / "supervision.json", record)
    if classification in {"rss_limit", "monitor_failure"} or (classification == "exited" and code):
        raise RuntimeError(f"diagnostic implementation/supervision failure: {record}")
    return record


def load_candidate(path, base, request):
    payload = d.read(path)
    checked = evaluate(payload, base, request)
    return dict(path=str(path), **checked)


def run(root):
    root.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    request = d.read(ORIGINAL)
    if request["global_hour"] != 2452 or request["W"] != 1:
        raise ValueError("unexpected original problem")
    base = d.request_kwargs(d.verified_inputs(), request)
    sources = [ORIGINAL, Path(__file__), d.HERE / "SOFT_TARGET_CONTINUATION.md"]
    sources += sorted((d.ROOT / "src/cvxopf").glob("*.py"))
    sources += [d.HERE / name for name in ("stage_d.py", "stage_b.py", "stage_d_physics.py")]
    paths = [SWEEP / f"trial-{i:02d}/result.json.gz" for i in range(8)]
    sweep_manifest = d.read(SWEEP / "manifest.json")
    if sweep_manifest["source"][str(ORIGINAL)] != d.digest(ORIGINAL):
        raise ValueError("original request differs from sweep provenance")
    sources.append(SWEEP / "manifest.json")
    sources += paths
    write(root / "manifest.json", dict(sources={str(p): d.digest(p) for p in sources},
        context=d.context(), total_seconds=TOTAL_SECONDS, per_worker_seconds=PER_WORKER_SECONDS,
        rss_mib=RSS_MIB, solver_options=d.SOLVER_OPTIONS, default_max_iter=3000,
        plan="SOFT_TARGET_CONTINUATION.md"))
    pool = [load_candidate(p, base, request) for p in paths]
    write(root / "initial_candidates.json", pool)
    carry = pool[4]  # First continuation must start from accepted lambda=10.
    old_high = pool[7]
    records = []

    def trial(name, weight, seed):
        remaining = TOTAL_SECONDS - (time.monotonic()-start) - 10  # reap allowance
        if remaining <= 0:
            raise TimeoutError("total diagnostic budget exhausted")
        directory = root / name
        directory.mkdir()
        seed_path = Path(seed["path"])
        write(directory / "request.json", dict(weight=weight, seed=str(seed_path),
            seed_sha256=d.digest(seed_path)))
        supervision = supervise(directory, remaining)
        candidate = None
        if supervision["classification"] == "exited":
            candidate = load_candidate(directory / "result.json.gz", base, request)
            pool.append(candidate)
        return candidate, supervision

    try:
        for index, weight in enumerate((100, 1000, 10000)):
            existing = pool[index+5]  # Frozen original sweep rows.
            contenders = [carry, existing]
            before = incumbent(contenders, weight)
            seed = carry if index == 0 else before
            returned, supervision = trial(f"soft-{weight}", weight, seed)
            carry = incumbent(contenders + ([returned] if returned else []), weight)
            record = dict(weight=weight, seed=seed["path"], incumbent_before=before,
                returned=returned, selected=carry, supervision=supervision,
                incumbent_objective=score(before, weight), selected_objective=score(carry, weight))
            records.append(record)
            write(root / f"selection-{weight}.json", record)
            print(d.jsonable(dict(weight=weight, selected=carry["path"], sse_mwh2=carry["sse_mwh2"],
                max_error_mwh=carry["max_error_mwh"], shedding_mwh=carry["shedding_mwh"])), flush=True)
        best = best_tracking(pool)
        second = materially_different(d.read(Path(best["path"])), d.read(Path(old_high["path"])))
        write(root / "hard-seed-selection.json", dict(best=best, old_high=old_high,
            second_seed_materially_different=second))
        for index, seed in enumerate([best] + ([old_high] if second else [])):
            returned, supervision = trial(f"hard-{index:02d}", None, seed)
            accepted = bool(returned and d.read(Path(returned["path"]))["accepted"])
            records.append(dict(hard_retry=index, seed=seed, returned=returned,
                                accepted=accepted, supervision=supervision))
            print(d.jsonable(dict(hard_retry=index, accepted=accepted,
                evaluation=returned)), flush=True)
            if accepted:
                break
        write(root / "finished.json", dict(outcome="diagnostic_complete", records=records,
            wall_seconds=time.monotonic()-start))
    except BaseException as exc:
        write(root / "stopped.json", dict(reason=f"{type(exc).__name__}: {exc}",
            records=records, wall_seconds=time.monotonic()-start))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
    elif args.output:
        run(args.output.resolve())
    else:
        parser.error("supply --output or --worker")
