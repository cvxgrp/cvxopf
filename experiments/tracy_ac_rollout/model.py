"""Read-only retained cohort admission and prepared window configuration."""
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from cvxopf._ac_rollout import RetainedEnergyPlan, RolloutCursor, request_window
from cvxopf._prepared_rollout import physical_snapshot, window_kwargs
from cvxopf.hierarchical import LayerSolveConfig
from experiments.case118_tracy_2021 import e3_prepared, e3_plus
from experiments.case118_tracy_2021.stage_b import TOLERANCES
from experiments.case118_tracy_2021.stage_d import read
from experiments.numerical_preparation.audit import serializable
from experiments.numerical_preparation.fixture import mathematical_inputs
from experiments.tracy_battery_transfer import model as transfer

HERE = Path(__file__).resolve().parent
ROOT = transfer.ROOT
SOURCES = ("singlenode_dc", "lossy_dc", "socp", "ac")
PHASES = ("component_first", "hourly")


def retained_transfers():
    """Original fixed-power outcomes, hash/supervision checked, not new audits.

    These remain explicitly historical comparators. Their original acceptance
    is not repurposed as E4 controlling evidence or as a rollout source filter.
    """
    root = ROOT / "experiments/tracy_battery_transfer/results/transfer_001"
    from experiments.numerical_preparation.run_qualification import resource_evidence
    binding = read(root / "binding.json")
    rows, sources, used = [], {}, 0.
    for frozen in binding["rows"]:
        number = frozen["job"]["id"]
        directory = root / f"call-{number:03d}"
        supervision = read(directory / "supervision.json")
        resources = resource_evidence(directory, supervision)
        for name in ("launch.json", "resources.jsonl", "worker.log"):
            path = directory / name
            if path.exists():
                sources[str(path.relative_to(ROOT))] = e3_prepared.digest(path)
        used += supervision["wall_seconds"]
        record = None
        completion_path = directory / "completion.json"
        if completion_path.exists():
            completion = read(completion_path)
            for name, digest in completion["artifacts"].items():
                if Path(name).name != name or e3_prepared.digest(directory / name) != digest:
                    raise ValueError("historical fixed-transfer artifact/hash mismatch")
                sources[str((directory / name).relative_to(ROOT))] = digest
            record = read(directory / "result.json.gz")
            if record["row"] != frozen or record["classification"] != completion["classification"]:
                raise ValueError("historical fixed-transfer source/classification mismatch")
            sources[str(completion_path.relative_to(ROOT))] = e3_prepared.digest(completion_path)
        sources[str((directory / "supervision.json").relative_to(ROOT))] = e3_prepared.digest(directory / "supervision.json")
        compatible = bool(record is not None and record["classification"] == "compatible"
            and resources
            and record["native"]["status"] == 0 and record["checks"]["passed"]
            and supervision["classification"] == "exited" and supervision["returncode"] == 0
            and supervision.get("ended_utc") and supervision["wall_seconds"] <= record["request"]["wall_seconds"]
            and used <= transfer.LIMITS["total_worker_seconds"])
        rows.append(dict(scenario=frozen["job"]["scenario"], donor=frozen["job"]["donor"],
            retained_classification=record["classification"] if record else supervision["classification"],
            retained_compatible=compatible, result=None if not compatible else record["result"],
            named_costs=None if not compatible else record["named_costs"]))
    return rows, sources


@dataclass(frozen=True)
class PreparedRolloutConfig:
    window_steps: int = 3
    primary_workers: int = 2
    helper_workers: int = 1
    phases: tuple[str, ...] = PHASES

    def __post_init__(self):
        if (self.window_steps, self.primary_workers, self.helper_workers, self.phases) != (3, 2, 1, PHASES):
            raise ValueError("rollout window/concurrency/phase policy is frozen")

    @property
    def ac(self):
        return LayerSolveConfig("IPOPT", dict(e3_prepared.DEFAULT_PROTOCOL["ac_options"]))


CONFIG = PreparedRolloutConfig()


def original_kwargs(record, prepared):
    cohort = e3_plus if record["cohort"] == "prepared_plus" else e3_prepared
    kwargs = cohort.kwargs_for_arm(prepared, record["arm"], record["input_identities"]["storage"],
                                  assembly="component_first" if record["arm"]["formulation"] == "ac" else "hourly")
    if e3_prepared.physical_input_digest(kwargs) != record["mathematical_input_sha256"]:
        raise ValueError("reconstructed physical inputs differ from retained source")
    return kwargs


def neutral_inputs(kwargs):
    value = mathematical_inputs(kwargs)
    value.pop("formulation")
    return value


def inventory():
    """Check finalized manifests, original labels, physical data and dispositions.

    The historical reader checks every completion artifact and supervision hash.
    No historical execution-context equality against current HEAD is required:
    this is retained-data admission, not permission to execute historical code.
    """
    data, prepared = transfer.source_inventory(), e3_prepared.verified_inputs()
    qualified_binding = ROOT / "experiments/tracy_battery_transfer/results/transfer_001/binding.json"
    disposition = read(qualified_binding)
    result = []
    for scenario, case in data["cases"].items():
        reference = case["ac"]
        physical = original_kwargs(reference, prepared)
        snapshot = physical_snapshot(physical)
        ids = snapshot.storage_device_ids
        initial = [s.initial_soc for s in snapshot.storage]
        final = [s.terminal_soc for s in snapshot.storage]
        for source in SOURCES:
            record = case[source]
            kwargs = original_kwargs(record, prepared)
            if neutral_inputs(kwargs) != neutral_inputs(physical):
                raise ValueError("source and AC physical fixture do not match")
            source_ref = transfer.source_reference(data, record)
            flags = tuple(k for k, v in record["audit"].get("residuals", {}).items()
                          if not np.isfinite(v) or v > record["audit"]["limits"][k])
            approved = record["classification"] == "accepted"
            if not approved and (record["cohort"] != "prepared_plus" or source == "ac"
                    or not any(row["donor"] == source_ref for row in disposition["rows"])):
                raise ValueError("numerically flagged source lacks its specific owner-approved use record")
            receipt = (transfer.source_path(data, record).parent / "completion.json"
                       if approved else qualified_binding)
            plan = RetainedEnergyPlan(plan_id=f"retained-{len(result):03d}", source_formulation=source,
                source_archive_sha256=source_ref["sha256"], source_commit=transfer.SOURCE_COMMITS[record["cohort"]],
                source_classification=record["classification"],
                source_use_disposition="accepted" if approved else "owner_approved_qualified",
                source_use_record_sha256=e3_prepared.digest(receipt), qualification_flags=flags,
                physical_input_sha256=transfer.object_hash(neutral_inputs(physical)),
                global_start=record["arm"]["start"], interval_count=24, delta_hours=snapshot.delta,
                storage_device_ids=tuple(record["input_identities"]["storage"]),
                boundary_soc_mwh=record["boundary_soc_mwh"])
            original_ids = plan.storage_device_ids
            plan = plan.aligned(ids, [s.capacity for s in snapshot.storage], initial, final,
                box_tolerance=TOLERANCES["box"], endpoint_tolerance=TOLERANCES["endpoint_mwh"])
            if record["arm"]["stop"] - record["arm"]["start"] != 24:
                raise ValueError("source coverage changed")
            result.append(dict(arm_id=f"arm-{len(result):03d}", scenario=scenario,
                source="ac_reference" if source == "ac" else source, plan=serializable(asdict(plan)),
                source_reference=source_ref, source_original_storage_ids=list(original_ids),
                receiving_column_order=[original_ids.index(x) for x in ids],
                initial_soc_mwh=initial, final_soc_mwh=final, reference=transfer.source_reference(data, reference)))
    if len(result) != 24:
        raise ValueError("expected six scenarios x four retained sources")
    sources = {str((ROOT / "experiments/case118_tracy_2021/results" / name).relative_to(ROOT)): digest
               for name, digest in data["hashes"].items()}
    for run in data["runs"]:
        for name in ("binding.json", "progress.json"):
            p = run["directory"] / name
            sources[str(p.relative_to(ROOT))] = e3_prepared.digest(p)
    sources[str(qualified_binding.relative_to(ROOT))] = e3_prepared.digest(qualified_binding)
    comparisons, comparison_sources = retained_transfers()
    sources.update(comparison_sources)
    comparison_manifest = [{k: v for k, v in row.items() if k not in {"result", "named_costs"}} for row in comparisons]
    return serializable(dict(schema=1, config=asdict(CONFIG), arms=result, sources=sources,
                             fixed_transfer_outcomes=comparison_manifest)), data, prepared


def plan_for(row):
    return RetainedEnergyPlan(**row["plan"])


def snapshot_for(row, data, prepared):
    return physical_snapshot(original_kwargs(data["cases"][row["scenario"]]["ac"], prepared))


def cursor_for(row, checkpoint=None):
    return RolloutCursor(0, plan_for(row).storage_device_ids, row["initial_soc_mwh"]) if checkpoint is None else RolloutCursor(
        checkpoint["completed_intervals"], tuple(checkpoint["storage_device_ids"]),
        checkpoint["realized_soc_mwh"], checkpoint["preceding_controlling_attempt_id"])


def window_for(row, cursor):
    return request_window(plan_for(row), cursor, row["final_soc_mwh"], CONFIG.window_steps, arm_id=row["arm_id"])


def construct(row, cursor, data, prepared, *, hard_target=True, phase="component_first"):
    if phase not in PHASES:
        raise ValueError("unknown AC objective-assembly phase")
    snapshot = snapshot_for(row, data, prepared)
    request = window_for(row, cursor)
    build, kwargs = window_kwargs(snapshot, request, hard_target=hard_target,
                                  preparation=e3_prepared.policy("ac", phase))
    return build, kwargs, snapshot, request
