"""Serial matched study. Import/preflight/status never optimize; launch is gated."""
# ruff: noqa: E402 -- thread controls precede numerical imports in every CLI worker.
import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
if __name__ == "__main__":
    for key in THREAD_KEYS:
        os.environ[key] = "1"

import cvxpy as cp
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
from cvxopf import build_opf_multistep, extract_results
from experiments.case118_annual_hierarchy.run_s4 import _child_rss_mib
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_json, atomic_immutable_json, atomic_gzip_json,
)
from experiments.case118_tracy_2021.run_e3 import supervise
from experiments.case118_tracy_2021.stage_d import read, reference
from experiments.numerical_preparation.audit import evidence_record, serializable
from experiments.m11_socp.e1_compare import native_record
from . import matched_model as model, matched_audit

MODULE = "experiments.numerical_preparation.run_matched"


def utc():
    return datetime.now(timezone.utc).isoformat()


def validate_output(root):
    root = Path(root).resolve()
    allowed = (model.HERE / "results").resolve()
    if root.parent != allowed or not root.name.startswith("matched_comparison_"):
        raise ValueError("use a new matched_comparison_* directory in numerical_preparation/results")
    return root


def monitoring():
    try:
        rss = _child_rss_mib(os.getpid())
    except OSError as exc:
        raise RuntimeError(f"required process/RSS access unavailable: {exc}") from exc
    if rss is None or not math.isfinite(rss) or rss < 0:
        raise RuntimeError("required process/RSS access unavailable")
    value = dict(utc=utc(), rss_mib=rss, platform=sys.platform)
    for name, command in (("thermal", ["pmset", "-g", "therm"]),
                          ("battery", ["pmset", "-g", "batt"]),
                          ("load", ["uptime"])):
        try:
            value[name] = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=1.)
        except (OSError, subprocess.SubprocessError) as exc:
            value[name] = dict(unavailable=f"{type(exc).__name__}: {exc}", advisory=True)
    return value


def verify_sources(binding):
    for name, expected in binding["context"]["sources"].items():
        if model.digest(model.ROOT / name) != expected:
            raise ValueError(f"execution dependency changed: {name}")
    if (model.ROOT / "ipopt.opt").exists():
        raise ValueError("ambient ipopt.opt appeared")


class WorkerClock:
    """Disjoint monotonic phase durations; native time is an explicit subset."""
    def __init__(self, directory):
        self.directory, self.began = directory, time.monotonic()
        self.last, self.current, self.timings = self.began, None, {}

    def phase(self, name):
        now = time.monotonic()
        if self.current is not None:
            self.timings[self.current] = self.timings.get(self.current, 0.)+now-self.last
        self.last, self.current = now, name
        atomic_json(self.directory / "phase.json", dict(phase=name, utc=utc(), elapsed_seconds=now-self.began))

    def finish(self):
        self.phase("published")
        return dict(phases=self.timings, worker_body_seconds=time.monotonic()-self.began)


class CapturingIPOPT(IPOPT):
    """Worker-local stock baseline adapter; no registry or global method patch."""
    def __init__(self, start):
        super().__init__()
        self.start = start
        self.native, self.handoff, self.elapsed = None, None, None

    def solve_via_data(self, data, warm_start, verbose, solver_opts, solver_cache=None):
        offset, layout = 0, []
        for variable in data["problem"].variables():
            layout.append([list(variable.shape), offset, offset+variable.size])
            offset += variable.size
        self.handoff = serializable(dict(complete_x0=data["x0"], layout=layout))
        if self.handoff != {k: self.start[k] for k in ("complete_x0", "layout")}:
            raise ValueError("baseline actual x0 differs from prospective complete start")
        began = time.monotonic()
        try:
            self.native = super().solve_via_data(data, warm_start, verbose, solver_opts, solver_cache)
            self.elapsed = time.monotonic()-began
            return self.native
        finally:
            if self.elapsed is None:
                self.elapsed = time.monotonic()-began


def baseline_public_solve(build, capture, options):
    """Instrument only this Problem's solve boundary, never a global registry.

    Installed DNLP accepts solver names, not solver instances. OPFBuild.solve
    still supplies its production defaults; the local hook uses precisely the
    installed stock chain with a pass-through IPOPT observer. All physical
    leaves already have the frozen complete cold start.
    """
    original_solve = build.prob.solve
    def observed_solve(**kwargs):
        solver, nlp = kwargs.pop("solver"), kwargs.pop("nlp")
        if solver != "IPOPT" or nlp is not True:
            raise ValueError("baseline requires the stock DNLP/IPOPT boundary")
        warm, verbose = kwargs.pop("warm_start", False), kwargs.pop("verbose", False)
        if warm:
            raise ValueError("matched baseline must cold start")
        chain = model.SolvingChain(reductions=[
            model.CvxAttr2Constr(reduce_bounds=not capture.BOUNDED_VARIABLES), model.Dnlp2Smooth(), capture])
        data, inverse = chain.apply(build.prob)
        solution = capture.solve_via_data(data, False, verbose, kwargs, solver_cache=None)
        build.prob.unpack_results(solution, chain, inverse)
    build.prob.solve = observed_solve
    try:
        build.solve(**options)
    finally:
        build.prob.solve = original_solve


def worker(root, directory):
    clock = WorkerClock(directory)
    binding, request = read(root / "binding.json"), read(directory / "request.json")
    item = binding["rows"][request["arm_id"]-1]
    arm = item["arm"]
    record = dict(schema=1, iteration=arm["id"], request=request, row_sha256=model.input_digest(item),
        optimizer_calls=0, public_solve_calls=0, classification="infrastructure_failure", exception=None,
        native=None, preparation=None, result=None, start=None, handoff=None)
    build, baseline_solver = None, None
    try:
        clock.phase("slice_admission")
        deadline = time.monotonic()+5.
        while not (directory / "launch.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        if read(directory / "launch.json")["pid"] != os.getpid() or (directory / "supervision.json").exists():
            raise ValueError("worker is not the supervised live launch")
        if request["binding"] != reference(root / "binding.json", root) or (root / "STOP").exists():
            raise ValueError("worker binding/stop mismatch")
        verify_sources(binding)
        kwargs = model.thaw(item)
        clock.phase("build")
        build = build_opf_multistep(**kwargs)
        clock.phase("prospective_preparation")
        baseline_solver = None
        if arm["formulation"] == "ac":
            assigned = serializable(model.physical_start(build, kwargs))
            record["start"] = model.ac_start(build, assigned)
            atomic_gzip_json(directory / "start.json.gz", dict(iteration=arm["id"], data=record["start"]))
            if arm["treatment"] == "baseline":
                baseline_solver = CapturingIPOPT(record["start"])
        else:
            data, _, inverse, _, _, _, _ = model.canonical_problem(build)
            convex_row = dict(arm=arm, canonical=dict(objective_offset=float(inverse[-1]["offset"])))
            record["canonical_dimensions"] = dict(variables=data["c"].size, rows=data["b"].size)
        clock.phase("public_solve")
        record["public_solve_calls"] = 1
        options = dict(item["settings"])
        if arm["formulation"] == "ac":
            options["solver"] = "IPOPT"
        else:
            options.update(solver="CLARABEL", warm_start=False, verbose=True,
                           canon_backend=build.canonicalization_backend)
        numerical_exception = None
        try:
            if baseline_solver is not None:
                baseline_public_solve(build, baseline_solver, options)
            else:
                build.solve(**options)
        except cp.error.SolverError as exc:
            numerical_exception = f"{type(exc).__name__}: {exc}"
        clock.phase("extraction")
        record["preparation"] = evidence_record(build.preparation_evidence)
        if baseline_solver is not None:
            record.update(native=serializable(baseline_solver.native), handoff=baseline_solver.handoff,
                          native_call_seconds=baseline_solver.elapsed)
            atomic_gzip_json(directory / "handoff.json.gz", dict(iteration=arm["id"], data=record["handoff"]))
        elif arm["formulation"] == "ac":
            if record["preparation"] is not None:
                record["native"] = record["preparation"]["native"]
                record["native_call_seconds"] = record["native"]["elapsed_seconds"]
        else:
            record["native"], record["preparation"] = native_record(build, convex_row)
            record["native_call_seconds"] = record["native"]["solve_time"]
        if record["native"] is None:
            raise RuntimeError(f"solver returned without native evidence: {numerical_exception}")
        record["optimizer_calls"] = 1  # A native return, not just prospective preparation.
        atomic_gzip_json(directory / "returned.json.gz", dict(iteration=arm["id"], native=record["native"], preparation=record["preparation"]))
        wanted = 0 if arm["formulation"] == "ac" else "Solved"
        if record["native"]["status"] != wanted:
            record.update(classification="native_rejected", exception=numerical_exception,
                          audit=dict(passed=False, reason="native solver did not fully converge"))
        else:
            if numerical_exception is not None:
                raise RuntimeError(numerical_exception)
            record["result"] = serializable(extract_results(build))
            record["named_costs"] = {k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")}
            clock.phase("independent_audit")
            record["audit"] = matched_audit.assess(kwargs, record)
            record["classification"] = "accepted" if record["audit"]["passed"] else "audit_rejected"
    except Exception as exc:
        record.update(classification="infrastructure_failure", exception=f"{type(exc).__name__}: {exc}")
        # Inversion/observation failure after a native return must not erase
        # that effort or its diagnostic primal. It remains unaccepted.
        try:
            if build is not None:
                evidence = evidence_record(build.preparation_evidence)
                if evidence is not None:
                    record.update(preparation=evidence, native=evidence["native"])
                elif baseline_solver is not None and baseline_solver.native is not None:
                    record.update(native=serializable(baseline_solver.native), handoff=baseline_solver.handoff)
            if record["native"] is not None:
                record["optimizer_calls"] = 1
                if not (directory / "returned.json.gz").exists():
                    atomic_gzip_json(directory / "returned.json.gz", dict(iteration=arm["id"],
                        native=record["native"], preparation=record["preparation"]))
        except Exception as secondary:
            record["secondary_archive_error"] = f"{type(secondary).__name__}: {secondary}"
    clock.phase("archive")
    atomic_gzip_json(directory / "result.json.gz", serializable(record))
    atomic_immutable_json(directory / "timing.json", clock.finish())
    names = ["request.json", "result.json.gz", "timing.json"]
    names += [p.name for p in directory.glob("*.json.gz") if p.name != "result.json.gz"]
    atomic_immutable_json(directory / "completion.json", dict(utc=utc(),
        classification=record["classification"], artifacts={n: model.digest(directory / n) for n in names}))


def reconcile(root, binding):
    """Retain partials; charge uncertain orphan effort, never accept their result."""
    directories = sorted(root.glob("attempts/attempt-*"))
    for number, directory in enumerate(directories):
        if (directory / "supervision.json").exists():
            continue
        if not (directory / "request.json").exists():
            if number != len(directories)-1 or any(not p.name.startswith(".") for p in directory.iterdir()):
                raise ValueError("request missing from a nonempty scientific attempt; inspect before resume")
            prefix = status(root, _directories=directories[:number])
            arm_id = prefix["next_arm"]
            if arm_id is None:
                raise ValueError("empty reservation after completed/blocked study")
            arm = binding["rows"][arm_id-1]["arm"]
            wall = min(binding["limits"]["wall_seconds_by_T"][str(arm["T"])],
                       binding["limits"]["total_worker_seconds"]-prefix["worker_seconds"])
            atomic_immutable_json(directory / "request.json", dict(arm_id=arm_id, wall_seconds=wall,
                binding=reference(root / "binding.json", root), protocol=reference(root / "protocol.json", root)))
            atomic_immutable_json(directory / "supervision.json", dict(classification="orphaned_interruption",
                returncode=None, wall_seconds=0., peak_sampled_rss_mib=None,
                accounting="request publication interrupted before any possible launch", ended_utc=utc()))
            continue
        if (directory / "launch.json").exists():
            pid = read(directory / "launch.json")["pid"]
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError(f"possible live orphan PID {pid}; inspect before resume")
        request = read(directory / "request.json")
        atomic_immutable_json(directory / "supervision.json", dict(classification="orphaned_interruption",
            returncode=None, wall_seconds=request["wall_seconds"], peak_sampled_rss_mib=None,
            accounting="unknown effort charged full reserved ceiling; cleanup unknown", ended_utc=utc()))


def status(root, *, audit=False, _directories=None):
    """Fast hash/receipt projection by default; explicit independent full replay."""
    binding = read(root / "binding.json")
    if read(root / "protocol.json") != dict(protocol=binding["limits"]):
        raise ValueError("frozen protocol differs")
    if audit and model.context() != binding["context"]:
        raise ValueError("independent replay dependencies/input/runtime differ")
    disposed, attempts, used = {}, [], 0.
    directories = sorted(root.glob("attempts/attempt-*")) if _directories is None else _directories
    for number, directory in enumerate(directories, 1):
        if directory.name != f"attempt-{number:03d}":
            raise ValueError("attempt journal is not contiguous")
        if not (directory / "request.json").exists():
            if number != len(directories):
                raise ValueError("missing request precedes later work")
            arm_id = next((r["arm"]["id"] for r in binding["rows"] if r["arm"]["id"] not in disposed), None)
            attempts.append(dict(path=str(directory.relative_to(root)), arm_id=arm_id,
                                 classification="awaiting_request", accepted=False))
            break
        request = read(directory / "request.json")
        arm_id = request["arm_id"]
        expected_arm = next((r["arm"]["id"] for r in binding["rows"] if r["arm"]["id"] not in disposed), None)
        if (request["binding"] != reference(root / "binding.json", root) or arm_id != expected_arm or
                request["protocol"] != reference(root / "protocol.json", root)):
            raise ValueError("attempt binding differs or repeats disposed arm")
        item = binding["rows"][arm_id-1]
        maximum = binding["limits"]["wall_seconds_by_T"][str(item["arm"]["T"])]
        if number > binding["limits"]["max_launches"] or not 0 < request["wall_seconds"] <= min(
                maximum, binding["limits"]["total_worker_seconds"]-used):
            raise ValueError("request exceeds cumulative allowance")
        summary = dict(path=str(directory.relative_to(root)), arm_id=arm_id, accepted=False)
        sup_path = directory / "supervision.json"
        if not sup_path.exists():
            if number != len(directories):
                raise ValueError("partial attempt precedes later work")
            summary.update(classification="active_partial", phase=read(directory / "phase.json")
                           if (directory / "phase.json").exists() else None)
            samples_path = directory / "resources.jsonl"
            samples = [json.loads(line) for line in samples_path.read_text().splitlines()] if samples_path.exists() else []
            summary["latest_resource"] = samples[-1] if samples else None
            attempts.append(summary)
            break
        sup = read(sup_path)
        if "samples" in sup:
            samples_path = directory / "resources.jsonl"
            samples = [json.loads(line) for line in samples_path.read_text().splitlines()] if samples_path.exists() else []
            rss_values = [sample["rss_mib"] for sample in samples]
            if (len(samples) != sup["samples"] or any(not math.isfinite(v) or v < 0 for v in rss_values) or
                    max(rss_values, default=0.) != sup["peak_sampled_rss_mib"]):
                raise ValueError("resource journal disagrees with supervision")
        elapsed = sup["wall_seconds"]
        if not isinstance(elapsed, (float, int)) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid supervised effort")
        used += elapsed
        summary.update(classification=sup["classification"], worker_seconds=elapsed,
                       peak_sampled_rss_mib=sup.get("peak_sampled_rss_mib"))
        complete = directory / "completion.json"
        record = None
        if complete.exists():
            manifest = read(complete)
            if not {"request.json", "result.json.gz", "timing.json"} <= set(manifest["artifacts"]):
                raise ValueError("incomplete publication manifest")
            for name, sha in manifest["artifacts"].items():
                if Path(name).name != name or model.digest(directory / name) != sha:
                    raise ValueError("published artifact changed")
            record = read(directory / "result.json.gz")
            if (record["request"] != request or record["row_sha256"] != model.input_digest(item) or
                    record["classification"] != manifest["classification"]):
                raise ValueError("result/input/request/publication differs")
            if record["classification"] != "infrastructure_failure" and "native" in record:
                if "returned.json.gz" not in manifest["artifacts"] or read(directory / "returned.json.gz") != dict(
                        iteration=arm_id, native=record["native"], preparation=record["preparation"]):
                    raise ValueError("native return archive differs")
                if item["arm"]["formulation"] == "ac":
                    if "start.json.gz" not in manifest["artifacts"] or read(directory / "start.json.gz") != dict(
                            iteration=arm_id, data=record["start"]):
                        raise ValueError("prospective start archive differs")
                    if item["arm"]["treatment"] == "baseline" and (
                            "handoff.json.gz" not in manifest["artifacts"] or read(directory / "handoff.json.gz") != dict(
                                iteration=arm_id, data=record["handoff"])):
                        raise ValueError("baseline handoff archive differs")
            summary.update(worker_classification=record["classification"],
                           timings=read(directory / "timing.json"),
                           native_call_seconds=record.get("native_call_seconds"))
            if sup["classification"] == "exited" and sup["returncode"] == 0:
                if record["classification"] == "infrastructure_failure":
                    summary["failure"] = record["exception"]
                else:
                    if audit:
                        checked = matched_audit.assess(model.thaw(item), record)
                        if checked != record["audit"]:
                            raise ValueError("independent replay differs from published audit")
                    summary["accepted"] = record["classification"] == "accepted"
                    if summary["accepted"] != record["audit"]["passed"]:
                        raise ValueError("published acceptance disagrees with audit")
                    summary["audit"] = record["audit"]
                    disposed[arm_id] = summary
            summary["publication_utc"] = manifest["utc"]
        elif sup["classification"] == "exited" and sup["returncode"] == 0:
            raise ValueError("successful worker lacks complete publication")
        if sup["classification"] == "wall_limit":
            disposed[arm_id] = summary
        decision = directory / "disposition.json"
        if decision.exists():
            summary["decision"] = read(decision)
            if summary["decision"]["action"] == "dispose":
                disposed[arm_id] = summary
        attempts.append(summary)
    remaining = [r["arm"]["id"] for r in binding["rows"] if r["arm"]["id"] not in disposed]
    blocked = attempts and attempts[-1]["arm_id"] not in disposed and not (root / attempts[-1]["path"] / "disposition.json").exists()
    rows = [dict(arm=r["arm"], disposition=disposed.get(r["arm"]["id"])) for r in binding["rows"]]
    pairs = []
    for pair in sorted({r["arm"]["pair"] for r in binding["rows"]}):
        members = [r for r in rows if r["arm"]["pair"] == pair]
        accepted = len(members) == 2 and all(r["disposition"] and r["disposition"]["accepted"] for r in members)
        comparison = dict(pair=pair, eligible=accepted)
        if accepted:
            a, b = sorted(members, key=lambda r: r["arm"]["treatment"])
            da, db = a["disposition"], b["disposition"]
            ca, cb = (d["audit"]["economics"]["physical_objective"] for d in (da, db))
            comparison.update(physical_cost_delta_prepared_minus_baseline=cb-ca)
            comparison["relative_cost_delta"] = (cb-ca)/abs(ca) if ca else None
            comparison["ens_delta_mwh"] = (db["audit"]["physical"]["metrics"]["energy_not_served_mwh"]-
                da["audit"]["physical"]["metrics"]["energy_not_served_mwh"]) if "physical" in da["audit"] else None
            # AC preparation moves derivative-oracle setup outside the native
            # adapter timer. Only public-call/whole-worker clocks are matched
            # end-to-end comparisons; raw native timers remain diagnostics.
            for key in ("worker_seconds",):
                comparison[key+"_baseline_over_prepared"] = da[key]/db[key] if db[key] > 0 else None
            ta, tb = (d["timings"]["phases"]["public_solve"] for d in (da, db))
            comparison["public_solve_baseline_over_prepared"] = ta/tb if tb > 0 else None
        pairs.append(comparison)
    return dict(rows=rows, pairs=pairs, attempts=attempts, reservations=len(attempts),
        worker_seconds=used, limits=binding["limits"], disposed_arms=len(disposed),
        accepted_arms=sum(a["accepted"] for a in disposed.values()), matrix_complete=not remaining,
        next_arm=None if blocked else (remaining[0] if remaining else None),
        pending_disposition=bool(blocked), independently_replayed=audit,
        artifact_counts={name:sum((d / filename).exists() for d in directories) for name, filename in (
            ("result_archives", "result.json.gz"), ("completion_manifests", "completion.json"),
            ("finalized_supervision", "supervision.json"), ("native_returns", "returned.json.gz"),
            ("ac_starts", "start.json.gz"))},
        acceptance_evidence="independent replay" if audit else "hash-checked worker audit receipts")


def run(root, commit, *, resume=False, acknowledge_stop=False, replacement_allowance=0,
        retry_interrupted=False, binding_factory=model.frozen_binding, supervisor=supervise):
    root = validate_output(root)
    if not resume and root.exists():
        raise ValueError("new launch requires a new output directory")
    if not resume:
        binding = binding_factory(replacement_allowance)
        revision = model.execution_revision(binding["context"], commit)
        root.mkdir(parents=True)
        atomic_immutable_json(root / "binding.json", binding)
        atomic_immutable_json(root / "authority.json", dict(execution_commit=revision,
            binding=reference(root / "binding.json", root), serial_workers=1,
            limits=binding["limits"], execution_authorized=True))
        # Existing supervisor consumes only poll_seconds and enforces 16 GiB.
        atomic_immutable_json(root / "protocol.json", dict(protocol=binding["limits"]))
    with (root / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        binding = read(root / "binding.json")
        current = model.context()
        model.execution_revision(current, commit)
        if current != binding["context"]:
            raise ValueError("resume changes relevant inputs/source/environment")
        authority = read(root / "authority.json")
        if authority != dict(execution_commit=authority.get("execution_commit"),
                binding=reference(root / "binding.json", root), serial_workers=1,
                limits=binding["limits"], execution_authorized=True):
            raise ValueError("launch authority differs")
        if acknowledge_stop:
            if not resume:
                raise ValueError("STOP acknowledgement requires resume")
            if (root / "STOP").exists():
                stop = root / "stops" / f"stop-{time.time_ns()}.json"
                stop.parent.mkdir(exist_ok=True)
                (root / "STOP").rename(stop)
        reconcile(root, binding)
        invocation = root / "invocations" / f"invocation-{time.time_ns()}"
        atomic_immutable_json(invocation / "start.json", dict(utc=utc(), resume=resume,
            telemetry=monitoring(), commit=commit))
        outcome = "infrastructure_failure"
        try:
            progress = status(root)
            if progress["pending_disposition"]:
                tail = root / progress["attempts"][-1]["path"]
                if progress["attempts"][-1]["classification"] == "active_partial":
                    raise ValueError("active partial cannot be disposed")
                if retry_interrupted:
                    allowance = binding["limits"]["max_launches"]-len(binding["rows"])
                    if not resume or progress["reservations"]-progress["disposed_arms"]-1 >= allowance:
                        raise ValueError("no declared replacement allowance remains")
                    action = "retry"
                else:
                    action = "dispose"
                atomic_immutable_json(tail / "disposition.json", dict(action=action, utc=utc(),
                    reason="explicit same-policy resume; failed attempt remains unaccepted"))
            while True:
                progress = status(root)
                atomic_json(root / "progress.json", progress)
                if progress["matrix_complete"]:
                    outcome = "matrix_complete"
                    break
                if (root / "STOP").exists():
                    outcome = "operator_stop"
                    break
                if (progress["worker_seconds"] >= binding["limits"]["total_worker_seconds"] or
                        progress["reservations"] >= binding["limits"]["max_launches"]):
                    outcome = "budget_exhausted"
                    break
                if progress["next_arm"] is None:
                    outcome = "needs_disposition"
                    break
                verify_sources(binding)
                item = binding["rows"][progress["next_arm"]-1]
                wall = min(binding["limits"]["wall_seconds_by_T"][str(item["arm"]["T"])],
                           binding["limits"]["total_worker_seconds"]-progress["worker_seconds"])
                directory = root / "attempts" / f"attempt-{progress['reservations']+1:03d}"
                request = dict(arm_id=item["arm"]["id"], wall_seconds=wall,
                    binding=reference(root / "binding.json", root), protocol=reference(root / "protocol.json", root))
                atomic_immutable_json(directory / "request.json", request)
                sup = supervisor([sys.executable, "-m", MODULE, "--output", str(root),
                                  "--worker", directory.name], directory, root, request)
                progress = status(root)
                atomic_json(root / "progress.json", progress)
                if (sup["classification"] not in {"exited", "wall_limit"} or
                        sup["classification"] == "exited" and sup["returncode"] != 0 or
                        progress["pending_disposition"]):
                    outcome = "operator_stop" if sup["classification"] == "interrupted" else "infrastructure_failure"
                    break
        except KeyboardInterrupt:
            outcome = "operator_stop"
        finally:
            atomic_immutable_json(invocation / "finish.json", dict(utc=utc(), outcome=outcome))
            atomic_json(root / "outcome.json", dict(utc=utc(), outcome=outcome))
    return dict(output=str(root), outcome=outcome, progress=status(root))


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ("preflight", "status", "report", "stop", "authorize-execution"):
        modes.add_argument("--"+name, action="store_true")
    modes.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=model.OUTPUT)
    parser.add_argument("--commit")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--acknowledge-stop", action="store_true")
    parser.add_argument("--replacement-allowance", type=int, default=0)
    parser.add_argument("--retry-interrupted", action="store_true")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    root = validate_output(args.output)
    if (args.resume or args.acknowledge_stop or args.retry_interrupted) and not args.authorize_execution:
        parser.error("resume/acknowledgement/replacement requires separately authorized execution")
    if args.retry_interrupted and not args.resume:
        parser.error("replacement requires explicit resume")
    if args.resume and args.replacement_allowance:
        parser.error("replacement allowance is frozen at initial launch, not enlarged on resume")
    if args.worker:
        directory = root / "attempts" / args.worker
        if directory.parent.resolve() != (root / "attempts").resolve() or not args.worker.startswith("attempt-"):
            parser.error("invalid worker path")
        worker(root, directory)
        return
    if args.preflight:
        binding = model.frozen_binding(args.replacement_allowance, canonical=True)
        result = dict(preflight_only=True, optimizer_calls=0, output_created=False,
            limits=binding["limits"], gates=binding["gates"], telemetry=monitoring(),
            context_sha256=model.input_digest(binding["context"]),
            rows=[dict(arm=row["arm"], input_sha256=row["input_sha256"], policy=row["policy"],
                       settings=row["settings"], canonical_start_size=len(row["preflight_start"]["complete_x0"])
                       if "preflight_start" in row else None) for row in binding["rows"]])
    elif args.status or args.report:
        with model.no_optimization():
            result = status(root, audit=args.audit)
            if args.report:
                from .matched_report import render
                result = render(root, result)
    elif args.stop:
        if not (root / "binding.json").exists():
            parser.error("no matched study to stop")
        atomic_immutable_json(root / "STOP", dict(utc=utc(), reason="operator request"))
        result = dict(stop_requested=True)
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, interrupted)
        result = run(root, args.commit, resume=args.resume, acknowledge_stop=args.acknowledge_stop,
            replacement_allowance=args.replacement_allowance, retry_interrupted=args.retry_interrupted)
    print(json.dumps(serializable(result), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
