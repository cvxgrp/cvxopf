"""Freeze a stratified random sample of post-cutover primary and helper wins."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import random

from experiments.retained_paths import retained_operation, retained_path

ROOT = Path(__file__).resolve().parents[2]
ANNUAL = ROOT / "experiments/case118_annual_hierarchy"
OUT = ROOT / "experiments/case118_vectorization_replay/results/case118_vectorization_replay"
SEED = 20260920


def read(path):
    return json.loads(retained_path(path).read_text())


def sha(path):
    return hashlib.sha256(retained_path(path).read_bytes()).hexdigest()


def ref(path):
    return dict(path=str(retained_path(path).resolve()), sha256=sha(path))


def checked(reference):
    path = Path(reference["path"])
    if sha(path) != reference["sha256"]:
        raise ValueError(f"Retained artifact changed: {path}")
    return read(path)


def stratify(rows, allocations, rng, field):
    """Disjoint rank strata; population/sample weights undo tail oversampling."""
    ranked = sorted(rows, key=lambda r: (r[field], r["iteration"]))
    selected, strata = [], []
    start = 0
    for end_fraction, count in allocations:
        stop = round(len(ranked) * end_fraction)
        group = ranked[start:stop]
        chosen = rng.sample(group, count)
        label = f"{start / len(ranked):.4f}–{stop / len(ranked):.4f}"
        strata.append(
            dict(
                stratum=label,
                population=len(group),
                sample=count,
                min_seconds=group[0][field],
                max_seconds=group[-1][field],
            )
        )
        selected.extend(
            dict(
                row,
                stratum=label,
                inclusion_probability=count / len(group),
                population_weight=len(group) / count,
            )
            for row in chosen
        )
        start = stop
    return selected, strata


@retained_operation()
def prepare():
    OUT.mkdir(parents=True, exist_ok=False)
    dispatch_path = ANNUAL / "analysis/artifacts/final_snapshot/dispatch/intervals.csv"
    times_path = ANNUAL / "analysis/artifacts/final_snapshot/solves/solve_times.csv"
    dispatch = list(csv.DictReader(dispatch_path.open()))
    timings = list(csv.DictReader(times_path.open()))
    timing_by_id = {}
    for row in timings:
        if row["regime"] == "speculative":
            directory = ROOT / Path(row["source"]).parent
            timing_by_id[
                (
                    str(directory.parent.name),
                    int(row["iteration"]),
                    int(directory.name.rsplit("-", 1)[1]),
                )
            ] = (row, directory)
    checkpoints = {
        p.parent.name: (p, read(p))
        for p in (ANNUAL / "results/s4b_annual_ac").glob("shard-*/checkpoint.json")
    }
    populations = {"primary": [], "helper": []}
    for row in dispatch:
        controller = row["controller_id"]
        if row["policy"] != "causal_first_speculative_v1" or row[
            "operator_intervention"
        ] not in ("", "False", "false"):
            continue
        shard, _, suffix = controller.split("/")
        order = int(suffix.rsplit("-", 1)[1])
        iteration = int(row["iteration"])
        cp_path, checkpoint = checkpoints[f"shard-{int(shard.rsplit('-', 1)[1]):03d}"]
        if iteration + 3 > checkpoint["interval"]["stop"]:
            continue
        timing, directory = timing_by_id[(shard, iteration, order)]
        if timing["status"] != "returned":
            raise ValueError("Accepted controller lacks returned solve timing")
        primary_dir = directory.with_name(f"ac-{iteration:06d}-spec-00")
        primary_life = read(primary_dir / "lifecycle.json")
        winner_life = read(directory / "lifecycle.json")
        assert winner_life["completion"]["outcome"] == "accepted"
        group = "primary" if order == 0 else "helper"
        populations[group].append(
            dict(
                iteration=iteration,
                shard_id=shard,
                historical_group=group,
                historical_winner_order=order,
                historical_solve_seconds=float(timing["minutes"]) * 60,
                historical_window_seconds=winner_life["reaped_monotonic"]
                - primary_life["launched_monotonic"],
                historical_primary_directory=str(primary_dir),
                historical_winner_directory=str(directory),
                checkpoint=str(cp_path),
                archive_sha256=row["archive_sha256"],
            )
        )
    rng = random.Random(SEED)
    allocation = [(i / 10, 11) for i in range(1, 10)] + [(0.95, 7), (0.99, 7), (1.0, 7)]
    primary, pstrata = stratify(
        populations["primary"], allocation, rng, "historical_solve_seconds"
    )
    helper, hstrata = stratify(
        populations["helper"],
        [(i / 6, 1) for i in range(1, 7)],
        rng,
        "historical_window_seconds",
    )
    selected = primary + helper
    rng.shuffle(selected)
    for row in selected:
        cp_path = Path(row.pop("checkpoint"))
        checkpoint = read(cp_path)
        entry = next(
            e for e in checkpoint["windows"] if e["iteration"] == row["iteration"]
        )
        archive_path = cp_path.parent / entry["relative_path"]
        assert sha(archive_path) == entry["sha256"] == row["archive_sha256"]
        window = json.loads(gzip.decompress(archive_path.read_bytes()))
        assert window["interval_stop"] - window["iteration"] == 3
        row["initial_soc_mwh"] = dict(
            zip(window["storage_device_ids"], window["initial_soc_mwh"], strict=True)
        )
        row["target_soc_mwh"] = dict(
            zip(window["storage_device_ids"], window["target_soc_mwh"], strict=True)
        )
        row["trajectory_start"] = checkpoint["interval"]["start"]
        row["trajectory_stop"] = checkpoint["interval"]["stop"]
        row["trajectory_initial_soc_mwh"] = dict(
            zip(
                window["storage_device_ids"], checkpoint["initial_soc_mwh"], strict=True
            )
        )
        references = {}
        for role in ("primary", "winner"):
            directory = Path(row[f"historical_{role}_directory"])
            life = read(directory / "lifecycle.json")
            for name in ("start.json", "request.json", "phase.json", "result.json"):
                path = directory / name
                if path.exists():
                    assert sha(path) == life["artifacts"][name]["sha256"]
                    references[f"{role}_{name}"] = ref(path)
            references[f"{role}_lifecycle.json"] = ref(directory / "lifecycle.json")
        row["references"] = references
        row["archive"] = ref(archive_path)
    manifest = dict(
        created_utc=datetime.now(timezone.utc).isoformat(),
        seed=SEED,
        population_definition="Accepted controlling windows in the completed toy study with policy causal_first_speculative_v1, no operator insertion, and exactly three hours; speculative regime is the post-cutover population.",
        population_counts={k: len(v) for k, v in populations.items()},
        primary_strata=pstrata,
        helper_strata=hstrata,
        sampling="Nine lower deciles: 11 each; p90–95, p95–99, p99–100: 7 each. Six helper winners: one per winner-latency sextile. Random without replacement within strata; shuffled execution order.",
        time_basis="Historical solve phase includes canonicalization, same as S5 helper clock; window seconds are primary launch through winner reaping, not full loser cleanup.",
        sources=[ref(dispatch_path), ref(times_path)]
        + [ref(p) for p, _ in checkpoints.values()],
        selected=selected,
    )
    (OUT / "sample.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in manifest.items() if k not in ("selected", "sources")},
            indent=2,
        )
    )


if __name__ == "__main__":
    prepare()
