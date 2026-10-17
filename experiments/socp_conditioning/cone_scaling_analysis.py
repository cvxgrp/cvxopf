"""Independent saved-vector reconstruction; no optimizer calls.

An optional bounded, wider-Krylov-space estimate fills missing input spectra.
It changes neither optimization settings nor immutable solve artifacts.
"""

import argparse
import gzip
import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, eigsh, splu

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import cone_scaling as c
from experiments.socp_conditioning import optimality_audit as oa


def matrices(path):
    with np.load(path, allow_pickle=False) as z:
        return {
            k: sparse.csc_matrix(
                (z[k + "_data"], z[k + "_indices"], z[k + "_indptr"]),
                shape=z[k + "_shape"],
            )
            for k in ("A", "P")
        } | {k: z[k].copy() for k in ("b", "c")}


def wider_spectrum(A):
    began = time.monotonic()
    result = dict(method="offline_sparse_gram_shift_invert_ncv80_tol1e-6_not_certified")
    try:
        G = (A.T @ A).tocsc()
        lu = splu(G)

        def bounded(fn, x):
            if time.monotonic() - began > 8:
                raise TimeoutError("eight-second offline spectral budget")
            return fn(x)

        op = LinearOperator(G.shape, matvec=lambda x: bounded(G.dot, x), dtype=float)
        inv = LinearOperator(
            G.shape, matvec=lambda x: bounded(lu.solve, x), dtype=float
        )
        v0 = np.random.default_rng(0).normal(size=G.shape[0])
        hi, _ = eigsh(op, k=1, which="LA", tol=1e-6, maxiter=300, ncv=80, v0=v0)
        _, vv = eigsh(
            op,
            k=1,
            which="LM",
            sigma=0,
            OPinv=inv,
            tol=1e-6,
            maxiter=300,
            ncv=80,
            v0=v0,
        )
        v = vv[:, 0]
        av = A @ v
        low, high = np.linalg.norm(av), np.sqrt(hi[0])
        residual = np.linalg.norm(A.T @ (av / low) - low * v)
        if not np.isfinite(low) or low <= 0 or residual > 1e-5 * max(1, high):
            raise ValueError("original-coordinate singular triplet failed")
        result.update(
            status="estimated_not_certified",
            condition_2=float(high / low),
            sigma_min=float(low),
            sigma_max=float(high),
            singular_triplet_residual=float(residual),
        )
    except Exception as exc:
        result.update(
            status="estimate_unavailable", exception=f"{type(exc).__name__}: {exc}"
        )
    return result | {"wall_seconds": time.monotonic() - began}


def analyze(root, *, labels=c.ARMS):
    summary = json.loads((root / "summary.json").read_text())
    binding = json.loads((root / "binding.json").read_text())
    if summary["context"] != binding or [a["label"] for a in summary["arms"]] != list(
        labels
    ):
        raise ValueError("incomplete/mismatched root")
    checked = []
    for item in summary["arms"]:
        folder = root / item["label"]
        for key, name in (
            ("arm_sha256", "arm.json.gz"),
            ("supervision_sha256", "supervision.json"),
            ("optimality_sha256", "optimality.json"),
        ):
            if d.sha(folder / name) != item[key]:
                raise ValueError("summary artifact mismatch")
        sup = json.loads((folder / "supervision.json").read_text())
        for name, sha in sup["artifacts"].items():
            if d.sha(folder / name) != sha:
                raise ValueError("supervision artifact mismatch")
        r = json.load(gzip.open(folder / "arm.json.gz", "rt"))
        if (
            sup["classification"] != "completed"
            or sup["returncode"] != 0
            or r["context"] != binding
            or r["context_after"] != binding
            or r["optimizer_calls"] != 1
        ):
            raise ValueError("execution evidence mismatch")
        for key, name in (
            ("canonical_archive", "canonical.npz"),
            ("transformed_archive", "transformed.npz"),
        ):
            if d.sha(folder / name) != r[key]["sha256"]:
                raise ValueError("matrix hash mismatch")
        source, changed = (
            matrices(folder / "canonical.npz"),
            matrices(folder / "transformed.npz"),
        )
        source["dims"] = r["canonical_archive"]["cone_dimensions"]
        with np.load(folder / "scales.npz", allow_pickle=False) as z:
            R, D = z["R"], z["D"]
        expected = c.transform(source, R, D)
        for key in ("A", "P"):
            np.testing.assert_allclose(
                (changed[key] - expected[key]).data, 0, atol=1e-12
            )
        for key in ("b", "c"):
            np.testing.assert_allclose(
                changed[key], expected[key], atol=1e-12, rtol=1e-14
            )
        raw, mapped = r["transformed_native_solution"], r["native_solution"]
        for key, value in (
            ("x", D * raw["x"]),
            ("s", np.asarray(raw["s"]) / R),
            ("z", R * raw["z"]),
        ):
            np.testing.assert_array_equal(mapped[key], value)
        optimality = oa.analyze(folder)
        spectral = r["conditioning"]["transformed"]["A"]
        if spectral["status"] != "estimated_not_certified":
            spectral = wider_spectrum(changed["A"])
        checked.append(
            dict(
                label=r["label"],
                hashes_and_mappings_verified=True,
                native_status=mapped["status"],
                iterations=mapped["iterations"],
                native_solve_seconds=mapped["solve_time"],
                physical_audit_passed=r["audit"]["passed"],
                accepted=r["accepted"],
                objective=r["result"]["objective"],
                named_costs=r["named_costs"],
                mapped_optimality=optimality,
                transformed_spectrum=spectral,
                scale_seconds=r["timings"]["scaling_seconds"],
                peak_rss_mib=sup["peak_rss_mib"],
            )
        )
    return dict(
        execution_context=binding,
        analysis_context=d.context(),
        analysis_source_sha256=d.sha(__file__),
        summary_sha256=d.sha(root / "summary.json"),
        arms=checked,
        optimization_calls=0,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    d.publish(args.output, analyze(args.root))
