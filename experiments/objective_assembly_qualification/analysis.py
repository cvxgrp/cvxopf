"""Read-only evidence replay, then fresh derived paired trajectory summaries.

No optimization or edits to evidence. Cost/trajectory comparisons require two
independently accepted arms; one-sided, missing and censored outcomes remain
visible. Figures compare treatments within a formulation, not different physics.
"""

import argparse
from pathlib import Path

import numpy as np

from experiments.numerical_preparation import run_qualification as q
from . import model as f, run

FIELDS = {"Pg": "Generator real output (MW)", "b": "Battery real output (MW)",
          "soc": "Stored energy (MWh)", "p_load_shed": "Load shedding (MW)"}


def differences(left, right):
    values = {}
    for name, units in FIELDS.items():
        a, b = np.asarray(left[name], float), np.asarray(right[name], float)
        if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError(f"invalid matched physical trajectory: {name}")
        values[name] = dict(units=units, shape=list(a.shape),
            maximum_absolute=float(np.max(abs(b-a), initial=0)),
            rms=float(np.sqrt(np.mean((b-a)**2))))
    return values


def paired_summary(report):
    attempts = {row["arm"]["id"]: row for row in report["attempts"]}
    rows = []
    for pair in report["pairs"]:
        arm = f.arms()[pair["arms"][0]-1]
        row = dict(group=arm.group, formulation=arm.formulation, T=arm.T,
                   input_hours=[arm.start, arm.start+arm.T], **pair)
        available = [attempts.get(n, {}) for n in pair["arms"]]
        # Timing is essential for timeout-versus-success pairs. Do not gate
        # timing, supervision/censoring or available warnings on joint acceptance.
        row.update(worker_seconds=[v.get("worker_seconds") for v in available],
            supervision=[v.get("classification", "not_launched") for v in available],
            accepted=[v.get("accepted", False) for v in available],
            cycling_warnings=[f.warning(v["checks"]) if v.get("checks") is not None else None
                              for v in available])
        if pair["both_accepted"]:
            a, b = (attempts[n] for n in pair["arms"])
            row["trajectories"] = differences(a["checks"]["result"], b["checks"]["result"])
        rows.append(row)
    return rows


def plot_pair(left, right, arm, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Per-generator, not just aggregate generation: a heatmap retains every
    # generator without an illegible overlay of dozens of individual lines.
    fig, axes = plt.subplots(2, 3, figsize=(14, 7), layout="constrained")
    a, b = (np.asarray(result["Pg"], float).T for result in (left, right))
    scale = max(float(np.max(abs(a))), float(np.max(abs(b))), 1.)
    delta = b-a
    extent = (arm.start, arm.start+arm.T, len(a)-.5, -.5)
    for ax, data, title, bound, cmap in zip(axes[0], (a, b, delta),
            ("Hourly assembly", "Component-first assembly", "Component-first minus hourly"),
            (scale, scale, max(float(np.max(abs(delta))), 1e-9)), ("viridis", "viridis", "RdBu_r"), strict=True):
        im = ax.imshow(data, aspect="auto", extent=extent, cmap=cmap,
                       vmin=-bound if cmap == "RdBu_r" else 0, vmax=bound)
        ax.set(title=title, ylabel="Generator (input order)", xlabel="Global input hour")
        fig.colorbar(im, ax=ax, label="MW")
    for ax, name in zip(axes[1], ("b", "soc", "p_load_shed"), strict=True):
        for result, style, label in ((left, "-", "hourly"), (right, "--", "component-first")):
            values = np.asarray(result[name], float)
            # Published SoC is post-step (the initial boundary is not included).
            # Power rates occupy [hour, hour+1); extend the last step to its end.
            if name == "soc":
                clock = arm.start+1+np.arange(len(values))
            else:
                values = np.vstack((values, values[-1]))
                clock = arm.start+np.arange(len(values))
            for device, column in enumerate(values.T):
                ax.plot(clock, column, linestyle=style, color=f"C{device % 10}",
                        label=f"{label}: {device}" if name != "p_load_shed" else None,
                        drawstyle="default" if name == "soc" else "steps-post")
        ax.set(title=FIELDS[name], xlabel="Global input hour")
        if name != "p_load_shed":
            ax.legend(fontsize="small")
    fig.suptitle(arm.group+" — accepted physical trajectories; local/nonunique schedules may differ")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def analyze(root):
    finish = q.read(root / "invocation-finish.json")  # No analysis of a live source-bound run.
    report = run.status(root)  # Independent checks, not a cached report's labels.
    pairs = paired_summary(report)
    output = root / "analysis"
    output.mkdir(exist_ok=False)
    q.atomic_immutable_json(output / "summary.json", dict(invocation=finish, pairs=pairs,
        counts={k: report[k] for k in ("expected_cases", "finalized", "accepted", "rejected", "timeouts",
            "cycling_warnings", "result_archives", "native_archives", "completion_manifests")}))
    attempts = {row["arm"]["id"]: row for row in report["attempts"]}
    for pair in pairs:
        if pair["both_accepted"]:
            a, b = pair["arms"]
            plot_pair(attempts[a]["checks"]["result"], attempts[b]["checks"]["result"], f.arms()[a-1],
                      output / f"pair-{a:03d}-{b:03d}.png")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=run.OUTPUT)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.parent != run.HERE / "results":
        parser.error("analysis input must be a direct child of this experiment's results directory")
    print(analyze(root))
