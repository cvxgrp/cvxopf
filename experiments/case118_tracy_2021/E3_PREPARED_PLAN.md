# Fresh E3 comparison with numerical preparation

Prepared 2026-10-08 following owner approval to return to E3 after
[E1 and E2 v2 passed](../m11_socp/E1_E2_V2_REPORT.md). The next run will be a
fresh execution of the same 20 physical problems, not a continuation of the
original stopped run. Runner integration, review, an owner commit and launch
preflight precede optimization. The combined AC ladder introduces one budget
decision below; no increased ceiling is adopted by this plan.

## Preserve the study and historical evidence

Retain the four selected 24-hour windows, their order, hourly duration, device
identities, shedding permissions/prices, generator curvature `rho=1/3`, battery
throughput cost `0.01`, full AC/SOCP admittances and original branch/device limits.
The [original protocol](E3_PROTOCOL.md) and
[boundary decisions](E3_BOUNDARY_DECISIONS.md) remain the physical specification.

Each event has the original energy-neutral leg with per-device initial and
hard terminal SoC equal to 50% of capacity. The large-deficit event additionally
has its separate 60%-initial to 25%-terminal depletion leg. Four formulations
per leg give 20 requested problems, including five AC problems. Target-free
diagnostics remain different problems and never complete a hard-target arm.

Preserve `results/e3/` byte-for-byte, including its STOP marker, nine accepted
arms and all 26 launches. Do not acknowledge its stop, copy accepted results
into the fresh cohort, change its classifications or cross-source resume it.
The new output is `results/e3_prepared/`; do not create binding, invocation,
launch or completion artifacts until actual authorized execution. Stage D stays
held and the isolated investigation worktree stays retained.

## Apply the approved representations

| Formulation | Device normalization | Exact fixed coordinates | Canonical scaling | Cycling and shedding cost coordinates | Objective assembly |
| --- | --- | --- | --- | --- | --- |
| AC | Enabled | Removed | None | Enabled | Component-first ladder, then hourly ladder |
| SOCP | Enabled | Removed | `joint5` | Original | Hourly |
| Lossy DC | Not applicable | Removed | `joint5` | Original | Hourly |
| Copper plate | Not applicable | Removed | `joint5` | Original | Hourly |

Use the public numerical-preparation machinery through `OPFBuild.solve()` and
the existing verified AC start boundary. No experimental numerical
monkey-patching, alternate solver binary, dependency addition, network
strengthening, voltage/reactive penalty or physical objective-weight change.
Library defaults remain unchanged.

SOCP uses the approved stock QDLDL settings: relative gap `1e-6`, absolute gap
and feasibility `1e-10`, 5,000 iterations, one thread and minimum termination
step `1e-8`. Reduced gap absolute/relative, feasibility and infeasibility
absolute/relative tolerances remain `1e-10`; reduced k/t ratio is `1e-6`.
Retain the remaining resolved stock settings explicitly. DC keeps its original
`1e-10` gap/feasibility controls, 5,000 iterations and one thread rather than
inheriting SOCP's convergence settings.

AC keeps its existing adaptive-barrier settings, `tol=1e-7`, exact Hessian,
zero bound relaxation, cold solve and verbose logs. Do not add an iteration
override or translate SOCP tolerances into IPOPT tolerances. Record effective
wrapper options, installed adapter/native versions and extension hashes.

## Run the two AC recovery phases in order

Each phase retains the original ten slots: primary, three causal perturbations,
flat, target-free, copied target-free and three target-free perturbations. Keep
the original scales, seeds, physical start transformations, leaf projections,
SoC reconstruction and complete handoff. Stop at the first independently
accepted solution of the requested hard-target problem.

Run the complete component-first phase before any hourly fallback. If its
target-free slot fails, skip its four dependent slots and proceed to the hourly
phase. If its full hard-target ladder fails, likewise proceed to hourly. The
hourly phase has its own target-free diagnostic and dependent source; do not
reuse a component-first target-free result as a new, undeclared hourly start.
Both phases retain the same original causal physical point and perturbation
recipe, with start mapping verified for each phase's canonical layout.

Interrupted attempts retry the same phase and slot only on an explicitly
authorized same-source resume. An unfinished or unsupervised archive cannot
advance a slot or supply a seed. Timeout or numerical rejection consumes its
slot; monitoring, RSS, worker or implementation failures stop for review.
Operator stop and budget exhaustion do not authorize entering the next phase.

## Budget decision before implementation and launch

The old ladder permitted at most 65 ordinary calls: 15 convex and 50 AC.
The two-phase ladder permits at most **115 ordinary calls**: 15 convex and
100 AC, including target-free diagnostics. Many arms may accept before using
their full ladder; this is a structural upper bound, not a runtime forecast.

| Control | Proposed fresh-run value |
| --- | ---: |
| Sampled worker RSS ceiling | 16,384 MiB, unchanged |
| Wall ceiling per attempt, including preparation and archival | 1,800 seconds, unchanged |
| Cumulative supervised worker wall budget | 43,200 seconds, unchanged |
| RSS polling interval | 1 second, unchanged |
| Launch ceiling, including interruptions | 120, pending owner approval |

The proposed 120 launches allow all 115 ordinary slots plus the original five
interruption allowances. This increases only the launch ceiling, from 70;
the 12-hour worker budget stays independent and may end the study before every
ladder is exhausted. Clip the final attempt to remaining worker time and charge
termination overhead. An alternative is to retain 70 launches and accept that
the expanded ladder can exhaust that ceiling before all arms are disposed.
Neither choice silently extends the total budget, invents recovery roles or
reopens an exhausted arm.

## Acceptance and independent replay

Require native full convergence (`Solved` for CLARABEL, IPOPT status 0) and
complete, finite restored physical primals. Keep the original independent
network/device/ENS, box, capability, storage recurrence and endpoint gates.
SOCP retains the complete lifted-network audit with its existing energy and
fraction thresholds; voltage recovery remains a separate diagnostic.

Use the approved scale-aware SOCP canonical-to-physical total-cost gate
`1e-4 + 1e-6*abs(physical total cost)`, separately from unchanged named
component-cost checks. DC retains its existing accounting gates. AC retains
approved cycling-slack warnings and separately checks unexplained total-cost
error after excluding explained cycling slack. Cycling gaps are warnings,
not acceptance failures; unexplained mapping, physical or accounting failures
remain failures. Freeze the exact implementation and thresholds in the fresh
protocol before launch, following the existing production qualification readers.

Each attempt records formulation, event, leg, global hour interval, phase,
slot/role, preparation policy, source dependencies, physical input digest,
complete physical/canonical/reduced start, native vectors/status, restoration
maps, economic diagnostics, results, supervision, logs and resources. Native
and physical costs remain distinct. Replay reconstructs maps and restores
retained vectors without optimization; it must not trust an acceptance label.
Manifest and retained-archive counts must agree. One-sided, unfinished,
rejected, timed-out and target-free outcomes remain explicit.

## Implementation and review checkpoint

Keep changes inside the Tracy experiment and its focused tests, plus the
relevant milestone status. Reuse the existing supervisor/journal and physical
start/audit helpers, extending them with an explicit prepared-cohort selection
and phase-aware requests. Preserve the historical protocol rather than silently
changing its execution contract. Add a fresh protocol and independent native
restoration reader; do not change production numerical transformations.

Non-solving tests must cover policy selection, complete ladder order and skip
rules, phase-specific source dependencies, interruptions, missing supervision,
resource clipping, stop/resume, artifact integrity, native/physical restoration,
full native-status gates and advisory cycling warnings. Sentinel-guard optimizer
entry points. Use synthetic subprocess rehearsals and existing analytic fixtures,
not unapproved Tracy or historical numerical test reruns.

Obtain scientific review from `cvxopf-review`, correct material findings and
leave changes unstaged for the owner. After the clean reviewed commit, check
actual process/RSS access, AC power and thermal telemetry before invocation
and each launch. Stop on missing required monitoring rather than bypassing it.
Bind the fresh source/environment, inputs, policies, resolved solver settings
and accepted budget before the first worker. No tracked edits while active.

Report accepted arms separately from disposed outcomes and archives, with
resource usage and censoring. Subsequent cost/trajectory comparisons require
eligible accepted arms; numerical failure is not evidence of infeasibility,
and SOCP acceptance is not AC realizability or a certified optimum.
