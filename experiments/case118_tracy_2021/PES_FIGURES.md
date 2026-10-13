# PES result-figure review drafts

Non-solving figure drafts, 2026-10-13. These are for visual iteration, not a
manuscript update or a new numerical study. Current output:
`results/pes_figures_review_001/` (ignored; PNG and vector PDF for each figure).

| Figure | Content |
| --- | --- |
| `01_joint_dispatch` | Grouped horizontal bars for six matched 24-hour cases and four models: common device cost, ENS and renewable curtailment. |
| 02, pending | Baseline/prepared numerical comparison; no completed matched results were available when these drafts were generated. |
| `03_fixed_schedule_transfer` | AC outcomes with fixed donor battery powers, relative to unrestricted joint AC. The three unresolved solver outcomes remain explicit; the caption separately identifies subsequent loop exclusions. |
| `04_ac_rollouts` | Executed-only 24-hour totals from three-hour AC rollouts, relative to joint AC, including the AC-signpost control. Axes match Figure 3. |

The plotter reuses `e3_results`, retained-transfer admission, and the rollout
`audit.trajectory` reconstruction. All 24 reconstructed rollout days pass its
physical/accounting checks; forced-shedding trajectories also pass the existing
input-only ENS witness. Source references, executed-window hashes, controlling
records, supervision and state continuity are checked. This is **not** a full
replay of native canonical starts, losing attempts, or recovery policy. Joint
and transfer classifications are historical retained evidence, not new solves
or new native acceptance decisions.

Common device cost excludes `dc_loss_cost`; integrated cost is not multiplied
by delta again. Energy sums use delta once. Rollouts aggregate only executed
first actions, never overlapping prediction objectives. Source qualifications
remain in the retained data and accompanying notes, not marker styling.
Numerical-scale positive and negative values are not clipped; symmetric-log
axis definitions and linear ranges are documented in the caption notes.
Local AC references are not globally optimal certificates. Signed differences
are descriptive, including near-zero negative values for the AC-signpost control.

`figure_data.json` retains unrounded metrics, source hashes and trajectory audits;
`manifest.json` hashes that snapshot, the PNG/PDF files and `figure_notes.md`.
The canonical [figure notes](figure_notes.md) are copied beside every rendering
bundle. They contain proposed LaTeX captions, interpretation and qualifications;
plots retain only panel labels, units, legends and minimal missing-data indicators.
No historical artifact is rewritten. Figures are 7.2 inches wide for two-column
PES placement; final manuscript placement remains separate from this iteration.

Generate a fresh bundle from retained owner-supplied archives:

```sh
XDG_CACHE_HOME=/private/tmp/cvxopf-pes-cache MPLCONFIGDIR=/private/tmp/cvxopf-pes-mpl \
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 uv run --offline --no-sync --extra notebook \
python -m experiments.case118_tracy_2021.plot_pes_results \
  --output experiments/case118_tracy_2021/results/pes_figures_review_002
```

For layout iteration only, add `--render-only` and point to an existing bundle.
That rereads the retained metric snapshot and overwrites only its review figures
and rendering manifest, not numerical evidence. It does not revalidate raw input
archives; use a fresh collection if the scientific extraction changes.
