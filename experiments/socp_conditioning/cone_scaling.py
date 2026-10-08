"""Four-arm, exactly equivalent cone-preserving scaling smoke test.

An experiment-local CLARABEL interface hook preserves build.solve(), CVXPY's
inverse reduction and public physical audits. Production code is unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from scipy import sparse
from cvxpy.reductions.solvers.conic_solvers.clarabel_conif import CLARABEL

from cvxopf import audit_socp_relaxation, extract_results
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import matrix_conditioning as m
from experiments.socp_conditioning import mosek_check, no_shedding as n
from experiments.socp_conditioning import optimality_audit as oa

ARMS = ("shedding_baseline", "shedding_scaled", "fixed_baseline", "fixed_scaled")
PASSES = 5
SCALE_MIN, SCALE_MAX = 1e-6, 1e6
BASE_SOLVE = CLARABEL.solve_via_data


def context():
    return d.context() | {
        "experiment": "cone_preserving_canonical_scaling",
        "scaling_passes": PASSES,
        "cumulative_scale_bounds": [SCALE_MIN, SCALE_MAX],
        "additional_sources": {
            str(Path(p).relative_to(d.ROOT)): d.sha(p)
            for p in (
                __file__,
                m.__file__,
                n.__file__,
                oa.__file__,
                mosek_check.__file__,
            )
        },
    }


def validate_scales(A, layout, R, D):
    if R.shape != (A.shape[0],) or D.shape != (A.shape[1],):
        raise ValueError("scale dimensions do not match canonical matrix")
    if not all(np.all(np.isfinite(v) & (v > 0)) for v in (R, D)):
        raise ValueError("scales must be finite and strictly positive")
    zero, nonneg, soc = layout
    if zero + nonneg + sum(soc) != A.shape[0]:
        raise ValueError("cone layout does not cover rows")
    start = zero + nonneg
    for size in soc:
        if not np.all(R[start : start + size] == R[start]):
            raise ValueError("SOC coordinates must share one row multiplier")
        start += size


def scales(A, layout):
    """Five row/column passes; a SOC uses its largest coordinate row norm.

    Zero rows/columns keep their scale. Clip cumulative scales, not problem
    coefficients. This depends on matrix data only, never a solver outcome.
    """
    R, D = np.ones(A.shape[0]), np.ones(A.shape[1])
    B = sparse.csr_matrix(A, copy=True)
    for _ in range(PASSES):
        row = np.sqrt(np.asarray(B.multiply(B).sum(axis=1)).ravel())
        start = sum(layout[:2])
        for size in layout[2]:
            row[start : start + size] = np.max(row[start : start + size])
            start += size
        next_R = np.clip(R / np.where(row > 0, row, 1), SCALE_MIN, SCALE_MAX)
        B = sparse.diags(next_R / R) @ B
        R = next_R
        col = np.sqrt(np.asarray(B.multiply(B).sum(axis=0)).ravel())
        next_D = np.clip(D / np.where(col > 0, col, 1), SCALE_MIN, SCALE_MAX)
        B = B @ sparse.diags(next_D / D)
        D = next_D
    validate_scales(A, layout, R, D)
    return R, D


def transform(data, R, D):
    layout = oa.cone_layout(str(data["dims"]), data["A"].shape[0])
    validate_scales(data["A"], layout, R, D)
    return data | {
        "A": (sparse.diags(R) @ data["A"] @ sparse.diags(D)).tocsc(),
        "P": (sparse.diags(D) @ data["P"] @ sparse.diags(D)).tocsc(),
        "b": R * data["b"],
        "c": D * data["c"],
    }


def mapped_solution(raw, R, D):
    # q and P were transformed by substitution, with no objective multiplier.
    return SimpleNamespace(
        **{
            k: getattr(raw, k)
            for k in (
                "status",
                "obj_val",
                "obj_val_dual",
                "r_prim",
                "r_dual",
                "iterations",
                "solve_time",
            )
        },
        x=D * np.asarray(raw.x),
        s=np.asarray(raw.s) / R,
        z=R * np.asarray(raw.z),
    )


def snapshot(solution):
    return {
        k: getattr(solution, k)
        for k in (
            "x",
            "s",
            "z",
            "obj_val",
            "obj_val_dual",
            "r_prim",
            "r_dual",
            "iterations",
            "solve_time",
        )
    } | {"status": str(solution.status)}


def verify_mapping(data, changed, R, D):
    """Check objective, residual, dual stationarity and complementarity maps."""
    rng = np.random.default_rng(0)
    x, s, z = (rng.normal(size=k) for k in (len(D), len(R), len(R)))
    hx, hs, hz = x / D, R * s, z / R
    pairs = {
        "primal": (
            changed["A"] @ hx + hs - changed["b"],
            R * (data["A"] @ x + s - data["b"]),
        ),
        "stationarity": (
            changed["P"] @ hx + changed["c"] + changed["A"].T @ hz,
            D * (data["P"] @ x + data["c"] + data["A"].T @ z),
        ),
        "objective": (
            0.5 * hx @ (changed["P"] @ hx) + changed["c"] @ hx,
            0.5 * x @ (data["P"] @ x) + data["c"] @ x,
        ),
        "complementarity": (hs @ hz, s @ z),
        "roundtrip_x": (D * hx, x),
        "roundtrip_s": (hs / R, s),
        "roundtrip_z": (R * hz, z),
    }
    errors = {}
    for name, (a, b) in pairs.items():
        error = oa.inf(np.asarray(a) - b) / max(1, oa.inf(b))
        if not np.isfinite(error) or error > 1e-11:
            raise ValueError(f"transformation identity failed: {name}: {error}")
        errors[name] = error
    return errors


def verify_reference(data, inverse, input_hash, fixed, *, directory=None):
    directory = directory or (
        d.HERE
        / "results"
        / (
            "no_shedding_001/clarabel"
            if fixed
            else "equilibration_001/surplus-equil_default"
        )
    )
    old = m.verified_record(directory)
    if old["input_sha256"] != input_hash:
        raise ValueError("historical physical input differs")
    path = directory / "canonical.npz"
    if d.sha(path) != old["canonical_archive"]["sha256"]:
        raise ValueError("historical matrix hash mismatch")
    with np.load(path, allow_pickle=False) as z:
        for key in ("A", "P"):
            current = data[key].tocsc()
            for part in ("data", "indices", "indptr", "shape"):
                np.testing.assert_array_equal(
                    getattr(current, part), z[key + "_" + part]
                )
        for key in ("b", "c"):
            np.testing.assert_array_equal(data[key], z[key])
    if (
        str(data["dims"]) != old["canonical_archive"]["cone_dimensions"]
        or float(inverse[-1]["offset"]) != old["canonical_evidence"]["canonical_offset"]
    ):
        raise ValueError("historical cones or objective offset differs")
    return dict(
        directory=str(directory),
        arm_sha256=d.sha(directory / "arm.json.gz"),
        canonical_sha256=d.sha(path),
        canonical_arrays_exact=True,
    )


def worker(
    directory,
    reference,
    label,
    *,
    scaling_strategy=None,
    case_name="surplus",
    reference_validator=verify_reference,
    model_variant="cones",
    canonical_reduction=None,
):
    start = time.monotonic()
    record = dict(
        schema=1,
        label=label,
        context=context(),
        exception=None,
        result=None,
        audit=None,
        native_solution=None,
        objective_divisor=1,
        timings={},
    )
    raw = None
    changed = None
    fixed = label.startswith("fixed")
    try:
        kwargs, historical = d.load_case(case_name, reference)
        if fixed:
            kwargs = n.fixed_inputs(kwargs)
        record.update(
            input_sha256=d.input_digest(kwargs), historical_reference=historical
        )
        began = time.monotonic()
        build, original, _ = d.build_variant(kwargs, model_variant)
        record["timings"]["construction_seconds"] = time.monotonic() - began
        began = time.monotonic()
        data, _, inverse = build.prob.get_problem_data(
            "CLARABEL", canon_backend=build.canonicalization_backend
        )
        record["timings"]["canonicalization_seconds"] = time.monotonic() - began
        record["reference"] = reference_validator(
            data, inverse, record["input_sha256"], fixed
        )
        layout = oa.cone_layout(str(data["dims"]), data["A"].shape[0])
        began = time.monotonic()
        reduction = (
            None if canonical_reduction is None else canonical_reduction(data, inverse)
        )
        solve_data = data if reduction is None else reduction.data
        solve_layout = oa.cone_layout(str(solve_data["dims"]), solve_data["A"].shape[0])
        record["timings"]["substitution_seconds"] = time.monotonic() - began
        began = time.monotonic()
        R, D = (
            (
                scales(solve_data["A"], solve_layout)
                if scaling_strategy is None
                else scaling_strategy(solve_data, solve_layout)
            )
            if label.endswith("scaled")
            else (np.ones(solve_data["A"].shape[0]), np.ones(solve_data["A"].shape[1]))
        )
        changed = (
            transform(solve_data, R, D) if label.endswith("scaled") else solve_data
        )
        record["timings"]["scaling_seconds"] = time.monotonic() - began
        record["mapping_check"] = verify_mapping(solve_data, changed, R, D)
        with (directory / "scales.npz").open("xb") as stream:
            np.savez_compressed(stream, R=R, D=D)
        record["scales_sha256"] = d.sha(directory / "scales.npz")
        record["scale_ranges"] = dict(R=m.distribution(R), D=m.distribution(D))
        record["canonical_archive"] = d.save_canonical(
            directory / "canonical.npz", data, inverse
        )
        if reduction is None:
            record["transformed_archive"] = d.save_canonical(
                directory / "transformed.npz", changed, inverse
            )
        else:
            record["substitution"] = reduction.evidence
            record["reduced_archive"] = reduction.save(
                directory / "reduced.npz", solve_data
            )
            record["transformed_archive"] = reduction.save(
                directory / "transformed.npz", changed
            )

        def restore(solution):
            unscaled = mapped_solution(solution, R, D)
            return unscaled if reduction is None else reduction.restore(unscaled)

        calls = []

        def interface(
            self, incoming, warm_start, verbose, solver_opts, solver_cache=None
        ):
            nonlocal raw
            if warm_start or calls:
                raise ValueError("one fresh solve only")
            for key in ("A", "P"):
                if (incoming[key] != data[key]).nnz:
                    raise ValueError("canonical input changed before solve")
            for key in ("b", "c"):
                np.testing.assert_array_equal(incoming[key], data[key])
            calls.append(label)
            raw = BASE_SOLVE(self, changed, False, verbose, solver_opts, solver_cache)
            return restore(raw)

        record["solver_options"] = dict(d.e3.DEFAULT_PROTOCOL["convex_options"])
        began = time.monotonic()
        try:
            with patch.object(CLARABEL, "solve_via_data", interface):
                build.solve(
                    solver="CLARABEL",
                    warm_start=False,
                    verbose=True,
                    **record["solver_options"],
                )
        except Exception as exc:
            record["exception"] = f"{type(exc).__name__}: {exc}"
        record["timings"]["solve_interface_seconds"] = time.monotonic() - began
        record["optimizer_calls"] = len(calls)
        record["native_info"] = d.e3.convergence_diagnostics(build)
        if raw is not None:
            record["transformed_native_solution"] = snapshot(raw)
            record["native_solution"] = snapshot(restore(raw))
            if reduction is not None:
                record["reduced_native_solution"] = snapshot(mapped_solution(raw, R, D))
            record["residual_coordinate_note"] = (
                "native r_prim/r_dual describe transformed coordinates; canonical_evidence is independently mapped to original coordinates"
            )
        began = time.monotonic()
        result = extract_results(build)
        record["result"] = result
        usable = (
            original.value is not None
            and np.isfinite(original.value)
            and all(
                v.value is not None and np.isfinite(v.value).all()
                for v in build.prob.variables()
            )
        )
        if usable:
            result["objective"] = float(original.value)
            named = {
                k: float(v.value)
                for k, v in build.expressions.items()
                if k.endswith("_cost")
            }
            record["named_costs"] = named
            record["audit"] = (n.audit if fixed else d.original_audit)(
                case_name, build, result, kwargs, named
            )
            record["relaxation_audit"] = audit_socp_relaxation(
                build, result, tolerances=d.e3.RELAXATION_TOLERANCES
            )
            record["canonical_evidence"] = d.canonical_evidence(
                data, inverse, record["native_solution"], result["objective"], 1
            )
            x = np.asarray(record["native_solution"]["x"])
            z = np.asarray(record["native_solution"]["z"])
            record["original_cones"] = dict(
                primal=oa.cone_errors(data["b"] - data["A"] @ x, layout),
                dual=oa.cone_errors(z, layout, dual=True),
            )
        record["timings"]["extraction_audit_seconds"] = time.monotonic() - began
        began = time.monotonic()
        record["conditioning"] = {
            name: dict(
                A=m.factorized_spectrum(problem["A"]),
                A_coefficients=m.distribution(problem["A"].data),
                c=m.distribution(problem["c"]),
                b=m.distribution(problem["b"]),
                P=m.distribution(problem["P"].data),
            )
            for name, problem in (("original", data), ("transformed", changed))
        }
        record["timings"]["offline_conditioning_seconds"] = time.monotonic() - began
        record["context_after"] = context()
        if record["context_after"] != record["context"]:
            raise ValueError("execution context changed")
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    record["accepted"] = d.successful(record)
    record["worker_elapsed_before_archive_seconds"] = time.monotonic() - start
    d.publish(directory / "arm.json.gz", d.e3.encode(record))
    d.publish(
        directory / "completion.json",
        dict(
            accepted=record["accepted"],
            arm_sha256=d.sha(directory / "arm.json.gz"),
            worker_wall_seconds=time.monotonic() - start,
        ),
    )


def main(*, worker_module=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--label", choices=ARMS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    output, reference = args.output.resolve(), args.reference.resolve()
    if args.worker:
        worker(output, reference, args.label)
        return
    output.mkdir(parents=True, exist_ok=False)
    d.publish(output / "binding.json", context())
    summaries = []
    for label in ARMS:
        folder = output / label
        mosek_check.supervise(
            folder,
            reference,
            worker_module=worker_module or __spec__.name,
            context_factory=context,
            worker_arguments=("--label", label),
        )
        supervision = json.loads((folder / "supervision.json").read_text())
        if supervision["classification"] != "completed":
            raise RuntimeError("stop after resource/process failure")
        record = m.verified_record(folder)
        if (
            record["context"] != context()
            or record.get("context_after") != record["context"]
        ):
            raise ValueError("worker/source context mismatch or incomplete execution")
        optimality = oa.analyze(folder) if record.get("canonical_evidence") else None
        d.publish(folder / "optimality.json", optimality)
        summaries.append(
            dict(
                label=label,
                accepted=record["accepted"],
                exception=record["exception"],
                status=(record.get("native_solution") or {}).get("status"),
                objective=(record.get("result") or {}).get("objective"),
                evidence=record.get("canonical_evidence"),
                conditioning=record.get("conditioning"),
                timings=record["timings"],
                arm_sha256=d.sha(folder / "arm.json.gz"),
                supervision_sha256=d.sha(folder / "supervision.json"),
                optimality_sha256=d.sha(folder / "optimality.json"),
            )
        )
    d.publish(
        output / "summary.json",
        dict(
            context=context(),
            arms=summaries,
            optimization_solves=len(ARMS),
            promotional=False,
        ),
    )


if __name__ == "__main__":
    main()
