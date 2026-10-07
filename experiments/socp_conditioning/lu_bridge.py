"""Persistent serial SuperLU server for the isolated native CLARABEL diagnostic.

JSON lines on stdin/stdout; numerical timings on stderr. The supplied upper
triangle already includes CLARABEL's original-coordinate static shift. No
additional shift, scaling, refinement, or optimization is applied here.
"""

import json
import sys
import time

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import splu

OPTIONS = dict(permc_spec="COLAMD", diag_pivot_thresh=1.0, options={"Equil": True})


class Bridge:
    def __init__(self):
        self.factorization = None
        self.dimension = None
        self.generation = 0

    def handle(self, request):
        if request["op"] == "factor":
            # Discard the old factor even if replacement fails; stale reuse is
            # never an alternative to reporting a failed numerical factorization.
            self.factorization = None
            began = time.monotonic()
            n = request["n"]
            if n != request["m"] or n <= 0:
                raise ValueError("expected square KKT")
            upper = sparse.csc_matrix(
                (request["nzval"], request["rowval"], request["colptr"]),
                shape=(n, n),
            )
            if not np.isfinite(upper.data).all() or sparse.tril(upper, -1).nnz:
                raise ValueError("expected finite upper triangle")
            matrix = (upper + upper.T - sparse.diags(upper.diagonal())).tocsc()
            assembly_seconds = time.monotonic() - began
            began = time.monotonic()
            factor = splu(matrix, **OPTIONS)
            seconds = time.monotonic() - began
            if (
                not np.isfinite(factor.L.data).all()
                or not np.isfinite(factor.U.data).all()
            ):
                raise ValueError("nonfinite LU factors")
            self.factorization, self.dimension = factor, n
            self.generation += 1
            return dict(
                ok=True,
                event="factor",
                generation=self.generation,
                seconds=seconds,
                assembly_seconds=assembly_seconds,
                L_nnz=factor.L.nnz,
                U_nnz=factor.U.nnz,
                matrix_nnz=matrix.nnz,
            )
        if request["op"] == "solve":
            if self.factorization is None:
                raise ValueError("no valid factorization")
            rhs = np.asarray(request["rhs"], dtype=float)
            if rhs.shape != (self.dimension,) or not np.isfinite(rhs).all():
                raise ValueError("invalid RHS")
            began = time.monotonic()
            x = self.factorization.solve(rhs)
            seconds = time.monotonic() - began
            if not np.isfinite(x).all():
                raise ValueError("nonfinite solution")
            return dict(
                ok=True,
                event="solve",
                generation=self.generation,
                seconds=seconds,
                x=x.tolist(),
            )
        raise ValueError("unknown operation")


def main():
    bridge = Bridge()
    for line in sys.stdin:
        try:
            response = bridge.handle(json.loads(line))
        except Exception as exc:
            response = dict(ok=False, error=f"{type(exc).__name__}: {exc}")
        print(
            "TRACE SUPERLU "
            + json.dumps(
                {k: v for k, v in response.items() if k != "x"}, allow_nan=False
            ),
            file=sys.stderr,
            flush=True,
        )
        print(json.dumps(response, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
