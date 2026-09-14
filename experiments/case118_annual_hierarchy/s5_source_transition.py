"""One reviewed S5 validation-only source transition, not a migration framework.

The immutable transition preserves literal stopping checkpoints and attributes
their window prefixes to the old execution. Later windows use the new context.
No tolerance, physical state, or original artifact is rewritten by publication.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json

TEMPLATE_PATH = (
    ROOT / "experiments/case118_annual_hierarchy/S5_SOURCE_VERSION_CONTINUATION.json"
)
TEMPLATE_SHA256 = "c6362a88041282cc1b477d0ff0c8d8fd9ec9d7bb56d9d7faa1111b4ffeac8b94"
RECORD_NAME = "source-version-transition.json"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("S5 transition requires an object")
    return cast(dict[str, Any], value)


def validate_contract(value: object) -> dict[str, Any]:
    """Permit only the reviewed stopping point and an explicit successor binding."""
    contract = _object(value)
    raw = TEMPLATE_PATH.read_bytes()
    if _digest(raw) != TEMPLATE_SHA256:
        raise ValueError("S5 transition template identity mismatch")
    template = _object(json.loads(raw))
    mutable = {
        "continuation_execution",
        "launch_authorized",
        "review_status",
        "classification",
    }
    if {k: v for k, v in contract.items() if k not in mutable} != {
        k: v for k, v in template.items() if k not in mutable
    }:
        raise ValueError("S5 transition changed the frozen stopping contract")
    if (
        contract.get("launch_authorized") is not True
        or contract.get("review_status") != "reviewed"
        or contract.get("classification") != "reviewed_s5_source_version_continuation"
    ):
        raise ValueError("S5 source transition is not reviewed and authorized")
    target = _object(contract["continuation_execution"])
    context = _object(target.get("context"))
    prior = template["prior_execution"]["context"]
    identity = {"git_commit", "source_fingerprint"}
    if (
        {k: v for k, v in context.items() if k not in identity}
        != {k: v for k, v in prior.items() if k not in identity}
        or target.get("required_clean_execution_commit") != context.get("git_commit")
        or target.get("required_execution_source_fingerprint")
        != context.get("source_fingerprint")
        or target.get("commit_must_match_exactly") is not True
        or target.get("descendant_commits_implicitly_allowed") is not False
    ):
        raise ValueError("S5 transition execution/scientific context mismatch")
    for field, length in (("git_commit", 40), ("source_fingerprint", 64)):
        item = context.get(field)
        if (
            not isinstance(item, str)
            or len(item) != length
            or any(c not in "0123456789abcdef" for c in item)
        ):
            raise ValueError("S5 transition needs an exact execution identity")
    return contract


def _read_ref(ref: Mapping[str, Any], data: bytes | None = None) -> bytes:
    raw = (ROOT / str(ref["path"])).read_bytes() if data is None else data
    if len(raw) != ref["bytes"] or _digest(raw) != ref["sha256"]:
        raise ValueError(f"S5 stopping artifact mismatch: {ref['path']}")
    return raw


def validate_record(value: object, output_root: Path) -> dict[str, Any]:
    """Verify immutable old evidence, including snapshots of advanced pointers."""
    record = _object(value)
    contract = validate_contract(record.get("contract"))
    if record.get("schema_version") != 1 or record.get(
        "contract_sha256"
    ) != object_sha256(contract):
        raise ValueError("S5 transition record identity mismatch")
    published = record.get("published_utc")
    if (
        not isinstance(published, str)
        or datetime.fromisoformat(published).utcoffset() is None
    ):
        raise ValueError("S5 transition lacks its publication timestamp")
    snapshots = _object(record.get("stopping_pointer_json"))
    expected: dict[str, Any] = {}
    for ref in contract["trusted_stopping_point"]["root_evidence"]:
        if Path(ref["path"]).name == "progress.json":
            expected["progress.json"] = ref
        else:
            # Immutable originals stay in place; never manufacture old evidence.
            path = output_root / Path(ref["path"]).name
            _read_ref(ref, path.read_bytes())
    for shard in contract["trusted_stopping_point"]["shards"]:
        name = f"shard-{int(shard['shard_id'][-3:]):03d}/checkpoint.json"
        expected[name] = shard["checkpoint"]
    if set(snapshots) != set(expected):
        raise ValueError("S5 transition stopping snapshot registry mismatch")
    for name, ref in expected.items():
        if not isinstance(snapshots[name], str):
            raise ValueError("S5 stopping snapshot must retain literal JSON bytes")
        _read_ref(ref, snapshots[name].encode())
    old_authority = contract["prior_execution"]["numerical_authority"]
    authority_json = record.get("prior_authority_json")
    if not isinstance(authority_json, str):
        raise ValueError("S5 transition lacks literal prior authority evidence")
    if (
        json.loads(_read_ref(old_authority, authority_json.encode()))
        != old_authority["payload"]
    ):
        raise ValueError("S5 prior authority payload mismatch")
    if record.get("prior_authority") != old_authority["payload"]:
        raise ValueError("S5 transition prior authority mismatch")
    new_authority = _object(record.get("new_authority"))
    new_context = contract["continuation_execution"]["context"]
    expected_authority = {
        **old_authority["payload"],
        "execution_commit": new_context["git_commit"],
        "source_fingerprint": new_context["source_fingerprint"],
        "source_version_contract_sha256": object_sha256(contract),
    }
    if new_authority != expected_authority:
        raise ValueError("S5 transition successor authority mismatch")
    return record


def load_base_transition(output_root: Path) -> dict[str, Any] | None:
    """Load the original validation-cost source transition."""
    path = output_root / RECORD_NAME
    return (
        validate_record(json.loads(path.read_text()), output_root)
        if path.is_file()
        else None
    )


def load_transition(output_root: Path) -> dict[str, Any] | None:
    """Load the latest reviewed transition across the retained execution chain."""
    base = load_base_transition(output_root)
    if base is None:
        return None
    from experiments.case118_annual_hierarchy.s5_operator_intervention import (
        load_record,
    )

    intervention = load_record(output_root, predecessor_transition=base)
    prior = base if intervention is None else intervention
    from experiments.case118_annual_hierarchy.s5_retry_transition import (
        AUDIT_SPEC,
        load_record as load_retry_record,
    )

    retry = load_retry_record(output_root, predecessor_transition=prior)
    if retry is None:
        return prior
    audit = load_retry_record(
        output_root, predecessor_transition=retry, spec=AUDIT_SPEC
    )
    latest = retry if audit is None else audit
    from experiments.case118_annual_hierarchy.s5_speculative_continuation import (
        load_record as load_speculative_record,
    )

    speculative = load_speculative_record(output_root, latest)
    return latest if speculative is None else speculative


def require_current_transition(
    output_root: Path, context: Mapping[str, object], authority: Mapping[str, object]
) -> dict[str, Any] | None:
    """Require the same recorded transition at root, worker, and child entry."""
    record = load_transition(output_root)
    if record is None:
        if authority.get("source_version_contract_sha256") is not None:
            raise ValueError("S5 execution lacks its reviewed source transition")
    elif (
        record["new_authority"] != authority
        or record["contract"]["continuation_execution"]["context"] != context
    ):
        raise ValueError("S5 execution differs from its reviewed source transition")
    return record


def completed_shard_finalization_binding(
    directory: Path,
    checkpoint: Mapping[str, object],
    context: Mapping[str, object],
    transition: Mapping[str, Any] | None,
) -> Mapping[str, object]:
    """Authorize reporting an unchanged completed checkpoint under the audit fix."""
    from experiments.case118_annual_hierarchy.s5_retry_transition import AUDIT_SPEC

    key = f"{directory.name}/checkpoint.json"
    if (
        transition is None
        or transition.get("classification")
        not in {
            AUDIT_SPEC.record_classification,
            "applied_s5_speculative_policy_continuation",
        }
        or context != transition["contract"]["continuation_execution"]["context"]
        or checkpoint.get("complete") is not True
        or key not in transition["stopping_pointer_json"]
        or checkpoint != json.loads(transition["stopping_pointer_json"][key])
    ):
        raise ValueError("S5 completed shard lacks its bound audit-only finalization")
    return {
        "classification": "completed_checkpoint_audit_only",
        "source_version_contract_sha256": transition["contract_sha256"],
        "checkpoint_sha256": _digest(transition["stopping_pointer_json"][key].encode()),
        "original_execution_source_fingerprint": checkpoint[
            "execution_source_fingerprint"
        ],
        "new_intervals_executed": 0,
    }


def worker_source_matches(
    directory: Path,
    worker: Mapping[str, object],
    context: Mapping[str, object],
    transition: Mapping[str, Any] | None,
) -> bool:
    """Distinguish original solve provenance from a later audit-only worker."""
    if worker.get("completed_checkpoint_finalization") is None:
        return worker.get("execution_source_fingerprint") == context.get(
            "source_fingerprint"
        )
    checkpoint = _object(json.loads((directory / "checkpoint.json").read_text()))
    binding = _object(worker["completed_checkpoint_finalization"])
    # A later continuation does not re-authorize or rewrite an already finalized
    # historical worker. Locate the retained transition that actually issued its
    # binding, then apply the same exact context/checkpoint checks as before.
    issuer = transition
    while issuer is not None:
        if issuer.get("contract_sha256") == binding.get(
            "source_version_contract_sha256"
        ):
            break
        predecessor = issuer.get("predecessor_transition")
        issuer = (
            cast(Mapping[str, Any], predecessor)
            if isinstance(predecessor, Mapping)
            else None
        )
    expected = completed_shard_finalization_binding(
        directory, checkpoint, context, issuer
    )
    return (
        worker["completed_checkpoint_finalization"] == expected
        and worker.get("execution_source_fingerprint")
        == checkpoint["execution_source_fingerprint"]
    )


def publish_transition(
    output_root: Path,
    contract_path: Path,
    context: Mapping[str, object],
    authority: Mapping[str, object],
) -> dict[str, Any]:
    """Fully validate the old prefix, then publish one immutable transaction."""
    raw_contract = json.loads(contract_path.read_text())
    from experiments.case118_annual_hierarchy.s5_retry_transition import (
        AUDIT_SPEC,
        CONTRACT_CLASSIFICATION,
        load_record as load_retry_record,
        publish_transition as publish_retry_transition,
    )

    if isinstance(raw_contract, Mapping) and raw_contract.get("classification") in {
        CONTRACT_CLASSIFICATION,
        AUDIT_SPEC.contract_classification,
    }:
        base = load_base_transition(output_root)
        if base is None:
            raise ValueError("S5 retry continuation lacks its base transition")
        from experiments.case118_annual_hierarchy.s5_operator_intervention import (
            load_record as load_intervention_record,
        )

        intervention = load_intervention_record(
            output_root, predecessor_transition=base
        )
        predecessor = base if intervention is None else intervention
        spec = None
        if raw_contract["classification"] == AUDIT_SPEC.contract_classification:
            retry = load_retry_record(output_root, predecessor_transition=predecessor)
            if retry is None:
                raise ValueError("S5 audit continuation lacks its retry predecessor")
            predecessor = retry
            spec = AUDIT_SPEC
        return publish_retry_transition(
            output_root,
            contract_path,
            context,
            authority,
            predecessor_transition=predecessor,
            spec=spec,
        )
    contract = validate_contract(raw_contract)
    if contract["continuation_execution"]["context"] != context:
        raise ValueError("S5 source transition does not bind this clean execution")
    existing = load_base_transition(output_root)
    if existing is not None:
        if existing["contract"] != contract or existing["new_authority"] != authority:
            raise ValueError("S5 existing transition differs from reviewed request")
        return existing
    refs = contract["trusted_stopping_point"]["root_evidence"]
    for ref in refs:
        _read_ref(ref, (output_root / Path(ref["path"]).name).read_bytes())
    snapshots = {"progress.json": (output_root / "progress.json").read_text()}
    # Lazy imports keep this provenance helper outside model-module cycles.
    from experiments.case118_annual_hierarchy.run_s4b import _outer
    from experiments.case118_annual_hierarchy.s4b_execution import (
        shard_entry,
        verify_shard_artifacts,
    )
    from experiments.case118_annual_hierarchy.s5_execution import annual_registry

    outer = _outer()
    for item in contract["trusted_stopping_point"]["shards"]:
        directory = output_root / f"shard-{int(item['shard_id'][-3:]):03d}"
        raw = _read_ref(
            item["checkpoint"], (directory / "checkpoint.json").read_bytes()
        )
        snapshots[f"{directory.name}/checkpoint.json"] = raw.decode()
        verify_shard_artifacts(
            directory,
            shard=shard_entry(item["shard_id"])[1],
            outer=outer,
            expected_execution_registry_sha256=str(
                annual_registry()["registry_sha256"]
            ),
            allowed_execution_modes=("annual",),
        )
    record = {
        "schema_version": 1,
        "published_utc": datetime.now(timezone.utc).isoformat(),
        "contract": contract,
        "contract_sha256": object_sha256(contract),
        "prior_authority": contract["prior_execution"]["numerical_authority"][
            "payload"
        ],
        "prior_authority_json": _read_ref(
            contract["prior_execution"]["numerical_authority"]
        ).decode(),
        "new_authority": dict(authority),
        "stopping_pointer_json": snapshots,
    }
    validate_record(record, output_root)
    atomic_immutable_json(output_root / RECORD_NAME, record)
    return record


def verify_checkpoint_segment(
    directory: Path,
    checkpoint: Mapping[str, object],
    *,
    context: Mapping[str, object] | None = None,
) -> bool:
    """Retain the exact old prefix, while attributing newly appended windows."""
    record = load_transition(directory.parent)
    if record is None:
        return False
    contract = record["contract"]
    new_context = contract["continuation_execution"]["context"]
    if context is not None and context != new_context:
        raise ValueError("S5 worker context differs from reviewed source transition")
    key = f"{directory.name}/checkpoint.json"
    snapshots = record["stopping_pointer_json"]
    if key in snapshots:
        old = json.loads(snapshots[key])
        count = old["completed_intervals"]
        windows = cast(list[object], checkpoint["windows"])
        if len(windows) < count or windows[:count] != old["windows"]:
            raise ValueError("S5 source transition changed the trusted window prefix")
        intervention_entry = record.get("intervention_window")
        has_intervention = (
            directory.name == "shard-003" and intervention_entry is not None
        )
        if has_intervention:
            if len(windows) > count and windows[count] != intervention_entry:
                raise ValueError("S5 source transition changed the intervention window")
            count += 1
        if len(windows) == count:
            expected = (
                record.get("post_intervention_checkpoint") if has_intervention else old
            )
            from experiments.case118_annual_hierarchy.s5_speculative_continuation import (
                CLASSIFICATION,
            )

            if record.get("classification") == CLASSIFICATION and not old["complete"]:
                successor = {
                    **cast(Mapping[str, Any], expected),
                    "execution_source_fingerprint": new_context["source_fingerprint"],
                }
                if checkpoint == successor:
                    return True
            if checkpoint != expected:
                raise ValueError("S5 source transition changed the stopping checkpoint")
            return True
    if checkpoint["execution_source_fingerprint"] != new_context["source_fingerprint"]:
        raise ValueError("S5 appended windows lack successor source provenance")
    return True


def historical_provenance_matches(
    record: Mapping[str, object],
    transition: Mapping[str, Any] | None,
    context: Mapping[str, object],
    authority: Mapping[str, object],
    *,
    output_root: Path,
) -> bool:
    """Allow frozen historical records from the supplied (possibly restored) run."""
    if (
        record.get("execution_context") == context
        and record.get("authority") == authority
    ):
        return True
    if transition is None:
        return False
    contract = transition["contract"]
    prior_context = contract.get("prior_execution_context")
    if prior_context is None:
        prior_context = contract["prior_execution"]["context"]
    if (
        record.get("execution_context") == prior_context
        and record.get("authority") == transition["prior_authority"]
    ):
        old_progress = json.loads(transition["stopping_pointer_json"]["progress.json"])
        # Root progress is copied verbatim into each reviewed transition.
        if record == old_progress:
            return True
        referenced_names = {
            str(item["path"])
            for field in (
                "supervision_records",
                "reviewed_continuations",
                "root_outcomes",
            )
            for item in cast(
                Sequence[Mapping[str, object]], old_progress.get(field, ())
            )
            if isinstance(item.get("path"), str)
        }
        for name in referenced_names:
            path = output_root / name
            if path.is_file() and record == json.loads(path.read_text()):
                return True
        if transition.get("classification") == (
            "applied_s5_interval_2448_operator_intervention"
        ):
            from experiments.case118_annual_hierarchy.s5_operator_intervention import (
                STOPPING_EVIDENCE,
            )

            root_names = {name for name in STOPPING_EVIDENCE if "/" not in name}
        elif isinstance(contract.get("trusted_stopping_evidence"), Mapping):
            root_names = {
                name
                for name in contract["trusted_stopping_evidence"]
                if "/" not in name and name != "progress.json"
            }
        elif (
            transition.get("classification")
            == "applied_s5_speculative_policy_continuation"
        ):
            root_names = set()
        else:
            root_names = {
                Path(ref["path"]).name
                for ref in contract["trusted_stopping_point"]["root_evidence"]
                if Path(ref["path"]).name
                in {"root-outcome-000.json", "supervision-wave-000-000.json"}
            }
        for name in root_names:
            if record == json.loads((output_root / name).read_text()):
                return True
    predecessor = transition.get("predecessor_transition")
    if isinstance(predecessor, Mapping):
        return historical_provenance_matches(
            record,
            cast(Mapping[str, Any], predecessor),
            cast(
                Mapping[str, object],
                predecessor["contract"]["continuation_execution"]["context"],
            ),
            cast(Mapping[str, object], predecessor["new_authority"]),
            output_root=output_root,
        )
    return False


def transition_context_authority_pairs(
    transition: Mapping[str, Any] | None,
) -> tuple[tuple[Mapping[str, object], Mapping[str, object]], ...]:
    """Return every explicitly bound execution segment, newest first."""
    pairs: list[tuple[Mapping[str, object], Mapping[str, object]]] = []
    current = transition
    while current is not None:
        contract = current["contract"]
        pairs.append(
            (
                cast(
                    Mapping[str, object],
                    contract["continuation_execution"]["context"],
                ),
                cast(Mapping[str, object], current["new_authority"]),
            )
        )
        predecessor = current.get("predecessor_transition")
        if isinstance(predecessor, Mapping):
            current = cast(Mapping[str, Any], predecessor)
        else:
            prior = contract.get("prior_execution")
            if isinstance(prior, Mapping):
                pairs.append(
                    (
                        cast(Mapping[str, object], prior["context"]),
                        cast(Mapping[str, object], current["prior_authority"]),
                    )
                )
            current = None
    return tuple(pairs)


__all__ = [
    "RECORD_NAME",
    "historical_provenance_matches",
    "load_base_transition",
    "load_transition",
    "publish_transition",
    "require_current_transition",
    "transition_context_authority_pairs",
    "validate_contract",
    "validate_record",
    "verify_checkpoint_segment",
]
