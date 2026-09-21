# Case118 AC timing study: add spatial P/Q vectorization

Status: proposed plan only. The owner committed and pushed the library correction as `4e0a91847745a0b8754eb0152fad898261ff9f4e`. Runner changes have not been implemented, and the new replay has not started. Work stays in the existing checkout. The owner reviews and commits all changes and handles pushes. Runner implementation requires owner approval of this plan; execution additionally requires clean independent review, owner review and commit of runner changes, and explicit owner launch authorization.

## Agreed scope

Run **one additional replay of the exact same 120 primary-winner and six helper-winner windows**. Reuse the two completed timing sets:

| Timing set | Time vectorization | Spatial P/Q vectorization | Action |
| --- | --- | --- | --- |
| Original stepwise | No | No | Reuse recorded timings |
| Previous replay | Yes | No | Reuse recorded timings |
| New replay | Yes | Yes | Run the frozen 120+6 set once |

A spatial-vectorization-only timing run is outside this plan. If the combined result is surprising, discuss a follow-up with the owner; do not automatically launch another condition. The unchanged helper policy may produce more than 126 solver attempts, but this remains one replay of 126 selected windows.

Previous experiment and results: [README](../case118_vectorization_replay/README.md) and [report](../case118_vectorization_replay/REPORT.md). The original stepwise timings and the time-only replay remain immutable comparison evidence.


## Question and comparison

Measure the same Case118 three-hour AC windows with P/Q constraints vectorized across both Ybus entries and time. Compare each window with (1) its original stepwise result and (2) the completed time-vectorized-only replay. The new build uses sparse P/Q storage and `temporal_assembly="vectorized"`; the library correction independently covers both temporal representations and both storage layouts.

The primary comparison is new combined vectorization versus the previous time-only replay. Include the original stepwise comparison for continuity. This is a paired historical timing comparison, not an estimate isolating vectorization from dependency and machine-condition changes.

## Frozen inputs and execution policy

Reuse the existing sample file byte for byte:

`outputs/case118_vectorization_replay/sample.json`

SHA256: `f950b14b061b60a582526d5a4d30ac02124ef2f1a62ddc41e6c74343f8750ddb`.

It contains the same 120 historical primary winners and six historical helper winners, seed 20260920, original strata, inclusion probabilities, weights, and random order. All 655 unique directly referenced historical artifacts were checked against their retained SHA256 values without mismatches during preparation. Revalidate references at execution. Do not redraw or replace failures.

Preserve original loads, renewable availability, initial storage state, terminal targets, costs, solver settings, and historical preceding-controller initializations. Verify regenerated named starts exactly and their mapping to native time-last variables. Do not feed new replay results into subsequent windows. Preserve within-window use of new target-free helper solutions and original perturbation seeds/draw order.

Use the original `SpeculativeSupervisor`, `SubprocessBackend`, and `WindowRace`: two primary windows on distinct original shards and one shared helper, with unchanged eligibility, budgets, audits, cancellation, and memory policy. Add no primary timeout. Preserve every attempt, including rejected, failed, and canceled attempts; winners may change.

## Required runner adjustments after plan approval

1. Add an explicit destination and frozen-sample input so the new run cannot overwrite the completed replay or invoke the sample generator. Proposed destination: `outputs/case118_spacetime_pq_replay` (must not already exist).
2. Replace the current historical exact-version rejection with an explicit, recorded exception for CVXPY 1.9.2 -> 1.9.3 and sparsediffpy 0.3.0 -> 0.6.1. Retain checks on all other recorded software versions and all physical request identities. Add sparsediffpy to new environment provenance. Record Python, dependencies, source hashes, and the owner-reviewed commit. Execution must use the owner-reviewed and committed changes; an uncommitted patch is not a substitute for this gate.
3. Keep original runner policy and initialization logic. Test output isolation, version-transition validation, frozen input/start identity, and three-way analysis using fixtures before launch.
4. Give the new analysis explicit inputs for both retained timing sets and its own destination. Do not overwrite the prior experiment's report or `artifacts/` directory. Parameterize temperature telemetry paths and run annotations; do not inherit the prior run's hard-coded telemetry timestamp or fan-start narrative.
5. Obtain a clean scientific review from `cvxopf-review` of the runner changes and preflight evidence, then owner review and commit, before execution. Wait for explicit owner launch authorization; neither protocol approval nor reviewer approval substitutes for that authorization.

## Measurements and interpretation

Compare the identical `before_ac_solve` -> `after_ac_solve` interval, including canonicalization, solver work, and start persistence. Do not label it pure IPOPT time. Report paired solve speedups, original-weighted primary-cohort mean and quantiles, geometric mean paired speedup, and window latency. Report the six historical helper winners separately.

Also retain construction and initialization-preparation time, sampled peak RSS, attempt counts, helper launches, cancellations, winner changes, statuses, physical acceptance residuals, objective differences, and dispatch/state differences. Inspect objective relative differences above 0.1%; do not silently exclude them. AC solves are nonconvex, so altered trajectories or local solutions remain possible.

Collect temperature/frequency telemetry from the outset, record cooling conditions, and keep them stable throughout the run. Record concurrent machine activity and interruptions. The prior replay changed cooling partway through; its partial telemetry does not support a causal cooling comparison. If telemetry is unavailable, report that limitation explicitly.

The previous replay took about 72 minutes; that is a planning reference, not a completion guarantee. There are no new runtime caps. Preserve partial evidence if interrupted and report completion explicitly. Readiness requires all 126 windows accounted for, every attempt accounted for, retained hashes and acceptance audits checked, and clean scientific review of the final three-way report.

## Deliverables and review

Keep the reviewed plan, runner/analysis code, and final human-readable report under version control. Keep raw generated run artifacts under the ignored `outputs/case118_spacetime_pq_replay/` directory. Any compact evidence tables or figures selected for the experiment directory must be explicit reviewable additions; do not add raw output trees.

Before execution, `cvxopf-review` must return a clean scientific review of runner changes and preflight evidence. After completion, it must review the three-way comparison and its supporting evidence before owner handoff. This plan alone does not authorize implementation, execution, commits, pushes, or PR closure.
