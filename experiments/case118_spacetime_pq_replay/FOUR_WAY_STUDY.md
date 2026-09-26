# Fresh 120+6 comparison of time and spatial vectorization

The owner selected four fresh replays on 2026-09-23. This supersedes the
single-new-condition scope of `PLAN.md` for this new run only. The original
studies and the interrupted combined replay remain immutable.

Run the same frozen 120 historical primary-winner and six historical helper-winner
windows in each condition: `none` (stepwise, spatial off), `time_only` (vectorized,
spatial off), `spatial_only` (stepwise, spatial on), `both` (vectorized, spatial on).
All use sparse P/Q variables, CVXPY 1.9.3 / sparsediffpy 0.6.1, and explicit
`SPARSE_DENSITY_THRESHOLD=0.0` in every fresh worker before any model is built.
This is the study's execution policy; do not change library import behavior or
project-wide defaults. Record the effective threshold in every attempt.

There are 504 window evaluations plus helper attempts under the original
SpeculativeSupervisor/WindowRace policy. Run conditions sequentially in the order
above; within each retain two primaries on distinct historical shards and one
shared helper. Preserve eligibility, budgets, audits, cancellation, memory policy,
numerical solver options, perturbation seeds/draw order and absence of a primary
timeout. Preserve every failed/canceled/rejected attempt. Recoverable per-attempt
errors retain the original helper policy. Stop the study on an incomplete
condition or unhandled supervisor failure; no restart or replacement sample.

The sample SHA256 remains
`f950b14b061b60a582526d5a4d30ac02124ef2f1a62ddc41e6c74343f8750ddb`.
Preserve its strata, weights, and random window order. Recheck all 655 unique
direct historical references. Freeze the physical inputs, causal preceding
controller starts, initial SoC and terminal targets. Never feed one window's new
result to another. Within a window, preserve use of its own newly computed
target-free helper solution. Unpack helpers only in time-vectorized modes;
stepwise helpers already use historical names. Record representation separately
from the unchanged historical request hash. Verify exact primary named starts
and complete model coordinate mapping; inspect native auxiliary starts as well.

Use a new root `results/case118_four_way_120plus6_dense_control`, with four
condition directories and a common binding. Existing directories are rejected.
Bind the owner-reviewed clean commit, source hashes, sample, package inventory,
native binaries, thread environment, and execution configuration. Check sources
in every worker. Check process-monitoring and thermal permissions before launch,
and obtain a current external-fan confirmation. Thermal samples must arrive
before each condition admits solves; retain process snapshots and observations.
Keep source-bound files frozen until all conditions stop.

Implementation reuses the original supervisor and worker, adding explicit
representation controls with backward-compatible defaults. Tests must cover
all four starts, stepwise/vectorized helper mapping and replay starts, config
propagation to subprocess requests, source/configuration provenance, output
isolation, and paired analysis including incomplete and changed-winner cases.
Prepare retained primary requests without solving, including canonicalization
to a mocked native boundary for hour 6047 in all four modes.

Compare fresh conditions with each other; historical results are context only.
Report original-weighted primary means and quantiles, paired geometric speedups,
build/initialization time, winner solve phase and window latency. Window latency
retains the original boundary: primary launch to winner reaping, excluding final
loser cleanup. Total study wall time includes cleanup. Build time
includes reconstructing the historical initialization model and constructing the
selected representation; report the initialization subphase separately. A solve
phase includes canonicalization, start persistence, and solver work; it is not
pure IPOPT time. Separate the six historical helper-winner windows, regardless of
their new winner. Account for all attempts, helper launches, cancellations,
winner changes, sampled RSS, and telemetry. Check numerical acceptance, objective
changes (flag absolute relative changes above 0.1%), and active/reactive dispatch
and state differences. Do not infer identical operating points from similar costs.

Primary factorial contrasts: none→time_only and spatial_only→both measure time
vectorization at each spatial setting; none→spatial_only and time_only→both
measure spatial batching at each time setting. Report their interaction and
none→both overall comparison. Use identical matched windows and weights; partial
results are explicitly descriptive of their observed subsets. Changed winners
and helper activity are operational outcomes and must accompany timing claims.
Concurrent solver competition and sequential condition order remain limitations;
this is one replay per condition, not a replicated microbenchmark.

Readiness requires all 126 windows and all attempts accounted for in each mode,
verified evidence hashes, acceptance audits, and scientific review. Retain the
existing independent scientific-review and owner-commit gates: prepare and test,
obtain clean independent review, then owner review/commit and launch authorization.
The owner stages/commits/pushes. Approval to prepare these four replays does not
waive those gates. No native study solves run during preparation.
