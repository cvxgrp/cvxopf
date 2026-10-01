# Stage B spatial-pressure screen

## Finding

The original five selected windows cover the strongest export-pressure events across
the four screened regions, but **regional import-pressure coverage is incomplete**.
The region anchored at bus 87 has its strongest sustained import pressure in
July and its hourly peak in August. Neither event is inside a selected window.
This is an input-only finding, not solved congestion or evidence of infeasibility.
No OPF was constructed or solved by the screen. The owner subsequently approved
the summer addition below, bringing the grid to 72 arms. The tables in this
report retain the original five-window assessment that motivated that decision.

## Method and inputs

The [protocol](STAGE_B_PROTOCOL.md#spatial-pressure-screen) specifies the rule,
written before evaluating the temporal rankings. Four farthest-first graph
seeds begin at bus 1, use active-branch hop distance, and resolve ties by
smallest bus ID. Nearest-seed assignment gives connected regions. Bus numbers
identify regions; they do not represent geography or utility operating areas.
The resulting seeds are **1, 52, 87 and 108** (listed in numeric order).

The screen uses the hash-checked Stage A arrays, device tables, and pinned
PGLib network. It includes distributed solar in renewable availability, uses
approved 5,000 MW aggregate generator capacity and E/3 battery power limits,
and retains all active parallel cut branches separately. Generator minima
are verified zero. No economics or generation dispatch is inferred.

| Region anchor | Buses | Cut branches | Sum of rateA (MVA) | Local generator Pmax (MW) | Battery power (MW) |
|---|---:|---:|---:|---:|---:|
| 1 | 37 | 9 | 2,258 | 1,007.67 | 1,773.18 |
| 52 | 51 | 14 | 3,060 | 2,851.11 | 2,113.08 |
| 87 | 14 | 9 | 1,360 | 496.55 | 515.97 |
| 108 | 16 | 8 | 1,190 | 644.67 | 279.94 |

Each cut may appear in two region records; these are not independent transfer
resources. Ratings normalize MW scores using the same numeric rateA limits
used by the DC branch model. They do not measure AC transfer capability, where
reactive power also consumes apparent-power capacity.

For each region, let L be hourly gross load and R renewable availability.
The main import score below is **max(L − R − G, 0)**, after maximum local
generation but before storage. The export score is **max(R − L − B, 0)**,
after maximum battery charging and with conventional generation off.
Both are potential-transfer indicators with units MW. The retained evidence
also includes pre-flexibility scores and imports after maximum discharge.

Hourly maxima use all 8,760 hours. Sustained pressure uses the maximum mean
over 359 non-wrapping 168-hour windows starting daily at midnight. A selected
window covers a week only when it contains that entire week. All timestamps
retain fixed UTC−08:00. Earliest ties are retained, and positive hours at or
above each annual 99th percentile are counted, including ties. When fewer than
1% of hours are positive, this count includes all positive hours. Overlapping
weeks and shared network cuts are descriptive, not independent samples.

## Import-pressure coverage

| Region | Annual hourly peak (MW) | Peak time | Selected / annual hourly peak | Strongest week begins | Annual strongest weekly mean (MW) | Selected / annual weekly maximum |
|---|---:|---|---:|---|---:|---:|
| 1 | 490.20 | Feb 19, 03:00 | 100% | Feb 13 | 30.03 | 100% |
| 52 | 264.87 | Feb 19, 03:00 | 100% | Feb 13 | 5.48 | 100% |
| 87 | 430.25 | Aug 7, 10:00 | **84.38%** | **Jul 13** | **81.56** | **25.06%** |
| 108 | 75.28 | Feb 19, 03:00 | 100% | Feb 13 | 1.82 | 100% |

Only **5 of 88** upper-tail import-score hours in the bus-87 region are in
the selected windows. Its best selected weekly mean is 20.44 MW, compared
with 81.56 MW over July 13–20 (exclusive stop). The annual peak is 31.64% of
the regional cut-rating sum. This is not evidence of a cut-capacity violation;
it identifies missing seasonal coverage of local import/storage needs.

The bus-87 region contains buses **82, 83, 84, 85, 86, 87, 88, 89, 90, 91,
92, 93, 96 and 102**. Its boundary lines and ratings are recorded individually
in the linked JSON. The same global renewable profiles are applied at fixed
sites, but different regional wind/solar/load shares change when net demand
is high; global net-load extremes need not cover each regional extreme.

After additionally allowing every local battery to discharge at its full MW
rating, the import score is zero in every region at every hour. **This does
not establish self-sufficiency:** the screen does not require sufficient SoC,
sustainable discharge, endpoint recovery, or internal delivery feasibility.
The nonzero weekly scores remain relevant to storage cycling and energy needs.

Before allowing local generation, the strongest weekly positive-net-load means
in regions 1, 52 and 108 occur in December (weeks starting Dec 18, Dec 2 and
Dec 18). Existing coverage reaches 68.37%, 64.33% and 66.74% respectively.
These are also omitted seasonal patterns, but their largest residual import
scores after local generator capacity are covered in February. December is
therefore a secondary coverage limitation, not evidence that a second new
window is necessary for the present targeted batch.

## Export-pressure coverage

| Region | Peak after maximum charging (MW) | Peak / cut-rating sum | Hourly peak | Strongest week begins | Selected hourly / weekly coverage |
|---|---:|---:|---|---|---|
| 1 | 3,976.93 | 1.76 | May 1, 15:00 | May 17 | 100% / 100% |
| 52 | 9,766.26 | 3.19 | May 1, 15:00 | May 17 | 100% / 100% |
| 87 | 500.20 | 0.37 | May 1, 20:00 | May 16 | 100% / 100% |
| 108 | 1,855.32 | 1.56 | May 1, 15:00 | May 17 | 100% / 100% |

The April–May period contains these events and also all pre-charging export
peaks and strongest weeks. Above-one ratios flag renewable evacuation pressure
even under optimistic local charging. They do not imply required exports:
renewables may be curtailed, and storage may already be full. Solved dispatch
is needed to measure actual congestion, curtailment and economic effects.

## Approved addition

The owner approved **July 12–August 9 (exclusive stop), 672 hours**, as a regional
import-pressure window. This Monday-to-Monday four-week interval contains both
the July 13–20 strongest week and the August 7 hourly peak in the bus-87 region.
It is a post-screen addition, not an executed condition.
The full six-setting, two-formulation comparison adds **12 solves**,
for **72 total**. Keeping all five original windows preserves their distinct
aggregate-extreme and ordinary-control purposes.

The [six-window assessment](stage_b_selection/spatial_screen_approved.json) and
[updated score table](stage_b_selection/spatial_coverage_approved.csv) are retained
separately from the original evidence. The new window covers the bus-87 import
peak and strongest week; all four regions now have 100% hourly-maximum and
strongest-week coverage for both import-after-generation and export-after-charging
scores. December's pre-generation pressure remains a documented limitation.
The ordinary control remains January 14–28; its original selection rule and
four-window exclusion set are unchanged. Further additions require approval.

## Reproduction and evidence

Run from the repository root:

```bash
uv run --extra dev python -m experiments.case118_tracy_2021.spatial_screen
uv run --extra dev pytest tests/test_tracy_spatial_screen.py tests/test_tracy_window_selection.py
```

- [Regions, exact buses, cut rows/ratings, capacities and per-window coverage](stage_b_selection/spatial_screen.json).
- [Compact full-year versus selected-window score table](stage_b_selection/spatial_coverage.csv).
- Original hourly scores: local, ignored `results/stage_b_spatial_screen/regional_hourly.csv`.
- The command now regenerates the approved six-window assessment in the
  `_approved` files, including `regional_hourly_approved.csv`, without overwriting
  the original five-window evidence. The original calculation's source hash is
  retained in its original JSON; current source/provenance accompanies the update.
- [Screen implementation](spatial_screen.py); source, array/table and output hashes
  are recorded in the JSON provenance. Stage A evidence is unchanged.

Regeneration requires the owner-provided Stage A input arrays and aggregate
CSV; missing or changed inputs fail rather than substitute synthetic data.
Clone-ready tests exercise topology, parallel cuts, signs, units, capacity
subtraction, clipping, half-open containment, weekly aggregation and zero-only
scores. Retained topology/capacity checks need no private data; the additional
full-array reconstruction test is skipped only when owner arrays are absent.
This coarse four-region screen does not search every possible cut or predict
internal branch congestion, reactive/voltage constraints, or AC feasibility.
