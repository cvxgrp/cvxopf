"""Offline exact-input cone arithmetic and physical row mapping; no solves."""

import argparse
from decimal import Decimal, localcontext
import gzip
import json
from pathlib import Path
import re

import numpy as np

from experiments.socp_conditioning import cone_interiority as c
from experiments.socp_conditioning import cone_scaling_analysis as ca
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import native_step_analysis as trace
from experiments.socp_conditioning import no_shedding as n
from experiments.socp_conditioning import termination_probe as t


def stable_norm(values):
    """Mirror the native overflow-safe norm; no NumPy reduction reordering."""
    scale, sumsq = 0.0, 1.0
    for value in values:
        if value == 0:
            continue
        absolute = abs(float(value))
        if scale < absolute:
            r = scale / absolute
            scale, sumsq = absolute, 1.0 + sumsq * r * r
        else:
            r = absolute / scale
            sumsq += r * r
    return scale * sumsq**0.5


def step_evidence(failure, name):
    previous = failure["previous"]
    x, direction = previous[name], previous["d" + name]
    alpha = failure["preceding_applied_alpha"]
    if failure["following_rescale_inverse"] is not None:
        raise ValueError("unexpected homogeneous rescaling requires explicit mapping")
    rounded = [alpha * dv + xv for xv, dv in zip(x, direction, strict=True)]
    if rounded != failure[name]:
        raise ValueError("recorded preceding step does not reproduce failed state")
    with localcontext() as ctx:
        ctx.prec = 90
        exact = [
            Decimal.from_float(xv) + Decimal.from_float(alpha) * Decimal.from_float(dv)
            for xv, dv in zip(x, direction, strict=True)
        ]
        margin = exact[0] - sum(v * v for v in exact[1:]).sqrt()
        determinant = exact[0] ** 2 - sum(v * v for v in exact[1:])
    return dict(
        rounded_update_matches=True,
        exact_arithmetic_update_margin=str(margin),
        exact_arithmetic_update_determinant=str(determinant),
        boundary=trace.soc_boundary(x, direction),
    )


def analyze(root, reference):
    summary = json.loads((root / "summary.json").read_text())
    folder = root / "instrumented"
    sup = json.loads((folder / "supervision.json").read_text())
    if d.sha(folder / "supervision.json") != summary["supervision_sha256"]:
        raise ValueError("supervision changed")
    for name, digest in sup["artifacts"].items():
        if d.sha(folder / name) != digest:
            raise ValueError("artifact changed")
    binding = json.loads((root / "binding.json").read_text())
    if (
        binding != summary["context"]
        or d.sha(root / "engine.json") != summary["engine_sha256"]
    ):
        raise ValueError("root identity changed")
    record = json.load(gzip.open(folder / "arm.json.gz", "rt"))
    prior = json.load(gzip.open(c.PRIOR / "arm.json.gz", "rt"))
    if d.sha(c.PRIOR / "arm.json.gz") != c.PRIOR_SHA:
        raise ValueError("prior changed")
    c.verify_replay(record, binding, prior)
    log = (folder / "worker.log").read_text()
    failure = c.parse_failure(log)
    if failure != summary["failure"]:
        raise ValueError("trace summary changed")
    native = json.loads((folder / "native_input.json").read_text())
    if int(re.search(r"constraints\s*=\s*(\d+)", log)[1]) != native["A"]["m"]:
        raise ValueError("native eliminated rows; additional mapping required")
    # Zero/nonnegative/SOC-only input: no chordal decomposition; no native row
    # elimination means ordered canonical rows survive native equilibration.
    start = 0
    ranges = []
    for cone in native["cones"]:
        kind, size = next(iter(cone.items()))
        ranges.append((start, start + size, kind))
        start += size
    lo, hi = failure["rows"]
    if (lo, hi, "SecondOrderConeT") not in ranges:
        raise ValueError("failed native SOC range mismatch")
    old = json.load(gzip.open(t.PRIOR / "arm.json.gz", "rt"))
    if d.sha(t.PRIOR / "arm.json.gz") != t.PRIOR_SHA:
        raise ValueError("canonical row map changed")
    if d.sha(t.PRIOR / "canonical.npz") != old["canonical_archive"]["sha256"]:
        raise ValueError("canonical matrix changed")
    rows = old["substitution"]["retained_rows"][lo:hi]
    A = ca.matrices(t.PRIOR / "canonical.npz")["A"].tocsr()
    entries = []
    for row in rows:
        vals = A.getrow(row)
        for col, value in zip(vals.indices, vals.data, strict=True):
            spec = next(
                s
                for s in old["canonical_archive"]["variables"]
                if s["offset"] <= col < s["offset"] + np.prod(s["shape"])
            )
            entries.append(
                dict(
                    row=row,
                    variable=spec["name"],
                    coefficient=float(value),
                    coordinate=[
                        int(v)
                        for v in np.unravel_index(
                            col - spec["offset"], spec["shape"], order="F"
                        )
                    ],
                )
            )
    block = next(
        s
        for s in old["canonical_archive"]["constraints"]
        if s["start"] <= rows[0] < s["stop"]
    )
    blocks = [s for s in old["canonical_archive"]["constraints"] if s["kind"] == "SOC"]
    if block != blocks[0]:
        raise ValueError("different physical cone family requires explicit mapping")
    pair, hour = next(e["coordinate"] for e in entries if e["variable"] == "W_re")
    kwargs, historical = d.load_case("surplus", reference)
    build, _, _ = d.build_variant(n.fixed_inputs(kwargs), "fixed_boxes")
    inv = {int(v): int(k) for k, v in build.data["ext_to_int"].items()}
    buses = [inv[int(v)] for v in build.data["voltage_product_pairs"][pair]]
    norms = {name: stable_norm(failure[name][1:]) for name in ("s", "z")}
    recorded = {
        key: float(value)
        for key, value in re.findall(r"([sz]norm)=([^ ]+)", failure["check"])
    }
    if any(norms[name] != recorded[name + "norm"] for name in norms):
        raise ValueError("native norm reconstruction differs")
    return dict(
        schema=1,
        optimizer_calls=0,
        promotional=False,
        summary_sha256=d.sha(root / "summary.json"),
        analysis_source_sha256=d.sha(__file__),
        physical=dict(
            family="voltage_product_SOC",
            buses=buses,
            pair_index=pair,
            local_interval=hour,
            global_interval=historical["arm"]["start"] + hour,
        ),
        original_rows=rows,
        canonical_entries=entries,
        failure=failure,
        native_norm_reconstruction=norms,
        step={name: step_evidence(failure, name) for name in ("s", "z")},
        native_determinant={
            name: (failure[name][0] - norms[name]) * (failure[name][0] + norms[name])
            for name in norms
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.root, args.reference)
    d.publish(args.root / "analysis.json", result)
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("canonical_entries", "failure")
            },
            indent=2,
        )
    )
