"""Offline validation of saved exact-substitution runs, without optimizer calls."""

import argparse
import json
from pathlib import Path

import numpy as np

from experiments.socp_conditioning import cone_scaling_analysis as ca
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import fixed_substitution as f
from experiments.socp_conditioning import joint_scaling as j
from experiments.socp_conditioning import matrix_conditioning as m
from experiments.socp_conditioning import optimality_audit as oa


def analyze(root):
    summary = json.loads((root / "summary.json").read_text())
    binding = json.loads((root / "binding.json").read_text())
    if summary["context"] != binding or [a["label"] for a in summary["arms"]] != list(
        f.ARMS
    ):
        raise ValueError("root mismatch")
    arms = []
    for item in summary["arms"]:
        folder = root / item["label"]
        r = m.verified_record(folder)
        for key, name in (
            ("arm_sha256", "arm.json.gz"),
            ("supervision_sha256", "supervision.json"),
            ("optimality_sha256", "optimality.json"),
        ):
            if d.sha(folder / name) != item[key]:
                raise ValueError("summary hash mismatch")
        sup = json.loads((folder / "supervision.json").read_text())
        for name, digest in sup["artifacts"].items():
            if d.sha(folder / name) != digest:
                raise ValueError("supervision artifact mismatch")
        if (
            sup["classification"] != "completed"
            or sup["returncode"] != 0
            or r["context"] != binding
            or r["context_after"] != binding
            or r["optimizer_calls"] != 1
        ):
            raise ValueError("execution mismatch")
        old_dir = d.HERE / "results/joint_fixed_boxes_001" / item["label"]
        before = m.verified_record(old_dir)
        if (
            d.sha(old_dir / "arm.json.gz") != r["reference"]["arm_sha256"]
            or before["input_sha256"] != r["input_sha256"]
        ):
            raise ValueError("comparator mismatch")
        if d.sha(folder / "canonical.npz") != before["canonical_archive"]["sha256"]:
            raise ValueError("original canonical problem changed")
        full, reduced, transformed = [
            ca.matrices(folder / name)
            for name in ("canonical.npz", "reduced.npz", "transformed.npz")
        ]
        sub = r["substitution"]
        fixed, free, rows, keep = [
            np.asarray(sub[k], dtype=int)
            for k in (
                "fixed_indices",
                "free_indices",
                "removed_equality_rows",
                "retained_rows",
            )
        ]
        values = np.asarray(sub["fixed_values"])
        np.testing.assert_array_equal(
            np.sort(np.r_[fixed, free]), np.arange(full["A"].shape[1])
        )
        np.testing.assert_array_equal(
            np.sort(np.r_[rows, keep]), np.arange(full["A"].shape[0])
        )
        old_layout = oa.cone_layout(
            r["canonical_archive"]["cone_dimensions"], full["A"].shape[0]
        )
        reduced_layout = oa.cone_layout(
            r["reduced_archive"]["cone_dimensions"], reduced["A"].shape[0]
        )
        assert reduced_layout == (old_layout[0] - len(fixed), *old_layout[1:])
        assert np.all(rows < old_layout[0])
        allowed = {
            int(i)
            for v in r["canonical_archive"]["variables"]
            if v["name"] in ("Pg", "p_nd")
            for i in oa.indices(v)
        }
        assert set(fixed) <= allowed
        definition = full["A"][rows].tocsr()
        np.testing.assert_array_equal(np.diff(definition.indptr), np.ones(len(rows)))
        np.testing.assert_array_equal(definition.indices, fixed)
        np.testing.assert_array_equal(definition.data * values, full["b"][rows])
        # Independent block identities, without invoking the reduction builder.
        np.testing.assert_array_equal((reduced["A"] - full["A"][keep][:, free]).data, 0)
        np.testing.assert_array_equal((reduced["P"] - full["P"][free][:, free]).data, 0)
        np.testing.assert_array_equal(
            reduced["b"], full["b"][keep] - full["A"][keep][:, fixed] @ values
        )
        np.testing.assert_array_equal(
            reduced["c"], full["c"][free] + full["P"][free][:, fixed] @ values
        )
        offset = (
            0.5 * values @ (full["P"][fixed][:, fixed] @ values)
            + full["c"][fixed] @ values
        )
        assert offset == sub["objective_offset"]
        with np.load(folder / "scales.npz", allow_pickle=False) as saved:
            R, D = saved["R"], saved["D"]
        R2, D2 = j.joint_scales(reduced, reduced_layout)
        np.testing.assert_array_equal(R, R2)
        np.testing.assert_array_equal(D, D2)
        for key, expected in (
            ("A", reduced["A"].multiply(R[:, None]).multiply(D[None, :])),
            ("P", reduced["P"].multiply(D[:, None]).multiply(D[None, :])),
        ):
            np.testing.assert_allclose(
                (transformed[key] - expected).data, 0, atol=1e-14
            )
        np.testing.assert_array_equal(transformed["b"], R * reduced["b"])
        np.testing.assert_array_equal(transformed["c"], D * reduced["c"])
        raw, mid, mapped = (
            r[k]
            for k in (
                "transformed_native_solution",
                "reduced_native_solution",
                "native_solution",
            )
        )
        for key, expected in (
            ("x", D * raw["x"]),
            ("s", np.asarray(raw["s"]) / R),
            ("z", R * raw["z"]),
        ):
            np.testing.assert_array_equal(mid[key], expected)
        x, s, z = [np.asarray(mapped[k]) for k in ("x", "s", "z")]
        np.testing.assert_array_equal(x[fixed], values)
        np.testing.assert_array_equal(x[free], mid["x"])
        np.testing.assert_array_equal(s[rows], 0)
        np.testing.assert_array_equal(s[keep], mid["s"])
        np.testing.assert_array_equal(z[keep], mid["z"])
        z0 = z.copy()
        z0[rows] = 0
        np.testing.assert_array_equal(
            z[rows],
            -(full["P"] @ x + full["c"] + full["A"].T @ z0)[fixed] / definition.data,
        )
        for key in ("obj_val", "obj_val_dual"):
            assert mapped[key] == mid[key] + offset
        kkt = oa.analyze(folder)
        arms.append(
            dict(
                label=item["label"],
                hashes_and_maps_verified=True,
                accepted=r["accepted"],
                physical_audit_passed=r["audit"]["passed"],
                largest_physical_gate_fraction=max(
                    v / r["audit"]["limits"][k]
                    for k, v in r["audit"]["residuals"].items()
                ),
                native_info=r["native_info"]["native_info"],
                previous_native_info=before["native_info"]["native_info"],
                substitution=sub,
                objective=r["result"]["objective"],
                previous_objective=before["result"]["objective"],
                timings=r["timings"],
                peak_rss_mib=sup["peak_rss_mib"],
                optimality=kkt,
                previous_optimality=oa.analyze(old_dir),
            )
        )
    return dict(
        schema=1,
        execution_context=binding,
        analysis_context=d.context(),
        analysis_source_sha256=d.sha(__file__),
        summary_sha256=d.sha(root / "summary.json"),
        optimization_calls=0,
        promotional=False,
        arms=arms,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    path = args.root / "verified_analysis.json"
    d.publish(path, analyze(args.root))
    print(path, d.sha(path))
