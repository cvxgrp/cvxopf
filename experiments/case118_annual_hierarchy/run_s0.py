"""Execute and atomically persist the frozen six-hour S0 pilot gate."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import tempfile
import time
from typing import Mapping
import warnings

import cvxpy as cp
import numpy as np

from cvxopf import OPFBuild, OPFOptions, StorageUnitIdeal, build_opf_multistep
from cvxopf.hierarchical import HierarchicalAcceptanceTolerances
from cvxopf.generator import gen_from_matpower
from cvxopf.results import extract_results
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy.pglib_case import (
    MANIFEST_PATH,
    load_pglib_case118,
    make_effectively_unlimited_case,
)
from experiments.case118_annual_hierarchy.scenario import (
    PILOT_GRID,
    materialize_pilot,
    select_pilot_window,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = Path(
    "experiments/case118_annual_hierarchy/results/s0_selected_window.json.gz"
)
SCHEMA_VERSION = 1
HORIZON_STEPS = 6
BRANCH_LIMIT_SENTINEL_MW = 1e6
POWER_TOLERANCE_MULTIPLIER = 100.0
DEVICE_RATING_MOVEMENT_FRACTION = 0.001
CAPACITY_THROUGHPUT_FRACTION = 0.001


def _jsonable(value: object) -> object:
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
    raise TypeError(f"S0 artifact cannot serialize {type(value).__name__}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_fingerprint() -> str:
    paths = sorted((ROOT / "src/cvxopf").glob("*.py"))
    paths.extend(
        [
            Path(__file__),
            Path(__file__).with_name("audit.py"),
            Path(__file__).with_name("pglib_case.py"),
            Path(__file__).with_name("scenario.py"),
            MANIFEST_PATH,
        ]
    )
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_output(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _software_versions() -> dict[str, object]:
    values: dict[str, object] = {"python": platform.python_version()}
    for package in (
        "cvxopf",
        "cvxpy",
        "numpy",
        "pandas",
        "clarabel",
        "cyipopt",
    ):
        try:
            values[package] = version(package)
        except PackageNotFoundError:
            values[package] = None
    try:
        import cyipopt

        values["ipopt"] = list(cyipopt.IPOPT_VERSION)
    except (ImportError, AttributeError):
        values["ipopt"] = None
    return values


def _peak_rss_mib() -> float:
    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports bytes; Linux reports KiB.
    return value / (1024.0**2) if sys.platform == "darwin" else value / 1024.0


def _problem_dimensions(build: OPFBuild) -> dict[str, int]:
    equalities = 0
    inequalities = 0
    other = 0
    for constraint in build.prob.constraints:
        if isinstance(constraint, cp.constraints.Equality):
            equalities += int(constraint.size)
        elif isinstance(constraint, cp.constraints.Inequality):
            inequalities += int(constraint.size)
        else:
            other += int(constraint.size)
    return {
        "scalar_variables": sum(
            int(variable.size) for variable in build.prob.variables()
        ),
        "scalar_equalities": equalities,
        "explicit_scalar_inequalities": inequalities,
        "other_scalar_constraints": other,
        "constraint_objects": len(build.prob.constraints),
    }


def _result_summary(
    case: Mapping[str, object], formulation: str, result: Mapping[str, object]
) -> dict[str, object]:
    branch = np.asarray(case["branch"], dtype=float)
    active = branch[:, 10] == 1.0
    if formulation == "ac":
        maximum_flow = float(
            max(
                np.max(np.asarray(result["branch_s_from"], dtype=float)),
                np.max(np.asarray(result["branch_s_to"], dtype=float)),
            )
        )
        rated = active & (branch[:, 5] > 0.0)
        utilization = (
            float(
                max(
                    np.max(
                        np.asarray(result["branch_s_from"])[:, rated]
                        / branch[rated, 5]
                    ),
                    np.max(
                        np.asarray(result["branch_s_to"])[:, rated]
                        / branch[rated, 5]
                    ),
                )
            )
            if np.any(rated)
            else None
        )
    else:
        flows = np.abs(np.asarray(result["p_flows"], dtype=float))[:, active]
        maximum_flow = float(np.max(flows))
        enforced = np.where(
            branch[active, 5] > 0.0,
            branch[active, 5],
            BRANCH_LIMIT_SENTINEL_MW,
        )
        utilization = float(np.max(flows / enforced))
    return {
        "maximum_branch_flow": maximum_flow,
        "maximum_branch_utilization": utilization,
        "maximum_flow_fraction_of_branch_limit_sentinel": (
            maximum_flow / BRANCH_LIMIT_SENTINEL_MW
        ),
        "maximum_absolute_storage_power_mw": float(
            np.max(np.abs(np.asarray(result["b"], dtype=float)))
        ),
        "storage_throughput_mwh": float(
            np.sum(np.abs(np.asarray(result["b"], dtype=float)))
        ),
        "renewable_output_mwh": float(
            np.sum(np.asarray(result["p_nd"], dtype=float))
        ),
        "renewable_curtailment_mwh": float(
            np.sum(np.maximum(np.asarray(result["curtailment"], dtype=float), 0.0))
        ),
    }


def _movement_gate(
    storage: tuple[StorageUnitIdeal, ...],
    result: Mapping[str, object],
    base_mva: float,
) -> dict[str, object]:
    power = np.abs(np.asarray(result["b"], dtype=float))
    maxima = np.max(power, axis=0)
    declared_power_tolerance_mw = (
        base_mva
        * HierarchicalAcceptanceTolerances().ac_active_balance_pu_abs
    )
    thresholds = np.array(
        [
            max(
                POWER_TOLERANCE_MULTIPLIER * declared_power_tolerance_mw,
                DEVICE_RATING_MOVEMENT_FRACTION
                * unit.apparent_power_rating,
            )
            for unit in storage
        ]
    )
    throughput = float(np.sum(power))
    throughput_threshold = float(
        CAPACITY_THROUGHPUT_FRACTION
        * sum(unit.capacity for unit in storage)
    )
    instantaneous_passed = bool(np.any(maxima > thresholds))
    throughput_passed = throughput > throughput_threshold
    return {
        "passed": instantaneous_passed and throughput_passed,
        "instantaneous_passed": instantaneous_passed,
        "throughput_passed": throughput_passed,
        "maximum_absolute_power_by_storage_mw": maxima,
        "power_threshold_by_storage_mw": thresholds,
        "throughput_mwh": throughput,
        "throughput_threshold_mwh": throughput_threshold,
        "comparison": "strictly_greater_than",
    }


def _run_case(
    network: str,
    formulation: str,
    case: dict[str, object],
    window_start: int,
    window_stop: int,
) -> dict[str, object]:
    pilot = materialize_pilot(case, PILOT_GRID[0])
    generators = gen_from_matpower(case["gen"], case["gencost"])
    options = OPFOptions(branch_limit_sentinel=BRANCH_LIMIT_SENTINEL_MW)
    rss_before = _peak_rss_mib()
    construction_start = time.perf_counter()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="df_load_q is retained as reactive load.*"
        )
        warnings.filterwarnings(
            "ignore", message="Storage apparent_power_rating is applied.*"
        )
        warnings.filterwarnings(
            "ignore", message=r"Branch .* has rateA=0; substituting.*"
        )
        build = build_opf_multistep(
            case,
            T=HORIZON_STEPS,
            formulation=formulation,
            options=options,
            generators=generators,
            loads=list(pilot.loads),
            df_load_p=pilot.df_load_p.iloc[window_start:window_stop],
            df_load_q=pilot.df_load_q.iloc[window_start:window_stop],
            nondispatchable=list(pilot.nondispatchable),
            df_nd=pilot.df_nd.iloc[window_start:window_stop],
            storage=list(pilot.storage),
        )
    construction_seconds = time.perf_counter() - construction_start
    rss_after_build = _peak_rss_mib()
    solve_start = time.perf_counter()
    exception = None
    try:
        build.solve()
    except Exception as error:  # retained scientific failure evidence
        exception = f"{type(error).__name__}: {error}"
    solve_seconds = time.perf_counter() - solve_start
    rss_after_solve = _peak_rss_mib()
    result = extract_results(build)
    audit = audit_probe(
        case,
        build,
        result,
        generators=generators,
        loads=pilot.loads,
        nondispatchable=pilot.nondispatchable,
        storage=pilot.storage,
        branch_limit_sentinel=BRANCH_LIMIT_SENTINEL_MW,
    )
    accepted = exception is None and audit.accepted_primal
    stats = build.prob.solver_stats
    payload: dict[str, object] = {
        "network": network,
        "formulation": formulation,
        "exception": exception,
        "accepted_primal": accepted,
        "audit": {
            "status": audit.status,
            "missing_or_nonfinite_fields": audit.missing_or_nonfinite_fields,
            "identity_error": audit.identity_error,
            "residuals": audit.residuals,
        },
        "timing_seconds": {
            "construction": construction_seconds,
            "solve_call": solve_seconds,
            "solver_setup": getattr(stats, "setup_time", None),
            "solver_solve": getattr(stats, "solve_time", None),
        },
        "peak_rss_mib": {
            "before_build": rss_before,
            "after_build": rss_after_build,
            "after_solve": rss_after_solve,
        },
        "solver_num_iters": getattr(stats, "num_iters", None),
        "dimensions": _problem_dimensions(build),
        "result": result,
    }
    if accepted:
        payload["summary"] = _result_summary(case, formulation, result)
        if network == "pglib_rated" and formulation == "ac":
            payload["movement_gate"] = _movement_gate(
                pilot.storage, result, float(np.asarray(case["baseMVA"]).item())
            )
    return payload


def build_artifact() -> dict[str, object]:
    """Run all four frozen S0 cases and return one complete artifact."""
    rated = load_pglib_case118()
    window = select_pilot_window(rated)
    cases = (
        ("pglib_rated", rated),
        ("pglib_effectively_unlimited", make_effectively_unlimited_case(rated)),
    )
    registry = [
        (network, formulation, case)
        for network, case in cases
        for formulation in ("lossy_dc", "ac")
    ]
    runs = [
        _run_case(network, formulation, case, window.start, window.stop)
        for network, formulation, case in registry
    ]
    rated_ac = next(
        run
        for run in runs
        if run["network"] == "pglib_rated" and run["formulation"] == "ac"
    )
    movement_gate = rated_ac.get("movement_gate")
    movement_passed = (
        bool(movement_gate["passed"])
        if isinstance(movement_gate, Mapping)
        else False
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "execution_source": {
            "git_commit": _git_output("rev-parse", "HEAD"),
            "git_clean": _git_output("status", "--porcelain") == "",
            "source_fingerprint": _source_fingerprint(),
            "runner_sha256": _sha256(Path(__file__)),
        },
        "software_versions": _software_versions(),
        "platform": platform.platform(),
        "profile_hashes": materialize_pilot(rated, PILOT_GRID[0]).profiles.hashes(),
        "pilot_parameters": PILOT_GRID[0].__dict__,
        "horizon_steps": HORIZON_STEPS,
        "window_selection": window.__dict__,
        "branch_limit_sentinel_mw": BRANCH_LIMIT_SENTINEL_MW,
        "runs": runs,
        "all_accepted": all(bool(run["accepted_primal"]) for run in runs),
        "rated_ac_movement_gate_passed": movement_passed,
    }


def _atomic_gzip_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
                encoded = json.dumps(
                    _jsonable(value), sort_keys=True, separators=(",", ":")
                ).encode()
                stream.write(encoded)
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    dirty = _git_output("status", "--porcelain")
    if dirty:
        raise SystemExit("S0 execution requires a clean committed worktree")
    artifact = build_artifact()
    _atomic_gzip_json(arguments.output, artifact)
    print(arguments.output)
    print(f"all_accepted={artifact['all_accepted']}")


if __name__ == "__main__":
    main()
