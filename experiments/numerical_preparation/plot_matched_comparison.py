"""Publication figures from retained matched evidence; native optimization forbidden.

Writes only a fresh analysis directory outside the execution tree. Reuses the
runner's independent replay and all-arm table, retaining every failed/censored
arm. The compact snapshot is sufficient to rerender without scientific replay.
"""

from __future__ import annotations

# ruff: noqa: E402 -- bind replay thread settings before numerical imports.

import argparse
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re

if __name__ == "__main__":
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[key] = "1"

from . import matched_model as model, matched_report, run_matched
from experiments.case118_tracy_2021.stage_d import read

OUTPUT = model.HERE / "figures/matched_comparison_001"
NEAR_ZERO = 1e-4  # Display threshold only; never an acceptance/economic threshold.
COLORS = {"baseline": "#D55E00", "prepared": "#0072B2"}
MARKERS = {"baseline": "s", "prepared": "o"}
CASES = ((1165, 3), (1165, 24), (2439, 3), (2439, 24))
LABELS = ("Deficit\nT = 3", "Deficit\nT = 24", "Transition\nT = 3", "Transition\nT = 24")


def tree_hashes(root):
    return {str(p.relative_to(root)): model.digest(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


def outcome(row):
    if row["accepted"]:
        return "accepted"
    if row["disposition"] == "wall_limit":
        return "timeout"
    if row["worker_classification"] == "native_rejected":
        return "native_rejected"
    if row["worker_classification"] == "audit_rejected":
        return "audit_rejected"
    raise ValueError(f"unsupported nonterminal/infrastructure outcome: {row['arm']['id']}")


def log_summary(path):
    lines = path.read_text(errors="replace").splitlines()
    iterations = next((int(m.group(1)) for line in lines
                       if (m := re.search(r"Number of Iterations\.*:\s*(\d+)", line))), None)
    summary = {}
    for line in lines:
        match = re.match(r"^(Objective|Dual infeasibility|Constraint violation|Variable bound violation|"
                         r"Complementarity|Overall NLP error)\.*:\s+([\deE.+-]+)\s+([\deE.+-]+)", line)
        if match:
            summary[match[1]] = dict(scaled=float(match[2]), unscaled=float(match[3]))
    message = next((line.strip() for line in lines
                    if line.startswith("EXIT:") or "Terminated with status" in line), None)
    return dict(iterations_from_log=iterations, message=message, ipopt_final_summary=summary)


def collect(root):
    """Fresh solver-forbidden replay; no acceptance inferred from file counts."""
    root = Path(root).resolve()
    before = tree_hashes(root)
    with model.no_optimization():
        progress = run_matched.status(root, audit=True)
        if not progress["matrix_complete"] or progress["pending_disposition"]:
            raise ValueError("publication extraction requires a terminal matched matrix")
        rows = matched_report.table(progress, root)
    binding = read(root / "binding.json")
    authority = read(root / "authority.json")
    if (authority["binding"]["sha256"] != model.digest(root / "binding.json") or
            authority["limits"] != binding["limits"] or
            read(root / "protocol.json") != dict(protocol=binding["limits"])):
        raise ValueError("binding, authority and protocol differ")
    for row in rows:
        attempt = next(a for a in reversed(progress["attempts"]) if a["arm_id"] == row["arm"]["id"])
        directory = root / attempt["path"]
        row.update(outcome=outcome(row), attempt_path=attempt["path"],
                   log_summary=log_summary(directory / "worker.log"))
        if row["log_summary"]["iterations_from_log"] is not None:
            row["iterations"] = row["log_summary"]["iterations_from_log"]
        record = read(directory / "result.json.gz") if (directory / "result.json.gz").exists() else {}
        audit = record.get("audit", {})
        row["economic_audit"] = audit.get("economics")
        row["physical_limits"] = audit.get("physical", {}).get("limits")
        row["policy"] = binding["rows"][row["arm"]["id"]-1]["policy"]
        row["public_solve_seconds"] = (row["timings"] or {}).get("phases", {}).get("public_solve")
        row["worker_limit_seconds"] = read(directory / "request.json")["wall_seconds"]
        if not row["accepted"]:
            row["unaccepted_candidate_physical_cost"] = row["physical_cost"]
            row["physical_cost"] = None
    if tree_hashes(root) != before:
        raise ValueError("execution tree changed during extraction")
    return dict(schema=1, execution_root=str(root),
        execution_commit=authority["execution_commit"],
        extracted_utc=datetime.now(timezone.utc).isoformat(), independently_replayed=True,
        raw_tree_sha256=before, packages=binding["context"]["packages"],
        ipopt_version=binding["context"]["ipopt_version"], platform=binding["context"]["platform"],
        numerical_threads=binding["context"]["thread_environment"], limits=binding["limits"],
        counts={k:progress[k] for k in ("accepted_arms", "disposed_arms", "reservations",
                                      "worker_seconds", "artifact_counts")},
        rows=rows, pairs=progress["pairs"],
        display=dict(near_zero_cost_band=NEAR_ZERO, cost_units="model cost units",
            timing_units="seconds", censored_clock="whole supervised worker",
            public_timeout="unavailable; no substituted native or whole-worker time"),
        analysis_sources={str(p.relative_to(model.ROOT)):model.digest(p) for p in
                          (Path(__file__), Path(matched_report.__file__))})


def cost_position(row):
    """Missing outcomes stay missing; near-zero clipping is display-only."""
    if row["outcome"] != "accepted":
        return None
    value = row["physical_cost"]
    if value is None or not math.isfinite(value) or value < 0:
        raise ValueError("accepted cost must be finite and nonnegative for this figure")
    return max(NEAR_ZERO, value)


def format_value(value):
    if value >= 100:
        return f"{value:,.0f}"
    if value >= .01:
        return f"{value:.3g}"
    return f"{value:.2e}"


def render(snapshot, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import LogLocator, NullFormatter

    output = Path(output).resolve()
    source = Path(snapshot["execution_root"]).resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("analysis output must be separate from the raw execution tree")
    output.mkdir(parents=True, exist_ok=False)
    plt.rcParams.update({"font.family": "DejaVu Serif", "font.size": 8,
        "axes.titlesize": 9, "axes.labelsize": 8, "xtick.labelsize": 7,
        "ytick.labelsize": 8, "axes.spines.top": False, "axes.spines.right": False,
        "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
        "svg.hashsalt": snapshot["raw_tree_sha256"]["binding.json"]})
    rows = snapshot["rows"]
    files = []

    def save(figure, stem):
        figure.canvas.draw()
        for extension in ("pdf", "svg", "png"):
            name = f"{stem}.{extension}"
            metadata = {"CreationDate": None, "ModDate": None} if extension == "pdf" else {}
            figure.savefig(output / name, dpi=400, bbox_inches="tight", facecolor="white", metadata=metadata)
            files.append(name)
        plt.close(figure)

    def axes_style(ax, title, limits):
        ax.set_title(title, loc="left", pad=8)
        ax.set_xscale("log")
        ax.set_xlim(*limits)
        ax.set_ylim(3.65, -.55)
        ax.set_yticks(range(4), LABELS)
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.xaxis.set_major_locator(LogLocator(base=10))
        ax.grid(axis="x", which="major", color=".86", linewidth=.5)
        ax.set_axisbelow(True)

    legend = [Line2D([], [], marker="s", color=COLORS["baseline"], linestyle="none",
                     markerfacecolor="white", label="Baseline accepted"),
              Line2D([], [], marker="o", color=COLORS["prepared"], linestyle="none",
                     label="Prepared accepted"),
              Line2D([], [], marker="x", color=COLORS["baseline"], linestyle="none",
                     label="Native rejection"),
              Line2D([], [], marker=">", color=COLORS["baseline"], linestyle="none",
                     label="Worker timeout")]

    fig, axes = plt.subplots(2, 2, figsize=(7.16, 5.3))
    fig.subplots_adjust(left=.13, right=.98, bottom=.11, top=.89, wspace=.38, hspace=.57)
    for i, form in enumerate(("ac", "socp")):
        for j, metric in enumerate(("public_solve_seconds", "supervised_seconds")):
            ax = axes[i, j]
            axes_style(ax, f"({chr(97+2*i+j)}) {'Full AC' if form=='ac' else 'Lifted SOCP'} / "
                       + ("public solve" if j == 0 else "whole worker"),
                       (4, 8000) if form == "ac" else (.08, 30))
            ax.set_xlabel("Elapsed time (s, logarithmic scale)")
            for row in (r for r in rows if r["arm"]["formulation"] == form):
                treatment = row["arm"]["treatment"]
                y = CASES.index((row["arm"]["start"], row["arm"]["T"])) + (-.15 if treatment == "baseline" else .15)
                state, value = row["outcome"], row[metric]
                if state == "timeout" and metric == "public_solve_seconds":
                    ax.text(.98, y, "TO; time N/A", transform=ax.get_yaxis_transform(),
                            ha="right", va="center", fontsize=7, color=COLORS[treatment])
                    continue
                if value is None or not math.isfinite(value) or value <= 0:
                    raise ValueError("missing or invalid returned timing")
                marker = ">" if state == "timeout" else MARKERS[treatment] if state == "accepted" else "x"
                ax.plot(value, y, marker=marker, color=COLORS[treatment], markersize=5,
                        markerfacecolor="white" if treatment == "baseline" and state == "accepted" else COLORS[treatment])
                left = value > (1500 if form == "ac" else 8)
                ax.annotate(format_value(value) + (" TO" if state == "timeout" else ""), (value, y),
                            xytext=(-5 if left else 5, 0), textcoords="offset points",
                            ha="right" if left else "left", va="center", fontsize=7)
    fig.legend(handles=legend, ncol=4, loc="upper center", bbox_to_anchor=(.5, .995),
               frameon=False, fontsize=7, handletextpad=.4, columnspacing=1.2)
    fig.text(.5, .015, "Failed returns are not accepted solves. TO: right-censored at the 1 h whole-worker limit.",
             ha="center", fontsize=7)
    save(fig, "matched_timing")

    fig, axes = plt.subplots(1, 2, figsize=(7.16, 3.15))
    fig.subplots_adjust(left=.13, right=.95, bottom=.28, top=.80, wspace=.40)
    for i, form in enumerate(("ac", "socp")):
        ax = axes[i]
        axes_style(ax, f"({chr(97+i)}) {'Full AC' if form=='ac' else 'Lifted SOCP'}", (4.5e-5, 2e7))
        ax.axvspan(4.5e-5, 1.35e-4, color=".93", zorder=0)
        ax.set_xticks([1e-4, 1e-2, 1, 1e2, 1e4, 1e6],
                     [r"$\leq10^{-4}$", r"$10^{-2}$", "1", r"$10^2$", r"$10^4$", r"$10^6$"])
        ax.set_xlabel("Physical total cost (model units, log scale)")
        for row in (r for r in rows if r["arm"]["formulation"] == form):
            treatment = row["arm"]["treatment"]
            y = CASES.index((row["arm"]["start"], row["arm"]["T"])) + (-.15 if treatment == "baseline" else .15)
            position = cost_position(row)
            if position is None:
                label = "TO: no accepted cost" if row["outcome"] == "timeout" else "Rejected: no accepted cost"
                ax.text(.99, y, label, transform=ax.get_yaxis_transform(), ha="right",
                        va="center", fontsize=6.5, color=COLORS[treatment])
                continue
            ax.plot(position, y, marker=MARKERS[treatment], color=COLORS[treatment], markersize=5,
                    markerfacecolor="white" if treatment == "baseline" else COLORS[treatment])
            left = position > 1e5
            ax.annotate(format_value(row["physical_cost"]), (position, y),
                        xytext=(-5 if left else 5, 0), textcoords="offset points",
                        ha="right" if left else "left", va="center", fontsize=7)
    fig.legend(handles=legend[:2], ncol=2, loc="upper center", frameon=False, fontsize=8)
    fig.text(.5, .04, "Shaded band displays costs ≤ 10⁻⁴ at its boundary; labels and data retain the actual values.\n"
             "Only accepted physical objectives are plotted; missing outcomes are not zero.",
             ha="center", fontsize=7, linespacing=1.6)
    save(fig, "matched_physical_cost")

    (output / "figure_data.json").write_text(json.dumps(snapshot, indent=2, allow_nan=False)+"\n")
    files.append("figure_data.json")
    columns = ("arm_id", "pair", "formulation", "scenario", "T", "treatment", "outcome",
               "public_solve_seconds", "supervised_seconds", "all_attempt_supervised_seconds",
               "physical_cost", "canonical_cost", "cycling_slack", "cycling_warning",
               "ens_mwh", "iterations", "sampled_peak_rss_mib", "native_status")
    with (output / "all_arms.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            flat = dict(row["arm"]) | {k:row.get(k) for k in columns if k not in row["arm"]}
            flat["arm_id"] = row["arm"]["id"]
            writer.writerow({k:flat[k] for k in columns})
    files.append("all_arms.csv")
    manifest = dict(schema=1, source_binding_sha256=snapshot["raw_tree_sha256"]["binding.json"],
        independently_replayed=snapshot["independently_replayed"],
        evidence_extracted_utc=snapshot.get("extracted_utc"),
        rendered_utc=datetime.now(timezone.utc).isoformat(),
        renderer_sha256=model.digest(Path(__file__)),
        raw_tree_sha256=snapshot["raw_tree_sha256"], analysis_sources=snapshot["analysis_sources"],
        matplotlib_version=matplotlib.__version__, files={name:model.digest(output / name) for name in files})
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False)+"\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=model.OUTPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--snapshot", type=Path, help="rerender an existing audited figure_data.json")
    args = parser.parse_args()
    source = args.source.resolve()
    before = tree_hashes(source)
    snapshot = read(args.snapshot) if args.snapshot else collect(source)
    if Path(snapshot["execution_root"]).resolve() != source:
        raise ValueError("snapshot/source mismatch")
    if before != snapshot["raw_tree_sha256"]:
        raise ValueError("raw execution identity differs from audited snapshot")
    output = render(snapshot, args.output)
    if tree_hashes(source) != before:
        raise ValueError("raw execution tree changed during plotting")
    print(json.dumps(dict(output=str(output), raw_files_verified=len(before), raw_tree_unchanged=True,
                         fresh_independent_replay=args.snapshot is None,
                         snapshot_independently_replayed=snapshot["independently_replayed"])))


if __name__ == "__main__":
    main()
