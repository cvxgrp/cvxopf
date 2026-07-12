# Milestone 7 -- HVDC Transmission Links: Build Plan

Status: proposed. Baseline confirmed **702 passed, 0 failed** (`uv run --extra dev pytest tests/`).

This plan was written after reading `problem.py`, `ac_problem.py`, `dc_problem.py`, `singlenode_dc_problem.py`, `results.py`, `storage.py`, and `__init__.py`. All design decisions from the Milestone 7 handoff are treated as resolved; this plan records how they map onto the existing code and flags the one item that could not be verified from the codebase.

---

## Section 1 -- Background and reference

### What HVDC adds
An HVDC link is a controllable point-to-point real-power transfer between two buses, `from_bus` and `to_bus`, with a loss model and four operational modes. Unity power factor at both terminals: **no reactive power, no apparent-power circle.** This makes HVDC structurally simpler than storage or nondispatchable units (which both carry a reactive term in AC).

### Representation: nodal injections are the fundamental variables
An HVDC link is modelled as a pair of **generator-like objects**, one at each
terminal, not as a line-like object. The two fundamental variables are the
**signed nodal injections** `p_in` (at `from_bus`) and `p_out` (at `to_bus`),
following the package-wide sign convention: **positive = generation/injection
into the grid, negative = consumption/withdrawal** (consistent with the battery
`b` variable). Terminal flow through the line is a *derived* quantity
(`|p_in|` sending-side / `|p_out|` receiving-side), constructed only if a future
branch-limit milestone needs the true crossing power -- see Section 1 forward
note.

**Both `p_in` and `p_out` are always `cp.Variable` objects**, for every mode and
every step -- this is a representation choice for uniform post-analysis (pull
in/out straight from `build.variables`), *not* a statement about degrees of
freedom. `p_out` is always tied to `p_in` by the affine loss equality (Section
1). The mathematical free-variable count per link per step is:

| Mode | free DOF | how pinned |
|---|---|---|
| scheduled | 0 | `p_in == p_scheduled_mw` (extra equality) + loss equality for `p_out` |
| band / downward / free | 1 | `p_in` free within bounds; `p_out` derived by loss equality |

So scheduled mode still builds `cp.Variable`s (pinned by an equality), it is
**not** pure numpy. The injection addend is therefore always a CVXPY
expression. Under Convention B (signed injections) both terminals enter the
balance with a **`+`**:
```
p == Cg @ Pg - Pd + ... + (1/baseMVA) * (Ch_from @ p_in_vec + Ch_to @ p_out_vec)
```
and likewise as the right-hand addend to `A @ p_flows + p_gen + ... == Pd`.

### Loss model (resolved)

**Why the obvious form is illegal.** The tempting single equation

```
p_out == -p_in - (loss_percent / 100) * abs(p_in)   # ILLEGAL
```

cannot be used as a constraint in **either** formulation:
- **Convex (CLARABEL):** DCP requires equality constraints to be affine on
  both sides. `cp.abs(p_in)` is convex, not affine, so this equality fails
  the DCP rule and CVXPY rejects it. There is no epigraph trick for an
  equality -- epigraph reformulation only moves a convex term into an
  *inequality*.
- **Nonconvex (IPOPT/DNLP):** `abs` is not differentiable at 0, so it is not
  a valid smooth equality atom either.

The nonconvexity is entirely in **choosing the flow direction**. Once the
sign of `p_in` is fixed *before problem construction*, the loss equation
collapses to one of two **affine** branches, each valid in both CLARABEL and
IPOPT. `p_in` is the single degree of freedom in every mode; `p_out` is
always derived from it by the selected branch (though it is still declared as
its own `cp.Variable` and tied by the branch equality -- see Representation).

**Sign-split affine branches** (`loss_frac = loss_percent / 100`; `p_in`,
`p_out` are signed nodal injections under Convention B):
```
from->to  (p_in >= 0):  p_out = -(1 - loss_frac) * p_in
to->from  (p_in <  0):  p_out = -(1 + loss_frac) * p_in
```
Both attenuate the *receiving* terminal relative to the *sending* terminal.
For `p_in >= 0`, `from_bus` injects (generates), `to_bus` absorbs
`(1-loss_frac)|p_in|` (`p_out < 0`) -- receiver gets less. For `p_in < 0`,
`from_bus` absorbs, `to_bus` injects `(1+loss_frac)|p_in|` (`p_out > 0`) --
sender supplies more to cover loss. Lossless (`loss_frac = 0`)
collapses both branches to `p_out = -p_in` (what is
injected at one node is withdrawn at the other).

**When each branch is used:**
- `"scheduled"` -- `p_in` pinned at `p_scheduled_mw` (sending-terminal
  setpoint) via an equality constraint. Its sign is known, so the matching
  branch is selected. `p_in`/`p_out` are still `cp.Variable`s (pinned by
  equality), so the injection addend is a CVXPY expression -- **not** numpy.
- `"downward"` -- one-directional by construction, so the sign is fixed and
  the matching branch is selected.
- `"band"` -- per step, compute the intersected `p_in` interval
  `[p_sched_t - bw, p_sched_t + bw] ∩ [-p_max, p_max]`. If it does not
  straddle zero (`lo >= 0` or `hi <= 0`), the direction is fixed and the
  matching branch is used. If it straddles zero, fall back to the lossless
  branch for that step and emit a `UserWarning` naming the link and the time
  step. Clamping to `±p_max` can only shrink the interval, so it never turns
  a fixed-direction band into a straddling one -- the gate is computed on the
  **intersected** interval.
- `"free"` -- always lossless (`p_out = -p_in`); the direction is
  unconstrained across the full `[-p_max, p_max]` range.

`loss_percent` defaults to 0.0 (lossless everywhere).

**Full sign-switching lossy model is deferred to a future milestone** (on par
with the lossy battery model): a charge/discharge-style split of `p_in`
into non-negative positive/negative parts would let `"free"` and
zero-straddling `"band"` steps carry losses. Out of scope for this MVP.

**Fixed converter loss (`loss_mw_fixed` / MATPOWER `LOSS0`) is also deferred**
to that milestone. The MVP models only proportional loss (`loss_percent` /
`LOSS1`). A constant no-load offset is second-order, has no verified data path
(the `dcline` mapping is unverified -- R1), and its direction-dependent sign
is cleaner to introduce alongside the charge/discharge split. There is no
`loss_mw_fixed` field on `HVDCLink` in the MVP.

**Forward note (terminal flow vs nodal injection).** Bounds and the
zero-crossing gate attach to `p_in` (the fundamental from-bus injection).
The physical line rating limits *terminal flow* = `|p_in|` (sending) /
`|p_out|` (receiving), which differ by the loss. A future branch-limit
milestone that needs true crossing power derives it from `p_in`/`p_out`;
the MVP box bound on `p_in` is sufficient here.

### Operational modes (resolved)

`p_in` bounds are on the from-bus nodal injection (the fundamental variable);
`p_out` is always a `cp.Variable` tied to `p_in` by the loss equality.

| Mode | p_in (free DOF, bounds) | loss model |
|---|---|---|
| scheduled | 0 DOF; `cp.Variable` pinned `p_in == p_scheduled_mw` (sending setpoint) | sign-split branch selected from `p_scheduled_mw` |
| band (default) | 1 DOF; `p_in ∈ [p_sched-bw, p_sched+bw] ∩ [-p_max, p_max]` | per step: matching branch if intersected interval doesn't straddle 0, else lossless + `UserWarning` |
| downward | 1 DOF; `p_in ∈ [0, p_sched]` if p_sched>=0 else `[p_sched, 0]` | direction fixed by construction → matching branch |
| free | 1 DOF; `p_in ∈ [-p_max, p_max]` | always lossless (`p_out = -p_in`) |

`p_scheduled_mw` is the **sending-terminal** setpoint: in `"scheduled"` mode
`p_in` is pinned to it and `p_out` is derived, so delivered power is *below*
the scheduled number by the loss. Document this in the `HVDCLink` field docs.

### Cost term (resolved)
```
cost += cp.multiply(cost_coefficient, cp.abs(p_in))   # per link, per step
```
Default `cost_coefficient = 0.0`. Mirrors storage `aging_weight`. Use `cp.multiply`, never `scalar * cp.abs(...)` (CvxpyDeprecationWarning). `cp.abs` in the **objective** is legal (convex). Since `p_in` is always a `cp.Variable` (even scheduled, where it is pinned by equality), the cost term is always a CVXPY expression.

### Formulation coverage (resolved)
- `"ac"` -- real-power injection added to the single `p ==` balance in `_make_step_constraints` (Section 3). No `q ==` change.
- `"lossy_dc"` -- injection added to `A @ p_flows + p_gen + ... == Pd` in `_make_dc_step_constraints` (Section 1).
- `"singlenode_dc"` -- **silently ignored.** Implemented in `problem.py` before dispatch: do not forward `hvdc`/`df_hvdc` into the singlenode builders. No warning. `"n_hvdc"` never appears in `build.data` for singlenode builds.

### Units and detection contract
- `p_in`, `p_out` are in **engineering units (MW)**, like `b`/`p_nd`. Enter the balance divided by `baseMVA`; not rescaled in `extract_results`.
- Detection contract: `"n_hvdc" in build.data`. **Never add `n_hvdc=0` as a default.**
- `hvdc.py` imports **numpy only** -- no other cvxopf modules (same rule as `storage.py`).

### MATPOWER dcline mapping (UNVERIFIED -- see Risk R1)
The handoff column map is reproduced in code as documented assumptions. The one value that cannot be checked against the repo (no existing dcline code) is whether MATPOWER `LOSS1` is a fraction (0-1) or percent (0-100). Plan assumes `loss_percent = LOSS1 * 100` and marks it `# TODO(verify)`. One-line change if wrong; does not block any other step.

---

## Section 2 -- Ordered steps with test gates

Follow the verification progression: offline unit tests for pure logic, then wiring tests, then the live solve as its own commit. Commit after each green gate.

### Step 1 -- `src/cvxopf/hvdc.py` (pure logic)
Mirror `storage.py` structure/docstrings:
- `HVDCLink` dataclass: `from_bus, to_bus, p_max_mw, p_scheduled_mw=0.0, bandwidth_mw=0.0, mode="band", loss_percent=0.0, cost_coefficient=0.0`. Docstring: `p_scheduled_mw` is the **sending-terminal setpoint** pinning `p_in` (the from-bus nodal injection); `p_out` is always derived, so delivered power is below the schedule by the loss.
- `_validate_hvdc(links, ext_bus_ids)`: `from_bus != to_bus`; both buses in `ext_bus_ids`; `p_max_mw > 0`; `bandwidth_mw >= 0`; `loss_percent >= 0`; `cost_coefficient >= 0`; `mode` in the four allowed values. Indexed `ValueError` messages like `_validate_storage`.
- `_make_hvdc_incidence_matrices(links, nb, ext_to_int)` -> `(Ch_from, Ch_to)`, each `(nb, n_hvdc)`; `np.empty((nb,0))` pair for empty input.
- `hvdc_from_dcline(dcline_table)` -> `list[HVDCLink]`; skip `BR_STATUS==0`; `# TODO(verify)` on `LOSS1 * 100`; `mode="scheduled"` on conversion.

**Gate 1 (offline unit):** `tests/test_hvdc.py::TestHVDCUnit` -- validation happy/sad, incidence shapes/entries, `hvdc_from_dcline` incl. inactive-line skip. No solve.

### Step 2 -- injection + bounds helper in `hvdc.py`
`_make_hvdc_step_injections(links, p_sched_t, ext_to_int, baseMVA)` -> `(injection_expr, p_in_list, p_out_list, cost_expr, constraints)`:
- **Both `p_in` and `p_out` are `cp.Variable`s per link, every mode** (uniform
  representation). `constraints` carries: the loss-branch equality tying
  `p_out` to `p_in`; the per-mode bound on `p_in`; and in scheduled mode the
  extra pin `p_in == p_scheduled_mw`.
- balance addend (per-unit, **Convention B -- both `+`**):
  `(1/baseMVA) * (Ch_from @ p_in_vec + Ch_to @ p_out_vec)`. Always a CVXPY
  expression (both terminals are variables in every mode).
- **`p_out` loss equality** is the affine branch selected for that link/step
  (see Loss model in Section 1) -- never an `abs`-in-equality. The branch
  coefficient `-(1 ± loss_frac)` is a numpy scalar chosen *before* building
  the equality, so `p_out == coeff * p_in` stays affine:
    - `scheduled`: sign of `p_scheduled_mw` selects the branch.
    - `downward`: sign fixed by construction selects the branch.
    - `band`: compute intersected `p_in` interval `[p_sched-bw, p_sched+bw] ∩
      [-p_max, p_max]`; if it straddles 0, use lossless (`p_out == -p_in`)
      and `warnings.warn` naming link + step; else use the matching branch.
    - `free`: always lossless (`p_out == -p_in`).
- `cost_expr`: summed `cp.multiply(cost_coefficient, cp.abs(p_in))`. Always a
  CVXPY expression (`p_in` is always a variable). `cp.abs` in the objective is
  legal.
- bounds are on `p_in`; built once here and reused by both builders.
Match each caller idiom: AC `(1.0/baseMVA) * (...)`, DC `cp.multiply(1.0/baseMVA, ...)`.

**Gate 2 (offline logic):** extend `test_hvdc.py` --
- scheduled link builds `cp.Variable`s for `p_in`/`p_out` (not numpy) plus the
  `p_in == p_scheduled_mw` pin; injection addend is a CVXPY expression.
- lossy scheduled link (`loss_percent>0`, `p_scheduled_mw>0`): loss equality
  is `p_out == -(1-loss_frac)*p_in`; reverse schedule
  (`p_scheduled_mw<0`) selects the `-(1+loss_frac)` branch.
- band with `p_in` interval straddling 0 emits `UserWarning` (link + step) and
  yields lossless equality `p_out == -p_in`; band with fixed-direction
  interval yields the matching branch coefficient.
- free link: lossless `p_out == -p_in`. No solve.
- **sign check:** for a lossless link with `p_in > 0`, assert the balance
  addend injects `+p_in/baseMVA` at from_bus and `-p_in/baseMVA` at to_bus
  (Convention B: both terms `+`, `p_out == -p_in`).

### Step 3 -- `problem.py` wiring
- module-level import + re-export of `HVDCLink`, `hvdc_from_dcline`.
- add `hvdc=None` to `build_opf`; `hvdc=None, df_hvdc=None` to `build_opf_multistep`.
- `df_hvdc` tiling fallback + `UserWarning` when `hvdc is not None and df_hvdc is None`, mirroring the `df_nd` block; columns are integer indices `0..n_hvdc-1`.
- **silent-ignore:** for `singlenode_dc`, do not forward `hvdc`/`df_hvdc` to the builder; singlenode builder signatures unchanged; no warning.
- forward `hvdc` (single) / `hvdc, df_hvdc` (multistep) to `ac`/`lossy_dc` builders.

**Gate 3 (wiring):** `tests/test_hvdc.py::TestSinglenodeIgnore` -- three silent-ignore tests (single identical, multistep identical + df_hvdc ignored, no `UserWarning`); assert `"n_hvdc" not in build.data`. Uses fast deterministic singlenode solve.

### Step 4 -- `dc_problem.py` integration (simpler network formulation first)
- module-level import from `hvdc.py` (matches existing storage/nd imports).
- `_parse_dc_case` gains `hvdc`; validate, build `Ch_from/Ch_to`, store `n_hvdc` + arrays.
- `_make_dc_step_constraints` gains hvdc params; add injection to the single balance line; append per-mode bounds.
- cost: add hvdc `cost_expr` (in builder, like storage aging cost).
- both single/multistep builders thread `hvdc`/`df_hvdc`; per-step `p_in`/`p_out` vars; populate `variables["p_hvdc_in"]`, `variables["p_hvdc_out"]`, `data["n_hvdc"]`, etc. Multistep reads `df_hvdc` row `t`.

**Gate 4 (live, deterministic, own commit):** `tests/test_hvdc.py::TestHVDCLossyDC` on case9 synthetic `bus 4 -> bus 9`:
- lossless free-mode solves; balance holds; `p_hvdc_out == -p_hvdc_in`.
- scheduled-mode (lossless) forces known transfer; generation shifts accordingly.
- lossy scheduled mode (`loss_percent>0`, `p_scheduled_mw>0`): delivered
  `|p_hvdc_out| = (1-loss_frac)|p_hvdc_in|`; sending draw exceeds delivered power.
- lossy fixed-direction band (interval doesn't straddle 0): matching branch
  applied, no warning. Zero-straddling band: lossless + `UserWarning`.
- T=1 multistep equals single-step.

### Step 5 -- `ac_problem.py` integration
- `_parse_case` gains `hvdc`; same data population.
- `_make_step_constraints` Section 3: add `hvdc_injection_p` to the `p ==` line only; **leave `q ==` untouched**. Add per-mode bounds as a new labelled `Section 3b: HVDC bounds`; preserve the single-`p==` rule.
- cost: add hvdc cost to `total_cost` alongside storage aging cost.
- thread through single/multistep builders.

**Gate 5 (live, deterministic):** `tests/test_hvdc.py::TestHVDCAC` on the same synthetic link. IPOPT converges; `q_net` structurally unaffected; scheduled transfer shifts real dispatch; lossy scheduled/fixed-direction-band loss fraction correct (`|p_hvdc_out| = (1-loss_frac)|p_hvdc_in|`); zero-straddling band falls back to lossless. case9 AC no-hvdc baseline as reference.

### Step 6 -- `results.py` extraction
- `_extract_ac_results` and `_extract_dc_results`: guard on `"n_hvdc" in data`; add `p_hvdc_in` (from_bus injection variable value), `p_hvdc_out` (to_bus injection variable value), `hvdc_loss` (derived). Both injections are read directly from the `cp.Variable`s (every mode, including scheduled). Shapes `(n_hvdc,)` single / `(T, n_hvdc)` multi. Document `p_hvdc_in`/`p_hvdc_out` as **signed nodal injections** (positive = injection into grid), not directional flows.
  - **`hvdc_loss` definition:** total power lost = sending-terminal magnitude −
    receiving-terminal magnitude, always ≥ 0. Under Convention B (pure
    proportional loss) this is exactly `hvdc_loss = p_in + p_out`, verified for
    both flow directions: for `p_in >= 0`, `p_in + p_out = loss_frac * p_in ≥
    0`; for `p_in < 0`, `p_in + p_out = -loss_frac * p_in ≥ 0`. Gate 6 asserts
    `hvdc_loss >= 0` and matches the loss fraction numerically.
- `_extract_singlenode_dc_results`: no change.

**Gate 6:** extend Gate 4/5 tests to assert results carry `p_hvdc_in/out/loss` with correct shapes and derived values.

### Step 7 -- public API, examples, docs
- `__init__.py`: re-export `HVDCLink`, `hvdc_from_dcline`; add to `__all__`.
- `examples/case9_hvdc_ac.py`, `examples/case9_hvdc_dc.py` (small, runnable).
- `CLAUDE.md`: flip Milestone 7 to complete; add HVDC formulation subsections (Convention-B nodal balance mods with **both** `Ch_from @ p_in` and `Ch_to @ p_out` entering with `+`; `p_in`/`p_out` are signed nodal injections, HVDC terminals modelled as generator-like objects; both are always `cp.Variable`s, DOF is 0 scheduled / 1 otherwise; sign-split affine loss branches + the rule that lossy branches are only used when the flow direction is fixed pre-construction; `p_scheduled_mw` = sending setpoint pinning `p_in`; `HVDCLink` field table; `hvdc=`/`df_hvdc=` on entry points; results keys `p_hvdc_in`/`p_hvdc_out`/`hvdc_loss`; singlenode silent-ignore contract). Add a `Milestone 15 -- full sign-switching lossy HVDC` (charge/discharge split) row as Future. `what not to do` bullets: no `n_hvdc=0` default; no `q` term; no second `p==`; no hvdc import in singlenode; `cp.multiply` for cost/loss; **never put `cp.abs` (or any non-affine atom) in the `p_out` loss equality -- select an affine branch by pre-construction sign instead**; do not select a lossy branch in `free` mode or in a zero-straddling `band` step; **both HVDC balance terms enter with `+` (signed injections), not `Ch_to − Ch_from`**; do not multiply `p_in`/`p_out` by `baseMVA` in `extract_results` (engineering units, like storage).

**Gate 7 (full suite):** `uv run --extra dev pytest tests/` -- expect `702 + new` passed, 0 failed. `uv run ruff format .` and `uv run ruff check .` clean.

---

## Section 3 -- Risks

**R1 -- MATPOWER `LOSS1` units (unverified).** Cannot be checked against the repo (no existing dcline code) and must not be confabulated. Mitigation: documented assumption `loss_percent = LOSS1 * 100` with `# TODO(verify)`; one-line fix if wrong; no solve test depends on it (tests use `HVDCLink` directly). Confirm with the researcher before trusting `hvdc_from_dcline` on real dcline data.

**R2 -- Convention-B sign of the balance addend.** Both HVDC terminals are
signed nodal injections and enter the balance with `+`:
`(1/baseMVA) * (Ch_from @ p_in + Ch_to @ p_out)`. The tempting terminal-flow
form `(Ch_to @ p_to - Ch_from @ p_from)` is a different (line-like) convention
and, combined with the loss branches written in Convention B (`p_out = -p_in`),
produces a latent sign bug (withdrawal at *both* ends). Mitigation: uniform
Convention B throughout; Gate 2 sign check asserts `+p_in` at from_bus and
`-p_in` at to_bus for a lossless link; CLAUDE.md `what not to do` bullet.

Note: scheduled mode is **no longer** numpy-vs-CVXPY dual -- `p_in`/`p_out` are
`cp.Variable`s in every mode (scheduled adds a pinning equality). The injection
addend and cost term are always CVXPY expressions, which removes the former
type-branching risk entirely.

**R3 -- single-`p==` invariant (AC).** Section 3 owns the only `p ==` constraint. hvdc term must be added inside that expression, bounds in a separate sub-section. Mitigation: CLAUDE.md invariant + targeted test.

**R4 -- dispatch signature drift.** Adding positional `hvdc`/`df_hvdc` risks passing them to singlenode builders. Mitigation: singlenode signatures unchanged; `problem.py` omits them from the singlenode call path (the silent-ignore mechanism).

**R5 -- affine loss branch depends on pre-construction direction choice.**
The loss equality is only affine because the flow direction (sign of
`p_in`) is fixed before the problem is built. If a future edit lets the
direction be a decision variable in a lossy mode, the equality becomes
nonconvex/non-DCP and will be silently wrong (convex) or rejected. Mitigation:
CLAUDE.md invariant -- lossy branches may only be selected in `scheduled`,
`downward`, or fixed-direction `band` steps; `free` and zero-straddling `band`
steps are always lossless. The full sign-switching lossy model (charge/
discharge split) is a separate future milestone.

**R6 -- non-determinism.** All gates use deterministic convex solves (CLARABEL) or a deterministic IPOPT solve on a fixed small case. No model-prompting verification. AC IPOPT ~2e-9 artifacts handled with existing tolerances.

**R7 -- silent lossless fallback in `band`.** A zero-straddling band step
drops to the lossless branch. Risk: a user expecting losses gets none and does
not notice. Mitigation: `UserWarning` naming the link and the time step at
construction. Warning suppression is deferred (future QoL item; violates MVP/
KISS for now), so these warnings fire by default.

**R8 -- fixed converter loss removed from MVP (RESOLVED).** An earlier draft
carried a `loss_mw_fixed` term (`p_out = coeff * p_in - loss_mw_fixed`) whose
sign was backwards -- it *increased* delivered power as the fixed loss grew.
Rather than fix the sign, `loss_mw_fixed` was removed from the MVP entirely: it
is a second-order no-load loss, has no verified data path (R1), and its
direction-dependent sign is cleaner to introduce alongside the deferred
charge/discharge split (see the deferred-milestone note in Section 1). The MVP
models proportional loss only, so `hvdc_loss = p_in + p_out` is exact and
`≥ 0` in both directions. No open sub-item remains.

---

## Open items status
- Item 1 (LOSS1 units): unresolved -- flagged R1; does not block implementation.
- Item 2 (singlenode handling): resolved (silent ignore, no warning).
- Item 3 (test case): resolved -- synthetic `bus 4 -> bus 9` on case9 + pure unit tests; no new case constructor.
- Item 4 (`hvdc_from_dcline` location): resolved -- `hvdc.py`.
- Item 5 (results keys): resolved -- `p_hvdc_in`, `p_hvdc_out` (both read from `cp.Variable`s), `hvdc_loss` (derived, ≥0); `(n_hvdc,)` single / `(T, n_hvdc)` multi. Documented as signed nodal injections, not directional flows.
- Item 6 (loss model): **corrected** -- the original `abs`-in-equality form was
  not DCP-valid (convex) nor a valid smooth equality (nonconvex). Resolved to
  sign-split affine branches selected pre-construction: `scheduled`/`downward`
  always, `band` only when the intersected interval doesn't straddle zero,
  `free` never (always lossless). `p_scheduled_mw` pins the sending terminal
  (`p_in`). Full sign-switching lossy model (charge/discharge split) deferred
  to a future milestone. Package-wide warning suppression deferred (future QoL).
- Item 7 (representation & signs): resolved -- HVDC terminals are
  generator-like objects with **nodal injections `p_in`/`p_out` as the
  fundamental variables** (Convention B: positive = injection, both balance
  terms `+`). Both are always `cp.Variable`s (DOF 0 scheduled / 1 otherwise);
  scheduled is not pure numpy. Result keys renamed `p_hvdc_from/to` ->
  `p_hvdc_in/out`. Fixed a latent sign bug (terminal-flow addend mixed with
  Convention-B loss branches). Terminal flow is derived on demand for a future
  branch-limit milestone.
- Item 8 (fixed converter loss): resolved -- `loss_mw_fixed` removed from the
  MVP (was a buggy leftover of the illegal loss equation); proportional loss
  only. Fixed converter loss (`LOSS0`) deferred to the full-lossy milestone
  alongside the charge/discharge split. See R8.
