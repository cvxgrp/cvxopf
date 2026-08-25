from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.case118_annual_hierarchy import s3_analysis


def _record(invocation: int, before: int, after: int) -> dict[str, object]:
    return {
        "invocation": invocation,
        "completed_before": before,
        "completed_after": after,
        "classification": "study_complete" if after == 720 else "planned_recycle",
        "context_matches": True,
    }


def _write_registry(directory: Path) -> None:
    boundaries = list(range(0, 721, 16))
    for invocation, (before, after) in enumerate(
        zip(boundaries, boundaries[1:], strict=False)
    ):
        (directory / f"supervision-{invocation:03d}.json").write_text(
            json.dumps(_record(invocation, before, after))
        )


def test_supervision_registry_requires_exact_global_schedule(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    records = s3_analysis._supervision_records(tmp_path)

    assert len(records) == 45
    assert records[0]["completed_after"] == 16
    assert records[-1]["completed_after"] == 720


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("invocation", 9, "invocation sequence"),
        ("completed_before", 1, "global-boundary sequence"),
        ("completed_after", 17, "global-boundary sequence"),
        ("classification", "rss_limit", "classification sequence"),
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
