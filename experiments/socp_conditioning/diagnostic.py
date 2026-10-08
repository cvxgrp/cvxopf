"""Isolated serial SOCP scaling comparison; no solves or writes on import."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
from unittest.mock import patch

import cvxpy as cp
import numpy as np
import pandas as pd

from cvxopf import (
    NondispatchableUnit,
    StorageUnitIdeal,
    audit_socp_relaxation,
    build_opf_multistep,
    extract_results,
)
from cvxopf import generator, nondispatchable, storage
from cvxopf.load import loads_from_matpower
from cvxopf.testcases import case9
from experiments.case118_tracy_2021 import e3
from tests.socp_matched import digest as input_digest, objective_components


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE_COMMIT = "dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a"
VARIANTS = ("baseline", "cones", "objective", "combined")
PREFERENCE = ("cones", "objective", "combined")
OBJECTIVE_DIVISOR = 1_000_000.0
SWEEP_DIVISORS = (1.0, 10.0, 100.0, 1000.0)
EQUILIBRATION = {
    "equil_default": dict(
        equilibrate_enable=True,
        equilibrate_min_scaling=1e-4,
        equilibrate_max_scaling=1e4,
    ),
    "equil_off": dict(
        equilibrate_enable=False,
        equilibrate_min_scaling=1e-4,
        equilibrate_max_scaling=1e4,
    ),
    "equil_narrow": dict(
        equilibrate_enable=True,
        equilibrate_min_scaling=1e-2,
        equilibrate_max_scaling=1e2,
    ),
}
WALL_SECONDS = 180.0
RSS_MIB = 4096.0
SMOKE_SUMMARY_SHA256 = (
    "158c5f3c04f55a367db99514d1a6bb2c3ea337bd5df03c0655b2a7c8f06bf0a2"
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_value(value):
    """Strict JSON; unavailable nonfinite diagnostics are explicitly null."""
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    if isinstance(value, np.ndarray):
        return json_value(value.tolist())
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def publish(path, value):
    """Immutable artifacts: callers always supply a fresh directory."""
    raw = (
        json.dumps(json_value(value), sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    if path.suffix == ".gz":
        raw = gzip.compress(raw, mtime=0)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(temporary, path)  # Refuses overwrite, unlike replace.
    temporary.unlink()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def context():
    paths = sorted((ROOT / "src/cvxopf").rglob("*.py"))
    paths += sorted((ROOT / "experiments/case118_tracy_2021").glob("*.py"))
    paths += [
        HERE / "diagnostic.py",
        HERE / "PROTOCOL.md",
        ROOT / "uv.lock",
        ROOT / "tests/socp_matched.py",
        ROOT / "experiments/case118_annual_hierarchy/pglib_case.py",
    ]
    import cvxopf

    if Path(cvxopf.__file__).resolve() != ROOT / "src/cvxopf/__init__.py":
        raise ValueError("must import cvxopf from the isolated worktree")
    return dict(
        commit=git("rev-parse", "HEAD"),
        worktree_status=git("status", "--porcelain"),
        qualification="uncommitted isolated experiment; production sources unchanged",
        sources={str(p.relative_to(ROOT)): sha(p) for p in paths},
        python=sys.version,
        platform=platform.platform(),
        architecture=platform.machine(),
        packages={
            k: version(k) for k in ("cvxpy", "clarabel", "numpy", "scipy", "pandas")
        },
        cvxopf_path=str(Path(cvxopf.__file__).resolve()),
    )


def unit_cones(p, q, ratings):
    """Same circle, normalized by each positive device nameplate, time-last."""
    ratings = np.asarray(ratings, float)
    if (
        p.shape != q.shape
        or ratings.shape != (p.shape[0],)
        or not np.all(np.isfinite(ratings) & (ratings > 0))
    ):
        raise ValueError("invalid device shapes or ratings")
    pn = cp.multiply(1 / ratings[:, None], p)
    qn = cp.multiply(1 / ratings[:, None], q)
    return cp.SOC(
        np.ones(p.size),
        cp.vstack(
            [
                cp.reshape(pn, (p.size,), order="F"),
                cp.reshape(qn, (q.size,), order="F"),
            ]
        ),
        axis=0,
    )


def nd_cones(units, p_nd, q_nd, p_available):
    return [
        p_nd >= 0,
        p_nd <= p_available,
        unit_cones(p_nd, q_nd, [u.apparent_power_rating for u in units]),
    ]


def storage_cones(units, b, b_q, soc):
    return [
        unit_cones(b, b_q, [u.apparent_power_rating for u in units]),
        soc[:, 1:] >= 0,
        soc[:, 1:] <= np.array([u.capacity for u in units])[:, None],
    ]


def exact_box(x, lower, upper):
    """Experiment-only: exact fixed coordinates are equalities, never near-fixed."""
    lo = np.broadcast_to(np.asarray(lower, float), x.shape).ravel(order="F")
    hi = np.broadcast_to(np.asarray(upper, float), x.shape).ravel(order="F")
    if not np.all(np.isfinite(lo) & np.isfinite(hi) & (lo <= hi)):
        raise ValueError("invalid finite box")
    flat = cp.reshape(x, (x.size,), order="F")
    fixed = lo == hi
    out = []
    if np.any(fixed):
        out.append(flat[fixed] == lo[fixed])
    if np.any(~fixed):
        out.extend([flat[~fixed] >= lo[~fixed], flat[~fixed] <= hi[~fixed]])
    return out


def generator_fixed_boxes(Pg, Qg, Pgmin, Pgmax, Qgmin, Qgmax):
    return exact_box(Pg, Pgmin, Pgmax) + [Qg >= Qgmin, Qg <= Qgmax]


def nd_fixed_boxes(units, p_nd, q_nd, p_available):
    return exact_box(p_nd, 0, p_available) + [
        unit_cones(p_nd, q_nd, [u.apparent_power_rating for u in units])
    ]


def build_variant(kwargs, variant, objective_divisor=None):
    if variant in EQUILIBRATION:
        variant = "cones"
    if variant not in (*VARIANTS, "fixed_boxes") or kwargs.get("formulation") != "socp":
        raise ValueError("experiment supports only declared SOCP variants")
    if kwargs.get("temporal_assembly") != "vectorized":
        raise ValueError("experiment requires vectorized assembly")
    if objective_divisor is not None and (
        variant != "combined"
        or isinstance(objective_divisor, bool)
        or objective_divisor not in SWEEP_DIVISORS
    ):
        raise ValueError("only frozen normalized-cone sweep divisors are allowed")
    with ExitStack() as stack:
        if variant in ("cones", "combined", "fixed_boxes"):
            stack.enter_context(
                patch.object(
                    nondispatchable,
                    "vectorized_ac_operating_constraints",
                    nd_fixed_boxes if variant == "fixed_boxes" else nd_cones,
                )
            )
            stack.enter_context(
                patch.object(
                    storage, "vectorized_ac_operating_constraints", storage_cones
                )
            )
        if variant == "fixed_boxes":
            stack.enter_context(
                patch.object(
                    generator, "ac_operating_constraints", generator_fixed_boxes
                )
            )
        build = build_opf_multistep(**kwargs)
    original = build.prob.objective.expr
    divisor = OBJECTIVE_DIVISOR if variant in ("objective", "combined") else 1.0
    if objective_divisor is not None:
        divisor = float(objective_divisor)
    if divisor != 1:
        build = replace(
            build,
            prob=cp.Problem(cp.Minimize(original / divisor), build.prob.constraints),
        )
    if not build.prob.is_dcp():
        raise ValueError("conditioning must preserve DCP")
    return build, original, divisor


def smoke_inputs():
    case = case9()
    loads = [
        replace(u, shedding_cost_per_mwh=10_000.0)
        for u in loads_from_matpower(case["bus"])
    ]
    return dict(
        case=case,
        T=3,
        delta=0.5,
        temporal_assembly="vectorized",
        formulation="socp",
        loads=loads,
        df_load_p=pd.DataFrame({u.device_id: [u.p_load_mw] * 3 for u in loads}),
        df_load_q=pd.DataFrame({u.device_id: [u.q_load_mvar] * 3 for u in loads}),
        storage=[
            StorageUnitIdeal(
                bus=5,
                apparent_power_rating=15,
                capacity=20,
                initial_soc=10,
                terminal_soc=10,
                terminal_constraint="equality",
                aging_weight=0.1,
                device_id="smoke-storage",
            )
        ],
        nondispatchable=[
            NondispatchableUnit(
                bus=7, p_available=8, apparent_power_rating=12, device_id="smoke-nd"
            )
        ],
        df_nd=pd.DataFrame({"smoke-nd": [8.0, 4.0, 10.0]}),
    )


def load_case(name, reference):
    if name == "case9":
        return smoke_inputs(), None
    number = {"surplus": 2, "deficit": 6}[name]
    binding = json.loads((reference / "binding.json").read_text())
    if (
        binding["context"]["commit"] != BASE_COMMIT
        or git("rev-parse", "HEAD") != BASE_COMMIT
    ):
        raise ValueError("unexpected E3/source base")
    for path, expected in binding["context"]["code_sha256"].items():
        if sha(ROOT / path) != expected:
            raise ValueError(f"historical execution source differs: {path}")
    directory = reference / f"arm-{number:03d}/attempt-000"
    completion = json.loads((directory / "completion.json").read_text())
    for name_, expected in completion["artifacts"].items():
        if sha(directory / name_) != expected:
            raise ValueError(f"historical artifact changed: {name_}")
    with gzip.open(directory / "result.json.gz", "rt") as stream:
        archived = json.load(stream)
    arm = binding["study"]["arms"][number]
    kwargs = e3.kwargs_for_arm(
        e3.verified_inputs(), arm, binding["study"]["storage_device_ids"]
    )
    neutral = dict(
        case=kwargs["case"],
        kwargs={k: v for k, v in kwargs.items() if k not in {"case", "formulation"}},
    )
    if input_digest(neutral) != archived["mathematical_input_sha256"]:
        raise ValueError("mathematical input mismatch")
    return kwargs, dict(
        arm=arm,
        binding_sha256=sha(reference / "binding.json"),
        completion_sha256=sha(directory / "completion.json"),
        result_sha256=sha(directory / "result.json.gz"),
        mathematical_input_sha256=archived["mathematical_input_sha256"],
    )


def original_audit(case_name, build, result, kwargs, named):
    if case_name != "case9":
        common, _ = e3.audit(build, result, kwargs, named)
        return common
    relaxation = audit_socp_relaxation(
        build, result, tolerances=e3.RELAXATION_TOLERANCES
    )
    costs = objective_components(dict(case=kwargs["case"], kwargs=kwargs), result)
    errors = {k: abs(v - named[k]) for k, v in costs.items()}
    error = abs(sum(costs.values()) - result["objective"])
    return dict(
        passed=relaxation["feasible"] is True
        and max([error, *errors.values()]) <= 1e-4,
        reconstructed_costs=costs,
        cost_errors=errors,
        objective_error=error,
    )


def canonical_evidence(data, inverse, native, original_cost, divisor):
    """Numerical original-canonical equations, not normalized solver summaries."""
    x, s, z = (np.asarray(native[k]) for k in ("x", "s", "z"))
    A, P, c, b = data["A"], data["P"], data["c"], data["b"]
    primal = A @ x + s - b
    dual = P @ x + c + A.T @ z
    equality_count = data["dims"].zero
    offset = float(inverse[-1]["offset"])
    reconstructed = float(0.5 * x @ (P @ x) + c @ x)
    source_units = divisor * (native["obj_val"] + offset)
    return dict(
        primal_inf=float(np.max(abs(primal))),
        equality_inf=float(np.max(abs(primal[:equality_count]), initial=0)),
        dual_inf=float(np.max(abs(dual))),
        complementary_product=float(s @ z),
        canonical_objective_reconstructed=reconstructed,
        canonical_offset=offset,
        native_objective_reconstruction_error=abs(reconstructed - native["obj_val"]),
        native_primal_original_units=source_units,
        native_dual_original_units=divisor * (native["obj_val_dual"] + offset),
        native_gap_original_units=divisor
        * abs(native["obj_val"] - native["obj_val_dual"]),
        native_vs_original_objective=abs(source_units - original_cost),
        coefficient_ranges={
            k: dict(
                min_nonzero=float(np.min(abs(v[v != 0]))), max_abs=float(np.max(abs(v)))
            )
            for k, v in (("A", A.data), ("P", P.data), ("c", c), ("b", b))
            if np.any(v != 0)
        },
    )


def successful(record):
    evidence = record.get("canonical_evidence") or {}
    objective = (record.get("result") or {}).get("objective")
    return bool(
        record.get("exception") is None
        and (record.get("native_solution") or {}).get("status") == "Solved"
        and (record.get("audit") or {}).get("passed") is True
        and objective is not None
        and np.isfinite(objective)
        and evidence.get("native_vs_original_objective", float("inf"))
        <= 1e-4 + 1e-8 * abs(objective)
    )


def save_canonical(path, data, inverse):
    arrays = {k: data[k] for k in ("b", "c")}
    for key in ("A", "P"):
        matrix = data[key].tocsc()
        arrays.update(
            {
                key + "_" + k: getattr(matrix, k)
                for k in ("data", "indices", "indptr", "shape")
            }
        )
    with path.open("xb") as stream:
        np.savez_compressed(stream, **arrays)
    offset = 0
    rows = []
    for c in inverse[-2].constraints:
        rows.append(
            dict(
                id=c.id,
                kind=type(c).__name__,
                shape=c.shape,
                start=offset,
                stop=offset + c.size,
            )
        )
        offset += c.size
    return dict(
        sha256=sha(path),
        shape=data["A"].shape,
        cone_dimensions=str(data["dims"]),
        constraints=rows,
        variables=[
            dict(
                name=v.name(),
                id=v.id,
                shape=v.shape,
                offset=inverse[-2].var_offsets[v.id],
            )
            for v in data["param_prob"].variables
        ],
    )


def worker(directory, case_name, variant, reference, objective_divisor=None):
    start = time.monotonic()
    record = dict(
        schema=1,
        case=case_name,
        variant=variant,
        exception=None,
        result=None,
        audit=None,
        native_solution=None,
        timings={},
    )
    try:
        record["context"] = context()
        kwargs, historical = load_case(case_name, reference)
        record["historical_reference"] = historical
        record["input_sha256"] = input_digest(kwargs)
        began = time.monotonic()
        build, original, divisor = (
            build_variant(kwargs, variant)
            if objective_divisor is None
            else build_variant(kwargs, variant, objective_divisor)
        )
        record["timings"]["construction_seconds"] = time.monotonic() - began
        record["objective_divisor"] = divisor
        record["solver_options"] = dict(e3.DEFAULT_PROTOCOL["convex_options"])
        if variant in EQUILIBRATION:
            record["solver_options"].update(EQUILIBRATION[variant])
        began = time.monotonic()
        data, _, inverse = build.prob.get_problem_data(
            "CLARABEL", canon_backend=build.canonicalization_backend
        )
        record["timings"]["canonicalization_seconds"] = time.monotonic() - began
        began = time.monotonic()
        try:
            build.solve(
                solver="CLARABEL",
                warm_start=False,
                verbose=True,
                **record["solver_options"],
            )
        except Exception as exc:
            record["exception"] = f"{type(exc).__name__}: {exc}"
        record["timings"]["solve_seconds"] = time.monotonic() - began
        began = time.monotonic()
        record["native_info"] = e3.convergence_diagnostics(build)
        solver = build.prob._solver_cache.get("CLARABEL")
        if solver is not None:
            solution = solver.get_solution()
            native = {
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
            }
            native["status"] = str(solution.status)
            record["native_solution"] = native
        record["solver_objective"] = build.prob.value
        result = extract_results(build)
        usable = (
            original.value is not None
            and np.isfinite(original.value)
            and all(
                v.value is not None and np.isfinite(v.value).all()
                for v in build.prob.variables()
            )
        )
        record["result"] = result
        if usable:
            result["objective"] = float(original.value)
            named = {
                k: float(v.value)
                for k, v in build.expressions.items()
                if k.endswith("_cost")
            }
            record["named_costs"] = named
            record["audit"] = original_audit(case_name, build, result, kwargs, named)
            record["relaxation_audit"] = audit_socp_relaxation(
                build, result, tolerances=e3.RELAXATION_TOLERANCES
            )
            if record["native_solution"] is not None:
                record["canonical_evidence"] = canonical_evidence(
                    data, inverse, native, result["objective"], divisor
                )
        record["timings"]["extraction_audit_seconds"] = time.monotonic() - began
        record["context_after"] = context()
        if record["context_after"] != record["context"]:
            raise ValueError("diagnostic sources/context changed during arm")
        began = time.monotonic()
        record["canonical_archive"] = save_canonical(
            directory / "canonical.npz", data, inverse
        )
        record["timings"]["canonical_archive_seconds"] = time.monotonic() - began
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    record["accepted"] = successful(record)
    record["worker_elapsed_before_json_seconds"] = time.monotonic() - start
    began = time.monotonic()
    publish(directory / "arm.json.gz", e3.encode(record))
    publish(
        directory / "completion.json",
        dict(
            accepted=record["accepted"],
            arm_sha256=sha(directory / "arm.json.gz"),
            archive_seconds=time.monotonic() - began,
            worker_wall_seconds=time.monotonic() - start,
        ),
    )


def rss(pid):
    value = subprocess.check_output(
        ["ps", "-o", "rss=", "-p", str(pid)], text=True
    ).strip()
    return float(value) / 1024 if value else 0.0


def stop(process):
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def retained_equilibration_rejection(record, variant):
    return bool(
        (record["exception"] is not None or record.get("audit") is None)
        and variant in EQUILIBRATION
        and record["native_solution"] is not None
        and record.get("canonical_archive")
    )


def supervise(root, case_name, variant, reference, objective_divisor=None):
    suffix = "" if objective_divisor is None else f"-divisor-{objective_divisor:g}"
    directory = root / f"{case_name}-{variant}{suffix}"
    directory.mkdir()
    process = None
    began, peak = time.monotonic(), 0.0
    error = None
    classification = "completed"
    try:
        with (directory / "worker.log").open("xb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "experiments.socp_conditioning.diagnostic",
                    "--worker",
                    "--output",
                    str(directory),
                    "--case",
                    case_name,
                    "--variant",
                    variant,
                    "--reference",
                    str(reference),
                ]
                + (
                    []
                    if objective_divisor is None
                    else ["--objective-divisor", str(objective_divisor)]
                ),
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            while process.poll() is None:
                try:
                    peak = max(peak, rss(process.pid))
                except subprocess.CalledProcessError:
                    if process.poll() is None:
                        raise
                if peak > RSS_MIB or time.monotonic() - began > WALL_SECONDS:
                    classification = "resource_limit"
                    stop(process)
                    break
                time.sleep(0.5)
            process.wait()
            if process.returncode != 0 and classification == "completed":
                classification = "worker_failure"
    except BaseException as exc:
        classification = (
            "supervisor_interrupted"
            if not isinstance(exc, Exception)
            else "supervisor_failure"
        )
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        stop(process)
        publish(
            directory / "supervision.json",
            dict(
                classification=classification,
                exception=error,
                peak_rss_mib=peak,
                wall_seconds=time.monotonic() - began,
                returncode=None if process is None else process.returncode,
                worker_log_sha256=sha(directory / "worker.log"),
            ),
        )
    if classification != "completed":
        raise RuntimeError(f"diagnostic stopped: {classification}")
    completion = json.loads((directory / "completion.json").read_text())
    if sha(directory / "arm.json.gz") != completion["arm_sha256"]:
        raise ValueError("arm publication hash mismatch")
    with gzip.open(directory / "arm.json.gz", "rt") as stream:
        record = json.load(stream)
    if (
        record.get("canonical_archive")
        and sha(directory / "canonical.npz") != record["canonical_archive"]["sha256"]
    ):
        raise ValueError("canonical publication mismatch")
    if retained_equilibration_rejection(record, variant):
        # Expected scientific rejection is retained and does not prevent the
        # predeclared remaining solver-settings arms. Resource/process errors stop.
        summary = dict(
            case=case_name,
            variant=variant,
            artifact_directory=directory.name,
            accepted=False,
            status=record["native_solution"]["status"],
            exception=record["exception"],
            timings=record["timings"],
            peak_rss_mib=peak,
            arm_sha256=completion["arm_sha256"],
            supervision_sha256=sha(directory / "supervision.json"),
        )
        print(json.dumps(summary), flush=True)
        return summary
    if record["exception"] is not None:
        raise RuntimeError(record["exception"])
    summary = dict(
        case=case_name,
        variant=variant,
        artifact_directory=directory.name,
        objective_divisor=record["objective_divisor"],
        accepted=record["accepted"],
        status=record["native_solution"]["status"],
        objective=record["result"]["objective"],
        max_p_balance_mw=record["relaxation_audit"]["residuals"]["p_balance"][
            "maximum"
        ],
        native_gap_original_units=record["canonical_evidence"][
            "native_gap_original_units"
        ],
        native_vs_original_objective=record["canonical_evidence"][
            "native_vs_original_objective"
        ],
        dual_stationarity_inf_original_units=(
            record["canonical_evidence"]["dual_inf"] * record["objective_divisor"]
        ),
        timings=record["timings"],
        peak_rss_mib=peak,
        arm_sha256=completion["arm_sha256"],
        supervision_sha256=sha(directory / "supervision.json"),
    )
    print(json.dumps(summary), flush=True)
    return summary


def verified_smoke_reference(directory):
    """Retain the failed smoke gate, under the owner's prospective amendment."""
    path = directory / "summary.json"
    if sha(path) != SMOKE_SUMMARY_SHA256:
        raise ValueError("unexpected prior smoke summary")
    summary = json.loads(path.read_text())
    if summary["complete"] or summary["exception"] != (
        "RuntimeError: Case9 correctness gate rejected; no Case118 launch"
    ):
        raise ValueError("expected the retained unsuccessful smoke comparison")
    if [(a["case"], a["variant"]) for a in summary["arms"]] != [
        ("case9", v) for v in VARIANTS
    ]:
        raise ValueError("incomplete prior smoke matrix")
    for arm in summary["arms"]:
        folder = directory / ("case9-" + arm["variant"])
        for name, key in (
            ("arm.json.gz", "arm_sha256"),
            ("supervision.json", "supervision_sha256"),
        ):
            if sha(folder / name) != arm[key]:
                raise ValueError("prior smoke artifact changed")
        with gzip.open(folder / "arm.json.gz", "rt") as stream:
            record = json.load(stream)
        if sha(folder / "canonical.npz") != record["canonical_archive"]["sha256"]:
            raise ValueError("prior canonical evidence changed")
        completion = json.loads((folder / "completion.json").read_text())
        supervision = json.loads((folder / "supervision.json").read_text())
        if (
            completion["arm_sha256"] != arm["arm_sha256"]
            or supervision["classification"] != "completed"
            or supervision["returncode"] != 0
            or sha(folder / "worker.log") != supervision["worker_log_sha256"]
            or record["context"] != record["context_after"]
            or record["context"] != summary["context"]
            or not successful(record)
        ):
            raise ValueError("prior smoke lifecycle or individual audit mismatch")
    return dict(
        directory=str(directory),
        summary_sha256=SMOKE_SUMMARY_SHA256,
        original_smoke_gate_passed=False,
        disposition="owner-approved descriptive Case118 continuation; no threshold waiver",
    )


def run(root, reference, smoke_reference=None):
    if root.exists():
        raise ValueError("output must be fresh")
    prior = (
        None if smoke_reference is None else verified_smoke_reference(smoke_reference)
    )
    rss(os.getpid())  # Preflight before any solver can be launched.
    root.mkdir(parents=True)
    report = dict(
        schema=1,
        began_utc=datetime.now(timezone.utc).isoformat(),
        context=context(),
        arms=[],
        complete=False,
        selected=None,
        reviewed_smoke_reference=prior,
    )
    publish(
        root / "binding.json",
        dict(
            context=report["context"],
            reference=str(reference),
            variants=VARIANTS,
            preference=PREFERENCE,
            objective_divisor=OBJECTIVE_DIVISOR,
            wall_seconds=WALL_SECONDS,
            rss_mib=RSS_MIB,
            reviewed_smoke_reference=prior,
        ),
    )
    try:
        for case_name in ("case9", "surplus") if prior is None else ("surplus",):
            for variant in VARIANTS:
                report["arms"].append(supervise(root, case_name, variant, reference))
            if case_name == "case9":
                base = report["arms"][0]["objective"]
                if not all(
                    a["accepted"]
                    and abs(a["objective"] - base) <= 1e-4 + 1e-6 * abs(base)
                    for a in report["arms"]
                ):
                    raise RuntimeError(
                        "Case9 correctness gate rejected; no Case118 launch"
                    )
        accepted = {
            a["variant"]
            for a in report["arms"]
            if a["case"] == "surplus" and a["accepted"]
        }
        report["selected"] = next((v for v in PREFERENCE if v in accepted), None)
        if report["selected"] is not None:
            for variant in ("baseline", report["selected"]):
                report["arms"].append(supervise(root, "deficit", variant, reference))
        report["complete"] = True
    except BaseException as exc:
        report["exception"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["ended_utc"] = datetime.now(timezone.utc).isoformat()
        publish(root / "summary.json", report)


def run_sweep(root, reference, smoke_reference):
    """Approved four-factor descriptive sweep; no automatic follow-on solves."""
    if root.exists():
        raise ValueError("output must be fresh")
    prior = verified_smoke_reference(smoke_reference)
    rss(os.getpid())
    root.mkdir(parents=True)
    report = dict(
        schema=1,
        study="normalized_cone_objective_divisor_sweep",
        began_utc=datetime.now(timezone.utc).isoformat(),
        context=context(),
        reviewed_smoke_reference=prior,
        divisors=SWEEP_DIVISORS,
        arms=[],
        complete=False,
    )
    publish(
        root / "binding.json",
        dict(
            context=report["context"],
            reference=str(reference),
            reviewed_smoke_reference=prior,
            divisors=SWEEP_DIVISORS,
            case="surplus",
            variant="combined",
            wall_seconds=WALL_SECONDS,
            rss_mib=RSS_MIB,
        ),
    )
    try:
        for divisor in SWEEP_DIVISORS:
            report["arms"].append(
                supervise(root, "surplus", "combined", reference, divisor)
            )
        report["complete"] = True
    except BaseException as exc:
        report["exception"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["ended_utc"] = datetime.now(timezone.utc).isoformat()
        publish(root / "summary.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--case", choices=("case9", "surplus", "deficit"))
    parser.add_argument("--variant", choices=(*VARIANTS, "fixed_boxes", *EQUILIBRATION))
    parser.add_argument("--smoke-reference", type=Path)
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--fixed-box-test", action="store_true")
    parser.add_argument("--equilibration-test", action="store_true")
    parser.add_argument("--objective-divisor", type=float, choices=SWEEP_DIVISORS)
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    if args.worker:
        worker(
            args.output.resolve(),
            args.case,
            args.variant,
            args.reference.resolve(),
            args.objective_divisor,
        )
    elif args.equilibration_test:
        root, reference = args.output.resolve(), args.reference.resolve()
        if root.exists():
            raise ValueError("output must be fresh")
        load_case("surplus", reference)
        rss(os.getpid())
        root.mkdir(parents=True)
        publish(
            root / "binding.json",
            dict(
                context=context(),
                reference=str(reference),
                study="equilibration_only",
                settings=EQUILIBRATION,
                model="cones",
                objective_divisor=1,
                wall_seconds=WALL_SECONDS,
                rss_mib=RSS_MIB,
            ),
        )
        report = dict(complete=False, arms=[])
        try:
            for variant in EQUILIBRATION:
                report["arms"].append(supervise(root, "surplus", variant, reference))
            report["complete"] = True
        except BaseException as exc:
            report["exception"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            publish(root / "summary.json", report)
    elif args.fixed_box_test:
        root = args.output.resolve()
        if root.exists():
            raise ValueError("output must be fresh")
        reference = args.reference.resolve()
        # Validate inputs and the immutable comparison evidence before launch.
        load_case("surplus", reference)
        baseline = HERE / "results/sweep_001/surplus-combined-divisor-1"
        completion = json.loads((baseline / "completion.json").read_text())
        if sha(baseline / "arm.json.gz") != completion["arm_sha256"]:
            raise ValueError("baseline hash mismatch")
        root.mkdir(parents=True)
        publish(
            root / "binding.json",
            dict(
                context=context(),
                reference=str(reference),
                baseline=str(baseline),
                baseline_arm_sha256=completion["arm_sha256"],
                study="exact_fixed_real_power_boxes_only",
                objective_divisor=1,
                wall_seconds=WALL_SECONDS,
                rss_mib=RSS_MIB,
            ),
        )
        report = dict(complete=False, arms=[])
        try:
            report["arms"].append(supervise(root, "surplus", "fixed_boxes", reference))
            report["complete"] = True
        finally:
            publish(root / "summary.json", report)
    elif args.sweep:
        if args.smoke_reference is None:
            parser.error("--sweep requires the retained --smoke-reference")
        run_sweep(
            args.output.resolve(),
            args.reference.resolve(),
            args.smoke_reference.resolve(),
        )
    else:
        run(
            args.output.resolve(),
            args.reference.resolve(),
            None if args.smoke_reference is None else args.smoke_reference.resolve(),
        )


if __name__ == "__main__":
    main()
