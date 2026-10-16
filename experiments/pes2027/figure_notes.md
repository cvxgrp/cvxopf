# PES figure captions and scientific notes

Caption drafts and manuscript notes for the retained-data figures, 2026-10-13.
The plots contain panel labels, units and method legends; interpretation and
qualifications belong in LaTeX captions or the surrounding manuscript text.
This canonical file is copied beside the PNG/PDF exports by the renderer and
its identity is recorded in the rendering manifest. It does not alter evidence.

## Figure 1: Joint 24-hour dispatch

### Proposed caption

Common device cost, energy not served (ENS), and nondispatchable curtailment for six
matched 24-hour Case118 treatments and four network models. Cost is the sum of
generation, storage cycling and load-shedding costs; the network flow penalty
is excluded. Styled markers have stems from zero. Cost and ENS use symmetric-log
axes, linear below 1 cost unit and 1 MWh respectively and logarithmic above;
curtailment is shown on a linear GWh scale.

### Manuscript notes

- The forced-shedding convex results retain their original numerical
  qualifications and owner-approved source-use disposition. Their inclusion
  does not upgrade their historical classifications. Uniform filled markers are
  a presentation choice, not an acceptance label.
- Raw numerical-scale values, including signed values, are preserved. A marker
  indistinguishable from zero is not a statement of exact arithmetic zero.
- Because the cost and ENS axes are nonlinear, stem-length ratios are not
  ratios of those quantities. The axes show their wide dynamic ranges; use
  retained values for quantitative percentage comparisons.
- SOCP relaxes the matched AC problem. Copper plate relaxes the network flow
  feasible set, which also carries a nonnegative objective penalty. Plotting
  common device cost alone does not establish a universal objective ordering.
- All full AC references are accepted local solutions, not certified global
  optima. Network-model names denote the project's stated formulations;
  network flow is not a phase-angle DC power-flow model.

## Figure 2: Numerical preparation comparison

Pending the separately implemented matched preparation study. No historical
unmatched runtime comparison is substituted and no result is fabricated.

## Figure 3: Fixed battery-power transfer

### Proposed caption

Changes in common device cost, ENS, and nondispatchable curtailment when each simpler
model's complete battery real-power schedule is fixed in the matched 24-hour
AC problem. Differences are transfer minus full AC; positive values indicate
increases. The three panels use the same signed symmetric-log limits as
Figure 4, with linear regions of +/-100 cost units and +/-1 MWh. The `n/a`
row denotes transfers without an accepted result, not zero differences.

### Manuscript notes

- Fifteen of eighteen retained fixed-power transfers were compatible. The
  deficit-to-surplus treatment retains two native rejections and one timeout;
  no rejected or missing point is assigned an economic or service outcome.
- Solver failures themselves do not prove infeasibility. Subsequent local
  loop analysis independently excludes all three exact donated power vectors
  at hour 2452 under the unchanged AC model. Keep that later certificate
  distinct from the original numerical dispositions.
- The receiving AC model fixes battery real powers while allowing other
  controls to vary. It removes the redundant terminal equality, retains the
  endpoint as an independent check, and initializes from the matched joint
  AC point with battery power and energy replaced by the donor schedule.
- The three forced-shedding convex donors keep their original source
  qualifications. Acceptance of a receiving AC transfer does not retroactively
  change the donor's status.

## Figure 4: Energy-signpost AC rollouts

### Proposed caption

Changes in common device cost, ENS, and nondispatchable curtailment for three-hour
AC receding-horizon rollouts guided by retained energy signposts. Each window
starts from realized battery energy, optimizes intervening controls, and
executes its first hour; windows shorten at the end of the 24-hour treatment.
The AC-signpost arm is an oracle control using the matched full AC energy
trajectory, not its future dispatch or network initialization. Differences
are rollout minus full AC, with axes matched to Figure 3. Totals include
executed first actions only, not overlapping prediction objectives.

### Manuscript notes

- All 24 reconstructed rollout days (576 executed hours) passed the existing
  executed-day physics and accounting checks. Forced-shedding days also passed
  the retained input-only ENS lower-bound witness.
- This extraction checks archive identities, selected-controller supervision,
  state continuity and complete executed-day physics/accounting. It is not a
  full native-start, losing-attempt or recovery-policy replay.
- Original individual initial/final energies and physical constraints are
  retained. No donor power schedule or intermediate donor energy path is fixed
  throughout an AC window; only the moving terminal energy signpost is imposed.
- Tiny signed differences for the AC-signpost control are retained as measured.
  Lower cost than a local full AC reference is possible; these comparisons
  neither certify global optimality nor establish unique dispatch trajectories.
- The forced-shedding source qualifications remain unchanged. A complete
  accepted rollout is a new receiving-controller result, not a relabeling of
  its source solve.

## Shared presentation and evidence conventions

Scenario order, donor colors, styled markers and stems are identical across
figures. Figure 1 shows absolute outcomes; Figures 3 and 4 show signed
differences from full AC, with common
axis limits so their magnitudes can be compared directly. All figures are
7.2 inches wide, approximately a two-column IEEE figure, with vector PDF and
190-dpi PNG exports. Final placement and caption length remain author choices.

The unrounded metric snapshot and retained source hashes are in
`figure_data.json`; the source, notes and export hashes are in `manifest.json`.
Render-only iteration reuses that snapshot without invoking an optimizer or
rewriting any original joint, transfer, or rollout artifact.
