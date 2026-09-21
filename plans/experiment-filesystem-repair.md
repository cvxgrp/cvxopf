# Experiment filesystem repair

Status: the owner approved decisions 1–4 on 2026-09-21, including retaining
historical material at its current locations. Implementation is paused pending
review of the [file-by-file disposition](experiment-filesystem-disposition.md),
as requested by the owner. Only planning documents have been changed in this task.

## Purpose

Make the experiment tree understandable without losing scientific evidence,
creating competing maintained tools, or treating an unauthorized change as an
approved precedent. Decide the desired organization before moving more files.

## Original request, actual changes, and proposed outcome

These are three separate questions.

The owner approved:

1. Renaming the ignored root folder from `outputs/` to `not-tracked/`.
2. Requiring future experiments to be self-contained.
3. Moving P/Q sparsity test results into their proper experiment folders.

The owner did not approve redistributing the older material covered by the S5
inventory and promotion documentation. The self-contained-experiment rule was
not permission to reorganize every historical experiment.

The previous operation also moved older annual-study, time-only replay, and
M14 comparison material, removed counterfactual compatibility links, and
changed readers, output defaults, documentation, tests, and repository guidance.
The inspected working tree has 38 modified tracked files, plus untracked
relocation records, a resolver and tests, and recent four-way study deliverables.
These changes must not be accepted or reverted as one undifferentiated batch.

**Proposed outcome:** retain the current physical locations, subject to owner
approval, while restoring the existing distinction between maintained tools,
selected scientific evidence, and historical originals. Do not move everything
back merely to undo the overreach. Retaining these locations would be a new
owner decision about the final state, not retroactive authorization of the move.

## Evidence and limits of this plan

The existing authorities are:

- [S5 artifact disposition](../experiments/case118_annual_hierarchy/S5_ARTIFACT_DISPOSITION.md).
- [Original S5 inventory](../experiments/case118_annual_hierarchy/analysis/output_inventory.csv).
- [Promotion manifest](../experiments/case118_annual_hierarchy/analysis/promotion_manifest.json).
- The five experiment-local `RESULT_LOCATIONS_20260921.json` records.
- The counterfactual study's pre-existing `RESULT_LOCATIONS.json` and
  `retained_files.py`.

The promotion manifest records 51 selected sources, with exact copies and
deliberate code adaptations. The inventory and disposition already explain why
originals were retained. They remain the basis for classification; this repair
does not replace them with a new scientific inventory.

The relocation records enumerate 3,001 moved files and 22 removed compatibility
links. A previous reviewer reported matching sizes/hashes and existing link
targets. This planning pass inspected the records and diffs but did not repeat
that full audit. Implementation must verify the evidence before changing it.

## Proposed final organization

No new directory hierarchy or bulk relocation is proposed.

| Location | Role after repair |
| --- | --- |
| Root `not-tracked/` | Unrelated local material; not the default destination for experiment outputs. |
| Each experiment's source, plans, and reports | Maintained entry points and reviewable scientific documentation. |
| Annual study `S5_REPORT.md`, `S5_RESULTS.json`, and `s5_closeout/` | Existing accepted scientific record and supporting package. |
| Annual study `analysis/` and promoted tests | Maintained analysis tools and saved-data dashboard. |
| Annual study `analysis/artifacts/` | Selected, tracked evidence; original promotion relationships remain recorded. |
| Relocated historical subtrees under experiment `results/` | Ignored archival originals and raw evidence, not alternate maintained analysis packages. |
| P/Q study `results/` | Retain the approved placement of raw P/Q study records. |
| Counterfactual study's existing directories | Retain the earlier established organization and existing resolver. |

The distinction is by documented role, not file extension. A historical `.py`
file remains evidence of the original implementation; its presence does not
make it a supported runnable tool. Some archived scripts rely on their former
directory depth. Preserve their bytes and explain that limitation instead of
repairing or importing them as current tools.

Intentional exact copies remain. Adapted originals and promoted counterparts
remain separate. No deduplication, deletion, new promotion, or scientific
reinterpretation is part of this repair.

## Changes proposed for implementation

### 1. Establish a bounded baseline

Record the current tracked diff and untracked deliverables. Check whether any
source-bound computation is active before touching its source files. Preserve
unrelated work and the recent four-way report, analyzer, and artifacts.

Verify all relocation-record sizes/hashes and removed-link targets. Check the
older S5 scientific inventory against its current locations. Distinguish a
historical source hash from a maintained implementation's current hash: an
adapted or subsequently edited tool need not match its original source.

If evidence is missing, a digest disagrees, or ownership is ambiguous, stop the
affected part and ask the owner. Do not regenerate evidence to make a check pass.

### 2. Make ownership explicit using existing documents

Update the S5 disposition and analysis README to state clearly:

- Which dashboard and scripts are maintained entry points.
- Which relocated paths contain archival originals.
- Which selected artifacts are exact copies and which code was adapted,
  referring to the existing inventory and promotion manifest.
- That archival source may require its historical layout/environment to run.

Reconcile contradictory present-tense statements about originals remaining at
their old locations. Keep historical paths identifiable as historical paths;
do not silently rewrite the account of where an earlier execution occurred.
Add current-location pointers separately where necessary.

Use existing experiment result-location documents, or a short experiment-level
result-location note where absent, to label the historical subtrees. Keep these
navigation notes outside the archived evidence trees so the archives remain
byte-for-byte unchanged. Do not create another dashboard, inventory framework,
or parallel set of artifact descriptions.

### 3. Separate archive reads from future writes

Keep maintained tool source in its existing location. Tools may read archived
evidence explicitly, but new reproductions should not be mixed into the
historical `results/s5_analysis`, `results/analysis`, or `results/s5_coverage`
trees.

For the annual study's maintained extraction and reproduction commands, propose
fresh destinations under `results/reproductions/`, retaining existing explicit
output overrides and refusal to overwrite an existing result directory. Adjust
only the relevant defaults, examples, and output-location test expectations.
Do not change numerical definitions, input selection, or accepted artifacts.

Keep the P/Q study's approved run locations. Do not redesign other completed
experiments' runners or change their scientific behavior as part of cleanup.

### 4. Resolve historical paths only where needed

Preserve paths embedded in hashed scientific records. Identify the actual
maintained readers that need historical-to-current translation.

The counterfactual study already has a manifest-backed resolver. Preserve it.
For the replay/P/Q readers that now require translation, propose narrowing the
new `experiments/retained_paths.py` to their actual needs and deriving mappings
from the existing relocation records rather than maintaining a second broad,
handwritten location table. Exclude duplicate counterfactual mappings.

Load and index the two relocation manifests once per analysis operation and
reuse that index for all lookups in the operation, rather than parsing the
manifests for each file lookup.

Keep the interface small: translate a recorded local path and preserve hash
checking at the reader. Unmapped paths must not trigger a guessed filename
search or select a different artifact. Do not create a generic registry,
filesystem compatibility links, or a new public package API.

Keep relocation verification distinct from runtime path translation. Decide
the exact helper changes from the caller inventory; if the narrow design above
does not suffice, present the discrepancy before extending it.

### 5. Review the existing diff by purpose

Retain the approved root rename and future-experiment guidance. Clarify that
future policy does not itself authorize migration of historical material.

Review the remaining hunks individually:

| Change category | Proposed treatment |
| --- | --- |
| P/Q result locations and required readers | Keep and verify within the approved study scope. |
| Older result-location references | Keep current-location navigation if the proposed final locations are approved; preserve historical execution descriptions. |
| Annual analysis output defaults | Adjust to fresh reproduction destinations as described above. |
| Broad historical-path resolver and new tests | Narrow to demonstrated callers and meaningful identity/failure checks. |
| Four-way report, analyzer, and compact evidence | Preserve as separate study work; change only necessary location handling, without endorsing scientific conclusions through this repair. |
| Existing inventories, promotion hashes, raw records, archived source | Preserve unchanged. |

Do not run a blanket Git restore. The owner's existing changes remain unstaged.

## Verification and completion criteria

Validation is proportionate to a filesystem/documentation repair:

1. Repeat relocation and scientific-inventory checks after implementation;
   demonstrate that archived evidence bytes and promoted scientific artifacts
   were not changed by the repair.
2. Confirm raw result trees remain ignored and the new diff contains only
   intended reviewable source/documentation changes.
3. Check current navigation links and historical-to-current mappings. Test
   required relative/absolute references, unmapped paths, and digest rejection.
4. Run affected existing analysis/dashboard/path tests using the repository's
   prescribed environment. Where reproduction is necessary, use a fresh
   temporary destination and compare the relevant numerical data or figure
   pixels; do not overwrite accepted evidence.
5. Verify the promoted dashboard still loads its saved evidence without using
   an archived dashboard implementation. No OPF solve, experiment rerun, broad
   refactor, or full scientific revalidation is authorized by this plan.

Present the owner with the final layout, maintained entry points, file-by-file
change summary, checks performed, and remaining limitations. The owner stages,
commits, and pushes. Completion requires that the owner can explain which
files to use and why apparently duplicate originals remain.

## Decisions requested before implementation

Approval of this plan would specifically approve:

1. Retaining the relocated historical material at its current experiment-local
   locations, with explicit archival status, instead of moving it back.
2. Keeping intentional copies and adapted historical source, using the existing
   disposition and manifests to explain their relationships.
3. Separating new annual-study reproductions from the historical subtrees via
   `results/reproductions/`.
4. Narrowing historical-path handling as described above, while preserving the
   counterfactual resolver and immutable evidence records.

The owner has approved these four choices. This approval does not erase the
distinction between the original authorized scope and the broader moves that
were performed without asking. The owner subsequently requested a file-by-file
disposition before implementation; that review is the next gate.
