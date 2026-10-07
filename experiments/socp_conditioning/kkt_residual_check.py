"""High-precision residual evaluation of saved vectors; no factor/optimizer calls."""

import argparse
from decimal import Decimal, localcontext
import gzip
import json
from pathlib import Path
import signal

import numpy as np

from experiments.socp_conditioning import cone_scaling_analysis as ca
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import kkt_factorization as k
from experiments.socp_conditioning import mosek_check as supervision
from experiments.socp_conditioning import termination_probe as t


def decimal_residual(K, rhs, x):
    """Evaluate every row at 80 digits, from exact stored binary64 numbers."""
    K = K.tocsr()
    with localcontext() as ctx:
        ctx.prec = 80
        xd = [Decimal.from_float(float(v)) for v in x]
        peak, peak_row = Decimal(0), 0
        for row in range(K.shape[0]):
            start, stop = K.indptr[row : row + 2]
            dot = sum(
                (
                    Decimal.from_float(float(v)) * xd[c]
                    for v, c in zip(K.data[start:stop], K.indices[start:stop])
                ),
                Decimal(0),
            )
            error = abs(Decimal.from_float(float(rhs[row])) - dot)
            if error > peak:
                peak, peak_row = error, row
        return dict(residual_inf=str(peak), row=peak_row, precision_digits=80)


def verify_factor(root):
    sup = json.loads((root / "supervision.json").read_text())
    if sup["classification"] != "completed":
        raise ValueError("incomplete factorization")
    for name, sha in sup["artifacts"].items():
        if d.sha(root / name) != sha:
            raise ValueError("factorization artifact changed")
    return json.loads((root / "analysis.json").read_text())


def context(root, capture):
    return dict(
        experiment="high_precision_saved_KKT_residuals",
        source_sha256=d.sha(__file__),
        factor_sha256=d.sha(root / "analysis.json"),
        capture_sha256=d.sha(capture / "summary.json"),
        optimizer_calls=0,
        factorization_calls=0,
        precision_digits=80,
    )


def worker(output, root, capture):
    result = dict(context=context(root, capture), exception=None, checks=[])
    try:
        factor = verify_factor(root)
        snapshots = k.verified_snapshots(capture)
        if factor["context"]["root_sha256"] != d.sha(capture / "summary.json"):
            raise ValueError("capture/factorization identity mismatch")
        K = k.matrix(snapshots[0]).tocsr()
        for r in snapshots:
            with np.load(root / f"unregularized-{Path(r['path']).stem}.npz") as z:
                vectors = dict(
                    native=np.asarray(r["native_x"]), independent_refined=z["refined"]
                )
            checks = {}
            for name, x in vectors.items():
                checks[name] = decimal_residual(K, r["rhs"], x)
            result["checks"].append(dict(source=r["path"], vectors=checks))
        # Identify the five largest diagonals through the archived canonical map.
        old = json.load(gzip.open(t.PRIOR / "arm.json.gz", "rt"))
        if d.sha(t.PRIOR / "arm.json.gz") != t.PRIOR_SHA:
            raise ValueError("canonical source changed")
        original = ca.matrices(t.PRIOR / "canonical.npz")
        if d.sha(t.PRIOR / "canonical.npz") != old["canonical_archive"]["sha256"]:
            raise ValueError("canonical matrix changed")
        nvars = old["substitution"]["reduced_shape"][1]
        identities = []
        for index in np.argsort(abs(K.diagonal()))[-5:][::-1]:
            row = old["substitution"]["retained_rows"][int(index) - nvars]
            block = next(
                b
                for b in old["canonical_archive"]["constraints"]
                if b["start"] <= row < b["stop"]
            )
            a = original["A"].getrow(row)
            entries = []
            for col, value in zip(a.indices, a.data):
                spec = next(
                    v
                    for v in old["canonical_archive"]["variables"]
                    if v["offset"] <= col < v["offset"] + np.prod(v["shape"])
                )
                entries.append(
                    dict(
                        variable=spec["name"],
                        coordinate=[
                            int(i)
                            for i in np.unravel_index(
                                col - spec["offset"], spec["shape"], order="F"
                            )
                        ],
                        coefficient=float(value),
                    )
                )
            identities.append(
                dict(
                    kkt_index=int(index),
                    diagonal=float(K[index, index]),
                    canonical_row=row,
                    block=block,
                    entries=entries,
                    rhs=float(original["b"][row]),
                )
            )
        result["largest_diagonal_identity"] = identities
    except Exception as exc:
        result["exception"] = f"{type(exc).__name__}: {exc}"
    d.publish(output / "analysis.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--worker", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True, help="Factorization root")
    p.add_argument("--capture", type=Path, required=True)
    args = p.parse_args()

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    output, root, capture = (
        args.output.resolve(),
        args.reference.resolve(),
        args.capture.resolve(),
    )
    if args.worker:
        worker(output, root, capture)
    else:
        supervision.supervise(
            output,
            root,
            worker_module=__spec__.name,
            context_factory=lambda: context(root, capture),
            worker_arguments=("--capture", str(capture)),
        )
