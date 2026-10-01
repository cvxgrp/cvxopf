"""Derive Tracy boundaries from the accepted lossy-DC trajectory; no AC work."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.case118_annual_hierarchy import s4b_manifest as rule
from .annual_results import HERE, load_results, sha


def derive(data):
    storage = data["tables"]["batteries"]
    record = data["runs"]["lossy_dc"]
    ids = storage.device_id.tolist()
    assert record["identities"]["storage"] == ids
    soc = record["boundary_soc_mwh"]
    power = record["result"]["b"]
    np.testing.assert_array_equal(soc[1:], record["result"]["soc"])
    capacities = storage.capacity_mwh.to_numpy()
    ratings = storage.active_power_limit_mw.to_numpy()
    boundaries, rounds = rule.derive_boundary_rounds(
        soc, power, capacities, ratings, storage_device_count=len(ids)
    )
    boundary_registry = [
        dict(
            index=t,
            timestamp=(
                pd.Timestamp("2021-01-01T00:00:00-08:00") + pd.Timedelta(hours=t)
            ).isoformat(),
            soc_mwh=soc[t].tolist(),
        )
        for t in boundaries
    ]
    # Full source vectors remain in the hash-bound annual archive. Retain all
    # scalar candidate scores/participants plus selected states, not a second
    # copy of the raw annual trajectory.
    omitted = {
        "boundary_soc_mwh",
        "preceding_storage_power_mw",
        "local_peak_absolute_power_mw",
    }
    compact_rounds = [
        {
            **r,
            "candidates": [
                {k: v for k, v in c.items() if k not in omitted}
                for c in r["candidates"]
            ],
        }
        for r in rounds
    ]
    specification = dict(
        horizon=8760,
        nominal_hours=730,
        minimum_ordinary_hours=672,
        maximum_ordinary_hours=792,
        participation_neighborhood="[max(0,t-3), min(8760,t+3))",
        participation_threshold="max(1e-6 MW, 0.001 * device power rating)",
        charging="sum(max(-b[t-1,i]/P[i],0)) >= 0.001",
        score="max(abs(soc[t,i]/E[i]-0.5)) over participating devices",
        ordering=[
            "smallest midpoint deviation",
            "largest normalized charging",
            "smallest distance from previous+730",
            "earliest boundary",
        ],
        final_truncation="append 8760 when remaining horizon <=792; may be shorter than 672",
        no_eligible_candidate="fail; no fallback",
    )
    payload = dict(
        schema_version=1,
        classification="derived_boundaries_not_ac_execution_authority",
        execution_commit=data["binding"]["context"]["commit"],
        source_hashes=data["hashes"],
        rule=specification,
        rule_sha256=rule.object_sha256(specification),
        implementation_sha256=sha(Path(__file__)),
        shared_rule_implementation_sha256=sha(Path(rule.__file__)),
        storage_device_ids=ids,
        capacities_mwh=capacities.tolist(),
        power_ratings_mw=ratings.tolist(),
        signpost_sha256=rule.object_sha256(
            dict(
                storage_device_ids=ids,
                boundary_indices=list(range(8761)),
                soc_mwh=soc.tolist(),
            )
        ),
        boundaries=boundary_registry,
        rounds=compact_rounds,
        shards=[
            dict(
                shard_id=f"shard-{j:03d}",
                start=a,
                stop=b,
                hours=b - a,
                initial_soc_mwh=soc[a].tolist(),
                terminal_soc_mwh=soc[b].tolist(),
            )
            for j, (a, b) in enumerate(zip(boundaries[:-1], boundaries[1:]))
        ],
        ac_execution_authorized=False,
        note="Boundary calculation only. AC policy, budgets, workers and qualification remain separately gated.",
    )
    return dict(manifest_sha256=rule.object_sha256(payload), manifest=payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "SHARD_BOUNDARIES.json")
    args = parser.parse_args()
    envelope = derive(load_results())
    with args.output.open("xb") as stream:
        stream.write(rule.canonical_json(envelope))
    p = envelope["manifest"]
    print("Boundaries:", [b["index"] for b in p["boundaries"]])
    print("Lengths:", [s["hours"] for s in p["shards"]])
    for r in p["rounds"]:
        c = next(
            c for c in r["candidates"] if c["global_boundary"] == r["selected_boundary"]
        )
        print(
            c["global_boundary"],
            "midpoint deviation",
            c["midpoint_deviation"],
            "charging",
            c["normalized_charging"],
            "participants",
            sum(c["participating_devices"]),
        )


if __name__ == "__main__":
    main()
