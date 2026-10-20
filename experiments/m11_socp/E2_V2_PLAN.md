# Prepared matched AC/SOCP comparison — E2 v2

The owner requested this refresh on 2026-10-08 after E1's baseline/prepared
regression passed all 18 calls. This checkpoint prepares the runner; execution
requires review, a clean owner commit and separate launch approval. Historical
[E2](E2_REPORT.md), its inputs, tests and artifacts remain unchanged. No E3,
Stage D, hierarchy, reference regeneration or production-default change.

## Same physical cases, current representations

Reuse exactly `tests/socp_matched.py`'s four fixtures: Case9 and Case14
single-step, and the mixed-device two-bus T=3 vectorized and stepwise cases.
The original matching guard, containment, direct AC physics, device constraints,
objective-component/duration/terminal checks, meaningful storage/shedding checks
and signed AC-minus-SOCP comparison remain in force. Reuse the original physical
input digest, not an identity that silently includes different physics.

AC uses normalized device limits, exact fixed boxes and cycling/shedding cost
coordinates. Only mixed vectorized uses `objective_assembly="component_first"`;
single-step and stepwise keep hourly because component-first is unsupported
there. SOCP uses normalized devices, exact fixed boxes and `joint5`, retaining
original economic coordinates and hourly assembly. Record each actual fixed
count, physical flat start, canonical start/layout, numerical policy and resolved
solver settings. There is no additional baseline sweep; historical observations
remain context rather than contemporaneous performance controls.

Eight public `OPFBuild.solve()` calls, AC then SOCP within each fixture, in
the original fixture order. Each pair has one fresh supervised worker. Stop
after any failed native solve, independent restoration/physical/accounting check,
pair comparison, resource limit or infrastructure failure. Check AC containment
before the SOCP call. No retry, alternate start, fallback ladder or tuning.
The selected vectorized AC representation applies the approved component-first
first-choice policy; this small regression has no recovery ladder to expand.

## Settings, acceptance and evidence

Retain E2's AC `max_iter=500`, `max_cpu_time=60`, `tol=acceptable_tol=1e-8`
and SOCP `max_iter=200`, `time_limit=20`, gap/feasibility tolerances `1e-9`.
Require native IPOPT status 0 and CLARABEL `Solved`, in addition to the original
independent checks. One numerical thread; stock QDLDL for SOCP, no warm starts.
These small-fixture settings do not replace the qualified Tracy settings.

Physical gates remain power `1e-4`, energy `1e-5`, voltage-squared/cone `1e-6`,
fraction `1e-7`, and recovery thresholds `1e-5`. Component accounting remains
`rtol=1e-8`, `atol=1e-6`. Signed gap and mixed-assembly agreement allowance remains
`2e-5 + 2e-6*abs(comparison cost)`; never clip a reversed gap or compare golden
nonunique dispatch arrays. Recovering the optimized SOCP point is diagnostic,
not an AC-infeasibility or exactness requirement.

Read-only replay reconstructs the canonical maps, starts and cost coordinates
from the current bound inputs, restores retained native vectors, compares public
physical output, and repeats the original independent checks. Native/physical
AC cycling excess and non-cancelling slack use the approved advisory limit
`1e-4 + 1e-6*abs(physical cycling cost)`, never reject merely for that warning.
Unexplained total accounting uses `1e-4 + 1e-6*abs(physical total cost)` after
subtracting explained cycling slack. Canonical objective reconstruction is
checked separately at `1e-7 + 1e-12*abs(native cost)`. No certified dual bound.

Retain complete result/native/preparation evidence for each attempted arm,
including warnings and inclusive timing; preserve one-sided results. A pair is
protected by an immutable AC-arm archive and its hash manifest before SOCP
starts, so interruption of the second solve cannot erase the finished first
arm's restoration, timing, warnings or containment evidence. Read-only replay
reports that checkpoint even for an unfinished or timed-out pair. A pair is
accepted only after both arms, finalized normal supervision and all comparisons
pass. Count finalized pairs, accepted pairs, optimizer calls, result/native
archives and completion manifests separately. No unfinished pair may advance.

Fresh ignored output: `results/e2_v2_001`. Refuse existing directories and source
or environment drift. Bind the reviewed clean commit, numerical-library versions
and native binary hash, public inputs, helpers and protocol before solving.

## Resources and handoff

Four launches, at most eight optimization calls. Each whole pair worker has a
180-second wall ceiling, 16 GiB sampled RSS and 0.1-second polling. Cumulative
worker allowance is 720 seconds; the eight native solver limits total 320 seconds.
Count failed calls and partial effort. A worker killed before completion has an
unknown optimizer-call count, reported separately with an upper bound of two
calls per censored pair; it is never reported as zero work. Check process/RSS access, AC power and
thermal telemetry before invocation and every launch. Missing monitoring stops.

Finish for owner review; no automatic M11 closure or later-study launch. Timings
are observations, not a repeated speed benchmark.

```sh
uv run --extra dev python -m experiments.m11_socp.e2_v2 --preflight
# After review, clean commit and separate execution approval:
uv run --extra dev python -m experiments.m11_socp.e2_v2 --authorize-execution --commit <full SHA>
uv run --extra dev python -m experiments.m11_socp.e2_v2 --status
```
