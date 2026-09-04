# Notes on possible scaling approaches

Status: exploratory notes, not part of the frozen S2 protocol. These began as
contemporaneous pre-completion notes and are now maintained as possible
post-S2 scaling directions rather than an authoritative experimental plan.

The current S2 reference executes a 168-hour outer plan using three-hour AC
windows and a one-hour stride. Each accepted window supplies only its first
action, after which the next AC problem starts from the realized storage state.
The notes below identify possible ways to extend this workflow toward an
8,760-hour study. They are hypotheses to test, not changes to the running S2
experiment or established recommendations.

## Outer-guided temporal sharding

The annual lossy-DC trajectory could be used to select boundaries for shorter
AC trajectory shards. Candidate boundaries include times when:

- storage devices are near the midpoint of their usable SoC ranges, providing
  balanced charging and discharging headroom;
- one or more storage devices are predicted to be full or empty, as a distinct
  strong-boundary comparator;
- storage states are otherwise close to strong DC signposts;
- charging and discharging powers are small;
- branch congestion and voltage stress proxies are low; or
- the remaining outer problem is relatively insensitive to small boundary-SoC
  perturbations.

Near-midpoint SoC is the preferred initial hypothesis because it maximizes
bidirectional energy headroom for absorbing a boundary-state error. Full or
empty storage can create a stronger energy signpost between shards, but it also
leaves little flexibility in one corrective direction. Saturation should
therefore be studied as a comparator rather than assumed to be the best cut.

For multiple storage devices, a candidate score could maximize the minimum
normalized bidirectional headroom across the fleet:

\[
h(t)=\min_i
\frac{2\min(e_{i,t}-e_i^{\min},\ e_i^{\max}-e_{i,t})}
{e_i^{\max}-e_i^{\min}}.
\]

This score is one at the midpoint and zero at either bound. Maximizing the
minimum prevents a large device with ample headroom from concealing a smaller
saturated device. Alternatives such as capacity-weighted mean headroom should
be treated as separate predeclared rules. The final cut-selection rule should
also incorporate available power headroom, network stress, and sensitivity to
boundary-SoC perturbations, and must be frozen before authoritative results are
inspected.

Independent shards with fixed DC initial and terminal SoCs are parallelizable,
but they constitute open-loop endpoint realization. They are not exactly
equivalent to causal execution: a later shard cannot know its realized initial
state until its predecessor has executed.

Possible parallel strategies are:

1. **Independent open-loop shards.** Solve all shards from DC boundary states,
   then measure boundary discontinuities and required reconciliation.
2. **Speculative causal shards.** Solve later shards from predicted states in
   parallel, then accept, repair, or rerun them when preceding realized states
   become available.
3. **Sequential shard boundaries with parallel interiors.** Preserve causal
   state transfer between shards while parallelizing conditional work within a
   shard or evaluating multiple initializations concurrently.
4. **Boundary-state ensembles.** Pre-solve a small deterministic set of nearby
   initial-SoC cases and select or interpolate only after the realized boundary
   becomes known. This requires a separate validity study and must not silently
   substitute interpolation for an accepted AC solve.

Any sharded study should report boundary mismatch, rerun frequency, discarded
speculative work, wall-clock speedup, total compute, and whether its executed
trajectory remains identical to the sequential reference.

## AC window length and execution stride

Window length and stride are separate controls:

- **Window length** determines how much nonlinear AC foresight is optimized.
- **Stride** determines how many planned actions are executed before the next
  state observation and outer/inner update.

Short windows reduce nonlinear model size, memory, and solve exposure, but may
miss upcoming congestion or reactive-power conditions. Long windows provide
more AC foresight and storage coordination, but may increase runtime, memory,
local-solver sensitivity, and recovery use.

A one-hour stride provides the strongest feedback and executes only the first
predicted action. A larger stride reduces the number of AC solves, potentially
almost in proportion to the stride, but executes multiple actions open-loop.
Using a window longer than the stride retains overlapping lookahead while
reducing solve count.

A useful bounded comparison could include:

| AC window | Stride | Purpose |
|---:|---:|---|
| 1 hour | 1 hour | Cheapest and most myopic baseline |
| 3 hours | 1 hour | Current feedback-rich S2 reference |
| 6 hours | 1 hour | More foresight at unchanged feedback frequency |
| 6 hours | 3 hours | Reduced solve count with overlapping lookahead |
| 12 hours | 3 hours | Longer foresight at moderate solve frequency |
| 24 hours | 6 hours | Aggressive annual-throughput candidate |

The six-hour/three-hour configuration is a plausible first scaling candidate,
not a selected default. The matrix should first be tested on the same frozen
week so that changes are attributable to window and stride rather than season
or scenario selection.

## Measurements needed

Every comparison should retain the existing acceptance and audit gates and
report at least:

- completion and accepted-solve fractions;
- recovery frequency and successful initialization slot;
- wall time by outer, controlling, target-free, and perturbation solves;
- peak and safe-boundary current RSS;
- generation and cycling costs, losses, and curtailment;
- voltage, thermal, nodal-balance, and storage-recurrence residuals;
- cumulative signpost deviation and final terminal deviation;
- differences in executed actions and realized SoC relative to the one-hour
  stride reference; and
- for parallel methods, critical-path time, aggregate compute, speculative
  waste, and boundary repair frequency.

Longer windows or larger strides should not be called more accurate solely
because their objective is lower. Accuracy here includes AC acceptance,
realized-state consistency, constraint residuals, and agreement with a declared
reference trajectory. Economic quality and computational throughput should be
reported separately.

## Suggested sequence

1. Retain the completed frozen S2 week as the three-hour/one-hour reference.
2. Re-run the same week for a small predeclared window/stride matrix.
3. Use the annual outer solution to characterize candidate shard boundaries
   without running annual AC execution.
4. Freeze a cut-scoring rule and one reconciliation policy.
5. Compare sequential and sharded execution on the frozen week or month.
6. Attempt 8,760 hours only after completion, resource, and trajectory-quality
   gates are satisfied.

Conclusions from these studies would apply to the frozen Case118 scenario and
solver stack. They would not establish a universally optimal window, stride,
or sharding policy.
