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
p == Cg @ Pg - Pd + ... + (1/baseMVA) * (Ch_from @ p_in + Ch_to @ p_out)
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
  `[p_sched_t - bw, p_sched_t + bw] ∩ [p_min, p_max]`. If it does not
  straddle zero (`lo >= 0` or `hi <= 0`), the direction is fixed and the
  matching branch is used. If it straddles zero, fall back to the lossless
  branch for that step and emit a `UserWarning` naming the link and the time
  step. Clamping to `[p_min, p_max]` can only shrink the interval, so it never
  turns a fixed-direction band into a straddling one -- the gate is computed on
  the **intersected** interval.
- `"free"` -- lossless when the box `[p_min, p_max]` straddles zero (the
  default symmetric `[-p_max, p_max]` case); if an explicit one-sided
  `[p_min, p_max]` fixes the direction, the matching lossy branch is used. The
  zero-crossing gate (not the mode name) is the single source of truth -- see
  the Operational modes note.

`loss_percent` defaults to 0.0 (lossless everywhere).

**Full sign-switching lossy model is deferred to a future milestone** (on par
with the lossy battery model): a charge/discharge-style split of `p_in`
into non-negative positive/negative parts would let `"free"` and
zero-straddling `"band"` steps carry losses. Out of scope for this MVP.

**Fixed converter loss (MATPOWER `LOSS0`) is NOT modelled in the first
implementation.** The MVP models only proportional loss (`loss_percent` /
`LOSS1`). Although fixed loss *could* be added affinely on a fixed-direction
branch (`p_out = coeff * p_in + sign(p_in) * loss0`, sign now verified against
the `t_case9_dcline` fixture -- see R1/R8), it is deliberately excluded: it is
second-order, and forcing every fixed-direction branch to carry it while
`free` / zero-straddling `band` steps cannot would create an inconsistent
seam (a no-load loss that vanishes whenever the optimizer's interval straddles
zero). Keeping the MVP purely proportional preserves the exact
`hvdc_loss = p_in + p_out` identity (Step 6) and defers all fixed-loss
modeling to the full-lossy HVDC milestone (Milestone 15), where the
charge/discharge split removes the seam. There is **no** `loss_mw_fixed` field
on `HVDCLink` in the MVP, and `hvdc_from_dcline` drops the `LOSS0` column
(warning on nonzero `LOSS0` -- see Step 1).

**Forward note (terminal flow vs nodal injection).** Bounds and the
zero-crossing gate attach to `p_in` (the fundamental from-bus injection).
The physical line rating limits *terminal flow* = `|p_in|` (sending) /
`|p_out|` (receiving), which differ by the loss. A future branch-limit
milestone that needs true crossing power derives it from `p_in`/`p_out`;
the MVP box bound on `p_in` is sufficient here.

### Operational modes (resolved)

`p_in` bounds are on the from-bus nodal injection (the fundamental variable);
`p_out` is always a `cp.Variable` tied to `p_in` by the loss equality. The
fundamental bound is a **general affine box `p_in ∈ [lo, hi]`**; the four named
modes are just presets that fill in `[lo, hi]` (and, for `scheduled`, add a
pinning equality). The MATPOWER `[Pmin, Pmax]` maps straight onto this box.
The per-step loss decision depends only on whether `[lo, hi]` straddles zero,
so it is uniform across every mode -- fixed-direction boxes get the matching
lossy branch, zero-straddling boxes get the lossless branch (+`UserWarning`).

| Mode | p_in (free DOF, bounds) | loss model |
|---|---|---|
| scheduled | 0 DOF; `cp.Variable` pinned `p_in == p_scheduled_mw` (sending setpoint) | sign-split branch selected from `p_scheduled_mw` |
| band (default) | 1 DOF; `p_in ∈ [p_sched-bw, p_sched+bw] ∩ [p_min, p_max]` | per step: matching branch if intersected interval doesn't straddle 0, else lossless + `UserWarning` |
| downward | 1 DOF; `p_in ∈ [0, p_sched]` if p_sched>=0 else `[p_sched, 0]` | direction fixed by construction → matching branch |
| free | 1 DOF; `p_in ∈ [p_min, p_max]` (default `[-p_max, p_max]`) | matching branch if box doesn't straddle 0, else lossless |

Note: with an explicit `p_min_mw`, `free` is no longer *necessarily* lossless
-- a `free` link whose `[p_min, p_max]` box is one-sided is fixed-direction and
gets the lossy branch. "Always lossless" holds only for the default symmetric
`[-p_max, p_max]`. This is the `[Pmin, Pmax]`-box generalization; the
zero-crossing gate is the single source of truth.

`p_scheduled_mw` is the **sending-terminal** setpoint: in `"scheduled"` mode
`p_in` is pinned to it and `p_out` is derived, so delivered power is *below*
the scheduled number by the loss. Document this in the `HVDCLink` field docs.

### Cost term (resolved)
The per-link cost is the full MATPOWER `dclinecost` polynomial in the transfer
magnitude, `c2 * |p_in|^2 + c1 * |p_in| + c0`. Because `(|x|)^2 = x^2`, the
quadratic term is written directly on `p_in` (no `abs` needed) and the linear
term keeps `cp.abs` so cost is symmetric in flow direction:
```
cost += (c2 * cp.square(p_in)                 # quadratic; = c2*|p_in|^2
         + cp.multiply(c1, cp.abs(p_in))       # linear magnitude cost
         + c0)                                 # constant (line on)
```
Both `cp.square` and `cp.abs` are convex → legal in the **objective**.
Follow the `poly_cost_expr` monomial-sum pattern in `cost.py` (explicit
monomials, **not** Horner) so the DCP checker accepts the quadratic; the same
caveat the generator cost documents applies. Use `cp.multiply`, never
`scalar * cp.abs(...)` (CvxpyDeprecationWarning). Since `p_in` is always a
`cp.Variable` (even scheduled, where it is pinned by equality), the cost term
is always a CVXPY expression.

`HVDCLink.cost_coeffs` is a `(c0, c1, c2)` tuple (default `(0.0, 0.0, 0.0)`),
mirroring the generator `cost_coeffs` convention in `make_singlenode_case`.
The `c0` constant only affects the reported objective (it does not change the
optimum) and is meaningful only while the line is energized; the MVP always
adds it for objective consistency.

### Formulation coverage (resolved)
- `"ac"` -- real-power injection added to the single `p ==` balance in `_make_step_constraints` (Section 3). No `q ==` change.
- `"lossy_dc"` -- injection added to `A @ p_flows + p_gen + ... == Pd` in `_make_dc_step_constraints` (Section 1).
- `"singlenode_dc"` -- **silently ignored (accepted and dropped).** `hvdc`/`df_hvdc` are forwarded to the singlenode builders through the shared call site (like `storage`/`nondispatchable`), which drop them without building anything. No warning. `"n_hvdc"` never appears in `build.data` for singlenode builds. See R4.

### Units and detection contract
- `p_in`, `p_out` are in **engineering units (MW)**, like `b`/`p_nd`. Enter the balance divided by `baseMVA`; not rescaled in `extract_results`.
- Detection contract: `"n_hvdc" in build.data`. **Never add `n_hvdc=0` as a default.**
- `hvdc.py` imports **numpy only** -- no other cvxopf modules (same rule as `storage.py`).

### MATPOWER dcline mapping (VERIFIED against `t_case9_dcline`)
The column order `fbus tbus status Pf Pt Qf Qt Vf Vt Pmin Pmax QminF QmaxF
QminT QmaxT loss0 loss1` and the loss-unit question are now confirmed against
the Pypower `t_case9_dcline` fixture. Pypower's loss law is
`Pt = Pf - loss0 - loss1 * Pf`; the fixture's row 0 (`loss0=1, loss1=0.01,
Pf=10 → Pt=8.9`) and row 3 (`loss0=0, loss1=0.05, Pf=10 → Pt=9.5`) both check
out. So **`loss1` is a per-unit fraction** and `loss_percent = loss1 * 100`
is correct (drop the old `# TODO(verify)`). `Pf` is Pypower's sending-terminal
setpoint; on import the MVP carries it only as a **non-binding reference**
(`p_scheduled_mw`) and optimizes `p_in` over `[Pmin, Pmax]` (`mode="free"`, see
Step 1) rather than pinning it -- so a solved cvxopf `p_in` need not equal `Pf`.
`Pt` is Pypower's derived receiving injection magnitude at *its* `Pf`,
consistent with our `p_out = -(1 - loss_frac) * p_in` branch for `p_in > 0`.
`loss0` (fixed loss) is real in the fixture but intentionally **not** modelled
in the MVP (see Loss model); `hvdc_from_dcline` drops it with a `UserWarning`
when nonzero.

---

## Section 2 -- Ordered steps with test gates

Follow the verification progression: offline unit tests for pure logic, then wiring tests, then the live solve as its own commit. Commit after each green gate.

### Step 0 -- `t_case9_dcline` test artifacts (standalone scripts; do FIRST)
This step touches only the two self-contained `uv` inline-dependency scripts in
`scripts/` (each pins its own deps) and their committed output artifacts. It
depends on nothing else in the milestone and must be completed first, because
every downstream solve gate (Gates 4/5/6/6b) consumes the `case9_dcline` case
file it produces.

**0a -- static case file via `scripts/generate_testcases.py`.**
- Extend `_generate_source` (and `_check_consistency`) to emit `dcline` /
  `dclinecost` tables when present in the source `ppc`. Existing cases without
  these keys are unaffected (conditional emission, like the `areas` block).
- Add a `t_case9_dcline` entry sourced from `pypower.t.t_case9_dcline`. Output
  `src/cvxopf/testcases/case9_dcline.py`, function `case9_dcline()`.
- **Static input only.** The script reads the Pypower case *dict* and never
  calls `toggle_dcline`/`runopf`, so the numpy-2.x float-indexing bug in
  `toggle_dcline` is never triggered here. No monkeypatch needed in this script.
- Reactive/voltage columns are preserved *in the case file* (faithful to the
  source), and dropped only later at `hvdc_from_dcline` import time (Step 1).

**0b -- Pypower reference fixture via `scripts/generate_pypower_fixtures.py`.**
- Add `t_case9_dcline` to the fixture `cases` list; output
  `tests/fixtures/case9_dcline_pypower_reference.json`, schema matching the
  existing fixtures (`case`, `solver`, `status`, `objective`, `Pg`, `Qg`,
  `Vm`, `Va_deg`).
- **This is a solved-OPF oracle**, so it must activate the DC line
  (`toggle_dcline(ppc, 'on')`) before `runopf`. That path triggers the
  numpy-2.x float-indexing bug in `toggle_dcline` (see R9). Mitigation: a
  **scoped monkeypatch** that coerces integer-valued float index arrays to int
  for the duration of the dcline `toggle_dcline`+`runopf` call only, leaving the
  existing case9/14/57 code path untouched. Confine the patch with a
  `try/finally` (or context manager) so it is reverted immediately after the
  solve. Document it inline as a Pypower/numpy-2.x workaround, not a cvxopf
  concern.
- The fixture is an **approximate** oracle: Pypower models `loss0` (row 0 has
  `loss0=1`) which the MVP drops, so a cvxopf solve will not match exactly. This
  is expected and consumed accordingly by Gate 6b.

**Gate 0 (offline, own commit):**
- `uv run scripts/generate_testcases.py` produces `case9_dcline.py`;
  `case9_dcline()` imports and loads; `dcline`/`dclinecost` arrays match the
  Pypower source shapes (4 dcline rows, 4 dclinecost rows).
- `uv run scripts/generate_pypower_fixtures.py` produces
  `case9_dcline_pypower_reference.json` with `status == "optimal"`.
- No solve in the test suite here -- these are script runs producing committed
  artifacts. The generated files are what the suite consumes.

### Step 1 -- `src/cvxopf/hvdc.py` (pure logic)
Mirror `storage.py` structure/docstrings:
- `HVDCLink` dataclass: `from_bus, to_bus, p_max_mw, p_min_mw=None, p_scheduled_mw=0.0, bandwidth_mw=0.0, mode="band", loss_percent=0.0, cost_coeffs=(0.0, 0.0, 0.0)`. Docstring: `p_scheduled_mw` is the **sending-terminal setpoint** for `p_in` (the from-bus nodal injection); it **pins `p_in` only in `"scheduled"` mode** -- in `band`/`free`/`downward` it is a non-binding reference (band centre / reporting / optional warm-start), and `hvdc_from_dcline` imports use it that way (`mode="free"`, optimized over `[Pmin, Pmax]`). `p_out` is always derived, so delivered power is below `|p_in|` by the loss. `cost_coeffs` is `(c0, c1, c2)`. `p_min_mw` is the lower `p_in` bound; when `None`, defaults per mode (`band`/`free`: `-p_max_mw`; `downward`: `0`) so existing modes are unchanged.
- `_validate_hvdc(links, ext_bus_ids)`: `from_bus != to_bus`; both buses in `ext_bus_ids`; `p_max_mw > 0`; `p_min_mw <= p_max_mw` when given; `bandwidth_mw >= 0`; `loss_percent >= 0`; `c2 >= 0` (convex quadratic) and `c1 >= 0` (nonneg magnitude cost); `mode` in the allowed values. Indexed `ValueError` messages like `_validate_storage`.
- `_make_hvdc_incidence_matrices(links, nb, ext_to_int)` -> `(Ch_from, Ch_to)`, each `(nb, n_hvdc)`; `np.empty((nb,0))` pair for empty input.
- `hvdc_from_dcline(dcline_table, dclinecost=None)` -> `list[HVDCLink]`. Column map is now **verified** against the `t_case9_dcline` fixture (see R1) -- header order `fbus tbus status Pf Pt Qf Qt Vf Vt Pmin Pmax QminF QmaxF QminT QmaxT loss0 loss1`. Skip `status==0` rows. Mapping:
  - `from_bus=fbus`, `to_bus=tbus`.
  - `loss_percent = loss1 * 100` (verified: `loss1` is a per-unit fraction; `Pt = Pf - loss0 - loss1*Pf`).
  - `[Pmin, Pmax]` → `p_min_mw` / `p_max_mw` (the fundamental `p_in` box). **`mode="free"`** so the optimizer schedules `p_in` freely within `[Pmin, Pmax]` -- importing a dcline yields a *controllable resource optimized over its rated range*, matching the Pypower semantics, **not** a fixed injection. **`Pf` is carried as a non-binding reference only** (`p_scheduled_mw = Pf`, used for reporting / optional warm-start), **never** as a pin: the MVP does **not** emit a `"scheduled"` link from an import, so `bandwidth_mw` stays at its `0.0` default and no degenerate zero-width band is ever produced. The per-step zero-crossing gate on `[Pmin, Pmax]` then auto-selects lossy (fixed-direction box) vs lossless (straddling box), uniform with every other `free` link -- no separate `"scheduled"`/`"downward"`/`"band"` inference is needed. A row with `Pmin >= 0` or `Pmax <= 0` is naturally fixed-direction and gets the lossy branch; a straddling `[Pmin, Pmax]` is lossless. (For `t_case9_dcline`: row 1 `[2,10]` and row 3 `[0,10]` are both fixed-direction → lossy branch; the optimizer chooses `p_in` in-range rather than being pinned at `Pf`.)
  - `loss0` (fixed loss) is **dropped**; if any active row has `loss0 != 0`, emit a `UserWarning` that the imported model omits fixed converter loss (deferred to Milestone 15) and will not match Pypower exactly.
  - `Qf, Qt, QminF, QmaxF, QminT, QmaxT` (reactive) and `Vf, Vt` (voltage setpoints) are dropped -- MVP is unity-PF, no HVDC voltage control. Note in the docstring.
  - `dclinecost` (optional, same polynomial layout as `gencost`) maps to `cost_coeffs=(c0, c1, c2)`; only model-2 polynomial rows up to quadratic are read (higher-order terms rejected with a clear error). When the `case9_dcline` case file (Step 0a) is the source, `dclinecost` is present and passed through; when absent, `cost_coeffs` defaults to `(0.0, 0.0, 0.0)`.
  - **Test artifact source:** `hvdc_from_dcline(case9_dcline()["dcline"], case9_dcline()["dclinecost"])` is the intended entry point for downstream gates -- 4 rows, one inactive (`status==0`) skipped -> 3 links; `loss0!=0` on row 0 emits the documented `UserWarning`.

**Gate 1 (offline unit):** `tests/test_hvdc.py::TestHVDCUnit` -- validation happy/sad, incidence shapes/entries, `hvdc_from_dcline` incl. inactive-line skip. No solve.

### Step 2 -- injection + bounds helper in `hvdc.py`
`_make_hvdc_step_injections(links, p_sched_t, ext_to_int, baseMVA)` -> `(injection_expr, p_in, p_out, cost_expr, constraints)`:
- **Container shape (matches storage/nd).** `p_in` and `p_out` are each a
  **single `cp.Variable((n_hvdc,))`** for the step -- **not** Python lists of
  per-link scalar Variables. `build.variables["p_hvdc_in"]` is therefore one
  `(n_hvdc,)` Variable single-step, and a `list[cp.Variable]` of length `T`
  (one `(n_hvdc,)` Variable per step) multistep, exactly like
  `variables["b"]`/`variables["p_nd"]`. `extract_results` walks it the same way
  (`var["p_hvdc_in"][t].value` per step).
- **Both `p_in` and `p_out` are `cp.Variable`s (every mode)** (uniform
  representation). `constraints` carries: the loss-branch equality tying
  `p_out` to `p_in`; the per-mode bound on `p_in`; and in scheduled mode the
  extra pin `p_in == p_scheduled_mw`. These are **vectorized over the
  `(n_hvdc,)` Variable** where the branch/bound is uniform, or built per link
  and stacked -- but the stored objects are the two `(n_hvdc,)` Variables, not
  lists. Note the per-step loss branch can differ per link (each link's box may
  or may not straddle zero), so the loss equality is assembled as a vector
  equality `p_out == coeff_vec * p_in` with a per-link `coeff_vec` (an
  `(n_hvdc,)` numpy array of `-(1 ± loss_frac)` / `-1` entries chosen
  pre-construction), keeping it a single affine vector equality.
- balance addend (per-unit, **Convention B -- both `+`**):
  `(1/baseMVA) * (Ch_from @ p_in + Ch_to @ p_out)`. Always a CVXPY
  expression (both terminals are variables in every mode).
- **`p_out` loss equality** is the affine branch selected for that link/step
  (see Loss model in Section 1) -- never an `abs`-in-equality. The branch
  coefficient `-(1 ± loss_frac)` is a numpy scalar chosen *before* building
  the equality, so `p_out == coeff * p_in` stays affine:
    - `scheduled`: sign of `p_scheduled_mw` selects the branch.
    - `downward`: sign fixed by construction selects the branch.
    - `band`: compute intersected `p_in` interval `[p_sched-bw, p_sched+bw] ∩
      [p_min, p_max]`; if it straddles 0, use lossless (`p_out == -p_in`)
      and `warnings.warn` naming link + step; else use the matching branch.
    - `free`: lossless when `[p_min, p_max]` straddles 0 (default symmetric
      box); matching branch when an explicit one-sided box fixes direction.
    The gate is on the box, uniform across modes.
- `cost_expr`: summed per-link polynomial `c2*cp.square(p_in) +
  cp.multiply(c1, cp.abs(p_in)) + c0` (from `cost_coeffs`). Always a CVXPY
  expression (`p_in` is always a variable). `cp.square` and `cp.abs` in the
  objective are legal (convex); build as an explicit monomial sum, not Horner
  (matches `poly_cost_expr`).
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
- forward `hvdc` (single) / `hvdc, df_hvdc` (multistep) to **all** builders through
  the single unified positional call site -- including the singlenode builders,
  which accept the new params and **drop them silently** (no warning). This is
  the storage/nd threading pattern: every builder shares one signature, so the
  params reach singlenode too; singlenode simply does not populate `"n_hvdc"` in
  `build.data`. The silent-ignore contract is thus "accepted and dropped," not
  "omitted from the call path." See R4.
- **do not** branch the dispatch on `formulation`; keep the single call site.

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
- `_extract_ac_results` and `_extract_dc_results`: guard on `"n_hvdc" in data`; add `p_hvdc_in` (from_bus injection), `p_hvdc_out` (to_bus injection), `hvdc_loss` (derived). Both injections are read off the single `(n_hvdc,)` `cp.Variable` per step (every mode, including scheduled) -- `var["p_hvdc_in"].value` single-step, `var["p_hvdc_in"][t].value` stacked over `t` multistep, matching the storage `b`/nd `p_nd` extraction walk (Step 2 container shape). Result-array shapes `(n_hvdc,)` single / `(T, n_hvdc)` multi. Document `p_hvdc_in`/`p_hvdc_out` as **signed nodal injections** (positive = injection into grid), not directional flows.
  - **`hvdc_loss` definition:** total power lost = sending-terminal magnitude −
    receiving-terminal magnitude, always ≥ 0. Under Convention B (pure
    proportional loss) this is exactly `hvdc_loss = p_in + p_out`, verified for
    both flow directions: for `p_in >= 0`, `p_in + p_out = loss_frac * p_in ≥
    0`; for `p_in < 0`, `p_in + p_out = -loss_frac * p_in ≥ 0`. Gate 6 asserts
    `hvdc_loss >= 0` and matches the loss fraction numerically.
- `_extract_singlenode_dc_results`: no change.

**Gate 6:** extend Gate 4/5 tests to assert results carry `p_hvdc_in/out/loss` with correct shapes and derived values.

**Gate 6b (live, Pypower approximate match):** `tests/test_hvdc.py::TestHVDCPypowerApprox` compares a solved cvxopf run on the `case9_dcline()` case (Step 0a) against the committed `case9_dcline_pypower_reference.json` fixture (Step 0b). **cvxopf solves with `formulation="ac"`** here (C4): the Pypower oracle is itself an AC-OPF, so `formulation="ac"` is the only apples-to-apples comparison for `objective`/`Pg`/`Qg`/`Vm`/`Va_deg`. A `lossy_dc` cvxopf solve would conflate the DC-vs-AC modeling gap with the dropped-`loss0` gap and produce only the `objective`/`Pg`/`p_net` overlap (DC results carry no `Qg`/`Vm`/`Va_deg`) -- so `lossy_dc` is validated by the internal-consistency checks in Gates 4/6, **not** against this Pypower oracle. Because the MATPOWER/Pypower dcline is itself a **dispatchable** resource optimized over `[Pmin, Pmax]` (a pair of dummy generators bounded by `[Pmin, Pmax]`, with the table `Pf` only a starting value -- verified against the MATPOWER manual §7.6), cvxopf's `mode="free"` import (C5) solves the **same** dcline problem as the oracle: the operating points genuinely coincide, so an `objective`/`Pg` comparison is meaningful up to the two documented modeling gaps below. This is an **approximate** comparison, **not** exact, for two reasons: (i) the MVP drops `loss0` (row 0 has `loss0=1`); and (ii) cost-model alignment -- the comparison assumes cvxopf's `dclinecost` `c0` handling matches Pypower's (inert for `t_case9_dcline`, whose `dclinecost` rows are all `c0=0`, but a nonzero-`c0` dataset would shift `objective` by `sum(c0)` over energized links; see C3). Consume it in exactly one of these documented ways (pick at implementation time):
- (a) loose tolerance on `objective`/`Pg` that absorbs the `loss0` discrepancy, or
- (b) tight assertions restricted to the `loss0==0` links (rows 1 and 3), loose elsewhere, or
- (c) internal-consistency assertions (flow conservation + the `p_out = -(1-loss1)*p_in` loss law on the fixed-direction links) as a **supplement** to (a)/(b), or as the primary check if the `loss0` gap makes an objective tolerance too loose to be meaningful.

Options (a)/(b) are genuine oracle checks now that C5 is resolved (the operating points coincide up to the two gaps above); (c) is available as a formulation-independent supplement. Whichever is chosen, the test docstring must state *why* it is approximate (the dropped `loss0`, and the `c0` cost-alignment assumption) and cite the MVP-vs-M15 handling table -- mirroring the existing "known acceptable discrepancies vs Pypower" pattern in `CLAUDE.md`. This gate depends on both AC (Step 5) and DC (Step 4) integration and runs just before the full-suite gate.

### Step 7 -- public API, examples, docs
- `__init__.py`: re-export `HVDCLink`, `hvdc_from_dcline`; add to `__all__`.
- `examples/case9_hvdc_ac.py`, `examples/case9_hvdc_dc.py` (small, runnable).
- `CLAUDE.md`: flip Milestone 7 to complete; add HVDC formulation subsections (Convention-B nodal balance mods with **both** `Ch_from @ p_in` and `Ch_to @ p_out` entering with `+`; `p_in`/`p_out` are signed nodal injections, HVDC terminals modelled as generator-like objects; both are always `cp.Variable`s, DOF is 0 scheduled / 1 otherwise; sign-split affine loss branches + the rule that lossy branches are only used when the flow direction is fixed pre-construction; `p_scheduled_mw` = sending setpoint pinning `p_in`; the `[Pmin, Pmax]`/`p_min_mw` box as the fundamental bound with band/downward/free as presets; the `cost_coeffs=(c0, c1, c2)` polynomial cost (`cp.square` for quadratic, `cp.abs` for linear); `hvdc_from_dcline` column map verified against `t_case9_dcline` with `loss0`/reactive/voltage columns dropped; `HVDCLink` field table; `hvdc=`/`df_hvdc=` on entry points; results keys `p_hvdc_in`/`p_hvdc_out`/`hvdc_loss`; singlenode silent-ignore contract). Add a `Milestone 15 -- full sign-switching lossy HVDC` (charge/discharge split) row as Future. `what not to do` bullets: no `n_hvdc=0` default; no `q` term; no second `p==`; no hvdc import in singlenode; `cp.multiply` for cost/loss; **never put `cp.abs` (or any non-affine atom) in the `p_out` loss equality -- select an affine branch by pre-construction sign instead**; do not select a lossy branch in `free` mode or in a zero-straddling `band` step; **both HVDC balance terms enter with `+` (signed injections), not `Ch_to − Ch_from`**; do not multiply `p_in`/`p_out` by `baseMVA` in `extract_results` (engineering units, like storage).

**Gate 7 (full suite):** `uv run --extra dev pytest tests/` -- expect `702 + new` passed, 0 failed. `uv run ruff format .` and `uv run ruff check .` clean.

---

## Section 3 -- Risks

**R1 -- MATPOWER `dcline`/`LOSS1` mapping (RESOLVED, verified against `t_case9_dcline`).** Previously unverifiable (no dcline code in the repo). The Pypower `t_case9_dcline` fixture confirms the column order and the loss law `Pt = Pf - loss0 - loss1 * Pf`: rows 0 (`loss0=1, loss1=0.01, Pf=10 → Pt=8.9`) and 3 (`loss0=0, loss1=0.05, Pf=10 → Pt=9.5`) both check out, so `loss1` is a per-unit fraction and `loss_percent = loss1 * 100` is correct. Residual risk: only the `t_case9_dcline` fixture was checked; confirm with the researcher that other `dcline` datasets share this scaling before treating `hvdc_from_dcline` as general. The MVP does not depend on it for solve gates (tests build `HVDCLink` directly).

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

**R4 -- single positional call site (silent-ignore mechanism).** All three
builders, including `_build_singlenode_dc_single`/`multistep`, share the
identical positional signature, and `problem.py` dispatches through one unified
call site per entry point. There is no way to forward `hvdc`/`df_hvdc` to
ac/lossy_dc but not to singlenode without either (a) branching the dispatch on
`formulation` or (b) giving every builder the new params. We choose **(b)**: it
matches how `storage`/`delta`/`nondispatchable`/`df_nd` are already threaded
through every builder including singlenode. The singlenode builders accept
`hvdc`/`df_hvdc` and **drop them silently** -- no warning, and `"n_hvdc"` is
never added to `build.data` for singlenode builds. Consequently "silent ignore"
means **accepted and dropped**, not "signatures unchanged / omitted from the
call path" (the earlier wording, which was mechanically impossible). Mitigation:
Gate 3 asserts `"n_hvdc" not in build.data` and that no `UserWarning` fires;
the drop is documented in the singlenode builder, the entry-point docstrings,
and CLAUDE.md.

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

**R9 -- `toggle_dcline` numpy-2.x float-indexing bug in the fixture script
(Step 0b).** Generating a *solved* Pypower oracle for `t_case9_dcline` requires
`toggle_dcline(ppc, 'on')`, which does float-array indexing at multiple sites
(`e2i[dc[:, F_BUS]]`, `ppc['bus'][dc[:, F_BUS], ...]`, and more across its
ext2int/int2ext hooks). numpy 2.x rejects float indices as a hard `IndexError`
(numpy 1.x only warned) -- same family as the documented `in1d` breakage that
forced the `numpy==2.2.6` pin, but pervasive, not a single site. Verified
empirically this session: patching one site surfaces the next. Downgrading numpy
is not viable in this env (`scipy==1.18.0` requires numpy>=2; older scipy has no
cp313 wheel and builds from source fail). Mitigation (planned): a **scoped
monkeypatch** in `generate_pypower_fixtures.py` coercing integer-valued float
index arrays to int for the duration of the dcline `toggle_dcline`+`runopf` call
only, reverted in a `finally`; the case9/14/57 path is untouched. It is a
Pypower/numpy-compat workaround in an isolated generation script -- **not** in
the package -- and is only run when regenerating fixtures (never in CI). Residual
risk: the monkeypatch is coupled to `toggle_dcline`'s internals; if a future
Pypower/numpy bump changes them, the patch must be revisited. Documented inline
at the patch site.

---

## Open items status
- Item 1 (LOSS1 units): **resolved** -- verified against the `t_case9_dcline` fixture (`Pt = Pf - loss0 - loss1*Pf`); `loss1` is a per-unit fraction, `loss_percent = loss1 * 100`. R1 downgraded to a residual "confirm other datasets share the scaling" note.
- Item 2 (singlenode handling): resolved (silent ignore, no warning).
- Item 3 (test case): resolved -- synthetic `bus 4 -> bus 9` on case9 + pure unit tests; no new case constructor.
- Item 4 (`hvdc_from_dcline` location): resolved -- `hvdc.py`.
- Item 5 (results keys): resolved -- `p_hvdc_in`, `p_hvdc_out`, `hvdc_loss` (derived, ≥0); `(n_hvdc,)` single / `(T, n_hvdc)` multi. **Container:** each of `p_hvdc_in`/`p_hvdc_out` is a single `(n_hvdc,)` `cp.Variable` per step (list of length `T` multistep), matching the storage `b`/nd `p_nd` pattern -- **not** a list of per-link scalar Variables. `extract_results` walks it exactly like `b`/`p_nd`. Documented as signed nodal injections, not directional flows.
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
- Item 8 (fixed converter loss): resolved -- deliberately **not modelled in the
  first implementation**. The `t_case9_dcline` fixture now verifies its sign
  (`+sign(p_in)*loss0`, from `Pt = Pf - loss0 - loss1*Pf`), so it *could* be
  added affinely on fixed-direction branches; it is excluded by choice, not by
  blocker (second-order; would create an inconsistent seam where the no-load
  loss vanishes on zero-straddling steps). MVP is purely proportional; `LOSS0`
  deferred to Milestone 15. No `loss_mw_fixed` field; `hvdc_from_dcline` warns
  on nonzero `loss0`. See R8.
- Item 9 (cost model): resolved -- generalized from a single linear coefficient
  to the full `dclinecost` polynomial `cost_coeffs=(c0, c1, c2)`. Quadratic uses
  `cp.square(p_in)` (since `(|x|)^2 = x^2`), linear uses `cp.abs(p_in)`; both
  convex in the objective. `dclinecost` maps model-2 polynomial rows up to
  quadratic.
- Item 10 (`[Pmin, Pmax]` bounds): resolved -- the fundamental `p_in` bound is a
  general affine box; `band`/`downward`/`free` are presets over it and MATPOWER
  `[Pmin, Pmax]` maps straight on. `HVDCLink` gains `p_min_mw` (defaults keep the
  named modes unchanged). The per-step zero-crossing gate on the box is the
  single source of truth for lossy-vs-lossless, uniform across modes.
- Item 11 (test artifacts / Step 0): resolved -- `t_case9_dcline` becomes a
  committed static case file (`src/cvxopf/testcases/case9_dcline.py` via extended
  `generate_testcases.py`, static input only, no solve) **and** a committed
  Pypower reference fixture (`tests/fixtures/case9_dcline_pypower_reference.json`
  via extended `generate_pypower_fixtures.py`, solved oracle needing the
  `toggle_dcline` monkeypatch -- see R9). Step 0 is done first; both artifacts
  feed downstream gates. The case file is the shared input for Gates 1/4/5/6;
  the fixture is consumed by Gate 6b.
- Item 12 (Pypower comparison scope / Gate 6b): resolved -- **cvxopf solves
  `formulation="ac"`** for this gate (C4), the only apples-to-apples match
  against the AC Pypower oracle (`lossy_dc` would conflate the DC-vs-AC gap with
  the `loss0` gap and lacks `Qg`/`Vm`/`Va_deg`; it is validated by the Gates
  4/6 internal-consistency checks instead). The MATPOWER/Pypower dcline is a
  **dispatchable** resource optimized over `[Pmin, Pmax]` (dummy-generator pair;
  table `Pf` is only a start value -- verified against the MATPOWER manual §7.6),
  so cvxopf's `mode="free"` import (C5) solves the **same** dcline problem and
  the operating points genuinely coincide -- an `objective`/`Pg` oracle
  comparison is meaningful. The match is **approximate, not exact** for two
  reasons: (i) the MVP drops `loss0` (row 0 `loss0=1`); (ii) a `c0`
  cost-alignment assumption (inert for `t_case9_dcline`, all `c0=0`; see C3).
  Gate 6b consumes the fixture via one of: loose tolerance on objective/Pg (a),
  `loss0==0`-links-only tight assertions (b), or internal-consistency checks (c,
  now a supplement rather than the only meaningful check). The test must
  document why it is approximate (dropped `loss0` + `c0` assumption) and cite the
  MVP-vs-M15 table, mirroring the existing "known acceptable discrepancies vs
  Pypower" pattern.
