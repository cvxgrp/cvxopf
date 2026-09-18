# S2 report: one-week hierarchical execution

## Disposition

**Complete as a resource-boundary stage; complete scientific trajectory
obtained through an exploratory continuation.**

The frozen 16 GiB execution successfully reached and handled its declared
resource boundary after 128 intervals. An explicitly labeled 24 GiB
continuation resumed from the verified checkpoint and completed a fully
accepted 168-interval trajectory.

This is neither an uninterrupted 16 GiB pass nor simply a failed run. The two
execution classifications are complementary scientific results:

| Invocation | Classification | Result |
|---|---|---|
| Authoritative, 16 GiB | `resource_limit` | 128 intervals durably completed; supervisor stopped the worker after observing 19,220.4 MiB RSS |
| Exploratory continuation, 24 GiB | `completed` | Resumed at interval 128 and completed the remaining 40 intervals in 2,142.2 seconds |

The continuation's `eligible_for_advancement: true` is worker-local. It means
that the completed trajectory passed that worker's numerical and provenance
checks. It does not override the experiment-level classification of the first
invocation or convert the combined execution into an uninterrupted pass of the
frozen 16 GiB policy.

## Scientific result

The completed trajectory provides strong evidence that one coherent lossy-DC
plan can guide sequential, network-constrained AC execution over the frozen
Case118 week:

- all 168 controlling AC attempts were accepted;
- the generated-flat first start and all 167 applicable shifted starts
  succeeded in their primary slots;
- no target-free or perturbation recovery attempt was needed;
- every executed action and realized storage state remained causally aligned
  across the process restart;
- fixed served load equaled requested load; and
- final storage targets were reached to numerical precision.

These conclusions apply to this network, synthetic year segment, storage
placement and sizing, hierarchy policy, and solver stack. They do not establish
general AC feasibility, recursive feasibility, or solver reliability for other
systems.

## Independent numerical reconstruction

| Measure | Result |
|---|---:|
| Completed intervals | 168 / 168 |
| Accepted controlling attempts | 168 / 168 |
| Recovery windows | 0 |
| Shifted-primary success | 167 / 167 |
| Maximum terminal SoC deviation | 5.68e-14 MWh |
| Maximum SoC recurrence residual | 7.65e-12 MWh |
| Maximum voltage violation | 0 pu |
| Maximum thermal residual | 2.78e-7 MVA |
| Maximum normalized squared thermal residual | 3.70e-9 |
| Fixed-load service residual | 0 MW |
| Cumulative absolute signpost deviation | 2.25e-10 MWh |
| Renewable curtailment | 4.95e-5 MWh |
| Active losses | 19,327.873 MWh |
| Controlling-solve time | 13,440.8 s |

Storage throughput was 611.806 MWh at bus 41, 2,517.240 MWh at bus 65,
8.04e-5 MWh at bus 89, and 292.403 MWh at bus 105. The near-zero use at bus
89 is a siting and economics question for later analysis, not a correctness
failure.

The reconstruction applies the complete independent AC gate to every
controlling archive. Its consolidated result is `accepted_for_s3: true`.
This is the study-level advancement decision based on the accepted complete
trajectory and successful resource recovery; it does not denote uninterrupted
compliance with the original 16 GiB execution condition.

## Resource boundary and restart

The authoritative supervisor detected the 16 GiB crossing, terminated the
worker without promoting incomplete work, and retained interval 128 as the
last verified physical boundary. The crossing was classified as a resource
limit rather than solver failure or infeasibility.

| Observation | Result |
|---|---:|
| Verified restart boundary | 128 intervals |
| Supervisor peak before termination | 19,220.4 MiB |
| Fresh continuation-worker RSS | 182.6 MiB |
| First resumed-window peak | 10,431.3 MiB |
| Continuation maximum safe-boundary RSS | 17,874.9 MiB |
| Remaining intervals completed | 40 |
| Continuation wall time | 2,142.2 s |

The restart reclaimed substantial process memory and preserved the causal
state chain. This is positive resilience evidence from a real resource event,
stronger than synthetic interruption testing alone. It supports a follow-up
study of planned worker recycling, but it does not identify the retaining
subsystem, prove monotonic memory growth, or establish an optimal recycling
interval.

Safe-boundary samples show that RSS after build release can remain close to
post-solve RSS. This is consistent with process-lifetime allocator or solver
state retention, but it is not proof that complete live OPF builds remain
reachable. Planned recycling must be evaluated as its own frozen policy.

## Provenance note

Invocation 0 began from clean commit
`3cd4229619792c3f7b20beba805d9fc2bf6bc54e`. An untracked exploratory scaling
notes file was created while it was running, so the supervisor recorded a dirty
end worktree and `context_matches: false`. The executable source fingerprint,
scenario, policy, solve configuration, and Git commit did not change. This is
procedural context rather than evidence of numerical drift, but it prevents the
first invocation from satisfying its literal clean-worktree advancement gate.

The scaling notes were removed before continuation execution. Invocation 1
started and ended clean, and its source provenance matched. The notes were
restored only after the worker had produced its final result record.

## Next gate

Before S3, freeze either a modest planned worker-recycling policy or a bounded
recycling comparison. It should measure restart overhead, peak RSS per worker,
archive continuity, and agreement of executed actions and realized states with
the present sequential result. Root-cause memory profiling is useful
engineering follow-up but is not required before that scientific comparison.

The compact integrity and metric record is `S2_RESULTS_METADATA.json`.
Its hashed `trajectory/checkpoint.json` is the final 168-interval checkpoint;
the interval-128 restart boundary is established by the authoritative
supervision record and immutable archive prefix. Detailed 52 MiB execution
artifacts remain ignored.
