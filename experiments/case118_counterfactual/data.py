"""Read-only source binding and episode-first extraction; never runs OPF."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.s4b_manifest import (
    load_authoritative_outer,
    load_verified_manifest,
    S4_OUTER_ARCHIVE_SHA256,
    EXPECTED_MANIFEST_SHA256,
)
from experiments.case118_annual_hierarchy.streaming_archive import _json_value
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    sha256_path,
)
from .model import MatchedWindow, array

ROOT = Path(__file__).resolve().parents[2]
ANNUAL = ROOT / "experiments/case118_annual_hierarchy"


class ToySource:
    def __init__(self):
        self.fixture = load_s4_fixture()
        self.outer, self.soc, self.battery = load_authoritative_outer()
        self.manifest = load_verified_manifest()["manifest"]
        self.boundaries = self.manifest["boundary_indices"]

    def window(self, start, steps):
        stop = start + steps
        if not isinstance(start, int) or not isinstance(steps, int) or steps <= 0:
            raise ValueError("window indices must be integers and steps positive")
        shard = next(
            (
                j
                for j, (a, b) in enumerate(zip(self.boundaries, self.boundaries[1:]))
                if a <= start < stop <= b
            ),
            None,
        )
        if shard is None:
            raise ValueError("matched window must remain within one source shard")
        inputs = self.fixture.inputs
        r = self.outer["result"]
        window = MatchedWindow(
            start,
            stop,
            self.outer["input_fingerprint"],
            array(
                np.asarray(r["Pg"])[start:stop],
                (steps, len(inputs.generators)),
                "DC Pg",
            ),
            self.battery[start:stop].copy(),
            array(
                np.asarray(r["p_nd"])[start:stop],
                (steps, len(inputs.nondispatchable)),
                "DC ND",
            ),
            self.soc[start : stop + 1].copy(),
            {
                "outer_sha256": S4_OUTER_ARCHIVE_SHA256,
                "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                "shard_ordinal": shard,
                "generator_rows": [asdict(g) for g in inputs.generators],
                "storage_ids": list(inputs.storage_device_ids),
                "renewable_ids": [u.device_id for u in inputs.nondispatchable],
            },
        )
        window.validate(inputs, self.fixture.policy)
        return window


def restore_window(value):
    value = dict(value)
    for name in ("pg_mw", "battery_mw", "renewable_mw", "soc_mwh"):
        value[name] = np.asarray(value[name], dtype=float)
    return MatchedWindow(**value)


def candidate_index():
    """Six-hour sustained scores, within shards; no automatic episode selection."""
    path = ANNUAL / "analysis/artifacts/final_snapshot/dispatch/intervals.csv"
    frame = pd.read_csv(path)
    for column, target in (
        ("generator_counterdirection_mw", "opposing_6h_mw"),
        ("battery_l1_mw", "battery_6h_mw"),
    ):
        frame[target] = frame.groupby("shard")[column].transform(
            lambda x: x.rolling(6, min_periods=6).mean()
        )
    valid = frame[["opposing_6h_mw", "battery_6h_mw"]].notna().all(axis=1)
    frame["generator_rank"] = frame.loc[valid, "opposing_6h_mw"].rank(pct=True)
    frame["battery_rank"] = frame.loc[valid, "battery_6h_mw"].rank(pct=True)
    frame["spatial_score"] = frame.generator_rank - frame.battery_rank
    frame["temporal_score"] = frame.battery_rank
    frame["joint_score"] = frame[["generator_rank", "battery_rank"]].min(axis=1)
    return frame, {
        "path": str(path.relative_to(ROOT)),
        "sha256": sha256_path(path),
        "definition": "trailing six complete hours within one shard; average percentile ranks; no selected episodes",
    }


def extract_context(source, start, stop, output):
    """Retain selected executed first actions and references, not unused horizons.

    Context may cross shard boundaries; explicit initial states preserve resets.
    The caller chooses context before choosing exact counterfactual solve windows.
    """
    if not 0 <= start < stop <= source.fixture.inputs.horizon_steps:
        raise ValueError("invalid context span")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    records, references = [], []
    inputs = source.fixture.inputs
    for ordinal, (a, b) in enumerate(zip(source.boundaries, source.boundaries[1:])):
        if stop <= a or start >= b:
            continue
        directory = ANNUAL / "results/s4b_annual_ac" / f"shard-{ordinal:03d}"
        checkpoint_path = directory / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text())
        if (
            checkpoint["outer_plan_sha256"] != S4_OUTER_ARCHIVE_SHA256
            or checkpoint["manifest_sha256"] != EXPECTED_MANIFEST_SHA256
        ):
            raise ValueError("context checkpoint source mismatch")
        references.append(
            {
                "path": str(checkpoint_path.relative_to(ROOT)),
                "sha256": sha256_path(checkpoint_path),
            }
        )
        entries = {entry["iteration"]: entry for entry in checkpoint["windows"]}
        for i in range(max(start, a), min(stop, b)):
            entry = entries[i]
            path = directory / entry["relative_path"]
            raw = path.read_bytes()
            if (
                len(raw) != entry["bytes"]
                or hashlib.sha256(raw).hexdigest() != entry["sha256"]
            ):
                raise ValueError("context archive hash mismatch")
            w = json.loads(gzip.decompress(raw))
            selected = [x for x in w["attempts"] if x["supplied_executed_action"]]
            if len(selected) != 1 or not selected[0]["audit"]["accepted_primal"]:
                raise ValueError("context requires one accepted controlling action")
            c = selected[0]
            if (
                w["iteration"] != i
                or c["attempt_id"] != w["executed_interval"]["controlling_attempt_id"]
                or w["storage_device_ids"] != list(inputs.storage_device_ids)
            ):
                raise ValueError(
                    "context iteration/controller/device identity mismatch"
                )
            result = c["result"]
            fields = (
                "Pg",
                "Qg",
                "Vm",
                "Va_deg",
                "b",
                "b_q",
                "soc",
                "p_nd",
                "q_nd",
                "branch_p_from",
                "branch_q_from",
                "branch_p_to",
                "branch_q_to",
                "branch_s_from",
                "branch_s_to",
                "p_load",
                "q_load",
                "curtailment",
            )
            values = {
                name: np.asarray(result[name], dtype=float)[0].tolist()
                for name in fields
            }
            if any(
                not np.isfinite(value).all()
                for value in map(np.asarray, values.values())
            ):
                raise ValueError("nonfinite context data")
            records.append(
                {
                    "iteration": i,
                    "timestamp": str(inputs.df_load_p.index[i]),
                    "shard": ordinal,
                    "shard_start": i == a,
                    "initial_soc_mwh": w["initial_soc_mwh"],
                    "ac_first_action": values,
                    "dc": {
                        name: np.asarray(source.outer["result"][name])[i].tolist()
                        for name in ("Pg", "b", "soc", "p_nd", "p_flows")
                    },
                    "dc_initial_soc_mwh": source.soc[i].tolist(),
                    "archive": {
                        "path": str(path.relative_to(ROOT)),
                        "sha256": entry["sha256"],
                    },
                    "controller": c["attempt_id"],
                    "policy": w.get("policy", "legacy"),
                    "operator_intervention": w.get("operator_intervention"),
                }
            )
    if [r["iteration"] for r in records] != list(range(start, stop)):
        raise ValueError("context coverage mismatch")
    atomic_immutable_json(
        output / "context.json",
        _json_value(
            {
                "start": start,
                "stop": stop,
                "kind": "retained_episode_context_not_solve_selection",
                "input_sha256": source.outer["input_fingerprint"],
                "outer_sha256": S4_OUTER_ARCHIVE_SHA256,
                "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                "checkpoints": references,
                "storage": [asdict(s) for s in inputs.storage],
                "generators": [asdict(g) for g in inputs.generators],
                "buses": np.asarray(inputs.case["bus"]),
                "branches": np.asarray(inputs.case["branch"]),
                "records": records,
            }
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=int)
    parser.add_argument("--stop", type=int)
    args = parser.parse_args()
    if args.start is None and args.stop is None:
        args.output.mkdir(parents=True, exist_ok=False)
        frame, provenance = candidate_index()
        frame.to_csv(args.output / "candidates.csv", index=False)
        atomic_immutable_json(args.output / "provenance.json", provenance)
    elif args.start is not None and args.stop is not None:
        extract_context(ToySource(), args.start, args.stop, args.output)
    else:
        parser.error("supply both --start and --stop for episode context")


if __name__ == "__main__":
    main()
