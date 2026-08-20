# M17 S3b causal-recovery experiment

## Main result

The predeclared causal hard-target recovery policy completed all 96 intervals
of the frozen Tracy-derived scenario. The first flat-start AC window was
accepted. Of the 95 later windows, 94 accepted the first shifted-preceding
start. One window, interval 80, required recovery: the target-free solve was
accepted, and copying that solution into a fresh hard-target problem produced
an accepted controlling solve. No deterministic perturbation attempt was
needed.

This result supports `shifted_with_recovery` as an operational policy for this
frozen scenario and solver stack. It does not establish a universal default
across networks, horizons, operating conditions, or nonlinear solvers.

## Frozen execution

The experiment ran from clean commit
`6c4214ce158feb5cb218dafc933d8fcd7b36f34c` under the environment and source
fingerprints recorded in `S3B_RESULTS_METADATA.json`. The complete ignored
artifact is `results/s3b_causal_recovery/causal_recovery.json.gz`; its SHA-256
is `a6f8ce5c5e01325a5ef376df6855687ee342f1504c28cd614d76a7dcbead1beb`.
The hash is an integrity identifier for this execution, not a promise of
byte-identical reproduction.

The runner registered nine slots for each of 96 AC windows, for 864 retained
records. It made 98 AC solver calls: 96 primary hard-target attempts, one
target-free attempt, and one copied-target-free hard attempt. All executed
attempts verified their complete canonicalized IPOPT starting vectors.

## Trajectory summary

| Quantity | S3b result |
|---|---:|
| Completed intervals | 96 / 96 |
| Shifted-primary successes | 94 / 95 |
| Shifted-primary success rate | 98.9% |
| AC solver calls | 98 |
| Generation cost | 360,283.8 |
| Storage cycling cost | 71.7 |
| Renewable curtailment | 239.2 MWh |
| Physical AC losses | 392.5 MWh |
| Maximum voltage-bound violation | 0.0 pu |
| Maximum branch-limit residual | $1.17\times10^{-10}$ MVA |
| Cumulative absolute signpost deviation | $1.12\times10^{-7}$ MWh |
| Outer-solve runtime | 24.8 s |
| AC-solve runtime | 117.9 s |
| Total runtime | 142.6 s |

Realized operating quantities use only the executed first interval from each
accepted controlling window. Target-free and unused predicted actions are not
included. Hard terminal penalties and outer objectives are diagnostic planning
quantities rather than realized operating cost.

## The interval-80 recovery

At interval 80, realized battery energy was 51.3221 MWh and the five-step DC
signpost was 390.3892 MWh. The shifted hard-target attempt received the complete
verified 930-coordinate IPOPT start, but returned `user_limit`. Its terminal
condition and storage recurrence were satisfied, while the maximum active
balance residual was 0.0101 pu, so the frozen accepted-primal gate correctly
rejected it as `unusable_primal`.

The target-free problem then used the same named model-owned start and the same
complete IPOPT starting vector. It solved to `optimal` with active and reactive
balance residuals below $3\times10^{-16}$ pu. Copying that accepted solution by
name into a fresh hard-target problem produced an `optimal` result in 0.72 s,
with active balance residual $1.58\times10^{-14}$ pu and zero reported terminal
deviation. This controlling solve executed the interval-80 action.

The comparison isolates the practical value of the target-free recovery step
within this causal policy. It does not prove that the rejected shifted problem
was physically infeasible, nor does it establish a universal IPOPT defect. As
in the interval-35 diagnostic, the result concerns initialization sensitivity
of this formulation, interface, and solver stack.

## Relation to S3

The original flat-start, hard-target replanned experiment stopped at interval
35. S3b reached interval 35 through its own causally shifted trajectory and
accepted the first shifted hard-target attempt there. This demonstrates that
causal propagation of the preceding accepted AC prediction avoids the earlier
failure in this realization. It is not a matched-state reproduction of the
archived interval-35 NLP, because the preceding controller actions and realized
state belong to the S3b trajectory.

Relative to the complete S3 frozen hard trajectory, S3b generation cost was
20.4 objective units higher, renewable curtailment differed by only 0.0002 MWh,
and physical AC losses were 0.6 MWh higher. These close operating totals are
useful context, but the policies solve different outer problems: S3b replans
from realized state at every interval, while the frozen policy retains one
outer trajectory.

## Architectural conclusion

The experiment closes the immediate evidence gap between retrospective
initialization diagnostics and an online-available recovery sequence. A causal
controller can normally reuse the preceding accepted AC prediction, and the
target-free solve can provide an effective recovery basin when that shifted
hard attempt fails. Perturbation slots remain useful declared fallbacks, but
this realization did not need them.

S4 can now represent `flat_only` and the reviewed shifted recovery sequence as
typed policies. The evidence supports selecting `shifted_with_recovery` for the
frozen reference workflow while retaining explicit attempt records, strict
accepted-primal gates, and termination after exhaustion.
