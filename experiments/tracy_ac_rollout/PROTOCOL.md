# E4 — Six-case matched three-hour AC rollout

Status: implementation checkpoint, 2026-10-12. The owner authorized the prepared
integration, 24-arm runner and non-solving tests, with two primary workers and
one shared race helper using the extended S5 ladder. **No qualification solves
or study execution are authorized.** See [the implemented runner policy](README.md).

Parent: [M11 Gate E4](../../plans/milestone-11-socp.md#gate-e4--six-case-matched-three-hour-ac-rollout).
The original Tracy Stage D and annual AC runs remain held. This is a new
experiment, not a continuation of their execution records.

## 1. Question and comparison

Can iterated three-hour full-physics AC OPF, guided by a retained 24-hour energy
plan and executing one hour per iteration, approximate the outcomes of the
jointly optimized 24-hour AC problem?

Compare three existing/proposed ways to obtain AC operating trajectories:

1. Retained unrestricted joint 24-hour AC solves: independently accepted local
   reference estimates, not certified global optima.
2. Retained AC solves with every battery's hourly real power fixed to a convex
   donor schedule: the completed 18-case transfer experiment.
3. New receding-horizon AC solves with only per-device endpoint energy
   signposts: timing of battery use inside each window remains free.

The fixed-transfer loop certificates explain why some exact hourly schedules
cannot be realized; they do not prove that their three-hour endpoint targets
are unreachable. Do not use a certificate against a donor power vector to
skip its E4 rollout or predeclare rollout infeasibility.

This first study holds the outer plans fixed. Inner windows are reoptimized
from realized state; the outer problem is not resolved. Known input data are
used without forecast error or disturbances. This tests horizon decomposition
and handoff fidelity, not robustness to uncertain forecasts or dynamic grid
stability. Long-horizon scalability remains a hypothesis beyond these cases.

## 2. Matched cohort and retained references

Reuse the existing physical fixtures without resizing, retuning or selecting
new windows. Indices are global hourly input indices, intervals are half-open,
and the source calendar remains fixed UTC−08:00 (not daylight-saving time).

| Case | Global input hours | Initial / final SoC, per device |
| --- | --- | --- |
| Large surplus / energy-neutral | [3308, 3332) | 50% / 50% |
| Large deficit / energy-neutral | [1165, 1189) | 50% / 50% |
| Large deficit / depletion | [1165, 1189) | 60% / 25% |
| Surplus → deficit / energy-neutral | [8580, 8604) | 50% / 50% |
| Deficit → surplus / energy-neutral | [2439, 2463) | 50% / 50% |
| Forced shedding / energy-neutral | [1165, 1189) | 50% / 50% |

The sixth case is the existing counterfactual stress fixture, not another
observed period. Preserve its frozen demand multiplier `7.5416905141946184`
and input-only ENS lower bound `439252.83068650117 MWh`; reconstruct them from
the [E3+ fixture contract](../case118_tracy_2021/E3_PLUS_PLAN.md), not from rounded
report values or a new stress calibration.

For every case use four retained signpost sources:

- `singlenode_dc`;
- `lossy_dc`;
- `socp`;
- `ac_reference`: the matched full-horizon AC trajectory, as an oracle control.

Thus "six-case" means six scenario/boundary treatments, not six controller
policies: **24 rollout arms, 24 executed hours each, 576 controlling windows**
if all arms complete. The full-horizon solves and fixed-power transfers are
retained comparisons, not new reruns. An AC-reference fixed-power transfer is
not added implicitly.

Sources are prepared E3 (`2edc6d3bbeac85bd78f5bf6d9cdc387f851eb0f0`) and E3+
(`13ad35bc5f0e9f8b5d20d54a55d2ab2cd7a26df8`). Reuse existing source inventory,
physical-input matching and historical artifact validation. Before execution,
freeze exact archive hashes, source classifications, ordered storage IDs,
24 input rows and 25 energy boundaries for each arm. Preserve the three E3+
convex donors' numerical flags and owner-approved source-use disposition;
neither that approval nor E4 success upgrades their historical acceptance.
Missing or unusable source states require review, not clipping or substitution.

All arms retain the 27-device fleet, network, loads and shedding permissions,
renewable profiles, generator data, `delta=1 h`, generator curvature `rho=1/3`,
and battery throughput weight `0.01`. AC uses full admittances and the same
voltage, reactive capability and both-terminal rating constraints as E3.

## 3. Rollout and boundary rule

Let local interval k range from 0 to 23; the scenario starts at global hour h.
Set e = min(k + 3, 24). Solve the AC problem on input rows [h+k, h+e):

1. Initialize every battery at its actual realized energy at boundary k,
   aligned by stable device ID. At k=0 use the original fixture's initial state.
2. Impose a hard per-device terminal energy equality at boundary e. For e<24,
   use the retained donor state at that exact boundary, without rounding.
3. Whenever e=24, impose the original fixture's exact final obligation, not
   the donor's numerically perturbed terminal value. Verify the retained source
   endpoint agrees within the existing source tolerance and record both values;
   this explicitly preserves the matched problem's final boundary.
4. Leave every battery's intervening real/reactive dispatch and intermediate
   state free, subject to the original physical constraints. Do not pin donor
   power, impose the full donor SoC path, or add terminal penalties.
5. Audit the complete controlling window before accepting its first action.
   Execute only interval k, retain the accepted energy at k+1 and independently
   check `s[k+1] = s[k] - delta*b[k]` (positive b means discharge).
6. Discard the remaining forecast actions as executed decisions; retain them
   as predictions and permissible sources for the next causal initialization.

The horizons are 22 three-hour windows followed by one two-hour and one
one-hour window. Use the vectorized multistep builder even at T=1; do not
silently switch numerical representation at the tail. Replace inherited
terminal equalities through the existing storage API rather than stacking
an original boundary-24 obligation onto an earlier window endpoint.

Only SoC is carried as physical intertemporal state for these ideal-storage
fixtures. Preflight must inventory any other intertemporal terms and reject
unrepresented coupling rather than assume that SoC is sufficient for future
devices. No implicit ramp, commitment, reserve or new storage physics is added.

Hard short-window targets do not guarantee recursive feasibility. An accepted
first action can leave the next moving endpoint unreachable; retain that as an
outcome if independently established. Solver rejection alone remains numerical
failure, not a certificate of physical infeasibility.

## 4. Narrow prepared-rollout integration prerequisite

The inspected helper inventory, module ownership and proposed typed retained-plan
interface are in [the integration design](INTEGRATION_DESIGN.md). That design is
implemented through private seams and the existing S5 race supervisor, ready
for review but not approved for numerical execution. Public M17 guards remain
unchanged; the new capability boundary is separate and experiment-scoped.

The current public hierarchy rejects both enabled numerical preparation and
every configured sheddable load (`shedding_cost_per_mwh is not None`). E4 must
preserve the original shedding permissions, particularly in the forced-shedding
case; preparation and retained-signpost ingestion alone are therefore not the
complete integration scope. Standalone prepared AC qualification does not
establish correct rollout integration.
Before implementation, review a small ownership/API design for **retained
signposts → prepared AC windows → audited first actions**:

- Reuse M17's identity alignment, window/endpoint selection, realized-state
  advancement, causal-start machinery and record semantics. Inventory the
  concrete reusable helpers and identify the smallest typed extension needed
  to consume a retained plan without running an outer solve.
- Use the existing public vectorized AC builder, `OPFBuild.solve()` path and
  production prepared-solve/verified-start boundary. Reuse numerical coordinate
  transformations, inverse restoration and canonical-layout verification;
  never implement a second scaling or initialization conversion.
- Carry an explicit AC-window preparation policy, separate from the donor's
  historical preparation. A donor is an archived physical trajectory, not an
  active outer layer to which AC options should be broadcast.
- Make an explicit ownership/capability decision for sheddable loads in the
  newly qualified rollout path. Reuse the existing load component's variables,
  served-load P/Q expressions, permissions and costs; do not disable shedding
  to enter M17, duplicate device physics, or simply remove its rejection guard.
  Cover shedding variables and associated cost-coordinate auxiliaries in
  initial and shifted causal starts, recovery, canonical mapping and physical
  restoration, including changing demand and T=2/T=1 tail windows.
- Independently reconstruct served active/reactive load, shedding fractions
  and caps, shedding cost and executed-only ENS. These checks must cover each
  accepted window and the concatenated executed trajectory, not just solver
  status or the forced-shedding lower-bound witness.
- Preserve existing M17 behavior, public defaults and unsupported-input
  rejection outside the newly qualified path. Do not simply remove either
  the preparation or sheddable-load guard. Unsupported combinations must still
  fail before a solve; any enabled path needs a reviewed validation boundary
  and compatibility tests.
- Reuse existing experiment supervision, logs, resource accounting and audit
  helpers. Do not copy a controller, per-device physics or a supervisor into a
  second implementation. Production modules must not import experiments.

This combines the minimum E4 subset of
[M21 §3.5](../../plans/milestone-21-configurable-hierarchy.md#35-numerical-preparation-per-layer)
with integration of the already implemented sheddable-load device capability.
It introduces no new load physics and does not require generic model-pair
registration, an online outer solve,
three layers, or M25 fleet-sum targets. The exact API and module ownership are
a design checkpoint, not settled by this protocol. If the necessary change is
larger, return to the owner with that scope rather than bypassing the boundary.

## 5. Numerical policy and causal initialization

Proposed baseline: reuse prepared E3 AC normalization, exact fixed-coordinate
removal, cycling/shedding cost coordinates and component-first objective
assembly, with its unchanged IPOPT settings and physical acceptance tolerances.
Freeze their resolved values and source identities before launch. No physical
regularizer, relaxed balance, changed economic weight or silent solver retuning.

The implemented [extended race table](README.md#two-primaries-and-one-shared-helper)
must be reviewed before numerical execution. Use a common deterministic initial-window policy across source arms;
later primary starts should reuse the preceding accepted window through the
existing causal shift machinery. Specify extension of the final start column,
SoC adjustment and T=2/T=1 handling; retain the full canonical IPOPT x0 and
its physical source. Do not reuse another arm's completed rollout.

For non-AC donor arms, do not initialize from the full-horizon AC reference or
its future actions. The AC-reference control receives its oracle **SoC
signposts only** and otherwise uses the same causal initialization policy.
This isolates signpost quality from privileged AC network starting points.

Any recovery uses only information available within the current arm: preceding
accepted predictions, declared deterministic starts/perturbations, or an
accepted target-free diagnostic obtained and charged during that window.
Target-free acceptance never advances state; it may initialize a fresh solve
of the original hard-target problem. No hard-to-soft fallback is included.

The existing approved component-first-before-hourly experimental recovery
ordering is the starting point for the design. Explicitly freeze slots, source
eligibility, seeds, assembly phases, first-accepted stopping, and budgets for
short windows rather than implicitly inheriting an unlimited T=24 ladder.
Initialization details and any qualification solves require separate approval;
this document grants neither an additional numerical matrix nor a retry budget.

## 6. Acceptance, accounting and comparison

Reuse independent physical/native restoration audits from prepared E3 and the
hierarchy acceptance machinery. Check full-window nodal P/Q balance, device
boxes/capabilities, voltage and both-terminal ratings, identity-aligned storage
recurrence and endpoint residual, finite native/restored values and objective
accounting. Verify first-action/state continuity independently from forecasts.
Retain explained cycling auxiliary slack as an advisory diagnostic under the
existing policy, separately from physical or unexplained accounting failures.

After each completed trajectory, independently audit its concatenated 24
executed intervals and 25 boundaries against the original full-horizon physical
problem, including the original final obligation. Compute physical cost and
ENS from these executed actions **once**, not by summing overlapping window
objectives or repeated terminal terms. Check the unchanged forced-shedding
input-only ENS witness on a completed trajectory, not by assigning the full
24-hour witness to each three-hour solve. Partial runs retain observed prefix
cost/ENS and missing coverage; do not extrapolate or compare partial totals as
if they represented a completed day.

Report per case and source, with all 24 arms visible:

- Completed intervals, first unresolved boundary and numerical disposition;
  controlling and diagnostic attempts, recovery use and residuals.
- Executed physical cost by component and absolute/relative differences from
  the retained full-AC reference; absolute differences when its denominator
  is near zero. Compare the corresponding fixed-power transfer where accepted.
- ENS/served demand, renewable curtailment, storage power/energy/throughput,
  final target error, generation, physical network absorption and voltage,
  reactive and thermal margins. Show per-device as well as fleet summaries.
- Build/preparation/solve/audit time, total elapsed and cumulative worker effort,
  peak sampled RSS and all failed/diagnostic effort. Historical T=24 timings
  are contextual, not a contemporaneous causal speed comparison.

Different trajectories can have similar quality; exact dispatch agreement is
not required. Negative cost differences from a local AC reference are allowed
and should be audited, not clipped. Do not add a DC-only loss proxy to AC cost.
The AC-reference arm tests short-horizon realization with oracle targets; its
success does not prove global optimality or general recursive feasibility.

Before execution, agree on the engineering tolerances used to describe a
"good approximation" in cost and resilience metrics, separately from mandatory
physical acceptance tolerances. Report continuous differences regardless of
those thresholds. Keep forced-shedding and depletion treatments separate; six
selected cases do not establish annual prevalence or holdout performance.

## 7. Verification and evidence design

Non-solving tests should cover source matching and numerical flags, shuffled
device identities, global/local indexing, target selection, original final
obligations, no hourly power pins, T=3/2/1, causal starts, preparation isolation,
restoration, rejection without state advancement, exact-once accepted
advancement, and executed-only accounting. Explicitly cover sheddable-load
capability validation, shedding-variable start/recovery mapping, independent
served-load P/Q and shedding-cost reconstruction, and ENS counted only over
executed actions; preserve rejection on the unchanged M17 path. Include an
end-to-end synthetic rollout with mocked solver outcomes and a partial failure. Preserve the
existing M17 regression behavior; reusable package tests belong under `tests/`,
experiment fixtures/reporting alongside this protocol.

Review the implementation and determine whether a small bounded numerical
integration qualification is needed. Define any such cases and budgets before
running them; existing standalone qualification is not automatic evidence for
the new window/state-transfer seam, but a broad new matrix is not required by
default.

Retain source/input/signpost identities, environment/settings, attempt role,
initial/target/realized states, full results and native/preparation/start
evidence, acceptance residuals, timing and finalized supervision. Use the
existing archive/checkpoint patterns so interruption preserves completed work
without advancing an unfinished attempt. Historical read-only analysis must
remain possible after a later documentation commit without rebinding old runs.
Use proportionate scientific reproducibility checks, not adversarial-security
infrastructure.

All new raw artifacts will live under this experiment's ignored `results/`
directory; add its local ignore rule with the future implementation. Do not
create outputs now or alter E3, E3+, transfer, diagnostic or Stage D archives.
Keep the protocol, runner tests, selected evidence and final report reviewable.

## 8. Review and execution gates

| Stage | Scope | Current status |
| --- | --- | --- |
| P0 | Review six-case/four-source design, source qualifications and reporting estimands | This planning draft |
| P1 | Review narrow prepared-rollout integration, ownership and compatibility design | Owner authorized implementation; [private seams](README.md) preserve public M17 guards |
| P2 | Authorized implementation and non-solving seam/regression verification | Implemented for review; no numerical solve |
| P3 | Review runner, frozen source manifest, exact recovery/settings and any bounded qualification | Pending; no solves authorized |
| P4 | Owner commit, reviewed compute limits and separate study launch approval | Pending |
| P5 | Execute and independently analyze all attempted trajectories; retain failures | Not authorized |

Before P4, explicitly approve per-attempt wall/iteration/RSS limits, total
worker-time and launch ceilings including recovery/diagnostics, concurrency,
run order, interruption/resume and resource-stop rules. No previous E3 or
annual-study budget is inherited automatically. Require functioning process
and thermal monitoring using existing project practice. A window with no
accepted controlling solve stops that arm; whether other arms continue after
that numerical outcome must be frozen in the execution policy. Infrastructure
failures pause for review rather than being counted as scientific rejections.

Excluded: outer replanning, soft or aggregate signposts, adaptive horizons,
cycle-strengthened SOCP, new physical models, annual
execution, automatic repair of donor schedules, or completion of all M21/M25.
Each is a possible separately reviewed follow-on, not an implicit response to
an unsuccessful E4 arm.

Owner operational amendment after the original telemetry stop: thermal-sampling
failure is advisory, while process/RSS and cumulative compute limits remain
mandatory. Explicit checked continuation may advance preserved checkpoints under
a separately frozen clean execution context without rerunning completed solves
or resetting inherited consumption. See the README continuation contract. This
amendment changes no physical constraint, numerical setting or acceptance gate.
