"""Run the post-hoc M14c tight-CLARABEL representation diagnostic."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any, Mapping, cast
from unittest.mock import patch

import cvxpy as cp
from cvxpy.reductions.solvers.conic_solvers.clarabel_conif import CLARABEL
import numpy as np

from cvxopf import OPFBuild, extract_results
from cvxopf.generator import gen_cost_expr, generator_gencost
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.run_s4 import _software_versions, _terminate
from experiments.case118_annual_hierarchy.streaming_driver import process_rss_bytes
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
    sha256_path,
)
from experiments.case118_annual_hierarchy import streaming_runner
from experiments.m14_time_vectorization.m14c_prefix_fixture import (
    M14C_INTEGRATION_COMMIT,
    PREFIX_EXECUTION_LIMITS,
    PREFIX_LADDER_HORIZONS,
    load_prefix_fixture,
)
from experiments.m14_time_vectorization.run_m14c_prefix_profile import (
    PROFILE_OUTPUT_DIRECTORY,
    shared_production_fingerprint,
)


SCHEMA_VERSION = 1
DIAGNOSTIC_OUTPUT_DIRECTORY = Path(
    "experiments/m14_time_vectorization/results/m14c_case118_tight_tolerance_diagnostic"
)
PROFILE_RESULT_SHA256 = (
    "25077b64f41aa054ac04383e1c7c898da5139203e9bdc1c1c9994521971cff73"
)
PROFILE_ANALYSIS_SHA256 = (
    "b019795be48b1340a75d2cfcf8c471587748a20b4a1b2f10bdacb8059d37489b"
)
PROFILE_SHARED_PRODUCTION_FINGERPRINT = (
    "be9133707e9e7358ba22c5a57c907a88ede57b8acd2abb1ab17c99600cd1a706"
)
TIGHT_CLARABEL_OPTIONS: Mapping[str, float] = {
    "tol_gap_abs": 1e-10,
    "tol_gap_rel": 1e-10,
    "tol_feas": 1e-10,
}
REPRESENTATIONS = (
    ("stepwise", "CPP"),
    ("vectorized", "SCIPY"),
)
BOUND_AUDIT_TOLERANCE = 2e-5
BR_R = 2
DIAGNOSTIC_SOURCE_FILES = (
    "experiments/m14_time_vectorization/M14C_TIGHT_TOLERANCE_DIAGNOSTIC.md",
    "experiments/m14_time_vectorization/m14c_tight_tolerance_diagnostic.py",
    "experiments/m14_time_vectorization/m14c_prefix_fixture.py",
    "experiments/case118_annual_hierarchy/audit.py",
    "experiments/case118_annual_hierarchy/p0_fixture.py",
    "experiments/case118_annual_hierarchy/pglib_case.py",
    "experiments/case118_annual_hierarchy/scenario.py",
    "experiments/case118_annual_hierarchy/s4_fixture.py",
    "experiments/case118_annual_hierarchy/streaming_runner.py",
)


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _source_paths() -> tuple[Path, ...]:
    paths = {ROOT / name for name in DIAGNOSTIC_SOURCE_FILES}
    paths.update((ROOT / "src" / "cvxopf").rglob("*.py"))
    return tuple(sorted(paths))


def diagnostic_source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in _source_paths():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _number_or_none(value: object) -> float | int | str | None:
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    return str(value)


def _capture_clarabel_solution(
    captured: dict[str, object],
) -> tuple[Any, Any]:
    """Return the CLARABEL method and a wrapper retaining discarded statistics."""
    original = CLARABEL.solve_via_data

    def wrapped(self: object, *args: object, **kwargs: object) -> object:
        solution = original(self, *args, **kwargs)
        primal = _number_or_none(getattr(solution, "obj_val", None))
        dual = _number_or_none(getattr(solution, "obj_val_dual", None))
        absolute_gap = None
        relative_gap = None
        if isinstance(primal, (int, float)) and isinstance(dual, (int, float)):
            absolute_gap = abs(float(primal) - float(dual))
            relative_gap = absolute_gap / max(1.0, abs(float(primal)), abs(float(dual)))
        captured.update(
            {
                "status": str(getattr(solution, "status", "unknown")),
                "iterations": _number_or_none(getattr(solution, "iterations", None)),
                "solve_time_seconds": _number_or_none(
                    getattr(solution, "solve_time", None)
                ),
                "primal_objective": primal,
                "dual_objective": dual,
                "absolute_gap": absolute_gap,
                "relative_gap": relative_gap,
                "primal_residual": _number_or_none(getattr(solution, "r_prim", None)),
                "dual_residual": _number_or_none(getattr(solution, "r_dual", None)),
            }
        )
        return solution

    return original, wrapped


def _solver_statistics(
    build: OPFBuild, clarabel: Mapping[str, object]
) -> Mapping[str, object]:
    stats = build.prob.solver_stats
    return {
        "solver_name": None if stats is None else stats.solver_name,
        "num_iters": None if stats is None else stats.num_iters,
        "setup_time_seconds": None if stats is None else stats.setup_time,
        "solve_time_seconds": None if stats is None else stats.solve_time,
        "extra_stats": None if stats is None else _json_value(stats.extra_stats),
        "clarabel": dict(clarabel),
    }


def _maximum_violation(values: object, lower: object, upper: object) -> float:
    array = np.asarray(values, dtype=float)
    lower_array = np.asarray(lower, dtype=float)
    upper_array = np.asarray(upper, dtype=float)
    return float(
        max(
            0.0,
            float(np.max(lower_array - array)),
            float(np.max(array - upper_array)),
        )
    )


def _time_first(values: object, shape: tuple[int, ...]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.shape == shape:
        return array
    if array.T.shape == shape:
        return array.T
    if array.ndim == 1 and array.shape == shape[1:]:
        return np.broadcast_to(array, shape)
    raise ValueError(f"bound shape {array.shape} cannot align with {shape}")


def full_bounds_audit(
    build: OPFBuild, result: Mapping[str, object]
) -> Mapping[str, object]:
    """Independently reconstruct every box and coupling in the frozen fixture."""
    data = build.data
    base = float(data["baseMVA"])
    pg = np.asarray(result["Pg"], dtype=float)
    flow = np.asarray(result["p_flows"], dtype=float)
    storage_power = np.asarray(result["b"], dtype=float)
    soc = np.asarray(result["soc"], dtype=float)
    residuals: dict[str, float] = {
        "generator_active_bound_mw_abs": _maximum_violation(
            pg, base * np.asarray(data["Pgmin"]), base * np.asarray(data["Pgmax"])
        ),
        "branch_flow_bound_mw_abs": _maximum_violation(
            flow, -base * np.asarray(data["f_max"]), base * np.asarray(data["f_max"])
        ),
        "storage_power_bound_mw_abs": _maximum_violation(
            storage_power,
            -np.asarray(data["storage_apparent_power_rating"]),
            np.asarray(data["storage_apparent_power_rating"]),
        ),
        "storage_energy_bound_mwh_abs": _maximum_violation(
            soc, 0.0, np.asarray(data["storage_capacity"])
        ),
    }
    preceding = np.vstack((np.asarray(data["storage_initial_soc"]), soc[:-1]))
    residuals["storage_recurrence_mwh_abs"] = float(
        np.max(np.abs(soc - (preceding - float(data["storage_delta"]) * storage_power)))
    )
    residuals["storage_terminal_mwh_abs"] = float(
        np.max(np.abs(soc[-1] - np.asarray(data["storage_terminal_soc"])))
    )

    load = np.asarray(result["p_load"], dtype=float)
    served = np.asarray(result["p_load_served"], dtype=float)
    fractions = result.get("load_shed_fraction")
    if fractions is None:
        residuals["load_shed_fraction_bound_abs"] = 0.0
        expected_served = load
    else:
        indices = np.asarray(data["sheddable_load_indices"], dtype=int)
        fraction_values = np.asarray(fractions, dtype=float)
        maximum = np.asarray(data["load_max_shed_fraction"], dtype=float)[indices]
        residuals["load_shed_fraction_bound_abs"] = _maximum_violation(
            fraction_values, 0.0, maximum
        )
        expected_served = load.copy()
        expected_served[:, indices] -= fraction_values * np.maximum(
            load[:, indices], 0.0
        )
    residuals["active_load_service_mw_abs"] = float(
        np.max(np.abs(served - expected_served))
    )

    nd_active = result.get("p_nd")
    if nd_active is not None:
        nd = np.asarray(nd_active, dtype=float)
        available = _time_first(data["nd_available"], nd.shape)
        residuals["nondispatchable_active_bound_mw_abs"] = _maximum_violation(
            nd, 0.0, available
        )
        rating = np.asarray(data["nd_apparent_power_rating"], dtype=float)
        residuals["nondispatchable_rating_bound_mw_abs"] = _maximum_violation(
            nd, 0.0, rating
        )
        curtailment = np.asarray(result["curtailment"], dtype=float)
        residuals["curtailment_nonnegativity_mw_abs"] = float(
            max(0.0, -float(np.min(curtailment)))
        )
        residuals["renewable_availability_identity_mw_abs"] = float(
            np.max(np.abs(nd + curtailment - available))
        )
    residuals["maximum_abs"] = max(residuals.values())
    return {
        "residuals": residuals,
        "accepted": residuals["maximum_abs"] <= BOUND_AUDIT_TOLERANCE,
        "tolerance": BOUND_AUDIT_TOLERANCE,
        "hvdc_device_count": 0,
    }


def objective_accounting(
    build: OPFBuild, result: Mapping[str, object], horizon: int
) -> Mapping[str, object]:
    fixture = load_prefix_fixture(horizon)
    pg = np.asarray(result["Pg"], dtype=float)
    gencost = generator_gencost(list(fixture.inputs.generators))
    generation = fixture.inputs.delta * sum(
        float(gen_cost_expr(gencost, cp.Constant(row)).value) for row in pg
    )
    branch_resistance = np.asarray(fixture.inputs.case["branch"], dtype=float)[:, BR_R]
    branch_flow_pu = np.asarray(result["p_flows"], dtype=float) / float(
        fixture.inputs.case["baseMVA"]
    )
    dc_loss = (
        fixture.inputs.delta
        * fixture.inputs.options.loss_weight
        * float(np.sum(branch_resistance * np.square(branch_flow_pu)))
    )
    components: dict[str, float] = {
        "generation_cost": generation,
        "dc_loss_cost": dc_loss,
    }
    for name, value in result.items():
        if name.endswith("_cost"):
            components[name] = float(cast(float, value))
    objective = float(cast(float, result["objective"]))
    return {
        "objective": objective,
        "components": components,
        "accounting_residual_abs": abs(objective - sum(components.values())),
    }


def _usable_primal(result: Mapping[str, object]) -> bool:
    required = (
        "objective",
        "Pg",
        "p_flows",
        "p_net",
        "b",
        "soc",
        "p_load",
        "p_load_served",
    )
    for name in required:
        if result.get(name) is None:
            return False
        try:
            values = np.asarray(result[name], dtype=float)
        except (TypeError, ValueError):
            return False
        if values.size == 0 or not np.isfinite(values).all():
            return False
    return True


def diagnostic_context(horizon: int, assembly: str) -> Mapping[str, object]:
    fixture = load_prefix_fixture(horizon)
    backend = dict(REPRESENTATIONS)[assembly]
    return {
        "schema_version": SCHEMA_VERSION,
        "git_commit": _git("rev-parse", "HEAD"),
        "git_clean": _git("status", "--porcelain") == "",
        "source_fingerprint": diagnostic_source_fingerprint(),
        "shared_production_fingerprint": shared_production_fingerprint(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "software_versions": dict(_software_versions()),
        "horizon_steps": horizon,
        "temporal_assembly": assembly,
        "canonicalization_backend": backend,
        "prefix_input_sha256": fixture.input_sha256,
        "prefix_scenario_sha256": fixture.scenario_sha256,
        "policy_sha256": fixture.annual.policy_sha256,
        "base_solve_config_sha256": fixture.annual.solve_config_sha256,
        "tight_clarabel_options": dict(TIGHT_CLARABEL_OPTIONS),
        "profiling_result_sha256": PROFILE_RESULT_SHA256,
        "profiling_analysis_sha256": PROFILE_ANALYSIS_SHA256,
        "annual_execution_authorized": False,
    }


def _worker(directory: Path, horizon: int, assembly: str) -> int:
    context = diagnostic_context(horizon, assembly)
    fixture = load_prefix_fixture(horizon)
    storage = tuple(replace(unit) for unit in fixture.inputs.storage)
    started = time.perf_counter()
    build = streaming_runner.build_window(
        fixture.inputs,
        "lossy_dc",
        0,
        horizon,
        storage,
        temporal_assembly=cast(Any, assembly),
    )
    captured: dict[str, object] = {}
    _, wrapper = _capture_clarabel_solution(captured)
    exception = None
    with patch.object(CLARABEL, "solve_via_data", wrapper):
        try:
            build.solve(solver="CLARABEL", nlp=False, **TIGHT_CLARABEL_OPTIONS)
        except Exception as exc:
            exception = f"{type(exc).__name__}: {exc}"
    result = extract_results(build)
    audit_exception = None
    try:
        audit = audit_probe(
            fixture.inputs.case,
            build,
            result,
            generators=fixture.inputs.generators,
            loads=fixture.inputs.loads,
            nondispatchable=fixture.inputs.nondispatchable,
            storage=storage,
            delta=fixture.inputs.delta,
            branch_limit_sentinel=fixture.inputs.options.branch_limit_sentinel,
            tolerances=fixture.annual.policy.tolerances,
        )
        audit_payload: Mapping[str, object] = {
            "accepted_primal": audit.accepted_primal,
            "status": audit.status,
            "residuals": dict(audit.residuals),
            "missing_or_nonfinite_fields": list(audit.missing_or_nonfinite_fields),
            "identity_error": audit.identity_error,
            "exception": None,
        }
    except Exception as exc:
        audit_exception = f"{type(exc).__name__}: {exc}"
        audit_payload = {
            "accepted_primal": False,
            "status": result.get("status"),
            "residuals": {},
            "missing_or_nonfinite_fields": [],
            "identity_error": None,
            "exception": audit_exception,
        }
    usable_primal = _usable_primal(result)
    if usable_primal:
        try:
            bounds = full_bounds_audit(build, result)
            accounting = objective_accounting(build, result, horizon)
        except Exception as exc:
            usable_primal = False
            reconstruction_error = f"{type(exc).__name__}: {exc}"
            bounds = {
                "accepted": False,
                "classification": "reconstruction_error",
                "exception": reconstruction_error,
                "residuals": {},
            }
            accounting = {
                "objective": result.get("objective"),
                "components": {},
                "accounting_residual_abs": None,
                "classification": "reconstruction_error",
                "exception": reconstruction_error,
            }
    else:
        bounds = {
            "accepted": False,
            "classification": "unavailable_primal",
            "exception": None,
            "residuals": {},
        }
        accounting = {
            "objective": result.get("objective"),
            "components": {},
            "accounting_residual_abs": None,
            "classification": "unavailable_primal",
            "exception": None,
        }
    end_context = diagnostic_context(horizon, assembly)
    context_matches = end_context == context
    native_solved = captured.get("status") == "Solved"
    audit_accepted = audit_payload.get("accepted_primal") is True
    accounting_residual = accounting.get("accounting_residual_abs")
    accepted = (
        exception is None
        and audit_exception is None
        and context_matches
        and native_solved
        and usable_primal
        and audit_accepted
        and bounds["accepted"] is True
        and isinstance(accounting_residual, (int, float))
        and not isinstance(accounting_residual, bool)
        and float(accounting_residual) <= 1e-5
        and build.temporal_assembly == assembly
        and build.canonicalization_backend == dict(REPRESENTATIONS)[assembly]
    )
    if accepted:
        classification = "accepted"
    elif exception is not None:
        classification = "solver_failure"
    elif result.get("status") in {"infeasible", "infeasible_inaccurate"}:
        classification = "solver_certified_infeasible"
    elif not usable_primal:
        classification = "unusable_primal"
    elif not native_solved:
        classification = "tight_tolerance_not_attained"
    else:
        classification = "audit_rejection"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "classification": classification,
        "exception": exception,
        "context": context,
        "end_context": end_context,
        "context_matches": context_matches,
        "wall_time_seconds": time.perf_counter() - started,
        "rss_bytes": process_rss_bytes(),
        "status": result.get("status"),
        "audit": audit_payload,
        "bounds_audit": bounds,
        "objective_accounting": accounting,
        "solver_statistics": _solver_statistics(build, captured),
        "result": result,
    }
    atomic_immutable_json(directory / "arm-result.json", _json_value(payload))
    return 0 if accepted else 1


def _accepted_arm(payload: Mapping[str, object], horizon: int, assembly: str) -> bool:
    context_raw = payload.get("context")
    audit_raw = payload.get("audit")
    bounds_raw = payload.get("bounds_audit")
    statistics_raw = payload.get("solver_statistics")
    if not all(
        isinstance(item, Mapping)
        for item in (context_raw, audit_raw, bounds_raw, statistics_raw)
    ):
        return False
    context = cast(Mapping[str, object], context_raw)
    audit = cast(Mapping[str, object], audit_raw)
    bounds = cast(Mapping[str, object], bounds_raw)
    statistics = cast(Mapping[str, object], statistics_raw)
    clarabel_raw = statistics.get("clarabel")
    if not isinstance(clarabel_raw, Mapping):
        return False
    clarabel = cast(Mapping[str, object], clarabel_raw)
    expected_context = diagnostic_context(horizon, assembly)
    return (
        payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("classification") == "accepted"
        and payload.get("exception") is None
        and payload.get("context_matches") is True
        and payload.get("end_context") == context
        and context == expected_context
        and audit.get("accepted_primal") is True
        and bounds.get("accepted") is True
        and statistics.get("solver_name") == "CLARABEL"
        and clarabel.get("status") == "Solved"
        and all(
            isinstance(clarabel.get(name), (int, float))
            and not isinstance(clarabel.get(name), bool)
            and np.isfinite(float(cast(float, clarabel.get(name))))
            for name in (
                "primal_objective",
                "dual_objective",
                "absolute_gap",
                "relative_gap",
                "primal_residual",
                "dual_residual",
            )
        )
    )


def _maximum_difference(left: object, right: object) -> float:
    left_array = np.asarray(left, dtype=float)
    right_array = np.asarray(right, dtype=float)
    if left_array.shape != right_array.shape:
        raise ValueError("diagnostic result shapes differ")
    return float(np.max(np.abs(left_array - right_array)))


def _comparison(
    stepwise: Mapping[str, object], vectorized: Mapping[str, object]
) -> Mapping[str, object]:
    step_result = cast(Mapping[str, object], stepwise["result"])
    vector_result = cast(Mapping[str, object], vectorized["result"])
    step_accounting = cast(Mapping[str, object], stepwise["objective_accounting"])
    vector_accounting = cast(Mapping[str, object], vectorized["objective_accounting"])
    step_components = cast(Mapping[str, object], step_accounting["components"])
    vector_components = cast(Mapping[str, object], vector_accounting["components"])
    return {
        "objective_absolute_difference": abs(
            float(cast(float, step_accounting["objective"]))
            - float(cast(float, vector_accounting["objective"]))
        ),
        "objective_relative_difference": abs(
            float(cast(float, step_accounting["objective"]))
            - float(cast(float, vector_accounting["objective"]))
        )
        / max(1.0, abs(float(cast(float, vector_accounting["objective"])))),
        "component_cost_absolute_differences": {
            name: abs(
                float(cast(float, step_components[name]))
                - float(cast(float, vector_components[name]))
            )
            for name in sorted(step_components.keys() & vector_components.keys())
        },
        "coordinate_maximum_absolute_differences": {
            name: _maximum_difference(step_result[name], vector_result[name])
            for name in ("Pg", "b", "soc", "p_net")
        },
        "p_flows_coordinate_comparison": "residual_gated_nonunique",
        "stepwise_clarabel": cast(Mapping[str, object], stepwise["solver_statistics"])[
            "clarabel"
        ],
        "vectorized_clarabel": cast(
            Mapping[str, object], vectorized["solver_statistics"]
        )["clarabel"],
        "stepwise_bounds_audit": stepwise["bounds_audit"],
        "vectorized_bounds_audit": vectorized["bounds_audit"],
    }


def _validate_profile_artifacts() -> None:
    result_path = PROFILE_OUTPUT_DIRECTORY / "profile-result.json"
    analysis_path = PROFILE_OUTPUT_DIRECTORY / "profile-analysis.json"
    if sha256_path(result_path) != PROFILE_RESULT_SHA256:
        raise ValueError("profiling root hash mismatch")
    if sha256_path(analysis_path) != PROFILE_ANALYSIS_SHA256:
        raise ValueError("profiling analysis hash mismatch")
    result = cast(Mapping[str, object], json.loads(result_path.read_text()))
    analysis = cast(Mapping[str, object], json.loads(analysis_path.read_text()))
    comparisons = analysis.get("comparisons")
    if (
        result.get("execution_complete") is not True
        or analysis.get("execution_complete") is not True
        or analysis.get("classification") != "mismatch"
        or result.get("annual_execution_authorized") is not False
        or analysis.get("annual_execution_authorized") is not False
        or shared_production_fingerprint() != PROFILE_SHARED_PRODUCTION_FINGERPRINT
        or not isinstance(comparisons, list)
        or len(comparisons) != len(PREFIX_LADDER_HORIZONS)
    ):
        raise ValueError("profiling evidence is not the reviewed mismatch record")
    for item in comparisons:
        if not isinstance(item, Mapping):
            raise ValueError("profiling comparison is not a mapping")
        context = item.get("execution_context_comparability")
        if not isinstance(context, Mapping):
            raise ValueError("profiling production context is missing")
        production = context.get("shared_production_fingerprint")
        if not isinstance(production, Mapping) or any(
            production.get(name) != PROFILE_SHARED_PRODUCTION_FINGERPRINT
            for name in ("stepwise", "vectorized")
        ):
            raise ValueError("profiling production fingerprint mismatch")


def run_diagnostic(
    directory: Path = DIAGNOSTIC_OUTPUT_DIRECTORY,
) -> Mapping[str, object]:
    directory = directory.expanduser().resolve()
    if directory.exists():
        raise FileExistsError("tight-tolerance diagnostic output already exists")
    if _git("status", "--porcelain") != "":
        raise ValueError("tight-tolerance diagnostic requires a clean committed tree")
    if _git("merge-base", M14C_INTEGRATION_COMMIT, "HEAD") != M14C_INTEGRATION_COMMIT:
        raise ValueError("diagnostic source lacks M14c integration ancestry")
    _validate_profile_artifacts()
    directory.mkdir(parents=True)
    records: list[Mapping[str, object]] = []
    accepted: dict[tuple[int, str], Mapping[str, object]] = {}
    classification = "accepted"
    for horizon in PREFIX_LADDER_HORIZONS:
        for assembly, backend in REPRESENTATIONS:
            arm = directory / f"{horizon:04d}-{assembly}"
            arm.mkdir()
            log_path = arm / "worker.log"
            command = [
                sys.executable,
                "-m",
                "experiments.m14_time_vectorization.m14c_tight_tolerance_diagnostic",
                "--worker",
                "--output-directory",
                str(arm),
                "--horizon",
                str(horizon),
                "--assembly",
                assembly,
            ]
            started = time.monotonic()
            with log_path.open("wb") as log:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    returncode = process.wait(
                        timeout=PREFIX_EXECUTION_LIMITS[horizon].worker_wall_seconds
                    )
                except BaseException:
                    if process.poll() is None:
                        _terminate(process)
                    process.wait()
                    raise
            result_path = arm / "arm-result.json"
            payload = (
                cast(Mapping[str, object], json.loads(result_path.read_text()))
                if result_path.is_file()
                else None
            )
            arm_classification = (
                "accepted"
                if returncode == 0
                and payload is not None
                and _accepted_arm(payload, horizon, assembly)
                else "worker_failure"
            )
            record = {
                "horizon_steps": horizon,
                "temporal_assembly": assembly,
                "canonicalization_backend": backend,
                "classification": arm_classification,
                "returncode": returncode,
                "wall_time_seconds": time.monotonic() - started,
                "worker_log_sha256": sha256_path(log_path),
                "arm_result_sha256": (
                    None if payload is None else sha256_path(result_path)
                ),
                "directory": arm.name,
            }
            records.append(record)
            atomic_json(
                directory / "diagnostic-progress.json",
                {"schema_version": SCHEMA_VERSION, "records": records},
            )
            if arm_classification != "accepted" or payload is None:
                classification = "stopped"
                break
            accepted[(horizon, assembly)] = payload
        if classification != "accepted":
            break
    comparisons = [
        {
            "horizon_steps": horizon,
            **_comparison(
                accepted[(horizon, "stepwise")],
                accepted[(horizon, "vectorized")],
            ),
        }
        for horizon in PREFIX_LADDER_HORIZONS
        if (horizon, "stepwise") in accepted and (horizon, "vectorized") in accepted
    ]
    result = {
        "schema_version": SCHEMA_VERSION,
        "classification": classification,
        "execution_complete": len(records) == 6 and classification == "accepted",
        "records": records,
        "comparisons": comparisons,
        "tight_clarabel_options": dict(TIGHT_CLARABEL_OPTIONS),
        "profiling_result_sha256": PROFILE_RESULT_SHA256,
        "profiling_analysis_sha256": PROFILE_ANALYSIS_SHA256,
        "diagnostic_only": True,
        "qualified_for_annual_authority_review": False,
        "annual_execution_authorized": False,
    }
    atomic_immutable_json(directory / "diagnostic-result.json", _json_value(result))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-directory", type=Path, default=DIAGNOSTIC_OUTPUT_DIRECTORY
    )
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--horizon", type=int)
    parser.add_argument("--assembly", choices=dict(REPRESENTATIONS))
    args = parser.parse_args()
    if args.worker:
        if args.horizon is None or args.assembly is None:
            parser.error("worker requires --horizon and --assembly")
        raise SystemExit(_worker(args.output_directory, args.horizon, args.assembly))
    print(json.dumps(run_diagnostic(args.output_directory), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
