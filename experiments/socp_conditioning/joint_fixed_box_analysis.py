"""Offline comparison of the three fixed-box arms; never runs an optimizer.

The original parent stopped during its final gap comparison. Reconstruct from
the three complete immutable worker records without recreating a run summary
or hiding that failed check. Analysis provenance is separate from execution.
"""

import argparse
import json
from pathlib import Path

import numpy as np

from experiments.socp_conditioning import cone_scaling as c
from experiments.socp_conditioning import cone_scaling_analysis as ca
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import joint_fixed_boxes as f
from experiments.socp_conditioning import joint_scaling as j
from experiments.socp_conditioning import matrix_conditioning as m
from experiments.socp_conditioning import optimality_audit as oa


def analyze(root):
    binding = json.loads((root / "binding.json").read_text())
    arms = []
    for label, (_, prior) in f.ARMS.items():
        directory = root / label
        r = m.verified_record(directory)
        sup = json.loads((directory / "supervision.json").read_text())
        if (
            sup["classification"] != "completed"
            or sup["returncode"] != 0
            or r["context"] != binding
            or r["context_after"] != binding
            or r["optimizer_calls"] != 1
        ):
            raise ValueError("incomplete execution")
        for name, digest in sup["artifacts"].items():
            if d.sha(directory / name) != digest:
                raise ValueError("artifact mismatch")
        old_dir = d.HERE / "results" / prior
        old = m.verified_record(old_dir)
        for folder, record in ((directory, r), (old_dir, old)):
            if d.sha(folder / "canonical.npz") != record["canonical_archive"]["sha256"]:
                raise ValueError("canonical hash mismatch")
        if r["reference"]["arm_sha256"] != d.sha(old_dir / "arm.json.gz"):
            raise ValueError("reference changed")
        if r["input_sha256"] != old["input_sha256"]:
            raise ValueError("physical input changed")
        source, target = (
            ca.matrices(p / "canonical.npz") for p in (old_dir, directory)
        )
        source["dims"] = old["canonical_archive"]["cone_dimensions"]
        target["dims"] = r["canonical_archive"]["cone_dimensions"]
        cleanup = f.check_cleanup(source, target, old["canonical_archive"]["variables"])
        if f.variable_schema(
            old["canonical_archive"]["variables"]
        ) != f.variable_schema(r["canonical_archive"]["variables"]):
            raise ValueError("variable layout changed")
        R, D = j.joint_scales(
            target, oa.cone_layout(target["dims"], target["A"].shape[0])
        )
        with np.load(directory / "scales.npz", allow_pickle=False) as saved:
            np.testing.assert_array_equal(saved["R"], R)
            np.testing.assert_array_equal(saved["D"], D)
        expected, actual = (
            c.transform(target, R, D),
            ca.matrices(directory / "transformed.npz"),
        )
        for key in ("A", "P"):
            np.testing.assert_array_equal((expected[key] - actual[key]).data, 0)
        for key in ("b", "c"):
            np.testing.assert_array_equal(expected[key], actual[key])
        raw, mapped = r["transformed_native_solution"], r["native_solution"]
        for key, value in (
            ("x", D * raw["x"]),
            ("s", np.asarray(raw["s"]) / R),
            ("z", R * raw["z"]),
        ):
            np.testing.assert_array_equal(mapped[key], value)
        now = oa.analyze(directory)
        before = oa.analyze(old_dir)
        if set(now["fixed_box_multipliers"]) & {"Pg", "p_nd"}:
            raise ValueError("fixed device boxes remain")
        arms.append(
            dict(
                label=label,
                cleanup=cleanup,
                hashes_and_mappings_verified=True,
                arm_sha256=d.sha(directory / "arm.json.gz"),
                supervision_sha256=d.sha(directory / "supervision.json"),
                reference_arm_sha256=d.sha(old_dir / "arm.json.gz"),
                accepted=r["accepted"],
                physical_audit=r["audit"],
                native={k: v for k, v in mapped.items() if k not in ("x", "s", "z")},
                native_info=r["native_info"],
                objective=r["result"]["objective"],
                timings=r["timings"],
                peak_rss_mib=sup["peak_rss_mib"],
                optimality=now,
                previous_optimality=before,
            )
        )
    return dict(
        schema=1,
        execution_context=binding,
        analysis_context=d.context(),
        analysis_sources={
            str(Path(p).relative_to(d.ROOT)): d.sha(p) for p in (__file__, oa.__file__)
        },
        binding_sha256=d.sha(root / "binding.json"),
        original_parent_outcome="postprocessing_gap_assertion_after_three_completed_solves",
        optimization_calls=0,
        promotional=False,
        arms=arms,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.root / "offline_analysis.json"
    d.publish(output, analyze(args.root))
    print(output, d.sha(output))
