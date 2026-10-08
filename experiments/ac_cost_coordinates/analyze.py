"""Replay retained evidence and plot matched physical trajectories; never solve."""

import argparse
import json
import os
from pathlib import Path
import re

# Match the execution environment before importing numerical libraries.
if __name__ == "__main__":
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[key] = "1"

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from cvxpy.reductions.solvers.nlp_solvers.nlp_solver import Oracles

from experiments.numerical_preparation import run_qualification as q
from experiments.numerical_preparation.tracy_variables import compare
from . import model as m, run as r

LABELS = ("Original", "Cost coordinates", "Prepared original", "Prepared cost coordinates")


def analyze(root):
    # Refuse to summarize a changing live run or bypass supervision/hash replay.
    finish = q.read(root/"invocation-finish.json")
    status = r.status(root)
    summaries, candidates = [], {}
    for attempt in status["attempts"]:
        number = attempt["arm"]
        directory = root/f"call-{number:03d}"
        log = (directory/"worker.log").read_text()
        iterations = re.findall(r"Number of Iterations\.*:\s+(\d+)", log)
        exits = re.findall(r"EXIT: (.*)", log)
        summary = {k: v for k, v in attempt.items() if k != "checks"}
        summary.update(label=LABELS[number-1], iterations=int(iterations[-1]) if iterations else None,
                       solver_exit=exits[-1] if exits else None)
        if "checks" in attempt:
            checks = attempt["checks"]
            summary.update(accounting=checks["accounting"], costs=checks["common"]["costs"],
                           residuals=checks["common"]["residuals"], transformation=checks["transformation"])
            record = q.read(directory/"result.json.gz")
            call, kwargs, view, _ = m.construct(attempt["historical_call"], attempt["scaled"])
            data, _ = m.canonical_data(view.solver)
            x0 = np.asarray(record["captured"]["complete_x0"])
            if not np.array_equal(x0, data["x0"]):
                raise ValueError("fresh canonical initialization differs from retained x0")
            if record["preparation_evidence"]:
                x0 = np.asarray(record["preparation_evidence"]["adjusted_x0"])
            oracle = Oracles(data["_bounds"].new_problem, verbose=False, use_hessian=True)
            oracle.objective(x0)
            maximum = float(np.max(abs(oracle.gradient(x0))))
            summary.update(initial_maximum_objective_gradient=maximum,
                           inferred_default_gradient_objective_scale=min(1., 100./maximum))
            candidates[LABELS[number-1]] = dict(result=checks["result"])
        summaries.append(summary)
    comparison = compare(candidates, kwargs) if candidates else None
    q.atomic_immutable_json(root/"analysis.json", dict(invocation=finish, summaries=summaries,
        comparison=comparison, scaling_note="Scale inferred from default gradient-based max-gradient 100 rule; not native telemetry."))
    if candidates:
        plots(root, candidates, kwargs)
    return summaries


def soc_boundaries(result, kwargs):
    """Public SoC contains only T end states; prepend the frozen input state."""
    end = np.asarray(result["soc"], float)
    if end.shape != (kwargs["T"], len(kwargs["storage"])):
        raise ValueError("unexpected end-state SoC shape")
    return np.vstack(([unit.initial_soc for unit in kwargs["storage"]], end))


def plots(root, candidates, kwargs):
    hours, boundaries = np.arange(1165, 1168), np.arange(1165, 1169)
    fig, axes = plt.subplots(3, 2, figsize=(11, 10), sharex=True, layout="constrained")
    panels = [("Pg", "Conventional generation (MW)", False),
              ("b", "Battery net real discharge (MW)", False),
              ("b", "Battery absolute throughput (MW)", True),
              ("soc", "Fleet state of charge (MWh)", False),
              ("b_q", "Battery reactive injection (MVAr)", False),
              ("p_load_shed", "Real load shed (MW)", False)]
    for label, candidate in candidates.items():
        result = candidate["result"]
        for axis, (key, title, absolute) in zip(axes.flat, panels, strict=True):
            value = soc_boundaries(result, kwargs) if key == "soc" else np.asarray(result[key], float)
            y = np.sum(abs(value) if absolute else value, axis=1)
            x = boundaries if key == "soc" else hours
            axis.plot(x, y, marker="o", label=label)
            axis.set_title(title)
            axis.grid(alpha=.25)
    for axis in axes[-1]:
        axis.set_xlabel("Global input hour (SoC: boundary hour)")
    axes[0, 0].legend(fontsize=8)
    fig.suptitle("Tracy T=3 AC: matched physical trajectories\nNet fleet sums may hide device counterflows; see device panels")
    fig.savefig(root/"fleet-trajectories.png", dpi=160)
    plt.close(fig)
    first = next(iter(candidates.values()))["result"]
    ids = first["storage_device_ids"]
    fig, axes = plt.subplots(9, 3, figsize=(12, 21), sharex=True, layout="constrained")
    for index, axis in enumerate(axes.flat):
        if index >= len(ids):
            axis.set_visible(False)
            continue
        for label, candidate in candidates.items():
            if candidate["result"]["storage_device_ids"] != ids:
                raise ValueError("unaligned storage identities")
            axis.plot(hours, np.asarray(candidate["result"]["b"])[:, index], marker=".", label=label)
        axis.set_title(str(ids[index]), fontsize=9)
        axis.set_ylabel("Discharge MW")
        axis.grid(alpha=.25)
    axes[0, 0].legend(fontsize=6)
    fig.suptitle("Device-aligned battery real dispatch; signed positive = discharge")
    fig.savefig(root/"battery-device-trajectories.png", dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=r.OUTPUT)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.parent != r.HERE/"results":
        parser.error("output must be inside the experiment results directory")
    summaries = analyze(root)
    print(json.dumps([{k: v for k, v in s.items() if k not in {"residuals"}} for s in summaries], indent=2))


if __name__ == "__main__":
    main()
