"""Case118 S1 outer/endpoint archive sufficiency gate."""

from __future__ import annotations

from dataclasses import dataclass, replace
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence, cast

import numpy as np

from cvxopf import OPFBuild
from cvxopf.generator import gen_from_matpower
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy.pglib_case import (
    load_pglib_case118,
    make_effectively_unlimited_case,
)
from experiments.case118_annual_hierarchy.run_s0 import (
    BRANCH_LIMIT_SENTINEL_MW,
    _result_summary,
)
from experiments.case118_annual_hierarchy.run_s1 import (
    AC_LOCAL_INITIAL_BOUNDARY,
    AC_LOCAL_TARGET_BOUNDARY,
)
from experiments.case118_annual_hierarchy.scenario import (
    PILOT_GRID,
    materialize_pilot,
)


ROOT = Path(__file__).resolve().parent
METADATA_PATH = ROOT / "S1_RESULTS_METADATA.json"
ARTIFACT_PATH = ROOT / "results/s1_summary.json.gz"
FIXTURE_PATH = ROOT / "S1_OUTER_ENDPOINT_NORMALIZED.json"
EXPECTED_NORMALIZED_FIXTURE_SHA256 = (
    "f265f2620b64d4d067450f62920f4b22012b772e9b9c13b2fb6ad88c8fc2ead5"
)


@dataclass(frozen=True)
class S1BoundaryGateReport:
    """Outcome of tracked and optional source-backed S1 boundary checks."""

    source_artifact_available: bool
    source_integrity_verified: bool
    source_rederived: bool
    tracked_fixture_digest_verified: bool
    networks: tuple[str, ...]
    accepted_record_count: int
    reconstructed_audit_count: int
    direct_ac_nonexecution_count: int
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _schema(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, list):
        shape: list[int] = []
        cursor: object = value
        while isinstance(cursor, list):
            shape.append(len(cursor))
            cursor = cursor[0] if cursor else None
        return shape
    return "scalar"


def normalize_s1_artifact(artifact: Mapping[str, object]) -> Mapping[str, object]:
    """Create the compact, fully source-bound S1 boundary representation."""
    workers: list[dict[str, object]] = []
    for raw_worker in cast(Sequence[Mapping[str, object]], artifact["workers"]):
        records: list[dict[str, object]] = []
        for raw_record in cast(Sequence[Mapping[str, object]], raw_worker["records"]):
            result = cast(Mapping[str, object] | None, raw_record.get("result"))
            records.append(
                {
                    "record_id": raw_record["record_id"],
                    "classification": raw_record["classification"],
                    "accepted_primal": raw_record["accepted_primal"],
                    "builder_called": raw_record["builder_called"],
                    "solver_called": raw_record["solver_called"],
                    "audit": raw_record.get("audit"),
                    "dimensions": raw_record.get("dimensions"),
                    "outer_boundary_handoff": raw_record.get(
                        "outer_boundary_handoff"
                    ),
                    "result_schema": (
                        None
                        if result is None
                        else {
                            name: _schema(value)
                            for name, value in sorted(result.items())
                        }
                    ),
                    "result_sha256": _digest(result),
                    "summary": raw_record.get("summary"),
                    "reason": raw_record.get("reason"),
                    "complete_record_sha256": _digest(raw_record),
                }
            )
        workers.append(
            {
                "network": raw_worker["network"],
                "worker_classification": raw_worker["worker_classification"],
                "eligible_for_advancement": raw_worker["eligible_for_advancement"],
                "provenance_matches": raw_worker["provenance_matches"],
                "parent_context_matches": raw_worker["parent_context_matches"],
                "start_context_sha256": _digest(raw_worker["start_context"]),
                "end_context_sha256": _digest(raw_worker["end_context"]),
                "supervision_sha256": _digest(raw_worker["supervision"]),
                "complete_worker_sha256": _digest(raw_worker),
                "records": records,
            }
        )
    return {
        "schema_version": 1,
        "source_artifact": {
            "bytes": 402_529,
            "sha256": "c5f1021f3302b248e88bf911eebd74ab392fbfdf989ce325ca5485cf7da8e7a1",
        },
        "interval": artifact["interval"],
        "execution_source_sha256": _digest(artifact["execution_source"]),
        "resource_policy_sha256": _digest(artifact["resource_policy"]),
        "workers": workers,
    }


def validate_normalized_fixture(fixture: Mapping[str, object]) -> tuple[str, ...]:
    """Validate the complete digest plus the frozen scientific boundary."""
    failures: list[str] = []
    if _digest(fixture) != EXPECTED_NORMALIZED_FIXTURE_SHA256:
        failures.append("tracked_fixture_digest")
    workers = cast(Sequence[Mapping[str, object]], fixture.get("workers", ()))
    if tuple(worker.get("network") for worker in workers) != (
        "pglib_rated",
        "pglib_effectively_unlimited",
    ):
        failures.append("networks")
    for worker in workers:
        network = str(worker.get("network"))
        if (
            worker.get("worker_classification") != "completed"
            or worker.get("eligible_for_advancement") is not True
            or worker.get("provenance_matches") is not True
            or worker.get("parent_context_matches") is not True
        ):
            failures.append(f"worker_gate_{network}")
        records = cast(Sequence[Mapping[str, object]], worker.get("records", ()))
        if tuple(record.get("record_id") for record in records) != (
            "outer_lossy_dc_24h",
            "endpoint_ac_6h",
            "direct_ac_24h",
        ):
            failures.append(f"record_registry_{network}")
            continue
        for record in records[:2]:
            if (
                record.get("classification") != "accepted"
                or record.get("accepted_primal") is not True
                or record.get("builder_called") is not True
                or record.get("solver_called") is not True
                or cast(Mapping[str, object], record.get("audit"))["status"]
                != "optimal"
            ):
                failures.append(f"accepted_record_{network}_{record['record_id']}")
        direct = records[2]
        if (
            direct.get("classification")
            != "not_authorized_by_s0_resource_gate"
            or direct.get("accepted_primal") is not False
            or direct.get("builder_called") is not False
            or direct.get("solver_called") is not False
        ):
            failures.append(f"direct_ac_nonexecution_{network}")
        outer_dimensions = cast(Mapping[str, object], records[0]["dimensions"])
        endpoint_dimensions = cast(Mapping[str, object], records[1]["dimensions"])
        if (
            outer_dimensions.get("scalar_variables") != 6000
            or endpoint_dimensions.get("scalar_variables") != 13752
        ):
            failures.append(f"dimensions_{network}")
        handoff = cast(Mapping[str, object], records[1]["outer_boundary_handoff"])
        if (
            handoff.get("initial_local_boundary") != AC_LOCAL_INITIAL_BOUNDARY
            or handoff.get("target_local_boundary") != AC_LOCAL_TARGET_BOUNDARY
        ):
            failures.append(f"handoff_indices_{network}")
    return tuple(failures)


def _case(network: str) -> dict[str, object]:
    rated = load_pglib_case118()
    return (
        rated
        if network == "pglib_rated"
        else make_effectively_unlimited_case(rated)
    )


def _reconstruct_source_audits(
    artifact: Mapping[str, object], failures: list[str]
) -> int:
    count = 0
    for worker in cast(Sequence[Mapping[str, object]], artifact["workers"]):
        network = str(worker["network"])
        case = _case(network)
        pilot = materialize_pilot(case, PILOT_GRID[0])
        generators = tuple(gen_from_matpower(case["gen"], case["gencost"]))
        records = cast(Sequence[Mapping[str, object]], worker["records"])
        outer_result = cast(Mapping[str, object], records[0]["result"])
        outer_soc = np.asarray(outer_result["soc"], dtype=float)
        handoff = cast(Mapping[str, object], records[1]["outer_boundary_handoff"])
        ids = tuple(str(unit.device_id) for unit in pilot.storage)
        handoff_ids = tuple(
            str(value)
            for value in cast(Sequence[object], handoff["storage_device_ids"])
        )
        initial_values = tuple(
            float(cast(float, value))
            for value in cast(Sequence[object], handoff["initial_soc_mwh"])
        )
        target_values = tuple(
            float(cast(float, value))
            for value in cast(Sequence[object], handoff["target_soc_mwh"])
        )
        initial = dict(zip(handoff_ids, initial_values, strict=True))
        target = dict(zip(handoff_ids, target_values, strict=True))
        if handoff_ids != ids:
            failures.append(f"handoff_identity_{network}")
        endpoint_storage = tuple(
            replace(
                unit,
                initial_soc=initial[device_id],
                terminal_soc=target[device_id],
                terminal_constraint="equality",
            )
            for unit, device_id in zip(pilot.storage, ids, strict=True)
        )
        if not np.array_equal(
            outer_soc[AC_LOCAL_INITIAL_BOUNDARY - 1],
            np.asarray(initial_values, dtype=float),
        ) or not np.array_equal(
            outer_soc[AC_LOCAL_TARGET_BOUNDARY - 1],
            np.asarray(target_values, dtype=float),
        ):
            failures.append(f"outer_handoff_{network}")
        for index, (formulation, storage) in enumerate(
            (("lossy_dc", pilot.storage), ("ac", endpoint_storage))
        ):
            record = records[index]
            result = cast(Mapping[str, object], record["result"])
            build = cast(OPFBuild, SimpleNamespace(formulation=formulation))
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
            archived_audit = cast(Mapping[str, object], record["audit"])
            if (
                not audit.accepted_primal
                or audit.status != archived_audit["status"]
                or list(audit.missing_or_nonfinite_fields)
                != archived_audit["missing_or_nonfinite_fields"]
                or audit.identity_error != archived_audit["identity_error"]
                or dict(audit.residuals) != archived_audit["residuals"]
            ):
                failures.append(f"audit_reconstruction_{network}_{formulation}")
            summary = _result_summary(case, formulation, result)
            if summary != record["summary"]:
                failures.append(f"summary_reconstruction_{network}_{formulation}")
            count += 1
    return count


def run_s1_boundary_gate() -> S1BoundaryGateReport:
    """Validate tracked S1 evidence and reconstruct it when source is present."""
    metadata = cast(Mapping[str, object], json.loads(METADATA_PATH.read_text()))
    fixture = cast(Mapping[str, object], json.loads(FIXTURE_PATH.read_text()))
    failures = list(validate_normalized_fixture(fixture))
    source_spec = cast(Mapping[str, object], metadata["artifact"])
    expected_source = {"bytes": source_spec["bytes"], "sha256": source_spec["sha256"]}
    if fixture["source_artifact"] != expected_source:
        failures.append("tracked_source_provenance")
    available = ARTIFACT_PATH.is_file()
    integrity = False
    rederived = False
    audit_count = 0
    if available:
        integrity = (
            ARTIFACT_PATH.stat().st_size == source_spec["bytes"]
            and _sha256(ARTIFACT_PATH) == source_spec["sha256"]
        )
        if not integrity:
            failures.append("source_integrity")
        else:
            with gzip.open(ARTIFACT_PATH, "rt", encoding="utf-8") as stream:
                artifact = cast(Mapping[str, object], json.load(stream))
            if normalize_s1_artifact(artifact) != fixture:
                failures.append("derived_fixture_drift")
            else:
                rederived = True
                audit_count = _reconstruct_source_audits(artifact, failures)
    workers = cast(Sequence[Mapping[str, object]], fixture["workers"])
    return S1BoundaryGateReport(
        source_artifact_available=available,
        source_integrity_verified=integrity,
        source_rederived=rederived,
        tracked_fixture_digest_verified="tracked_fixture_digest" not in failures,
        networks=tuple(str(worker["network"]) for worker in workers),
        accepted_record_count=4,
        reconstructed_audit_count=audit_count,
        direct_ac_nonexecution_count=2,
        failures=tuple(failures),
    )


__all__ = [
    "ARTIFACT_PATH",
    "EXPECTED_NORMALIZED_FIXTURE_SHA256",
    "FIXTURE_PATH",
    "S1BoundaryGateReport",
    "normalize_s1_artifact",
    "run_s1_boundary_gate",
    "validate_normalized_fixture",
]
