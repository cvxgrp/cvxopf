# Stage 0b validation record

Validated 2026-09-17 against the current uncommitted promotion package.
All commands ran from the repository root. No new OPF solve or full annual
archive audit was performed.

## Environments and executed checks

Project checks used `UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --no-sync
--extra dev` with the existing Python 3.11 environment:

- `pytest tests/test_case118_s5_coverage.py tests/test_case118_s5_dashboard.py -q`:
  18 passed; dashboard module skipped because this environment lacks optional
  matplotlib. The existing CVXPY OpenMP-runtime warning was emitted; no solve ran.
- `ruff check tests/test_case118_s5_coverage.py tests/test_case118_s5_dashboard.py`:
  passed.

Plot/notebook checks used the same `uv run --no-sync --extra dev` prefix with
this explicit existing interpreter, without changing project dependencies:
`/Users/bmeyers/.cache/uv/environments-v2/gasoline-trend-comparison-214caeb59c01069b/bin/python`.
Its versions were Python 3.13.2, matplotlib 3.11.1, marimo 0.23.15,
NumPy 2.5.1, and pandas 3.0.5. The notebook preserves its original marimo 0.24.2
script pin; this validation used the available compatible 0.23.15 environment,
not a new installation of that pin.

- `-m unittest tests.test_case118_s5_dashboard -v`: eight passed, including clean-output-tree CLI reproduction,
  completion-wrapper directory creation/overwrite refusal, and the
  complete saved calendar and DC-feature alignment. Matplotlib emitted its
  existing `set_bad` pending-deprecation warning.
- `-m marimo check experiments/case118_annual_hierarchy/analysis/s5_dashboard.py`:
  passed.
- `-m marimo export html experiments/case118_annual_hierarchy/analysis/s5_dashboard.py
  -o /private/tmp/cvxopf-stage0b-dashboard.html`: completed successfully.
  Initial sandbox execution could not bind marimo's local kernel socket;
  the same export succeeded with the local socket permission. No collector
  or solver was invoked.
- `experiments/case118_annual_hierarchy/analysis/render_saved_timing.py
  --output /private/tmp/cvxopf-stage0b-timing`: completed successfully.

## Scientific and relocation checks

The original and promoted `load_snapshot` results have exactly equal calendar
matrices and tensors, including NaNs. The joined DC-feature/AC-correction
frames match exactly for all 8,760 intervals. Raw and calendar-adjusted
correlation tables, ranked frames, and selected frames match exactly for all
four combinations of rounding and intervention exclusion.

AST comparison confirmed unchanged numerical functions: dispatch `finite` and
`metrics`; calendar `fold_calendar`, `load_snapshot`, `plot_heatmaps`; stress
`correlations`, `stress_tables`, pair definitions and plots; notebook signed
and opposing calendar transforms/plots and solve-time plot. Path/default/UI
changes are recorded in the promotion manifest. Three timing collectors are
exact byte copies. Saved timing rendering changes only publication/destination
handling around the preserved plotting computations.

All four selected timing PNGs regenerated with **identical decoded RGBA
pixels**: completion zoom, period P95, post-reboot distribution, and parallel
throughput. PNG file bytes differ due to encoding/metadata, so this is a pixel
comparison, not a claim of byte-identical regeneration. The promoted original
PNGs themselves are byte-identical copies. Calendar, stress-matrix and
throughput figures were visually inspected for axes, labels and clipping.

A copy of `analysis/` under `/private/tmp/cvxopf-portable-2e8esazb/experiments/
case118_annual_hierarchy/analysis` successfully loaded all 8,760 hours and
rendered calendar/stress plots without the original repository on its import
path. A Python audit hook rejected any file open under the original ignored
`outputs/` or `presentations/` paths; none occurred. Raw archive collectors
are intentionally separate from this portable saved-data path.

All 51 promoted destinations passed byte-count/hash verification and are
eligible for ordinary Git tracking. All noncache inventory source hashes and
all selected source hashes remained unchanged. The current documentation's
local file links resolve; preserved historical artifact text keeps its original
source-relative paths as explained in the disposition. Python syntax checks
and `git diff --check` passed. Numerical protocols and runtime sources were
not altered by these checks.

Independent review status is recorded in
[the disposition record](../S5_ARTIFACT_DISPOSITION.md).

## Owner-requested dashboard layout follow-up

Matrix and Tensor now appear in tabs beside Inputs, which displays the already
tracked report figure `s5_closeout/input_heatmaps.png` exactly. This adds that
sibling file to the saved viewer's runtime dependencies; the earlier isolated
copy check predates this addition. No input values or plot definitions changed.
Changes were made through the active marimo code-mode context and saved by
the runtime. Live cell execution and tab/weekday/metric interactions were
checked; selected-tab state survives control-driven rerendering. `marimo check`
passed. Temporary widget-format errors during scratchpad interaction checks
were corrected; they did not change notebook code or scientific data.

## Signed-ramp scatter for owner evaluation

Added through live code mode: signed backward net-load ramp versus signed
executed AC fleet battery power, colored by study hour. Power is reconstructed
as aggregate DC discharge minus charge plus the signed AC-minus-DC power
change. Differencing precedes intervention filtering; the undefined first
ramp is omitted (8,759 points with the default filter). Ramp magnitudes match
the retained absolute-ramp feature to 1e-9; reconstructed power stays within
the fleet rating. Live cells report no errors; marimo check passes and the
figure was visually inspected. This new descriptive view is for owner
evaluation and was not part of the original Stage 0b scientific review.

## Operating heatmaps for owner evaluation

Extracted compact DC generation, battery-power and end-SoC totals directly
from the 33 MB accepted outer archive after verifying its hash against the
final correction report. No AC archive scan or solver call was needed.
Checked shape, horizon, storage identities, boundary indices, SoC recurrence
and equality of boundary/result SoC before extraction. The first extraction
attempt rejected a mistaken interpretation of the descriptive generator-order
field; the corrected check uses the 54-entry generator-bus list and produced
no partial artifact from that failed attempt.

Live operating tabs reconstruct AC totals by adding retained signed
AC-minus-DC differences. DC battery and SoC totals agree with the independently
retained DC feature table; plotted AC battery power agrees with the signed-ramp
scatter. Both trajectories satisfy fleet power/SoC bounds, and panel color limits
match between AC and DC. Annual net generation difference is
1,211,848.695369983 MWh, matching the committed operation summary. Live cells
show no errors; marimo check passes. These owner-requested additions are
exploratory views under evaluation, separate from the base promotion review.

The owner subsequently moved Inputs to the upper Executed AC/DC plan group,
leaving Matrix/Tensor differences below. Applied through live code mode;
cell execution and marimo check passed. No numerical or data changes.

The first Inputs-width adjustment stretched the report PNG and did not match
the AC/DC typography. Replaced it with heatmaps from the report's hash-verified
aggregate input table, using the same calendar plotting helper as AC/DC. All
three render with a 15-inch figure width and the same font sizes; Inputs uses
five panels instead of three and is therefore taller. Report input values and
per-signal color-scale definitions are unchanged; month/hour axes now match
AC/DC. Live execution and marimo check passed.

## Synchronized 72-hour time-series follow-up

Added through the live runtime, using the same operating/input frames as the
heatmaps. Verified 72-point generation slices against retained data, synchronized
x limits through the final window ending 2026-01-01 00:00 UTC, and persistence
of the selected tab when the shared slider moves. Verified AC SoC line segments
break into two 36-point segments for a window centered on a shard boundary,
without losing or changing endpoint values. Restored the user's start/tab after
checks. Initial window visually inspected; marimo check and live cell checks
passed. No input or operating data was changed.
