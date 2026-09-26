# Stage 0a review checkpoint

**Current disposition:** this checkpoint was completed and committed as
`9c26366`; the owner-approved scientific report and complete accepted
`S5_RESULTS.json` are the formal S5 record. Separate artifact promotion was
committed as `0ae9d86`. S5 is closed; the owner chose to omit S6's optional
congestion sensitivity on the toy data. The preparation, approval-pending statements,
proposed commit text, and source-fingerprint reconstruction below are retained
as the historical checkpoint narrative, not current outstanding work.

Prepared 2026-09-17 by the current builder task, `case118-tracy-2021-study`.
The owner retired the previous builder and assigned `cvxopf-review` as the
independent reviewer. Scope is the completed **toy** study, per
[`case118-tracy-2021-study-plan.md`](../../plans/case118-tracy-2021-study-plan.md).

## Present status

- Scientific report expanded with final-input heatmaps, actual input metrics,
  DC/AC net and absolute differences, opposing redispatch, loading/storage
  summaries, and full-year exploratory correlations.
- `cvxopf-discuss` supplied read-only scientific input. The report incorporates
  its distinctions among imposed patterns, net balancing, spatial changes,
  inherited SoC divergence, and recovery-policy/calendar confounding.
- Original one-pass promotion change reviewed CLEAN under the scientific
  register by `cvxopf-review`. It ran seven focused checks independently.
- Builder verification: all 29 S5 execution tests and four new closeout tests
  passed; all four figures visually inspected. No study solve or full S5 audit.
- Full reporting-package review completed CLEAN under the scientific register.
  Reviewer independently checked all four closeout tests, numeric/source hashes,
  annual totals, calendar, generation balance, per-device SoC recurrence and
  shard boundaries, all 8,760 DC loading covariates, recovery/seasonal summaries,
  selected correlations, and all four figures. It recomputed two additional
  AC actions (1379 and 7182) beyond the builder's six samples. No actionable
  findings remain for this preparation checkpoint.
- The complete formal result has been published and independent result and
  file-disposition review is CLEAN. Owner commit approval remains pending. The design pause follows commit 1;
  it is not decided here.
- The owner approved the S5 report on 2026-09-17 after reviewing the scientific
  package. The owner subsequently authorized the single full-analysis launch
  with two workers and a 12 GiB combined cap. Final commit approval is separate.

The owner subsequently requested a small parallel-analysis refactor while
reviewing the report. The analyzer now offers `--workers 2` (serial remains
the default), and removes repeated full artifact verification around each
completed shard's existing audit. No physics, merge, or runner implementation
changes. Its 92 focused tests passed, including actual spawned-process
equivalence/failure tests and continuation/speculative-integration regressions.
Independent review completed CLEAN; the reviewer separately ran 54 tests,
checked equivalence of the removed/reused validation paths, ordered reduction,
failure propagation, provenance, and publication. No actionable findings.
The approved full run completed on 2026-09-17 in 434.83 seconds, with a
maximum sampled aggregate RSS of 8.72 GiB and empty stderr. The complete
accepted result is published in `S5_RESULTS.json`. Builder checks confirm
canonical bytes, the internal digest, equality with stdout, and exact agreement
with all retained prior summary fields except the provenance-dependent digest.
Logs and one-second RSS samples are
retained in `experiments/case118_annual_hierarchy/results/s5_analysis/closeout-analysis-20260917T211119Z/`.
The monitor enforces the 45-minute and 12 GiB aggregate bounds, including its
own RSS as well as the analyzer and descendants. It records completion or the
stop reason and performs no automatic retry.

## Implementation choices made visible

The reporting code is a standalone experiment utility. It does not change
the runner, mathematical formulations, source profiles, or accepted trajectory.
It reads the fixed final dashboard snapshot rather than refreshing it. A
bounded sample checks six AC actions against retained archives; all rows are
checked for calendar and checkpoint membership. DC covariates are recomputed
from the existing annual outer primal. The methods document states precisely
what this does and does not validate.

The package saves actual per-device final inputs (~12 MB) and an hour-level
comparison table (~7 MB). These simple arrays and tables support independent
inspection and future plotting. Exact historical checks protect this record;
they do not prescribe identities or execution restrictions for future studies.
Correlation adjustment and rounding are descriptive sensitivities, not a new
inference model, acceptance tolerance, or prospective prediction system.

## Authorized single full-analysis execution

The previously completed analyzer result was discarded except for its printed
summary and digest. The summary omits authority/context, shard and supervision
artifacts, lifecycle records, and source transitions. Inventing those missing
fields would not recover the result. A scoped search found no complete payload.
The reviewer reports that the retired builder's pass took approximately
25 minutes; this is historical evidence, not a new-run timing guarantee.

Approved execution:

- One invocation of `s5_analysis --promote`, with **no preview analysis and no
  automatic retry**. Read the completed twelve-shard execution tree, reconstruct
  physical/trajectory and lifecycle evidence, and save that exact accepted
  complete object as `S5_RESULTS.json`. No OPF solve, new action, or change to
  the accepted execution tree.
- Use the existing historical authority
  `outputs/s5_authority/speculative-cutover/S5_GIT_DRIFT_NUMERICAL_EXECUTION_AUTHORITY.json`.
  Its equality with the final source transition's authority and its loader
  validation have been checked. The default tracked authority path is absent.
- Budget: one parent and **two shard-audit workers**, 45 minutes wall time,
  12 GiB observed aggregate current RSS across the analysis process tree,
  revised from 8 GiB at the owner's request on 2026-09-17.
  These are proposed stop bounds, not predicted consumption. Sample
  resource use during execution and stop on a bound or an analysis error;
  report evidence before proposing another attempt. Stop the entire analysis
  process tree if a resource bound is reached. The measured runtime was 7 min 15 s; the historical 25-minute serial pass
  included repeated traversal and is not a controlled comparison.
- Retain stdout, stderr, elapsed time, and resource observations in a new
  `outputs/s5_analysis/closeout-analysis-<timestamp>/` directory. The full JSON
  is published atomically before the CLI prints it, avoiding the prior loss.
- After success, independently review the result and its agreement with the
  report. Only then request owner closeout/commit approval.

The historical command, from repository root, was:

```sh
uv run --no-sync python -m experiments.case118_annual_hierarchy.s5_analysis \
  --workers 2 \
  --output-root experiments/case118_annual_hierarchy/results/s4b_annual_ac \
  --authority outputs/s5_authority/speculative-cutover/S5_GIT_DRIFT_NUMERICAL_EXECUTION_AUTHORITY.json \
  --promote experiments/case118_annual_hierarchy/S5_RESULTS.json
```

Current location note (2026-09-21): the authority is now under
`experiments/case118_annual_hierarchy/results/s5_authority/`; the closeout logs
are under `results/s5_analysis/`. See [RESULT_LOCATIONS.md](RESULT_LOCATIONS.md).
The command above records the original execution and does not authorize rerunning
it or overwriting the accepted result.

The governing plan explicitly says,
“If a full pass is required, state its scope and cost and obtain execution
approval.” The owner supplied that approval with “please start the run now”
after setting the combined cap to 12 GiB. This authorizes this one pass;
it does not authorize a retry, commit, or Stage 0b execution.


The analyzer's source fingerprint includes `S5_PROTOCOL.md`. After publication,
its opening status paragraph was updated to describe the completed pass. The
run fingerprint is reproduced exactly with all current source bytes and only
that paragraph restored to this pre-run text:

```text
**2026-09-17 status:** All 8,760 intervals and one full independent analysis
are complete (`accepted_for_s6=true`). The scientific report is drafted in
`S5_REPORT.md`; formal closeout awaits `S5_RESULTS.json`.
```

The following ` The implementation` and all later text are unchanged. The
reconstructed protocol is retained as `S5_PROTOCOL_at_analysis_reconstructed.md`
in the run directory; it is not described as a contemporaneous snapshot. This
reproduces source fingerprint
`c15956932a007196ab043ef74ed5fd8c0aba26116aa7428defd1fe0946fc808f`.
No analysis code changed after execution.

Independent final review returned CLEAN. It verified canonical bytes/digest and
stdout identity; all retained prior fields; the merge, authority, twelve
shard/checkpoint identities and ordered summary hashes; sixteen supervision
hashes and ten continuations; all four study resource aggregates; the 429
analyzer samples and 8.7218475 GiB peak; unchanged numerical package hashes;
the documentation-only fingerprint substitution; and exact package disposition.
No further full reconstruction, solve, staging, or commit was performed.

## Proposed commit 1 disposition

| Path | Disposition and reason |
| --- | --- |
| `experiments/case118_annual_hierarchy/S5_REPORT.md` | Include the owner-approved scientific/input record; formal result review is CLEAN. |
| `experiments/case118_annual_hierarchy/S5_RESULTS.json` | Complete accepted payload published and independently reviewed CLEAN; include. |
| `experiments/case118_annual_hierarchy/S5_PROTOCOL.md` | Include existing one-pass promotion/status clarification. |
| `experiments/case118_annual_hierarchy/experiment_log.md` | Include closeout history and explicit pending/completed status. |
| `experiments/case118_annual_hierarchy/s5_analysis.py` | Include the reviewed single-reconstruction promotion correction. |
| `tests/test_case118_s5_execution.py` | Include its promotion regressions. |
| `tests/test_case118_s5_analysis_parallel.py` | Include spawned-process serial/parallel equivalence and error tests. |
| `tests/test_case118_s5_source_transition.py` | Include incomplete-segment rejection and completed-shard traversal checks. |
| `experiments/case118_annual_hierarchy/s5_closeout.py` | Include standalone preparation/rendering logic. |
| `tests/test_case118_s5_closeout.py` | Include calendar/event/energy and historical input-array checks. |
| `experiments/case118_annual_hierarchy/s5_closeout/` | Include the exact small report package listed in its README and disposition index below. |
| `experiments/case118_annual_hierarchy/S5_CLOSEOUT_CHECKPOINT.md` | Include this review/execution/file-disposition record, updated with decisions. |
| `plans/case118-tracy-2021-study-plan.md` | Include the owner-developed governing plan; mark only genuinely completed gates. |
| `.gitignore` | Include the owner-requested ignore rule for the local background recovery proposal. |
| `plans/s5-tracy-recovery-plan.md` | Preserve locally, ignored at the owner's request; exclude from this closeout commit. |
| Original `outputs/`, all accepted execution archives and source data | Preserve in place. No broad promotion or ignore-rule changes in commit 1. |

Proposed commit text, for use only after all gates are satisfied:

```text
experiment(case118): close out the analytical annual study

Document the actual toy inputs and accepted operator-assisted trajectory,
preserve reproducible input and DC-to-AC comparison evidence, and distinguish
descriptive grid/storage/recovery associations from causal claims. Persist
the complete independent analysis through the reviewed one-pass promotion.
```

## Questions reserved for the design pause

After commit 1, use the new scientific record to decide whether the eight
matched-window counterfactual proposal remains informative. Separate minimum
small repair, economic redispatch, and battery flexibility using common
AC-evaluated costs and matched boundaries. Decide what constitutes a material
improvement. If explaining the evening storage shift requires longer windows,
give that a separate comparison protocol rather than inferring a horizon
effect from the current three-hour run.

If studying a rule for when AC is useful, include DC-selected ordinary controls
and future held-out periods; outcome-selected large-correction windows cannot
estimate annual value or prospective predictive skill. Preserve required
extraction/locking/cost-audit support in Stage 0b. No new study or scheduler
redesign is selected or authorized by these questions.

## Exact closeout package file list

| File | Bytes |
| --- | ---: |
| `README.md` | 9,252 |
| `absolute_changes.png` | 217,963 |
| `aggregate_inputs.csv` | 957,115 |
| `bus_resources.csv` | 7,269 |
| `correlations.png` | 256,116 |
| `correlations_calendar.csv` | 1,890 |
| `correlations_raw.csv` | 1,891 |
| `correlations_rounded_calendar.csv` | 1,886 |
| `correlations_without_interventions.csv` | 1,885 |
| `dc_branch_loading.csv` | 7,466 |
| `devices.json` | 40,627 |
| `dispatch_source.json` | 7,309 |
| `dispatch_summary.csv` | 950 |
| `final_inputs.npz` | 12,343,980 |
| `hourly_comparison.csv` | 7,022,334 |
| `input_heatmaps.png` | 235,543 |
| `input_summary.json` | 1,655 |
| `monthly_inputs.csv` | 6,451 |
| `monthly_recovery_groups.csv` | 8,662 |
| `operation_summary.json` | 888 |
| `prior_analysis_summary.json` | 4,445 |
| `provenance.json` | 4,187 |
| `retained_merge.json` | 3,216 |
| `shortfall_events.json` | 3 |
| `signed_changes.png` | 153,443 |
