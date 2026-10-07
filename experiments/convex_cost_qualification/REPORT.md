# Convex cost-coordinate qualification: implementation checkpoint

2026-10-07. **No scientific solves executed; no qualification result claimed.**

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
