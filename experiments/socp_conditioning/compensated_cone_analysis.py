"""Offline reconstruction of the bounded compensated-cone trial."""

import argparse
from decimal import Decimal, localcontext
import gzip
import json
from pathlib import Path
import re

from experiments.socp_conditioning import compensated_cone as c
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import native_step_analysis as trace


def analyze(root):
    summary = json.loads((root / "summary.json").read_text())
    folder = root / "compensated"
    sup = json.loads((folder / "supervision.json").read_text())
    for name, digest in sup["artifacts"].items():
        if d.sha(folder / name) != digest:
            raise ValueError("supervised artifact changed")
    if d.sha(folder / "supervision.json") != summary["supervision_sha256"]:
        raise ValueError("supervision identity changed")
    for name, key in (
        ("native-fixtures.json", "native_fixtures_sha256"),
        ("engine.json", "engine_sha256"),
    ):
        if d.sha(root / name) != summary[key]:
            raise ValueError("root artifact changed")
    binding = json.loads((root / "binding.json").read_text())
    if binding != summary["context"]:
        raise ValueError("context mismatch")
    record = json.load(gzip.open(folder / "arm.json.gz", "rt"))
    prior = json.load(gzip.open(c.PRIOR / "arm.json.gz", "rt"))
    if d.sha(c.PRIOR / "arm.json.gz") != c.PRIOR_SHA:
        raise ValueError("reference changed")
    c.lu.k.validate_arm(record, binding, prior, "superlu")
    fixture = json.loads((root / "native-fixtures.json").read_text())
    if c.validate_fixtures(fixture["stdout"]) != fixture["fixtures"]:
        raise ValueError("native fixture summary mismatch")
    log = (folder / "worker.log").read_text()
    events = c.fallbacks(log)
    if events != summary["fallbacks"]:
        raise ValueError("fallback summary mismatch")
    for event in events:
        with localcontext() as ctx:
            ctx.prec = 90
            actual = (
                Decimal.from_float(event["scaled_det"])
                * Decimal.from_float(event["scale"]) ** 2
            )
            exact = Decimal(event["exact_determinant"])
            event["relative_determinant_error"] = (
                str(abs(actual - exact) / abs(exact)) if exact else None
            )
    vectors = []
    # The final refinement exits on NaN before emitting its usual final record.
    # Retain that incomplete evidence rather than demanding a successful trace.
    for line in log.splitlines():
        if line.startswith("TRACE VECTORS"):
            row = re.search(r"rows=(\d+)\.\.(\d+)", line)
            v = {
                k: json.loads(value)
                for k, value in re.findall(r"(\w+)=(\[[^]]*\])", line)
            }
            vectors.append(
                v
                | dict(
                    rows=[int(row[1]), int(row[2])],
                    dual_boundary=trace.soc_boundary(v["z"], v["dz"]),
                )
            )
    late = log.split("TRACE COMPENSATED", 1)[1]
    return dict(
        schema=1,
        promotional=False,
        optimizer_calls=0,
        summary_sha256=d.sha(root / "summary.json"),
        analysis_source_sha256=d.sha(__file__),
        fixture_count=len(fixture["fixtures"]),
        fallbacks=events,
        unchanged_before_fallback=c.prefix_matches(
            record["trace"], prior["trace"], events[0]["iteration"]
        ),
        final_vectors_unchanged=c.n.same_solution(
            record["raw_clarabel"], prior["raw_clarabel"]
        ),
        later_zero_steps=vectors,
        late_linear_evidence=[
            line
            for line in late.splitlines()
            if line.startswith(("TRACE REFINE", "TRACE LU_ERROR", "TRACE STEP"))
        ],
        bridge=c.lu.bridge_summary(log),
        native=record["native"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.root)
    d.publish(args.root / "analysis.json", result)
    print(json.dumps(result, indent=2))
