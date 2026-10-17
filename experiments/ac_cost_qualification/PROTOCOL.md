# Bounded AC cost-coordinate qualification

Implementation only, authorized 2026-10-07 after the committed four-arm
diagnostic. Execution requires owner approval and a reviewed clean commit.
This is step 2: convex qualification is step 3 and precedes production extraction.
No production changes, defaults promotion, E3 launch/resumption, Stage D work,
dependency installation, or historical artifact edits are included.

## Ordered matrix

Every arm uses combined device normalization and exact-fixed removal, vectorized
AC and unchanged IPOPT options from `numerical_preparation.fixture`. The coordinate
treatments are original, cycling only, shedding only, and both. They preserve
weights and all constraints by exact affine substitution; CVXPY owns abs
canonicalization. No explicit epigraph or modified economics is introduced.

| Arms | Inputs | Initialization | Coordinates |
| --- | --- | --- | --- |
| 01–04 | Original Tracy T=3 `[1165,1168)` | stock | original, cycling, shedding, both |
| 05–06 | Same original Tracy T=3 | retained qualification call 024 physical primal | original, both |
| 07–08 | Same original Tracy T=3 | retained qualification call 025 physical primal | original, both |
| 09–10 | Original Tracy surplus prefix T=3 `[3308,3311)` | stock | original, both |
| 11–12 | Original Tracy ramp-up prefix T=3 `[8580,8583)` | stock | original, both |
| 13–14 | Original Tracy deficit prefix T=6 `[1165,1171)` | stock | original, both |
| 15–16 | Existing device-bearing Case9 T=3, generation-cost coverage | stock | original, both |
| 17–18 | Supply-limited Tracy T=3 `[1165,1168)` | stock | original, both |
| 19–20 | Supply-limited Tracy T=6 `[1165,1171)` | stock | original, both |
| 21–22 | Supply-limited Tracy T=24 `[1165,1189)` | stock | original, both |

Tracy uses existing verified inputs and the original E3 selected fleet and neutral
initial/terminal SoC (0.5 capacity). Prefixes impose their own neutral terminal
boundary. Imported historical starts reset only exact fixed Pg/ND entries and
the initial SoC boundary through the existing complete-start rule; free physical
coordinates are not clipped or perturbed. Historical starts are declared independent initialization arms, not
IPOPT warm starts or a recovery ladder. Every arm is run once irrespective of
earlier numerical outcomes. No best-of selection or fresh-result-derived start.
Matched groups must have identical physical-input and physical-start hashes.

## Tracy forced-shedding variants

These are labeled counterfactual stress variants, not the original observed
Tracy sequences. For each interval compute the conservative hourly supply bound
`U = sum(generator Pmax) + sum(min(renewable availability, rating))
+ sum(storage apparent-power rating)`. Select one demand multiplier
`alpha = max(1, 1.1 * max_t(U_t / D_t))` using inputs only, before any solve.
Multiply explicit active and reactive demand frames by the same alpha, preserving
power factor, time variation, VOLL, shed caps, devices, costs, supply inputs,
network and terminal policy. Retain original and modified demand, the resolved
multiplier, and the fully resolved input hash.

The fixtures have no negative demand or HVDC and the AC network is passive.
Ignoring battery energy limits, reactive constraints and network losses makes U
an upper bound, not an achievable dispatch. Verify strictly positive `D'-U`
in every hour and a material integrated ENS lower bound before execution. Thus
every feasible result must shed load. This does not prove AC feasibility or
determine the economically optimal shedding schedule. The audit checks positive
ENS and ENS at least that bound, allowing the unchanged 1e-4 MWh energy gate.

## Evidence and acceptance

Bind full clean commit, working-source hashes, installed numerical packages and
binaries, raw input hashes, this protocol, fully resolved solver options, policy,
physical starts and coordinate maps. Verify/pin historical call 024/025 completed
archives and supervision; never invoke their old source-bound runner. All new
records live in a fresh ignored `results/qualification_001` directory, created
only at execution. No resume, overwrite, cross-source replay, retries, settings
changes or automatic budget extensions.

Retain request, launch, phase, fresh resource samples, worker log, verified complete
canonical IPOPT x0/layout, native primal/multipliers/status/objective, preparation
evidence, restored physical dispatch, economic audit, supervision and completion
hashes. A supervised timeout/interruption between atomic result publication and
completion publication leaves an explicitly labeled partial archive: preserve
its hash without accepting/replaying it. A timeout consumes its arm and continues;
other stopping dispositions remain stopping dispositions. Missing completion is
fatal for an otherwise successful worker; a completed manifest missing its
archive is always inconsistent. Retained archive/manifest counts are separate
from validated completions and accepted arms. Independent non-solving replay reconstructs the canonical layout and
objective at the retained primal, physical dispatch and costs, exact maps, and
archive/resource consistency. Economic replay evaluates only the smooth canonical
objective expression and its objective-only gradient; it does not instantiate
network Jacobian/Hessian machinery. A worker without supervision is unfinished even
when its archive claims acceptance. Completed manifests must match all retained
solver/start/result records. Only supervised, within-limit status-0 results with
all physical, restoration and economic checks qualify individually.

Keep the existing original-unit physical/component-expression checks unchanged.
Add prospective native-versus-physical total and component gates of
`1e-4 + 1e-6*abs(physical cost)`. Reconstruct cycling canonical cost from its
automatic auxiliary, shedding cost from its original or cost-valued coordinate,
and generator cost from the remaining canonical objective. Report each component
discrepancy, negative cycling slack, total auxiliary excess and maximum coordinate
cost slack so a large generation/shedding total cannot hide cycling error.
Require independent canonical-objective reconstruction within
`1e-7 + 1e-12*abs(native objective)`; no component cancellation may mask failure.
Report native optimality diagnostics and IPOPT exit/iterations where available;
accounting and physical feasibility alone are not optimality certificates.

No matched-cost or trajectory-equality acceptance gate for nonconvex AC: report
physical costs, ENS, battery throughput, component activation and identity-aligned
trajectory differences for every available candidate, including accounting rejects.
Do not claim global optimality, universal benefit, or that remaining cycling is
necessary. Missing/timeout/rejected outcomes remain explicit. A coverage question
is incomplete if its needed accepted candidate or active cost component is absent;
Case9 generation and forced Tracy shedding coverage are verified from outcomes,
not assumed from fixture labels. Matrix disposal is not treatment qualification.

## Resource and authorization boundaries

One serial worker; one numerical-library thread. Maximum 22 launches, 180 seconds
per worker including construction/solve/audit, sampled 16 GiB RSS, 3,960 cumulative
worker-seconds. Use existing supervisor, STOP handling and atomic artifacts.
Check process/RSS permission, AC power and actual macmon temperature before first
and each subsequent launch. Stop on operator interruption, missing monitoring,
RSS limit, worker/infrastructure failure or exhausted budget; numerical rejection
and wall timeout consume their arm and continue the declared matrix. Timeout is
not proof of infeasibility. Sources may not change while workers are active.

`--preflight` assembles/checks inputs without optimizing or creating a run directory.
`--authorize-execution --commit <full SHA>` is a separate explicit launch command
after owner approval. `--status` independently replays evidence without solving;
`--analyze` writes a new immutable summary only after terminal invocation evidence.
Review implementation before execution and results afterward. No automated action
follows a favorable result.
