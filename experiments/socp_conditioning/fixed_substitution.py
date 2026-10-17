"""Three serial joint-scaling trials eliminating exact fixed Pg/p_nd coordinates.

Canonical substitution is experiment-local. The full model remains available
for CVXPY inverse reduction and independent original-unit physical auditing.
"""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from experiments.socp_conditioning import cone_scaling as c
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import joint_fixed_boxes as f
from experiments.socp_conditioning import joint_scaling as j
from experiments.socp_conditioning import optimality_audit as oa

BASE_WORKER = c.worker
ARMS = tuple(f.ARMS)


def context():
    base = f.context()
    return base | {
        "experiment": "joint_scaling_exact_fixed_coordinate_substitution",
        "additional_sources": base["additional_sources"]
        | {str(Path(__file__).relative_to(d.ROOT)): d.sha(__file__)},
    }


class Reduction:
    def __init__(self, data, allowed):
        self.original = data
        A, P = data["A"].tocsr(), data["P"].tocsc()
        if (P != P.T).nnz:
            raise ValueError("expected symmetric P")
        selected = {}
        for row in range(data["dims"].zero):
            start, stop = A.indptr[row : row + 2]
            if stop - start != 1:
                continue
            col, coefficient = int(A.indices[start]), A.data[start]
            if col not in allowed or coefficient == 0:
                continue
            if col in selected:
                raise ValueError("multiple defining equalities for fixed coordinate")
            value = data["b"][row] / coefficient
            if not np.isfinite(value):
                raise ValueError("nonfinite fixed value")
            selected[col] = (row, value, coefficient)
        if not selected:
            raise ValueError("no exact fixed Pg/p_nd coordinates")
        self.fixed = np.array(sorted(selected), dtype=int)
        self.rows = np.array([selected[i][0] for i in self.fixed], dtype=int)
        self.values = np.array([selected[i][1] for i in self.fixed])
        self.coefficients = np.array([selected[i][2] for i in self.fixed])
        self.free = np.setdiff1d(np.arange(A.shape[1]), self.fixed)
        self.keep = np.setdiff1d(np.arange(A.shape[0]), self.rows)
        v = self.values
        self.offset = float(
            0.5 * v @ (P[self.fixed][:, self.fixed] @ v) + data["c"][self.fixed] @ v
        )
        dims = deepcopy(data["dims"])
        dims.zero -= len(self.rows)
        self.data = data | dict(
            A=A[self.keep][:, self.free].tocsc(),
            b=data["b"][self.keep] - A[self.keep][:, self.fixed] @ v,
            P=P[self.free][:, self.free].tocsc(),
            c=data["c"][self.free] + P[self.free][:, self.fixed] @ v,
            dims=dims,
        )
        self.evidence = dict(
            fixed_indices=self.fixed,
            fixed_values=v,
            removed_equality_rows=self.rows,
            equality_coefficients=self.coefficients,
            free_indices=self.free,
            retained_rows=self.keep,
            objective_offset=self.offset,
            original_shape=A.shape,
            reduced_shape=self.data["A"].shape,
            families=["Pg", "p_nd"],
            dual_note="eliminated equality multipliers reconstructed from stationarity; not native solver multipliers",
            identity_checks=self.check(),
        )

    def restore(self, raw):
        full = self.original
        x, s, z = (
            np.zeros(full["A"].shape[1]),
            np.zeros(full["A"].shape[0]),
            np.zeros(full["A"].shape[0]),
        )
        x[self.fixed], x[self.free] = self.values, raw.x
        s[self.keep], z[self.keep] = raw.s, raw.z
        gradient = full["P"] @ x + full["c"] + full["A"].T @ z
        z[self.rows] = -gradient[self.fixed] / self.coefficients
        snapshot = c.snapshot(raw)
        snapshot.pop("status")
        return SimpleNamespace(
            **(
                snapshot
                | dict(
                    status=raw.status,
                    x=x,
                    s=s,
                    z=z,
                    obj_val=raw.obj_val + self.offset,
                    obj_val_dual=raw.obj_val_dual + self.offset,
                )
            )
        )

    def check(self):
        """Check primal, objective, dual and slack maps on deterministic points."""
        rng = np.random.default_rng(0)
        new, old = self.data, self.original
        x, s, z = (
            rng.normal(size=k) for k in (len(self.free), len(self.keep), len(self.keep))
        )
        raw = SimpleNamespace(
            x=x,
            s=s,
            z=z,
            status="diagnostic",
            obj_val=0.0,
            obj_val_dual=0.0,
            r_prim=0.0,
            r_dual=0.0,
            iterations=0,
            solve_time=0.0,
        )
        full = self.restore(raw)
        pairs = {
            "primal": (
                old["A"] @ full.x + full.s - old["b"],
                np.bincount(
                    self.keep,
                    weights=new["A"] @ x + s - new["b"],
                    minlength=old["A"].shape[0],
                ),
            ),
            "objective": (
                0.5 * full.x @ (old["P"] @ full.x) + old["c"] @ full.x,
                0.5 * x @ (new["P"] @ x) + new["c"] @ x + self.offset,
            ),
            "free_stationarity": (
                (old["P"] @ full.x + old["c"] + old["A"].T @ full.z)[self.free],
                new["P"] @ x + new["c"] + new["A"].T @ z,
            ),
            "complementarity": (full.s @ full.z, s @ z),
        }
        errors = {}
        for name, (a, b) in pairs.items():
            error = oa.inf(np.asarray(a) - b) / max(1, oa.inf(b))
            if not np.isfinite(error) or error > 1e-11:
                raise ValueError(f"substitution identity failed: {name}")
            errors[name] = error
        return errors

    def save(self, path, data):
        arrays = {k: data[k] for k in ("b", "c")}
        for key in ("A", "P"):
            matrix = data[key].tocsc()
            arrays.update(
                {
                    key + "_" + name: getattr(matrix, name)
                    for name in ("data", "indices", "indptr", "shape")
                }
            )
        with path.open("xb") as stream:
            np.savez_compressed(stream, **arrays)
        return dict(
            sha256=d.sha(path),
            shape=data["A"].shape,
            cone_dimensions=str(data["dims"]),
            coordinate_order="original canonical free_indices; rows original retained_rows",
        )


def reduce(data, inverse):
    variables = data["param_prob"].variables
    allowed = {
        i
        for v in variables
        if v.name() in ("Pg", "p_nd")
        for i in range(
            inverse[-2].var_offsets[v.id], inverse[-2].var_offsets[v.id] + v.size
        )
    }
    return Reduction(data, allowed)


def worker(directory, reference, label):
    case = f.ARMS[label][0]

    def verify(data, inverse, input_hash, fixed):
        return c.verify_reference(
            data,
            inverse,
            input_hash,
            fixed,
            directory=d.HERE / "results/joint_fixed_boxes_001" / label,
        )

    BASE_WORKER(
        directory,
        reference,
        label,
        scaling_strategy=j.joint_scales,
        case_name=case,
        reference_validator=verify,
        model_variant="fixed_boxes",
        canonical_reduction=reduce,
    )


if __name__ == "__main__":
    with (
        patch.object(c, "context", context),
        patch.object(c, "ARMS", ARMS),
        patch.object(c, "worker", worker),
    ):
        c.main(worker_module=__spec__.name)
