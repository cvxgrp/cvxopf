# Retained AC battery excursion screening

Yes: 142 retained three-hour windows pass a strict screen for a planned battery excursion beginning close to DC SoC, with effectively unchanged terminal state and idle DC battery power. These are qualifying windows, not independent episodes or proven economic opportunities.

## Selection and evidence

The screening thresholds were chosen for this exploratory read-only check: initial sum of absolute per-device AC–DC SoC gaps at most 0.001 MWh; first-hour sum of absolute battery-power departures at least 10 MW; no operator intervention. 181 of 8,760 saved intervals pass this initial screen.

All 181 original controlling-plan archives were then read and matched to their checkpoint size/hash and the audited interval-table hash. Checked one accepted controlling attempt, iteration and device identities, horizon indices, SoC recurrence, target agreement with DC, and achieved terminal state. This examines each original full optimized plan; it does not concatenate actions from overlapping controller windows.

The stricter subset additionally requires exactly three hours, no initial shard reset or crossing, total absolute initial-to-target SoC movement at most 0.001 MWh, full-window DC absolute throughput at most 0.001 MWh, and AC throughput exceeding the endpoint-implied minimum by at least 20 MWh. 142 windows pass, distributed across all 12 synthetic-calendar months. The cutoffs are selection choices, not revised physical solver tolerances.

## Examples

Fleet battery power is positive for discharge and negative for charge. Times are synthetic-calendar UTC; each row is a single three-hour optimization plan.

| Start | Index | Hour 1 MW | Hour 2 MW | Hour 3 MW | AC absolute throughput MWh | DC throughput MWh |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2025-05-03 16:00:00+00:00 | 2944 | -186.424 | -0.327 | 186.750 | 373.501 | 0.000070 |
| 2025-05-22 16:00:00+00:00 | 3400 | -207.480 | -0.385 | 207.866 | 415.731 | 0.000054 |
| 2025-06-16 16:00:00+00:00 | 4000 | -197.525 | -0.317 | 197.842 | 395.684 | 0.000053 |

May 3 at 16:00 (2944) is especially convenient: it is in the previously inspected 48-hour context, four hours after the study of AC dispatch adjustments began. Its initial fleet L1 SoC gap is 0.000019 MWh; required total per-device endpoint movement is just 0.000082 MWh, while the plan includes 373.501 MWh of absolute battery throughput. The large first charge is concentrated at storage buses 41 and 65, and the plan reverses it by the third hour.

The endpoint requirement alone does not require this large round trip, and the selected window has no material inherited SoC deficit to repay. That establishes the desired selection property. It does not establish the marginal-price explanation: network feasibility, cost incentives and local solver behavior remain to be distinguished. These future actions were planned; only the first action was implemented before the next controller reoptimization.

Recommendation: inspect index 2944 first for a bounded mechanism test, because its context is already available. No new OPF solves were run, and no next test is launched by this selection.

## Reproduction

From the repository root:

```sh
PYTHONPATH=. UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --no-sync --extra dev python experiments/case118_counterfactual/battery_operation/screening/screen.py
```

[Summary and provenance](summary.json), [all screened windows](screened-windows.csv), and [retained plan arrays](plans.json). The implementation tree is unchanged.
