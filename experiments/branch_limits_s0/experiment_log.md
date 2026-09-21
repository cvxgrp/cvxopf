# M4 Stage 0 experiment log

This is an append-only record of the characterization process and observations
made at the time. Final architectural conclusions belong in the milestone
plan after review.

## 2026-07-30 — experiment opened

- The approved M4 plan was committed as `a80d57e`.
- Stage 0 is restricted to characterization code; production builders remain
  unchanged.
- The first candidate uses scalar `theta[i, 0]` and `v[i, 0]` indexing,
  branch-specific terminal admittances, normalized unit-right-hand-side
  thermal inequalities, and exact reuse of terminal expressions between
  reporting and enforcement.
- Measurements will distinguish the pre-M4 baseline, reporting-only direct
  expressions, enforced direct expressions, and enforced lifted variables.
- The bundled-case branch-status audit will precede shared strict `{0, 1}`
  validation.

## 2026-07-30 — first direct-expression and lifted comparison

- Scalar direct expressions agree with independent complex-current evaluation
  to `6.7e-15` p.u. or better on case9 and `1.5e-14` p.u. or better on case57.
- A synthetic fixed-voltage fixture with unequal parallel branches, a
  phase-shifting transformer, an analytically reversed terminal
  representation, an inactive zero-impedance row, and an empty branch table
  passes at machine precision. The inactive row is skipped before impedance
  division and produces exact zero coefficients.
- Both direct and lifted normalized constraints solve case9 to the same
  objective. The direct solve took about `0.17 s`; the lifted solve took about
  `0.05–0.06 s`.
- The case57 result is decisive enough to reject timing noise. Three
  alternating repetitions gave:
  - direct: `16.87–17.98 s`;
  - lifted: `0.628–0.643 s`.
  Both structures return the same objective to numerical precision.
- All 80 case57 ratings are `9900 MVA` and nonbinding. The roughly `28x`
  median solve-time difference is therefore a DNLP expression-structure
  effect, not a congestion or active-set effect.
- On case118, reporting-only expressions add about `0.06–0.07 s` of Python
  construction to a roughly `0.29–0.35 s` baseline build. The lifted,
  enforced model solves in about `3.2 s`, versus about `1.8 s` for the
  reporting-only problem.
- The lifted enforced structure solved every bundled case tested: case9,
  case9 PWL, case9 dcline, case14, case30, case30 PWL, case39, case57, and
  case118.
- Provisional conclusion: direct voltage expressions remain the authoritative
  branch-equation right-hand sides, but four lifted network-owned
  terminal-flow variables should probably be tied to them. The lifted
  variables—not a separately rebuilt expression graph—would then be the
  identical objects used for publication and thermal enforcement. This is the
  concrete fallback condition anticipated by Decision 3 and must be reviewed
  before Stage 1 production design is locked.

## 2026-07-30 — sparsity and status observations

- Every bundled branch status audited is exactly `1`. This supports the
  approved shared strict `{0, 1}` validation; explicit synthetic zero-status
  coverage is still required in implementation tests.
- For case57, `sparsity_tol` values from `0` through `0.1` retain the same 213
  `Ybus` entries and show no construction benefit.
- At `sparsity_tol=1.0`, eight entries are removed and the approximate nodal
  model becomes infeasible. No scientifically useful positive-tolerance
  fallback is supported by this first sweep.

## 2026-07-30 — lifted structure approved

- The project owner approved the lifted fallback after reviewing the repeated
  case57 evidence.
- The production contract is now:
  1. construct each direct scalar-indexed voltage expression once;
  2. tie four network-owned lifted terminal-flow variables to those
     expressions;
  3. publish and thermally constrain the identical lifted variable objects.
- The direct expressions remain the authoritative branch equations. The
  lifted variables are a DNLP structural representation, not a different
  physical model.
- The M4 plan now records Stage 0 as complete and Decision 3 as locked.

## 2026-07-30 — dense and multistep confirmation

- Dense `P/Q` confirms rather than weakens the lifted decision:
  - case9 direct `0.174 s`, lifted `0.036 s`;
  - case57 direct `75.96 s`, lifted `0.682 s`.
- The dense case57 direct solve emitted a numerical warning during evaluation
  but eventually returned the same optimum within solver tolerance. The
  roughly `111x` solve-time ratio is a structural failure for the direct
  constraint path.
- For a three-step case9 build, direct enforcement solved in `0.533 s` and
  lifted enforcement in `0.178 s`, with matching objectives.
- CVXPY's DNLP/IPOPT path did not populate `Problem.compilation_time`,
  `SolverStats.solve_time`, iteration counts, derivative nonzero counts, or
  detailed solver memory. The report identifies these metrics as unavailable
  rather than inferring them from wall time.

## 2026-07-30 — lifted reporting-only follow-up and timing correction

- Review identified that the earlier “reporting-only” mode constructed unused
  direct expressions but solved the unchanged pre-M4 problem. It did not
  characterize the approved production structure when thermal enforcement is
  disabled.
- Stage 0 was reopened and the harness now distinguishes four structures:
  pre-M4 baseline, unused direct reporting, lifted reporting without thermal
  inequalities, and lifted enforcement.
- The lifted reporting-only structure solved every bundled case. Representative
  baseline versus lifted-reporting solve times were `0.030 s` versus `0.049 s`
  for case9, `0.295 s` versus `0.509 s` for case57, and `1.734 s` versus
  `2.547 s` for case118.
- The `T=3` case9 lifted-reporting structure solved in `0.147 s`.
- The empty-branch candidate creates no variables or defining equalities and
  publishes four `(0,)` constants. This is a required exception to the
  ordinary lifted path because `cp.Variable(0)` is not viable.
- The earlier `16.87–17.98 s` direct and `0.628–0.643 s` lifted case57 ranges
  were from a prior run and were stale relative to the then-canonical JSON.
  The complete refreshed run recorded `18.36–19.34 s` direct and
  `0.671–0.688 s` lifted. Timing is machine-local; the stable conclusion is
  the roughly `27x` structural advantage of lifting.
