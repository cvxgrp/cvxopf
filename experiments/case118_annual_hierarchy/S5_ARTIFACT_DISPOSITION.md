# S5 Stage 0b artifact disposition

Status: implementation and verification complete; independent scientific review
by `cvxopf-review` is CLEAN. The owner approved and committed the separate
Stage 0b package as `0ae9d86`. Subsequent owner-requested dashboard tabs and signed-ramp
scatter and AC/DC operating heatmaps are documented in the validation record;
those descriptive additions were not part of the original promotion review.
The completed scientific closeout was committed separately as `9c26366`.
This package preserves evidence and tools; it does not begin Stage 0c solves
or Tracy input generation.

## Inventory and exact promotion

[output_inventory.csv](analysis/output_inventory.csv) records all 737 files
present in `outputs/` at inventory time (516,103,223 bytes). Each row identifies
its scenario, purpose, producing code where recoverable, dependencies,
disposition, and destination where applicable. Scientific/archive files have
literal SHA-256 and byte counts; interpreter/UI caches and OS metadata are
explicitly temporary and have no scientific hash authority. Later generated
validation outputs are outside this point-in-time inventory.

[promotion_manifest.json](analysis/promotion_manifest.json) is the exact
source-to-destination list: 51 selected sources, 25,484,474 original bytes
(24.30 MiB). Of these, 47 are in `outputs/`; four presentation scripts are
explicit transitive dependencies. Every entry records original and promoted
bytes/hashes and any transformation. Original sources remain unchanged.
Unlisted `outputs/` material remains in its original ignored location; nothing
was deleted. There is no bulk output promotion or ignore-rule expansion.

The manifest covers scientific content and relocated code. New support files
are this disposition, the analysis README, validation record, inventory, manifest, and package
`__init__.py`; the Tracy/analysis plans now link the promoted protocol and
record the future adapted dashboard requirement. The two promoted tests live
in `tests/`. These are part of the same review package.

## Group dispositions and reproduction dependencies

| Material | Disposition and rationale | Producer / dependencies |
| --- | --- | --- |
| Final dashboard snapshot `20260917T152634.819559Z` | Promote selected dispatch, completion, period and solve tables/metadata plus displayed PNGs. Retain duplicate render products and race details in archive. | Original `dashboard_support.refresh_snapshot`; dispatch analyzer, completion/period/solve collectors and presentation renderer. Saved viewer needs only promoted files. |
| Earlier dashboard snapshots and `LATEST.json` | Retain historical; they are not final scientific authority. | Per-snapshot `dashboard.json` records collector hashes; original ignored run tree needed for raw re-collection. |
| Full annual DC features from `20260915T173653331961Z` | Promote features and report as an exact pair. Ignore the old 5,525-interval membership in final views. Other coverage versions remain historical. | `coverage_report.py`, fixture, outer archive and manifest. All 8,760 feature rows align to final dispatch identities. |
| Dispatch/stress/dashboard/coverage code and tests | Promote usable code; relocate paths and imports. Fixed data replaces discovery of newest ignored outputs. Remove live-refresh/deck publication and operator photo from promoted viewer. | Python, NumPy, pandas, matplotlib, marimo; archive extractors additionally need project imports and raw S4/S5 evidence. |
| Presentation timing collectors and renderer | Promote three collectors exactly; relocate completion-band wrapper; extract saved-data renderer with explicit fresh output. Preserve historical timing definitions. | Collector environment variables and raw run receipts; saved renderer uses promoted tables/events only. No TeX or ignored presentation dependency. |
| Other `outputs/analysis` and standalone plot scripts | Retain historical: interim timing views and superseded exploratory scripts, not current results. | Nearby `plot.py`, `render.py`, `analyze.py`, or named script where recoverable; original checkpoints/receipts and plotting environment. Unresolved provenance is labeled in the inventory. |
| `s5_authority`, speculative cutover evidence, operator runbook | Retain archived authority, not instructions for new studies; large/raw records already referenced by accepted S5 artifacts. | S5 launcher/supervisor, process receipts, source fingerprints and checkpoint identities. Local ignored archive remains necessary for full reconstruction. |
| Interval 2448/2450/6122 diagnostics | Retain archived recovery evidence; do not promote failed/diagnostic starts as accepted actions. | Retained replay/attempt code where present, exact source window, fixture and solver environment. |
| Successful closeout analysis and startup failure | Promote completion/run/resource records, text logs, and archival supervisor source. Keep duplicate printed summaries/status views in archive. | `s5_analysis.py` invoked by the retained `supervise.py.txt`; latter is archival source, not a reusable launcher. `.log` bytes copied to `.txt` names. |
| Reconstructed analysis-time S5 protocol | Promote explicitly as a reconstruction, not a contemporaneously captured file. | Successful analysis provenance and recorded pre-edit status; see its name and original run records. |
| `completed-analysis-summary.json` | Already preserved as `s5_closeout/prior_analysis_summary.json`; do not duplicate. | Earlier analysis output. Current accepted full result is `S5_RESULTS.json`. |
| Original counterfactual proposal | Promote to [protocol draft](../../plans/case118-toy-ac-counterfactual-protocol.md), retaining scientific arm conditions and updating approved selection/execution design. | Approved three-question and horizon plans; exact windows, arm initialization and numerical budgets remain Stage 0c work. |
| Operator observations and fan photo | Promote observations exactly; retain photo as supplementary local material. Timing events remain in figures. | Operator report, not instrumented thermal evidence; timing changes do not isolate causation. |
| Tracy source inspection | Promote inspection JSON, labeled future-study source inventory; this is not generated Tracy input data. | Historical source files listed in the JSON; provenance only, not a runtime dashboard dependency. |
| Notebook caches, preview exports and OS files | Caches/OS metadata temporary; earlier preview exports historical, reproducible from their source where identified. | Interpreter/marimo/browser. No scientific authority. |
| Resiliency classification spreadsheet | Retain out of scope; separate task. | Source producer not established by this S5 inventory. |

Preserved evidence files keep historical paths, timestamps and source hashes
unchanged. Relative paths in historical notes refer to their original source
directory, identified by the manifest. They do not silently become paths within
the promoted directory. The analysis README gives current entry points.

## Support for the approved follow-ups

Already tracked closeout arrays/tables remain in `s5_closeout/`, with extraction
and reporting in `s5_closeout.py` and audited aggregate accounting in
`s5_analysis.py`; they are not copied again. These cover toy input patterns,
DC loading, dispatch/storage differences, annual losses, curtailment, costs,
and recovery summaries. The promoted tools preserve aligned hourly adjustment
and pre-AC feature definitions and interactive exploration.

Stage 0c still needs selected device/branch first-action arrays, voltage/reactive
and storage margins, episode cards and the candidate index. Use the accepted
archive/checkpoint references and existing fixture/outer loaders for those
bounded extractions. Build matched locks/endpoints, R1/R2 objectives and audits,
incumbent transfer, common-cost comparisons, and arm-specific initialization
as separately reviewed implementation. Do not treat this package as having
implemented episode selection or the counterfactual experiment.

The separate horizon study additionally needs causal per-variant rolling state,
explicit horizon/target timing, end-shortening flags, and once-counted executed
cost/resource accounting. Periods come from the first analysis's findings.
The existing two-main/one-helper supervisor is retained in the repository;
this promotion neither redesigns it nor launches workers.

The future Tracy dashboard is an explicit owner requirement: similar useful
views, adapted to Tracy inputs, timestamps, device choices and diagnostics.
The fixed toy explorer remains a separate reference.

## Verification and review

- Exact original/promoted calendar arrays and stress tables for all 8,760
  intervals, raw/rounded and with/without operator interventions.
- Numerical function syntax unchanged for dispatch metrics, calendar folding,
  signed/opposing heatmaps, stress correlations/plots and notebook solve plots.
- 18 coverage tests and eight calendar/dashboard/reproduction tests passed.
- `marimo check` passed; HTML export executed successfully on retained data.
- Saved timing figures rendered successfully to a fresh temporary directory; all
  four selected timing PNGs reproduce identical decoded pixels.
- Source selection review by `cvxopf-review`: CLEAN; independently checked all
  51 source hashes and complete 737-file inventory, DC/dispatch identities and
  selected feature arrays against the committed closeout.
- All destination hashes, ordinary Git eligibility, portable loading without
  ignored outputs/presentation reads, current documentation links, syntax and
  visual checks passed. Final independent implementation review is CLEAN: the
  reviewer independently reran all 26 relevant tests and equivalence/hash checks.
  The review caught missing output-parent creation in two scripts; both are
  fixed and covered by the reproduction tests. No material findings remain.

See [VALIDATION.md](analysis/VALIDATION.md) for exact checks, environments,
optional-dependency handling and the marimo version used.

No OPF solve, full annual audit, original output mutation, or Git commit was
performed for Stage 0b validation. Owner approval and commit 2 are the remaining
exit gate after the package review; Stage 0c implementation starts afterward.

### Owner-requested operating-trajectory addition

The original 51-source promotion list is unchanged. The subsequent dashboard
request adds `analysis/extract_dc_operating_totals.py` and two compact files in
`analysis/artifacts/operating_totals/`. They are identified separately under
`owner_requested_additions` in the manifest. These new derived totals support
generation/power/SoC heatmaps, without modifying original outputs or accepted
scientific records. See the analysis README and validation record for method,
source identity and reproduction.
