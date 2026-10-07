"""Read-only reconstruction of native cone-step and KKT refinement evidence."""

import argparse
from decimal import Decimal, localcontext
import gzip
import hashlib
import json
from pathlib import Path
import re

import numpy as np

from experiments.socp_conditioning import cone_scaling_analysis as ca
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import native_step_probe as probe
from experiments.socp_conditioning import no_shedding as n
from experiments.socp_conditioning import termination_probe as t


def soc_boundary(x, direction):
    """High precision boundary distance for the exact retained binary64 values."""
    with localcontext() as ctx:
        ctx.prec = 90
        x, y = ([Decimal.from_float(float(v)) for v in a] for a in (x, direction))
        a = y[0] ** 2 - sum(v * v for v in y[1:])
        b = 2 * (x[0] * y[0] - sum(v * w for v, w in zip(x[1:], y[1:])))
        c = x[0] ** 2 - sum(v * v for v in x[1:])
        margin = x[0] - sum(v * v for v in x[1:]).sqrt()
        roots = []
        disc = b * b - 4 * a * c
        if a and disc >= 0:
            q = (-b - disc.sqrt()) / 2 if b >= 0 else (-b + disc.sqrt()) / 2
            if q:
                roots = [q / a, c / q]
        elif not a and b:
            roots = [-c / b]
        if y[0] < 0:
            roots.append(-x[0] / y[0])
        positive = [v for v in roots if v >= 0]
        return dict(
            cone_margin=str(margin),
            determinant=str(c),
            first_nonnegative_boundary=float(min(positive)) if positive else None,
            direction_inf=max(abs(float(v)) for v in y),
        )


def parse_trace(text):
    iteration, solves, steps, limits, vectors = -1, [], [], [], []
    for line in text.splitlines():
        if line.startswith("TRACE ITER"):
            iteration = int(re.search(r"iter=(\d+)", line)[1])
        elif line.startswith("TRACE REFINE initial="):
            fields = dict(re.findall(r"(\w+)=([^ ]+)", line))
            solves.append(
                dict(
                    iteration=iteration,
                    candidates=[],
                    **{k: float(v) for k, v in fields.items()},
                )
            )
        elif line.startswith("TRACE REFINE candidate="):
            solves[-1]["candidates"].append(
                float(re.search(r"candidate=([^ ]+)", line)[1])
            )
        elif line.startswith("TRACE REFINE final="):
            fields = dict(re.findall(r"(\w+)=([^ ]+)", line))
            r = solves[-1]
            r.update(
                final=float(fields["final"]),
                meets_tolerance=fields["meets_tolerance"] == "true",
            )
            r["relative_to_rhs"] = r["final"] / max(r["rhs_inf"], 1e-300)
            r["tolerance_ratio"] = r["final"] / r["tolerance"]
        elif line.startswith("TRACE LIMIT"):
            fields = dict(re.findall(r"(\w+)=([^ ]+)", line))
            limits.append(dict(iteration=iteration, **fields))
        elif line.startswith("TRACE VECTORS"):
            row = re.search(r"rows=(\d+)\.\.(\d+)", line)
            fields = {
                k: json.loads(v) for k, v in re.findall(r"(\w+)=(\[[^]]*\])", line)
            }
            vectors.append(
                dict(iteration=iteration, rows=[int(row[1]), int(row[2])], **fields)
            )
        elif line.startswith("TRACE STEP"):
            fields = dict(re.findall(r"(\w+)=([^ ]+)", line))
            steps.append(
                dict(
                    iteration=iteration,
                    direction=fields.pop("direction"),
                    **{k: float(v) for k, v in fields.items()},
                )
            )
    if not steps or any("final" not in r for r in solves):
        raise ValueError("missing native trace evidence")
    return solves, steps, limits, vectors


def analyze(root, reference):
    summary = json.loads((root / "summary.json").read_text())
    binding = json.loads((root / "binding.json").read_text())
    engine = json.loads((root / "engine.json").read_text())
    if (
        summary["context"] != binding
        or hashlib.sha256(json.dumps(engine, sort_keys=True).encode()).hexdigest()
        != binding["native_engine_sha256"]
    ):
        raise ValueError("root/engine identity mismatch")
    for path, digest in (binding["sources"] | binding["additional_sources"]).items():
        if d.sha(d.ROOT / path) != digest:
            raise ValueError(f"source changed: {path}")
    if d.sha(probe.BASELINE / "arm.json.gz") != binding["baseline_sha256"]:
        raise ValueError("baseline changed")
    baseline = json.load(gzip.open(probe.BASELINE / "arm.json.gz", "rt"))
    records = {}
    for arm in summary["arms"]:
        folder = root / arm["mode"]
        sup = json.loads((folder / "supervision.json").read_text())
        if d.sha(folder / "supervision.json") != arm["supervision_sha256"]:
            raise ValueError("supervision changed")
        for name, digest in sup["artifacts"].items():
            if d.sha(folder / name) != digest:
                raise ValueError("artifact changed")
        record = json.load(gzip.open(folder / "arm.json.gz", "rt"))
        if (
            record["context"] != binding
            or record["context_after"] != binding
            or not probe.same_solution(baseline["raw_clarabel"], record["raw_clarabel"])
        ):
            raise ValueError("exact replay evidence mismatch")
        records[arm["mode"]] = record
    log = (root / "instrumented/worker.log").read_text()
    native = json.loads((root / "instrumented/native_input.json").read_text())
    if int(re.search(r"constraints\s*=\s*(\d+)", log)[1]) != native["A"]["m"]:
        raise ValueError("native row elimination requires additional mapping")
    solves, steps, limits, vectors = parse_trace(log)
    old = json.load(gzip.open(t.PRIOR / "arm.json.gz", "rt"))
    if d.sha(t.PRIOR / "arm.json.gz") != t.PRIOR_SHA:
        raise ValueError("canonical map changed")
    data = ca.matrices(t.PRIOR / "canonical.npz")
    if d.sha(t.PRIOR / "canonical.npz") != old["canonical_archive"]["sha256"]:
        raise ValueError("canonical matrix changed")
    A = data["A"].tocsr()
    kwargs, historical = d.load_case("surplus", reference)
    build, _, _ = d.build_variant(n.fixed_inputs(kwargs), "fixed_boxes")
    variable_schema = old["canonical_archive"]["variables"]
    locations = []
    for v in vectors:
        rows = old["substitution"]["retained_rows"][slice(*v["rows"])]
        entries = []
        for row in rows:
            z = A.getrow(row)
            for col, val in zip(z.indices, z.data):
                spec = next(
                    s
                    for s in variable_schema
                    if s["offset"] <= col < s["offset"] + np.prod(s["shape"])
                )
                entries.append(
                    dict(
                        row=row,
                        variable=spec["name"],
                        coordinate=[
                            int(i)
                            for i in np.unravel_index(
                                col - spec["offset"], spec["shape"], order="F"
                            )
                        ],
                        coefficient=float(val),
                    )
                )
        block = next(
            c
            for c in old["canonical_archive"]["constraints"]
            if c["start"] <= rows[0] < c["stop"]
        )
        soc_blocks = [
            c for c in old["canonical_archive"]["constraints"] if c["kind"] == "SOC"
        ]
        position = soc_blocks.index(block)
        if position == 0:
            k = next(e["coordinate"] for e in entries if e["variable"] == "W_re")
            pair, hour = k
            internal = build.data["voltage_product_pairs"][pair]
            inv = {int(v): int(k) for k, v in build.data["ext_to_int"].items()}
            physical = dict(
                family="voltage_product_SOC",
                buses=[inv[int(b)] for b in internal],
                local_interval=hour,
            )
        elif position in (1, 2):
            ordinal = (rows[0] - block["start"]) // 3
            branches = build.data["constrained_branch_indices"]
            hour, local = divmod(ordinal, len(branches))
            branch = int(branches[local])
            physical = dict(
                family="branch_apparent_power_SOC",
                branch_index=branch,
                buses=[
                    int(build.data[k][branch])
                    for k in ("branch_from_bus_external", "branch_to_bus_external")
                ],
                terminal="from" if position == 1 else "to",
                local_interval=hour,
                rating_mva=float(build.data["branch_rate_a_mva"][branch]),
            )
        else:
            physical = dict(family="device_SOC")
        if "local_interval" in physical:
            physical["global_interval"] = (
                historical["arm"]["start"] + physical["local_interval"]
            )
        locations.append(
            v
            | dict(
                original_rows=rows,
                canonical_entries=entries,
                physical=physical,
                primal_boundary=soc_boundary(v["s"], v["ds"]),
                dual_boundary=soc_boundary(v["z"], v["dz"]),
            )
        )
    return dict(
        schema=1,
        promotional=False,
        optimizer_calls=0,
        root_sha256=d.sha(root / "summary.json"),
        log_sha256=d.sha(root / "instrumented/worker.log"),
        analysis_source_sha256=d.sha(__file__),
        exact_replay_verified=True,
        native_status=records["instrumented"]["native"],
        refinement=solves,
        steps=steps,
        limiting_cones=limits,
        near_zero_steps=locations,
        refinement_failed_count=sum(not r["meets_tolerance"] for r in solves),
        peak_combined_rss_mib={
            k: r["native_combined_peak_rss_mib"] for k, r in records.items()
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.root, args.reference)
    d.publish(args.root / "trace-analysis.json", result)
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("refinement", "steps", "limiting_cones", "near_zero_steps")
            },
            indent=2,
        )
    )
