# Tracy annual shard boundaries

The owner approved using the accepted Stage C **lossy-DC** SoC trajectory to
calculate shard boundaries. This is a partition and state calculation only;
it does not authorize AC solves, freeze an AC recovery policy, or establish
that the proposed joins are AC-realizable.

## Rule and source

Apply the reviewed [S4b boundary rule](../case118_annual_hierarchy/S4B_PROTOCOL.md#frozen-annual-boundary-rule)
to Tracy's 27 identified batteries. Reuse the existing selection implementation
with an explicit device count; its four-device default and the earlier study's
rule, manifest and numerical evidence remain unchanged. No old boundary or SoC
is imported into the Tracy partition.

- Nominal length: 730 hours; ordinary candidate lengths: 672–792 inclusive.
- Participation: any power magnitude over the six-hour neighborhood `[t-3,t+3)`
  reaches `max(1e-6 MW, 0.001 * device rating)`.
- Eligibility: at least one participant and normalized charging in interval
  `t-1` of at least 0.001, summing `max(-b/P,0)` across devices.
- Minimize the worst participating-device deviation from 50% SoC, then favor
  larger normalized charging, proximity to the nominal boundary, and earlier
  time, in that exact order.
- Append the year end when the remainder is at most 792 hours. The final shard
  may be shorter than 672 hours. No fallback if no candidate is eligible.

The source is the annual lossy-DC archive at
`results/stage_c/arm-001/result.json.gz`, SHA-256
`f984b690497d60dd983314f168be641063a933b0b1b0c717a94f0341d76e9991`,
from execution commit `034ea6b9e5d9dd4276d6e742847f547c19a1047d`.
The read-only loader verifies the accepted annual pair and offline analysis,
input identities, and archive hashes before derivation.

## Partition

All ranges are half-open global hourly intervals. Timestamps are fixed
UTC−08:00 throughout, with no daylight-saving conversion.

| Shard | Start | Stop | Hours | End timestamp (exclusive) |
|---|---:|---:|---:|---|
| 000 | 0 | 685 | 685 | Jan 29, 13:00 |
| 001 | 685 | 1381 | 696 | Feb 27, 13:00 |
| 002 | 1381 | 2109 | 728 | Mar 29, 21:00 |
| 003 | 2109 | 2782 | 673 | Apr 26, 22:00 |
| 004 | 2782 | 3521 | 739 | May 27, 17:00 |
| 005 | 3521 | 4294 | 773 | Jun 28, 22:00 |
| 006 | 4294 | 4988 | 694 | Jul 27, 20:00 |
| 007 | 4988 | 5727 | 739 | Aug 27, 15:00 |
| 008 | 5727 | 6491 | 764 | Sep 28, 11:00 |
| 009 | 6491 | 7283 | 792 | Oct 31, 11:00 |
| 010 | 7283 | 7956 | 673 | Nov 28, 12:00 |
| 011 | 7956 | 8692 | 736 | Dec 29, 04:00 |
| 012 | 8692 | 8760 | 68 | Jan 1, 2022, 00:00 |

The result is **13 shards**, totaling 8,760 hours without gaps or overlaps.
The **68-hour tail is intentional under the frozen truncation rule**, not a
derivation error; no merge or boundary adjustment was made to force 12 shards.

All 12 selected interior boundaries are eligible. The worst midpoint deviation
among the selected boundaries is **0.22105** at hour 4,988 (July 27), meaning
every locally participating battery there is within **22.11 percentage points**
of 50% SoC. Selected-boundary participant counts range from 20 to 27; at some
boundaries the rule excludes locally stationary devices from scoring but still
retains their exact SoC. Neither the midpoint score nor charging eligibility
is an AC feasibility certificate.

## Retained evidence and reproduction

[`SHARD_BOUNDARIES.json`](SHARD_BOUNDARIES.json) retains all 1,452 candidate
scores, participation flags, selection rounds, the boundary registry, all 27
initial/terminal states for every shard, source/rule/implementation hashes, and
a digest of the complete identity-aligned annual SoC signposts. The envelope
digest covers the payload, not itself. Full raw trajectories remain in the
annual archive rather than being duplicated in the boundary document.

To reproduce into a fresh destination (no numerical solve):

```sh
uv run python -m experiments.case118_tracy_2021.shard_boundaries \
  --output experiments/case118_tracy_2021/results/rederived_boundaries.json
```

Tests independently reconstruct every candidate's participation, charging score,
midpoint score and eligibility from the annual arrays, verify ranking, exact
state joins, and byte-identical rederivation. Without ignored annual archives,
the retained manifest's envelope, ranges, rankings and state joins can still
be checked, but raw-source rederivation is unavailable.

The next scientific gate remains separately approved bounded AC qualification,
including actual shard starts and a representative join. AC runner settings,
worker counts, resource budgets and scheduling are not part of this artifact.
