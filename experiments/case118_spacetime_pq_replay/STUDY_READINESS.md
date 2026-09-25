# Four fresh 120+6 replays: preparation checkpoint

Execution update, 2026-09-25: the gates described in this historical preparation
checkpoint were subsequently satisfied and the owner-authorized run finished
all 504 windows. See `FOUR_WAY_STUDY_REPORT.md` for refreshed final results and
final-review status. The statements below describe the pre-launch checkpoint.

Prepared 2026-09-23 on base commit `4d56dd66fb43cd678371a549d103d92596fe7d2d`.
The owner selected four fresh conditions. **No study solves have started.**
The new root `results/case118_four_way_120plus6_dense_control` is unused.

## Prepared implementation

- `study.py`: common preflight and clean-commit gate, four sequential conditions,
  environment/native/source binding, thermal admission, isolated destinations.
- Shared replay runner: carries explicit settings into every subprocess request
  and condition environment; unchanged SpeculativeSupervisor and WindowRace.
- Shared worker: selects time/spatial representation, sets threshold zero before
  building in fresh study workers, preserves stepwise and packed helper/replay
  starts, and records effective configuration. Original entry-point defaults stay
  backward compatible.
- `analyze_study.py`: six fresh paired comparisons, original primary weights,
  separate historical helper cohort, geometric speedups, means/quantiles,
  interaction ratios, changed winners, numerical differences, and retained
  failed/canceled attempts in partial reports. Checks every attempt's settings
  against its condition. Shared analysis now records unavailable primary native
  starts explicitly when a helper recovers a construction-error primary.
- `prepare_study.py`: exercises the actual worker through mocked native entry.

## Verification

- Preflight verified the exact sample SHA256 and all **655** directly referenced
  historical artifacts: **126** windows per condition, **504** total evaluations
  plus any helper attempts. Dependencies remain CVXPY 1.9.3 / sparsediffpy 0.6.1;
  all other recorded versions match. The full package inventory, thread settings,
  native libraries and source hashes are retained in preflight evidence.
- **110 tests passed** across factorial/replay, race policy, supervisor, and
  retained-path tests. Coverage includes all four nonuniform helper coordinate
  mappings, exact timeout-replay start reuse, configuration propagation into
  subprocess requests, matched weights and helper cohorts, changed winners,
  invalid/mismatched input rejection, and partial no-winner evidence accounting.
- Ruff and `git diff --check` passed. The test process emitted the existing
  CVXPY OpenMP-import warning; there were no native optimization calls in these
  preparation checks.
- All four actual hour-6047 worker preparations match the successful diagnostic
  controls exactly at native entry: complete x0, physical starts, canonical
  layout, bounds, options, and stored Jacobian/Hessian sizes. These are no-solve
  checks, not additional convergence runs. Source bindings were rechecked.
- Independent read-only scientific review found no remaining material code
  findings after corrections to error-policy wording, attempt configuration
  verification, partial attempt accounting, and missing-start reporting.

Compact evidence: `artifacts/study_preparation.json`. Full final preflight:
`results/four_way_study_preflight_20260923_02.json`. Final no-solve evidence:
`results/four_way_study_preparation_20260923_03/`.
The first preparation exposed an unsupported build keyword before any solve;
its log remains in `results/four_way_study_preparation_20260923/`. The corrected
`_02` and final `_03` checks passed; nothing was deleted or overwritten.

This validates the harness and one retained interval's native boundary, not all
126 outcomes. Sequential condition order, machine-wide contention and changing
helper winners remain interpretation limits. The existing physical audit uses
lifted injections; it does not independently recompute every nonlinear equation.
Treat close objectives and materially different reactive allocations separately.

## Remaining launch gates

The existing study protocol requires independent review, owner review/commit,
and explicit launch authorization. Implementation approval does not waive the
owner's Git workflow. Changes are unstaged; no commit or push was performed.
After owner commit, bind the new full HEAD, confirm the external fan is currently
on, verify `ps` and thermal permissions on the actual launch context, and launch
the command in `README.md`. Do not use the base commit above for changed code.
The runner rechecks the clean commit, all historical references and environment.
Keep bound files unchanged until the study finishes or stops. Preserve partial
evidence on interruption; this runner deliberately has no automatic resume.

Final scientific review of the complete 504-window comparison remains required
after execution. Historical timings are context, not the fresh factorial baseline.

Suggested commit message:

```text
Prepare four fresh Case118 vectorization replays with sparse dispatch disabled

Reuse the frozen 120+6 sample and existing helper policy across all four
time and spatial vectorization combinations. Record execution settings,
verify retained starts and environment, and compare matched fresh results.

Add no-solve preparation checks, scientific review evidence, and tests for
helper mappings, replay starts, configuration propagation, and partial reports.
```
