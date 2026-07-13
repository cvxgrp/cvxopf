---
name: hvdc-silent-ignore-dispatch-constraint
description: problem.py dispatches all formulation builders through one positional call site, so "singlenode signatures unchanged" silent-ignore is impossible as written
metadata:
  type: project
---

The Milestone 7 HVDC plan (`plans/milestone-7-hvdc.md`, Step 3 / R4) describes
the `singlenode_dc` silent-ignore of `hvdc`/`df_hvdc` as "do not forward to the
singlenode builder; singlenode builder signatures unchanged." This is
**mechanically impossible** against the current code.

`problem.py` dispatches through a single unified positional call site per entry
point (as of 2026-07-12, branch `dcline`):
- `build_opf`: `builders[formulation](case, options, storage, delta, nondispatchable)`
- `build_opf_multistep`: `builders[formulation](case, df_P, df_Q, T, options, coupling_constraints, storage, delta, nondispatchable, df_nd)`

All three builders (`_build_ac_single`, `_build_lossy_dc_single`,
`_build_singlenode_dc_single`, and the multistep trio) share an **identical
positional signature**. You cannot forward `hvdc` to ac/lossy_dc but not to
singlenode at one call site.

**Why:** to inject `hvdc` positionally you must either (a) branch the dispatch
on `formulation` (a real edit to the unified call site the plan doesn't
mention), or (b) give the singlenode builders the `hvdc`/`df_hvdc` params too
(ignored) — which contradicts "signatures unchanged." Storage/nd/df_nd are
already threaded through *every* builder including singlenode, so (b) matches
the existing pattern and is lower-risk; but then R4's "signatures unchanged"
wording is false and must be rewritten.

**How to apply:** when implementing Step 3, pick (a) or (b) explicitly and
fix the plan's Step 3 + R4 language. Detection via `"n_hvdc" not in build.data`
(Gate 3) is consistent with (b). Related: [[hvdc-plan-mvp-scope]].
