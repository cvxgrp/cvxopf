"""Fresh baseline/prepared E1 regression; see E1_REGRESSION_PLAN.md.

Import and --preflight never solve or create run artifacts. Execution requires
an authorized clean commit; --status independently rechecks retained primals.
"""
# ruff: noqa: E402 -- numerical imports follow CLI thread limits.

import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
from types import SimpleNamespace

THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
if __name__ == "__main__":
    for key in THREAD_KEYS:
        os.environ[key] = "1"

import clarabel
import numpy as np
from scipy import sparse
from cvxopf import extract_results
from cvxopf._convex_preparation import substitute, joint_scales, restore
from cvxopf._numerical_preparation import resolve_fixed_map, prepared_options
from experiments.ac_cost_coordinates.run import monitoring
from experiments.numerical_preparation import run_qualification as q
from experiments.numerical_preparation.audit import evidence_record, serializable
from experiments.m11_socp.e2_compare import context as base_context
from tests.socp_reference_comparison import ASSEMBLIES, FIXTURES, POLICIES, compare, construct
from tests.socp_reference_cases import cases, input_hash, policy, SETTINGS

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "results/e1_regression_001"
MODULE = "experiments.m11_socp.e1_compare"
LIMITS = dict(max_launches=18, wall_seconds=30., rss_mib=16384.,
              total_worker_seconds=540., poll_seconds=.1)
SOLVER_SETTINGS = dict(SETTINGS, max_threads=1, direct_solve_method="qdldl", warm_start=False, verbose=False)


def arms():
    return [dict(id=i, case=name, assembly=assembly, treatment=treatment)
            for i, (name, assembly, treatment) in enumerate(
                ((n, a, t) for n in cases() for a in ASSEMBLIES for t in POLICIES), 1)]


def context():
    """Public-only provenance; no ignored Tracy data or reference regeneration."""
    value = base_context()
    paths = [Path(__file__), HERE / "E1_REGRESSION_PLAN.md",
             ROOT / "tests/socp_reference_comparison.py", ROOT / "tests/socp_reference_audit.py",
             ROOT / "experiments/numerical_preparation/run_qualification.py",
             ROOT / "experiments/numerical_preparation/audit.py",
             ROOT / "experiments/case118_tracy_2021/run_e3.py",
             ROOT / "experiments/ac_cost_coordinates/run.py"]
    paths += sorted((ROOT / "scripts/socp_reference").rglob("*.jl"))
    paths += [ROOT / "scripts/socp_reference/reference_env/Manifest.toml"]
    paths += [FIXTURES / f"{name}_socp_powermodels_reference.json" for name in cases()]
    value["source_sha256"].update({str(p.relative_to(ROOT)): q.digest(p) for p in paths})
    value["thread_environment"] = {k: os.environ.get(k) for k in THREAD_KEYS}
    from importlib.metadata import distribution
    installed = distribution("clarabel")
    if installed.files is None:
        raise ValueError("CLARABEL distribution file provenance unavailable")
    value["clarabel_native_sha256"] = {str(installed.locate_file(p)): q.digest(
        installed.locate_file(p)) for p in installed.files
        if str(p).endswith((".so", ".dylib"))}
    if not value["clarabel_native_sha256"]:
        raise ValueError("CLARABEL native binary provenance unavailable")
    return value


def row_binding(arm):
    case, reference, build = construct(arm["case"], arm["assembly"], arm["treatment"])
    data, _, inverse = build.prob.get_problem_data("CLARABEL", canon_backend=build.canonicalization_backend)
    # Bind exact original canonical arrays, not process-local CVXPY identities.
    signature = {}
    for key in ("P", "A"):
        matrix = data.get(key)
        signature[key] = None if matrix is None else dict(shape=list(matrix.shape),
            data=hashlib.sha256(matrix.data.tobytes()).hexdigest(),
            indices=hashlib.sha256(matrix.indices.tobytes()).hexdigest(),
            indptr=hashlib.sha256(matrix.indptr.tobytes()).hexdigest())
    signature.update({key: hashlib.sha256(data[key].tobytes()).hexdigest() for key in ("b", "c")})
    signature.update(zero=data["dims"].zero, nonneg=data["dims"].nonneg,
                     soc=data["dims"].soc, objective_offset=float(inverse[-1]["offset"]))
    return dict(arm=arm, input_sha256=input_hash(case), physical_policy=policy(arm["case"]),
                numerical_preparation=asdict(build.numerical_preparation), canonical=signature,
                scalar_variables=build.prob.size_metrics.num_scalar_variables,
                reference_objective=reference["objective"],
                reference_sha256=q.digest(FIXTURES / f"{arm['case']}_socp_powermodels_reference.json"))


def protocol():
    return dict(protocol=LIMITS, settings=SOLVER_SETTINGS,
                plan_sha256=q.digest(HERE / "E1_REGRESSION_PLAN.md"))


def frozen_binding():
    resolved = clarabel.DefaultSettings()
    for key, value in SOLVER_SETTINGS.items():
        if key != "warm_start":
            setattr(resolved, key, value)
    return dict(context=context(), rows=[row_binding(a) for a in arms()],
                resolved_settings={k: getattr(resolved, k) for k in dir(resolved)
                                   if not k.startswith("_") and not callable(getattr(resolved, k))})


def verify_binding(root):
    binding = q.read(root / "binding.json")
    if binding != frozen_binding() or q.read(root / "protocol.json") != protocol():
        raise ValueError("E1 source/environment/input/settings binding changed")
    return binding


def request(root, number, wall):
    return dict(call_id=number, role="primary", wall_seconds=wall,
                protocol=q.reference(root / "protocol.json", root))


def native_record(build, row):
    evidence = evidence_record(build.preparation_evidence)
    if row["arm"]["treatment"] == "prepared":
        if evidence is None:
            raise ValueError("prepared E1 solve lacks preparation evidence")
        return evidence["native"], evidence
    if evidence is not None:
        raise ValueError("baseline E1 solve unexpectedly prepared")
    solver = build.prob._solver_cache.get("CLARABEL")
    if solver is None:
        raise ValueError("baseline E1 solve lacks native evidence")
    raw = solver.get_solution()
    native = {k: getattr(raw, k) for k in (
        "x", "s", "z", "obj_val", "obj_val_dual", "r_prim", "r_dual", "iterations", "solve_time")}
    native.update(status=str(raw.status), cvxpy_objective_offset=row["canonical"]["objective_offset"])
    return serializable(native), None


def canonical_problem(build):
    """Rebuild the inverse chain without optimization or process-local IDs."""
    _, options = prepared_options(build, SOLVER_SETTINGS)
    chain = build.prob._construct_chain(solver="CLARABEL", canon_backend=build.canonicalization_backend,
                                        ignore_dpp=True, solver_opts=options)
    data, inverse = chain.apply(build.prob)
    data = dict(data, P=data.get("P", sparse.csc_array((data["c"].size,) * 2)))
    constraints = inverse[-1]["eq_constr"] + inverse[-1]["other_constr"]
    mapping = resolve_fixed_map(build._exact_boxes, inverse[:-1], inverse[-2], constraints,
                                data["c"].size, data["b"].size)
    reduced, offset = substitute(data, mapping)
    D, R = (joint_scales(reduced) if build.numerical_preparation.canonical_scaling == "joint5"
            else (np.ones(mapping.free.size), np.ones(mapping.kept.size)))
    return data, chain, inverse, mapping, D, R, offset


def restored_result(build, native, evidence):
    """Check maps/scales against the fresh graph and unpack retained native x."""
    data, chain, inverse, mapping, D, R, offset = canonical_problem(build)
    assert native["cvxpy_objective_offset"] == float(inverse[-1]["offset"])
    raw = SimpleNamespace(**native)
    restored, checks = restore(data, mapping, raw, D, R, offset)
    assert checks["objective_reconstruction"] <= max(1e-7, 1e-10*abs(restored.obj_val))
    if build.numerical_preparation.enabled:
        assert evidence is not None and evidence["native"] == native
        expected = dict(full_size=mapping.full_size, row_count=mapping.row_count,
                        fixed=mapping.fixed.tolist(), values=mapping.values.tolist(), dropped=mapping.dropped.tolist())
        assert evidence["coordinates"] == expected
        assert evidence["variable_scale"] == D.tolist() and evidence["row_scale"] == R.tolist()
        assert evidence["objective_offset"] == offset
        assert evidence["checks"] == serializable(checks)
    else:
        assert evidence is None
    build.prob.unpack_results(restored, chain, inverse)
    return serializable(extract_results(build)), offset


def assess(row, result, native, evidence):
    """Non-solving independent primal/cost checks for live and archived records."""
    if native.get("status") != "Solved":
        return dict(passed=False, reason="native CLARABEL did not fully converge")
    _, _, build = construct(**{k: row["arm"][k] for k in ("assembly", "treatment")}, name=row["arm"]["case"])
    try:
        restored, offset = restored_result(build, native, evidence)
        assert restored["status"] == result["status"]
        # Same retained vectors, same fresh inverse chain: this is a mapping
        # identity check, not a new engineering-feasibility tolerance.
        for key in ("w", "W_re", "W_im", "Pg", "Qg", "branch_p_from", "branch_q_from", "branch_p_to", "branch_q_to"):
            np.testing.assert_allclose(restored[key], result[key], rtol=0, atol=1e-10)
        checked = compare(row["arm"]["case"], row["arm"]["assembly"], build, result)
        restored_cost = native["obj_val"] + offset + native["cvxpy_objective_offset"]
        error = abs(restored_cost-checked["objective"])
        limit = max(1e-7, 1e-10*abs(checked["objective"]))
        assert np.isfinite(restored_cost) and error <= limit
        return dict(passed=True, comparison=checked, restored_native_objective=restored_cost,
                    native_objective_error=error, native_objective_limit=limit)
    except (AssertionError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return dict(passed=False, reason=f"independent E1 check failed: {exc}")


def worker(root, number):
    directory = root / f"call-{number:03d}"
    record = dict(iteration=number, call_id=number, optimizer_calls=0, exception=None)
    began = time.monotonic()

    def phase(name):
        q.atomic_json(directory / "phase.json", dict(phase=name, utc=q.utc(), elapsed_seconds=time.monotonic()-began))

    try:
        deadline = time.monotonic()+5
        while not (directory / "launch.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        req = q.read(directory / "request.json")
        if (q.read(directory / "launch.json")["pid"] != os.getpid() or
                (directory / "supervision.json").exists() or (root / "STOP").exists() or
                req != request(root, number, req["wall_seconds"]) or
                isinstance(req["wall_seconds"], bool) or
                not 0 < req["wall_seconds"] <= LIMITS["wall_seconds"]):
            raise ValueError("invalid E1 worker launch/request")
        binding = verify_binding(root)
        row = binding["rows"][number-1]
        record.update(row=row, request=req)
        phase("construct")
        arm = row["arm"]
        _, _, build = construct(arm["case"], arm["assembly"], arm["treatment"])
        phase("solve")
        record["optimizer_calls"] = 1
        try:
            build.solve(**SOLVER_SETTINGS)
        finally:
            record["native"], record["preparation_evidence"] = native_record(build, row)
            q.atomic_gzip_json(directory / "native.json.gz", dict(iteration=number, native=record["native"]))
        phase("audit")
        record["result"] = serializable(extract_results(build))
        record["audit"] = assess(row, record["result"], record["native"], record["preparation_evidence"])
        verify_binding(root)
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    native_rejection = record.get("native", {}).get("status") not in (None, "Solved")
    record["classification"] = ("rejected" if native_rejection else "exception") if record["exception"] else (
        "accepted" if record.get("audit", {}).get("passed") else "rejected")
    phase("archive")
    q.atomic_gzip_json(directory / "result.json.gz", serializable(record))
    names = ["request.json", "result.json.gz"] + (["native.json.gz"] if "native" in record else [])
    q.atomic_immutable_json(directory / "completion.json", dict(
        artifacts={n: q.digest(directory / n) for n in names}))
    phase("complete")


def status(root):
    binding = verify_binding(root)
    directories = sorted(root.glob("call-*"))
    if len(directories) > LIMITS["max_launches"]:
        raise ValueError("E1 launch ceiling exceeded")
    rows, used = [], 0.
    counts = dict(finalized=0, accepted=0, rejected=0, completion_manifests=0, result_archives=0, native_archives=0)
    for number, directory in enumerate(directories, 1):
        if directory.name != f"call-{number:03d}":
            raise ValueError("noncontiguous E1 attempts")
        row = dict(arm=binding["rows"][number-1]["arm"], classification="unfinished", accepted=False)
        rows.append(row)
        req = q.read(directory / "request.json")
        wall = req["wall_seconds"]
        if (req != request(root, number, wall) or isinstance(wall, bool) or
                not 0 < wall <= min(LIMITS["wall_seconds"], LIMITS["total_worker_seconds"]-used)):
            raise ValueError("E1 request/budget mismatch")
        for field, name in (("completion_manifests", "completion.json"), ("result_archives", "result.json.gz"),
                            ("native_archives", "native.json.gz")):
            counts[field] += int((directory / name).exists())
        if not (directory / "supervision.json").exists():
            if number != len(directories):
                raise ValueError("cannot advance unfinished E1 attempt")
            continue
        sup = q.read(directory / "supervision.json")
        elapsed = sup["wall_seconds"]
        if (sup["classification"] not in {"exited", "wall_limit", "rss_limit", "interrupted", "supervisor_failure"}
                or isinstance(elapsed, bool) or not math.isfinite(elapsed) or elapsed < 0):
            raise ValueError("invalid E1 worker effort")
        resources = q.resource_evidence(directory, sup)
        used += elapsed
        counts["finalized"] += 1
        row.update(classification=sup["classification"], worker_seconds=elapsed,
                   peak_sampled_rss_mib=sup["peak_sampled_rss_mib"])
        if not (directory / "completion.json").exists():
            if sup["classification"] == "exited" and sup["returncode"] == 0:
                raise ValueError("successful E1 worker lacks completion")
            continue
        completion, record = q.read(directory / "completion.json"), q.read(directory / "result.json.gz")
        names = {"request.json", "result.json.gz"} | ({"native.json.gz"} if "native" in record else set())
        if completion["artifacts"] != {n: q.digest(directory / n) for n in names}:
            raise ValueError("E1 manifest/archive mismatch")
        if (record["call_id"] != number or record.get("row", binding["rows"][number-1]) != binding["rows"][number-1] or
                record.get("request", req) != req or record["optimizer_calls"] not in (0, 1)):
            raise ValueError("E1 record/binding mismatch")
        if "native" in record and q.read(directory / "native.json.gz") != dict(iteration=number, native=record["native"]):
            raise ValueError("E1 native archives disagree")
        if "audit" in record and assess(record["row"], record["result"], record["native"],
                record["preparation_evidence"]) != record["audit"]:
            raise ValueError("E1 independent audit replay disagrees")
        native_rejection = record.get("native", {}).get("status") not in (None, "Solved")
        classification = ("rejected" if native_rejection else "exception") if record["exception"] else (
            "accepted" if record.get("audit", {}).get("passed") else "rejected")
        if record["classification"] != classification:
            raise ValueError("E1 classification disagrees with retained evidence")
        row.update(worker_classification=classification, audit=record.get("audit"), exception=record["exception"])
        row["accepted"] = bool(classification == "accepted" and record["optimizer_calls"] == 1 and resources and
            sup["classification"] == "exited" and sup["returncode"] == 0 and elapsed <= wall and used <= LIMITS["total_worker_seconds"])
        counts["accepted"] += int(row["accepted"])
        counts["rejected"] += int(classification == "rejected")
    return dict(expected_cases=18, launches=len(directories), **counts, attempts=rows,
                worker_seconds=used, remaining_worker_seconds=max(0., LIMITS["total_worker_seconds"]-used))


def run(root, commit):
    if root.exists():
        raise FileExistsError("E1 output must be a fresh directory")
    binding = frozen_binding()
    if binding["context"]["worktree_status"] or binding["context"]["head"] != commit or len(commit) != 40:
        raise ValueError("reviewed clean full commit required before E1 execution")
    if any(binding["context"]["thread_environment"][k] != "1" for k in THREAD_KEYS):
        raise ValueError("single-thread numerical environment required")
    telemetry = monitoring()
    root.mkdir(parents=True, exist_ok=False)
    q.atomic_immutable_json(root / "binding.json", binding)
    q.atomic_immutable_json(root / "protocol.json", protocol())
    q.atomic_immutable_json(root / "invocation-start.json", dict(utc=q.utc(), telemetry=telemetry))
    outcome, exception = "failure", None
    try:
        for number in range(1, 19):
            progress = status(root)
            if progress["launches"] != progress["finalized"]:
                raise ValueError("unfinished E1 attempt cannot advance")
            if (root / "STOP").exists() or progress["remaining_worker_seconds"] <= 0:
                outcome = "operator_stop" if (root / "STOP").exists() else "budget_exhausted"
                break
            telemetry = monitoring()
            directory = root / f"call-{number:03d}"
            directory.mkdir()
            q.atomic_immutable_json(directory / "telemetry-start.json", telemetry)
            req = request(root, number, min(LIMITS["wall_seconds"], progress["remaining_worker_seconds"]))
            q.atomic_immutable_json(directory / "request.json", req)
            q.supervise([sys.executable, "-m", MODULE, "--worker", str(number), "--output", str(root)], directory, root, req)
            progress = status(root)
            q.atomic_json(root / "report.json", progress)
            if not progress["attempts"][-1]["accepted"]:
                outcome = "stopped_on_outcome"
                break
        else:
            outcome = "matrix_complete"
    except KeyboardInterrupt:
        outcome = "operator_stop"
    except Exception as exc:
        exception = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        q.atomic_immutable_json(root / "invocation-finish.json", dict(utc=q.utc(), outcome=outcome, exception=exception))
    return dict(outcome=outcome, output=str(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--authorize-execution", action="store_true")
    mode.add_argument("--worker", type=int, choices=range(1, 19))
    parser.add_argument("--commit")
    args = parser.parse_args()
    if args.worker:
        worker(args.output, args.worker)
        return
    if args.preflight:
        result = frozen_binding()
    elif args.status:
        result = status(args.output)
    else:
        if not args.commit:
            parser.error("execution requires --commit <full SHA>")
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        result = run(args.output, args.commit)
    print(json.dumps(serializable(result), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
