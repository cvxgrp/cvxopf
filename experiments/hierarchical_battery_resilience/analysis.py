"""Load and validate the persisted M17-S3 manual experiment artifacts."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pandas as pd

from experiments.hierarchical_battery_resilience.reproduce import (
    CONTEXT_FILE,
    DEFAULT_OUTPUT,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json_artifact(path: Path) -> dict:
    """Load one compressed result artifact."""
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def validate_artifacts(output_path: Path = DEFAULT_OUTPUT) -> dict:
    """Verify every artifact against the completed-run metadata."""
    metadata = json.loads((output_path / "metadata.json").read_text())
    context = json.loads((output_path / CONTEXT_FILE).read_text())
    if any(metadata.get(key) != value for key, value in context.items()):
        raise ValueError("S3 run context does not match completed metadata")
    for name, expected in metadata["artifacts"].items():
        path = output_path / name
        if not path.is_file():
            raise ValueError(f"Missing S3 artifact: {name}")
        if path.stat().st_size != expected["bytes"]:
            raise ValueError(f"S3 artifact size mismatch: {name}")
        if _sha256(path) != expected["sha256"]:
            raise ValueError(f"S3 artifact hash mismatch: {name}")
    return metadata


def trajectory_summary(output_path: Path = DEFAULT_OUTPUT) -> pd.DataFrame:
    """Return the verified four-policy trajectory summary."""
    validate_artifacts(output_path)
    return pd.read_csv(output_path / "trajectory_summary.csv")


def endpoint_summary(output_path: Path = DEFAULT_OUTPUT) -> pd.DataFrame:
    """Flatten endpoint solve outcomes and residuals for review."""
    validate_artifacts(output_path)
    payload = load_json_artifact(output_path / "endpoint_realization.json.gz")
    rows = []
    for record in payload["realizations"]:
        attempt = record["attempt"]
        rows.append(
            {
                "case": record["case"]["name"],
                "start": record["case"]["start"],
                "stop": record["case"]["stop"],
                "status": attempt["audit"]["status"],
                "outcome": attempt["audit"]["outcome"],
                "diagnosis": attempt["window_diagnosis"],
                "wall_time_seconds": attempt["audit"]["wall_time_seconds"],
                **attempt["audit"]["residuals"],
            }
        )
    return pd.DataFrame(rows)


def attempt_summary(
    outer_policy: str,
    inner_policy: str,
    output_path: Path = DEFAULT_OUTPUT,
) -> pd.DataFrame:
    """Flatten retained AC attempt outcomes for one sequential study."""
    validate_artifacts(output_path)
    path = output_path / f"{outer_policy}__{inner_policy}.json.gz"
    payload = load_json_artifact(path)
    return pd.DataFrame(
        {
            "attempt_id": attempt["attempt_id"],
            "kind": attempt["attempt_kind"],
            "iteration": attempt["iteration"],
            "status": attempt["audit"]["status"],
            "outcome": attempt["audit"]["outcome"],
            "diagnosis": attempt["window_diagnosis"],
            "accepted_primal": attempt["audit"]["accepted_primal"],
            "wall_time_seconds": attempt["audit"]["wall_time_seconds"],
        }
        for attempt in payload["ac_attempts"]
    )
