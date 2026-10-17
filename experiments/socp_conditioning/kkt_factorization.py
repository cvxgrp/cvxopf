"""Bounded independent sparse LU checks of saved native KKT systems.

No optimization calls. Check the original matrix and the recorded static shift;
neither incorporates QDLDL's dynamic pivot modifications. One factor per matrix,
three saved RHSs, then at most five residual-correction steps on each original
system. Estimates and backward errors do not certify forward accuracy.
"""

import argparse
import gzip
import json
from pathlib import Path
import signal
import time

import numpy as np
import scipy
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, onenormest, splu

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import mosek_check as supervision
from experiments.socp_conditioning import native_step_probe as n

OPTIONS = dict(permc_spec="COLAMD", diag_pivot_thresh=1.0, options={"Equil": True})


def verified_snapshots(root):
    summary = json.loads((root / "summary.json").read_text())
    if [a["mode"] for a in summary["arms"]] != ["control", "instrumented"]:
        raise ValueError("missing replay arms")
    for arm in summary["arms"]:
        folder = root / arm["mode"]
        if (
            not arm["exact_baseline_vectors"]
            or d.sha(folder / "supervision.json") != arm["supervision_sha256"]
        ):
            raise ValueError("unverified native replay")
        sup = json.loads((folder / "supervision.json").read_text())
        for name, sha in sup["artifacts"].items():
            if d.sha(folder / name) != sha:
                raise ValueError(f"artifact changed: {name}")
        r = json.load(gzip.open(folder / "arm.json.gz", "rt"))
        if (
            r["context"] != summary["context"]
            or r["context_after"] != summary["context"]
        ):
            raise ValueError("capture provenance changed")
        if d.sha(n.BASELINE / "arm.json.gz") != n.BASELINE_SHA:
            raise ValueError("baseline changed")
        prior = json.load(gzip.open(n.BASELINE / "arm.json.gz", "rt"))
        if not n.same_solution(prior["raw_clarabel"], r["raw_clarabel"]):
            raise ValueError("native solution changed")
    snapshots = []
    # CVXPY raises for the rejected native status before the context manager's
    # trailing bookkeeping. The supervisor still binds every snapshot on disk.
    for name, sha in sorted(sup["artifacts"].items()):
        if not (name.startswith("kkt-") and name.endswith(".json")):
            continue
        path = root / "instrumented" / name
        if d.sha(path) != sha:
            raise ValueError("KKT snapshot changed")
        snapshots.append(json.loads(path.read_text()) | dict(path=name, sha256=sha))
    if len(snapshots) != 3:
        raise ValueError("expected constant, affine, combined RHS snapshots")
    return snapshots


def matrix(record):
    a = record["K"]
    triangle = sparse.csc_matrix(
        (a["nzval"], a["rowval"], a["colptr"]), shape=(a["m"], a["n"])
    )
    if a["m"] != a["n"] or record["triangle"] not in ("Triu", "Tril"):
        raise ValueError("invalid symmetric KKT storage")
    return (triangle + triangle.T - sparse.diags(triangle.diagonal())).tocsc()


def metrics(K, rhs, x):
    if not np.isfinite(x).all() or not np.isfinite(rhs).all():
        raise ValueError("nonfinite linear solution or right-hand side")
    residual = rhs - K @ x
    e = float(np.linalg.norm(residual, np.inf))
    bn = float(np.linalg.norm(rhs, np.inf))
    xn = float(np.linalg.norm(x, np.inf))
    denom = abs(K) @ abs(x) + abs(rhs)
    componentwise = np.divide(
        abs(residual), denom, out=np.zeros_like(residual), where=denom > 0
    )
    normK = float(np.max(np.asarray(abs(K).sum(axis=1))))
    return dict(
        residual_inf=e,
        rhs_inf=bn,
        x_inf=xn,
        rhs_relative_residual=e / max(bn, 1e-300),
        normwise_backward_error=e / max(normK * xn + bn, 1e-300),
        componentwise_backward_error=float(np.max(componentwise)),
    )


def refine(K, lu, rhs, x):
    """Keep only corrections that improve the original-system residual."""
    history = [metrics(K, rhs, x)]
    trials = []
    for _ in range(5):
        candidate = x + lu.solve(rhs - K @ x)
        if not np.isfinite(candidate).all():
            trials.append(dict(nonfinite=True))
            break
        m = metrics(K, rhs, candidate)
        improved = m["residual_inf"] < history[-1]["residual_inf"]
        trials.append(m | dict(retained=improved))
        if not improved:
            break
        x = candidate
        history.append(m)
    return x, history, trials


def context(root):
    return dict(
        experiment="independent_saved_KKT_factorization",
        root_sha256=d.sha(root / "summary.json"),
        analysis_source_sha256=d.sha(__file__),
        numpy=np.__version__,
        scipy=scipy.__version__,
        factor_options=OPTIONS,
        optimization_calls=0,
        threads=1,
        limits=dict(wall_seconds=d.WALL_SECONDS, rss_mib=d.RSS_MIB),
    )


def worker(directory, root):
    result = dict(
        context=context(root),
        exception=None,
        systems=[],
        factorizations=[],
        optimizer_calls=0,
    )
    try:
        snapshots = verified_snapshots(root)
        K = matrix(snapshots[0])
        eps = snapshots[0]["static_regularizer"]
        signs = np.array(snapshots[0]["dsigns"])
        Kreg = K + sparse.diags(eps * signs)
        for r in snapshots:
            if (
                r["K"] != snapshots[0]["K"]
                or r["dsigns"] != snapshots[0]["dsigns"]
                or r["static_regularizer"] != eps
            ):
                raise ValueError("three snapshots must share exactly the same matrix")
            rhs, x = np.asarray(r["rhs"]), np.asarray(r["native_x"])
            original = metrics(K, rhs, x)
            result["systems"].append(
                dict(
                    source=r["path"],
                    sha256=r["sha256"],
                    native=original,
                    native_on_static_shift=metrics(Kreg, rhs, x),
                    logged_native_residual=r["native_residual_inf"],
                    requested_tolerance=r["requested_tolerance"],
                )
            )
        values = abs(K.data[K.data != 0])
        result["matrix"] = dict(
            shape=K.shape,
            nnz=K.nnz,
            min_abs_nonzero=float(values.min()),
            max_abs_nonzero=float(values.max()),
            norm_inf=float(np.max(np.asarray(abs(K).sum(axis=1)))),
            static_regularizer=eps,
            longdouble_mantissa_bits=int(np.finfo(np.longdouble).nmant),
        )
        for name, target in (("unregularized", K), ("recorded_static_shift", Kreg)):
            started = time.monotonic()
            record = dict(matrix=name, exception=None, solves=[])
            try:
                lu = splu(target.tocsc(), **OPTIONS)
                record.update(
                    factor_seconds=time.monotonic() - started,
                    L_nnz=lu.L.nnz,
                    U_nnz=lu.U.nnz,
                )
                inverse = LinearOperator(
                    target.shape,
                    matvec=lu.solve,
                    rmatvec=lambda x: lu.solve(x, trans="T"),
                    dtype=float,
                )
                # A descriptive lower estimate, not an interval certificate.
                np.random.seed(0)
                record["condition_1_estimate"] = float(
                    np.max(np.asarray(abs(target).sum(axis=0)))
                    * onenormest(inverse, t=2, itmax=5)
                )
                for r in snapshots:
                    rhs = np.asarray(r["rhs"])
                    x = lu.solve(rhs)
                    original = metrics(K, rhs, x)
                    fitted = metrics(target, rhs, x)
                    refined, history, trials = refine(K, lu, rhs, x)
                    record["solves"].append(
                        dict(
                            source=r["path"],
                            original_system=original,
                            factorized_system=fitted,
                            refinement_history=history,
                            refinement_trials=trials,
                            meets_original_requested_tolerance=history[-1][
                                "residual_inf"
                            ]
                            <= r["requested_tolerance"],
                        )
                    )
                    with (directory / f"{name}-{Path(r['path']).stem}.npz").open(
                        "xb"
                    ) as stream:
                        np.savez_compressed(stream, direct=x, refined=refined)
            except Exception as exc:
                record["exception"] = f"{type(exc).__name__}: {exc}"
            record["elapsed_seconds"] = time.monotonic() - started
            result["factorizations"].append(record)
    except Exception as exc:
        result["exception"] = f"{type(exc).__name__}: {exc}"
    d.publish(directory / "analysis.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--worker", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--reference", type=Path, required=True, help="Verified native capture root"
    )
    args = p.parse_args()

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    output, root = args.output.resolve(), args.reference.resolve()
    if args.worker:
        worker(output, root)
    else:
        verified_snapshots(root)
        supervision.supervise(
            output,
            root,
            worker_module=__spec__.name,
            context_factory=lambda: context(root),
        )
