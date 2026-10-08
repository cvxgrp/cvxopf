# Bounded numerical preparation qualification

Proposed 2026-10-06, paired with the
[typed API design](../../plans/numerical-preparation-api.md). This freezes the
25-call matrix in the [approved transfer plan](../../plans/socp-conditioning-integration-transfer.md).
It is not execution permission or evidence of qualification. No runner,
production transformation, fresh execution directory, or numerical result is
created at this design checkpoint.

## Identity, inputs, and execution gate

Preservation baseline: `eb511da3f` on `socp`. Actual qualification must bind its
later reviewed clean implementation commit, complete source hashes, this
protocol's hash, resolved inputs, package versions/extension hashes, full solver
settings, and numerical-preparation policy. Stop preflight on an unexplained
input/version/settings difference; do not silently replace a control. Initially
use the installed stock CLARABEL 0.11.1 and existing project IPOPT environment.
No MOSEK/COPT, custom solver binary, Rust rebuild, or dependency installation.

Pinned historical references, SHA-256:

| File | SHA-256 |
| --- | --- |
| `plans/socp-conditioning-evidence-inventory.json` | `97efdf91c7494765624638571eb8a3d0097c4e3d51b197d60b06ca134f7d0371` |
| `experiments/case118_tracy_2021/e3_selection/selection.json` | `54c4879ca0cafaa126b498c25e069688432da24acdbd04c29e4903005f78572e` |
| `experiments/case118_tracy_2021/results/e3/binding.json` | `a423375c2041e929f5425319d6d0b36a605bdcbddb82bffc74a719b757edccfc` |

Historical inspection uses the preserved hash-verifying artifact resolver, not
legacy source-bound experiment loaders against modified production sources.
Read-only input assembly uses `e3.verified_inputs()` and `e3.kwargs_for_arm()`
with the original binding's ordered storage IDs and arm definitions. Neither
helper invocation authorizes an E3 run. Freeze the resolved mathematical-input
digest for each fresh arm before launch; matched arms differ only in declared
preparation. Retain original raw-data hashes and their availability checks.

Owner approval of this design is followed by implementation and review; numerical
launch additionally requires monitoring permission preflight and owner approval
of the implementation/qualification execution checkpoint. Keep one immutable
binding and a new ignored root `results/qualification_001`, absent until then.
No edits to source/protocol during workers. Use the established supervisor
patterns: request, launch, phase, resources JSONL, worker log, supervision,
completion, independently recomputable audit, archive hashes, and invocation
start/finish records. An unsupervised attempt remains unfinished, never accepted.

One serial worker and one solver/native-library thread; set and record thread
environment before process start. Fixed ceilings: 25 launches/numerical calls,
180 seconds per worker (including preparation/solve/audit), 16 GiB sampled RSS,
4,500 cumulative worker-seconds. Stop or battery pause interrupts conservatively
and records the disposition; it does not grant a replacement call. No retries,
alternative starts/solvers, horizon extensions, gate changes, or recovery runs.
Use read-only process checks plus fresh logs/resource samples to corroborate
health. Record time/RSS limits and failed, interrupted, or missing-worker outcomes
without calling them infeasible. No ETA from solver iterations.

## Deterministic device-bearing Case9 fixture

Start from `cvxopf.testcases.case9()` at the execution commit, retain network,
base MVA, gencost and reactive limits, and use explicit
`gen_from_matpower(case["gen"], case["gencost"])` devices. Override only
generator row 1 (bus 2) to
`p_min_mw=p_max_mw=50` and row 2 (bus 3) to
`p_min_mw=30`, `p_max_mw=30+1e-7`. Row 0 (bus 1) is unchanged. Record both source
hash and fully resolved numeric inputs rather than relying on a case nickname.

Explicit loads replace the case Pd/Qd fallback: bus 5 `(90 MW,30 MVAr)`, bus 7
`(100,35)`, bus 9 `(125,50)`, IDs `load5`, `load7`, `load9`. All are sheddable,
with maximum fraction 1 and shedding cost 10,000 cost units/MWh. Use `delta=1`
hour. For T=3 multiply both P and Q by `[1,1.1,0.9]` in time order; T=1 uses
the first row. Reactive load remains present in the fixture even for DC, which
ignores it according to its existing formulation contract.

Add these devices with engineering-unit values:

| Device | Bus | Rating / capacity | Real-power availability or SoC |
| --- | ---: | --- | --- |
| `nd5` | 5 | 25 MVA | T=3 availability `[0,20,0]` MW |
| `nd7` | 7 | 15 MVA | T=3 availability `[1e-7,10,1e-7]` MW |
| `battery9` | 9 | 10 MVA / 40 MWh | Initial 20 MWh, terminal equality 18 MWh |

T=1 uses the first availability entry for each ND device. Storage is ideal,
aging weight 0.01, no terminal cost, no other storage/HVDC devices. No altered
network branch limits, voltage limits, load costs or generator costs. Use
`enforce_vset=False`, `init_flat=True`, `enforce_branch_limits=True`,
`sparse_pq=True`, `vectorize_pq=True`; retain the other baseline `OPFOptions`
values. T=1 uses single-step `build_opf`; T=3 uses vectorized
`build_opf_multistep` with `automatic_sparse_dispatch=False`.

Assert structurally before execution: bus-2 Pg is exact fixed, bus-3 Pg is
near-fixed and retained; ND zero-availability entries are exact fixed, positive
`1e-7` entries are retained. Floating-point unit conversion must not collapse
the positive gap; if it does, fail fixture preflight rather than relabel it.
Absence/shape/stepwise cases belong to unit tests, not extra numerical calls.
Feasibility is not assumed merely because the fixture is deterministic.

## Frozen treatments, controls, and starts

`baseline`: all preparation fields disabled. `normalized`: device normalization
only. `combined_ac`: normalization plus exact boxes, canonical scaling `none`.
`prepared_socp`: normalization, exact boxes, `joint5`.
`prepared_dc`: exact boxes and `joint5`, normalization disabled.

Prepared SOCP controls match the final stock-QDLDL evidence: `direct_solve_method`
`qdldl`, `tol_gap_rel=1e-6`, `tol_gap_abs=tol_feas=1e-10`, `max_iter=5000`,
`max_threads=1`, `min_terminate_step_length=1e-8`; reduced gap absolute, gap
relative, feasibility, infeasibility absolute and infeasibility relative
tolerances are all `1e-10`, and reduced k/t ratio tolerance is `1e-6`. Record
all other resolved stock settings without altering them. Native full `Solved`
is required, not reduced-tolerance `AlmostSolved` or unknown-status acceptance.

Both baseline and prepared DC use identical stock QDLDL with
`tol_gap_abs=tol_gap_rel=tol_feas=1e-10`, `max_iter=5000`, `max_threads=1`.
Other settings are identical resolved stock defaults, serialized before launch;
DC does not inherit SOCP's relative-gap or minimum-step change.

Every AC arm uses identical existing IPOPT controls: `mu_strategy=adaptive`,
`tol=1e-7`, `bound_relax_factor=0`, exact Hessian, no derivative test, no
least-square dual initialization, `warm_start=False`, `verbose=True`. No new
iteration cap or tolerance translation; the 180-second worker ceiling applies.
Record every resolved option and installed adapter/backend version.

AC starts use flat network voltage/angle and the same deterministic complete
physical start across matched arms. Before any canonicalization, fill remaining
unset values by the existing complete-start rule; explicitly assign selected
fixed Pg/ND entries their defining values in *every* treatment, including
baseline. Keep the exact initial SoC boundary. Retain physical start arrays,
full canonical/auxiliary start layouts and actual IPOPT `x0`. Different
canonical auxiliary coordinates are permitted only with explicit verified maps;
free physical coordinates must match exactly. No start imported from a fresh
SOCP/DC result, target-free arm, alternate hierarchy layer, or random seed.
These are standalone builds. Prepared hierarchical execution is deferred;
sharing its verified IPOPT boundary does not qualify shared DC/AC layer options.

## Exact ordered call list

Tracy input intervals below are global, half-open hour ranges. Four neutral
24-hour controls are the QDLDL archives under preserved
`four_conditions_001` (surplus, deficit, ramp_up, ramp_down), verified through
their inventory hashes. The depletion comparator is original E3 arm 10 attempt
000; its numerical rejection remains a rejection, not an accepted optimum.
Historical controls are not fresh baseline calls and cannot qualify current
disabled code or establish a production speedup.

Some neutral historical controls failed their historical total absolute-cost
gate despite native full convergence. Evaluate comparator eligibility under
the prospective SOCP gate in the new qualification report, retaining both the
original classification and this separate retrospective audit. Do not rewrite
the archives or describe that assessment as historical acceptance. If retained
evidence cannot support the independent comparator audit, mark its pair check
unavailable and the SOCP qualification incomplete, without adding a call.

| Call | Formulation | Input | Treatment |
| --- | --- | --- | --- |
| 01 | SOCP | Tracy surplus `[3308,3332)`, neutral | prepared_socp |
| 02 | SOCP | Tracy deficit `[1165,1189)`, neutral | prepared_socp |
| 03 | SOCP | Tracy ramp up `[8580,8604)`, neutral | prepared_socp |
| 04 | SOCP | Tracy ramp down `[2439,2463)`, neutral | prepared_socp |
| 05 | SOCP | Tracy deficit `[1165,1189)`, depletion | prepared_socp |
| 06 | lossy_dc | Case9 T=1 | baseline |
| 07 | lossy_dc | Case9 T=1 | prepared_dc |
| 08 | lossy_dc | Case9 T=3 | baseline |
| 09 | lossy_dc | Case9 T=3 | prepared_dc |
| 10 | lossy_dc | Tracy deficit `[1165,1189)`, neutral | baseline |
| 11 | lossy_dc | Tracy deficit `[1165,1189)`, neutral | prepared_dc |
| 12 | singlenode_dc | Case9 T=1 | baseline |
| 13 | singlenode_dc | Case9 T=1 | prepared_dc |
| 14 | singlenode_dc | Case9 T=3 | baseline |
| 15 | singlenode_dc | Case9 T=3 | prepared_dc |
| 16 | singlenode_dc | Tracy deficit `[1165,1189)`, neutral | baseline |
| 17 | singlenode_dc | Tracy deficit `[1165,1189)`, neutral | prepared_dc |
| 18 | AC | Case9 T=1 | baseline |
| 19 | AC | Case9 T=1 | normalized |
| 20 | AC | Case9 T=1 | combined_ac |
| 21 | AC | Case9 T=3 | baseline |
| 22 | AC | Case9 T=3 | normalized |
| 23 | AC | Case9 T=3 | combined_ac |
| 24 | AC | Tracy deficit `[1165,1168)`, neutral | baseline |
| 25 | AC | Tracy deficit `[1165,1168)`, neutral | combined_ac |

All Tracy arms keep `rho=1/3`, throughput cost 0.01, selected 27-device fleet,
optional shedding, and original economic/network/device inputs. Neutral initial
and hard terminal SoC are both 0.5 capacity; depletion uses 0.6 to 0.25 capacity.
The AC three-hour prefix has its *own* neutral terminal equality at hour 1168,
not the original 24-hour boundary. It is a bounded short-window qualification,
not a replay or recovery of the full E3 arm. All roles are primary; no recovery
ladder/target-free calls. Do not count a target-free result as hard-target success.

## Independent gates and disposition

Native full convergence plus complete finite restored results are mandatory.
Use original-unit independent component/network/ENS audits, not transformed
solver residuals alone. Preserve Stage B thresholds: power `1e-4` MW/MVAr,
boxes `2e-5` engineering units, SoC recurrence `1e-4` MWh, terminal `1e-3` MWh,
fraction `1e-8`, energy `1e-4` MWh, and each named component-cost check
`1e-4 + 1e-10*abs(component cost)`. Include AC numeric network/reactive checks;
SOCP uses the existing lifted-network audit with energy `1e-4`, fraction `1e-8`,
and unchanged remaining `SOCPAuditTolerances` fields. Rank/product/cycle recovery
diagnostics remain separate from relaxed feasibility acceptance.

Separate total canonical-versus-physical accounting from component checks.
Only prepared SOCP uses the prospective total gate
`abs(Ccanonical-Cphysical) <= 1e-4 + 1e-6*abs(Cphysical)`. DC/AC retain the
original total gate `1e-4 + 1e-10*abs(Cphysical)`. Audit implementation must
explicitly distinguish these two levels; do not call the historical E3 audit
and then override a failed component or aggregate label. Retain the discrepancy,
threshold and native status/gaps separately. AC requires native IPOPT status 0;
status 1 or 6 does not qualify a treatment.

For independently accepted comparable outcomes, report physical objective,
ENS/served energy, storage endpoints and aligned trajectory deltas. Matched
objective difference must be at most
`1e-4 + 1e-6*max(abs(Cbaseline),abs(Cprepared))`; ENS difference must be at most
`1e-4` MWh. Apply these pair checks to fresh DC/AC baselines and to accepted
neutral SOCP historical controls. A rejected historical depletion comparator
has no accepted-objective equivalence gate. No trajectory equality requirement
under nonunique optima; report differences rather than suppressing them.
AC objective agreement is a local-start comparison, not global optimality.

For each formulation report all prescribed outcomes, structural transformation
checks, pair checks, native metrics, original-coordinate restoration residuals,
wall time and sampled RSS. Qualified means all its prescribed arms and applicable
pair gates pass; rejected/timeout/failed pair means not qualified, and unfinished
or unlaunched calls mean incomplete. No missing result counts as a pass. No
blanket default promotion or unplanned remedial solve follows a failure.
Single observations and historical timing differences do not establish speedup.

Stop after the bounded matrix and owner-facing review of the implementation,
outcomes and explicit formulation-specific disposition. Prospective E3 defaults,
new source-bound execution, original E3 resumption, Stage D and worktree
retirement remain separate gates. Preserve all rejected/interrupted evidence.
