"""Isolated, fixed-start quadratic terminal-target diagnostic for Stage D."""

import argparse
import json
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import time

import numpy as np

from . import stage_d as d
from .stage_b import TOLERANCES
from experiments.case118_annual_hierarchy.run_s4 import _child_rss_mib, _terminate

MODULE = "experiments.case118_tracy_2021.soft_target_sweep"
SOURCE = d.HERE / "results/stage_d/trajectory-15/hour-00"
WEIGHTS = (0, .01, .1, 1, 10, 100, 1000, 10000)
WALL_SECONDS = 600
RSS_MIB = 8192


def write(path, value):
    d.atomic_immutable_json(path, d.jsonable(value))


def worker(directory):
    start = time.monotonic()
    trial = d.read(directory / "request.json")
    manifest = d.read(directory.parent / "manifest.json")
    assert manifest["script_sha256"] == d.digest(Path(__file__))
    assert all(d.digest(Path(p)) == sha for p, sha in manifest["source"].items())
    before = d.context()
    request = d.read(SOURCE / "attempt-000/request.json")
    seed = d.read(SOURCE / "attempt-005/result.json.gz")
    assert seed["accepted"] and seed["request"]["role"] == "target_free"
    assert request["global_hour"] == 2452 and request["W"] == 1
    assert seed["request"]["initial_soc_mwh"] == request["initial_soc_mwh"]
    kwargs = d.request_kwargs(d.verified_inputs(), request)
    weight = trial["weight"]
    kwargs["storage"] = [replace(s, terminal_constraint=None,
        terminal_soc=s.terminal_soc if weight else None,
        terminal_cost="quadratic" if weight else None,
        terminal_weight=weight if weight else None) for s in kwargs["storage"]]
    build = d.build_opf_multistep(**kwargs)
    raw = {k: np.asarray(v) for k, v in seed["logical_solution"].items()}
    assigned = d.pack_start(raw, build, request["initial_soc_mwh"])
    d.starts._assign_start(build, assigned)
    write(directory / "start.json", dict(raw=raw, assigned=assigned))

    def observe(e):
        d.atomic_gzip_json(directory / "x0.json.gz", d.jsonable(dict(
            iteration=2452,
            complete_x0=e.complete_x0, layout=[dict(x) for x in e.layout],
            layout_signature=e.layout_signature,
            model_coordinate_count=e.model_coordinate_count,
            auxiliary_coordinate_count=e.auxiliary_coordinate_count)))

    options = dict(d.SOLVER_OPTIONS, max_iter=3000, max_cpu_time=540)
    solved = d.starts._solve_ac_with_verified_x0(build,
        d.HierarchicalSolveConfig(ac=d.LayerSolveConfig("IPOPT", options=options)),
        start_observer=observe)
    write(directory / "solve-outcome.json", dict(exception=solved.exception,
        elapsed_seconds=solved.elapsed_seconds, captured_x0=solved.evidence is not None))
    if solved.evidence is None or solved.exception is not None:
        raise RuntimeError(solved.exception)
    result = d.jsonable(d.extract_results(build))
    named = {k: float(v.value) for k, v in build.expressions.items()
             if k.endswith("_cost") and v.value is not None}
    # Stage B audit assumes no terminal cost. Audit the operating objective
    # after subtracting an independently reconstructed soft penalty; retain
    # the original result unchanged and check the named terminal term too.
    error = np.asarray(result["soc"])[-1] - request["target_soc_mwh"]
    penalty = float(weight * np.sum(error ** 2))
    physical_kwargs = dict(kwargs, storage=[replace(s, terminal_soc=None,
        terminal_constraint=None, terminal_cost=None, terminal_weight=None)
        for s in kwargs["storage"]])
    operating = dict(result, objective=result["objective"] - penalty)
    audit = d.audit_result(operating, physical_kwargs, named)
    terminal_actual = named.get("storage_terminal_cost", 0.0)
    terminal_residual = abs(terminal_actual - penalty)
    terminal_limit = TOLERANCES["cost_abs"] + TOLERANCES["cost_rel"] * abs(penalty)
    after = d.context()
    accepted = (solved.exception is None and solved.evidence is not None
                and audit["passed"] and terminal_residual <= terminal_limit
                and before == after)
    payload = dict(iteration=2452, weight=weight, accepted=accepted, result=result,
        named_costs=named, audit=audit, exception=solved.exception,
        target_error_mwh=error, target_max_abs_mwh=float(np.max(abs(error))),
        target_rms_mwh=float(np.sqrt(np.mean(error ** 2))),
        operating_cost=operating["objective"], penalty=penalty,
        terminal_accounting_residual=terminal_residual,
        terminal_accounting_limit=terminal_limit,
        solve_seconds=solved.elapsed_seconds, worker_seconds=time.monotonic()-start,
        context_before=before, context_after=after,
        iterations=getattr(build.prob.solver_stats, "num_iters", None))
    d.atomic_gzip_json(directory / "result.json.gz", d.jsonable(payload))
    write(directory / "completion.json", dict(accepted=accepted,
        result_sha256=d.digest(directory / "result.json.gz")))


def run(root):
    root.mkdir(parents=True, exist_ok=False)
    write(root / "manifest.json", dict(weights=WEIGHTS, context=d.context(),
        script_sha256=d.digest(Path(__file__)), wall_seconds=WALL_SECONDS,
        rss_mib=RSS_MIB, max_iter=3000, max_cpu_time=540,
        source={str(p): d.digest(p) for p in (
            SOURCE / "attempt-000/request.json", SOURCE / "attempt-005/result.json.gz")},
        initialization="same archived target-free internal solution for every trial",
        stopping_rule="all eight weights; abort on supervision or implementation failure"))
    summaries = []
    for index, weight in enumerate(WEIGHTS):
        directory = root / f"trial-{index:02d}"
        directory.mkdir()
        write(directory / "request.json", dict(weight=weight))
        started = time.monotonic()
        peak = 0.0
        classification = "exited"
        with (directory / "worker.log").open("xb") as log, (directory / "resources.jsonl").open("x") as resources:
            process = subprocess.Popen([sys.executable, "-m", MODULE,
                "--worker", str(directory)], stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
            write(directory / "launch.json", dict(pid=process.pid))
            try:
                while process.poll() is None:
                    rss = _child_rss_mib(process.pid)
                    if rss is None and process.poll() is None:
                        classification = "monitor_failure"
                        break
                    peak = max(peak, rss or 0)
                    resources.write(json.dumps(dict(elapsed_seconds=time.monotonic()-started,
                        rss_mib=rss)) + "\n")
                    resources.flush()
                    if peak > RSS_MIB or time.monotonic()-started > WALL_SECONDS:
                        classification = "resource_limit"
                        break
                    time.sleep(1)
            finally:
                if process.poll() is None:
                    _terminate(process)
                code = process.wait()
        supervision = dict(classification=classification, returncode=code,
            wall_seconds=time.monotonic()-started, peak_rss_mib=peak)
        write(directory / "supervision.json", supervision)
        if code != 0 or classification != "exited":
            write(root / "stopped.json", supervision)
            return
        payload = d.read(directory / "result.json.gz")
        summary = {k: payload[k] for k in ("weight", "accepted", "target_max_abs_mwh",
            "target_rms_mwh", "operating_cost", "penalty", "solve_seconds", "iterations")}
        summaries.append(summary)
        print(summary, flush=True)
    write(root / "summary.json", summaries)


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
