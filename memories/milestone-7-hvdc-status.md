---
name: milestone-7-hvdc-status
description: "Milestone 7 (HVDC) progress, T0/Step-0 done, and the T-vs-Step naming"
metadata: 
  node_type: memory
  type: project
  originSessionId: da9d833f-e393-464c-8586-36830dd69ef5
---

Milestone 7 (HVDC transmission links) is being built on branch `dcline` per
`plans/milestone-7-hvdc.md`.

- **Naming:** the plan labels work items "Step 0"–"Step 7"; the user calls them
  **"T0"–"T7"**. **T0 == Step 0.** Confirm scope per session.
- **T0 (Step 0) complete as of 2026-07-13** and verified (Gate 0 green, 702
  tests pass). Two artifacts, independent paths from `pypower.t.t_case9_dcline`:
  `src/cvxopf/testcases/case9_dcline.py` (0a, via `generate_testcases.py`) and
  `tests/fixtures/case9_dcline_pypower_reference.json` (0b, via
  `generate_pypower_fixtures.py`). Verified exact table-for-table equality
  between them.
- **Key non-obvious decision:** the fixture oracle does NOT use pypower's
  `toggle_dcline` (broken under numpy 2.x across its ext2int AND int2ext
  userfcns). Instead a self-contained "Option A" solve: hand-built
  `_dcline_to_gens` (two dummy gens per DC line) + a custom P-coupling
  `formulation` userfcn `(1-L1)*Pgf + Pgt = -L0/baseMVA`, dropping `dclinecost`
  (matching pypower's own `t_dcline.py`). Cross-checked against pypower's
  hardcoded expected array. Full rationale in `scripts/README.md`.
- **Next: T1** = `src/cvxopf/hvdc.py` (pure logic: `HVDCLink`, validation,
  incidence, `_hvdc_static_box`, `hvdc_from_dcline`), gated by Gate 1 unit tests.
- Uncommitted at session close: `scripts/README.md` (needs its own commit).

See [[cvxopf-session-working-style]] for how the user wants this driven.
