# Convex cost-coordinate qualification and coordinate-check correction

2026-10-07. The 96-arm run is complete. The owner-approved, **non-solving**
coordinate re-audit accepts **74/96**, versus **56/96** under the original
checker. All 18 coordinate-only exclusions are recovered without changing any
published dispatch, cost, solver setting, or non-coordinate audit.

## Updated results

| Formulation | Original accepted | Re-audit accepted | Remaining exclusions |
| --- | ---: | ---: | ---: |
| SOCP | 16/32 | 17/32 | 11 native-status, 4 stated-model feasibility |
| Lossy DC | 19/32 | 27/32 | 1 native-status, 4 stated-model feasibility |
| Copper plate | 21/32 | 30/32 | 2 native-status |
| Total | 56/96 | 74/96 | 22 |

There are 20 accepted candidates with cycling warnings and 34 with recorded
bound-projection warnings; these categories overlap. Warnings do not imply
that the physical dispatch is unusable. Every candidate still has to pass its
mapping, engineering feasibility, economic reconstruction and resource checks.

The 18 recovered IDs are 20, 34, 38, 42, 46, 50, 51, 58, 59, 70, 82, 83, 84,
88, 90, 92, 94 and 96. Fifteen had expected CVXPY box projection, not an inverse
mapping error. The largest was 13.46 W of nondispatchable output against the
existing 20 W box tolerance. Three had initial SoC equality residuals, at most
0.01246 Wh against the existing 100 Wh energy tolerance. These now appear as
separate physical feasibility checks, not coordinate-restoration failures.

The corrected mapping comparison retains its tight `1e-10 + 1e-12*max(abs(expected))`
tolerance. Bound projections are independently predicted from declared leaf
attributes, not inferred from constraints. Raw excursions remain recorded and
must pass the existing physical box tolerances. Large excursions and unexplained
mapping discrepancies remain hard failures. The old checker remains available
for exact reproduction of historical classifications.

## Remaining outcomes to discuss

The 14 native-status exclusions comprise four `InsufficientProgress`, seven
`NumericalError` and three `AlmostSolved`. They are not proofs of infeasibility.
The three `AlmostSolved` candidates deserve separate inspection of raw physical
and economic accuracy, rather than automatic acceptance or rejection based on
their status text alone. Their retained canonical primal/dual objective gaps
are approximately 73.31 (lossy DC forced T=3, call 54), 1.55e-6 (copper plate
ordinary T=6, call 78), and 27,311.61 (copper plate forced T=3, call 86). These
are solver-space diagnostics, not independent physical-cost certificates. The
original protocol did not physically audit these non-`Solved` candidates; this
coordinate correction leaves that policy and their classifications unchanged.

All eight converged feasibility exclusions concern forced shedding:

| Calls / treatment | Residuals beyond the existing gates |
| --- | --- |
| Lossy DC 56, 60 / prepared cost coordinates, T=3,6 | Balance 135.39 and 128.55 W versus 100 W; raw flow-box excursions 31.03 and 29.83 W versus 20 W |
| Lossy DC 62 / original preparation, cost coordinates, T=24 | Balance 385.71 W versus 100 W; raw generator-box excursion 128.57 W versus 20 W |
| Lossy DC 64 / prepared cost coordinates, T=24 | Balance 1,655.57 W versus 100 W |
| SOCP 23,27,31 / prepared original costs, T=3,6,24 | Real balance 20.176,18.439,1.271 kW versus 0.100 kW; reactive and, for T=3,6, branch residuals also fail |
| SOCP 32 / prepared cost coordinates, T=24 | Real balance 105.748 kW, reactive balance 16.260 kVAr, branch excess up to 79.308 kVA; cone residual also fails |

The small DC misses warrant engineering-tolerance review; the SOCP cases also
need network/cone interpretation. None is silently promoted here. Preparation
plus cost coordinates is not yet universally qualified for convex fixtures:
accepted counts per eight fixtures are SOCP 5/8, lossy DC 5/8, copper plate 8/8.
Improved auxiliary economic accuracy and declared-model feasibility are distinct
claims. Production extraction/default changes and any new solves remain deferred.

## Owner decision: retain original cost coordinates for convex formulations

On 2026-10-07, after reviewing the matched results by formulation, the owner
decided not to adopt the battery-throughput and load-shedding cost-coordinate
reformulation for **SOCP, lossy DC or copper plate** (`singlenode_dc`). Retain
their original cost coordinates alongside the existing numerical-preparation
machinery. This decision concerns coordinate representation, not the physical
cycling/shedding weights or the other preparation transformations.

| Prepared formulation | Original coordinates accepted | Cost coordinates accepted | Interpretation |
| --- | ---: | ---: | --- |
| Lossy DC | 8/8 | 5/8 | Ordinary fixtures have essentially equal costs; all three forced-shedding rescaled candidates miss feasibility gates. |
| Copper plate | 8/8 | 8/8 | No compelling general benefit from rescaling; forced T=6/T=24 balance and cycling accuracy worsen despite passing feasibility. |
| SOCP | 5/8 | 5/8 | Ordinary fixtures offer no material additional benefit; forced T=3/T=6 stop with `InsufficientProgress`, and rescaled T=24 has larger balance, branch and cone errors. |

Rescaling did help some unprepared convex cases, but those gains do not establish
a benefit over the prepared original-coordinate path. The decision is therefore
not a claim that rescaling is universally worse. Nor does retaining original
coordinates resolve the three prepared SOCP forced-shedding feasibility misses:
their original-coordinate real-balance errors remain 20.176, 18.439 and 1.271 kW
for T=3, T=6 and T=24, respectively, against the 0.100 kW gate. Those are
separate numerical-accuracy questions about the stated SOCP model, not checks
of full AC realizability.

This closes the convex cost-coordinate adoption decision, not the remaining
SOCP accuracy investigation or production extraction. The separately approved
AC/IPOPT cost-coordinate evidence is unchanged; there is no project-wide
cost-coordinate rule. Keep the experimental rescaled treatments for evidence
and reproducibility. Do not change original run classifications, audit gates,
solver settings or historical artifacts to implement this decision. This
checkpoint records the decision only; it makes no production/default changes
and authorizes no new solves, E3 execution or Stage D work.

## Evidence and verification

Original execution: clean commit `29f8aea803386149f49854b5fb22b71615f664b9`,
`results/qualification_001`, completed `2026-10-08T00:59:30.423803Z`. All 96
result/native archives, completion manifests and finalized supervision records
agree; 335.84 worker-seconds, peak sampled RSS 628.20 MiB, no resource limits.

The original protocol, raw run records, report and plots remain unchanged. Its
original analysis was generated under the clean bound sources before editing.
The revised report, complete device comparisons and 24 relabeled fleet plots
are in `results/coordinate_reaudit_001`, with original-file hashes, revised
source/environment/policy hashes and an immutable completion manifest. The
re-audit forbids optimizer entry points and reproduces original acceptance
before applying the correction to every retained converged candidate.

- Original binding SHA256: `880190b5c8b62d2a317d1d52ae73cd1b52d2acc5ca830c40e48f9068cd4664d8`.
- Original analysis SHA256: `d17bd56243952ddae9cbf7f261cc93546c8bcd4b88a505b465306e46288cb19a`.
- Re-audit report SHA256: `02c80421dc7f659daf289cf5d64b5055d0f280cb30b2ad21009d5641c74b2696`.
- Re-audit analysis SHA256: `04210aaac714acb5cfb7194cf34bdefad6ab85262da8c0a19185dd7c65482bfc`.
- Re-audit completion SHA256: `784676b0f37d85b5491dcc8ed2790cc0b4acb31f3dc709d39e22483b3b4cdeef`.

214 combined non-solving regression tests pass, including 32 new coordinate
and re-audit tests plus existing convex, AC and historical projection tests.
Ruff and whitespace checks pass. Independent `cvxopf-review` scientific review
returned **CLEAN**, independently reproducing 74/96 and the exact 18 recovered
cases, unchanged non-coordinate checks/public arrays, and matching original and
re-audit artifact hashes. Changes are unstaged and uncommitted; no new numerical solves occurred.
See `COORDINATE_REAUDIT_PROTOCOL.md` and `reaudit.py` for the recorded policy
and reproducible reader. Raw results remain ignored.

## Historical runner implementation checkpoint

2026-10-07. **No scientific solves executed; no qualification result claimed.**

The following section records the earlier, pre-execution checkpoint, not the
current execution state or review status of the correction above.

The owner deferred PYPOWER fixture duplication to production extraction/general
test building and selected bounded SOCP, lossy DC and copper-plate qualification.
This checkpoint implements the prospective runner and its protocol only. It does
not change production, defaults, historical AC evidence, E3 or held Stage D.

## Scope and design

`PROTOCOL.md` declares 96 arms: three formulations, eight input fixtures and four
matched coordinate/preparation treatments. Original deficit, surplus and ramp-up
Tracy intervals, longer deficit, device-bearing Case9 and forced-shedding T=3/6/24
fixtures exercise ordinary and stressed economics. The severe shedding recipe
matches the AC qualification, with an input-only shortage witness, not a new load
observation. All four treatments have equal physical-input hashes per group.

The experiment uses positive exact cycling/shedding diagonal coordinates,
preserving explicit constraints **and DC variable-bound attributes**. Generation
and lossy-DC loss costs remain unchanged. Production-qualified preparation stays
optional. A scoped stock-CLARABEL observer verifies the delivered canonical data
and retains native outputs; it neither changes nor retries the numerical call.
No experimental observer or transformation is proposed for production here.

Original-space feasibility/projection, native full convergence, provenance,
canonical reconstruction and noncycling component accounting remain hard checks.
Cycling discrepancies are advisory, including their explained contribution to
total discrepancy, as the owner approved. Reports distinguish accepted arms,
warnings, numerical rejection, timeouts, unfinished tails and completed archives.
Same-formulation pair costs/ENS and full device trajectories are descriptive;
nonunique schedules need not match. Future fleet plots include battery absolute
throughput and initial/final SoC; absent DC reactive channels are not zeros.

## Verification and execution boundary

Non-solving tests forbid creation of a CLARABEL numerical optimizer. They cover
exact algebra/bounds, fresh canonical identities, real build.solve adapter and
inverse-chain wiring with mock native evidence, hard/advisory accounting,
forced-shedding witnesses, atomic manifests, supervised replay, unfinished tails,
resource gates, and terminal immutable analysis. Full-matrix preflight assembles
canonical data without optimizing. Passing these checks is runner readiness,
not scientific success.

Proposed bounds: 96 launches, 180 seconds per worker, 17,280 cumulative
worker-seconds, 16 GiB sampled RSS, serial single-thread execution. Launch requires
owner review, a new clean committed source binding, separate execution approval,
AC power and real resource/thermal preflight. The isolated investigation and all
historical raw artifacts remain unchanged. No files are staged or committed by
the builder.

Final verification: **156 combined non-solving tests pass** (94 new runner
tests plus 62 existing AC qualification/coordinate tests), Ruff passes, and
the 96-arm canonical preflight passes every algebra/bound/witness check across
24 matched groups (32 arms per formulation). The working tree is deliberately
uncommitted and therefore ineligible for launch. Independent `cvxopf-review`
scientific review returned **CLEAN**, including a final pass over the plotting
tests/documentation; the reviewer independently confirmed 94 focused tests and
the complete non-solving matrix preflight. No actionable findings remain. The
numerical output directory is absent. Review does not authorize execution.

## File-by-file checkpoint

| File | Purpose |
| --- | --- |
| `.gitignore` | Keeps raw numerical outputs and Python cache files ignored. |
| `fixture.py` | Prespecified 96-arm matrix, matched inputs and shortage witnesses. |
| `model.py` | Exact positive cost maps, bound preservation, canonical binding, native observation and inverse restoration. |
| `audit.py` | Independent physical/projection/economic acceptance and advisory cycling diagnostics. |
| `run.py` | Clean-commit launch boundary, supervised workers, atomic publication and read-only replay. |
| `analyze.py` | Immutable terminal summaries, same-model pair comparisons and fleet plots. |
| `test_convex_cost_qualification.py` | Non-solving algebra, boundary, lifecycle, accounting and plot regressions. |
| `PROTOCOL.md` | Prospective matrix, gates, comparisons, resource limits and execution exclusions. |
| `REPORT.md` | Implementation handoff; no numerical conclusions. |

Proposed commit message: `Add bounded convex cost-coordinate qualification runner`.
