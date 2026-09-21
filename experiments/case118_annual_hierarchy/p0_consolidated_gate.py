"""Consolidated executable decision and record for Case118 P0."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from time import perf_counter
from typing import Mapping, Sequence

from experiments.case118_annual_hierarchy.p0_equivalence import (
    NominalEquivalenceReport,
    run_nominal_equivalence,
)
from experiments.case118_annual_hierarchy.p0_import_gate import (
    ImportGateReport,
    run_import_gate,
)
from experiments.case118_annual_hierarchy.p0_injected_equivalence import (
    INJECTED_CASES,
    InjectedEquivalenceReport,
    run_injected_equivalence,
)
from experiments.case118_annual_hierarchy.p0_persistence_gate import (
    PersistenceGateReport,
    run_persistence_gate,
)
from experiments.case118_annual_hierarchy.p0_s1_boundary_gate import (
    S1BoundaryGateReport,
    run_s1_boundary_gate,
)
from experiments.case118_annual_hierarchy.p0_s3b_gate import (
    S3BNormalizationReport,
    run_s3b_normalization_gate,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_REPORT_PATH = ROOT / "P0_RESULTS.json"
NOMINAL_HORIZONS = (6, 24)
EXPECTED_INJECTED_REGISTRY_SHA256 = (
    "01d94f02e148723d4bfb19932dc7a07590e5bbef0788a7203f4618ff3bb49390"
)
EXECUTION_SOURCES = (
    "audit.py",
    "p0_consolidated_gate.py",
    "p0_equivalence.py",
    "p0_fixture.py",
    "p0_import_gate.py",
    "p0_injected_equivalence.py",
    "p0_persistence_gate.py",
    "p0_s1_boundary_gate.py",
    "p0_s3b_gate.py",
    "streaming_archive.py",
    "streaming_driver.py",
    "streaming_runner.py",
    "streaming_schema.py",
)


@dataclass(frozen=True)
class ConsolidatedP0Report:
    """Complete P0 evidence tree and formal advancement decision."""

    schema_version: int
    created_at_utc: str
    execution_context: Mapping[str, object]
    nominal: tuple[NominalEquivalenceReport, ...]
    injected: tuple[InjectedEquivalenceReport, ...]
    persistence: PersistenceGateReport
    s3b: S3BNormalizationReport
    s1_boundary: S1BoundaryGateReport
    import_boundary: ImportGateReport
    clean_source_required: bool
    wall_time_seconds: float
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_json_value(self) -> Mapping[str, object]:
        value = asdict(self)
        value["passed"] = self.passed
        if self.passed and self.clean_source_required:
            decision = "advance_to_s2"
        elif self.passed:
            decision = "preliminary_pass"
        else:
            decision = "p0_blocked"
        value["decision"] = decision
        return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(args: Sequence[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def execution_context() -> Mapping[str, object]:
    """Identify the exact source tree used by one consolidated execution."""
    source_hashes = {
        name: _sha256(ROOT / name)
        for name in EXECUTION_SOURCES
    }
    return {
        "git_commit": _git(("rev-parse", "HEAD")),
        "git_status_porcelain": _git(("status", "--porcelain")),
        "source_sha256": source_hashes,
        "combined_source_sha256": hashlib.sha256(
            json.dumps(
                source_hashes, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
    }


def _injected_registry_sha256() -> str:
    value = [
        {
            "name": case.name,
            "horizon_steps": case.horizon_steps,
            "outcomes": [
                [iteration, ordinal, outcome]
                for (iteration, ordinal), outcome in sorted(case.outcomes.items())
            ],
            "expected_controlling_ordinals": list(
                case.expected_controlling_ordinals
            ),
            "expected_completed_intervals": case.expected_completed_intervals,
            "expected_terminal_outcome": case.expected_terminal_outcome,
        }
        for case in INJECTED_CASES
    ]
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _collect_failures(
    nominal: Sequence[NominalEquivalenceReport],
    injected: Sequence[InjectedEquivalenceReport],
    persistence: PersistenceGateReport,
    s3b: S3BNormalizationReport,
    s1_boundary: S1BoundaryGateReport,
    import_boundary: ImportGateReport,
    *,
    context: Mapping[str, object],
    clean_source_required: bool,
) -> tuple[str, ...]:
    failures: list[str] = []
    if tuple(report.horizon_steps for report in nominal) != NOMINAL_HORIZONS:
        failures.append("nominal:registry")
    for nominal_report in nominal:
        if not nominal_report.equivalent:
            failures.extend(
                f"nominal:{nominal_report.horizon_steps}:{item}"
                for item in nominal_report.mismatches
            )
        if nominal_report.completed_intervals != nominal_report.horizon_steps:
            failures.append(f"nominal:{nominal_report.horizon_steps}:completion")
    expected_cases = tuple(case.name for case in INJECTED_CASES)
    if _injected_registry_sha256() != EXPECTED_INJECTED_REGISTRY_SHA256:
        failures.append("injected:registry_digest")
    if tuple(report.case_name for report in injected) != expected_cases:
        failures.append("injected:registry")
    for injected_report in injected:
        if not injected_report.equivalent:
            failures.extend(
                f"injected:{injected_report.case_name}:{item}"
                for item in injected_report.mismatches
            )
        if not injected_report.unsuccessful_evidence_verified:
            failures.append(
                f"injected:{injected_report.case_name}:failure_evidence"
            )
    failures.extend(f"persistence:{item}" for item in persistence.failures)
    failures.extend(f"s3b:{item}" for item in s3b.failures)
    failures.extend(f"s1_boundary:{item}" for item in s1_boundary.failures)
    failures.extend(
        f"import:{item.path}:{item.line}:{item.imported_module}"
        for item in import_boundary.violations
    )
    if clean_source_required and context["git_status_porcelain"] != "":
        failures.append("provenance:dirty_worktree")
    return tuple(failures)


def run_consolidated_p0(
    root: Path, *, clean_source_required: bool = True
) -> ConsolidatedP0Report:
    """Execute every frozen P0 sub-gate and return one decision record."""
    started = perf_counter()
    root.mkdir(parents=True, exist_ok=True)
    nominal = tuple(
        run_nominal_equivalence(horizon, root / f"nominal-{horizon}h")
        for horizon in NOMINAL_HORIZONS
    )
    injected = tuple(
        run_injected_equivalence(case, root / f"injected-{case.name}")
        for case in INJECTED_CASES
    )
    persistence = run_persistence_gate(root / "persistence")
    s3b = run_s3b_normalization_gate()
    s1_boundary = run_s1_boundary_gate()
    import_boundary = run_import_gate()
    context = execution_context()
    failures = _collect_failures(
        nominal,
        injected,
        persistence,
        s3b,
        s1_boundary,
        import_boundary,
        context=context,
        clean_source_required=clean_source_required,
    )
    return ConsolidatedP0Report(
        schema_version=1,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        execution_context=context,
        nominal=nominal,
        injected=injected,
        persistence=persistence,
        s3b=s3b,
        s1_boundary=s1_boundary,
        import_boundary=import_boundary,
        clean_source_required=clean_source_required,
        wall_time_seconds=perf_counter() - started,
        failures=failures,
    )


def atomic_write_report(path: Path, report: ConsolidatedP0Report) -> None:
    """Publish a strict-JSON closure record atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        report.to_json_value(), indent=2, sort_keys=True, allow_nan=False
    )
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument(
        "--allow-dirty-preliminary",
        action="store_true",
        help="run all gates but prohibit an advance_to_s2 decision",
    )
    args = parser.parse_args()
    report = run_consolidated_p0(
        args.work_directory,
        clean_source_required=not args.allow_dirty_preliminary,
    )
    atomic_write_report(args.report, report)
    print(json.dumps(report.to_json_value(), indent=2, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_REPORT_PATH",
    "EXECUTION_SOURCES",
    "EXPECTED_INJECTED_REGISTRY_SHA256",
    "NOMINAL_HORIZONS",
    "ConsolidatedP0Report",
    "atomic_write_report",
    "execution_context",
    "run_consolidated_p0",
]
