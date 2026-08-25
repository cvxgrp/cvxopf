from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.case118_annual_hierarchy import s3_analysis


def _record(
    invocation: int,
    before: int,
    after: int,
    *,
    classification: str | None = None,
) -> dict[str, object]:
    context = {"source_fingerprint": "frozen", "git_clean": True}
    return {
        "invocation": invocation,
        "completed_before": before,
        "completed_after": after,
        "classification": classification
        or ("study_complete" if after == 720 else "planned_recycle"),
        "context_matches": True,
        "start_context": context,
        "checkpoint_sha256_before": (
            None if invocation == 0 and before == 0 else f"checkpoint-{before}"
        ),
        "checkpoint_sha256_after": f"checkpoint-{after}",
    }


def _write_registry(directory: Path) -> None:
    boundaries = list(range(0, 721, 16))
    for invocation, (before, after) in enumerate(
        zip(boundaries, boundaries[1:], strict=False)
    ):
        (directory / f"supervision-{invocation:03d}.json").write_text(
            json.dumps(_record(invocation, before, after))
        )


def _write_review(
    directory: Path,
    *,
    next_invocation: int,
    prior_classification: str,
    completed: int,
    prior_record_kind: str = "supervision",
    checkpoint_sha256: str | None = None,
) -> None:
    prior_name = (
        f"supervision-{next_invocation - 1:03d}.json"
        if prior_record_kind == "supervision"
        else f"interrupted-invocation-{next_invocation - 1:03d}.json"
    )
    prior_path = directory / prior_name
    (directory / f"reviewed-continuation-{next_invocation:03d}.json").write_text(
        json.dumps(
            {
                "next_invocation": next_invocation,
                "prior_record_kind": prior_record_kind,
                "prior_invocation": next_invocation - 1,
                "prior_classification": prior_classification,
                "completed_intervals": completed,
                "checkpoint_sha256": checkpoint_sha256
                or f"checkpoint-{completed}",
                "execution_context": {
                    "source_fingerprint": "frozen",
                    "git_clean": True,
                },
                "prior_record_path": prior_name,
                "prior_record_sha256": s3_analysis.sha256_path(prior_path),
            }
        )
    )


def test_supervision_registry_accepts_exact_global_schedule(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    records = s3_analysis._supervision_records(tmp_path)

    assert len(records) == 45
    assert records[0]["checkpoint_sha256_before"] is None
    assert records[0]["completed_after"] == 16
    assert records[-1]["completed_after"] == 720


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("invocation", 9, "invocation sequence"),
        ("completed_before", 1, "global-boundary sequence"),
        ("completed_after", 17, "next global boundary"),
        ("classification", "unexpected", "classification sequence"),
        ("context_matches", False, "provenance mismatch"),
    ],
)
def test_supervision_registry_rejects_drift(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    _write_registry(tmp_path)
    path = tmp_path / "supervision-000.json"
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match=message):
        s3_analysis._supervision_records(tmp_path)


def test_supervision_registry_accepts_completed_reviewed_continuation(
    tmp_path: Path,
) -> None:
    boundaries = [0, 16, 32, 35, *range(48, 721, 16)]
    for invocation, (before, after) in enumerate(
        zip(boundaries, boundaries[1:], strict=False)
    ):
        classification = (
            "rss_limit"
            if (before, after) == (32, 35)
            else "study_complete"
            if after == 720
            else "planned_recycle"
        )
        (tmp_path / f"supervision-{invocation:03d}.json").write_text(
            json.dumps(
                _record(
                    invocation,
                    before,
                    after,
                    classification=classification,
                )
            )
        )
    _write_review(
        tmp_path,
        next_invocation=3,
        prior_classification="rss_limit",
        completed=35,
    )

    records = s3_analysis._supervision_records(tmp_path)

    assert len(records) == 46
    assert records[2]["classification"] == "rss_limit"
    assert records[2]["record_kind"] == "supervision"
    assert records[2]["reviewed_continuation"] is not None
    assert records[3]["completed_before"] == 35
    assert records[-1]["classification"] == "study_complete"
    assert s3_analysis._execution_disposition(records, 720) == (
        True,
        "complete",
        "study_complete",
    )


def test_supervision_registry_accepts_retained_partial_trajectory(
    tmp_path: Path,
) -> None:
    records = [
        _record(0, 0, 16),
        _record(1, 16, 32),
        _record(2, 32, 35, classification="rss_limit"),
    ]
    for invocation, record in enumerate(records):
        (tmp_path / f"supervision-{invocation:03d}.json").write_text(
            json.dumps(record)
        )

    retained = s3_analysis._supervision_records(tmp_path)

    assert retained[-1]["classification"] == "rss_limit"
    assert retained[-1]["completed_after"] == 35
    assert s3_analysis._execution_disposition(retained, 35) == (
        False,
        "partial",
        "rss_limit",
    )


def test_terminal_review_authorization_is_retained_as_pending(
    tmp_path: Path,
) -> None:
    records = [
        _record(0, 0, 16),
        _record(1, 16, 19, classification="worker_failure"),
    ]
    for invocation, record in enumerate(records):
        (tmp_path / f"supervision-{invocation:03d}.json").write_text(
            json.dumps(record)
        )
    _write_review(
        tmp_path,
        next_invocation=2,
        prior_classification="worker_failure",
        completed=19,
    )

    retained = s3_analysis._supervision_records(tmp_path)

    authorization = retained[-1]["reviewed_continuation"]
    assert isinstance(authorization, dict)
    assert authorization["state"] == "pending"
    assert s3_analysis._execution_disposition(retained, 19)[1] == "partial"


def test_continuation_after_abnormal_stop_requires_matching_review(
    tmp_path: Path,
) -> None:
    records = [
        _record(0, 0, 16),
        _record(1, 16, 19, classification="worker_failure"),
        _record(2, 19, 32),
    ]
    for invocation, record in enumerate(records):
        (tmp_path / f"supervision-{invocation:03d}.json").write_text(
            json.dumps(record)
        )

    with pytest.raises(ValueError, match="lacks reviewed continuation"):
        s3_analysis._supervision_records(tmp_path)

    _write_review(
        tmp_path,
        next_invocation=2,
        prior_classification="worker_failure",
        completed=19,
    )
    assert len(s3_analysis._supervision_records(tmp_path)) == 3

    bad = json.loads((tmp_path / "supervision-002.json").read_text())
    bad["completed_after"] = 31
    (tmp_path / "supervision-002.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="next global boundary"):
        s3_analysis._supervision_records(tmp_path)


def test_supervision_registry_represents_reviewed_process_interruption(
    tmp_path: Path,
) -> None:
    (tmp_path / "supervision-000.json").write_text(json.dumps(_record(0, 0, 16)))
    (tmp_path / "interrupted-invocation-001.json").write_text(
        json.dumps(
            _record(1, 16, 19, classification="reviewed_interruption")
        )
    )
    (tmp_path / "supervision-002.json").write_text(json.dumps(_record(2, 19, 32)))
    _write_review(
        tmp_path,
        next_invocation=2,
        prior_classification="reviewed_interruption",
        completed=19,
        prior_record_kind="interrupted_invocation",
    )

    records = s3_analysis._supervision_records(tmp_path)

    assert [record["invocation"] for record in records] == [0, 1, 2]
    assert records[1]["record_kind"] == "interrupted_invocation"
    assert records[1]["classification"] == "reviewed_interruption"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("checkpoint_sha256", "wrong-checkpoint"),
        ("completed_intervals", 18),
        ("prior_classification", "rss_limit"),
        ("prior_record_kind", "supervision"),
        ("prior_invocation", 0),
        ("prior_record_sha256", "wrong-record"),
    ],
)
def test_reviewed_continuation_rejects_wrong_prior_identity(
    tmp_path: Path, field: str, value: object
) -> None:
    (tmp_path / "supervision-000.json").write_text(json.dumps(_record(0, 0, 16)))
    (tmp_path / "interrupted-invocation-001.json").write_text(
        json.dumps(
            _record(1, 16, 19, classification="reviewed_interruption")
        )
    )
    (tmp_path / "supervision-002.json").write_text(json.dumps(_record(2, 19, 32)))
    _write_review(
        tmp_path,
        next_invocation=2,
        prior_classification="reviewed_interruption",
        completed=19,
        prior_record_kind="interrupted_invocation",
    )
    path = tmp_path / "reviewed-continuation-002.json"
    authorization = json.loads(path.read_text())
    authorization[field] = value
    path.write_text(json.dumps(authorization))

    with pytest.raises(ValueError, match="identity mismatch"):
        s3_analysis._supervision_records(tmp_path)


def test_supervision_registry_rejects_later_missing_starting_checkpoint(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)
    path = tmp_path / "supervision-001.json"
    record = json.loads(path.read_text())
    record["checkpoint_sha256_before"] = None
    path.write_text(json.dumps(record))

    with pytest.raises(ValueError, match="starting checkpoint identity"):
        s3_analysis._supervision_records(tmp_path)


def test_supervision_registry_rejects_fabricated_initial_checkpoint(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)
    path = tmp_path / "supervision-000.json"
    record = json.loads(path.read_text())
    record["checkpoint_sha256_before"] = "checkpoint-0"
    path.write_text(json.dumps(record))

    with pytest.raises(ValueError, match="unexpected starting checkpoint"):
        s3_analysis._supervision_records(tmp_path)


def test_supervision_registry_rejects_discontinuous_checkpoint_chain(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)
    path = tmp_path / "supervision-001.json"
    record = json.loads(path.read_text())
    record["checkpoint_sha256_before"] = "different"
    path.write_text(json.dumps(record))

    with pytest.raises(ValueError, match="checkpoint chain mismatch"):
        s3_analysis._supervision_records(tmp_path)


def test_supervision_registry_rejects_orphan_review_authorization(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)
    (tmp_path / "reviewed-continuation-045.json").write_text("{}")

    with pytest.raises(ValueError, match="orphan record"):
        s3_analysis._supervision_records(tmp_path)


def test_partial_result_cannot_be_promoted(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="partial S3 analysis"):
        s3_analysis._promote_completed_result(
            tmp_path / "S3_RESULTS.json", {"execution_complete": False}
        )
    assert not (tmp_path / "S3_RESULTS.json").exists()


def test_completed_result_is_promoted_immutably(tmp_path: Path) -> None:
    destination = tmp_path / "S3_RESULTS.json"
    result = {"execution_complete": True, "completed_intervals": 720}
    s3_analysis._promote_completed_result(destination, result)

    assert json.loads(destination.read_text()) == result
    with pytest.raises(FileExistsError):
        s3_analysis._promote_completed_result(
            destination,
            {"execution_complete": True, "completed_intervals": 719},
        )
