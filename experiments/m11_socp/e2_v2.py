"""Prepared E2 refresh. Preflight/replay never optimize; see E2_V2_PLAN.md."""
# ruff: noqa: E402 -- numerical imports follow CLI thread limits.

import argparse
import ast
import inspect
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
import textwrap
import warnings

THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
if __name__ == "__main__":
    for key in THREAD_KEYS:
        os.environ[key] = "1"

from cvxopf import extract_results
from experiments.ac_cost_coordinates.run import monitoring
from experiments.numerical_preparation import run_qualification as q
from experiments.numerical_preparation.audit import evidence_record, serializable
from experiments.m11_socp.e1_compare import context as public_context, SOLVER_SETTINGS
from experiments.m11_socp.e2_compare import timed_solve
from experiments.m11_socp.e2_v2_audit import construct, row_binding, assess
from tests.socp_matched import PAIR_NAMES, AC_SETTINGS

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUTPUT = HERE / "results/e2_v2_001"
MODULE = "experiments.m11_socp.e2_v2"
LIMITS = dict(max_launches=4, max_optimizer_calls=8, wall_seconds=180., rss_mib=16384.,
              total_worker_seconds=720., poll_seconds=.1)
SETTINGS = dict(ac=dict(AC_SETTINGS, warm_start=False, verbose=False), socp=SOLVER_SETTINGS)


def context():
    value = public_context()
    paths = [Path(__file__), HERE / "e2_v2_audit.py", HERE / "E2_V2_PLAN.md",
             ROOT / "experiments/ac_cost_qualification/audit.py",
             ROOT / "experiments/ac_production_verification/audit.py"]
    value["source_sha256"].update({str(p.relative_to(ROOT)):q.digest(p) for p in paths})
    from importlib.metadata import distribution
    installed = distribution("cyipopt")
    value["cyipopt_native_sha256"] = {str(installed.locate_file(p)):q.digest(installed.locate_file(p))
        for p in (installed.files or ()) if str(p).endswith((".so", ".dylib"))}
    if not value["cyipopt_native_sha256"]:
        raise ValueError("IPOPT native binary provenance unavailable")
    return value


def protocol():
    return dict(protocol=LIMITS, settings=SETTINGS, plan_sha256=q.digest(HERE / "E2_V2_PLAN.md"))


def frozen_binding():
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    import clarabel
    resolved = clarabel.DefaultSettings()
    for k,v in SETTINGS["socp"].items():
        if k != "warm_start":
            setattr(resolved,k,v)
    # Bind stock adapter defaults and explicit overrides; no optimizer instance.
    adapter = inspect.getsource(IPOPT.solve_via_data)
    defaults = [node.value for node in ast.walk(ast.parse(textwrap.dedent(adapter)))
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "default_options"
                                               for t in node.targets)]
    if len(defaults) != 1:
        raise ValueError("installed IPOPT adapter default options unsupported")
    ac_settings = ast.literal_eval(defaults[0])
    ac_settings.update({k:v for k,v in SETTINGS["ac"].items() if k not in ("warm_start","verbose")})
    ac_settings.setdefault("print_level", 3)
    return dict(context=context(), rows=[row_binding(n) for n in PAIR_NAMES],
        ipopt_adapter_sha256=hashlib.sha256(adapter.encode()).hexdigest(),
        resolved_settings=dict(ac=ac_settings, socp={k:getattr(resolved,k) for k in dir(resolved)
            if not k.startswith("_") and not callable(getattr(resolved,k))}))


def verify_binding(root):
    binding = q.read(root / "binding.json")
    if binding != frozen_binding() or q.read(root / "protocol.json") != protocol():
        raise ValueError("prepared E2 source/environment/input/settings binding changed")
    return binding


def request(root, number, wall):
    return dict(call_id=number, role="primary", wall_seconds=wall,
                protocol=q.reference(root / "protocol.json", root))


def checked(name, row, records):
    try:
        return assess(name, row, records)
    except (AssertionError, ValueError, TypeError, KeyError, AttributeError, StopIteration) as exc:
        return dict(passed=False, reason=f"{type(exc).__name__}: {exc}")


def full_native(formulation, native):
    return type(native.get("status")) is int and native["status"] == 0 if formulation == "ac" else native.get("status") == "Solved"


def archive_ac(directory, record):
    """Publish the completed first arm before risking the second solve."""
    value = {k:record[k] for k in ("iteration", "call_id", "row", "request", "audit")}
    value["record"] = record["records"]["ac"]
    q.atomic_gzip_json(directory/"ac-arm.json.gz", serializable(value))
    names = ("request.json", "ac-native.json.gz", "ac-arm.json.gz")
    q.atomic_immutable_json(directory/"ac-completion.json", dict(artifacts={n:q.digest(directory/n) for n in names}))


def replay_ac(directory, number, row, req):
    """Inspect a retained first arm without accepting or resuming the pair."""
    if not (directory/"ac-completion.json").exists():
        return None
    names = ("request.json", "ac-native.json.gz", "ac-arm.json.gz")
    if q.read(directory/"ac-completion.json")["artifacts"] != {n:q.digest(directory/n) for n in names}:
        raise ValueError("E2 AC checkpoint manifest/archive mismatch")
    value = q.read(directory/"ac-arm.json.gz")
    if value["iteration"] != number or value["call_id"] != number or value["row"] != row or value["request"] != req:
        raise ValueError("E2 AC checkpoint binding mismatch")
    arm = value["record"]
    if q.read(directory/"ac-native.json.gz") != dict(iteration=number,native=arm["native"]):
        raise ValueError("E2 AC checkpoint native mismatch")
    audit = (checked(row["name"],row,{"ac":arm}) if full_native("ac",arm["native"]) else
             dict(passed=False,reason="ac native solve not fully converged"))
    if audit != value["audit"]:
        raise ValueError("E2 AC checkpoint audit replay disagrees")
    return value


def worker(root, number):
    directory = root / f"call-{number:03d}"
    record = dict(iteration=number, call_id=number, optimizer_calls=0, records={}, exception=None)
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
            req != request(root, number, req["wall_seconds"]) or isinstance(req["wall_seconds"],bool) or
            not 0 < req["wall_seconds"] <= LIMITS["wall_seconds"]):
            raise ValueError("invalid E2 worker launch/request")
        row = verify_binding(root)["rows"][number-1]
        record.update(row=row, request=req)
        phase("construct")
        construction = time.monotonic()
        _, ac, socp, _ = construct(row["name"])
        record["pair_build_seconds"] = time.monotonic()-construction
        for formulation, build in (("ac",ac),("socp",socp)):
            phase(formulation+"_solve")
            record["optimizer_calls"] += 1
            arm = record["records"][formulation] = {}
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                arm["timing"] = timed_solve(build, SETTINGS[formulation])
            arm["warnings"] = [dict(category=w.category.__name__, message=str(w.message)) for w in caught]
            arm["preparation_evidence"] = evidence_record(build.preparation_evidence)
            if arm["preparation_evidence"] is None:
                raise ValueError("public prepared solve lacks native evidence")
            arm["native"] = arm["preparation_evidence"]["native"]
            q.atomic_gzip_json(directory / (formulation+"-native.json.gz"), dict(iteration=number, native=arm["native"]))
            if not full_native(formulation, arm["native"]):
                record["audit"] = dict(passed=False, reason=formulation+" native solve not fully converged")
                if formulation == "ac":
                    archive_ac(directory,record)
                break
            if arm["timing"]["exception"]:
                raise RuntimeError(arm["timing"]["exception"])
            arm["result"] = serializable(extract_results(build))
            phase(formulation+"_audit")
            record["audit"] = checked(row["name"], row, record["records"])
            if formulation == "ac":
                archive_ac(directory,record)
            if not record["audit"]["passed"]:
                break
        verify_binding(root)
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    record["classification"] = classify(record)
    phase("archive")
    q.atomic_gzip_json(directory / "result.json.gz", serializable(record))
    names = ["request.json", "result.json.gz"] + [p.name for p in sorted(directory.glob("*-native.json.gz"))]
    if (directory/"ac-completion.json").exists():
        names += ["ac-arm.json.gz", "ac-completion.json"]
    q.atomic_immutable_json(directory / "completion.json", dict(artifacts={n:q.digest(directory/n) for n in names}))
    phase("complete")


def classify(record):
    if record["exception"]:
        return "exception"
    return "accepted" if (record.get("audit",{}).get("passed") and
        set(record["records"]) == {"ac","socp"} and all(full_native(f,a.get("native",{}))
        for f,a in record["records"].items())) else "rejected"


def status(root):
    binding = verify_binding(root)
    directories = sorted(root.glob("call-*"))
    if len(directories)>4:
        raise ValueError("E2 launch ceiling exceeded")
    attempts, used, calls, censored = [],0.,0,0
    counts = dict(finalized=0, accepted_pairs=0, rejected_pairs=0, result_archives=0,
                  native_archives=0, completion_manifests=0, ac_checkpoint_manifests=0, cycling_warnings=0)
    for number,directory in enumerate(directories,1):
        if directory.name != f"call-{number:03d}":
            raise ValueError("noncontiguous E2 pairs")
        row = binding["rows"][number-1]
        item = dict(name=row["name"], classification="unfinished", accepted=False)
        attempts.append(item)
        req = q.read(directory/"request.json")
        wall = req["wall_seconds"]
        if req != request(root,number,wall) or isinstance(wall,bool) or not 0<wall<=min(180.,720.-used):
            raise ValueError("E2 request/budget mismatch")
        counts["result_archives"] += int((directory/"result.json.gz").exists())
        counts["completion_manifests"] += int((directory/"completion.json").exists())
        counts["native_archives"] += len(list(directory.glob("*-native.json.gz")))
        checkpoint = replay_ac(directory,number,row,req)
        if checkpoint is not None:
            counts["ac_checkpoint_manifests"] += 1
            item["completed_ac"] = dict(native_status=checkpoint["record"]["native"]["status"],
                                        audit=checkpoint["audit"], timing=checkpoint["record"].get("timing"))
        if not (directory/"supervision.json").exists():
            if number != len(directories):
                raise ValueError("unfinished E2 pair cannot advance")
            censored += 1
            continue
        sup = q.read(directory/"supervision.json")
        if sup["classification"] not in ("exited", "wall_limit", "rss_limit", "interrupted", "supervisor_failure"):
            raise ValueError("invalid E2 supervision classification")
        elapsed = sup["wall_seconds"]
        if isinstance(elapsed,bool) or not math.isfinite(elapsed) or elapsed<0:
            raise ValueError("invalid E2 worker effort")
        resource_ok = q.resource_evidence(directory,sup)
        used += elapsed
        counts["finalized"] += 1
        item.update(classification=sup["classification"], worker_seconds=elapsed,
                    peak_sampled_rss_mib=sup["peak_sampled_rss_mib"])
        if not (directory/"completion.json").exists():
            if sup["classification"] == "exited" and sup["returncode"] == 0:
                raise ValueError("normal E2 worker lacks completion")
            censored += 1
            continue
        record = q.read(directory/"result.json.gz")
        names = {"request.json","result.json.gz"} | {f+"-native.json.gz" for f,a in record["records"].items() if "native" in a}
        if checkpoint is not None:
            names |= {"ac-arm.json.gz", "ac-completion.json"}
            if record["records"].get("ac") != checkpoint["record"]:
                raise ValueError("E2 completed AC checkpoint disagrees with pair archive")
        if {p.name for p in directory.glob("*-native.json.gz")} != {n for n in names if n.endswith("-native.json.gz")}:
            raise ValueError("E2 retained native archives disagree with manifest")
        if q.read(directory/"completion.json")["artifacts"] != {n:q.digest(directory/n) for n in names}:
            raise ValueError("E2 manifest/archive mismatch")
        if record["call_id"] != number or record.get("row",row) != row or record.get("request",req) != req:
            raise ValueError("E2 record/binding mismatch")
        expected_calls = len(record["records"])
        if record["optimizer_calls"] != expected_calls or not 0<=expected_calls<=2:
            raise ValueError("E2 optimization call count mismatch")
        calls += expected_calls
        for f,a in record["records"].items():
            if "native" in a and q.read(directory/(f+"-native.json.gz")) != dict(iteration=number,native=a["native"]):
                raise ValueError("E2 native archives disagree")
        if "audit" in record and all(full_native(f,a.get("native",{})) for f,a in record["records"].items()):
            if checked(row["name"],row,record["records"]) != record["audit"]:
                raise ValueError("E2 independent audit replay disagrees")
        classification = classify(record)
        if classification != record["classification"]:
            raise ValueError("E2 classification disagrees")
        item.update(worker_classification=classification, optimizer_calls=expected_calls,
                    audit=record.get("audit"), exception=record["exception"])
        item["accepted"] = bool(classification == "accepted" and resource_ok and
            sup["classification"] == "exited" and sup["returncode"] == 0 and elapsed<=wall and used<=720.)
        counts["accepted_pairs"] += int(item["accepted"])
        counts["rejected_pairs"] += int(classification == "rejected")
        counts["cycling_warnings"] += int(record.get("audit",{}).get("economics",{}).get("cycling_warning",False))
    assembly_check = None
    if counts["accepted_pairs"] == 4:
        vector,step = (attempts[i]["audit"]["summary"]["arms"]["socp"]["objective"] for i in (2,3))
        difference,allowance = vector-step,2e-5+2e-6*abs(step)
        assembly_check = dict(difference=difference, allowance=allowance, passed=abs(difference)<=allowance)
    return dict(expected_pairs=4, launches=len(directories), optimizer_calls=calls,
                optimizer_call_count_censored_pairs=censored, optimizer_calls_upper_bound=calls+2*censored, **counts,
                worker_seconds=used, remaining_worker_seconds=max(0.,720.-used),
                mixed_assembly_check=assembly_check, attempts=attempts)


def run(root,commit):
    if root.exists():
        raise FileExistsError("E2 output must be fresh")
    binding = frozen_binding()
    if binding["context"]["worktree_status"] or binding["context"]["head"] != commit or len(commit)!=40:
        raise ValueError("reviewed clean full commit required")
    if any(binding["context"]["thread_environment"][k] != "1" for k in THREAD_KEYS):
        raise ValueError("single-thread environment required")
    telemetry = monitoring()
    root.mkdir(parents=True,exist_ok=False)
    q.atomic_immutable_json(root/"binding.json",binding)
    q.atomic_immutable_json(root/"protocol.json",protocol())
    q.atomic_immutable_json(root/"invocation-start.json",dict(utc=q.utc(),telemetry=telemetry))
    outcome,exception = "failure",None
    try:
        for number in range(1,5):
            progress = status(root)
            if progress["launches"] != progress["finalized"]:
                raise ValueError("unfinished E2 pair cannot advance")
            if (root/"STOP").exists() or progress["remaining_worker_seconds"]<=0:
                outcome = "operator_stop" if (root/"STOP").exists() else "budget_exhausted"
                break
            telemetry = monitoring()
            directory = root/f"call-{number:03d}"
            directory.mkdir()
            q.atomic_immutable_json(directory/"telemetry-start.json",telemetry)
            req = request(root,number,min(180.,progress["remaining_worker_seconds"]))
            q.atomic_immutable_json(directory/"request.json",req)
            q.supervise([sys.executable,"-m",MODULE,"--worker",str(number),"--output",str(root)],directory,root,req)
            progress = status(root)
            q.atomic_json(root/"report.json",progress)
            if not progress["attempts"][-1]["accepted"]:
                outcome = "stopped_on_outcome"
                break
        else:
            outcome = "matrix_complete" if progress["mixed_assembly_check"]["passed"] else "assembly_comparison_failed"
    except KeyboardInterrupt:
        outcome = "operator_stop"
    except Exception as exc:
        exception = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        q.atomic_immutable_json(root/"invocation-finish.json",dict(utc=q.utc(),outcome=outcome,exception=exception))
    return dict(outcome=outcome,output=str(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=OUTPUT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight",action="store_true")
    mode.add_argument("--status",action="store_true")
    mode.add_argument("--authorize-execution",action="store_true")
    mode.add_argument("--worker",type=int,choices=range(1,5))
    parser.add_argument("--commit")
    args = parser.parse_args()
    if args.worker:
        worker(args.output,args.worker)
        return
    if args.preflight:
        result = frozen_binding()
    elif args.status:
        result = status(args.output)
    else:
        if not args.commit:
            parser.error("execution requires --commit <full SHA>")
        signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
        result = run(args.output,args.commit)
    print(json.dumps(serializable(result),indent=2,allow_nan=False))


if __name__ == "__main__":
    main()
