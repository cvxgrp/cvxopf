# Battery operation in AC and DC

This experiment tests whether shifting battery energy across time lowers
operating cost in AC and DC models of the same three selected windows.

All 12 primary comparisons completed and passed fresh post-run physical,
identity, endpoint, and cost checks. The three selected AC windows contain
feasible lower-cost battery excursions under the original toy economics;
the matched DC solutions remain effectively idle. Independent SCIENTIFIC
numerical review by `cvxopf-review` is CLEAN.

## Matched results

F fixes real battery power to the DC schedule. B frees that schedule while
retaining identical per-device initial and terminal SoC, loads, generator
costs/limits, and renewable real output fixed to DC. AC reactive controls
remain free within their original constraints. AC F is named G internally.
Each window comprises three one-hour steps starting at 16:00 UTC in the
synthetic 2025 calendar. Costs are objective units, not asserted dollars.

| Window | AC generation cost reduction | Added battery cost | Net AC cost reduction | Reduction / fixed cost | AC throughput, B (MWh) | AC branch loss reduction (MWh) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| May 3, [2944,2947) | 612.6883 | 386.9281 | 225.7602 | 0.08605% | 386.9282 | 8.0319 |
| May 22, [3400,3403) | 632.7147 | 372.9893 | 259.7254 | 0.09837% | 372.9893 | 8.6217 |
| June 16, [4000,4003) | 677.9352 | 416.2888 | 261.6464 | 0.10023% | 416.2889 | 9.0984 |

The generation savings exceed the retained throughput penalty of 1 cost unit
per MWh in every AC comparison. Free-battery fleet schedules in MW (positive
means discharge) are:

| Window | Hour 1 | Hour 2 | Hour 3 |
| --- | ---: | ---: | ---: |
| May 3 | -193.1049 | -0.3592 | +193.4640 |
| May 22 | -186.2073 | -0.2873 | +186.4946 |
| June 16 | -207.7793 | -0.3652 | +208.1444 |

The excursions occur primarily at bus 65, with smaller activity at bus 41,
matching the direction and location of the retained historical plans. These
are full optimized three-hour plans, not a replay of three historically
implemented actions. Their endpoints preserve the DC schedule's tiny nonzero
state movement rather than imposing exact zero fleet energy change.

All DC native-objective improvements are below 0.00003 cost units and free
throughput below 0.00007 MWh. These are negligible numerical changes, not a
resolved DC rescheduling benefit. Native DC objective includes its weighted
loss proxy, which is retained separately in the machine-readable summary;
that proxy is not physical AC loss energy. Compare improvements within each
formulation, not the absolute AC and DC objective levels.

## Interpretation

The matched results support the proposed model-dependent economic mechanism
in these selected episodes: meaningful temporal battery action can reduce
the AC model's cost even with weak generator curvature and a substantial
throughput penalty, while the corresponding DC comparison shows no resolved
incentive. This is consistent with AC network physics and constraints
changing the costs of serving load across times and locations.

Fixed-battery AC was feasible in all three windows. Real-power cycling was
therefore not necessary for matched-window feasibility; battery reactive
support remained free in both arms. The observed improvement is conditional
on the common endpoints, fixed renewable output, and retained toy costs.

These solves do not separate the contributions of congestion, reactive or
voltage restrictions, and physical losses. They do not extract nodal dual
prices or establish a unique historical cause. The DC objective's loss proxy
is also an explicit difference between the models. The restricted AC baseline
and free-battery solve are local nonlinear solutions: the observed cost
reductions are feasible witnesses, not certified global battery value.
This selected sample provides no estimate of annual frequency or benefit.

## Execution and verification

- Source commit: `a0575f25a72fbb1a2654a1853ce258942046db2c`.
- Run: `experiments/case118_counterfactual/results/battery_operation_20260917`.
- Protocol: `experiments/case118_counterfactual/battery_operation/protocol.json`.
- Six serial DC solves, then six AC primary solves using two main lanes.
  No helpers, retries, cancellations, or rejected candidates were needed.
- Active wall: 421.9025 seconds; summed worker wall: 638.2417 seconds.
  These costs remain consumed for the prescribed-transfer phase.
- Sampled peak aggregate RSS: 13.1708 GiB; sampled peak worker RSS: 7.5764 GiB.
- All 12 children exited successfully and were reaped; no run processes remain.
- `check_results.py` rechecked source and context identities, all selected
  candidates, all 12 physical audits, fixed-arm feasibility under free-arm
  rules, generator/storage/native objective arithmetic, SoC recurrence and
  endpoints, and 66 retained artifact size/hash entries across 12 lifecycles.
  No new optimization was performed. Output: `verification.json`.

## Checkpoint

The owner has reviewed these results and authorized step 3, the 1 and 5 MWh
transfer comparisons. Those 12 comparisons have now run; see the separate
[prescribed energy shift report](TRANSFER_REPORT.md), including the subsequent
owner-requested June 16 5 MWh AC solve that returned `optimal` with lower cost.
Both attempts remain retained. The completed comparisons support the reported AC/DC distinction;
the more detailed question of which network effects produce it remains open.
