"""Analyze the non-promotional M14c stepwise/vectorized prefix profile."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from typing import Mapping, cast

import cvxpy as cp
import numpy as np

from cvxopf.generator import gen_cost_expr, generator_gencost
from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.streaming_archive import (
    load_verified_outer_plan_archive,
)
from experiments.case118_annual_hierarchy.streaming_runner import StreamingOuterPlan
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    sha256_path,
)
from experiments.m14_time_vectorization.m14c_prefix_fixture import (
    M14C_INTEGRATION_COMMIT,
    PRE_LADDER_INTEGRATION_CHECKPOINT,
    PRE_LADDER_INTEGRATION_SHA256,
    PREFIX_LADDER_HORIZONS,
    PREFIX_LADDER_OUTPUT_DIRECTORY,
    load_prefix_fixture,
)
from experiments.m14_time_vectorization.run_m14c_prefix_profile import (
    PROFILE_SOURCE_FILES,
    PROFILE_OUTPUT_DIRECTORY,
    REFERENCE_EXECUTION_COMMIT,
    REFERENCE_LADDER_RESULT_SHA256,
    SCHEMA_VERSION,
    profile_source_fingerprint,
    shared_production_fingerprint,
    validate_reference_ladder,
)


ABSOLUTE_TOLERANCE = 2e-5
RELATIVE_TOLERANCE = 1e-9
RESIDUAL_GATED_NONUNIQUE_FIELDS = frozenset({"p_flows"})
BR_R = 2
ANALYSIS_FILES = (
    *PROFILE_SOURCE_FILES,
    "experiments/m14_time_vectorization/m14c_prefix_profile_analysis.py",
)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return cast(Mapping[str, object], value)


def _number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def analysis_source_fingerprint() -> str:
    digest = hashlib.sha256()
    paths = [ROOT / name for name in ANALYSIS_FILES]
    paths.extend((ROOT / "src/cvxopf").rglob("*.py"))
    for path in sorted(set(paths)):
        if not path.is_file():
            raise FileNotFoundError("M14c profile analysis source is incomplete")
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _phase_times(worker: Mapping[str, object]) -> Mapping[str, float]:
    samples = cast(list[object], worker.get("resource_samples"))
    by_name = {
        str(_mapping(item, "phase sample")["phase"]): _number(
            _mapping(item, "phase sample")["elapsed_seconds"], "phase elapsed"
        )
        for item in samples
    }
    required = {
        "before_construction",
        "after_construction",
        "before_solve",
        "after_solve",
        "after_archive",
        "after_release",
    }
    if not required <= set(by_name):
        raise ValueError("profile phase evidence is incomplete")
    return {
        "construction_seconds": by_name["after_construction"]
        - by_name["before_construction"],
        "canonicalization_solve_seconds": by_name["after_solve"]
        - by_name["before_solve"],
        "postsolve_to_archive_seconds": by_name["after_archive"]
        - by_name["after_solve"],
        "release_seconds": by_name["after_release"] - by_name["after_archive"],
    }


def _validate_stepwise_context(context: Mapping[str, object], horizon: int) -> None:
    fixture = load_prefix_fixture(horizon)
    limits = fixture.limits
    commit = context.get("git_commit")
    if not isinstance(commit, str) or len(commit) != 40:
        raise ValueError("stepwise profile execution commit is invalid")
    production_fingerprint = shared_production_fingerprint(commit)
    reference_production_fingerprint = shared_production_fingerprint(
        REFERENCE_EXECUTION_COMMIT
    )
    if (
        context.get("schema_version") != SCHEMA_VERSION
        or context.get("git_clean") is not True
        or context.get("source_fingerprint") != profile_source_fingerprint(commit)
        or context.get("shared_production_fingerprint") != production_fingerprint
        or context.get("reference_shared_production_fingerprint")
        != reference_production_fingerprint
        or context.get("shared_production_matches_reference")
        != (production_fingerprint == reference_production_fingerprint)
        or context.get("horizon_steps") != horizon
        or context.get("prefix_input_sha256") != fixture.input_sha256
        or context.get("prefix_scenario_sha256") != fixture.scenario_sha256
        or context.get("policy_sha256") != fixture.annual.policy_sha256
        or context.get("solve_config_sha256") != fixture.annual.solve_config_sha256
        or context.get("m14c_integration_commit") != M14C_INTEGRATION_COMMIT
        or context.get("m14c_integration_sha256") != PRE_LADDER_INTEGRATION_SHA256
        or context.get("m14c_integration_checkpoint")
        != PRE_LADDER_INTEGRATION_CHECKPOINT
        or context.get("m14c_source_commit") != fixture.annual.m14c_source_commit
        or context.get("big_experiment_parent_commit")
        != fixture.annual.big_experiment_parent_commit
        or context.get("m14c_merge_base_commit")
        != fixture.annual.m14c_merge_base_commit
        or context.get("annual_component_hashes") != dict(fixture.annual.hashes)
        or context.get("annual_scenario_sha256") != fixture.annual.scenario_hash
        or context.get("prefix_ladder_executed") is not False
        or context.get("annual_execution_authorized") is not False
        or context.get("temporal_assembly") != "stepwise"
        or context.get("canonicalization_backend") != "CPP"
        or context.get("generator_quadratic_cost")
        != fixture.annual.generator_quadratic_cost
        or context.get("generator_conditioning_evidence_sha256")
        != fixture.annual.generator_conditioning_evidence_sha256
        or context.get("reference_temporal_assembly") != "vectorized"
        or context.get("reference_canonicalization_backend") != "SCIPY"
        or context.get("reference_ladder_result_sha256")
        != REFERENCE_LADDER_RESULT_SHA256
        or context.get("resource_policy")
        != {
            "rss_limit_mib": limits.child_rss_mib,
            "worker_wall_seconds": limits.worker_wall_seconds,
            "supervisor_wall_seconds": limits.supervisor_wall_seconds,
            "poll_seconds": limits.poll_seconds,
        }
        or not isinstance(context.get("software_versions"), Mapping)
        or not isinstance(context.get("platform"), str)
        or not isinstance(context.get("architecture"), str)
    ):
        raise ValueError("stepwise profile execution context is invalid")
    if _git("merge-base", M14C_INTEGRATION_COMMIT, commit) != M14C_INTEGRATION_COMMIT:
        raise ValueError("stepwise profile execution lacks integration ancestry")


def _load_point(
    directory: Path, horizon: int, *, reference: bool
) -> tuple[
    StreamingOuterPlan,
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
]:
    supervision = _mapping(
        json.loads((directory / "supervision.json").read_text()), "supervision"
    )
    worker = _mapping(
        json.loads((directory / "worker-result.json").read_text()), "worker"
    )
    context = _mapping(
        json.loads((directory / "execution-context.json").read_text()), "context"
    )
    if not reference:
        _validate_stepwise_context(context, horizon)
    if (
        supervision.get("classification") != "accepted"
        or supervision.get("returncode") != 0
        or supervision.get("resource_triggers") != []
        or supervision.get("resource_policy") != context.get("resource_policy")
        or supervision.get("worker_result") != worker
        or worker.get("classification") != "accepted"
        or worker.get("context_matches") is not True
        or worker.get("start_context") != context
        or worker.get("end_context") != context
        or supervision.get("outer_plan_sha256")
        != sha256_path(directory / "outer-plan.json.gz")
        or supervision.get("worker_log_sha256") != sha256_path(directory / "worker.log")
    ):
        raise ValueError("accepted profile point lifecycle is inconsistent")
    peak_rss = _number(supervision.get("peak_sampled_rss_mib"), "peak RSS")
    limits = load_prefix_fixture(horizon).limits
    if peak_rss <= 0.0 or peak_rss > limits.child_rss_mib:
        raise ValueError("accepted profile point RSS evidence is invalid")
    worker_wall = _number(worker.get("wall_time_seconds"), "worker wall time")
    supervisor_wall = _number(
        supervision.get("wall_time_seconds"), "supervisor wall time"
    )
    if (
        worker_wall > limits.worker_wall_seconds
        or supervisor_wall > limits.supervisor_wall_seconds
    ):
        raise ValueError("accepted profile point exceeded a wall-time limit")
    fixture = load_prefix_fixture(horizon)
    outer = load_verified_outer_plan_archive(
        directory / "outer-plan.json.gz",
        inputs=fixture.inputs,
        policy=fixture.annual.policy,
        expected_solve_config_sha256=fixture.annual.solve_config_sha256,
        expected_source_fingerprint=str(context["source_fingerprint"]),
        expected_scenario_hash=fixture.scenario_sha256,
    )
    expected_assembly = "vectorized" if reference else "stepwise"
    expected_backend = "SCIPY" if reference else "CPP"
    if (
        outer.temporal_assembly != expected_assembly
        or outer.canonicalization_backend != expected_backend
        or not outer.accepted_primal
        or outer.audit.accepted_primal is not True
    ):
        raise ValueError("profile point representation or audit is not accepted")
    return outer, worker, supervision, context


def _result_mismatches(
    left: Mapping[str, object], right: Mapping[str, object]
) -> list[str]:
    mismatches: list[str] = []
    if left.keys() != right.keys():
        return ["result_schema"]
    for name in left:
        a = np.asarray(left[name])
        b = np.asarray(right[name])
        if name in RESIDUAL_GATED_NONUNIQUE_FIELDS:
            if a.shape != b.shape:
                mismatches.append(f"{name}.schema")
            continue
        if a.shape != b.shape:
            mismatches.append(name)
        elif np.issubdtype(a.dtype, np.number) and np.issubdtype(b.dtype, np.number):
            if not np.allclose(
                a.astype(float),
                b.astype(float),
                atol=ABSOLUTE_TOLERANCE,
                rtol=RELATIVE_TOLERANCE,
                equal_nan=True,
            ):
                mismatches.append(name)
        elif not np.array_equal(a, b):
            mismatches.append(name)
    return mismatches


def _reference_registry(root: Path) -> Mapping[int, Path]:
    validated = validate_reference_ladder(root)
    return {
        horizon: cast(Path, record["directory"])
        for horizon, record in validated.items()
    }


def _resource_evidence(
    worker: Mapping[str, object], supervision: Mapping[str, object]
) -> Mapping[str, object]:
    samples = cast(list[object], worker.get("resource_samples"))
    phase_samples = []
    for item in samples:
        sample = _mapping(item, "worker phase RSS sample")
        phase_samples.append(
            {
                "phase": str(sample.get("phase")),
                "elapsed_seconds": _number(
                    sample.get("elapsed_seconds"), "phase elapsed"
                ),
                "rss_bytes": _number(sample.get("rss_bytes"), "phase RSS"),
            }
        )
    return {
        "external_rss_mib": {
            "first": _number(supervision.get("first_sampled_rss_mib"), "first RSS"),
            "peak": _number(supervision.get("peak_sampled_rss_mib"), "peak RSS"),
            "final": _number(supervision.get("final_sampled_rss_mib"), "final RSS"),
        },
        "supervisor_total_wall_seconds": _number(
            supervision.get("wall_time_seconds"), "supervisor wall"
        ),
        "worker_phase_rss": phase_samples,
    }


def _declared_component_costs(
    outer: StreamingOuterPlan, horizon: int
) -> Mapping[str, float]:
    """Reconstruct every named component cost available in the frozen model."""
    fixture = load_prefix_fixture(horizon)
    pg = np.asarray(outer.result["Pg"], dtype=float)
    gencost = generator_gencost(list(fixture.inputs.generators))
    generation_cost = fixture.inputs.delta * sum(
        float(gen_cost_expr(gencost, cp.Constant(row)).value) for row in pg
    )
    branch_resistance = np.asarray(fixture.inputs.case["branch"], dtype=float)[:, BR_R]
    base_mva = float(fixture.inputs.case["baseMVA"])
    branch_flow_pu = np.asarray(outer.result["p_flows"], dtype=float) / base_mva
    dc_loss_cost = (
        fixture.inputs.delta
        * fixture.inputs.options.loss_weight
        * float(np.sum(branch_resistance * np.square(branch_flow_pu)))
    )
    costs: dict[str, float] = {
        "generation_cost": generation_cost,
        "dc_loss_cost": dc_loss_cost,
    }
    for name, value in outer.result.items():
        if name.endswith("_cost"):
            costs[name] = _number(value, f"component cost {name}")
    return costs


def _context_comparability(
    vectorized: Mapping[str, object], stepwise: Mapping[str, object]
) -> Mapping[str, object]:
    fields = (
        "shared_production_fingerprint",
        "platform",
        "architecture",
        "software_versions",
    )
    mismatched = [name for name in fields if vectorized.get(name) != stepwise.get(name)]
    return {
        "classification": (
            "matched_execution_context"
            if not mismatched
            else "descriptive_mismatched_execution_context"
        ),
        "mismatched_fields": mismatched,
        "git_commit": {
            "vectorized": vectorized.get("git_commit"),
            "stepwise": stepwise.get("git_commit"),
            "match": vectorized.get("git_commit") == stepwise.get("git_commit"),
        },
        "source_fingerprint": {
            "vectorized": vectorized.get("source_fingerprint"),
            "stepwise": stepwise.get("source_fingerprint"),
            "match": vectorized.get("source_fingerprint")
            == stepwise.get("source_fingerprint"),
        },
        "shared_production_fingerprint": {
            "vectorized": vectorized.get("shared_production_fingerprint"),
            "stepwise": stepwise.get("shared_production_fingerprint"),
            "match": vectorized.get("shared_production_fingerprint")
            == stepwise.get("shared_production_fingerprint"),
        },
        "platform_match": vectorized.get("platform") == stepwise.get("platform"),
        "architecture_match": vectorized.get("architecture")
        == stepwise.get("architecture"),
        "software_versions_match": vectorized.get("software_versions")
        == stepwise.get("software_versions"),
    }


def analyze_profile(
    directory: Path = PROFILE_OUTPUT_DIRECTORY,
    reference_root: Path = PREFIX_LADDER_OUTPUT_DIRECTORY,
) -> Mapping[str, object]:
    directory = directory.expanduser().resolve()
    reference_root = reference_root.expanduser().resolve()
    if _git("status", "--porcelain") != "":
        raise ValueError("M14c prefix profile analysis requires a clean worktree")
    profile_result = _mapping(
        json.loads((directory / "profile-result.json").read_text()), "profile result"
    )
    records = cast(list[object], profile_result.get("records"))
    if (
        profile_result.get("schema_version") != SCHEMA_VERSION
        or profile_result.get("classification") != "accepted"
        or profile_result.get("execution_complete") is not True
        or profile_result.get("annual_execution_authorized") is not False
        or profile_result.get("reference_ladder_result_sha256")
        != REFERENCE_LADDER_RESULT_SHA256
        or len(records) != len(PREFIX_LADDER_HORIZONS)
    ):
        raise ValueError("stepwise profile root is incomplete")
    references = _reference_registry(reference_root)
    comparisons = []
    mathematical_mismatches: list[str] = []
    for horizon, item in zip(PREFIX_LADDER_HORIZONS, records, strict=True):
        record = _mapping(item, "profile record")
        point = directory / str(record["directory"])
        if (
            record.get("horizon_steps") != horizon
            or record.get("classification") != "accepted"
            or record.get("supervision_sha256")
            != sha256_path(point / "supervision.json")
        ):
            raise ValueError("stepwise profile registry is inconsistent")
        vector, vector_worker, vector_supervision, vector_context = _load_point(
            references[horizon], horizon, reference=True
        )
        stepwise, step_worker, step_supervision, step_context = _load_point(
            point, horizon, reference=False
        )
        mismatches = _result_mismatches(vector.result, stepwise.result)
        vector_costs = _declared_component_costs(vector, horizon)
        stepwise_costs = _declared_component_costs(stepwise, horizon)
        vector_objective = float(cast(float, vector.result["objective"]))
        stepwise_objective = float(cast(float, stepwise.result["objective"]))
        if vector_costs.keys() != stepwise_costs.keys():
            mismatches.append("component_cost_schema")
        component_costs = {}
        for name in sorted(vector_costs.keys() & stepwise_costs.keys()):
            vector_value = vector_costs[name]
            stepwise_value = stepwise_costs[name]
            difference = abs(vector_value - stepwise_value)
            component_costs[name] = {
                "vectorized": vector_value,
                "stepwise": stepwise_value,
                "absolute_difference": difference,
            }
            if not np.isclose(
                vector_value,
                stepwise_value,
                atol=ABSOLUTE_TOLERANCE,
                rtol=RELATIVE_TOLERANCE,
            ):
                mismatches.append(f"component_cost.{name}")
        if vector.storage_device_ids != stepwise.storage_device_ids:
            mismatches.append("storage_device_ids")
        if not np.array_equal(
            np.asarray(vector.global_boundary_indices),
            np.asarray(stepwise.global_boundary_indices),
        ):
            mismatches.append("global_boundary_indices")
        if not np.allclose(
            np.asarray(vector.boundary_soc_mwh, dtype=float),
            np.asarray(stepwise.boundary_soc_mwh, dtype=float),
            atol=ABSOLUTE_TOLERANCE,
            rtol=RELATIVE_TOLERANCE,
        ):
            mismatches.append("boundary_soc_mwh")
        mathematical_mismatches.extend(f"{horizon}:{name}" for name in mismatches)
        vector_times = _phase_times(vector_worker)
        step_times = _phase_times(step_worker)
        detailed = _mapping(step_worker.get("phase_timings"), "stepwise timings")
        vector_peak = _number(
            vector_supervision.get("peak_sampled_rss_mib"), "vector peak RSS"
        )
        step_peak = _number(
            step_supervision.get("peak_sampled_rss_mib"), "stepwise peak RSS"
        )
        vector_wall = _number(vector_worker.get("wall_time_seconds"), "vector wall")
        step_wall = _number(step_worker.get("wall_time_seconds"), "stepwise wall")
        vector_supervisor_wall = _number(
            vector_supervision.get("wall_time_seconds"), "vector supervisor wall"
        )
        step_supervisor_wall = _number(
            step_supervision.get("wall_time_seconds"), "stepwise supervisor wall"
        )
        comparisons.append(
            {
                "horizon_steps": horizon,
                "mathematically_equivalent": not mismatches,
                "mismatches": mismatches,
                "residual_gated_nonunique_fields": sorted(
                    RESIDUAL_GATED_NONUNIQUE_FIELDS
                ),
                "execution_context_comparability": _context_comparability(
                    {
                        **vector_context,
                        "shared_production_fingerprint": (
                            shared_production_fingerprint(REFERENCE_EXECUTION_COMMIT)
                        ),
                    },
                    step_context,
                ),
                "objective": {
                    "vectorized": vector_objective,
                    "stepwise": stepwise_objective,
                    "absolute_difference": abs(vector_objective - stepwise_objective),
                    "accounting_residual": {
                        "vectorized": abs(
                            vector_objective - sum(vector_costs.values())
                        ),
                        "stepwise": abs(
                            stepwise_objective - sum(stepwise_costs.values())
                        ),
                    },
                },
                "component_costs": component_costs,
                "audit": {
                    "vectorized_accepted": vector.audit.accepted_primal,
                    "stepwise_accepted": stepwise.audit.accepted_primal,
                    "vectorized_residuals": dict(vector.audit.residuals),
                    "stepwise_residuals": dict(stepwise.audit.residuals),
                },
                "timing_seconds": {
                    "vectorized": {
                        **vector_times,
                        "worker_total": vector_wall,
                        "supervisor_total": vector_supervisor_wall,
                        "extraction": None,
                        "audit": None,
                        "archive": None,
                    },
                    "stepwise": {
                        **step_times,
                        "worker_total": step_wall,
                        "supervisor_total": step_supervisor_wall,
                        "extraction": _number(
                            detailed.get("extraction_seconds"), "stepwise extraction"
                        ),
                        "audit": _number(
                            detailed.get("audit_seconds"), "stepwise audit"
                        ),
                        "archive": _number(
                            detailed.get("archive_seconds"), "stepwise archive"
                        ),
                    },
                    "ratios_stepwise_over_vectorized": {
                        "construction": step_times["construction_seconds"]
                        / vector_times["construction_seconds"],
                        "canonicalization_solve": step_times[
                            "canonicalization_solve_seconds"
                        ]
                        / vector_times["canonicalization_solve_seconds"],
                        "worker_total": step_wall / vector_wall,
                    },
                    "absolute_differences_stepwise_minus_vectorized": {
                        "construction": step_times["construction_seconds"]
                        - vector_times["construction_seconds"],
                        "canonicalization_solve": step_times[
                            "canonicalization_solve_seconds"
                        ]
                        - vector_times["canonicalization_solve_seconds"],
                        "postsolve_to_archive": step_times[
                            "postsolve_to_archive_seconds"
                        ]
                        - vector_times["postsolve_to_archive_seconds"],
                        "worker_total": step_wall - vector_wall,
                        "supervisor_total": step_supervisor_wall
                        - vector_supervisor_wall,
                    },
                },
                "peak_rss_mib": {
                    "vectorized": vector_peak,
                    "stepwise": step_peak,
                    "ratio_stepwise_over_vectorized": step_peak / vector_peak,
                    "absolute_difference_stepwise_minus_vectorized": step_peak
                    - vector_peak,
                },
                "resource_evidence": {
                    "vectorized": _resource_evidence(vector_worker, vector_supervision),
                    "stepwise": _resource_evidence(step_worker, step_supervision),
                },
                "boundary_characterization": {
                    "storage_device_ids": list(vector.storage_device_ids),
                    "global_boundary_indices": {
                        "vectorized": np.asarray(
                            vector.global_boundary_indices, dtype=int
                        ).tolist(),
                        "stepwise": np.asarray(
                            stepwise.global_boundary_indices, dtype=int
                        ).tolist(),
                    },
                    "soc_mwh": {
                        "vectorized": np.asarray(
                            vector.boundary_soc_mwh, dtype=float
                        ).tolist(),
                        "stepwise": np.asarray(
                            stepwise.boundary_soc_mwh, dtype=float
                        ).tolist(),
                    },
                },
                "dimensions": {
                    "vectorized": vector_worker.get("dimensions"),
                    "stepwise": step_worker.get("dimensions"),
                },
            }
        )
    result = {
        "schema_version": SCHEMA_VERSION,
        "classification": "accepted" if not mathematical_mismatches else "mismatch",
        "execution_complete": True,
        "mathematical_mismatches": mathematical_mismatches,
        "performance_is_descriptive": True,
        "qualified_for_annual_authority_review": not mathematical_mismatches,
        "annual_execution_authorized": False,
        "timing_resolution_note": (
            "Historical vectorized extraction and audit are retained only as the "
            "combined post-solve-to-archive interval; they are not imputed."
        ),
        "reference_ladder_result_sha256": REFERENCE_LADDER_RESULT_SHA256,
        "comparisons": comparisons,
        "analysis_context": {
            "git_commit": _git("rev-parse", "HEAD"),
            "git_clean": _git("status", "--porcelain") == "",
            "analysis_source_fingerprint": analysis_source_fingerprint(),
            "platform": platform.platform(),
        },
    }
    atomic_immutable_json(directory / "profile-analysis.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=PROFILE_OUTPUT_DIRECTORY)
    parser.add_argument(
        "--reference-directory", type=Path, default=PREFIX_LADDER_OUTPUT_DIRECTORY
    )
    args = parser.parse_args()
    print(
        json.dumps(
            analyze_profile(args.directory, args.reference_directory),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
