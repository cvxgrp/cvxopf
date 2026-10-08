"""Non-solving terminal dispositions, same-model comparisons and fleet plots."""

from collections import defaultdict
from itertools import combinations

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
    if not all(by_id[a.id]["accepted"] for a in arms):
        return "not_qualified"
    for arm in arms:
        costs = by_id[arm.id]["checks"]["common"]["costs"]
        if (arm.dataset == "case9" and costs["generator_cost"] <= f.GATES["cost_abs"] or
                arm.forced_shedding and costs["load_shedding_cost"] <= f.GATES["cost_abs"]):
            return "incomplete_economic_coverage"
    return "qualified_for_declared_matrix"


def pair_metrics(candidates):
    rows = {}
    for left, right in combinations(candidates, 2):
        a, b = candidates[left], candidates[right]
        costs = [sum(c["common"]["costs"].values()) for c in (a, b)]
        limit = f.GATES["cost_abs"]+f.GATES["cost_rel"]*max(abs(v) for v in costs)
        difference = abs(costs[0]-costs[1])
        ens_difference = abs(a["result"]["energy_not_served"]-b["result"]["energy_not_served"])
        rows[left+"__"+right] = dict(physical_costs=costs, cost_difference=difference, cost_reference_limit=limit,
            cost_comparison_flag=difference > limit, ens_difference_mwh=ens_difference,
            ens_reference_limit_mwh=1e-4, ens_comparison_flag=ens_difference > 1e-4,
            independently_accepted=[a["accepted"], b["accepted"]],
            cycling_gap_warnings=[a["economics"]["cycling_gap_warning"], b["economics"]["cycling_gap_warning"]])
    return rows


def plot_group(root, number, arm, candidates, kwargs):
    path = root / f"group-{number:02d}-fleet.png"
    if path.exists():
        raise FileExistsError(path)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 2, figsize=(11, 10), sharex=True, layout="constrained")
    panels = (("Pg", "Generation MW", False), ("b", "Battery net real MW", False),
              ("b", "Battery absolute throughput MW", True), ("soc", "Fleet SoC MWh", False),
              ("b_q", "Battery reactive MVAr (not modeled in DC)", False),
              ("p_load_shed", "Real load shed MW", False))
    for label, candidate in candidates.items():
        label += " [rejected]" if not candidate["accepted"] else " [cycling warning]" if candidate["economics"]["cycling_gap_warning"] else ""
        if candidate.get("coordinate_checks", {}).get("bound_projection_warning"):
            label += " [bound projection]"
        for axis, (key, title, absolute) in zip(axes.flat, panels, strict=True):
            axis.set_title(title)
            axis.grid(alpha=.25)
            result = candidate["result"]
            if result.get(key) is None:
                continue
            values = soc_boundaries(result, kwargs) if key == "soc" else np.asarray(result[key], float)
            axis.plot(np.arange(arm.start, arm.start+len(values)),
                      np.sum(abs(values) if absolute else values, axis=1), marker=".", label=label)
    axes[0, 0].legend(fontsize=7)
    for axis in axes[-1]:
        axis.set_xlabel("Global input hour (Case9: fixture step; SoC: boundary)")
    fig.suptitle(f"{arm.group}\nConverged candidates; fleet sums can hide device counterflows")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return dict(path=path.name, sha256=q.digest(path))


def analyze(root, *, make_plots=True):
    finish = q.read(root / "invocation-finish.json")
    if (root / "analysis.json").exists():
        raise FileExistsError(root / "analysis.json")
    return analyze_progress(root, root, r.status(root), finish, make_plots=make_plots)


def analyze_progress(source, output, progress, finish, *, make_plots=True):
    """Render verified progress in a fresh directory without rewriting evidence."""
    if (output / "analysis.json").exists():
        raise FileExistsError(output / "analysis.json")
    if any(a["classification"] == "unfinished" for a in progress["attempts"]):
        raise ValueError("unfinished evidence cannot support terminal analysis")
    summaries, groups = [], defaultdict(dict)
    for attempt in progress["attempts"]:
        arm = f.Arm(**attempt["arm"])
        summary = {k: v for k, v in attempt.items() if k != "checks"}
        if "checks" in attempt:
            checks = attempt["checks"]
            native = q.read(source / f"call-{arm.id:03d}" / "result.json.gz")["native"]
            summary.update(costs=checks["common"]["costs"], economics=checks["economics"],
                physical_passed=checks["common"]["passed"], residuals=checks["common"]["residuals"],
                native={k: v for k, v in native.items() if k not in {"x", "s", "z"}},
                restoration_diagnostics=checks["original_space_diagnostics"], forced_shedding=checks["forced_shedding"])
            summary["coordinate_checks"] = checks["coordinate_checks"]
            groups[arm.group][arm.treatment] = dict(result=checks["result"], common=checks["common"],
                accepted=attempt["accepted"], economics=checks["economics"], coordinate_checks=checks["coordinate_checks"])
        elif (source / f"call-{arm.id:03d}" / "result.json.gz").exists() and attempt.get("archive_state") != "incomplete_publication":
            native = q.read(source / f"call-{arm.id:03d}" / "result.json.gz").get("native", {})
            summary["native"] = {k: v for k, v in native.items() if k not in {"x", "s", "z"}}
        summaries.append(summary)
    comparisons, plots = {}, []
    for number, (group, candidates) in enumerate(groups.items(), 1):
        arm = next(a for a in f.arms() if a.group == group)
        _, kwargs, _ = f.kwargs_for_arm(arm)
        comparison = compare(candidates, kwargs)
        comparison["axes"]["hours"] = list(range(arm.start, arm.start+arm.T))
        comparison["axes"]["boundary_hours"] = list(range(arm.start, arm.start+arm.T+1))
        comparison["boundary_soc_mwh"] = {name: soc_boundaries(c["result"], kwargs) for name, c in candidates.items()}
        comparison["battery_throughput_mwh"] = {name: kwargs["delta"]*float(np.sum(abs(np.asarray(c["result"]["b"]))))
                                                for name, c in candidates.items()}
        comparison["economic_pairs"] = pair_metrics(candidates)
        comparison["note"] = "Same-formulation comparisons are descriptive; cycling warnings do not reject. SOCP is not AC realization."
        if not all(c["passed"] for checks in comparison["axis_checks"].values() for c in checks.values()):
            raise ValueError("device/time axis mismatch")
        comparisons[group] = comparison
        if make_plots:
            plots.append(plot_group(output, number, arm, candidates, kwargs))
    dispositions = {}
    for form in f.FORMS:
        dispositions[form] = {treatment: disposition([a for a in f.arms() if a.formulation == form and a.treatment == treatment],
                                                    progress["attempts"])
                             for treatment in ("baseline_original", "baseline_cost", "prepared_original", "prepared_cost")}
    value = serializable(dict(invocation=finish, progress={k: v for k, v in progress.items() if k != "attempts"},
        summaries=summaries, treatment_dispositions=dispositions, comparisons=comparisons, plots=plots,
        limitations="Bounded stated-model qualification, not default promotion, AC realizability, or a certified lower bound. Pair discrepancies require interpretation, not trajectory identity."))
    q.atomic_immutable_json(output / "analysis.json", value)
    return value
