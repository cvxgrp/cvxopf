"""Execute only the reviewed 25-call protocol, with no retries or default changes.

Run ``--preflight`` to inspect inputs without creating the execution directory.
After owner review/commit and separate execution permission, use
``--authorize-execution --commit <full SHA>``. ``--status`` independently
replays retained evidence without solving. A STOP or interrupted worker consumes
its call; explicit resume can advance only finalized supervision, never retry it.

The one-time ``--resume --adopt-replay-fix`` transition is specific to the
retained call-001 replay-metadata failure. It appends an immutable context for
calls 002–025, allowing changes only to this runner and its infrastructure test.
The original binding, protocol, call-001 evidence, and invocation stay intact.
Numerical inputs/settings, installed packages, production sources, and budgets
must match exactly. Subsequent resumes use that recorded context without another
transition. This is not a general cross-source resume mechanism.
"""
# ruff: noqa: E402 -- thread limits precede all numerical imports.

import argparse
from dataclasses import fields
from datetime import datetime, timezone
import fcntl
from importlib.metadata import distribution, version
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "VECLIB_MAXIMUM_THREADS")
if __name__ == "__main__":
    for key in THREAD_KEYS:
        os.environ[key] = "1"

import numpy as np
import cvxpy as cp
from cvxopf import extract_results
from cvxopf._hierarchical_solver import _complete_start, _solve_ac_with_verified_x0
from experiments.case118_annual_hierarchy.run_s4 import _child_rss_mib
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_json, atomic_immutable_json, atomic_gzip_json)
from experiments.case118_tracy_2021.ac_archive import verify_x0
from experiments.case118_tracy_2021.prepare import digest
from experiments.case118_tracy_2021.run_e3 import supervise
from experiments.case118_tracy_2021.run_stage_b import convergence_diagnostics
from experiments.case118_tracy_2021.stage_d import read, reference
from .fixture import (HERE, ROOT, OUTPUT, LIMITS, PINS, calls, call_binding, kwargs_for_call,
                      build_for_call, structural_inputs, solver_options, verify_pins, e3)
from .audit import (serializable, evidence_record, single_step_result, audit_record,
                    pair_check, historical_control)

MODULE = "experiments.numerical_preparation.run_qualification"
REPLAY_FIX_ORIGIN = "bd936f3ac9367ae2e37b5cc68b80f8cddd69706b"
REPLAY_FIX_BINDING_SHA256 = "6b7e8fe8126f9ced99efd0ed6c4c94d322dab15e85ad02192acacf6adf0c587c"
REPLAY_FIX_INVOCATION = "invocation-1791386299438779000"
REPLAY_FIX_SOURCES = frozenset(("experiments/numerical_preparation/run_qualification.py",
                               "tests/test_preparation_qualification.py"))
REPLAY_FIX_FILES = frozenset(("request.json", "launch.json", "phase.json", "resources.jsonl",
                            "worker.log", "supervision.json", "completion.json", "result.json.gz"))


def utc():
    return datetime.now(timezone.utc).isoformat()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def context():
    """Bind repository sources and installed numerical adapters/extensions."""
    sources = set(git("ls-files").splitlines())
    sources.update(str(p.relative_to(ROOT)) for p in HERE.glob("*.py"))
    sources = {p for p in sources if p.endswith(".py") or p in
               {"uv.lock", "pyproject.toml", str((HERE / "QUALIFICATION_PROTOCOL.md").relative_to(ROOT))}}
    packages, installed = {}, {}
    for name in ("cvxpy", "clarabel", "cyipopt", "sparsediffpy", "numpy", "scipy", "pandas"):
        packages[name] = version(name)
        dist = distribution(name)
        # Stock adapter/oracle Python and native binaries, including bundled BLAS.
        for item in dist.files or ():
            if str(item).endswith((".py", ".so", ".dylib")):
                path = Path(dist.locate_file(item)).resolve()
                installed[str(path)] = digest(path)
    if packages["clarabel"] != "0.11.1":
        raise ValueError("qualification requires installed stock CLARABEL 0.11.1")
    if (ROOT / "ipopt.opt").exists():
        raise ValueError("ambient ipopt.opt would alter frozen settings")
    import cyipopt
    return dict(commit=git("rev-parse", "HEAD"), clean=not bool(git("status", "--porcelain")),
                sources={p: digest(ROOT / p) for p in sorted(sources)},
                packages=packages, installed=installed,
                ipopt_library_version=list(cyipopt.IPOPT_VERSION),
                thread_environment={key: os.environ.get(key) for key in THREAD_KEYS},
                protocol_sha256=digest(HERE / "QUALIFICATION_PROTOCOL.md"),
                historical_pins=verify_pins() or PINS)


def frozen_binding():
    prepared = e3.verified_inputs()
    from experiments.case118_tracy_2021.prepare import SOURCE
    from experiments.case118_annual_hierarchy.pglib_case import SOURCE_CASE_PATH
    return serializable(dict(context=context(), limits=LIMITS,
                             raw_inputs={str(p.relative_to(ROOT)): digest(p) for p in
                                 (SOURCE, SOURCE_CASE_PATH, ROOT / "experiments/case118_tracy_2021/stage_a/manifest.json")},
                             calls=[call_binding(c, kwargs_for_call(c, prepared)) for c in calls()]))


def monitoring_preflight():
    if _child_rss_mib(os.getpid()) is None:
        raise RuntimeError("RSS/process permission unavailable; obtain it before execution")
    if sys.platform != "darwin":
        raise RuntimeError("qualification thermal preflight is currently configured for this macOS host")
    thermal = subprocess.check_output(["pmset", "-g", "therm"], text=True)
    battery = subprocess.check_output(["pmset", "-g", "batt"], text=True)
    if "AC Power" not in battery:
        raise RuntimeError("qualification requires AC power; no new call launched")
    return dict(utc=utc(), thermal=thermal, battery=battery)


def replay_fix_changes(original, continued):
    """Allow only the reviewed runner/test transition, never numerical changes."""
    if (original["commit"] != REPLAY_FIX_ORIGIN or original["clean"] is not True or
        continued["clean"] is not True or len(continued["commit"]) != 40 or
        any(c not in "0123456789abcdef" for c in continued["commit"]) or
        continued["commit"] == original["commit"]):
        raise ValueError("replay-fix transition requires its original and new clean commits")
    if ({k: v for k, v in original.items() if k not in {"commit", "sources"}} !=
        {k: v for k, v in continued.items() if k not in {"commit", "sources"}}):
        raise ValueError("replay-fix transition changed numerical environment/protocol")
    before, after = original["sources"], continued["sources"]
    if set(before) != set(after):
        raise ValueError("replay-fix transition added or removed bound sources")
    changed = {p: dict(before=before[p], after=after[p]) for p in before if before[p] != after[p]}
    if set(changed) != REPLAY_FIX_SOURCES:
        raise ValueError("replay-fix transition permits only the runner and its test")
    return changed


def preserved_replay_failure(root):
    """Hash the original completed call and failed invocation without rewriting."""
    directory = root / "call-001"
    if {p.name for p in directory.iterdir()} != REPLAY_FIX_FILES:
        raise ValueError("replay-fix call-001 evidence set differs")
    invocation = root / "invocations" / REPLAY_FIX_INVOCATION
    if read(invocation / "finish.json")["outcome"] != "failure":
        raise ValueError("replay-fix origin is not the recorded failed invocation")
    paths = sorted(directory.iterdir()) + [invocation / "start.json", invocation / "finish.json"]
    return {str(p.relative_to(root)): digest(p) for p in paths}


def replay_fix_transition(root, binding, candidate=None):
    """Validate the sole additive transition; reading it never adopts a change."""
    path = root / "replay-fix-transition.json"
    transition = candidate if candidate is not None else (read(path) if path.exists() else None)
    if transition is None:
        return None
    if (transition["kind"] != "replay-fix-v1" or transition["first_call"] != 2 or
        digest(root / "binding.json") != REPLAY_FIX_BINDING_SHA256 or
        transition["original_binding"] != reference(root / "binding.json", root) or
        transition["protocol"] != reference(root / "protocol.json", root) or
        read(root / "protocol.json") != dict(protocol=LIMITS)):
        raise ValueError("replay-fix transition origin/protocol mismatch")
    if transition["source_changes"] != replay_fix_changes(binding["context"], transition["context"]):
        raise ValueError("replay-fix transition source hashes mismatch")
    if transition["preserved"] != preserved_replay_failure(root):
        raise ValueError("replay-fix preserved evidence changed")
    return transition


def binding_for_call(binding, transition, call_id):
    if transition is not None and call_id >= transition["first_call"]:
        return dict(binding, context=transition["context"])
    return binding


def request_for_call(root, call_id, wall, transition):
    request = dict(call_id=call_id, role="primary", protocol=reference(root / "protocol.json", root), wall_seconds=wall)
    if transition is not None and call_id >= transition["first_call"]:
        request["source_transition"] = reference(root / "replay-fix-transition.json", root)
    return request


def prepare_replay_fix_transition(root, original, continued):
    """Construct, but do not publish, the one-time call-002 continuation."""
    if sorted(p.name for p in root.glob("call-*")) != ["call-001"]:
        raise ValueError("replay-fix adoption requires exactly the completed first call")
    if {k: v for k, v in original.items() if k != "context"} != {k: v for k, v in continued.items() if k != "context"}:
        raise ValueError("replay-fix transition changed frozen inputs/settings/budgets")
    candidate = dict(kind="replay-fix-v1", first_call=2, adopted_utc=utc(),
        original_binding=reference(root / "binding.json", root), protocol=reference(root / "protocol.json", root),
        context=continued["context"], source_changes=replay_fix_changes(original["context"], continued["context"]),
        preserved=preserved_replay_failure(root))
    replay_fix_transition(root, original, candidate)
    progress, _ = replay(root, transition=candidate)
    if progress["disposed"] != 1 or progress["accepted"] != 1 or progress["next_call"] != 2:
        raise ValueError("replay-fix origin must be independently accepted and supervised")
    return candidate


def physical_start(build, kwargs):
    """One deterministic stock start, with exact entries assigned in every arm."""
    _complete_start(build)
    masks = structural_inputs(kwargs)
    for name, mask, defining in (
        ("Pg", np.asarray(masks["pg_fixed"]),
         np.array([g.p_min_mw for g in kwargs["generators"]]) / kwargs["case"]["baseMVA"]),
        ("p_nd", np.asarray(masks["nd_fixed"]).T,
         np.zeros((len(kwargs["nondispatchable"]), kwargs["T"])))):
        var = build.variables[name]
        value = np.asarray(var.value).copy()
        if name == "Pg":
            if value.ndim > 1:
                value[mask, :] = defining[mask, None]
            else:
                value[mask] = defining[mask]
        else:
            if value.ndim == 1:
                mask = mask[:, 0]
            value[mask] = 0.
        var.value = value
    assigned = _complete_start(build)
    # Stable named physical arrays include original network/device/branch starts;
    # canonical cost auxiliaries are separately retained in x0.json.gz.
    return assigned


def worker(root, directory):
    binding = read(root / "binding.json")
    request = read(directory / "request.json")
    call = calls()[request["call_id"] - 1]
    transition = replay_fix_transition(root, binding)
    binding = binding_for_call(binding, transition, call.id)
    frozen = binding["calls"][call.id - 1]
    began, build = time.monotonic(), None
    record = dict(iteration=call.id, request=request, exception=None, optimizer_calls=0,
                  execution_context=binding["context"], mathematical_input_sha256=frozen["mathematical_input_sha256"])

    def phase(name):
        atomic_json(directory / "phase.json", dict(phase=name, utc=utc(), elapsed_seconds=time.monotonic()-began))

    try:
        # The parent publishes launch.json immediately after Popen. A hidden
        # worker command cannot run an already finalized or unrelated request.
        deadline = time.monotonic() + 5.
        while not (directory / "launch.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        launch = read(directory / "launch.json")
        if launch["pid"] != os.getpid() or (directory / "supervision.json").exists() or (root / "STOP").exists():
            raise ValueError("worker is not the live authorized launch")
        if request != request_for_call(root, call.id, request["wall_seconds"], transition):
            raise ValueError("worker request/source-transition mismatch")
        phase("prepare")
        if context() != binding["context"]:
            raise ValueError("worker source/environment differs from binding")
        kwargs = kwargs_for_call(call)
        if serializable(call_binding(call, kwargs)) != frozen:
            raise ValueError("worker numeric inputs/settings differ from binding")
        phase("construct")
        build = build_for_call(call, kwargs)
        if call.formulation == "ac":
            assigned = physical_start(build, kwargs)
            record["physical_start"] = serializable(assigned)
            atomic_immutable_json(directory / "start.json", dict(assigned_start=serializable(assigned)))

            def start_observer(value):
                atomic_gzip_json(directory / "x0.json.gz", serializable(
                    dict(iteration=call.id, **{f.name: getattr(value, f.name) for f in fields(value)})))

            def native_observer(value):
                record["native"] = serializable(value)

            phase("solve")
            record["optimizer_calls"] = 1
            outcome = _solve_ac_with_verified_x0(build, None, start_observer=start_observer,
                         native_observer=native_observer, solver_options=solver_options("ac"))
            record["exception"] = outcome.exception
        else:
            phase("solve")
            record["optimizer_calls"] = 1
            try:
                build.solve(warm_start=False, verbose=True, **solver_options(call.formulation))
            except cp.error.SolverError as exc:
                # A native rejection can raise during public inversion. Retain
                # that diagnostic; do not invent a primal or a replacement call.
                record["exception"] = f"{type(exc).__name__}: {exc}"
            finally:
                record["convex_diagnostics"] = convergence_diagnostics(build)
                record["native"] = record["convex_diagnostics"].get("native_info") or {}
                solver = build.prob._solver_cache.get("CLARABEL")
                if solver is not None:
                    native = solver.get_solution()
                    record["native"].update({n: serializable(getattr(native, n)) for n in
                                            ("x", "s", "z", "obj_val", "obj_val_dual")})
        record["preparation_evidence"] = evidence_record(build.preparation_evidence)
        if build.preparation_evidence is not None:
            record["native"] = serializable(build.preparation_evidence.native)
        phase("extract_and_audit")
        result = extract_results(build)
        if call.source == "case9" and call.T == 1:
            result = single_step_result(result)
        record["result"] = result
        record["boundary_soc_mwh"] = (None if result.get("soc") is None else
            np.vstack(([s.initial_soc for s in kwargs["storage"]], result["soc"])))
        record["named_costs"] = {k: float(v.value) if v.value is not None else None
                                 for k, v in build.expressions.items() if k.endswith("_cost")}
        captured = read(directory / "x0.json.gz") if (directory / "x0.json.gz").exists() else None
        # Audit the exact retained representation, including explicit nonfinite
        # diagnostics, so no-primal failure schemas replay identically.
        record["audit"] = audit_record(call, kwargs, build, serializable(record), captured)
        if context() != binding["context"]:
            raise ValueError("source/environment changed during worker")
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
        if build is not None:
            record["preparation_evidence"] = evidence_record(build.preparation_evidence)
            if build.preparation_evidence is not None:
                record["native"] = serializable(build.preparation_evidence.native)
        record["audit"] = dict(accepted=False, reason=record["exception"])
    native_status = record.get("native", {}).get("status")
    native_rejection = native_status is not None and native_status != (0 if call.formulation == "ac" else "Solved")
    record["classification"] = ("rejected" if native_rejection else "exception") if record["exception"] else (
        "accepted" if record["audit"]["accepted"] else "rejected")
    phase("archive")
    atomic_gzip_json(directory / "result.json.gz", serializable(record))
    names = ["request.json", "result.json.gz"]
    names += [n for n in ("start.json", "x0.json.gz") if (directory / n).exists()]
    atomic_immutable_json(directory / "completion.json", dict(
        classification=record["classification"], artifacts={n: digest(directory / n) for n in names}))
    phase("complete")
    return record["classification"]


def replayable_audit(audit):
    """Compare physical evidence, not statistics from the original solve.

    A replay build is deliberately unsolved, so it cannot reproduce the live
    build's solver statistics. Keep those diagnostics in the hashed archive;
    exclude only that field from comparison, without changing either audit.
    Native convergence, physical residuals, and transformation checks still
    require exact agreement.
    """
    relaxation = audit.get("relaxation")
    if relaxation is None:
        return audit
    return dict(audit, relaxation={k: v for k, v in relaxation.items()
                                  if k != "solver_statistics"})


def independent_record(call, binding, directory, prepared):
    """Read manifests before independently recomputing original-unit gates."""
    completion = read(directory / "completion.json")
    if not {"request.json", "result.json.gz"} <= set(completion["artifacts"]):
        raise ValueError("incomplete artifact manifest")
    for name, expected in completion["artifacts"].items():
        if Path(name).name != name or digest(directory / name) != expected:
            raise ValueError("completed manifest/archive mismatch")
    record = read(directory / "result.json.gz")
    if (record["request"] != read(directory / "request.json") or
        record["classification"] != completion["classification"] or
        record["execution_context"] != binding["context"] or
        record["mathematical_input_sha256"] != binding["calls"][call.id-1]["mathematical_input_sha256"] or
        record["optimizer_calls"] not in (0, 1)):
        raise ValueError("archive/request/binding mismatch")
    if record["classification"] == "exception" or (record["exception"] and "common" not in record["audit"]):
        if record["audit"]["accepted"]:
            raise ValueError("exception cannot be accepted")
        if record["classification"] == "rejected":
            status = record.get("native", {}).get("status")
            if status is None or status == (0 if call.formulation == "ac" else "Solved"):
                raise ValueError("rejected exception lacks native rejection evidence")
        return record
    if record["optimizer_calls"] != 1:
        raise ValueError("numerical outcome lacks its single optimizer call")
    kwargs = kwargs_for_call(call, prepared)
    if serializable(call_binding(call, kwargs)) != binding["calls"][call.id-1]:
        raise ValueError("replay input/settings mismatch")
    build = build_for_call(call, kwargs) if call.formulation == "socp" else None
    captured = None
    if call.formulation == "ac":
        if not {"start.json", "x0.json.gz"} <= set(completion["artifacts"]):
            raise ValueError("AC outcome lacks complete start evidence")
        captured = read(directory / "x0.json.gz")
        verify_x0(read(directory / "start.json"), captured)
        expected = physical_start(build_for_call(call, kwargs), kwargs)
        if serializable(expected) != record["physical_start"] or record["physical_start"] != read(directory / "start.json")["assigned_start"]:
            raise ValueError("physical start differs from deterministic input start")
    audit = serializable(audit_record(call, kwargs, build, record, captured))
    if replayable_audit(audit) != replayable_audit(record["audit"]) or audit["accepted"] != (record["classification"] == "accepted"):
        raise ValueError("independent audit/archive mismatch")
    expected_soc = (None if record["result"].get("soc") is None else
                   np.vstack(([s.initial_soc for s in kwargs["storage"]], record["result"]["soc"])))
    if not np.array_equal(expected_soc, record["boundary_soc_mwh"]):
        raise ValueError("storage boundary archive mismatch")
    return record


def resource_evidence(directory, supervision):
    """Cross-check fresh sampler records; PID existence alone is not health."""
    launch = read(directory / "launch.json") if (directory / "launch.json").exists() else None
    path = directory / "resources.jsonl"
    samples = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    previous, peak = -1., 0.
    for sample in samples:
        elapsed, rss = sample["elapsed_seconds"], sample["rss_mib"]
        if not (math.isfinite(elapsed) and elapsed >= previous and math.isfinite(rss) and rss >= 0):
            raise ValueError("invalid resource sample")
        previous, peak = elapsed, max(peak, rss)
    if len(samples) != supervision["samples"] or peak != supervision["peak_sampled_rss_mib"]:
        raise ValueError("resource journal/supervision mismatch")
    if samples and (launch is None or not (directory / "worker.log").exists()):
        raise ValueError("resource evidence lacks worker launch/log")
    return bool(samples and launch and peak <= LIMITS["rss_mib"])


def replay(root, *, auditor=None, transition=None):
    """Report unfinished tails, but never accept them or offer a next call."""
    binding = read(root / "binding.json")
    if binding["limits"] != LIMITS or len(binding["calls"]) != 25:
        raise ValueError("qualification binding matrix/limits mismatch")
    transition = replay_fix_transition(root, binding, transition)
    active = binding_for_call(binding, transition, 2)
    if auditor is None and context() != active["context"]:
        raise ValueError("independent replay requires the bound source/environment")
    dirs = sorted(root.glob("call-*"))
    if len(dirs) > 25:
        raise ValueError("launch ceiling exceeded")
    prepared = None if auditor is not None else e3.verified_inputs()
    attempts, records, used = [], {}, 0.
    for i, directory in enumerate(dirs):
        call = calls()[i]
        if directory.name != f"call-{call.id:03d}":
            raise ValueError("noncontiguous calls; no retries permitted")
        if not (directory / "supervision.json").exists():
            if i != len(dirs)-1:
                raise ValueError("unsupervised call precedes later calls")
            attempts.append(dict(call_id=call.id, classification="unfinished", accepted=False))
            break
        request = read(directory / "request.json")
        wall = request["wall_seconds"]
        if request != request_for_call(root, call.id, wall, transition) or isinstance(wall, bool) or not 0 < wall <= min(180., 4500.-used):
            raise ValueError("invalid request/call/budget")
        if read(root / "protocol.json") != dict(protocol=LIMITS):
            raise ValueError("protocol reference mismatch")
        sup = read(directory / "supervision.json")
        elapsed = sup["wall_seconds"]
        if isinstance(elapsed, bool) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid supervision effort")
        used += elapsed
        classification = sup["classification"]
        if classification not in {"exited", "wall_limit", "rss_limit", "interrupted", "supervisor_failure"}:
            raise ValueError("unknown supervision disposition")
        resources_ok = resource_evidence(directory, sup)
        accepted = False
        if (directory / "completion.json").exists():
            record = (auditor(call, directory) if auditor is not None else
                      independent_record(call, binding_for_call(binding, transition, call.id), directory, prepared))
            accepted = (classification == "exited" and sup["returncode"] == 0 and
                        resources_ok and elapsed <= wall and record["audit"]["accepted"])
            # Pair gates cannot use even an accepted worker archive when its
            # supervision stopped it or observed a resource ceiling.
            records[call.id] = dict(record, supervised_accepted=bool(accepted))
        elif classification == "exited" and sup["returncode"] == 0:
            raise ValueError("successful worker exit lacks completion")
        attempts.append(dict(call_id=call.id, classification=classification, accepted=bool(accepted),
                             execution_commit=binding_for_call(binding, transition, call.id)["context"]["commit"],
                             wall_seconds=elapsed, peak_sampled_rss_mib=sup["peak_sampled_rss_mib"],
                             resource_evidence_available=resources_ok, wall_ceiling_exceeded=elapsed > wall))
    unfinished = bool(attempts and attempts[-1]["classification"] == "unfinished")
    disposed = sum(a["classification"] != "unfinished" for a in attempts)
    return dict(attempts=attempts, launches=len(dirs), disposed=disposed,
                accepted=sum(a["accepted"] for a in attempts), unresolved=disposed-sum(a["accepted"] for a in attempts),
                worker_seconds=used, finished=disposed == 25,
                next_call=None if unfinished or disposed == 25 or used >= 4500 else disposed+1), records


def qualification_report(binding, progress, records, transition=None):
    pairs = {}
    for left, right in ((6, 7), (8, 9), (10, 11), (12, 13), (14, 15), (16, 17),
                        (18, 19), (18, 20), (21, 22), (21, 23), (24, 25)):
        pairs[f"{left:02d}-{right:02d}"] = (pair_check(records[left], records[right]) if left in records and right in records
                                          else dict(available=False, passed=False, reason="unavailable call"))
    historical = {}
    prepared = e3.verified_inputs()
    for call in calls()[:5]:
        try:
            kwargs = kwargs_for_call(call, prepared)
            control = historical_control(call, kwargs, build_for_call(call, kwargs) if call.id < 5 else None)
            historical[str(call.id)] = control
            if call.id < 5:
                pairs[f"historical-{call.id:02d}"] = (pair_check(control, records[call.id]) if call.id in records
                    else dict(available=False, passed=False, reason="unavailable call"))
        except (OSError, ValueError, KeyError, RuntimeError, AssertionError) as exc:
            historical[str(call.id)] = dict(available=False, reason=f"{type(exc).__name__}: {exc}")
            if call.id < 5:
                pairs[f"historical-{call.id:02d}"] = dict(available=False, passed=False, reason="retained comparator unavailable")
    dispositions = {}
    for formulation in ("socp", "lossy_dc", "singlenode_dc", "ac"):
        ids = {c.id for c in calls() if c.formulation == formulation}
        own = [a for a in progress["attempts"] if a["call_id"] in ids]
        checks = [v for key, v in pairs.items() if (key.startswith("historical") and formulation == "socp") or
                  (not key.startswith("historical") and int(key.split("-")[1]) in ids)]
        if any(not a["accepted"] and a["classification"] != "unfinished" for a in own) or any(v.get("available") and not v["passed"] for v in checks):
            disposition = "not_qualified"
        elif len(own) < len(ids) or any(a["classification"] == "unfinished" for a in own) or any(not v.get("available") for v in checks):
            disposition = "incomplete"
        else:
            disposition = "qualified"
        dispositions[formulation] = disposition
    outcomes = {str(i): dict(classification=r["classification"], exception=r["exception"],
                            execution_commit=r["execution_context"]["commit"],
                            native={k: v for k, v in r.get("native", {}).items() if k not in
                                    {"x", "s", "z", "g", "mult_g", "mult_x_L", "mult_x_U"}},
                            audit=r["audit"], preparation_checks=(r.get("preparation_evidence") or {}).get("checks"))
                for i, r in records.items()}
    return serializable(dict(progress=progress, pairs=pairs, historical=historical, qualification=dispositions,
                             outcomes=outcomes, execution_commit=binding["context"]["commit"],
                             continuation_commit=None if transition is None else transition["context"]["commit"],
                             source_transition=transition,
                             note="Numerical rejection/timeout is not proof of infeasibility; AC comparison is local-start only. No default or speedup claim."))


def run(commit, *, resume=False, acknowledge_stop=False, adopt_replay_fix=False):
    binding = frozen_binding()
    if len(commit or "") != 40 or not binding["context"]["clean"] or binding["context"]["commit"] != commit:
        raise ValueError("reviewed clean full execution commit required")
    if any(binding["context"]["thread_environment"][key] != "1" for key in THREAD_KEYS):
        raise ValueError("single-thread environment must precede process start")
    telemetry = monitoring_preflight()
    if acknowledge_stop and not resume:
        raise ValueError("STOP acknowledgement requires explicit resume")
    if adopt_replay_fix and not resume:
        raise ValueError("replay-fix adoption requires explicit resume")
    if not resume:
        OUTPUT.mkdir(parents=True, exist_ok=False)
        atomic_immutable_json(OUTPUT / "binding.json", binding)
        atomic_immutable_json(OUTPUT / "protocol.json", dict(protocol=LIMITS))
    with (OUTPUT / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original = read(OUTPUT / "binding.json")
        transition = replay_fix_transition(OUTPUT, original)
        if adopt_replay_fix and transition is None:
            transition = prepare_replay_fix_transition(OUTPUT, original, binding)
            atomic_immutable_json(OUTPUT / "replay-fix-transition.json", transition)
        if binding_for_call(original, transition, 2) != binding:
            raise ValueError("resume binding differs; explicit replay-fix adoption required")
        if acknowledge_stop and (OUTPUT / "STOP").exists():
            target = OUTPUT / "stops" / f"stop-{time.time_ns()}.json"
            target.parent.mkdir(exist_ok=True)
            (OUTPUT / "STOP").rename(target)
        invocation = OUTPUT / "invocations" / f"invocation-{time.time_ns()}"
        atomic_immutable_json(invocation / "start.json", dict(utc=utc(), resume=resume, telemetry=telemetry,
            execution_context=binding["context"], source_transition=None if transition is None else
            reference(OUTPUT / "replay-fix-transition.json", OUTPUT)))
        outcome = "failure"
        try:
            while True:
                if context() != binding["context"]:
                    raise ValueError("source/environment changed")
                progress, records = replay(OUTPUT)
                atomic_json(OUTPUT / "progress.json", progress)
                if progress["finished"]:
                    outcome = "matrix_complete"
                    break
                if (OUTPUT / "STOP").exists():
                    outcome = "operator_stop"
                    break
                if progress["next_call"] is None:
                    outcome = "unfinished" if any(a["classification"] == "unfinished" for a in progress["attempts"]) else "budget_exhausted"
                    break
                monitoring_preflight()
                call_id = progress["next_call"]
                directory = OUTPUT / f"call-{call_id:03d}"
                directory.mkdir()
                request = request_for_call(OUTPUT, call_id, min(180., 4500.-progress["worker_seconds"]), transition)
                atomic_immutable_json(directory / "request.json", request)
                sup = supervise([sys.executable, "-m", MODULE, "--worker", directory.name], directory, OUTPUT, request)
                if sup["classification"] in {"interrupted", "supervisor_failure", "rss_limit"} or (
                    sup["classification"] == "exited" and sup["returncode"] != 0):
                    outcome = sup["classification"]
                    break
                if (directory / "completion.json").exists() and read(directory / "completion.json")["classification"] == "exception":
                    outcome = "worker_exception"
                    break
            progress, records = replay(OUTPUT)
            atomic_json(OUTPUT / "progress.json", progress)
            atomic_json(OUTPUT / "report.json", qualification_report(original, progress, records, transition))
        except KeyboardInterrupt:
            outcome = "operator_stop"
        finally:
            atomic_immutable_json(invocation / "finish.json", dict(utc=utc(), outcome=outcome))
    return dict(outcome=outcome, output=str(OUTPUT))


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--commit")
    parser.add_argument("--authorize-execution", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--adopt-replay-fix", action="store_true",
                        help="explicit one-time metadata-fix context transition for remaining calls")
    parser.add_argument("--acknowledge-stop", action="store_true")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.adopt_replay_fix and (not args.resume or not args.authorize_execution or
                                args.worker or args.status or args.stop or args.preflight):
        parser.error("replay-fix adoption requires --authorize-execution --resume")
    if args.worker:
        directory = (OUTPUT / args.worker).resolve()
        if directory.parent != OUTPUT.resolve() or not directory.name.startswith("call-"):
            parser.error("worker path outside qualification")
        worker(OUTPUT, directory)
    elif args.preflight:
        result = frozen_binding()
        result["telemetry"] = monitoring_preflight()
        print(json.dumps(result, indent=2, allow_nan=False))
    elif args.status:
        progress, records = replay(OUTPUT)
        binding = read(OUTPUT / "binding.json")
        print(json.dumps(qualification_report(binding, progress, records, replay_fix_transition(OUTPUT, binding)), indent=2, allow_nan=False))
    elif args.stop:
        if not (OUTPUT / "binding.json").exists():
            parser.error("no qualification run to stop")
        atomic_immutable_json(OUTPUT / "STOP", dict(utc=utc(), reason="operator stop"))
    else:
        if not args.authorize_execution:
            parser.error("execution requires separate owner authorization and --authorize-execution")
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, interrupted)
        print(json.dumps(run(args.commit, resume=args.resume, acknowledge_stop=args.acknowledge_stop,
                             adopt_replay_fix=args.adopt_replay_fix), indent=2))


if __name__ == "__main__":
    main()
