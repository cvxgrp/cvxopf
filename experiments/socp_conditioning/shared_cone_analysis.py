"""Offline reconstruction of the shared-determinant replay; no optimization."""

import argparse
import gzip
import json
from pathlib import Path
import re

import numpy as np

from experiments.socp_conditioning import cone_interiority_analysis as ca
from experiments.socp_conditioning import shared_cone as s


def analyze(root, reference):
    d = s.d
    summary = json.loads((root / "summary.json").read_text())
    folder = root / "shared"
    for name, key in (
        ("engine.json", "engine_sha256"),
        ("native-fixtures.json", "fixtures_sha256"),
        ("shared/supervision.json", "supervision_sha256"),
        ("shared/arm.json.gz", "arm_sha256"),
    ):
        if d.sha(root / name) != summary[key]:
            raise ValueError("summary artifact changed")
    sup = json.loads((folder / "supervision.json").read_text())
    for name, digest in sup["artifacts"].items():
        if d.sha(folder / name) != digest:
            raise ValueError("supervision artifact changed")
    binding = json.loads((root / "binding.json").read_text())
    if binding != summary["context"]:
        raise ValueError("binding changed")
    fixtures = json.loads((root / "native-fixtures.json").read_text())
    if s.validate_fixtures(fixtures["stdout"]) != fixtures["validation"]:
        raise ValueError("fixture validation changed")
    if d.sha(s.PRIOR / "arm.json.gz") != s.PRIOR_SHA:
        raise ValueError("reference changed")
    record = json.load(gzip.open(folder / "arm.json.gz", "rt"))
    prior = json.load(gzip.open(s.PRIOR / "arm.json.gz", "rt"))
    s.lu.k.validate_arm(record, binding, prior, "superlu")
    if record["native"] != summary["native"]:
        raise ValueError("native summary changed")
    log = (folder / "worker.log").read_text()
    failure = s.c.c.parse_failure(log)
    if failure != summary["cone_failure"]:
        raise ValueError("failure summary changed")
    native = json.loads((folder / "native_input.json").read_text())
    if int(re.search(r"constraints\s*=\s*(\d+)", log)[1]) != native["A"]["m"]:
        raise ValueError("native eliminated rows")
    lo, hi = failure["rows"]
    offset = 0
    ranges = []
    for cone in native["cones"]:
        kind, size = next(iter(cone.items()))
        ranges.append((offset, offset + size, kind))
        offset += size
    if (lo, hi, "SecondOrderConeT") not in ranges:
        raise ValueError("failed SOC range mismatch")
    old = json.load(gzip.open(s.t.PRIOR / "arm.json.gz", "rt"))
    if d.sha(s.t.PRIOR / "arm.json.gz") != s.t.PRIOR_SHA:
        raise ValueError("canonical mapping changed")
    if d.sha(s.t.PRIOR / "canonical.npz") != old["canonical_archive"]["sha256"]:
        raise ValueError("canonical matrix changed")
    rows = old["substitution"]["retained_rows"][lo:hi]
    matrix = ca.ca.matrices(s.t.PRIOR / "canonical.npz")["A"].tocsr()
    spec = next(v for v in old["canonical_archive"]["variables"] if v["name"] == "W_re")
    coordinates = set()
    for row in rows:
        for col in matrix.getrow(row).indices:
            if spec["offset"] <= col < spec["offset"] + np.prod(spec["shape"]):
                coordinates.add(
                    tuple(
                        int(v)
                        for v in np.unravel_index(
                            col - spec["offset"], spec["shape"], order="F"
                        )
                    )
                )
    if len(coordinates) != 1:
        raise ValueError("ambiguous physical cone mapping")
    pair, hour = coordinates.pop()
    kwargs, historical = d.load_case("surplus", reference)
    build, _, _ = d.build_variant(ca.n.fixed_inputs(kwargs), "fixed_boxes")
    inv = {int(v): int(k) for k, v in build.data["ext_to_int"].items()}
    return dict(
        schema=1,
        optimizer_calls=0,
        promotional=False,
        summary_sha256=d.sha(root / "summary.json"),
        analysis_sources={
            str(Path(p).relative_to(d.ROOT)): d.sha(p)
            for p in (__file__, ca.__file__, ca.trace.__file__, s.c.c.__file__)
        },
        physical=dict(
            family="voltage_product_SOC",
            pair_index=pair,
            buses=[inv[int(v)] for v in build.data["voltage_product_pairs"][pair]],
            local_interval=hour,
            global_interval=historical["arm"]["start"] + hour,
        ),
        original_rows=rows,
        step={name: ca.step_evidence(failure, name) for name in ("s", "z")},
        gap_improvement=prior["native"]["gap_abs"] / record["native"]["gap_abs"],
        failure=failure,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.root, args.reference)
    s.d.publish(args.root / "analysis.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "failure"}, indent=2))
