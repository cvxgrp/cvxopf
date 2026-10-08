"""Bounded offline input-matrix diagnostics. Never invokes an optimizer."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import signal
import time
from unittest.mock import patch

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import structural_rank
from scipy.sparse.linalg import LinearOperator, eigsh, splu, svds

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import mosek_check, no_shedding
from experiments.socp_conditioning.optimality_audit import cone_layout


ARMS = {
    "clarabel_shedding": "equilibration_001/surplus-equil_default",
    "clarabel_fixed": "no_shedding_001/clarabel",
    "mosek_shedding": "mosek_002",
    "mosek_fixed": "no_shedding_001/mosek",
    "copt_shedding": "copt_002",
    "copt_fixed": "no_shedding_001/copt",
}
SPECTRAL_SECONDS = 8.0


def context():
    return dict(
        **no_shedding.context(),
        matrix_analysis_sha256=d.sha(__file__),
        spectral_seconds=SPECTRAL_SECONDS,
        optimization_calls_authorized=0,
    )


def distribution(v):
    a = np.abs(np.asarray(v).ravel())
    nz = a[(a > 0) & np.isfinite(a)]
    return dict(
        size=a.size,
        zeros=int(np.sum(a == 0)),
        nonfinite=int(np.sum(~np.isfinite(a))),
        nonzero_quantiles=None
        if not nz.size
        else dict(
            zip(
                ("min", "p01", "median", "p99", "max"),
                np.quantile(nz, [0, 0.01, 0.5, 0.99, 1]).tolist(),
                strict=True,
            )
        ),
        nonzero_spread=None if not nz.size else float(nz.max() / nz.min()),
    )


def clean(A):
    A = sparse.csr_matrix(A, dtype=float)
    A.sum_duplicates()
    A.eliminate_zeros()
    A.sort_indices()
    return A


def equilibrate(A):
    """Five algebraic row/column 2-norm passes, not solver cone scaling."""
    B = A.copy()
    for _ in range(5):
        row = np.sqrt(np.asarray(B.multiply(B).sum(axis=1)).ravel())
        B = sparse.diags(1 / np.maximum(row, 1e-300)) @ B
        col = np.sqrt(np.asarray(B.multiply(B).sum(axis=0)).ravel())
        B = B @ sparse.diags(1 / np.maximum(col, 1e-300))
    return clean(B)


def spectrum(A):
    """Rectangular 2-norm condition estimate, with a bounded sparse solve."""
    began = time.monotonic()
    record = dict(structural_rank_upper_bound=int(structural_rank(A)))
    if min(A.shape) < 2:
        return record | {"status": "too_small"}

    products = 0

    def product(matrix, x):
        nonlocal products
        products += 1
        if time.monotonic() - began > SPECTRAL_SECONDS or products > 4000:
            raise TimeoutError("spectral time/product budget exceeded")
        return matrix @ x

    # ARPACK returns to these Python callbacks for every matrix product. Unlike
    # the earlier PROPACK call, this enforces the budget without relying on a
    # signal interrupting a long Fortran routine. No normal matrix is assembled.
    operator = LinearOperator(
        A.shape,
        matvec=lambda x: product(A, x),
        rmatvec=lambda x: product(A.T, x),
        dtype=float,
    )
    try:
        high = svds(
            operator,
            k=1,
            which="LM",
            solver="arpack",
            tol=1e-6,
            maxiter=min(160, min(A.shape)),
            random_state=0,
            return_singular_vectors=True,
        )
        record["sigma_max_estimate"] = float(high[1][0])
        if record["structural_rank_upper_bound"] < min(A.shape):
            record.update(status="structurally_rank_deficient", condition_2="infinite")
        else:
            low = svds(
                operator,
                k=1,
                which="SM",
                solver="arpack",
                tol=1e-6,
                maxiter=min(160, min(A.shape)),
                random_state=0,
                return_singular_vectors=True,
            )
            u, s, vt = low
            residual = max(
                np.linalg.norm(A @ vt[0] - s[0] * u[:, 0]),
                np.linalg.norm(A.T @ u[:, 0] - s[0] * vt[0]),
            )
            if (
                not np.isclose(np.linalg.norm(u[:, 0]), 1, atol=1e-5)
                or not np.isclose(np.linalg.norm(vt[0]), 1, atol=1e-5)
                or residual > 1e-5 * max(1.0, record["sigma_max_estimate"])
            ):
                raise ValueError(
                    "returned singular triplet failed independent residual/norm check"
                )
            record.update(
                status="estimated_not_certified",
                sigma_min_estimate=float(s[0]),
                singular_triplet_residual=float(residual),
                condition_2="infinite" if s[0] == 0 else float(high[1][0] / s[0]),
                smallest_direction_columns=[
                    dict(index=int(i), weight=float(vt[0, i]))
                    for i in np.argsort(abs(vt[0]))[-8:][::-1]
                ],
            )
    except Exception as exc:
        record.update(
            status="estimate_unavailable", reason=f"{type(exc).__name__}: {exc}"
        )
    return record | {
        "elapsed_seconds": time.monotonic() - began,
        "matrix_products": products,
    }


def describe(A):
    A = clean(A)
    row = np.sqrt(np.asarray(A.multiply(A).sum(axis=1)).ravel())
    col = np.sqrt(np.asarray(A.multiply(A).sum(axis=0)).ravel())
    # Exact duplicate/opposite rows only; no numerical rank claim from rounding.
    seen, duplicates = {}, 0
    for i in range(A.shape[0]):
        a, b = A.indptr[i : i + 2]
        vals = A.data[a:b]
        if not len(vals):
            continue
        normalized = vals if vals[0] > 0 else -vals
        key = (A.indices[a:b].tobytes(), normalized.tobytes())
        duplicates += key in seen
        seen[key] = i
    return dict(
        shape=A.shape,
        nnz=A.nnz,
        coefficients=distribution(A.data),
        row_norms=distribution(row),
        column_norms=distribution(col),
        duplicate_or_opposite_rows=duplicates,
        raw_spectrum=spectrum(A),
        algebraically_equilibrated_spectrum=spectrum(equilibrate(A)),
        factorized_raw_spectrum=factorized_spectrum(A),
        factorized_equilibrated_spectrum=factorized_spectrum(equilibrate(A)),
    )


def factorized_spectrum(A):
    """Sparse Gram shift-invert estimate, checked in the original A coordinates.

    Forming a Gram matrix squares conditioning: estimates here are explicitly
    not certificates. Bound symbolic product size and use the worker's external
    memory/time supervisor during factorization; iterative products also have a
    local deadline. Never construct a dense normal matrix or optimize the OPF.
    """
    began = time.monotonic()
    record = dict(method="sparse_gram_shift_invert_not_certified")
    if structural_rank(A) < min(A.shape):
        return record | {
            "status": "structurally_rank_deficient",
            "condition_2": "infinite",
        }
    B = clean(A if A.shape[0] >= A.shape[1] else A.T)
    work = int(np.sum(np.diff(B.indptr).astype(np.int64) ** 2))
    record["gram_product_nnz_upper_bound"] = work
    if work > 10_000_000:
        return record | {"status": "size_budget_exceeded"}
    try:
        G = (B.T @ B).tocsc()
        record["gram_nnz"] = G.nnz
        lu = splu(G)
        record["factor_nnz"] = lu.L.nnz + lu.U.nnz

        def bounded_product(fn, x):
            if time.monotonic() - began > SPECTRAL_SECONDS:
                raise TimeoutError("factorized spectrum budget exceeded")
            return fn(x)

        op = LinearOperator(
            G.shape, matvec=lambda x: bounded_product(G.dot, x), dtype=float
        )
        inv = LinearOperator(
            G.shape, matvec=lambda x: bounded_product(lu.solve, x), dtype=float
        )
        high = eigsh(
            op,
            k=1,
            which="LA",
            tol=1e-4,
            maxiter=500,
            v0=np.random.default_rng(0).normal(size=G.shape[0]),
        )[0][0]
        _, v = eigsh(
            op,
            k=1,
            sigma=0,
            which="LM",
            OPinv=inv,
            tol=1e-7,
            maxiter=100,
            v0=np.random.default_rng(0).normal(size=G.shape[0]),
        )
        v = v[:, 0]
        bv = B @ v
        small = np.linalg.norm(bv)
        big = np.sqrt(high)
        residual = np.linalg.norm(B.T @ (bv / small) - small * v)
        record.update(
            sigma_min_estimate=float(small),
            sigma_max_estimate=float(big),
            singular_triplet_residual=float(residual),
        )
        if not np.isfinite(small) or small <= 0 or residual > 1e-5 * max(1, big):
            raise ValueError(
                "factorized estimate failed original-coordinate residual check"
            )
        record.update(status="estimated_not_certified", condition_2=float(big / small))
    except Exception as exc:
        record.update(
            status="estimate_unavailable", reason=f"{type(exc).__name__}: {exc}"
        )
    return record | {"elapsed_seconds": time.monotonic() - began}


def verified_record(directory):
    completion = json.loads((directory / "completion.json").read_text())
    arm = directory / "arm.json.gz"
    if d.sha(arm) != completion["arm_sha256"]:
        raise ValueError("arm hash mismatch")
    r = json.load(gzip.open(arm, "rt"))
    for name, expected in r["context"]["sources"].items():
        if name.startswith("src/cvxopf/") and d.sha(d.ROOT / name) != expected:
            raise ValueError("production sources changed")
    return r


def read_problem(label, output, reference):
    directory = d.HERE / "results" / ARMS[label]
    r = verified_record(directory)
    if label.startswith("clarabel"):
        path = directory / "canonical.npz"
        if d.sha(path) != r["canonical_archive"]["sha256"]:
            raise ValueError("canonical hash mismatch")
        with np.load(path, allow_pickle=False) as z:

            def mat(k):
                return sparse.csc_matrix(
                    (z[k + "_data"], z[k + "_indices"], z[k + "_indptr"]),
                    shape=z[k + "_shape"],
                )

            A, P, b, c = mat("A"), mat("P"), z["b"], z["c"]
        nzero, _, _ = cone_layout(r["canonical_archive"]["cone_dimensions"], A.shape[0])
        return (
            A,
            A[:nzero],
            P,
            b,
            c,
            dict(
                input_sha256=r["input_sha256"],
                artifact_sha256=d.sha(path),
                representation="quadratic objective; conic slack form",
                variable_layout=r["canonical_archive"]["variables"],
            ),
        )
    if label.startswith("mosek"):
        import mosek

        path = directory / "task.ptf.gz"
        if d.sha(path) != r["task_sha256"]:
            raise ValueError("task hash mismatch")
        with mosek.Task() as task:
            task.readdata(str(path))
            m, n = task.getnumcon(), task.getnumvar()
            ii, jj, vv = [], [], []
            for i in range(m):
                _, cols, vals = task.getarow(i)
                ii.extend([i] * len(cols))
                jj.extend(cols)
                vv.extend(vals)
            A = sparse.csr_matrix((vv, (ii, jj)), shape=(m, n))
            keys, lo, hi = task.getconboundslice(0, m)
            eq = np.array([k == mosek.boundkey.fx for k in keys])
            assert task.getnumqobjnz() == 0
            return (
                A,
                A[eq],
                sparse.csr_matrix((n, n)),
                np.asarray(hi),
                np.asarray(task.getc()),
                dict(
                    input_sha256=r["input_sha256"],
                    artifact_sha256=d.sha(path),
                    representation="dualized conic maximization; quadratic costs conified",
                    cones=task.getnumcone(),
                    equality_rows=int(eq.sum()),
                ),
            )
    import coptpy
    from cvxpy.reductions.solvers.conic_solvers.copt_conif import COPT

    kwargs, _ = d.load_case("surplus", reference)
    if label.endswith("fixed"):
        kwargs = no_shedding.fixed_inputs(kwargs)
    assert d.input_digest(kwargs) == r["input_sha256"]
    build, _, _ = d.build_variant(kwargs, "cones")
    data, _, _ = build.prob.get_problem_data(
        "COPT", canon_backend=build.canonicalization_backend
    )
    calls = []

    def do_not_solve(self):
        calls.append("optimizer deliberately skipped")

    path = output / "reconstructed.mps"
    with patch.object(coptpy.Model, "solve", do_not_solve):
        result = COPT().solve_via_data(
            data, False, False, dict(Threads=1, reoptimize=False, save_file=str(path))
        )
    assert len(calls) == 1
    if (
        d.sha(path) != r["task_sha256"]
        or path.read_bytes() != (directory / "task.mps").read_bytes()
    ):
        raise ValueError("COPT non-solving reconstruction differs from retained task")
    model = result["model"]
    A = clean(model.getA())
    rows, cols = model.getConstrs(), model.getVars()
    lo = np.asarray(model.getInfo("LB", rows))
    hi = np.asarray(model.getInfo("UB", rows))
    eq = lo == hi
    b = np.where(abs(hi) < 1e20, hi, lo)
    return (
        A,
        A[eq],
        sparse.csr_matrix((A.shape[1], A.shape[1])),
        b,
        np.asarray(model.getInfo("Obj", cols)),
        dict(
            input_sha256=r["input_sha256"],
            artifact_sha256=r["task_sha256"],
            representation="primal conic form with explicit cone-coordinate variables",
            reconstructed_mps_byte_identical=True,
            optimizer_calls=0,
            cones=model.getAttr("Cones"),
            equality_rows=int(eq.sum()),
        ),
    )


def worker(output, reference, label):
    started = time.monotonic()
    record = dict(context=context(), label=label, exception=None)
    try:
        A, E, P, b, c, identity = read_problem(label, output, reference)
        record.update(
            identity=identity,
            A=describe(A),
            equalities=describe(E),
            b=distribution(b),
            c=distribution(c),
            P=dict(
                shape=P.shape,
                nnz=P.nnz,
                diagonal=distribution(P.diagonal()),
                off_diagonal_nnz=(P - sparse.diags(P.diagonal())).nnz,
            ),
            wall_seconds=time.monotonic() - started,
        )
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    d.publish(output / "analysis.json", record)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--worker", action="store_true")
    p.add_argument("--label", choices=ARMS)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    args = p.parse_args()

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    output, reference = args.output.resolve(), args.reference.resolve()
    if args.worker:
        worker(output, reference, args.label)
    else:
        output.mkdir(parents=True, exist_ok=False)
        d.publish(output / "binding.json", context())
        for label in ARMS:
            mosek_check.supervise(
                output / label,
                reference,
                worker_module=__spec__.name,
                context_factory=context,
                worker_arguments=("--label", label),
            )
            sup = json.loads((output / label / "supervision.json").read_text())
            if sup["classification"] != "completed":
                raise RuntimeError("analysis resource/process failure")


if __name__ == "__main__":
    main()
