# Bounded convex cost-coordinate qualification

Implementation checkpoint, 2026-10-07. The owner requested the next convex
qualification stage after accepting bounded AC results and advisory cycling-gap
semantics. Review/commit precede separate numerical execution approval. No solve
is authorized by this document or by non-solving preflight/tests.

## Question and matrix

Does the exact cycling/shedding cost-coordinate substitution preserve useful,
accurately accounted convex dispatch across SOCP, lossy DC and copper plate?
Does its behavior depend on the already qualified numerical preparation?

For each formulation, run these eight fixtures in the order shown:

| Fixture | Global input-hour interval |
| --- | --- |
| Original Tracy deficit T=3 | `[1165,1168)` |
| Original Tracy surplus T=3 | `[3308,3311)` |
| Original Tracy ramp-up T=3 | `[8580,8583)` |
| Original Tracy deficit T=6 | `[1165,1171)` |
| Existing device-bearing Case9 T=3 | fixture steps `[0,3)` |
| Forced-shedding Tracy T=3 | `[1165,1168)` |
| Forced-shedding Tracy T=6 | `[1165,1171)` |
| Forced-shedding Tracy T=24 | `[1165,1189)` |

Every fixture has four arms, in order: preparation-disabled/original coordinates,
preparation-disabled/cost coordinates, prepared/original, prepared/cost coordinates.
This is 32 arms per formulation, 96 total, ordered SOCP, lossy DC, copper plate.
Every arm runs once regardless of earlier numerical outcomes; no retry, best-of
selection, resume, outcome-dependent tuning or fallback solver. No AC historical
starts are imported: CLARABEL is cold-started with caching/warm starts disabled.

The prepared SOCP profile is device-limit normalization, exact-fixed-box removal
and joint5 canonical scaling; the prepared DC profiles are exact-fixed-box removal
and joint5 without device normalization. These are the existing qualified profiles,
not a new factorial investigation of every preparation primitive. Formulation
settings are unchanged from `numerical_preparation.fixture`: stock CLARABEL 0.11.1,
QDLDL, one thread, 5,000 iterations. SOCP uses `tol_gap_rel=1e-6`,
`tol_gap_abs=tol_feas=1e-10` and the tested `1e-8` minimum-step cutoff; DC uses
`tol_gap_abs=tol_gap_rel=tol_feas=1e-10`. Record all resolved settings, including
reduced/default settings. Do not translate AC/IPOPT settings into CLARABEL.

## Exact mathematical inputs and transformations

Reuse the original E3 selected fleet and neutral Tracy SoC boundaries, existing
Case9 device fixture, hourly delta and unmodified costs. Forced demand uses the
same input-only counterfactual recipe as the AC stage: multiply real/reactive
demand by one interval-wide `max(1,1.1*max(U/D))`, where U includes generator
Pmax, renewable availability clipped by rating and storage power rating.
Supply, VOLL, shed caps, storage, network and terminal policy remain unchanged.
The conservative witness ignores battery energy/reactive/network limitations;
its strictly positive deficit proves necessary shedding, not feasible dispatch.
These are severe stress cases, not observed Tracy demand.

Within each formulation/fixture all four arms must have exactly the same physical
input hash. The substitutions are signed `u=delta*aging_weight*b` and
`v=delta*VOLL*demand*shed_fraction`, with exact inverse maps in every constraint.
They preserve economics: cycling contributes `sum(abs(u))`, shedding `sum(v)`.
Native DC leaf boxes are scaled by the same positive diagonal map; preserving
only explicit constraints would not preserve the DC feasible set. Independently
check the transformed bound arrays as well as constraint arguments.
Generator costs and lossy-DC loss regularization are unchanged; CVXPY owns all
auxiliary construction. Positive finite scales and DCP are required. Compare
objective and every constraint argument at a deterministic non-solving point;
this point is an algebra check, not a feasible start supplied to CLARABEL.

## Audits and warning policy

Require native `Solved`, not `AlmostSolved`, unchanged original-unit network,
device, SoC and component-expression checks, exact primal restoration/projection,
canonical data/map binding, finite evidence, resource-compliant finalized
supervision and matching completed manifests. Independently reconstruct original
canonical objective and native substitution/CVXPY constants within
`1e-7 + 1e-12*abs(restored native objective)`.

Reconstruct cycling, shedding, generation and (where present) DC loss costs.
Keep `1e-4 + 1e-6*abs(physical component cost)` for component comparisons.
Generator, shedding and loss accounting remain hard checks; report native total,
physical total and total discrepancy separately. Subtract only the independently
identified cycling gap when checking unexplained total discrepancy. The cycling
component and absolute auxiliary-slack threshold are **warnings only** under the
owner decision: no rejection directly or through the total check. Retain signed
slack, L1 slack, min/max slack, limits and warning counts. Do not reinterpret an
unexplained discrepancy or nonfinite evidence as a cycling warning.

Identify the single positively priced automatic abs auxiliary from canonical
layout AND objective coefficients; constraint-only auxiliaries are not cycling.
Reject unsupported cost structures rather than guessing. Force-shedding arms
must actually shed materially and satisfy the input-only hourly/ENS lower bound.
SOCP audits establish its stated lifted problem's feasibility, not AC realization,
rank-one voltage products, certified lower bounds or relaxation tightening.

## Comparisons and decision

For every available converged candidate retain physical costs, component gaps,
ENS, absolute battery throughput, native residuals/gap/iterations and restoration
diagnostics. Publish identity-aligned complete generator, battery, renewable and
load arrays, SoC including initial/final boundaries, and all within-group deltas.
Fleet plots must distinguish absent DC reactive channels and warn that fleet
sums can conceal device counterflows. Include physically/accounting-rejected
candidates descriptively, clearly labeled; never count them as accepted.

Report same-formulation total-cost/ENS differences against descriptive
`1e-4 + 1e-6*max(abs(costs))` and `1e-4 MWh` reference thresholds. These are
comparison flags, not returned-solution rejection gates. Nonunique trajectories
need not match. Do not enforce cross-formulation cost equality: their network
models differ. Report acceptance/warning throughput and missing/rejected outcomes
without turning numerical rejection/timeout into proof of infeasibility.

Per-formulation/treatment qualification requires every declared arm accepted and
measured generation/shedding cost activation in Case9/forced cases; otherwise
report not-qualified or incomplete. Report pair discrepancies independently even
if that bounded disposition passes. Owner review decides whether economically
material disagreement warrants more investigation. Success alone never enables
defaults, production promotion or a project-wide objective-cost rule.

## Evidence, resources and execution boundary

Bind full reviewed clean commit, experiment and repository sources, input hashes,
installed numerical Python/native hashes, protocols, scales, canonical sparse-data
fingerprints, layouts, resolved policies and settings. A process-local temporary
stock-adapter observation wrapper checks the actual delivered data fingerprint
and archives native output without changing the call; it is not a production
solver patch and must never be promoted. No sources change during execution.

Use a fresh ignored `results/qualification_001` root, never overwrite/resume.
Retain request/launch/phase/resources/log/native/preparation/audit/supervision and
completion records; completed manifests must agree with retained archives.
No supervision means unfinished and unadvanceable, regardless of live PID or
completion label. A supervised limit/interruption in the atomic archive/manifest
publication gap preserves a partial hash but cannot accept/replay that archive.
Timeout consumes its arm and continues; operator stop, RSS, monitoring or worker
failure stops the invocation. A terminal matrix is not automatically qualification.

One serial worker, one numerical thread, **96 launches maximum, 180 seconds per
worker, 17,280 cumulative worker-seconds, sampled 16 GiB RSS ceiling**. These are
proposed bounds for owner review, not execution approval or estimated runtime.
Verify read-only ps/RSS permission, AC power and actual macmon temperature before
first/every launch. STOP and process signals preserve finalized supervision.
Independent status replays evidence without solving; terminal analysis writes only
fresh immutable summaries/plots. New execution needs a reviewed clean commit and
separate owner approval. Production extraction, PYPOWER test extension, prepared
hierarchies, E3 and held Stage D remain outside this checkpoint.
