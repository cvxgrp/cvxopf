"""Run the resource-supervised case118 S1 characterization."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
from typing import Mapping, cast
import warnings

import numpy as np

from cvxopf import OPFOptions, StorageUnitIdeal, build_opf_multistep
from cvxopf.generator import gen_from_matpower
from cvxopf.results import extract_results
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy.pglib_case import (
    load_pglib_case118,
    make_effectively_unlimited_case,
)
from experiments.case118_annual_hierarchy.run_s0 import (
    BRANCH_LIMIT_SENTINEL_MW,
    ROOT,
    _atomic_gzip_json,
    _git_output,
    _jsonable,
    _peak_rss_mib,
    _problem_dimensions,
    _result_summary,
    _sha256,
    _source_fingerprint,
    _software_versions,
)
from experiments.case118_annual_hierarchy.scenario import (
    PILOT_GRID,
    materialize_pilot,
)


DEFAULT_OUTPUT = Path(
    "experiments/case118_annual_hierarchy/results/s1_summary.json.gz"
)
WORKER_DIRECTORY = Path(
    "experiments/case118_annual_hierarchy/results/s1_workers"
)
S1_START = 3744
S1_STOP = 3768
AC_START = 3757
AC_STOP = 3763
AC_LOCAL_INITIAL_BOUNDARY = AC_START - S1_START
AC_LOCAL_TARGET_BOUNDARY = AC_STOP - S1_START
RSS_LIMIT_MIB = 16.0 * 1024.0
WORKER_WALL_LIMIT_SECONDS = 45.0 * 60.0
TOTAL_WALL_LIMIT_SECONDS = 2.0 * 60.0 * 60.0
RSS_POLL_SECONDS = 1.0
SCHEMA_VERSION = 1


def _case(network: str) -> dict[str, object]:
    rated = load_pglib_case118()
    if network == "pglib_rated":
        return rated
    if network == "pglib_effectively_unlimited":
        return make_effectively_unlimited_case(rated)
    raise ValueError(f"Unknown S1 network {network!r}")


def _status_record(
    record_id: str, classification: str, *, reason: str
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "classification": classification,
        "reason": reason,
        "builder_called": False,
        "solver_called": False,
        "accepted_primal": False,
    }


def _unexecuted_worker(
    network: str, classification: str, reason: str
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "network": network,
        "records": [
            _status_record(
                "outer_lossy_dc_24h", classification, reason=reason
            ),
            _status_record(
                "endpoint_ac_6h",
                "source_unavailable",
                reason="24-hour outer task did not produce an accepted source",
            ),
            _status_record(
                "direct_ac_24h",
                "not_authorized_by_s0_resource_gate",
                reason="S0 limits direct AC to at most six hours",
            ),
        ],
    }


def _s1_source_fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(_source_fingerprint().encode())
    for path in (Path(__file__), Path(__file__).with_name("S1_PROTOCOL.md")):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _worker_context() -> dict[str, object]:
    rated = load_pglib_case118()
    return {
        "git_commit": _git_output("rev-parse", "HEAD"),
        "git_clean": _git_output("status", "--porcelain") == "",
        "runner_sha256": _sha256(Path(__file__)),
        "source_fingerprint": _s1_source_fingerprint(),
        "profile_hashes": materialize_pilot(
            rated, PILOT_GRID[0]
        ).profiles.hashes(),
        "software_versions": _software_versions(),
        "platform": platform.platform(),
    }


def _classify_solve(
    status: str | None, exception: str | None, accepted: bool
) -> str:
    if exception is not None:
        return "solver_failure"
    if status in {"infeasible", "infeasible_inaccurate"}:
        return "solver_certified_infeasible"
    if accepted:
        return "accepted"
    return "unusable_primal"


def _scientific_registry_eligible(
    records: list[dict[str, object]],
) -> bool:
    if [record.get("record_id") for record in records] != [
        "outer_lossy_dc_24h",
        "endpoint_ac_6h",
        "direct_ac_24h",
    ]:
        return False
    outer, endpoint, direct = records
    endpoint_outcomes = {
        "accepted",
        "solver_certified_infeasible",
        "solver_failure",
        "unusable_primal",
    }
    return bool(
        outer.get("classification") == "accepted"
        and outer.get("accepted_primal") is True
        and endpoint.get("classification") in endpoint_outcomes
        and direct.get("classification")
        == "not_authorized_by_s0_resource_gate"
        and direct.get("builder_called") is False
        and direct.get("solver_called") is False
    )


def _build_and_solve(
    network: str,
    formulation: str,
    case: dict[str, object],
    storage: tuple[StorageUnitIdeal, ...],
    start: int,
    stop: int,
) -> tuple[dict[str, object], Mapping[str, object]]:
    pilot = materialize_pilot(case, PILOT_GRID[0])
    generators = gen_from_matpower(case["gen"], case["gencost"])
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
            T=stop - start,
            formulation=formulation,
            options=OPFOptions(
                branch_limit_sentinel=BRANCH_LIMIT_SENTINEL_MW
            ),
            generators=generators,
            loads=list(pilot.loads),
            df_load_p=pilot.df_load_p.iloc[start:stop],
            df_load_q=pilot.df_load_q.iloc[start:stop],
            nondispatchable=list(pilot.nondispatchable),
            df_nd=pilot.df_nd.iloc[start:stop],
            storage=list(storage),
        )
    construction_seconds = time.perf_counter() - construction_start
    rss_after_build = _peak_rss_mib()
    solve_start = time.perf_counter()
    exception = None
    try:
        build.solve()
    except Exception as error:  # scientific failure evidence
        exception = f"{type(error).__name__}: {error}"
    solve_seconds = time.perf_counter() - solve_start
    result = extract_results(build)
    audit = audit_probe(
        case,
        build,
        result,
        generators=generators,
        loads=pilot.loads,
        nondispatchable=pilot.nondispatchable,
        storage=storage,
        branch_limit_sentinel=BRANCH_LIMIT_SENTINEL_MW,
    )
    accepted = exception is None and audit.accepted_primal
    stats = build.prob.solver_stats
    record: dict[str, object] = {
        "record_id": (
            "outer_lossy_dc_24h"
            if formulation == "lossy_dc"
            else "endpoint_ac_6h"
        ),
        "classification": _classify_solve(
            audit.status, exception, accepted
        ),
        "builder_called": True,
        "solver_called": True,
        "accepted_primal": accepted,
        "exception": exception,
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
            "after_solve": _peak_rss_mib(),
        },
        "solver_num_iters": getattr(stats, "num_iters", None),
        "dimensions": _problem_dimensions(build),
        "result": result,
    }
    if accepted:
        record["summary"] = _result_summary(case, formulation, result)
    return record, result


def _boundary_soc(
    storage: tuple[StorageUnitIdeal, ...],
    outer_result: Mapping[str, object],
    boundary: int,
) -> np.ndarray:
    if boundary == 0:
        return np.array([unit.initial_soc for unit in storage], dtype=float)
    soc = np.asarray(outer_result["soc"], dtype=float)
    return soc[boundary - 1].copy()


def build_worker_artifact(
    network: str,
    checkpoint: Path,
    *,
    expected_commit: str | None = None,
    expected_source_fingerprint: str | None = None,
) -> dict[str, object]:
    """Execute one network in a fresh worker and checkpoint after the outer."""
    start_context = _worker_context()
    expectations_supplied = (
        expected_commit is not None or expected_source_fingerprint is not None
    )
    expected_matches = not expectations_supplied or (
        start_context["git_commit"] == expected_commit
        and start_context["source_fingerprint"] == expected_source_fingerprint
        and bool(start_context["git_clean"])
    )
    if not expected_matches:
        mismatch_artifact = _unexecuted_worker(
            network,
            "provenance_mismatch",
            "worker start context did not match the parent context",
        )
        mismatch_artifact["start_context"] = start_context
        mismatch_artifact["end_context"] = _worker_context()
        mismatch_artifact["provenance_matches"] = False
        mismatch_artifact["eligible_for_advancement"] = False
        _atomic_gzip_json(checkpoint, mismatch_artifact)
        return mismatch_artifact
    case = _case(network)
    pilot = materialize_pilot(case, PILOT_GRID[0])
    outer, outer_result = _build_and_solve(
        network,
        "lossy_dc",
        case,
        pilot.storage,
        S1_START,
        S1_STOP,
    )
    records: list[dict[str, object]] = [
        outer,
        _status_record(
            "endpoint_ac_6h",
            "pending" if outer["accepted_primal"] else "source_unavailable",
            reason=(
                "awaiting bounded endpoint realization"
                if outer["accepted_primal"]
                else "24-hour outer plan was not accepted"
            ),
        ),
        _status_record(
            "direct_ac_24h",
            "not_authorized_by_s0_resource_gate",
            reason="S0 limits direct AC to at most six hours",
        ),
    ]
    artifact: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "network": network,
        "start_context": start_context,
        "records": records,
    }
    _atomic_gzip_json(checkpoint, artifact)
    if outer["accepted_primal"]:
        initial = _boundary_soc(
            pilot.storage, outer_result, AC_LOCAL_INITIAL_BOUNDARY
        )
        target = _boundary_soc(
            pilot.storage, outer_result, AC_LOCAL_TARGET_BOUNDARY
        )
        endpoint_storage = tuple(
            replace(
                unit,
                initial_soc=float(initial[index]),
                terminal_soc=float(target[index]),
                terminal_constraint="equality",
                terminal_cost=None,
                terminal_weight=None,
            )
            for index, unit in enumerate(pilot.storage)
        )
        endpoint, _ = _build_and_solve(
            network,
            "ac",
            case,
            endpoint_storage,
            AC_START,
            AC_STOP,
        )
        endpoint["outer_boundary_handoff"] = {
            "initial_local_boundary": AC_LOCAL_INITIAL_BOUNDARY,
            "target_local_boundary": AC_LOCAL_TARGET_BOUNDARY,
            "global_start": AC_START,
            "global_stop": AC_STOP,
            "storage_device_ids": [unit.device_id for unit in endpoint_storage],
            "initial_soc_mwh": initial,
            "target_soc_mwh": target,
        }
        records[1] = endpoint
        _atomic_gzip_json(checkpoint, artifact)
    end_context = _worker_context()
    artifact["end_context"] = end_context
    artifact["provenance_matches"] = start_context == end_context
    artifact["eligible_for_advancement"] = bool(
        artifact["provenance_matches"]
        and _scientific_registry_eligible(records)
    )
    _atomic_gzip_json(checkpoint, artifact)
    return artifact


def _rss_mib(pid: int) -> float | None:
    completed = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(completed.stdout.strip()) / 1024.0
    except ValueError:
        return None


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10.0)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _checkpoint_endpoint_pending(checkpoint: Path) -> bool:
    try:
        with gzip.open(checkpoint, "rt", encoding="utf-8") as stream:
            artifact = json.load(stream)
        records = artifact["records"]
        return any(
            record.get("record_id") == "endpoint_ac_6h"
            and record.get("classification") == "pending"
            for record in records
        )
    except (
        OSError,
        EOFError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return False


def _apply_supervision_outcome(
    artifact: dict[str, object],
    *,
    limit_reason: str | None,
    returncode: int | None,
    expected_context: Mapping[str, object],
) -> None:
    records = cast(list[dict[str, object]], artifact["records"])
    if limit_reason is not None:
        for record in records:
            if record.get("classification") == "pending":
                record.update(
                    _status_record(
                        str(record["record_id"]),
                        "resource_limit",
                        reason=limit_reason,
                    )
                )
        artifact["worker_classification"] = "resource_limit"
        artifact["eligible_for_advancement"] = False
    elif returncode != 0:
        for record in records:
            if record.get("classification") == "pending":
                record.update(
                    _status_record(
                        str(record["record_id"]),
                        "worker_failure",
                        reason=f"worker exited with code {returncode}",
                    )
                )
        artifact["worker_classification"] = "worker_failure"
        artifact["eligible_for_advancement"] = False
    start_context = artifact.get("start_context")
    end_context = artifact.get("end_context")
    parent_context_matches = (
        start_context == dict(expected_context)
        and end_context == dict(expected_context)
    )
    artifact["parent_context_matches"] = parent_context_matches
    if limit_reason is None and returncode == 0:
        if parent_context_matches:
            artifact["worker_classification"] = "completed"
            artifact["eligible_for_advancement"] = (
                _scientific_registry_eligible(records)
            )
        else:
            artifact["worker_classification"] = "provenance_mismatch"
            artifact["eligible_for_advancement"] = False


def supervise_worker(
    network: str,
    checkpoint: Path,
    expected_context: Mapping[str, object],
    *,
    ac_wall_limit_seconds: float,
    worker_total_limit_seconds: float,
    rss_limit_mib: float,
) -> dict[str, object]:
    command = [
        sys.executable,
        "-m",
        "experiments.case118_annual_hierarchy.run_s1",
        "--worker",
        network,
        "--output",
        str(checkpoint),
        "--expected-commit",
        str(expected_context["git_commit"]),
        "--expected-source-fingerprint",
        str(expected_context["source_fingerprint"]),
    ]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    started = time.monotonic()
    ac_started: float | None = None
    maximum_rss = 0.0
    limit_reason = None
    while process.poll() is None:
        if ac_started is None and _checkpoint_endpoint_pending(checkpoint):
            ac_started = time.monotonic()
        rss = _rss_mib(process.pid)
        if rss is not None:
            maximum_rss = max(maximum_rss, rss)
        elapsed = time.monotonic() - started
        if rss is not None and rss > rss_limit_mib:
            limit_reason = "rss_limit"
            break
        if elapsed > worker_total_limit_seconds:
            limit_reason = "total_wall_time_limit"
            break
        if (
            ac_started is not None
            and time.monotonic() - ac_started > ac_wall_limit_seconds
        ):
            limit_reason = "ac_wall_time_limit"
            break
        time.sleep(RSS_POLL_SECONDS)
    if limit_reason is not None:
        _terminate(process)
    stdout, stderr = process.communicate()
    if checkpoint.exists():
        try:
            with gzip.open(checkpoint, "rt", encoding="utf-8") as stream:
                artifact = cast(dict[str, object], json.load(stream))
        except (OSError, EOFError, json.JSONDecodeError, TypeError, ValueError):
            artifact = _unexecuted_worker(
                network,
                "worker_failure",
                "worker checkpoint was unreadable",
            )
    else:
        artifact = _unexecuted_worker(
            network,
            "resource_limit" if limit_reason else "worker_failure",
            reason=limit_reason or "worker exited before its first checkpoint",
        )
    _apply_supervision_outcome(
        artifact,
        limit_reason=limit_reason,
        returncode=process.returncode,
        expected_context=expected_context,
    )
    artifact["supervision"] = {
        "returncode": process.returncode,
        "limit_reason": limit_reason,
        "maximum_sampled_rss_mib": maximum_rss,
        "wall_seconds": time.monotonic() - started,
        "ac_wall_seconds": (
            None if ac_started is None else time.monotonic() - ac_started
        ),
        "stdout": stdout.decode(errors="replace"),
        "stderr": stderr.decode(errors="replace"),
    }
    _atomic_gzip_json(checkpoint, artifact)
    return artifact


def build_summary(output: Path) -> dict[str, object]:
    started = time.monotonic()
    expected_context = _worker_context()
    workers: list[dict[str, object]] = []
    for network in ("pglib_rated", "pglib_effectively_unlimited"):
        remaining = TOTAL_WALL_LIMIT_SECONDS - (time.monotonic() - started)
        if remaining <= 0.0:
            workers.append(
                {
                    "network": network,
                    "classification": "total_wall_time_limit_before_start",
                    "records": _unexecuted_worker(
                        network,
                        "resource_limit",
                        "total S1 wall-time limit reached before worker start",
                    )["records"],
                }
            )
            continue
        checkpoint = output.parent / "s1_workers" / f"{network}.json.gz"
        workers.append(
            supervise_worker(
                network,
                checkpoint,
                expected_context,
                ac_wall_limit_seconds=WORKER_WALL_LIMIT_SECONDS,
                worker_total_limit_seconds=remaining,
                rss_limit_mib=RSS_LIMIT_MIB,
            )
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "execution_source": expected_context,
        "interval": {
            "start": S1_START,
            "stop": S1_STOP,
            "endpoint_start": AC_START,
            "endpoint_stop": AC_STOP,
        },
        "resource_policy": {
            "rss_limit_mib": RSS_LIMIT_MIB,
            "worker_wall_limit_seconds": WORKER_WALL_LIMIT_SECONDS,
            "total_wall_limit_seconds": TOTAL_WALL_LIMIT_SECONDS,
            "rss_poll_seconds": RSS_POLL_SECONDS,
        },
        "workers": workers,
        "total_wall_seconds": time.monotonic() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--worker",
        choices=("pglib_rated", "pglib_effectively_unlimited"),
    )
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-source-fingerprint")
    arguments = parser.parse_args()
    if arguments.worker is not None:
        artifact = build_worker_artifact(
            arguments.worker,
            arguments.output,
            expected_commit=arguments.expected_commit,
            expected_source_fingerprint=arguments.expected_source_fingerprint,
        )
        print(json.dumps(_jsonable({"network": artifact["network"]})))
        return
    if _git_output("status", "--porcelain"):
        raise SystemExit("S1 execution requires a clean committed worktree")
    if arguments.output.exists() or (
        arguments.output.parent / "s1_workers"
    ).exists():
        raise SystemExit("S1 execution requires a fresh output location")
    summary = build_summary(arguments.output)
    _atomic_gzip_json(arguments.output, summary)
    print(arguments.output)


if __name__ == "__main__":
    main()
