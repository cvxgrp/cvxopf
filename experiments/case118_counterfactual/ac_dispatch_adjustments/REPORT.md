# AC dispatch adjustments

This experiment asks how much generation must change to obtain feasible AC
operation, whether further generator changes lower cost, and whether battery
rescheduling adds value. All four comparisons passed the physical and cost
checks. Allowing unrestricted generator redispatch reduced cost by 13.35%
relative to the lower-cost small-adjustment solution. Allowing battery
rescheduling produced only a numerical-scale cost difference in this window.
These are feasible solutions and observed improvements from nonconvex solves,
not certified minimum adjustments or global value.

## Scope and common conditions

Implementation: `35d951b`. Source inputs, ordered identities, software versions, selected window, and settings are retained in [study.json](../results/ac_dispatch_adjustments_20260917/study.json) and [protocol.json](protocol.json).

The selected window is May 3, 12:00–15:00 UTC in the synthetic calendar (indices 2940–2942). It sits inside the highest-ranked joint six-hour episode, chosen before new outcomes, with 48 hours of [retained context](context.png). The context contains no shard reset or operator intervention. This is a deliberately diagnostic window, not a representative annual sample.

Every arm uses the same DC initial and terminal battery SoC, renewable real-power schedule, and original AC constraints and toy costs. R1/R2/G hold battery real power at DC; B frees it. Reactive controls remain available in all arms. Historical AC entered this window about 63 MWh below the DC storage state, and recharged during it. The matched experiment removes that inherited state difference; it does not replay or explain away that historical recharge.

## Results

| Comparison (saved code) | Common cost (objective units) | Total absolute generator change (MWh) | Battery throughput (MWh) |
| --- | ---: | ---: | ---: |
| Minimize generator adjustment; battery fixed (R1) | 198,207.098457 | 2,138.203346 | 0.000304823 |
| Minimize cost with adjustment limited to R1 + 1 MWh; battery fixed (R2) | 196,047.889036 | 2,139.203345 | 0.000304823 |
| Minimize cost with unrestricted generator redispatch; battery fixed (G) | 169,880.380983 | 3,972.203303 | 0.000304823 |
| Minimize cost with generator redispatch and battery rescheduling (B) | 169,880.380784 | 3,972.203274 | 0.000304823 |

R2→G reduces common cost by **26,167.508054 units**, or **13.347508% of R2**, and by **2.161517 units per served MWh**. Total absolute generator departure rises, while net generator increase (equal here to additional AC supply for losses up to audited DC balance residuals) falls from 458.853 to 328.470 MWh. Opposing generator change rises from 840.175 to 1,821.867 MWh. Branch losses fall by 130.383 MWh. This is evidence that a larger rearrangement of generation can be substantially cheaper under common constraints and battery conditions in this window. The experiment does not isolate a single binding constraint as its cause.

G→B changes cost by just **0.000198118 units**, about **0.000000101% of R2**. This is smaller than the single-candidate cost-reconstruction tolerance at this scale (about 0.000270 units). That tolerance checks arithmetic consistency, not solver optimality; the small difference cannot support a resolved battery-value claim. Throughput remains effectively zero and the maximum battery-power difference from DC is only 0.0000573 MW. This finding is conditional on this three-hour window, common DC endpoints/state, historical toy economics, and the starts tried. It does not establish that storage lacks value in other episodes or over longer horizons.

R1→R2 also reduces cost, by 2,159.209421 units. R1 optimizes departure rather than cost, so this is not an estimate of the marginal value of the extra 1 MWh allowance.

## Numerical and lifecycle verification

- Four primary candidates accepted; each was selected over its destination-audited incumbent where applicable. R1's perturbation helper launched after 300 seconds, lost the race, and was canceled and reaped. B required no target-free recovery in this comparison.
- Four completed arms, five launched attempts, **483.02 seconds wall time** and **502.22 aggregate worker-seconds**. Per-primary worker elapsed times: R1 324.26 s, R2 53.06 s, G 82.22 s, B 22.58 s; canceled helper 20.10 s.
- Sampled peak combined RSS: **12.10 GiB**; sampled peak worker RSS: **10.39 GiB**, within the reused 24/16 GiB hard limits. These are sampled peaks, not OS maximum-RSS counters.
- All five workers were reaped; final process inspection found no remaining counterfactual parent/workers. Independently verified all 29 artifact size/hash entries in lifecycle receipts.
- [check_results.py](check_results.py) independently reconstructs every selected candidate's generation/storage costs and generator departure from retained arrays, and checks selected-file hashes. Values agree within 1e-8.
- Frozen physical tolerances passed throughout. R1's departure epigraph objective exceeds recomputed actual departure by 0.000754 MWh, within the declared 0.001 MWh check; R2's budget is based on the recomputed departure. Later cost-reconstruction differences are within declared tolerances. No tolerances were changed after launch.
- The first launch directory records an environmental failure: sandbox denial of `ps` after 0.019 s, before model construction or a solve. Its worker was reaped. The successful fresh `-r2` invocation used identical numerical settings with process-monitor access.

Independent protocol/context and final SCIENTIFIC numerical reviews by cvxopf-review were CLEAN. The reviewer independently reconstructed all four complete acceptance audits from authoritative inputs, matched every saved audit, and verified source/execution/protocol and artifact identities. This completes the selected one-window comparison. No additional windows of these four comparisons have been run. The separately selected [battery operation experiment](../battery_operation/REPORT.md) addresses a different question using three windows.

Raw results: [summary.json](../results/ac_dispatch_adjustments_20260917/summary.json). Additional verification: [verification.json](verification.json).
