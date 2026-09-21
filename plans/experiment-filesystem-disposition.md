# Experiment filesystem repair: file-by-file disposition

Status: for owner review before implementation. The four design decisions in
[the repair plan](experiment-filesystem-repair.md) are approved. This document
specifies their proposed application; no implementation has begun.

## Reading this disposition

Paths are repository-relative. Each table heading supplies the directory prefix
for its filenames. **Keep** means retain the existing working-tree change with
no further edit. **Revise** means the existing change is insufficient or needs
correction. **Preserve** means leave the file's current bytes unchanged; this is
not approval of separate scientific work. **Add** means a proposed new file.

The inventory below covers all 38 modified tracked files and all 14 untracked
files present at the start of this disposition, including the repair plan.
It also names proposed additions and protected existing authorities. Existing
uncommitted work remains unstaged. No file moves or deletions are proposed.

## Repository policy and shared path handling

| File | Disposition | Exact intended scope |
| --- | --- | --- |
| `.gitignore` | Keep | Retain `/not-tracked/` in place of `/outputs/`; raw experiment results remain covered by experiment-local ignore rules. |
| `CLAUDE.md` | Revise | Retain the approved self-contained-experiment procedure. State that historical migrations require owner approval; the procedure is not blanket permission to move prior studies. |
| `experiments/retained_paths.py` | Revise | Preserve the small `retained_path(saved)` interface. Replace the handwritten cross-project table with exact file mappings read from the time-only and P/Q replay relocation manifests. Do not duplicate counterfactual mappings or map unrelated annual/M14 trees. Leave unmapped paths unchanged; no directory or filename guessing. Keep the existing read-only relocation verifier as a separate function, independent of runtime mapping, covering all five manifests. |
| `tests/test_retained_paths.py` | Revise | Use small manifest fixtures for actual replay/P/Q callers. Cover recorded relative/absolute paths, unmapped and external paths, changed-file hash rejection, and ignored raw-output defaults. Do not require the owner's full local archives for unit tests. |
| `plans/experiment-filesystem-repair.md` | Revise — planning only | Record approval of decisions 1–4 and the pending disposition review; link this document. Done while preparing this disposition. |
| `plans/experiment-filesystem-disposition.md` | Add — planning only | This review document. |

The two replay manifests become runtime dependencies of the narrow resolver.
Load and index both manifests once per analysis operation, then reuse that
index for every lookup in the operation. Do not re-read or parse the manifests
for each file lookup. Scope reuse to the operation so a later operation can
observe updated manifests. Verify with a focused test that multiple lookups
within one operation load each manifest only once.

The existing runner fingerprint must include those manifests as well as the
helper source, so future execution records identify the mappings used. Historical
execution fingerprints remain untouched. Before implementing, inspect recorded
references to confirm exact file mappings suffice; if an actual caller needs
directory translation or another experiment's historical paths, return to the
owner with that specific case instead of expanding the design silently.

## Annual Case118 study

Prefix: `experiments/case118_annual_hierarchy/`.

| File | Disposition | Exact intended scope |
| --- | --- | --- |
| `S5_ARTIFACT_DISPOSITION.md` | Revise | Keep existing group decisions. Distinguish original locations from current archive locations, remove contradictory present-tense location claims, and identify maintained tools versus historical source. Link the existing inventory, promotion manifest, relocation record, and result-location note. |
| `S5_CLOSEOUT_CHECKPOINT.md` | Revise | Preserve original paths in historical launch instructions/accounts. Add separately labeled current locations and a current reconstruction command where useful, rather than making a historical command appear to have used the new paths. |
| `S5_INTERVAL_2448_INTERVENTION.md` | Keep | Retain the current location of the recovery evidence; no change to the scientific account. |
| `S5_REPORT.md` | Revise | Preserve `outputs/s5_analysis/completed-analysis-summary.json` as the historical copy source and separately identify its current archive location. Keep the accepted report conclusions unchanged. |
| `experiment_log.md` | Revise | Restore the historical launch destination in the dated narrative and add a clearly labeled current-location pointer. |
| `analysis/README.md` | Revise | Make `analysis/s5_dashboard.py` and promoted analysis tools the explicit maintained entry points. Label originals as archival. Update reproduction destinations to the paths below. Explain that manifest hashes describe promotion-time versions, not necessarily today's edited tools. |
| `analysis/analyze_dispatch_changes.py` | Revise | Change only the default destination to `results/reproductions/dispatch_changes_<timestamp>`; retain `S5_DISPATCH_OUT`, input identities, numerical definitions, and fresh-output protections. |
| `analysis/analyze_stress_correlations.py` | Revise | Change only the default destination to `results/reproductions/stress_correlations_<timestamp>`; preserve saved inputs and metrics. |
| `analysis/collectors/refresh_completion_band.py` | Revise | Change only the default destination to `results/reproductions/s5_completion_band_<timestamp>`; preserve `S5_COMPLETION_OUT` and collector behavior. |
| `analysis/coverage_report.py` | Revise | Change only the default destination to `results/reproductions/s5_coverage/<timestamp>`; preserve explicit `--output`, calculations, and existing guards. |
| `analysis/render_saved_timing.py` | Revise | Keep the current archival-source pointer but label it as historical provenance. No rendering or numerical changes. Output remains explicitly supplied by the user. |
| `s5_closeout.py` | Keep | Retain the new paths for reading the original final snapshot and prior summary. Do not rerun package preparation or replace the accepted closeout package. |
| `RESULT_LOCATIONS_20260921.json` | Preserve | Keep all 735 original relocation records and hashes unchanged. |
| `RESULT_LOCATIONS.md` | Add | Short navigation note defining archival subtrees, maintained entry points, and fresh reproduction destinations. Reference existing manifests rather than repeating their inventories. |

Annual README examples will use `results/reproductions/s5_timing_reproduction`,
`results/reproductions/s5_dashboard_review.html`, and
`results/reproductions/dc_operating_totals_reproduction` for the explicit-output
commands. Ensure the documented parent-directory preparation is sufficient;
do not change additional tools just to make an example work.

Root-level affected test:

| File | Disposition | Exact intended scope |
| --- | --- | --- |
| `tests/test_case118_s5_dashboard.py` | Revise | Update the reproduction output expectation to `results/reproductions/stress_correlations_*`; retain existing numerical/reproduction and overwrite-refusal checks. |

## Counterfactual study

Prefix: `experiments/case118_counterfactual/`.

| File | Disposition | Exact intended scope |
| --- | --- | --- |
| `RESULT_LOCATIONS.md` | Keep | Retain the record of removed compatibility links and the existing resolver's role. Do not imply that scientific data was moved again. |
| `RESULT_LOCATIONS_20260921.json` | Preserve | Keep the 22 link-removal records unchanged. Verify targets still exist; recreate no aliases. |

## P/Q replay and diagnostics

Prefix: `experiments/case118_spacetime_pq_replay/`.

| File | Disposition | Exact intended scope |
| --- | --- | --- |
| `.gitignore` | Keep | Retain `/results/`. |
| `FOUR_WAY_DIAGNOSTIC.md` | Keep | Retain experiment-local preparation and execution destinations. Do not run these commands. |
| `PLAN.md` | Keep | Retain current locations for the frozen prior sample and new study outputs. No scientific design changes. |
| `PRIMARY_DIAGNOSTIC.md` | Keep | Retain current command destinations and pointer to the retained failed startup. |
| `README.md` | Revise | Keep maintained entry points and four-way report links. State that current-location verification is separate from review of scientific conclusions; align location/helper descriptions with the final narrow resolver. |
| `analyze.py` | Keep | Retain translation of the recorded prior comparison path. Validate against the narrowed resolver. |
| `diagnose_primary.py` | Keep | Retain experiment-local destination guards and recorded-start translation. No changes to starts, solver policy, or solve authorization. |
| `run.py` | Keep | Retain current output/prior paths and creation of missing parent directories. Preserve destination isolation and preflight checks. |
| `FOUR_WAY_REPORT.md` | Preserve | Separate study deliverable; no rewriting, regeneration, or new scientific endorsement in this repair. |
| `analyze_primary.py` | Preserve | Keep current bytes and resolver interface; verify compatibility without `--write` if a bounded read-only check is appropriate. Broader analysis review is outside this repair. |
| `artifacts/four_way/summary.json` | Preserve | Separate scientific evidence; no regeneration. |
| `RESULT_LOCATIONS.md` | Revise | Describe approved current locations and the narrower mapping source accurately. Distinguish preservation verification from scientific reconstruction. Remove any implication that the original root rename authorized all older moves. |
| `RESULT_LOCATIONS_20260921.json` | Preserve | Keep all 888 relocation records unchanged. This is one of the resolver's two mapping inputs. |

## Earlier time-only replay

Prefix: `experiments/case118_vectorization_replay/`.

| File | Disposition | Exact intended scope |
| --- | --- | --- |
| `.gitignore` | Keep | Retain `/results/`. |
| `README.md` | Revise | Keep the current frozen-sample location; link a short result-location note distinguishing retained runs from maintained code and tracked compact evidence. |
| `REPORT.md` | Keep | Retain the current raw-evidence location. No numerical or conclusion changes. |
| `analyze.py` | Keep | Retain the matching current-location text in report generation. Do not regenerate the accepted report. |
| `run.py` | Revise | Retain helper source fingerprinting; also fingerprint both explicit mapping manifests used by that helper. Preserve historical bindings and all execution policy. |
| `sample.py` | Keep | Retain new output default, parent creation, and resolver calls in `read`, `sha`, and `ref`. Validate that hash checks still reject changed bytes. Do not regenerate the frozen sample. |
| `worker.py` | Keep | Retain translation of recorded starts. No numerical changes or worker launches. |
| `RESULT_LOCATIONS_20260921.json` | Preserve | Keep all 1,328 relocation records unchanged. This is the other runtime mapping input. |
| `RESULT_LOCATIONS.md` | Add | Identify retained raw replay records, maintained analysis, and tracked sample/comparison artifacts. State that duplicate selected evidence is intentional. |

## Earlier M14 comparisons and notebook

Prefix: `experiments/m14_time_vectorization/`, except where stated.

| File | Disposition | Exact intended scope |
| --- | --- | --- |
| `AC_VECTORIZATION_REPORT.md` | Keep | Retain current pointers to raw trajectories and original runner snapshot. No scientific changes. |
| `M14D_SINGLENODE_REPORT.md` | Keep | Retain current figure and trajectory locations; verify links resolve. |
| `TRACY_WEEK_COMPARISON.md` | Keep | Retain current input/trajectory/log locations. |
| `analyze_tracy_week.py` | Keep | Retain current archive reads and output location. Do not regenerate comparison bundles or launch solves. |
| `check_network_trajectories.py` | Keep | Retain paths to original runner and raw trajectories; preserve checks. |
| `compare_network_vectorization.py` | Keep | Retain experiment-local raw directory. Do not run its solver modes. |
| `plot_singlenode_comparison.py` | Keep | Retain current output path and documentation. This script launches solves; do not execute it for this repair. |
| `RESULT_LOCATIONS_20260921.json` | Preserve | Keep all 50 relocation records unchanged. |
| `RESULT_LOCATIONS.md` | Add | Briefly distinguish retained comparison evidence and historical runner snapshots from maintained comparison tools. |
| Root `notebooks/tracy_week_solutions.py` | Keep | Retain relocated bundle path and existing digest check; no notebook redesign. |
| Root `plans/milestone-14-time-vectorization.md` | Keep | Retain current raw-evidence pointer. No changes to milestone acceptance or conclusions. |

## Protected evidence and unchanged maintained files

Each individual file in the five relocation manifests has the same disposition:
**preserve at its recorded current location, unchanged**. Those existing manifests
are the exact file-by-file lists; this document deliberately does not copy 3,023
records into another competing inventory. For the 22 link records, preserve the
target and retain the record of link removal rather than recreate a link.

In particular, archived dashboard/analyzer/test `.py` files are preserved as
historical source, even when their old relative imports or root calculations
prevent execution in the new location. No attempt will be made to maintain them.

The following authorities and maintained files also remain unchanged:

| File or existing evidence set | Disposition |
| --- | --- |
| Annual `analysis/output_inventory.csv` | Preserve original inventory and classifications. |
| Annual `analysis/promotion_manifest.json` | Preserve promotion-time hashes and transformations; do not update old hashes to make current source match. |
| Annual `S5_RESULTS.json` and `s5_closeout/` contents | Preserve accepted evidence; no regeneration. |
| Annual `analysis/artifacts/` contents | Preserve selected evidence, including intentional exact copies. |
| Annual `analysis/s5_dashboard.py`, `dashboard_support.py`, `dashboard_stress.py` | Keep as maintained entry points; no changes proposed. |
| Annual `analysis/extract_dc_operating_totals.py` | No change; only its documented explicit output destination changes. |
| Annual `analysis/VALIDATION.md` | Preserve historical validation record; do not rewrite it as validation of this repair. |
| `tests/test_case118_s5_coverage.py` | No change proposed; run affected existing checks as appropriate. |
| Counterfactual `RESULT_LOCATIONS.json`, `retained_files.py`, and current callers | Preserve existing mapping and verification implementation. |
| Annual and M14 `.gitignore` files | No change; already ignore `results/`. |
| `src/cvxopf/`, solver settings, frozen samples, execution/source bindings | No changes. |
| Root `not-tracked/` contents | No changes. |

## Implementation boundary and review handoff

Only the two planning documents are changed in preparing this disposition.
All table entries describing code/document changes are future actions.

After owner review, implementation will verify the baseline, apply only the
listed changes, and perform the repair plan's proportionate checks. Verification
will not launch solves or regenerate accepted artifacts. Report actual changed
files and verification outcomes here or in the repair plan; do not create a new
reporting framework. If an additional source file, evidence mutation, or broader
path abstraction proves necessary, explain it and ask before extending scope.
