# Completed toy-study explorer and analysis tools

This directory preserves the completed analytical Case118 study's dashboard and
its supporting data. It describes synthetic 2025 UTC inputs, not Tracy 2021.
The accepted scientific record remains [S5_REPORT.md](../S5_REPORT.md) and
[S5_RESULTS.json](../S5_RESULTS.json). See the
[artifact disposition](../S5_ARTIFACT_DISPOSITION.md) for scope and archives.

## Open the explorer

From the repository root, using the notebook's pinned marimo environment:

```sh
uv run --isolated experiments/case118_annual_hierarchy/analysis/s5_dashboard.py
```

For interactive editing or viewing with project notebook dependencies:

```sh
uv run --extra notebook marimo run experiments/case118_annual_hierarchy/analysis/s5_dashboard.py
```

The first command executes the notebook as a script; the second opens the
interactive dashboard. Python 3.12+ is required by its script metadata.
The explorer reads `artifacts/final_snapshot`, `artifacts/dc_features`, and the
compact DC totals in `artifacts/operating_totals`, and the report
input table `../s5_closeout/aggregate_inputs.csv` with its provenance record.
Inputs are rendered through the same plotting helper as AC/DC, using matching
figure width, text sizes and calendar axes while retaining the report values
and color-scale definitions. Inputs shares the upper tab group with Executed AC and DC plan. Matrix and
Tensor differences occupy the lower tab group; the weekday control is inside Tensor.
Tab selection persists when the metric or weekday changes.
Changing metric, weekday, rounding, or intervention controls uses this saved
data. It does not read raw AC archives or run optimization. The original live
refresh dashboard is retained as archival source in ignored `../results/s5_analysis/`.
Use the dashboard and tools in this `analysis/` directory as maintained entry
points. Archived scripts may require their former layout/environment; they are
not alternate supported tools. See [current result locations](../RESULT_LOCATIONS.md).

The final correction snapshot is `20260917T152634.819559Z`, with 8,760 accepted
first actions. Collectors captured timing/completion evidence separately; their
recorded timestamps remain visible. The annual DC features were captured at
`20260915T173653331961Z`. All 8,760 feature rows match the final dispatch's
outer archive, manifest, and fixture identities. That older report's 5,525-entry
completion list is historical and is **not** used to select dashboard intervals.

The dashboard retains signed net, total absolute, and opposing generation
changes, storage power and SoC differences, calendar views, exploratory stress
associations, and timing/recovery views. An owner-requested exploratory scatter
compares signed backward hourly net-load ramp with signed executed AC fleet
battery power (positive discharge), omitting the first undefined ramp. Its definitions are unchanged by
promotion. Rank associations are descriptive, with no causal or global-optimality
claim; inherited storage-state differences are explicitly retrospective.

## Reproduce plots and derived tables

The saved timing renderer reads only promoted data and requires a new destination.
New reproductions are separate from the historical archive subtrees:

```sh
mkdir -p experiments/case118_annual_hierarchy/results/reproductions
uv run --extra notebook python experiments/case118_annual_hierarchy/analysis/render_saved_timing.py --output experiments/case118_annual_hierarchy/results/reproductions/s5_timing_reproduction
uv run --extra notebook python experiments/case118_annual_hierarchy/analysis/analyze_stress_correlations.py
```

The latter writes a fresh timestamped directory under `experiments/case118_annual_hierarchy/results/reproductions`.
It also accepts explicit `--snapshot` and `--features` paths. Saved metadata
keeps original paths/hashes; these document historical collection and are not
runtime dependencies. Promotion-time transformations and byte hashes are in
[promotion_manifest.json](promotion_manifest.json); they describe the promoted
versions, not necessarily the current maintained source after later edits.

`analyze_dispatch_changes.py` re-extracts accepted first actions from the raw
study and verifies artifact hashes; `coverage_report.py` reconstructs DC
features and coverage from the fixture/outer/checkpoint evidence. They require
the original ignored study archive and project environment. Their default
outputs remain ignored, fresh directories. Running them is an explicit
re-extraction, unnecessary for opening the explorer or Stage 0b verification.

Historical timing collectors in `collectors/` retain their original definitions.
`collect_completion.py`, `collect_periods.py`, and `collect_solves.py` take
`S5_REPO_ROOT` and `S5_PLOT_OUT` environment variables (an existing fresh output
directory), and require the original ignored run/checkpoint/receipt tree.
`refresh_completion_band.py` invokes the local completion collector and takes
`S5_COMPLETION_OUT` (a new directory). None launches OPF solves. The saved
renderer avoids these archive dependencies for reproducing the promoted views.

The final snapshot's pre-rendered completion-band plot is preserved byte for
byte. Recreating its raw completion collection needs the historical archive;
`render_saved_timing.py` reproduces the selected saved-data timing figures.

## Verification and reuse

```sh
mkdir -p experiments/case118_annual_hierarchy/results/reproductions
uv run --extra dev --extra notebook pytest tests/test_case118_s5_coverage.py tests/test_case118_s5_dashboard.py
uv run --extra notebook marimo check experiments/case118_annual_hierarchy/analysis/s5_dashboard.py
uv run --extra notebook marimo export html experiments/case118_annual_hierarchy/analysis/s5_dashboard.py -o experiments/case118_annual_hierarchy/results/reproductions/s5_dashboard_review.html
```

The dashboard tests skip when the optional plotting dependency is absent.
Stage 0b additionally compared original/promoted arrays and correlation tables,
checked unchanged numerical function syntax, verified source/destination hashes,
and rendered saved timing figures. No full annual audit or new OPF solve was
needed; see [VALIDATION.md](VALIDATION.md) for actual checks/environments and the
disposition record for review status.

A future Tracy dashboard should reuse these useful components while adapting
its time axis, renewable/input views, device configuration, and diagnostics to
the new scenario. This is not yet that dashboard or a generic dashboard framework.

## AC/DC operating heatmaps

The Operating trajectories tabs above the difference heatmaps show gross
dispatchable generation (excluding renewables), signed fleet battery power,
and total end-of-hour fleet SoC. AC and DC share each panel's color limits.
DC totals come directly from the accepted outer archive; AC totals add the
retained executed AC-minus-DC differences. No solve or archive scan occurs
when opening these tabs. To reproduce the compact DC source in a new folder:

```sh
mkdir -p experiments/case118_annual_hierarchy/results/reproductions
uv run --extra dev python experiments/case118_annual_hierarchy/analysis/extract_dc_operating_totals.py --output experiments/case118_annual_hierarchy/results/reproductions/dc_operating_totals_reproduction
```

The extractor verifies the archive hash against the correction report and
records source and generated-file hashes, row order, units and SoC timing.
The default source is the retained local S4 outer archive; `--outer`, `--report`
and `--output` permit explicit paths. Original archives remain unchanged.

## Synchronized 72-hour time series

Below the operating heatmaps, Executed AC, DC plan and Inputs time-series tabs
use the same retained data. One start-hour slider (0 through 8,688, step one
hour) controls all three. The selected tab persists as the window changes.
Power/inputs are hourly steps; SoC samples are located at hour-end timestamps.
Y-axis ranges stay fixed across windows and match between AC and DC. Windows
never wrap the year. Shard boundaries are marked; AC SoC lines do not connect
across controller resets. Correlation filters do not alter these full-data views.
