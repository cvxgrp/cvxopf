---
name: milestone-7-hvdc-status
description: "Milestone 7 (HVDC) COMPLETE (2026-07-20, branch dcline); durable oracle/naming facts"
metadata: 
  node_type: memory
  type: project
  originSessionId: da9d833f-e393-464c-8586-36830dd69ef5
---

Milestone 7 (HVDC transmission links) is **COMPLETE** as of 2026-07-20, built
on branch `dcline` per `plans/milestone-7-hvdc.md`. All steps T0–T7 done;
**816 tests pass** (815 baseline + the new `TestHVDCYbusAgreement`). Not yet
merged to main — committed on `dcline`.

What shipped: `src/cvxopf/hvdc.py` (`HVDCLink`, validation, incidence,
`_hvdc_static_box`, `hvdc_from_dcline`, injection/operating/cost methods);
AC + lossy_dc integration (signed Convention-B injections, proportional loss
on fixed-direction links); `singlenode_dc` silently drops HVDC; results keys
`p_hvdc_in`/`p_hvdc_out`/`hvdc_loss`; public API re-exports; two examples;
Ybus-agreement test + committed fixture; CLAUDE.md + README + plan updated.

Durable non-obvious facts (still true, kept for reference):
- **Naming:** plan labels work items "Step 0"–"Step 7"; the user calls them
  "T0"–"T7". T0 == Step 0.
- **dcline fixture oracle** does NOT use pypower's `toggle_dcline` (broken under
  numpy 2.x across its ext2int AND int2ext userfcns). Instead a self-contained
  "Option A" solve: hand-built `_dcline_to_gens` (two dummy gens per DC line) +
  a custom P-coupling `formulation` userfcn `(1-L1)*Pgf + Pgt = -L0/baseMVA`,
  dropping `dclinecost` (matching pypower's own `t_dcline.py`), cross-checked
  against pypower's hardcoded expected array. Full rationale in
  `scripts/README.md`.
- **Ybus:** DC lines contribute NOTHING to Ybus (modelled as injections, not
  admittance branches); pinned by `TestHVDCYbusAgreement` against a committed
  static fixture (`scripts/generate_pypower_fixtures.py` `_run_dcline_ybus`).
- **Gate 6b** is consistency-based, NOT a Pypower value-match — see
  [[case9-dcline-optima-gap]]; the capstone report is
  `experiments/dnlp_vs_pypower/` (see [[dnlp-canonicalization-tractability-thesis]]).

See [[cvxopf-session-working-style]] for how the user wants work driven.
