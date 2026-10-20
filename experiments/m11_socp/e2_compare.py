"""Bounded matched AC/SOCP evidence. See E2_PLAN.md before executing.

Run from repository root: uv run --extra dev python -m experiments.m11_socp.e2_compare
Raw artifacts go to a fresh ignored directory, never replacing prior evidence.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
from time import perf_counter
import traceback
from unittest.mock import patch

import numpy as np

from cvxpy.reductions.solvers.solving_chain import SolvingChain
from cvxopf import extract_results
from tests.socp_matched import (
    AC_SETTINGS, SOCP_SETTINGS, PAIR_NAMES, build_pair, check_containment, check_pair, fixture, json_value,
)


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def encode(value):
    value = json_value(value)
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, complex):
        return dict(real=value.real, imag=value.imag)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(encode(value), sort_keys=True, indent=2, allow_nan=False)+"\n")
    temporary.replace(path)


def context():
    sources = sorted((ROOT / "src" / "cvxopf").rglob("*.py")) + [
        ROOT / "tests" / "socp_matched.py", ROOT / "tests" / "socp_reference_cases.py",
        ROOT / "tests" / "test_socp_matched.py", HERE / "e2_compare.py", HERE / "E2_PLAN.md",
        ROOT / "uv.lock",
    ]
    return dict(
        head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        worktree_status=subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True),
        source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        python=sys.version, platform=platform.platform(), machine=platform.machine(), processor=platform.processor(),
        versions={p: importlib.metadata.version(p) for p in ("cvxpy", "clarabel", "cyipopt", "numpy", "scipy")},
    )


def timed_solve(build, settings):
    original = SolvingChain.apply
    canonicalization = []

    def observed(chain, *args, **kwargs):
        start = perf_counter()
        try:
            return original(chain, *args, **kwargs)
        finally:
            canonicalization.append(perf_counter()-start)

    start = perf_counter()
    error = None
    try:
        with patch.object(SolvingChain, "apply", observed):
            build.solve(**settings)
    except Exception:
        error = traceback.format_exc()
    elapsed = perf_counter()-start
    stats = build.prob.solver_stats
    info = dict(solve_wall_including_canonicalization_s=elapsed,
        reduction_chain_apply_s=sum(canonicalization) if canonicalization else None,
        reduction_chain_calls=len(canonicalization), settings=settings, exception=error,
        solver_name=None if stats is None else stats.solver_name,
        solver_s=None if stats is None else stats.solve_time,
        iterations=None if stats is None else stats.num_iters,
        extra_stats=None if stats is None else stats.extra_stats,
        status=build.prob.status,
        scalar_variables=build.prob.size_metrics.num_scalar_variables,
        scalar_equalities=build.prob.size_metrics.num_scalar_eq_constr,
        scalar_inequalities=build.prob.size_metrics.num_scalar_leq_constr,
        constraint_objects=len(build.prob.constraints),
        canonicalization_backend=build.canonicalization_backend,
        canonical_soc_sizes=None,
    )
    if error is None and build.formulation == "socp":
        data, _, _ = build.prob.get_problem_data("CLARABEL", canon_backend=build.canonicalization_backend)
        info["canonical_soc_sizes"] = data["dims"].soc
    return info


def summary_audit(audit):
    return dict(available=audit["available"], feasible=audit["feasible"],
        complete=audit["complete"], tolerances=audit["tolerances"],
        residuals=audit["residuals"], reason=audit["reason"])


def summary(spec, checked):
    delta = spec["kwargs"].get("delta", 1.)
    audits = {"ac": checked["containment"]["relaxation_audit"], "socp": checked["socp_audit"]}
    arms = {}
    for arm in ("ac", "socp"):
        result, audit = checked[f"{arm}_result"], audits[arm]
        w = (np.asarray(result["Vm"])**2 if arm == "ac" else np.asarray(result["w"]))
        hvdc_loss = (None if "p_hvdc_in" not in result else
                     float(delta*np.sum(-result["p_hvdc_in"]-result["p_hvdc_out"])))
        arms[arm] = dict(objective=result["objective"], status=result["status"],
            components=checked["containment"]["objective_components"] if arm == "ac" else checked["socp_components"],
            branch_loss_mwh=float(delta*np.sum((audit["branch_from_mva"]+audit["branch_to_mva"]).real)),
            bus_shunt_mwh=float(delta*np.sum(w*spec["case"]["bus"][:, 4])),
            hvdc_loss_mwh=hvdc_loss, energy_not_served_mwh=result.get("energy_not_served", 0.),
            storage_power_mw=result.get("b"), storage_end_boundaries_mwh=result.get("soc"),
            audit=summary_audit(audit))
    recovery = checked["socp_recovery"]
    return dict(arms=arms, objective_gap=checked["objective_gap"], gap_allowance=checked["gap_allowance"],
        objective_label=checked["objective_label"], dual_certificate=None,
        lifted_ac_exact_recovery=checked["containment"]["recovery"]["exact_product_recovery"],
        lifted_ac_feasible=checked["containment"]["recovery"]["ac_feasible"],
        socp_rank_relative=checked["socp_audit"]["edge_gaps"]["relative_summary"],
        recovery_available=recovery["available"], recovery_reason=recovery["reason"],
        recovered_ac_feasible=recovery["ac_feasible"],
        exact_product_recovery=recovery["exact_product_recovery"],
        cycle_summary=recovery.get("cycle_summary"), product_summary=recovery.get("product_summary"),
        recovered_ac_audit=None if recovery["ac_audit"] is None else summary_audit(recovery["ac_audit"]))


def worker(name, output):
    record = dict(name=name, accepted=False, solves_attempted=0,
                  started_utc=datetime.now(timezone.utc).isoformat(), context=context())
    try:
        spec = fixture(name)
        record["inputs"] = json_value(spec)
        start = perf_counter()
        ac, socp, identity = build_pair(spec)
        record.update(input_sha256=identity, pair_build_s=perf_counter()-start, attempts={})
        for arm, build, settings in (("ac", ac, AC_SETTINGS), ("socp", socp, SOCP_SETTINGS)):
            record["solves_attempted"] += 1
            write(output, record)
            record["attempts"][arm] = timed_solve(build, settings)
            record[f"{arm}_result"] = extract_results(build)
            write(output, record)
            if record["attempts"][arm]["exception"] or build.prob.status not in ("optimal", "optimal_inaccurate"):
                raise RuntimeError(f"{arm} did not return an accepted solver termination")
            if arm == "ac":
                record["ac_containment"] = check_containment(spec, ac, socp, record["ac_result"])
                write(output, record)
        checked = check_pair(spec, ac, socp)
        record["checked"] = checked
        record["summary"] = summary(spec, checked)
        record["accepted"] = True
    except Exception:
        record["error"] = traceback.format_exc()
    record["end_context"] = context()
    record["ended_utc"] = datetime.now(timezone.utc).isoformat()
    if record["context"] != record["end_context"]:
        record.update(accepted=False, context_error="Source/environment changed during execution")
    write(output, record)
    return 0 if record["accepted"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "results" / "e2")
    parser.add_argument("--worker", choices=PAIR_NAMES)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker, args.output)
    args.output.mkdir(parents=True, exist_ok=False)
    root = dict(context=context(), accepted=False, records=[], timeout_s_per_pair=180,
                settings={"ac": AC_SETTINGS, "socp": SOCP_SETTINGS}, solves_attempted=0)
    write(args.output / "run.json", root)
    for name in PAIR_NAMES:
        output = args.output / f"{name}.json"
        with (args.output / f"{name}.log").open("w") as log:
            try:
                completed = subprocess.run([sys.executable, "-m", "experiments.m11_socp.e2_compare",
                    "--worker", name, "--output", str(output)], cwd=ROOT,
                    stdout=log, stderr=subprocess.STDOUT, timeout=180, check=False)
                returncode = completed.returncode
            except subprocess.TimeoutExpired:
                returncode = None
        record = json.loads(output.read_text()) if output.exists() else {}
        accepted = returncode == 0 and record.get("accepted") is True and record.get("context") == root["context"]
        item = dict(name=name, accepted=accepted, returncode=returncode,
                    artifact=output.name if output.exists() else None,
                    sha256=hashlib.sha256(output.read_bytes()).hexdigest() if output.exists() else None,
                    summary=record.get("summary"), pair_build_s=record.get("pair_build_s"),
                    attempts=record.get("attempts"), error=record.get("error"))
        root["records"].append(item)
        root["solves_attempted"] += record.get("solves_attempted", 0)
        write(args.output / "run.json", root)
        print(f"{name}: {'accepted' if accepted else 'STOPPED'}", flush=True)
        if not accepted:
            return 1
    vector, stepwise = (root["records"][i]["summary"]["arms"]["socp"]["objective"] for i in (2, 3))
    root["mixed_assembly_objective_difference"] = vector-stepwise
    root["accepted"] = bool(abs(vector-stepwise) <= 2e-5+2e-6*abs(stepwise))
    root["end_context"] = context()
    root["accepted"] &= root["end_context"] == root["context"]
    write(args.output / "run.json", root)
    return 0 if root["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
