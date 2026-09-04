# S3 one-month Case118 hierarchy report

## Outcome

S3 completed the frozen 720-hour rated Case118 hierarchy from clean execution
commit `c5d0bbcbb3b1cad9036294ed8f680e4c55673264`. All 720 controlling AC
intervals were accepted. The exact planned schedule produced 44 worker
restarts and 45 invocations, followed by `study_complete` at boundary 720.
There were no abnormal stops, reviewed continuations, artifact failures, or
provenance mismatches.

Independent reconstruction classifies the trajectory as complete and
numerically eligible for S4 review. This is evidence for the frozen month,
network, scenario, machine, policy, and solver stack; it is not a universal
reliability result.

## Numerical audit

| Measure | Result |
|---|---:|
| Completed intervals | 720 / 720 |
| Accepted ordinal-zero controllers | 717 |
| Recovery windows | 3 |
| Shifted-primary success | 716 / 719 (99.583%) |
| Maximum terminal deviation | $4.19\times10^{-13}$ MWh |
| Maximum SoC recurrence residual | $2.03\times10^{-10}$ MWh |
| Maximum voltage violation | 0 pu |
| Maximum thermal residual | $2.69\times10^{-7}$ MVA |
| Fixed-load service residual | 0 MW |
| Cumulative absolute signpost deviation | $1.14\times10^{-7}$ MWh |
| Renewable curtailment | 126.761 MWh |
| Active losses | 81,022.575 MWh |
| Realized generation cost | 51,938,760.505 |
| Storage-cycling cost | 12,717.425 |

All three recovery windows—281, 455, and 457—had the same causal lifecycle.
The shifted primary returned `user_limit` and was withheld; an accepted
target-free solve then initialized an accepted copied hard-target solve. No
perturbation attempt was needed. These events demonstrate successful recovery
from initialization-sensitive local-solver behavior, not modeled
infeasibility.

Storage throughput was 2,163.644, 9,613.477, 0.000579, and 940.303 MWh at
buses 41, 65, 89, and 105 respectively. Bus 89 was effectively inactive in
this trajectory; no general storage-siting conclusion is drawn from that one
scenario.

## Resource and restart record

Total retained wall time was 41,744.1 seconds (11 h 35 min 44 s). Maximum
externally sampled worker RSS was 15,985.7 MiB, below the frozen 24 GiB limit;
the maximum internally sampled current RSS was 15,689.7 MiB. Restart-to-first-
checkpoint time across invocations 1–44 ranged from 26.0 to 273.6 seconds,
with a median of 41.9 seconds.

Every restart resumed from the exact verified checkpoint and immutable outer
plan. The promoted result retains all 45 lifecycle classifications and the
global-interval-indexed RSS series. Planned recycling therefore controlled the
observed process lifetime without changing the causal trajectory or requiring
an abnormal continuation.

## Post-S3 tail-latency decision

Three additional accepted primaries took 469–546 seconds. A separately labeled
post-hoc replay of those selected extreme windows found that causal target-free
plus copied-target-free recovery passed the unchanged hard-target acceptance
gate and that a 300-second primary budget followed by recovery reduced measured
solver-path latency in all three replays. The five-minute rule is therefore
frozen as a bounded Case118 tail-latency safeguard before S4b/S5 AC execution;
it does not alter S3, apply to outer-only S4, or claim a universally optimal
timeout. `FIVE_MINUTE_TIMEOUT_POLICY.md` records the decision and its deferred
performance questions.

The replay artifact hash and its dirty-worktree provenance qualification remain
a deferred record-completion item before S4b. They do not affect the accepted
S3 execution or block outer-only S4.

## Promoted record

`S3_RESULTS.json` is the independently reconstructed compact record:

- bytes: `144315`
- SHA-256: `74f2c37f4dfe039a991cfddba5d035954108bb2bceccbc53fe6950f61ac903aa`
- outer plan SHA-256:
  `7b17a0935e748f6dde0d9b16166eb07bd49d96205f1ae03c8fc2e9f9707c032d`
- final checkpoint SHA-256:
  `fdd5ce4a78d82d71e0217e78cd6014a066f410d0d51fd2004b9eb2db765fbf2d`
- execution source fingerprint:
  `7043d756847a4a107f2ad68aa80dde0742a65ea097b662a67400b95ed4b4b02d`

The detailed 720-window execution tree remains ignored but integrity-bound by
the promoted record.
