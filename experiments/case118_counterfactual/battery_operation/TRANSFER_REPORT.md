# Cost of prescribed battery energy shifts in AC and DC

All 12 prescribed-schedule comparisons completed and passed independent
post-run physical, identity, schedule, endpoint, and cost checks. The 1 MWh
shift produced lower-cost feasible AC solutions in all three selected windows,
while the matched DC shifts increased native objective cost. The original
June 16 5 MWh AC result had higher cost and status `optimal_inaccurate`.
An additional owner-requested solve returned `optimal` with a 14.712430-unit
saving. There is now a lower-cost feasible AC witness for every prescribed
shift, while all six matched DC shifts increase cost.

Independent numerical reviews of both the original study and the additional
solve by `cvxopf-review` are CLEAN under the scientific review standard.
The additional solve is separately retained; the original result remains
unchanged.

## Comparison

This is step 3 of the [battery operation study](../../../plans/case118-toy-battery-mechanism-test.md).
The same three-hour windows start at 16:00 UTC in the synthetic 2025 calendar.
Starting from each retained DC battery schedule, prescribe an extra 1 or 5 MWh
of fleet charging in the first hour and the same discharge in the third hour.
The middle hour is unchanged. Device allocations use the historical weights
saved before the fixed/free outcomes. Each device retains its original initial
and terminal SoC; renewable real power and physical limits remain unchanged.
Generator dispatch and AC reactive controls are reoptimized.

Earlier accepted fixed-battery results supply each baseline; they were
re-audited, not re-solved. The AC baseline also supplies initialization, but
cannot serve as a feasible fallback under the changed battery schedule.
The historical toy costs remain: generator curvature 1e-4 and storage
throughput penalty 1 cost unit per MWh. Costs below are objective units,
not asserted dollars.

## Observed cost changes

Changes are prescribed minus fixed: **negative means a saving**. AC native
cost is generation plus battery throughput cost. DC native cost additionally
includes its weighted loss proxy; that term is reported separately below.

| Window | Transfer (MWh) | AC cost change | AC change / MWh | DC cost change | DC change / MWh | AC solver status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| May 3 | 1 | -2.443397 | -2.443397 | +1.184900 | +1.184900 | `optimal` |
| May 3 | 5 | -11.768780 | -2.353756 | +5.928653 | +1.185731 | `optimal` |
| May 22 | 1 | -2.939755 | -2.939755 | +1.179212 | +1.179212 | `optimal` |
| May 22 | 5 | -14.676655 | -2.935331 | +5.900220 | +1.180044 | `optimal` |
| June 16 | 1 | -2.947518 | -2.947518 | +1.179478 | +1.179478 | `optimal` |
| June 16 | 5 | +79.046490 | +15.809298 | +5.901479 | +1.180296 | `optimal_inaccurate` |
| June 16, owner-requested additional solve | 5 | -14.712430 | -2.942486 | +5.901479 | +1.180296 | `optimal` |

The final row reuses the same DC result; no additional DC solve was performed.

All six DC solves returned `optimal`. In the original batch, five AC solves returned `optimal`;
these remain local nonconvex results, not globally certified optima.
June 16's 5 MWh AC solve returned `optimal_inaccurate`.
It passed the unchanged physical and cost audits and was accepted
under the existing policy. No helper was triggered for that case. Its +79.05
cost change is a property of the returned feasible solution, not an established
penalty of prescribing 5 MWh or evidence of an economic threshold between
1 and 5 MWh. The additional solve below establishes a lower-cost feasible
solution for this exact prescribed schedule.

## Cost components and losses

Each prescribed shift adds exactly 2E MWh of absolute battery throughput to
numerical precision, and therefore 2E cost units. The table separates generator
cost from this penalty. DC's loss-proxy change is in objective units;
physical AC branch-loss change is in MWh. These quantities are not interchangeable.

| Window | Transfer (MWh) | AC generation cost change | Added battery cost | DC generation cost change | DC loss-proxy cost change | AC branch-loss change (MWh) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| May 3 | 1 | -4.443397 | +2.000000 | -0.813723 | -0.001377 | -0.303348 |
| May 3 | 5 | -21.768780 | +10.000000 | -4.064594 | -0.006753 | -0.849771 |
| May 22 | 1 | -4.939755 | +2.000000 | -0.819135 | -0.001652 | +0.004246 |
| May 22 | 5 | -24.676655 | +10.000000 | -4.091679 | -0.008100 | +0.021720 |
| June 16 | 1 | -4.947518 | +2.000000 | -0.818908 | -0.001614 | +0.004503 |
| June 16 | 5 | +69.046490 | +10.000000 | -4.090604 | -0.007917 | -0.105924 |
| June 16, owner-requested additional solve | 5 | -24.712430 | +10.000000 | -4.090604 | -0.007917 | +0.025543 |

For the 1 MWh shifts, observed AC generation savings of 4.44–4.95 exceed the
2-unit battery penalty; net savings are 2.44–2.95 per MWh shifted. DC generation
savings are only about 0.81–0.82 per MWh shifted, so the penalty dominates.
The small reduction in DC's loss proxy does not change that conclusion.
All three windows now show AC savings for the 5 MWh shift, with similar average
cost changes per MWh to their 1 MWh shifts. These are finite differences along the selected schedule
direction, not nodal dual prices or infinitesimal derivatives.

AC savings do not require lower total branch-loss energy in every comparison:
May 22's shifts and June 16's 1 MWh shift slightly increase total branch losses
while reducing generation cost. This rules out attributing all observed cost
savings simply to a reduction in aggregate loss energy. It does not isolate
congestion, reactive/voltage constraints, the location and timing of losses,
or changes among marginal generators.

## Additional June 16 solve requested by the owner

After reviewing the original outcome, the owner requested another June 16
5 MWh AC solve seeking a clean solver return. One isolated attempt reused the
same model, data, prescribed schedule, endpoints, solver options, and audit
tolerances. The initialization changed to the existing small perturbation of
the accepted fixed-battery baseline: slot 6, scale 0.0001, seed 17400021.
This is a fixed-schedule perturbation, not target-free recovery.

The attempt returned `optimal`, passed fresh physical and cost audits, and
reduced generation cost by 24.712430 units against the fixed baseline. Added
throughput cost is 10 units, leaving a net saving of 14.712430 units, or
2.942486 per MWh shifted. Its cost is 93.758920 units below the original
`optimal_inaccurate` result. Physical branch-loss energy increases by
0.025543 MWh, again showing that lower generation cost need not mean lower
total branch-loss energy.

The diagnostic was bounded to one attempt, 300 seconds between solve-phase
markers, 420 seconds worker wall time, and the existing 16 GiB worker RSS limit.
It used 46.049475 active seconds and 46.042461 aggregate worker seconds. The
worker exited successfully and was reaped. No further attempts were launched.
Both phases plus this diagnostic consumed 842.270134 active seconds,
1,394.779224 aggregate worker seconds, 14 AC attempts and 12 DC attempts.

Raw evidence is retained separately in
`experiments/case118_counterfactual/results/battery_transfer_jun16_retry_20260917`:
the diagnostic script/hash, pre-launch plan, exact source snapshot, request,
complete initialization, result, independent audit, and lifecycle. All committed
runtime Python files match `dd21216`; the worktree also contained the uncommitted
post-run checker from the first execution. The snapshot records that context
explicitly. No model or solver implementation changed for this diagnostic.
[check_transfer_retry.py](check_transfer_retry.py) verifies this provenance,
unchanged problem and baseline, energy/endpoints, costs, lifecycle artifacts,
and cumulative resources without building or solving an optimization problem.
Its output is [transfer-retry-verification.json](transfer-retry-verification.json).

## Interpretation and limits

The prescribed small shifts strengthen the earlier [fixed/free comparison](REPORT.md):
under common storage endpoints and fixed renewable output, the selected AC
model admits lower-cost battery schedules in a direction for which the matched
DC calculation reports higher cost. Strong generator curvature is not needed
for this observed difference under the historical toy economics.

The AC comparison still depends on local solutions, including the retained
fixed baseline. It supplies lower-cost feasible witnesses relative to that
baseline, not a certified optimum or proof of a unique physical cause. The
different June 16 returns demonstrate sensitivity to initialization and solver
termination; `optimal` does not certify global optimality. The selected
three-window sample supports no annual frequency or
annual benefit estimate and does not transfer toy numerical values to Tracy.

## Original batch execution and verification

- Source commit: `dd212168cc301e42d9d97dd084552168b86a4c42`; clean committed
  execution authorized by the owner. No source-snapshot alternative was used.
- Launch protocol: [transfer-protocol.json](transfer-protocol.json).
- Raw artifacts: `experiments/case118_counterfactual/results/battery_transfers_20260917`.
- Six serial DC solves followed by six AC comparisons with two main workers
  and one shared helper. The May 22 1 MWh primary reached the existing
  300-second helper threshold; its perturbed-start helper completed first.
  The primary was canceled and reaped. All 13 child lifecycles are accounted
  for; no study processes remain.
- This phase: 374.318135 active seconds and 710.495054 aggregate worker seconds;
  seven AC attempts and six DC attempts, including canceled work.
- Both phases combined: 796.220660 active seconds, 1,348.736764 aggregate worker
  seconds, 13 AC attempts and 12 DC attempts. The DC allowance is fully used;
  no automatic continuation is authorized.
- Sampled peak aggregate RSS: 12.8395 GiB; sampled peak worker RSS: 7.2720 GiB.
- [check_transfers.py](check_transfers.py) reconstructs source identities and
  pre-outcome schedules, re-audits all baseline and prescribed results, verifies
  cost components and finite differences, checks energy and endpoints, hashes
  71 retained artifacts, and reconciles lifecycle and cumulative budget records.
  It does not build or solve optimization problems. Its retained output is
  [transfer-verification.json](transfer-verification.json).

The scientific result is ready for owner review. The toy horizon study remains
deferred. No Tracy input generation,
new model economics, additional AC solves, or commits are part of this closeout.
