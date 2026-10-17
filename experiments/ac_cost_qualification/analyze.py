"""Terminal non-solving summaries and matched fleet trajectory plots."""

from collections import defaultdict
import re

import numpy as np

from experiments.ac_cost_coordinates.analyze import soc_boundaries
from experiments.numerical_preparation import run_qualification as q
from experiments.numerical_preparation.audit import serializable
from experiments.numerical_preparation.tracy_variables import compare
from . import fixture as f, run as r


def disposition(arms, attempts):
    by_id = {a["arm"]["id"]: a for a in attempts}
    if any(a.id not in by_id or by_id[a.id]["classification"] == "unfinished" for a in arms):
        return "incomplete"
    return "qualified_for_declared_matrix" if all(by_id[a.id]["accepted"] for a in arms) else "not_qualified"


def plot_group(root, group_number, arm, candidates, kwargs):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 2, figsize=(11, 10), sharex=True, layout="constrained")
    panels = (("Pg", "Generation MW", False), ("b", "Battery net real MW", False),
              ("b", "Battery absolute throughput MW", True), ("soc", "Fleet SoC MWh", False),
              ("b_q", "Battery reactive MVAr", False), ("p_load_shed", "Real load shed MW", False))
    for label, candidate in candidates.items():
        for axis, (key, title, absolute) in zip(axes.flat, panels, strict=True):
            result = candidate["result"]
            values = soc_boundaries(result, kwargs) if key == "soc" else np.asarray(result[key], float)
            hours = np.arange(arm.start, arm.start + len(values))
            axis.plot(hours, np.sum(abs(values) if absolute else values, axis=1), marker=".", label=label)
            axis.set_title(title)
            axis.grid(alpha=.25)
    axes[0, 0].legend(fontsize=7)
    for axis in axes[-1]:
        axis.set_xlabel("Global input hour (Case9: fixture step; SoC: boundary)")
    fig.suptitle(f"{arm.group}\nConverged candidates include economic rejects; fleet sums can hide device counterflows")
    path = root / f"group-{group_number:02d}-fleet.png"
    if path.exists():
        raise FileExistsError(path)
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return dict(path=path.name, sha256=q.digest(path))


def analyze(root, *, make_plots=True):
    finish = q.read(root / "invocation-finish.json")
    if (root / "analysis.json").exists():
        raise FileExistsError(root / "analysis.json")
    progress = r.status(root)
    if any(a["classification"] == "unfinished" for a in progress["attempts"]):
        raise ValueError("unfinished evidence cannot support terminal analysis")
    summaries, groups = [], defaultdict(dict)
    for attempt in progress["attempts"]:
        arm = f.Arm(**attempt["arm"])
        directory = root / f"call-{arm.id:03d}"
        text = (directory / "worker.log").read_text() if (directory / "worker.log").exists() else ""
        iterations = re.findall(r"Number of Iterations\.*:\s+(\d+)", text)
        exits = re.findall(r"EXIT: (.*)", text)
        summary = {k: v for k, v in attempt.items() if k != "checks"}
        summary.update(iterations=int(iterations[-1]) if iterations else None,
                       solver_exit=exits[-1] if exits else None)
        if "checks" in attempt:
            checks = attempt["checks"]
            record = q.read(directory / "result.json.gz")
            evidence = record["preparation_evidence"]
            summary.update(costs=checks["common"]["costs"], economics=checks["economics"],
                physical_passed=checks["common"]["passed"], physical_residuals=checks["common"]["residuals"],
                forced_shedding=checks["forced_shedding"], native_status=record["native"]["status"],
                native_restoration_optimality_diagnostics=evidence["checks"] if evidence else None,
                battery_throughput_mwh=float(record.get("delta", 1.) * np.sum(abs(np.asarray(checks["result"]["b"])))))
            groups[arm.group][arm.mode] = dict(result=checks["result"])
        summaries.append(summary)
    comparisons, plots = {}, []
    for number, (group, candidates) in enumerate(groups.items(), 1):
        arm = next(a for a in f.arms() if a.group == group)
        _, kwargs, _ = f.kwargs_for_arm(arm)
        comparison = compare(candidates, kwargs)
        # The shared descriptive array comparator originated in a T=3 study;
        # replace its old plot axes explicitly for this group's resolved interval.
        comparison["axes"]["hours"] = list(range(arm.start, arm.start+arm.T))
        comparison["axes"]["boundary_hours"] = list(range(arm.start, arm.start+arm.T+1))
        comparison["note"] = "Matched AC physical deltas are descriptive, not equality or global-optimality gates."
        if not all(check["passed"] for checks in comparison["axis_checks"].values() for check in checks.values()):
            raise ValueError("trajectory/device axis mismatch")
        comparisons[group] = comparison
        if make_plots:
            plots.append(plot_group(root, number, arm, candidates, kwargs))
    by_id = {s["arm"]["id"]: s for s in summaries}
    def active(arm, cost):
        summary = by_id.get(arm.id, {})
        return bool(summary.get("accepted") and summary.get("costs", {}).get(cost, 0.) > f.GATES["cost_abs"])
    generation = [a for a in f.arms() if a.dataset == "case9" and a.mode == "both"]
    shedding = [a for a in f.arms() if a.forced_shedding and a.mode == "both"]
    coverage = dict(generation_cost=all(active(a, "generator_cost") for a in generation),
                    forced_shedding_cost=all(active(a, "load_shedding_cost") for a in shedding))
    dispositions = {mode: disposition([a for a in f.arms() if a.mode == mode], progress["attempts"]) for mode in f.MODES}
    if dispositions["both"] == "qualified_for_declared_matrix" and not all(coverage.values()):
        dispositions["both"] = "incomplete_economic_coverage"
    value = serializable(dict(invocation=finish, progress={k: v for k, v in progress.items() if k != "attempts"},
        summaries=summaries, treatment_dispositions=dispositions, coverage=coverage,
        comparisons=comparisons, plots=plots,
        limitations="Bounded local AC evidence, not global optimality or default promotion. Failed/missing coverage is not qualified. Objective scaling is inferred, not native telemetry."))
    q.atomic_immutable_json(root / "analysis.json", value)
    return value
