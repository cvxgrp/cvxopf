"""Frozen six-arm scaling diagnostic on the saved failing KKT system.

Symmetric infinity-norm Ruiz equilibration: 0/5/10 passes on K and the recorded
static shift. Solve (D K D)y = D rhs and return x = D y. Same SuperLU options,
at most five mapped residual corrections, and 80-digit original-system checks.
No optimizer calls or feedback into CLARABEL. One worker/thread, 180 s, 4 GiB.
"""

import argparse
import json
from pathlib import Path
import signal
import time
from types import SimpleNamespace

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, onenormest, splu

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import kkt_factorization as k
from experiments.socp_conditioning import kkt_residual_check as r
from experiments.socp_conditioning import mosek_check as supervision

PASSES = (0, 5, 10)


def congruence(K, D):
    """Pairwise scale products preserve exact symmetry of binary64 storage."""
    a = K.tocoo(copy=True)
    a.data *= D[a.row] * D[a.col]
    return a.tocsc()


def ruiz(K, passes):
    if passes not in PASSES:
        raise ValueError("unfrozen pass count")
    D = np.ones(K.shape[0])
    history = []
    for _ in range(passes):
        current = congruence(K, D)
        norms = np.asarray(abs(current).max(axis=1).toarray()).ravel()
        if np.any(norms <= 0) or not np.isfinite(norms).all():
            raise ValueError("zero or nonfinite KKT row")
        step = np.clip(1 / np.sqrt(norms), 1e-8, 1e8)
        D = np.clip(D * step, 1e-16, 1e16)
        history.append(
            dict(row_max_min=float(norms.min()), row_max_max=float(norms.max()))
        )
    scaled = congruence(K, D)
    if (scaled != scaled.T).nnz or not np.isfinite(scaled.data).all():
        raise ValueError("invalid scaled matrix")
    return scaled, D, history


def matrix_summary(A):
    values = abs(A.data[A.data != 0])
    norms = np.asarray(abs(A).max(axis=1).toarray()).ravel()
    return dict(
        min_abs_nonzero=float(values.min()),
        max_abs_nonzero=float(values.max()),
        row_max_min=float(norms.min()),
        row_max_max=float(norms.max()),
    )


def context(root):
    return k.context(root) | dict(
        experiment="saved_KKT_symmetric_scaling",
        sources={Path(p).name: d.sha(p) for p in (__file__, k.__file__, r.__file__)},
        passes=list(PASSES),
        matrices=["unregularized", "recorded_static_shift"],
        scaling="D K D; x=D y; RHS=D rhs; positive symmetric infinity-norm Ruiz",
        per_pass_clip=[1e-8, 1e8],
        cumulative_clip=[1e-16, 1e16],
        refinement="at most five original-system residual improvements; mapped solve",
        adjudication="80-digit residual against original unregularized K and saved RHS",
    )


def worker(output, root):
    before = context(root)
    result = dict(context=before, exception=None, arms=[], optimizer_calls=0)
    try:
        snapshots = k.verified_snapshots(root)
        if any(
            s["K"] != snapshots[0]["K"]
            or s["dsigns"] != snapshots[0]["dsigns"]
            or s["static_regularizer"] != snapshots[0]["static_regularizer"]
            for s in snapshots
        ):
            raise ValueError("matrix identity mismatch")
        K = k.matrix(snapshots[0])
        shift = snapshots[0]["static_regularizer"] * np.asarray(snapshots[0]["dsigns"])
        result["original_matrix"] = matrix_summary(K)
        for matrix_name, target in (
            ("unregularized", K),
            ("recorded_static_shift", K + sparse.diags(shift)),
        ):
            for passes in PASSES:
                began = time.monotonic()
                arm = dict(matrix=matrix_name, passes=passes, exception=None, solves=[])
                try:
                    start = time.monotonic()
                    A, D, history = ruiz(target, passes)
                    arm.update(
                        scaling_seconds=time.monotonic() - start,
                        scaling_history=history,
                        scale_min=float(D.min()),
                        scale_max=float(D.max()),
                        matrix_summary=matrix_summary(A),
                    )
                    label = f"{matrix_name}-{passes}"
                    with (output / f"{label}-scales.npz").open("xb") as stream:
                        np.savez_compressed(stream, D=D)
                    start = time.monotonic()
                    lu = splu(A, **k.OPTIONS)
                    arm.update(
                        factor_seconds=time.monotonic() - start,
                        L_nnz=lu.L.nnz,
                        U_nnz=lu.U.nnz,
                    )
                    inverse = LinearOperator(
                        A.shape,
                        matvec=lu.solve,
                        rmatvec=lambda v: lu.solve(v, trans="T"),
                        dtype=float,
                    )
                    np.random.seed(0)
                    estimate = float(
                        np.max(np.asarray(abs(A).sum(axis=0)))
                        * onenormest(inverse, t=2, itmax=5)
                    )
                    arm["condition_1_estimate"] = (
                        estimate if np.isfinite(estimate) else None
                    )
                    mapped = SimpleNamespace(solve=lambda v: D * lu.solve(D * v))
                    for snapshot in snapshots:
                        rhs = np.asarray(snapshot["rhs"])
                        direct = mapped.solve(rhs)
                        refined, hist, trials = k.refine(K, mapped, rhs, direct)
                        precise = r.decimal_residual(K, rhs, refined)
                        arm["solves"].append(
                            dict(
                                source=snapshot["path"],
                                direct_original=k.metrics(K, rhs, direct),
                                refined_original=k.metrics(K, rhs, refined),
                                refinement_history=hist,
                                refinement_trials=trials,
                                high_precision=precise,
                                requested_tolerance=snapshot["requested_tolerance"],
                                meets_tolerance=float(precise["residual_inf"])
                                <= snapshot["requested_tolerance"],
                            )
                        )
                        with (
                            output / f"{label}-{Path(snapshot['path']).stem}.npz"
                        ).open("xb") as stream:
                            np.savez_compressed(stream, direct=direct, refined=refined)
                except Exception as exc:
                    arm["exception"] = f"{type(exc).__name__}: {exc}"
                arm["elapsed_seconds"] = time.monotonic() - began
                result["arms"].append(arm)
                d.publish(output / f"{matrix_name}-{passes}.json", arm)
                print(
                    json.dumps(
                        dict(
                            matrix=matrix_name,
                            passes=passes,
                            exception=arm["exception"],
                            residuals=[
                                s["high_precision"]["residual_inf"]
                                for s in arm["solves"]
                            ],
                        )
                    ),
                    flush=True,
                )
    except Exception as exc:
        result["exception"] = f"{type(exc).__name__}: {exc}"
    result["context_after"] = context(root)
    if result["context_after"] != before:
        result["exception"] = "analysis source or reference changed"
    d.publish(output / "analysis.json", result)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--worker", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    args = p.parse_args()

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    output, root = args.output.resolve(), args.reference.resolve()
    if args.worker:
        worker(output, root)
    else:
        k.verified_snapshots(root)
        supervision.supervise(
            output,
            root,
            worker_module=__spec__.name,
            context_factory=lambda: context(root),
        )
