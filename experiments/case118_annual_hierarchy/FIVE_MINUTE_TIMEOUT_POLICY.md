# Case118 five-minute primary-attempt policy

## Decision

For subsequent execution of the frozen Case118 annual experiment, each
target-constrained primary AC attempt receives an explicit 300-second solver
wall-time budget. If the primary does not return an accepted primal within that
budget, it is retained with its timeout/status evidence and the unchanged
causal recovery sequence begins. The target-free solve must be accepted before
its solution can initialize the copied hard-target solve. All existing
accepted-status, residual, first-accepted stopping, and state-advancement rules
remain unchanged.

This is an experiment policy, not a general `cvxopf` default. It is a bounded
tail-latency safeguard for this scenario and solver stack, not a claim that 300
seconds is an optimal timeout or that it saves time on every difficult window.

## Evidence

The completed S3 month contained three accepted primary attempts that exceeded
300 seconds. A post-hoc extreme-window replay reconstructed each exact archived
initial state, outer signpost, immediately preceding causal controller, named
shifted start, profiles, policy, and solver configuration. Both recovery solves
were paid for and independently passed the unchanged hard-target acceptance
gate.

| Interval | Archived primary (s) | Target-free (s) | Copied hard-target (s) | Immediate pair (s) | 300 s + pair (s) | Fallback saving (s) |
|---:|---:|---:|---:|---:|---:|---:|
| 479 | 546.17 | 55.37 | 93.56 | 148.93 | 448.93 | 97.24 |
| 569 | 535.63 | 34.91 | 104.19 | 139.11 | 439.11 | 96.52 |
| 603 | 469.42 | 24.93 | 115.88 | 140.80 | 440.80 | 28.62 |

The replay retained all 9,120 IPOPT starting coordinates for every solve
(6,876 model-owned and 2,244 reduction-introduced) and verified exact named
start and target reconstruction. Timings exclude model construction and start
assignment and include CVXPY canonicalization, complete IPOPT `x0`
construction/verification, and IPOPT execution.

These windows were selected because their S3 primaries were slow. The result
supports this tail safeguard but does not estimate general runtime savings.

## Implementation gate before S4b/S5 AC execution

1. The primary solve is actually stopped at its typed, explicit, retained
   300-second budget, after which recovery begins.
2. Timeout classification, the complete primary evidence, and every recovery
   attempt remain in the audit tree.
3. The budget is part of frozen experiment configuration and provenance, never
   a hidden constant.
4. A focused synthetic test exercises timeout into target-free and copied
   recovery without waiting five minutes.
5. Reporting separates the consumed primary budget, target-free time, copied
   time, construction/canonicalization time where available, worker/restart
   overhead, and total window latency.
6. Existing acceptance, causal-source, first-accepted stopping, and exact-once
   state-advancement rules remain unchanged.

## Deferred performance work

- prospective evaluation over larger samples;
- adaptive budgets based on horizon, recent solves, or solver progress;
- speculative parallel recovery;
- construction and worker-restart overhead;
- machine- and solver-specific calibration;
- median, upper-quantile, and worst-case latency; and
- alternative warm starts and future formulations.

These questions belong in a dedicated performance milestone and do not block
adoption of the frozen Case118 safeguard.
