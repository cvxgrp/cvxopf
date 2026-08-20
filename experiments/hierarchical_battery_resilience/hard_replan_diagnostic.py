"""Predeclared interval-35 initialization diagnostic for M17-S3.

This is experiment-specific orchestration. It deliberately remains separate
from the public hierarchical-controller API planned for M17-S4.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gzip
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import re
import subprocess
from time import perf_counter
from typing import Mapping

import cvxpy as cp
import cyipopt
import numpy as np
from cvxpy.reductions.solvers.nlp_solving_chain import (
    _set_nlp_initial_point,
)
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT

from cvxopf import OPFBuild, extract_results
from experiments.hierarchical_battery_resilience.manual_runner import (
    AC_REQUIRED_FIELDS,
    SolveAudit,
    _ac_residuals,
    _build_window,
    _classify_audit,
    _finite_fields,
    _identity_error,
    _inner_storage,
)
from experiments.hierarchical_battery_resilience.scenario import (
    FrozenScenario,
    load_frozen_scenario,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = Path(__file__).resolve().parent
AUTHORITATIVE_RESULTS = EXPERIMENT_ROOT / "results/s3_authoritative_0cd65b1"
AUTHORITATIVE_MANIFEST = EXPERIMENT_ROOT / "S3_RESULTS_METADATA.json"
STORAGE_ID = "battery_bus_7"
PRIMARY_INITIAL_SOC_MWH = 515.0979097002988
PRIMARY_TARGET_SOC_MWH = 849.9999996548939
FROZEN_INITIAL_SOC_MWH = 516.7309542175291
FROZEN_TARGET_SOC_MWH = 849.9999999140263
PRECEDING_INITIAL_SOC_MWH = 379.5262446425310
PRECEDING_TARGET_SOC_MWH = 724.7153210174109
CANONICAL_TARGET_SOC_MWH = 850.0
EXPECTED_RECORD_COUNT = 14
EXPECTED_SOLVER_CALL_COUNT = 14
DEFAULT_OUTPUT = EXPERIMENT_ROOT / "results/s3_hard_replan_diagnostic"
SOLVER_CONTEXT = {
    "solver": "IPOPT",
    "ipopt_version": "3.14.19",
    "interface": "cyipopt",
    "cyipopt_version": "1.7.0",
    "cvxpy_version": "1.9.2",
    "options": {
        "mu_strategy": "adaptive",
        "tol": 1e-7,
        "bound_relax_factor": 0.0,
        "hessian_approximation": "exact",
        "derivative_test": "none",
        "least_square_init_duals": "no",
        "print_level": 3,
    },
}
STEP_SUFFIX = re.compile(r"^(?P<base>.+)_(?P<step>[0-4])$")


@dataclass(frozen=True)
class ProblemSpec:
    """One exact five-step AC problem identity."""

    name: str
    start: int
    stop: int
    initial_soc_mwh: float
    target_soc_mwh: float | None


@dataclass(frozen=True)
class InitializationSpec:
    """One fully declared initialization transformation."""

    name: str
    parent_source: str | None = None
    scale: float | None = None
    seed: int | None = None


@dataclass(frozen=True)
class CanonicalRecordSpec:
    """One pre-registered record and its source dependency."""

    record_id: str
    category: str
    problem: ProblemSpec
    initialization: InitializationSpec
    dependency: str | None = None


@dataclass
class DiagnosticRecord:
    """One canonical source or diagnostic record."""

    record_id: str
    category: str
    problem: ProblemSpec
    input_hashes: Mapping[str, str]
    solver_context: Mapping[str, object]
    initialization: InitializationSpec
    solver_executed: bool
    x0_verified: bool
    source_classification: str | None
    source_differences: tuple[str, ...]
    starting_values: dict[str, object]
    raw_perturbations: dict[str, object] | None
    object_ids_before: Mapping[str, tuple[int, ...]] | None
    object_ids_after: Mapping[str, tuple[int, ...]] | None
    object_identity_preserved: bool | None
    results: dict | None
    audit: SolveAudit | None
    terminal_deviation_mwh: Mapping[str, float] | None
    scientific_classification: str
    solution_values: dict[str, np.ndarray] | None = None


MATCHED_PROBLEMS = (
    ProblemSpec(
        "matched_frozen_raw",
        35,
        40,
        FROZEN_INITIAL_SOC_MWH,
        FROZEN_TARGET_SOC_MWH,
    ),
    ProblemSpec(
        "matched_replanned_raw",
        35,
        40,
        PRIMARY_INITIAL_SOC_MWH,
        PRIMARY_TARGET_SOC_MWH,
    ),
    ProblemSpec(
        "matched_frozen_canonical",
        35,
        40,
        FROZEN_INITIAL_SOC_MWH,
        CANONICAL_TARGET_SOC_MWH,
    ),
    ProblemSpec(
        "matched_replanned_canonical",
        35,
        40,
        PRIMARY_INITIAL_SOC_MWH,
        CANONICAL_TARGET_SOC_MWH,
    ),
    ProblemSpec(
        "matched_frozen_replanned_target",
        35,
        40,
        FROZEN_INITIAL_SOC_MWH,
        PRIMARY_TARGET_SOC_MWH,
    ),
    ProblemSpec(
        "matched_replanned_frozen_target",
        35,
        40,
        PRIMARY_INITIAL_SOC_MWH,
        FROZEN_TARGET_SOC_MWH,
    ),
)

TARGET_FREE_SOURCE = ProblemSpec(
    "source_target_free", 35, 40, PRIMARY_INITIAL_SOC_MWH, None
)
PRECEDING_SOURCE = ProblemSpec(
    "source_preceding",
    34,
    39,
    PRECEDING_INITIAL_SOC_MWH,
    PRECEDING_TARGET_SOC_MWH,
)
ALTERNATE_INITIALIZATIONS = (
    InitializationSpec("B_frozen", "matched_frozen_raw"),
    InitializationSpec("C_target_free", "source_target_free"),
    InitializationSpec("D_shifted_preceding", "source_preceding"),
    InitializationSpec(
        "E_frozen_perturb_1e-4", "matched_frozen_raw", 1e-4, 17035
    ),
    InitializationSpec(
        "E_frozen_perturb_1e-3", "matched_frozen_raw", 1e-3, 27035
    ),
    InitializationSpec(
        "E_frozen_perturb_1e-2", "matched_frozen_raw", 1e-2, 37035
    ),
)

CANONICAL_RECORDS = (
    *(
        CanonicalRecordSpec(
            problem.name,
            "matched_state",
            problem,
            InitializationSpec("A_flat"),
        )
        for problem in MATCHED_PROBLEMS
    ),
    CanonicalRecordSpec(
        TARGET_FREE_SOURCE.name,
        "source",
        TARGET_FREE_SOURCE,
        InitializationSpec("A_flat"),
    ),
    CanonicalRecordSpec(
        PRECEDING_SOURCE.name,
        "source",
        PRECEDING_SOURCE,
        InitializationSpec("A_flat"),
    ),
    *(
        CanonicalRecordSpec(
            initialization.name,
            "alternate_initialization",
            MATCHED_PROBLEMS[1],
            initialization,
            initialization.parent_source,
        )
        for initialization in ALTERNATE_INITIALIZATIONS
    ),
)

if len(CANONICAL_RECORDS) != EXPECTED_RECORD_COUNT:  # pragma: no cover
    raise RuntimeError("M17 diagnostic canonical registry must contain 14 records")
if len({record.record_id for record in CANONICAL_RECORDS}) != len(
    CANONICAL_RECORDS
):  # pragma: no cover
    raise RuntimeError("M17 diagnostic canonical record IDs must be unique")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_fingerprint(paths: list[Path]) -> str:
    """Match the authoritative S3 source-fingerprint algorithm exactly."""
    digest = hashlib.sha256()
    for path in sorted(paths):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _read_gzip_json(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected an object in {path}")
    return payload


def verify_authoritative_sources(
    results_path: Path = AUTHORITATIVE_RESULTS,
    manifest_path: Path = AUTHORITATIVE_MANIFEST,
) -> dict:
    """Verify artifacts and every source that reconstructs the diagnostic."""
    tracked = json.loads(manifest_path.read_text())
    authoritative = tracked["execution_source"]["source_fingerprints"]
    scenario_manifest = EXPERIMENT_ROOT / "prepared_scenario/manifest.json"
    if _sha256(scenario_manifest) != tracked["scenario"]["manifest_sha256"]:
        raise ValueError("Frozen scenario-manifest hash differs from S3")

    model_paths = sorted((REPOSITORY_ROOT / "src/cvxopf").rglob("*.py"))
    if (
        _source_fingerprint(model_paths)
        != authoritative["cvxopf_python_tree_sha256"]
    ):
        raise ValueError("Current src/cvxopf tree differs from S3")

    for relative in (
        "experiments/hierarchical_battery_resilience/scenario.py",
        "experiments/hierarchical_battery_resilience/manual_runner.py",
    ):
        if _sha256(REPOSITORY_ROOT / relative) != authoritative["files"][relative]:
            raise ValueError(f"Current {relative} differs from S3")

    for name in (
        "frozen__hard_equality.json.gz",
        "replan_every_step__hard_equality.json.gz",
    ):
        expected = tracked["artifacts"][name]
        path = results_path / name
        if (
            not path.is_file()
            or path.stat().st_size != expected["bytes"]
            or _sha256(path) != expected["sha256"]
        ):
            raise ValueError(f"Authoritative artifact integrity failure: {name}")
    return tracked


def _build_problem(scenario: FrozenScenario, spec: ProblemSpec) -> OPFBuild:
    initial = {STORAGE_ID: spec.initial_soc_mwh}
    target = (
        None
        if spec.target_soc_mwh is None
        else {STORAGE_ID: spec.target_soc_mwh}
    )
    policy = None if target is None else "hard_equality"
    storage = _inner_storage(scenario, initial, target, policy)
    return _build_window(scenario, "ac", spec.start, spec.stop, storage)


def _array_hash(values: np.ndarray) -> str:
    array = np.asarray(values)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode())
    digest.update(np.asarray(array.shape, dtype=np.int64).tobytes())
    digest.update(np.ascontiguousarray(array).tobytes())
    return digest.hexdigest()


def _problem_input_hashes(
    scenario: FrozenScenario, spec: ProblemSpec
) -> dict[str, str]:
    return {
        "load_p": _array_hash(
            scenario.df_load_p.iloc[spec.start : spec.stop].to_numpy()
        ),
        "load_q": _array_hash(
            scenario.df_load_q.iloc[spec.start : spec.stop].to_numpy()
        ),
        "nondispatchable": _array_hash(
            scenario.df_nd.iloc[spec.start : spec.stop].to_numpy()
        ),
    }


def _variables_by_name(build: OPFBuild) -> dict[str, cp.Variable]:
    variables = build.prob.variables()
    names = [variable.name() for variable in variables]
    if len(names) != len(set(names)):
        raise ValueError("Diagnostic requires unique CVXPY variable names")
    return dict(zip(names, variables, strict=True))


def _complete_start(build: OPFBuild) -> dict[str, np.ndarray]:
    _set_nlp_initial_point(build.prob)
    values = {}
    for name, variable in _variables_by_name(build).items():
        if variable.value is None:
            raise RuntimeError(f"CVXPY did not initialize {name}")
        values[name] = np.asarray(variable.value, dtype=float).copy()
    return values


def _flatten_start(build: OPFBuild) -> np.ndarray:
    return np.concatenate(
        [
            np.asarray(variable.value, dtype=float).flatten(order="F")
            for variable in build.prob.variables()
        ]
    )


def _assign_complete_start(
    build: OPFBuild,
    values: Mapping[str, np.ndarray],
) -> None:
    variables = _variables_by_name(build)
    if set(values) != set(variables):
        missing = sorted(set(variables) - set(values))
        extra = sorted(set(values) - set(variables))
        raise ValueError(f"Starting-value namespace mismatch: {missing=}, {extra=}")
    for name, variable in variables.items():
        value = np.asarray(values[name], dtype=float)
        if value.shape != variable.shape:
            raise ValueError(
                f"Starting-value shape mismatch for {name}: "
                f"{value.shape} != {variable.shape}"
            )
        variable.value = value.copy()


def _copy_start(source: DiagnosticRecord) -> dict[str, np.ndarray]:
    if source.solution_values is None:
        raise ValueError(f"Source {source.record_id} has no solution values")
    return {name: value.copy() for name, value in source.solution_values.items()}


def _shift_preceding_start(
    source: DiagnosticRecord,
    initial_soc_mwh: float = PRIMARY_INITIAL_SOC_MWH,
    delta_hours: float = 1.0,
) -> dict[str, np.ndarray]:
    source_values = _copy_start(source)
    destination: dict[str, np.ndarray] = {}
    by_base: dict[str, dict[int, np.ndarray]] = {}
    unsuffixed: dict[str, np.ndarray] = {}
    for name, value in source_values.items():
        match = STEP_SUFFIX.fullmatch(name)
        if match is None:
            unsuffixed[name] = value.copy()
            continue
        by_base.setdefault(match.group("base"), {})[
            int(match.group("step"))
        ] = value.copy()
    for name, value in unsuffixed.items():
        destination[name] = value
    for base, steps in by_base.items():
        if set(steps) != set(range(5)):
            raise ValueError(f"Incomplete step namespace for {base}")
        for step in range(4):
            destination[f"{base}_{step}"] = steps[step + 1].copy()
        destination[f"{base}_4"] = steps[4].copy()
    for base in ("b", "b_q"):
        if f"{base}_4" not in destination:
            raise ValueError(f"Shift source lacks {base} variables")
        destination[f"{base}_4"] = np.zeros_like(destination[f"{base}_4"])
    state = float(initial_soc_mwh)
    for step in range(5):
        state -= delta_hours * destination[f"b_{step}"].reshape(-1)[0].item()
        destination[f"soc_{step}"] = np.array([state])
    return destination


def _perturb_start(
    source: DiagnosticRecord,
    build: OPFBuild,
    *,
    scale: float,
    seed: int,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    center = _copy_start(source)
    variables = _variables_by_name(build)
    if set(center) != set(variables):
        raise ValueError("Perturbation source and destination namespaces differ")
    rng = np.random.default_rng(seed)
    assigned = {}
    raw_changes = {}
    for name in sorted(variables):
        variable = variables[name]
        value = center[name]
        flat = value.flatten(order="F")
        change = scale * np.maximum(1.0, np.abs(flat)) * rng.standard_normal(
            flat.size
        )
        raw = (flat + change).reshape(value.shape, order="F")
        projected = np.asarray(variable.project(raw), dtype=float)
        assigned[name] = projected.copy()
        raw_changes[name] = change.reshape(value.shape, order="F")
    return assigned, raw_changes


def _object_ids(build: OPFBuild) -> dict[str, tuple[int, ...]]:
    return {
        "variables": tuple(id(value) for value in build.prob.variables()),
        "constraints": tuple(id(value) for value in build.prob.constraints),
        "parameters": tuple(id(value) for value in build.prob.parameters()),
    }


def _run_build_with_verified_x0(
    build: OPFBuild,
) -> tuple[
    dict[str, np.ndarray],
    bool,
    bool,
    dict[str, tuple[int, ...]],
    dict[str, tuple[int, ...]],
    str | None,
    float,
]:
    """Solve one build while verifying the actual IPOPT starting vector."""
    starts = _complete_start(build)
    expected_x0 = _flatten_start(build)
    before_ids = _object_ids(build)
    captured_x0: np.ndarray | None = None
    x0_verified = False
    solver_executed = False
    original = IPOPT.solve_via_data

    def capturing_solve(
        self, data, warm_start, verbose, solver_opts, solver_cache=None
    ):
        nonlocal captured_x0, x0_verified, solver_executed
        captured_x0 = np.asarray(data["x0"], dtype=float).copy()
        x0_verified = np.array_equal(captured_x0, expected_x0)
        if not x0_verified:
            raise RuntimeError("Assigned CVXPY values do not match IPOPT x0")
        solver_executed = True
        return original(
            self,
            data,
            warm_start,
            verbose,
            solver_opts,
            solver_cache,
        )

    exception = None
    started = perf_counter()
    IPOPT.solve_via_data = capturing_solve
    try:
        build.solve()
    except Exception as exc:  # experimental solver outcomes are retained
        exception = f"{type(exc).__name__}: {exc}"
    finally:
        IPOPT.solve_via_data = original
    elapsed = perf_counter() - started
    after_ids = _object_ids(build)
    return (
        starts,
        captured_x0 is not None and x0_verified,
        solver_executed,
        before_ids,
        after_ids,
        exception,
        elapsed,
    )


def _solve_with_verified_x0(
    scenario: FrozenScenario,
    spec: ProblemSpec,
    initialization: InitializationSpec,
    values: Mapping[str, np.ndarray] | None = None,
    raw_perturbations: Mapping[str, np.ndarray] | None = None,
) -> DiagnosticRecord:
    build = _build_problem(scenario, spec)
    if values is not None:
        _assign_complete_start(build, values)
    (
        starts,
        x0_verified,
        solver_executed,
        object_ids_before,
        object_ids_after,
        exception,
        elapsed,
    ) = _run_build_with_verified_x0(build)
    results = extract_results(build)
    required = AC_REQUIRED_FIELDS
    missing = _finite_fields(results, required)
    target = (
        None
        if spec.target_soc_mwh is None
        else {STORAGE_ID: spec.target_soc_mwh}
    )
    if missing:
        residuals = {}
        deviations = None
    else:
        residuals, deviations = _ac_residuals(
            scenario,
            build,
            results,
            target,
            None if target is None else "hard_equality",
        )
    audit = _classify_audit(
        scenario,
        build,
        results,
        exception,
        elapsed,
        required,
        residuals,
        _identity_error(scenario, build, results),
        soft=False,
    )
    solution_values = None
    if audit.accepted_primal:
        solution_values = {
            name: np.asarray(variable.value, dtype=float).copy()
            for name, variable in _variables_by_name(build).items()
        }
    return DiagnosticRecord(
        record_id=spec.name if initialization.name == "A_flat" else initialization.name,
        category="diagnostic",
        problem=spec,
        input_hashes=_problem_input_hashes(scenario, spec),
        solver_context=SOLVER_CONTEXT,
        initialization=initialization,
        solver_executed=solver_executed,
        x0_verified=x0_verified,
        source_classification=None,
        source_differences=(),
        starting_values={name: value.tolist() for name, value in starts.items()},
        raw_perturbations=(
            None
            if raw_perturbations is None
            else {
                name: np.asarray(value).tolist()
                for name, value in raw_perturbations.items()
            }
        ),
        object_ids_before=object_ids_before,
        object_ids_after=object_ids_after,
        object_identity_preserved=object_ids_before == object_ids_after,
        results=results,
        audit=audit,
        terminal_deviation_mwh=deviations,
        scientific_classification=(
            "accepted" if audit.accepted_primal else "not_accepted"
        ),
        solution_values=solution_values,
    )


def _find_attempt(
    payload: dict,
    *,
    iteration: int,
    attempt_kind: str,
) -> dict:
    matches = [
        attempt
        for attempt in payload["ac_attempts"]
        if attempt["iteration"] == iteration
        and attempt["attempt_kind"] == attempt_kind
    ]
    if len(matches) != 1:
        raise ValueError(
            "Expected one authoritative attempt for "
            f"{iteration=} and {attempt_kind=}, got {len(matches)}"
        )
    return matches[0]


def load_authoritative_attempts(
    results_path: Path = AUTHORITATIVE_RESULTS,
) -> dict[str, dict]:
    """Load the three source attempts and failed primary control."""
    verify_authoritative_sources(results_path)
    frozen = _read_gzip_json(results_path / "frozen__hard_equality.json.gz")
    replanned = _read_gzip_json(
        results_path / "replan_every_step__hard_equality.json.gz"
    )
    return {
        "matched_frozen_raw": _find_attempt(
            frozen, iteration=35, attempt_kind="controlling"
        ),
        "matched_replanned_raw": _find_attempt(
            replanned, iteration=35, attempt_kind="controlling"
        ),
        "source_target_free": _find_attempt(
            replanned, iteration=35, attempt_kind="diagnostic"
        ),
        "source_preceding": _find_attempt(
            replanned, iteration=34, attempt_kind="controlling"
        ),
    }


def _result_tolerances(name: str) -> tuple[float, float]:
    if name == "objective":
        return 1e-4, 1e-8
    if name == "Vm":
        return 1e-7, 1e-8
    if name == "Va_deg":
        return 1e-5, 1e-8
    if name in {"storage_device_id_is_explicit"}:
        return 0.0, 0.0
    return 1e-4, 1e-8


def classify_source_equivalence(
    record: DiagnosticRecord,
    authoritative: Mapping[str, object],
    scenario: FrozenScenario,
) -> tuple[str, tuple[str, ...]]:
    """Compare a rebuilt source with its archived public result and audit."""
    if record.audit is None or not record.audit.accepted_primal:
        return "source_unavailable", ("rebuilt source was not accepted",)
    differences = []
    expected_audit = authoritative["audit"]
    if record.audit.status != expected_audit["status"]:
        differences.append("status")
    if record.audit.outcome != expected_audit["outcome"]:
        differences.append("outcome")
    if record.audit.accepted_primal:
        reconstructed_classification = (
            "target_conditioned_failure"
            if record.problem.target_soc_mwh is None
            else "hard_target_met"
        )
        if reconstructed_classification != authoritative["window_diagnosis"]:
            differences.append("window_diagnosis")

    expected_results = authoritative["results"]
    actual_results = record.results or {}
    for name, expected in expected_results.items():
        if name == "status":
            continue
        actual = actual_results.get(name)
        if (expected is None) != (actual is None):
            differences.append(f"{name}:availability")
            continue
        if expected is None:
            continue
        if name == "storage_device_ids":
            if list(actual) != list(expected):
                differences.append(name)
            continue
        expected_array = np.asarray(expected)
        actual_array = np.asarray(actual)
        if expected_array.shape != actual_array.shape:
            differences.append(f"{name}:shape")
            continue
        if expected_array.dtype.kind in "OUS":
            if not np.array_equal(actual_array, expected_array):
                differences.append(name)
            continue
        atol, rtol = _result_tolerances(name)
        if not np.allclose(
            actual_array.astype(float),
            expected_array.astype(float),
            atol=atol,
            rtol=rtol,
        ):
            differences.append(name)

    expected_residuals = expected_audit["residuals"]
    tolerances = scenario.control.acceptance_tolerances
    for name, expected in expected_residuals.items():
        actual = record.audit.residuals.get(name)
        tolerance = tolerances[name]
        if (
            actual is None
            or not np.isfinite(actual)
            or actual > tolerance
            or not np.isclose(actual, expected, atol=tolerance, rtol=0.0)
        ):
            differences.append(f"residual:{name}")

    expected_initial = authoritative["initial_soc_mwh"]
    expected_target = authoritative["target_soc_mwh"]
    actual_initial = {STORAGE_ID: record.problem.initial_soc_mwh}
    actual_target = (
        None
        if record.problem.target_soc_mwh is None
        else {STORAGE_ID: record.problem.target_soc_mwh}
    )
    if actual_initial != expected_initial:
        differences.append("initial_soc_mwh")
    if actual_target != expected_target:
        differences.append("target_soc_mwh")
    if (
        record.problem.start != authoritative["interval_start"]
        or record.problem.stop != authoritative["interval_stop"]
    ):
        differences.append("interval")
    if authoritative["storage_device_ids"] != [STORAGE_ID]:
        differences.append("storage_device_ids")
    return (
        ("reproduced_authoritative_source" if not differences else "new_accepted_source_basin"),
        tuple(differences),
    )


def unavailable_record(
    spec: ProblemSpec,
    initialization: InitializationSpec,
    reason: str,
    input_hashes: Mapping[str, str] | None = None,
) -> DiagnosticRecord:
    """Retain one canonical record when a dependent solve cannot execute."""
    return DiagnosticRecord(
        record_id=(
            spec.name if initialization.name == "A_flat" else initialization.name
        ),
        category="diagnostic",
        problem=spec,
        input_hashes=dict(input_hashes or {}),
        solver_context=SOLVER_CONTEXT,
        initialization=initialization,
        solver_executed=False,
        x0_verified=False,
        source_classification="source_unavailable",
        source_differences=(reason,),
        starting_values={},
        raw_perturbations=None,
        object_ids_before=None,
        object_ids_after=None,
        object_identity_preserved=None,
        results=None,
        audit=None,
        terminal_deviation_mwh=None,
        scientific_classification=reason,
    )


def _execute_registered_record(
    registered: CanonicalRecordSpec,
    scenario: FrozenScenario,
    records: Mapping[str, DiagnosticRecord],
) -> DiagnosticRecord:
    """Execute one pre-registered record or retain its dependency failure."""
    initialization = registered.initialization
    try:
        if registered.dependency is None:
            record = _solve_with_verified_x0(
                scenario, registered.problem, initialization
            )
        else:
            source = records[registered.dependency]
            if source.solution_values is None:
                record = unavailable_record(
                    registered.problem,
                    initialization,
                    f"source_unavailable:{registered.dependency}",
                    _problem_input_hashes(scenario, registered.problem),
                )
            else:
                if initialization.name == "D_shifted_preceding":
                    values = _shift_preceding_start(
                        source, delta_hours=scenario.control.delta_hours
                    )
                    raw = None
                elif initialization.scale is not None:
                    projection_build = _build_problem(
                        scenario, registered.problem
                    )
                    values, raw = _perturb_start(
                        source,
                        projection_build,
                        scale=initialization.scale,
                        seed=int(initialization.seed),
                    )
                else:
                    values = _copy_start(source)
                    raw = None
                record = _solve_with_verified_x0(
                    scenario,
                    registered.problem,
                    initialization,
                    values,
                    raw,
                )
                record.source_classification = source.source_classification
                record.source_differences = source.source_differences
    except Exception as exc:
        record = unavailable_record(
            registered.problem,
            initialization,
            f"initialization_construction_error:{type(exc).__name__}: {exc}",
            _problem_input_hashes(scenario, registered.problem),
        )
        record.source_classification = None
    record.record_id = registered.record_id
    record.category = registered.category
    return record


def diagnostic_summary(records: Mapping[str, DiagnosticRecord]) -> dict:
    """Apply the predeclared completeness and scientific classification."""
    expected_ids = {record.record_id for record in CANONICAL_RECORDS}
    actual_ids = set(records)
    exact_ids = {
        "matched_replanned_raw",
        *(initialization.name for initialization in ALTERNATE_INITIALIZATIONS),
    }
    exact = [records[name] for name in sorted(exact_ids & actual_ids)]
    reproduced_sources = all(
        records[name].source_classification == "reproduced_authoritative_source"
        for name in (
            "matched_frozen_raw",
            "source_target_free",
            "source_preceding",
        )
        if name in records
    ) and {
        "matched_frozen_raw",
        "source_target_free",
        "source_preceding",
    } <= actual_ids
    exact_complete = (
        len(exact) == 7
        and all(record.solver_executed and record.x0_verified for record in exact)
    )
    all_records_executed = all(
        record.solver_executed and record.x0_verified
        for record in records.values()
    )
    complete = (
        actual_ids == expected_ids
        and reproduced_sources
        and exact_complete
        and all_records_executed
        and all(
            record.object_identity_preserved is True
            for record in records.values()
        )
    )
    control = records.get("matched_replanned_raw")
    alternates = [
        records[name]
        for name in (item.name for item in ALTERNATE_INITIALIZATIONS)
        if name in records
    ]
    control_accepted = (
        control is not None
        and control.solver_executed
        and control.x0_verified
        and control.audit is not None
        and control.audit.accepted_primal
    )
    control_executed_not_accepted = (
        control is not None
        and control.solver_executed
        and control.x0_verified
        and control.audit is not None
        and not control.audit.accepted_primal
    )
    alternate_accepted = any(
        record.solver_executed
        and record.x0_verified
        and record.audit is not None
        and record.audit.accepted_primal
        for record in alternates
    )
    if control_accepted:
        feasibility_classification = (
            "modeled_feasible_run_to_run_or_backend_sensitivity"
        )
    elif alternate_accepted and control_executed_not_accepted:
        feasibility_classification = "modeled_feasible_initialization_dependent"
    elif alternate_accepted:
        feasibility_classification = (
            "modeled_feasible_alternate_initialization_incomplete_control"
        )
    else:
        feasibility_classification = None
    if not complete:
        if feasibility_classification is None:
            classification = "incomplete"
        elif feasibility_classification.endswith("incomplete_control"):
            classification = feasibility_classification
        else:
            classification = f"{feasibility_classification}_incomplete_protocol"
    elif feasibility_classification is not None:
        classification = feasibility_classification
    else:
        classification = "all_declared_initializations_failed_unresolved"
    exact_problem_accepted = any(
        record.solver_executed
        and record.x0_verified
        and record.audit is not None
        and record.audit.accepted_primal
        for record in exact
    )
    return {
        "expected_record_count": EXPECTED_RECORD_COUNT,
        "actual_record_count": len(records),
        "expected_solver_call_count": EXPECTED_SOLVER_CALL_COUNT,
        "actual_solver_call_count": sum(
            record.solver_executed for record in records.values()
        ),
        "x0_verified_count": sum(
            record.x0_verified for record in records.values()
        ),
        "complete": complete,
        "exact_problem_accepted": exact_problem_accepted,
        "feasibility_classification": feasibility_classification,
        "classification": classification,
    }


def run_diagnostic(
    results_path: Path = AUTHORITATIVE_RESULTS,
) -> tuple[dict[str, DiagnosticRecord], dict]:
    """Execute the frozen 14-record registry in its declared order."""
    authoritative = load_authoritative_attempts(results_path)
    scenario = load_frozen_scenario()
    records: dict[str, DiagnosticRecord] = {}
    for registered in CANONICAL_RECORDS:
        record = _execute_registered_record(registered, scenario, records)
        if registered.record_id in authoritative:
            classification, differences = classify_source_equivalence(
                record, authoritative[registered.record_id], scenario
            )
            if registered.record_id in {
                "matched_frozen_raw",
                "source_target_free",
                "source_preceding",
            }:
                record.source_classification = classification
                record.source_differences = differences
        records[registered.record_id] = record
    return records, diagnostic_summary(records)


def _jsonable(value):
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"Cannot serialize diagnostic value {type(value).__name__}")


def record_payload(record: DiagnosticRecord) -> dict:
    """Return a strict-JSON-compatible record without in-memory CVXPY state."""
    payload = asdict(record)
    payload.pop("solution_values")
    return _jsonable(payload)


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text)
    temporary.replace(path)


def _atomic_write_gzip_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as stream:
        json.dump(_jsonable(payload), stream, allow_nan=False, separators=(",", ":"))
    temporary.replace(path)


def _git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def execution_context() -> dict:
    """Return the clean committed context required before scientific execution."""
    status = _git_output("status", "--porcelain")
    return {
        "git_commit": _git_output("rev-parse", "HEAD"),
        "git_dirty": bool(status),
        "git_status_porcelain": status.splitlines(),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "packages": {
            name: version(name)
            for name in (
                "cvxopf",
                "cvxpy",
                "numpy",
                "pandas",
                "cyipopt",
                "clarabel",
            )
        },
        "ipopt_version": "3.14.19",
        "diagnostic_source_sha256": _sha256(Path(__file__)),
        "authoritative_manifest_sha256": _sha256(AUTHORITATIVE_MANIFEST),
        "participating_source_fingerprints": {
            "cvxopf_python_tree_sha256": _source_fingerprint(
                sorted((REPOSITORY_ROOT / "src/cvxopf").rglob("*.py"))
            ),
            "scenario_py_sha256": _sha256(EXPERIMENT_ROOT / "scenario.py"),
            "manual_runner_py_sha256": _sha256(
                EXPERIMENT_ROOT / "manual_runner.py"
            ),
        },
        "authoritative_source_commit": (
            "0cd65b1a1c809b81813389f58fde6559a161d147"
        ),
    }


def write_artifacts(
    records: Mapping[str, DiagnosticRecord],
    summary: Mapping[str, object],
    output_path: Path,
    context: Mapping[str, object],
) -> dict:
    """Atomically persist the canonical records, summary, and integrity metadata."""
    output_path.mkdir(parents=True, exist_ok=True)
    records_path = output_path / "diagnostic_records.json.gz"
    summary_path = output_path / "diagnostic_summary.json"
    _atomic_write_gzip_json(
        records_path,
        [record_payload(records[item.record_id]) for item in CANONICAL_RECORDS],
    )
    _atomic_write_text(
        summary_path, json.dumps(_jsonable(summary), indent=2) + "\n"
    )
    metadata = {
        **dict(context),
        "expected_record_ids": [item.record_id for item in CANONICAL_RECORDS],
        "artifacts": {
            path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in (records_path, summary_path)
        },
    }
    _atomic_write_text(
        output_path / "metadata.json",
        json.dumps(_jsonable(metadata), indent=2) + "\n",
    )
    return metadata


def execute_to_directory(output_path: Path = DEFAULT_OUTPUT) -> dict:
    """Execute once from a clean commit and persist the complete audit."""
    if output_path.exists() and any(output_path.iterdir()):
        raise ValueError(f"Diagnostic output directory is not fresh: {output_path}")
    context = execution_context()
    if context["git_dirty"]:
        raise ValueError("Diagnostic execution requires a clean Git worktree")
    expected_versions = {
        "python": "3.13.2",
        "cvxpy": "1.9.2",
        "numpy": "2.5.1",
        "cyipopt": "1.7.0",
        "clarabel": "0.11.1",
        "ipopt": (3, 14, 19),
    }
    actual_versions = {
        "python": context["python"],
        "cvxpy": context["packages"]["cvxpy"],
        "numpy": context["packages"]["numpy"],
        "cyipopt": context["packages"]["cyipopt"],
        "clarabel": context["packages"]["clarabel"],
        "ipopt": tuple(cyipopt.IPOPT_VERSION),
    }
    if actual_versions != expected_versions:
        raise ValueError(
            f"Diagnostic solver environment differs: {actual_versions!r}"
        )
    records, summary = run_diagnostic()
    post_status = _git_output("status", "--porcelain")
    post_commit = _git_output("rev-parse", "HEAD")
    post_diagnostic_hash = _sha256(Path(__file__))
    post_participating = {
        "cvxopf_python_tree_sha256": _source_fingerprint(
            sorted((REPOSITORY_ROOT / "src/cvxopf").rglob("*.py"))
        ),
        "scenario_py_sha256": _sha256(EXPERIMENT_ROOT / "scenario.py"),
        "manual_runner_py_sha256": _sha256(
            EXPERIMENT_ROOT / "manual_runner.py"
        ),
    }
    stable = (
        not post_status
        and post_commit == context["git_commit"]
        and post_diagnostic_hash == context["diagnostic_source_sha256"]
        and post_participating == context["participating_source_fingerprints"]
    )
    context["post_execution"] = {
        "git_commit": post_commit,
        "git_status_porcelain": post_status.splitlines(),
        "diagnostic_source_sha256": post_diagnostic_hash,
        "participating_source_fingerprints": post_participating,
        "execution_source_stable": stable,
    }
    if not stable:
        summary = {
            **summary,
            "complete": False,
            "classification": "incomplete_execution_source_changed",
        }
    return write_artifacts(records, summary, output_path, context)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    metadata = execute_to_directory(args.output)
    print(json.dumps(_jsonable(metadata), indent=2))


if __name__ == "__main__":
    main()
