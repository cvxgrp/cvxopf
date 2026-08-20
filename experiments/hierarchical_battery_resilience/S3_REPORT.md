# M17-S3 manual DC-to-AC experiment

## Status

The frozen manual experiment has been executed and its results are ready for
scientific and policy review. No public hierarchical-controller abstraction
has been implemented.

The numerical run reported below was produced from an uncommitted S3 working
tree based on S2 commit `52c2896`. Its artifact hashes are internally
consistent, but the original metadata recorded only package versions and not
the exact source tree. It is therefore preliminary evidence rather than the
authoritative reproducibility run. The S3 infrastructure now records Git
commit and dirty state plus deterministic fingerprints of the experiment and
`src/cvxopf` sources. After this infrastructure is checkpointed, the normative
experiment must be rerun from a clean commit before S3 is closed.

## Main findings

1. **DC energy endpoints are AC-realizable in both reviewed regimes.** Both
   18-hour endpoint cases solved with accepted AC primals, exact inherited
   terminal SoC, and all frozen residuals inside tolerance. AC did not simply
   replay the DC battery trajectory.

2. **The fixed-plan hard-target controller completed the full 96 hours.** It
   returned the battery from 500.0 MWh to 500.0 MWh and met every DC signpost
   to numerical tolerance.

3. **The quadratic target is materially soft at the frozen weight.** The
   fixed-plan soft controller completed, but finished at 344.4 MWh rather than
   500.0 MWh. Its mean absolute predicted endpoint deviation was 161.5 MWh.

4. **Stepwise replanning does not by itself guarantee AC target feasibility.**
   The hard-target replanned run terminated at interval 35 when the AC solver
   did not return an accepted realization of the new DC signpost. The
   target-free diagnostic AC problem solved, so this is classified as
   target-conditioned failure rather than general AC network infeasibility.
   This classification does not prove global infeasibility of the nonconvex
   target-conditioned AC problem.

5. **A soft inner target can destroy remaining-horizon outer feasibility.**
   The soft replanned run executed 95 intervals. Its accumulated signpost
   deviations left 371.9 MWh before the last interval. Returning to the fixed
   500.0 MWh global target required 128.1 MW of charging, while even the
   copper-plate final-interval headroom was only about 123.6 MW. The final
   outer DC problem was therefore infeasible before an AC window was built.

The fifth result is the central design finding. Feedback and replanning are
not the same as recursive feasibility. If the inner layer may depart from its
energy signpost, the executed next state must also remain inside the feasible
set of the remaining outer problem if the global terminal obligation is to be
guaranteed.

## Frozen experiment

The experiment uses `tracy_high_96h_v1`, with 96 hourly intervals, a nominal
five-hour AC window, one explicitly identified 150 MVA / 1,000 MWh battery,
fixed nonsheddable loads, zero-cost renewable curtailment, and enforced AC
`rateA` limits. The long layer is lossy DC; the short layer is nonlinear AC.
Every controlling action was subject to the S2 accepted-primal and residual
gate. No fallback, target relaxation, load shedding, solver retuning, or
post-result policy change was introduced.

The two endpoint cases were frozen before execution:

| Case | Intervals | Geometric role |
|---|---:|---|
| `crosses_saturation_boundary_32_50` | `[32, 50)` | crosses a storage saturation boundary |
| `within_regime_60_78` | `[60, 78)` | remains within one decoupled regime |

## Endpoint realization

| Case | AC status | Terminal error | Max battery-power difference from DC | Max interior SoC difference |
|---|---|---:|---:|---:|
| saturation crossing | `optimal` | 0.0 MWh | 2.90 MW | 3.44 MWh |
| within regime | `optimal` | 0.0 MWh | 4.53 MW | 13.70 MWh |

All SoC, active/reactive balance, voltage, and two-terminal thermal residuals
passed. The nonzero interior differences are expected and desirable: the AC
layer preserves the communicated energy states while independently selecting
an AC-feasible dispatch. Crossing the saturation boundary did not prevent
endpoint realization and did not produce the larger interior discrepancy in
this pair.

## Sequential results

| Outer policy | Inner policy | Executed | Final SoC | Outcome | Generation cost | AC active loss |
|---|---|---:|---:|---|---:|---:|
| frozen | hard equality | 96/96 | 500.0 MWh | complete | 360,263.4 | 391.9 MWh |
| frozen | quadratic soft | 96/96 | 344.4 MWh | complete | 358,924.3 | 383.3 MWh |
| replan every step | hard equality | 35/96 | 515.1 MWh | target-conditioned AC failure | 137,546.0* | 147.7 MWh* |
| replan every step | quadratic soft | 95/96 | 371.9 MWh | final outer problem infeasible | 357,833.0* | 373.7 MWh* |

`*` denotes a partial-horizon total and must not be compared as though it
covered all 96 intervals.

Neither completed run violated voltage or branch limits beyond the frozen
tolerances. Renewable curtailment was 239.2 MWh in both complete runs and
remained a metric of interest, not an optimized penalty.

The soft policies frequently chose nonzero endpoint deviation: 92 of 96 fixed
windows and 89 of 95 accepted replanned windows were classified
`soft_target_deviated`. Mean absolute deviations were 161.5 MWh and 149.3 MWh,
respectively. This is not a residual failure; the reported quadratic terminal
cost agreed with the modeled expression in every accepted window.

### Replanned hard failure

At iteration 35, the realized starting SoC was 515.1 MWh and the new outer DC
plan selected 850.0 MWh at the five-hour AC endpoint. The outer plan itself
was accepted. The hard-conditioned AC solve returned `infeasible`; the same AC
window without a terminal target returned an accepted solution. The runner
therefore retained both attempts and classified the event as
`target_conditioned_failure`, then correctly executed neither.

The five-hour copper-plate energy headroom was more than the requested energy
increase, so a simple storage-rating or aggregate-generation calculation does
not explain the failure. The retained evidence localizes the failure to adding
the target to the AC solve, but it does not yet distinguish an AC network
limitation from local nonconvex solver behavior. A matched-state or alternate-
initialization diagnostic would be an additional study and is not silently
introduced into the frozen baseline.

Local-solver sensitivity is a plausible leading hypothesis: only 1.63 MWh of
initial-state difference separated the failed case from the successful frozen
case with essentially the same target, and battery, active/reactive generator,
and thermal margins remained in that successful solution. This is not an
interior-feasibility proof, however. Voltage magnitude reached its 1.10 p.u.
upper bound at buses 6 and 8. Physical infeasibility has therefore neither been
demonstrated nor excluded.

### Replanned soft failure

After 95 accepted actions, the battery held 371.9 MWh. The final global target
was still 500.0 MWh, so the one remaining interval required 128.1 MW of
charging. At that interval:

- load was 265.4 MW;
- available nondispatchable power was 38.9 MW;
- dispatchable capacity was 350.0 MW; and
- aggregate charging headroom was therefore about 123.6 MW.

The final outer solve was correctly classified `infeasible`. No AC attempt was
made, and the failed outer plan remains in the audit record.

## Policy implications for S4

The experiment does not support naming one of the four tested combinations as
an unqualified default:

- hard targets preserve energy obligations but do not guarantee an accepted
  AC solve; the observed failure remains unresolved between physical
  infeasibility and local nonconvex solver behavior;
- soft targets preserve AC solvability longer but do not preserve the global
  terminal obligation or recursive outer feasibility;
- frozen planning completed here, but it does not update its energy plan from
  realized AC state; and
- stepwise replanning is the correct MPC-like feedback pattern, but feedback
  alone supplies no viability guarantee.

The conservative S4 recommendation is therefore:

1. keep outer and inner policies explicit rather than hiding a default;
2. preserve the current terminate-and-audit behavior for an unaccepted
   controlling solve;
3. expose frozen and stepwise-replanned operation as distinct policies; and
4. treat recursive-feasibility protection as an explicit design problem, not
   as an automatic relaxation or solver workaround.

Before S4 is frozen, decide whether a remaining-horizon viability guard belongs
inside M17 or is a separately staged controller extension. The baseline result
should not be altered retroactively to make every trajectory complete.

## Reproduction and artifacts

Run:

```bash
uv run python -m experiments.hierarchical_battery_resilience.reproduce
```

Interrupted runs may use `--resume`; only complete readable artifacts are
reused after schema, study/policy identity, trajectory counts, previous
artifact hashes, scenario, software, Git, and source-fingerprint checks pass.
All artifact writes are atomic. Unavailable nonfinite result fields are
encoded as JSON `null` while their audit classification is retained.

The local ignored artifact directory contains compressed complete extracted
results for every outer plan and AC attempt, a trajectory summary, and hashes.
The preliminary run used Python 3.13.2, CVXPY 1.9.2, NumPy 2.5.1, and pandas
3.0.3. The frozen scenario-manifest SHA-256 was
`46ae15be4f56681416423f17d9374c95a8f5274010319c552ce5308bf7cbc80b`.
