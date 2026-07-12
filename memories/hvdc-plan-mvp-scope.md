---
name: hvdc-plan-mvp-scope
description: How the Pypower t_case9_dcline standard test case maps onto the HVDC MVP (Milestone 7) vs full-lossy (Milestone 15) scope
metadata:
  type: project
---

Milestone 7 (HVDC MVP) is planned in `plans/milestone-7-hvdc.md`. Non-obvious scope decisions, settled 2026-07-12, that future planning sessions must respect:

- **Proportional loss only in the MVP.** `dcline` `loss1` (per-unit fraction; `loss_percent = loss1 * 100`, verified against the `t_case9_dcline` fixture via `Pt = Pf - loss0 - loss1*Pf`). Applied via sign-split affine branches selected pre-construction — lossy only when flow direction is fixed (`scheduled`/`downward`/fixed-direction `band`), lossless in `free` and zero-straddling `band`.
- **Fixed converter loss (`loss0` / MATPOWER `LOSS0`) is deliberately NOT modelled in the MVP.** Its sign *is* verifiable (`+sign(p_in)*loss0`), so exclusion is a scope choice (KISS + avoiding a seam where no-load loss vanishes on zero-straddling steps), not a technical blocker. `hvdc_from_dcline` drops `loss0` and warns when nonzero. Deferred to Milestone 15 (full sign-switching lossy HVDC, charge/discharge split).
- **Consequence for the standard test case:** importing `t_case9_dcline` will NOT reproduce Pypower's results exactly — its row-0 dcline has `loss0=1`, which the MVP ignores. This is expected, not a bug.
- **`Never put `cp.abs` in an equality constraint** — the original loss equation was non-DCP (convex atom in an equality) and non-smooth for IPOPT. See [[cvxpy-affine-equality-rule]].

CLAUDE.md carries the roadmap-altitude version (Milestone 7 + Milestone 15 detail rows); implementation-detail refinements (quadratic `cost_coeffs`, `[Pmin,Pmax]` box) live in the plan doc, not CLAUDE.md.