"""Pinned, optimizer-disabled coordinate re-audit; never edit the original run."""

import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import clarabel
import cvxpy as cp

from cvxopf import OPFBuild
from experiments.numerical_preparation import fixture as old, run_qualification as q
from . import analyze, audit, fixture as f, model, run as r

OUTPUT = r.HERE / "results/coordinate_reaudit_001"
ORIGINAL = {
    "binding.json": "880190b5c8b62d2a317d1d52ae73cd1b52d2acc5ca830c40e48f9068cd4664d8",
    "protocol.json": "071c667df05677c097be370d44a7b72b13bd0870f80cde9f2203da3dabc6b0f5",
    "report.json": "42226a90e69c0b1ff26f80b2486614d58ce34c470573e8f9a28cdf904ab9c0e5",
    "invocation-start.json": "0749b5e154783bceb9a44fb1226f4e5dfc80951c97524c3482b7d02d78c06b67",
    "invocation-finish.json": "e3df5bb4d82c1953788fcc803582f8a3736f88fac009d6e17c74f6c4991553a2",
    "analysis.json": "d17bd56243952ddae9cbf7f261cc93546c8bcd4b88a505b465306e46288cb19a",
}
PREFIX = "experiments/convex_cost_qualification/"
CHANGED = {PREFIX+n for n in ("audit.py", "run.py", "analyze.py", "REPORT.md", "test_convex_cost_qualification.py")}
CHANGED.add("experiments/numerical_preparation/tracy_variables.py")
ADDED = {PREFIX+n for n in ("reaudit.py", "test_coordinate_reaudit.py", "COORDINATE_REAUDIT_PROTOCOL.md")}


def pins(root):
    """Hash complete retained evidence, including logs and original plot labels."""
    return {str(p.relative_to(root)): q.digest(p) for p in sorted(root.rglob("*")) if p.is_file()}


def verify_context(before, now):
    """Permit only the recorded checker/reader transition, not numerical edits."""
    if before["commit"] != "29f8aea803386149f49854b5fb22b71615f664b9" or before["clean"] is not True:
        raise ValueError("unexpected original execution context")
    excluded = {"clean", "sources", "convex_cost_qualification_sources"}
    if {k: v for k, v in before.items() if k not in excluded} != {k: v for k, v in now.items() if k not in excluded}:
        raise ValueError("re-audit numerical environment changed")
    for key in ("sources", "convex_cost_qualification_sources"):
        left, right = before[key], now[key]
        if set(left)-set(right) or set(right)-set(left)-ADDED:
            raise ValueError("re-audit added/removed an unapproved source")
        if any(left[name] != right[name] for name in left if name not in CHANGED):
            raise ValueError("re-audit changed an unapproved source")


def verify_original(root):
    for name, expected in ORIGINAL.items():
        if q.digest(root / name) != expected:
            raise ValueError(f"pinned original evidence changed: {name}")
    binding = q.read(root / "binding.json")
    now = r.context()
    verify_context(binding["context"], now)
    original_protocol = dict(protocol=f.LIMITS, gates=f.GATES, protocol_sha256=q.digest(r.HERE / "PROTOCOL.md"))
    if (q.read(root / "protocol.json") != original_protocol or binding["limits"] != f.LIMITS
            or binding["gates"] != f.GATES
            or [row["arm"] for row in binding["rows"]] != [r.asdict(arm) for arm in f.arms()]):
        raise ValueError("original matrix/protocol/limits mismatch")
    for name, sha in binding["raw_inputs"].items():
        if q.digest(old.ROOT / name) != sha:
            raise ValueError("original physical input changed")
    finish = q.read(root / "invocation-finish.json")
    if finish["outcome"] != "matrix_complete" or finish["exception"] is not None:
        raise ValueError("re-audit requires complete successful invocation")
    directories = sorted(root.glob("call-*"))
    if [d.name for d in directories] != [f"call-{i:03d}" for i in range(1, 97)]:
        raise ValueError("re-audit requires all 96 retained attempts")
    for directory in directories:
        sup, req = (q.read(directory / name) for name in ("supervision.json", "request.json"))
        if (sup["classification"] != "exited" or sup["returncode"] != 0
                or not 0 <= sup["wall_seconds"] <= req["wall_seconds"] or not q.resource_evidence(directory, sup)):
            raise ValueError("re-audit requires finalized resource-qualified workers")
    return binding, now


def unchanged_checks(before, after):
    """Only coordinate diagnostics and their aggregate verdict may change."""
    omitted = {"passed", "coordinate_checks"}
    if {k: v for k, v in before.items() if k not in omitted} != {k: v for k, v in after.items() if k not in omitted}:
        raise ValueError("non-coordinate checks or published values changed")


def evaluate(root):
    binding, context = verify_original(root)
    revised = []

    def historical(view, kwargs, stress, native, evidence, signature):
        before = audit.assess(view, kwargs, stress, native, evidence, signature, historical_coordinates=True)
        after = audit.assess(view, kwargs, stress, native, evidence, signature)
        unchanged_checks(before, after)
        revised.append(after)
        return before

    original = r.replay(root, binding, auditor=historical)
    if original != q.read(root / "report.json"):
        raise ValueError("original terminal report does not reproduce")
    progress, index = deepcopy(original), 0
    for item in progress["attempts"]:
        item["original_accepted"] = item["accepted"]
        if "checks" in item:
            item["checks"] = revised[index]
            index += 1
            item["accepted"] = bool(item["checks"]["passed"])
    progress["accepted"] = sum(a["accepted"] for a in progress["attempts"])
    progress["accepted_with_cycling_warnings"] = sum(a["accepted"] and a["checks"]["economics"]["cycling_gap_warning"]
                                                    for a in progress["attempts"])
    progress["accepted_with_bound_projection_warnings"] = sum(a["accepted"] and a["checks"]["coordinate_checks"]["bound_projection_warning"]
                                                              for a in progress["attempts"])
    return progress, context


def reaudit(root=r.OUTPUT, output=OUTPUT, *, make_plots=True):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or root in output.parents or output in root.parents:
        raise ValueError("re-audit output must be separate from original evidence")
    if output.exists():
        raise FileExistsError(output)
    before = pins(root)

    def forbidden(*args, **kwargs):
        raise AssertionError("re-audit must not invoke numerical optimizers")

    with (patch.object(clarabel, "DefaultSolver", forbidden), patch.object(OPFBuild, "solve", forbidden),
          patch.object(cp.Problem, "solve", forbidden), patch.object(model.CLARABEL, "solve_via_data", forbidden)):
        progress, context = evaluate(root)
        if pins(root) != before:
            raise ValueError("original evidence changed during re-audit")
        output.mkdir(parents=True, exist_ok=False)
        q.atomic_immutable_json(output / "binding.json", dict(utc=q.utc(), original_root=str(root),
            original_files=before, context=context, policy_sha256=q.digest(r.HERE / "COORDINATE_REAUDIT_PROTOCOL.md"),
            optimizer_calls=0))
        q.atomic_immutable_json(output / "report.json", progress)
        value = analyze.analyze_progress(root, output, progress, q.read(root / "invocation-finish.json"), make_plots=make_plots)
    if pins(root) != before:
        raise ValueError("original evidence changed during analysis")
    q.atomic_immutable_json(output / "completion.json", dict(original_files_unchanged=True,
        artifacts=pins(output), accepted=progress["accepted"], optimizer_calls=0))
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    value = reaudit(output=args.output)
    print(dict(value["progress"], remaining_by_formulation=dict(Counter(
        a["arm"]["formulation"] for a in value["summaries"] if not a["accepted"]))))
