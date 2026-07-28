# Design Spec — CLAUDE.md reduction to ~500 lines

## Objective
Reduce CLAUDE.md from ~1,000 to ~500 lines by applying the principle we're simultaneously documenting (the Boyd "find the underlying abstraction, point to one source of truth" aesthetic): **CLAUDE.md holds invariants and non-obvious operative rules; it points to `plans/`, memories, code, and tests for derivable detail.**

## Guiding rule
Uniform, no special cases: *every* milestone's durable prose moves to a `plans/` file; CLAUDE.md's milestone table row carries a one-line summary + a `see plans/...` pointer. API field tables stay detailed (unique in-file reference, not duplication).

## Ordering constraint (critical)
Create plan files **before** deleting the corresponding CLAUDE.md prose. Never delete a sole copy. This is a migration, not a rewrite — move text nearly verbatim.

---

## Step A — Create plan files (relocation, verbatim)

Migrate existing CLAUDE.md milestone prose into new files. Content moves nearly verbatim from the current CLAUDE.md sections named.

| New file | Source prose | Status |
|---|---|---|
| `plans/milestone-5-storage.md` | current `### Milestone 5` | complete |
| `plans/milestone-8-nondispatchable.md` | current `### Milestone 8` | complete |
| `plans/milestone-9-sparse-pq.md` | current `### Milestone 9` | complete |
| `plans/milestone-15-full-lossy-hvdc.md` | current `### Milestone 15` | future |
| `plans/milestone-17-hierarchical-dc-ac.md` | current `### Milestone 17` | future |

Already-existing plan files (no creation needed, reuse as pointer targets): `plans/milestone-7-hvdc.md`, `plans/milestone-16-unify-components.md`.

Also fold into the relevant plan file, not CLAUDE.md: the M7 `dcline`-column table and MVP-vs-M15 detail → append to `plans/milestone-7-hvdc.md` if not already there (verify before deleting from CLAUDE.md; the memories `[[milestone-7-hvdc-status]]` / `[[hvdc-plan-mvp-scope]]` may already hold it).

M4 (stubbed) has minimal prose; leave as table row + the existing one-line note, no plan file needed.

---

## Step B — CLAUDE.md edits (in file order)

Net target: ~1,000 → ~500. Running deltas noted.

**B1. Insert "Design aesthetic (read this first)"** after the "developed by the CVX Group at Stanford." line, before its `---`. Content: the compact Boyd section (two quotes — "Clarity of Thought" and the 87-requirements/cost-of-ownership passage; the Linux nod; the three existing-instances bullets; the three practical-implications bullets; the citation footer with `from 9:55` and the full inControl URL). `+40`

**B2. Repository layout tree → prose pointer.** Replace the fenced tree with: the one-paragraph description naming `problem.py`, the three `*_problem.py` builders, `network/cost/data/results`, the four component modules (`storage.py`, `nondispatchable.py`, `hvdc.py`, `generator.py`), and `testcases/`/`tests/`/`examples/`/`notebooks/`/`scripts/`, plus the "run `find` for the current list rather than trusting a hand-maintained tree" line. `−55`

**B3. Fix stale baseline.** `Expected result: **512 passed...**` → `Expected result: all tests pass (baseline currently 816; run to confirm).` `~0`

**B4. Collapse duplicated device blocks.** Keep full Storage/ND/HVDC variable descriptions under `### "ac"`. Under `### "lossy_dc"`, replace re-descriptions with the single "Device models in DC" note (no reactive term; `b_q`/`q_nd` absent; real-power box not circle; storage `UserWarning`; HVDC identical to AC; results omit `Vm/Va_deg/Qg/q_net`). `−70`

**B5. Fold results-keys into one table** spanning `ac` / `lossy_dc` / `singlenode_dc` (replacing the three per-section results-key prose lines). `−15`

**B6. All milestone prose sections → table-row summary + pointer.** Delete every `### Milestone N — ...` prose block below the table. The milestone **table stays**; each row's Notes cell gets a terse summary and a `see plans/milestone-N-*.md` pointer (and memory wikilinks where they exist). Applies uniformly to M5, M7, M8, M9, M15, M16, M17. Delete the M7 `dcline` column table and MVP-vs-M15 subtable (now in the plan file per Step A). `−330` (this is the dominant cut)

**B7. Prune "What not to do" from ~70 to ~20 bullets.** Keep only non-obvious, high-consequence rules **not** already stated inline in a section above. 
- **Keep** (representative): use `build.solve()` not `build.prob.solve()`; `nlp=True` for AC / `nlp=False` for convex; never a second `p==`/`q==` after `_make_step_constraints`; detection is presence-not-`ns=0`/`nnd=0`/`n_hvdc=0`; no `cp.abs` in the HVDC loss equality (affine branch only); singlenode silently drops HVDC; don't add `pypower` as a dependency; `ac_problem`↔`dc_problem` no cross-import; import components from their own module not `problem.py`.
- **Drop** (now redundant with their inline sections): the 5 baseMVA/engineering-unit bullets (Units section), most granular HVDC bullets (HVDC block), the `sparse_pq`/M4 bullets (M9 pointer + M4 row), the ND-cost/`q_nd`/`nd_available` bullets (ND block). `−40`

**B8. Keep API field tables detailed** (`OPFOptions`, `OPFBuild`, `StorageUnitIdeal`, `NondispatchableUnit`). No change — deliberate: unique in-file reference agents consult constantly, not duplication. `0`

**B9. Trim DCP section worked example.** Keep the per-object API block and rules-in-brief; cut the `sqrt(1+square(x))` → `norm(hstack([1,x]),2)` walkthrough (it's in the CVXPY docs; the rule statement suffices). `−25`

---

## Line math

| Edit | Δ |
|---|---|
| B1 insert aesthetic | +40 |
| B2 layout → pointer | −55 |
| B4 collapse device blocks | −70 |
| B5 results-keys table | −15 |
| B6 milestone prose → pointers | −330 |
| B7 prune "what not to do" | −40 |
| B9 trim DCP example | −25 |
| **Net** | **≈ −495** |

1,000 − 495 ≈ **505.** At/near target with the API tables (B8) preserved intact. If it lands slightly over 500, the first additional cut is condensing the completed-milestone table-row summaries further; the API tables stay.

---

## Invariants preserved (must survive the cut)
- The device/network DCP boundary section (only the worked *example* trims; the invariant stays).
- The `build.solve()` / `nlp` solver-defaults rule.
- Units: per-unit vs engineering-unit distinction.
- Detection-by-presence contract.
- Section-order rule for `_make_step_constraints` (the "exactly one `p==`/`q==`" invariant).
- All four API field tables.

## Verification after applying
1. `wc -l CLAUDE.md` → confirm ≤ ~520.
2. Grep that every milestone table row with a `see plans/...` pointer has a corresponding existing file (no dangling pointers).
3. Confirm no unique detail was deleted without a home: the M7 `dcline` table exists in `plans/milestone-7-hvdc.md`; M15/M17 prose exists in their new plan files.
4. `uv run --extra dev pytest tests/ -q` unaffected (doc-only change) — baseline 816.

## Commit framing
Two logical commits: (1) `docs: migrate milestone prose to plans/ (M5,8,9,15,17 files)`; (2) `docs: trim CLAUDE.md to ~500 lines + add Boyd design-aesthetic section`. Keeps the relocation auditable separately from the reduction.