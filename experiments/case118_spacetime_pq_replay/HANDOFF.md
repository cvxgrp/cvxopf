# Handoff: hour 6047 AC convergence regression

Prepared 2026-09-21. Project workflow instructions are in `CLAUDE.md`.

## Current state

Latest 2026-09-23: the owner selected four fresh 120+6 replays using the new
packages and disabled sparse dispatch. Runner and paired analysis are prepared;
see `FOUR_WAY_STUDY.md` and `STUDY_READINESS.md`. No study solves have started.
Owner review/commit, current fan confirmation and launch authorization remain.

Latest 2026-09-22: the owner-authorized repeat of all four vectorization modes
with the new packages and sparse dispatch disabled is complete. **All four are
optimal and accepted:** none 339 iterations, time-only 70, spatial-only 190,
both 76. See `FOUR_WAY_DENSE_CONTROL_REPORT.md` and
`artifacts/provenance/four_way_dense_control.json`. No production configuration
has been adopted; the repeat supports retaining the new stack with an explicit
compatibility policy, pending its scope and broader validation decision.

Preceding evidence: see `ORACLE_INVESTIGATION.md`. At the shared starting point,
functions and tested derivatives agree exactly; the stored Jacobian structure
changes. One separately owner-authorized control with the new packages and
CVXPY's density dispatch disabled restores the old structure and exact accepted
solution in 70 iterations. This localizes the trigger to the density-dispatch
behavior, without establishing a derivative defect. Upstream issue and email
drafts are ready and include the new four-way results. The owner requested this
evaluation while discussing stabilization; no production setting or main
environment has been changed yet.

- Checkout: `/Users/bmeyers/github/cvxopf`, branch `todo-fix`.
- HEAD when inspected: `93294af124e1135388dc5d8d444a32b0b16755c2`.
- Worktree was clean before adding this handoff.
- All four diagnostic conditions are complete; no experiment is running.
- Update 2026-09-22: the owner-authorized historical-code dependency comparison
  is also complete. A (old packages) was accepted in 70 iterations; B (new
  packages) was rejected at 3,000 iterations. Both exactly reproduce their
  corresponding retained successful/failed results. Start with
  `ENVIRONMENT_COMPARISON_REPORT.md` for this new evidence.

Related task: **Close out PR #8**, thread
`01a0c232-d989-73e3-95f0-adf5a0f54abd`. It contains the implementation,
review, launch, and result discussion.

## Question being investigated

The three-hour, 118-bus AC problem at hour 6047 previously solved successfully,
especially quickly with time vectorization. After spatial P/Q batching and a
dependency upgrade, it repeatedly reaches IPOPT's 3,000-iteration limit.
Are we seeing a dependency/oracle regression, a shared model or initialization
change, or numerical sensitivity of equivalent nonlinear representations?

The controlled old-environment comparison was authorized and completed on
2026-09-22. With historical code fixed, the CVXPY/sparsediffpy pair transition
reproduces the failure. Subsequent authorized dispatch controls localized the
trigger and restored convergence in all four modes. Those evaluations are now
complete; project-wide adoption and broader validation remain to be agreed.

## Evidence now available

Start with these maintained files:

- `FOUR_WAY_REPORT.md`: completed four-way results and limitations.
- `artifacts/four_way/summary.json`: compact retained comparison.
- `FOUR_WAY_DIAGNOSTIC.md`: frozen four-way design.
- `PRIMARY_DIAGNOSTIC.md`: preceding isolated two-way diagnostic.
- `RESULT_LOCATIONS.md` and `RESULT_LOCATIONS_20260921.json`: relocation map.
- `../case118_vectorization_replay/REPORT.md`: successful historical time-only
  replay; its `artifacts/comparison.csv` and `artifacts/environment.json` retain
  row-level results and environment evidence.

Paths above are relative to this handoff's directory. Current raw roots:

```text
experiments/case118_spacetime_pq_replay/results/
  case118_spacetime_pq_replay/                 # interrupted 120+6 replay
  case118_6047_primary_diagnostic/             # failed startup, not a solve
  case118_6047_primary_diagnostic_retry/       # completed two-way diagnostic
  case118_6047_four_way/                       # completed four-way diagnostic
experiments/case118_vectorization_replay/results/
  case118_vectorization_replay/               # successful historical time-only replay
```

Embedded historical paths are resolved by `experiments.retained_paths` using
the relocation manifests. Filesystem/hash verification is distinct from
scientific reconstruction.

### Historical success

The retained comparison CSV's **hour 6047** row reports:

- Original stepwise winner solve phase: **351.036845 s**, accepted.
- Historical time-only winner solve phase: **33.270276 s**, `optimal`, accepted.
- Objectives: approximately 266883.716124 and 266883.717282 respectively.
- This earlier sampled study completed 126/126 accepted windows. It is not the
  later interrupted combined-vectorization replay.

Historical environment records Python 3.11.15, CVXPY 1.9.2, cyipopt 1.7.0,
IPOPT 3.14.19, NumPy 2.4.6, pandas 3.0.3, and CLARABEL 0.11.1. The subsequent
upgrade protocol identifies sparsediffpy 0.3.0 -> 0.6.1; the old environment JSON
does not itself record sparsediffpy, so verify that version from the historical
lock/source evidence when preparing reproduction.

**Historical exact execution revision remains to be resolved from retained
provenance.** The replay implementation first appears in `2ac05b039`, but that
does not establish its execution revision. Retained `execution_sources` hashes
can help identify the code that actually ran.

### Completed four-way diagnostic

Executed at `56a531b5f0d6f6d737d83a449b07714973553622`, with CVXPY 1.9.3 and
sparsediffpy 0.6.1. Other reported numerical package versions match those above.
One isolated primary per condition, sequential fresh processes, same frozen
physical request and causal start, no helpers or recovery, no numerical tuning.
Only diagnostic IPOPT logging was enabled at the solver boundary.

| Condition | Time assembly | Spatial P/Q batching | Native IPOPT seconds | Solve-phase seconds | Result |
|---|---|---|---:|---:|---|
| none | stepwise | off | 717.402 | 723.441 | 3,000 iterations; rejected |
| time_only | vectorized | off | 284.574 | 286.528 | 3,000 iterations; rejected |
| spatial_only | stepwise | on | 448.483 | 451.879 | 3,000 iterations; rejected |
| both | vectorized | on | 196.344 | 197.464 | 3,000 iterations; rejected |

All four have `user_limit`; accepted throughput is zero. Solve phase includes
canonicalization, start capture/persistence, and solver return. These are times
to unsuccessful termination, not successful-solve speedups. Equal iteration
counts do not imply equal function-evaluation or line-search work.

The preceding isolated two-way diagnostic also rejected both spatial-on arms
at 3,000 iterations (~451.9 s stepwise, ~197.8 s vectorized). The four-way
report verifies exact repeated extracted results, physical residuals, full x0,
and normalized layouts for those corresponding spatial-on arms. Within the
four-way run, toggling spatial batching preserves exact full x0 and normalized
layout for each temporal representation. This does not assert native-coordinate
identity across the two different temporal representations.

### Dual-residual behavior: progress was repeatedly lost

Inspect `results/case118_6047_four_way/time_only/worker.log`.
The following are printed **scaled dual** residuals, not the unscaled final
summary values:

| Iteration | Primal violation | Dual residual |
|---:|---:|---:|
| 0 | 0.623 | 14.2 |
| 474 | 3.64e-5 | 0.00322 |
| 475 | 0.0145 | 1.61e12 |
| 2098 | 1.28e-7 | 1.19e10 |
| 2916 | 7.62e-9 | 8.08e-6 |
| 2917 | 9.16e-6 | 1.33e9 |
| 3000 | 0.00133 | 3.13e9 |

At 2916 -> 2917, printed log10(mu) changes from -4.6 to -10.6. This is a
useful localization point for investigating centrality/barrier transitions,
conditioning, or derivative behavior, **not proof of any cause**. A single row
with small primal/dual residuals does not prove the full termination criteria
passed: complementarity and all stopping quantities are not supplied there.
Restoration-phase rows (iteration suffix `r`) report a different problem's dual
residual; keep them separate in any further trend analysis.

## Interpretations and limits

Established:

- Spatial batching is not necessary for the current failure: both spatial-off
  conditions failed too. Time vectorization is not necessary either.
- Isolation reproduces failures, weakening concurrency as the main explanation.
- Vectorization reduces elapsed time to the iteration cap in this diagnostic,
  but does not restore convergence.
- Turning flags off in current code does not restore the historical executable.
- The four-way comparison fixes today's dependency stack, so it cannot isolate
  the dependency upgrade from shared implementation changes.

Still hypotheses, not findings:

- A CVXPY/DNLP or sparse derivative regression (gathers, broadcasts, sparsity,
  derivative accumulation, or Hessians).
- Correct but differently conditioned/reordered reductions producing unstable
  nonlinear solver paths.
- A shared model/initialization difference from the historical successful run.

The results establish neither infeasibility nor a specific faulty dependency.
Rejected objectives are not feasible-cost comparisons.

### Audit interpretation

`experiments/case118_annual_hierarchy/audit.py::_ac_residuals` compares device
injections against retained `p_net`/`q_net`; those are lifted model variables in
`src/cvxopf/ac_problem.py`. That particular balance check does not independently
recompute nonlinear network injection from voltage, angle, and Ybus. Small values
can therefore coexist with violated nonlinear defining constraints. This
explains why those audit fields alone are not proof of physical feasibility; it
does not identify the actual largest native residual. Other audit paths may
perform additional checks and should be inspected rather than conflated.
All four attempts were correctly rejected by their status gate.

## Proposed next experiment

1. Resolve the exact historical successful time-only code/source identity and
   environment, including the lockfile and native IPOPT/linear-solver stack.
2. Reproduce **one** frozen hour-6047 time-only primary under that old code and
   old environment. Retain the original request, preceding source, SoC endpoints,
   physical start, canonical x0/layout, solver settings, and acceptance criteria.
3. If that baseline succeeds, hold the same historical code fixed and change
   only to the current compatible dependency stack. Record any necessary harness
   adaptation separately from model or initialization changes.
4. If this changes convergence, bisect compatible dependency transitions.
   CVXPY 1.9.2 and 1.9.3 have different sparsediffpy requirements, which constrain
   the valid comparison matrix.
5. If the old baseline does not reproduce, stop causal attribution and examine
   source identity, start reconstruction, native libraries, and other environment
   differences before expanding the experiment.

An old-code/old-environment versus new-code/new-environment comparison alone
cannot separate code from dependencies. Where compatible, a code/environment
cross-comparison is the discriminating control. Start narrowly, not with another
126-window replay.

Environment isolation and checkout location remain to be agreed with the owner.

If necessary after this comparison, a bounded diagnostic can evaluate exact
constraint values and check Jacobian/Hessian consistency at shared physical
points, localizing the largest violated defining constraint.

## Immediate open items

Update from the 2026-09-21 provenance investigation: see
`HISTORICAL_PROVENANCE.md` and `artifacts/provenance/summary.json`.
The 104 tracked execution-source hashes match `2ac05b039` exactly; the ignored
105th source also verifies. Reflog timing places execution HEAD at `ca4015527`
with replay files subsequently committed, so source snapshot and execution HEAD
must remain distinct. The successful and failed time-only runs have identical
full canonical x0 (9,124 coordinates), normalized layout, and named starts.
The owner approved preparation on 2026-09-22. The isolated historical snapshot
and two environments are now prepared; both reproduce the historical start and
pass mocked native-entry checks. See `HISTORICAL_PROVENANCE.md`'s completed
preparation section and `artifacts/provenance/preparation_summary.json`.
The owner subsequently authorized the solve harness and convergence test, now
complete; see `ENVIRONMENT_COMPARISON_REPORT.md`. Historical native binary identity
remains unverified; binary hashes match between the two newly prepared arms.

- Review `FOUR_WAY_DENSE_CONTROL_REPORT.md`: the completed dispatch control
  restores convergence in all four current-code vectorization modes.
- Agree the scope of an explicit compatibility policy and a bounded broader
  validation sample before adopting it project-wide. The main environment,
  dependency constraints, and production defaults remain unchanged.
- Review the updated CVXPY issue and colleague email drafts. Neither has been
  submitted/sent; the full convergence reproducer still needs portable packaging.
- Do not attribute the demonstrated CVXPY dispatch trigger to a proven derivative
  defect or a measured IPOPT/MUMPS mechanism.
- Preserve the historical native-library uncertainty in interpretation; source,
  starts, dependency inventories, and current paired native hashes are established.
