"""Bind, execute, inspect or resume the serial eight-arm Case9 benchmark."""
# ruff: noqa: E402 -- native thread limits must precede numerical imports.

import argparse
import fcntl
import importlib.metadata
import json
import math
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from uuid import uuid4

THREADS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
if __name__ == "__main__":
    os.environ.update(dict.fromkeys(THREADS, "1"))
    os.environ.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")

from experiments.case118_annual_hierarchy.run_s4 import _child_rss_mib
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json, atomic_json, sha256_path,
)
from experiments.case118_tracy_2021.run_e3 import supervise

from . import preflight
from .benchmark_audit import assess, pair_summary
from .contract import CELLS, decode_case, require

HERE, ROOT, OUTPUTS = preflight.HERE, preflight.REPO, preflight.OUTPUTS
WORKER = "experiments.case9_matched_comparison.benchmark_worker"
SOLVERS = ("cvxopf", "pypower")
# Reverse order in two cells, balanced across both factors.
ARMS = [[cell, side] for i, cell in enumerate(CELLS)
        for side in (SOLVERS if i in (0, 3) else SOLVERS[::-1])]
RSS_MIB = 16384  # Enforced by the reused supervisor.
AUDIT_WALL_SECONDS = 30


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def output(run_id):
    require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", run_id), "invalid run ID")
    require(not OUTPUTS.is_symlink() and not (OUTPUTS / run_id).is_symlink(), "symlinked output")
    return OUTPUTS / run_id


def read(path):
    return preflight.load(path)


def ref(path, base):
    return {"path": str(path.relative_to(base)), "sha256": sha256_path(path)}


def checked(base, record):
    path = base / record["path"]
    require(sha256_path(path) == record["sha256"], f"artifact hash mismatch: {path}")
    return read(path)


def settings_from(path):
    settings = read(path)
    reference = read(HERE / "BENCHMARK_SETTINGS.json")
    require(settings.keys() == reference.keys(), "incomplete benchmark settings")
    for side in SOLVERS:
        require(settings[side].keys() == reference[side].keys(), f"incomplete {side} options")
        for key, value in settings[side].items():
            expected = reference[side][key]
            if isinstance(expected, (bool, str)) or key in {"cost_mult", "bound_relax_factor"}:
                require(value == expected and type(value) is type(expected),
                        f"unsupported benchmark option {side}.{key}")
            elif isinstance(expected, int):
                require(type(value) is int and value >= (0 if expected == 0 else 1),
                        f"invalid benchmark option {side}.{key}")
            else:
                positive(value, f"{side}.{key}")
    for key in ("wall_seconds_per_worker", "poll_seconds", "material_cost_threshold"):
        positive(settings[key], key)
    require(settings["poll_seconds"] <= 1., "RSS sampling interval must be at most one second")
    return settings


def positive(value, label):
    require(type(value) in (float, int) and math.isfinite(value) and value > 0, f"invalid {label}")


def source_paths():
    return sorted(set(preflight.bound_files()) | {
        HERE / name for name in ("benchmark.py", "benchmark_worker.py", "benchmark_audit.py",
                                 "BENCHMARK_SETTINGS.json", "BENCHMARK.md")
    } | {
        ROOT / name for name in (
            "experiments/case118_tracy_2021/run_e3.py", "experiments/case118_tracy_2021/stage_d.py",
            "experiments/case118_tracy_2021/e3.py", "experiments/case118_tracy_2021/prepare.py",
            "experiments/case118_annual_hierarchy/run_s4.py",
        )
    })


def ambient_check():
    require(not (ROOT / "ipopt.opt").exists(), "ambient ipopt.opt would change the benchmark")


def bind(root, preflight_root, settings_path, python):
    """Freeze the settings and four validated inputs, without launch authority."""
    require(not root.exists(), "binding requires a new output directory")
    ambient_check()
    paths = source_paths()
    scoped = [str(path.relative_to(ROOT)) for path in paths]
    require(not git("status", "--porcelain", "--untracked-files=all", "--", *scoped),
            "commit benchmark code and relevant inputs before binding")
    provenance = preflight.provenance(python)
    preflight_binding = preflight_root / "binding.json"
    require(read(preflight_binding)["inputs"] == provenance,
            "preflight binding differs from current source or environment; rerun solver-free preflight")
    cells, receipts = {}, {}
    for cell in CELLS:
        receipt = preflight.cell_receipt(preflight_root, cell)
        require(receipt is not None, f"missing verified preflight for {cell}")
        marker = preflight_root / cell / "passed.json"
        request = read(preflight_root / cell / "attempts" / read(marker)["attempt"] / "request.json")
        cells[cell] = {"cell": cell, "case": request["case"], "ratings": request["ratings"],
                       "start": next(p for p in request["points"] if p["name"] == "accounting")}
        receipts[cell] = ref(marker, ROOT)
    settings = settings_from(settings_path)
    binding = {
        "schema": 1, "purpose": "serial matched Case9 AC benchmark", "commit": git("rev-parse", "HEAD"),
        "settings": settings, "settings_sha256": sha256_path(settings_path),
        "sources": {name: sha256_path(path) for name, path in zip(scoped, paths, strict=True)},
        "preflight_binding": ref(preflight_binding, ROOT), "preflight_receipts": receipts,
        "environment": {**provenance, "cyipopt": importlib.metadata.version("cyipopt")},
        "arms": ARMS, "cells": cells, "rss_limit_mib": RSS_MIB, "threads": 1,
        "audit_worker_wall_seconds": AUDIT_WALL_SECONDS,
        "attempt_policy": "one launched solve per arm; unlaunched reservations may be replaced",
    }
    root.mkdir(parents=True)
    atomic_immutable_json(root / "binding.json", binding)
    atomic_immutable_json(root / "protocol.json", {"protocol": {"poll_seconds": settings["poll_seconds"]}})
    return sha256_path(root / "binding.json")


def verify_binding(root, binding):
    ambient_check()
    for name, digest in binding["sources"].items():
        require(sha256_path(ROOT / name) == digest, f"execution dependency changed: {name}")
    provenance = preflight.provenance(Path(binding["environment"]["pypower"]["executable"]))
    require({**provenance, "cyipopt": importlib.metadata.version("cyipopt")} == binding["environment"],
            "solver/runtime environment changed")
    require(checked(ROOT, binding["preflight_binding"])["inputs"] == provenance,
            "upstream preflight binding changed")
    preflight_root = (ROOT / binding["preflight_binding"]["path"]).parent
    for cell, reference in binding["preflight_receipts"].items():
        checked(ROOT, reference)
        require(preflight.cell_receipt(preflight_root, cell) is not None, f"invalid preflight receipt: {cell}")
    require(binding["arms"] == ARMS and binding["rss_limit_mib"] == RSS_MIB
            and binding["audit_worker_wall_seconds"] == AUDIT_WALL_SECONDS and binding["threads"] == 1,
            "unexpected execution policy")
    require(read(root / "protocol.json") == {"protocol": {"poll_seconds": binding["settings"]["poll_seconds"]}},
            "supervisor protocol differs from binding")
    rss = _child_rss_mib(os.getpid())
    require(rss is not None and math.isfinite(rss) and rss >= 0, "required RSS monitoring unavailable")


def arm_attempts(root, binding):
    """Derive membership from immutable requests, not from a mutable counter."""
    records = {tuple(arm): [] for arm in binding["arms"]}
    for path in sorted((root / "attempts").glob("*/request.json")):
        request = read(path)
        arm = request["cell"], request["solver"]
        require(arm in records and request["binding_sha256"] == sha256_path(root / "binding.json"),
                f"attempt does not belong to binding: {path}")
        records[arm].append(path.parent)
    return records


def consumed(directory):
    return (directory / "launch.json").exists() or (directory / "worker_started.json").exists()


def reconcile(root, records):
    """Refuse a live orphan, then conservatively finalize interrupted attempts."""
    with (root / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for attempts in records.values():
            for directory in attempts:
                manifest = directory / "supervision.json"
                if manifest.exists():
                    continue
                launched = consumed(directory)
                ceiling = read(directory / "request.json")["wall_seconds"]
                atomic_immutable_json(manifest, {
                    "classification": "supervisor_lost" if launched else "unlaunched_interruption",
                    "returncode": None, "wall_seconds": ceiling if launched else 0.,
                    "accounting": "full_ceiling" if launched else "unlaunched",
                    "peak_sampled_rss_mib": None, "ended_utc": preflight.utc(),
                })


def interpreter(binding, side):
    key = binding["environment"]
    return key["cvxopf_executable"] if side == "cvxopf" else key["pypower"]["executable"]


def raw_return(root, directory, arm):
    raw = read(directory / "return.json")
    require([raw["cell"], raw["solver"]] == list(arm)
            and raw["binding_sha256"] == sha256_path(root / "binding.json"), "native return mismatch")
    require(set(raw["native_artifacts"]) == {"native_start.json", "native_return.json"},
            "native return missing artifacts")
    for name, digest in raw["native_artifacts"].items():
        require(sha256_path(directory / name) == digest, f"native artifact hash mismatch: {name}")
    return raw


def observed_exit(supervision):
    return supervision["classification"] == "exited" and supervision["returncode"] == 0


def auditable(supervision, directory):
    return (observed_exit(supervision)
            or supervision["classification"] == "supervisor_lost"
            and (directory / "return.json").is_file())


def audit_return(root, binding, directory, arm):
    raw = raw_return(root, directory, arm)
    supervision_path = directory / "supervision.json"
    supervised = observed_exit(read(supervision_path))
    verdict_path = directory / "audit.json"
    cell = binding["cells"][arm[0]]
    if verdict_path.exists():
        return verified_verdict(directory, cell, raw)
    evidence, reports = {}, {}
    if raw["point"] is not None:
        for target in SOLVERS:
            if (root / "STOP").exists():
                raise KeyboardInterrupt
            audit_dir = directory / "audit_attempts" / target / uuid4().hex
            request, result = audit_dir / "request.json", audit_dir / "result.json"
            atomic_immutable_json(request, {"cell": cell, "point": raw["point"]})
            with (audit_dir / "worker.log").open("xb") as log:
                subprocess.run([interpreter(binding, target), "-m", WORKER, "audit",
                                target, str(request), str(result)], cwd=ROOT, env=preflight.child_env(),
                               stdout=log, stderr=subprocess.STDOUT, timeout=AUDIT_WALL_SECONDS,
                               check=True)
            returned = read(result)
            require(returned["solver"] == target and returned["request_sha256"] == sha256_path(request),
                    "audit worker evaluated the wrong dispatch")
            reports[target] = returned["evaluation"]
            evidence[target] = {"request": ref(request, directory), "result": ref(result, directory)}
    verdict = assess(decode_case(cell["case"]), raw, reports, supervised=supervised)
    atomic_immutable_json(verdict_path, {"return": ref(directory / "return.json", directory),
                                         "supervision": ref(supervision_path, directory),
                                         "targets": evidence, "verdict": verdict})
    return verdict


def verified_verdict(directory, cell, raw):
    item = read(directory / "audit.json")
    checked(directory, item["return"])
    require(item["supervision"] == ref(directory / "supervision.json", directory),
            "audit receipt differs from supervision record")
    supervised = observed_exit(checked(directory, item["supervision"]))
    verdict = item["verdict"]
    require(set(item["targets"]) == set(verdict.get("evaluations", {})),
            "audit receipt missing target evidence")
    for target, evidence in item["targets"].items():
        require(checked(directory, evidence["request"]) == {"cell": cell, "point": raw["point"]},
                "audit input differs from bound case or returned primal")
        result = checked(directory, evidence["result"])
        require(result["solver"] == target
                and result["request_sha256"] == evidence["request"]["sha256"]
                and result["evaluation"] == verdict["evaluations"][target], "audit evidence mismatch")
    require(assess(decode_case(cell["case"]), raw, verdict.get("evaluations", {}),
                   supervised=supervised) == verdict,
            "published acceptance differs from the evidence")
    return verdict


def progress(root, binding):
    records = arm_attempts(root, binding)
    arms, paired, worker_seconds = [], {}, 0.
    for arm, attempts in records.items():
        launched = [directory for directory in attempts if consumed(directory)]
        require(len(launched) <= 1, f"more than one solve reservation consumed for {arm}")
        for directory in attempts:
            if (directory / "supervision.json").exists():
                worker_seconds += read(directory / "supervision.json")["wall_seconds"]
        entry = {"cell": arm[0], "solver": arm[1], "attempts": len(attempts), "state": "pending"}
        if launched:
            directory = launched[0]
            entry["attempt"] = str(directory.relative_to(root))
            manifest = directory / "supervision.json"
            if not manifest.exists():
                entry["state"] = "unfinalized"
            else:
                sup = read(manifest)
                entry.update(state=sup["classification"], supervision=sup)
                if sup["classification"] == "exited" and (
                    not observed_exit(sup) or not (directory / "return.json").is_file()
                ):
                    entry["state"] = "worker_failed"
                elif auditable(sup, directory):
                    raw = raw_return(root, directory, arm)
                    entry["native_success"] = raw["native_success"]
                    entry["solver_call_seconds"] = raw["solver_call_seconds"]
                    if not (directory / "audit.json").is_file():
                        entry["state"] = "awaiting_audit"
                    else:
                        verdict = verified_verdict(directory, binding["cells"][arm[0]], raw)
                        entry.update(state=verdict["classification"], audit_failures=verdict["failures"])
                        paired.setdefault(arm[0], {})[arm[1]] = {"raw": raw, "audit": verdict}
        arms.append(entry)
    return {
        "outcome": read(root / "outcome.json") if (root / "outcome.json").exists() else None,
        "arms": arms, "finalized_worker_seconds": worker_seconds,
        "accepted": sum(a["state"] == "accepted" for a in arms),
        "terminal": sum(a["state"] not in {"pending", "unfinalized", "awaiting_audit"} for a in arms),
        "pairs": {cell: pair_summary(paired.get(cell, {}), binding["settings"]["material_cost_threshold"])
                  for cell in CELLS},
    }


def execute(root, digest, *, resume=False, acknowledge_stop=False, supervisor=supervise,
            verify=verify_binding):
    binding_path = root / "binding.json"
    require(digest == sha256_path(binding_path), "launch/resume needs the exact binding SHA-256")
    binding = read(binding_path)
    with (root / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify(root, binding)
        authority_path = root / "authority.json"
        authority = {"binding_sha256": digest, "execution_authorized": True}
        if resume:
            require(read(authority_path) == authority, "resume requires existing matching launch authority")
        else:
            require(not acknowledge_stop and not authority_path.exists(), "use --resume for an existing run")
            atomic_immutable_json(authority_path, authority)
        if acknowledge_stop:
            require(resume, "STOP acknowledgement requires resume")
            if (root / "STOP").exists():
                archived = root / "stops" / f"stop-{time.time_ns()}"
                archived.parent.mkdir(exist_ok=True)
                (root / "STOP").rename(archived)
        reconcile(root, arm_attempts(root, binding))
        outcome = "infrastructure_failure"
        atomic_json(root / "outcome.json", {"outcome": "running", "utc": preflight.utc()})
        try:
            for arm in binding["arms"]:
                if (root / "STOP").exists():
                    raise KeyboardInterrupt
                directories = arm_attempts(root, binding)[tuple(arm)]
                launched = [d for d in directories if consumed(d)]
                require(len(launched) <= 1, f"duplicate launched attempts: {arm}")
                if launched:
                    directory = launched[0]
                    sup = read(directory / "supervision.json")
                    if not auditable(sup, directory):
                        continue  # Launched attempts, including failures, never retry.
                else:
                    ambient_check()
                    directory = root / "attempts" / uuid4().hex
                    request = {
                        "cell": arm[0], "solver": arm[1], "binding_sha256": digest,
                        "protocol": ref(root / "protocol.json", root),
                        "wall_seconds": binding["settings"]["wall_seconds_per_worker"],
                        "requested_utc": preflight.utc(),
                    }
                    atomic_immutable_json(directory / "request.json", request)
                    print(f"{arm[0]} {arm[1]}: launching {directory.name}", flush=True)
                    sup = supervisor([interpreter(binding, arm[1]), "-m", WORKER, "solve",
                                      str(root), directory.name], directory, root, request)
                    if sup["classification"] == "interrupted":
                        raise KeyboardInterrupt
                    if sup["classification"] == "wall_limit":
                        continue  # A censored arm remains a result.
                    require(sup["classification"] == "exited" and sup["returncode"] == 0,
                            f"benchmark stopped at {arm}: {sup['classification']} ({sup['returncode']})")
                verdict = audit_return(root, binding, directory, arm)
                print(f"{arm[0]} {arm[1]}: {verdict['classification']}", flush=True)
            outcome = "matrix_complete"
        except KeyboardInterrupt:
            outcome = "operator_stop"
        finally:
            atomic_json(root / "outcome.json", {"outcome": outcome, "utc": preflight.utc()})
    return outcome


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    freeze = sub.add_parser("bind")
    freeze.add_argument("run_id")
    freeze.add_argument("--preflight", required=True)
    freeze.add_argument("--settings", required=True, type=Path)
    freeze.add_argument("--pypower-python", required=True, type=Path)
    for name in ("launch", "resume"):
        command = sub.add_parser(name)
        command.add_argument("run_id")
        command.add_argument("--binding-sha256", required=True)
        if name == "resume":
            command.add_argument("--acknowledge-stop", action="store_true")
    sub.add_parser("status").add_argument("run_id")
    args = parser.parse_args()
    root = output(args.run_id)
    if args.action == "bind":
        print(bind(root, output(args.preflight), args.settings, args.pypower_python.absolute()))
    elif args.action == "status":
        print(json.dumps(progress(root, read(root / "binding.json")), indent=2, allow_nan=False))
    else:
        signal.signal(signal.SIGTERM, interrupted)
        print(execute(root, args.binding_sha256, resume=args.action == "resume",
                      acknowledge_stop=getattr(args, "acknowledge_stop", False)))


if __name__ == "__main__":
    main()
