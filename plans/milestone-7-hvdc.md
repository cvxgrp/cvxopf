# Milestone 7 -- HVDC Transmission Links: Build Plan

Status: in progress -- **Step 0 (T0) complete as of 2026-07-13** (Gate 0 green:
`case9_dcline` case file + Pypower reference fixture generated and verified;
702 tests pass). Steps 1-7 (T1-T7) pending. Baseline confirmed **702 passed,
0 failed** (`uv run --extra dev pytest tests/`).

This plan was written after reading `problem.py`, `ac_problem.py`, `dc_problem.py`, `singlenode_dc_problem.py`, `results.py`, `storage.py`, and `__init__.py`. All design decisions from the Milestone 7 handoff are treated as resolved; this plan records how they map onto the existing code and flags the one item that could not be verified from the codebase.

---

## Section 1 -- Background and reference

### What HVDC adds
An HVDC link is a controllable point-to-point real-power transfer between two buses, `from_bus` and `to_bus`, with a loss model and four operational modes. Unity power factor at both terminals: **no reactive power, no apparent-power circle.** This makes HVDC structurally simpler than storage or nondispatchable units (which both carry a reactive term in AC).

### Representation: the per-step box is the sole internal model
An HVDC link is modelled as a pair of **generator-like objects**, one at each
terminal, not as a line-like object. The two CVXPY variables are the
**signed nodal injections** `p_in` (at `from_bus`) and `p_out` (at `to_bus`),
following the package-wide sign convention: **positive = generation/injection
into the grid, negative = consumption/withdrawal** (consistent with the battery
`b` variable). Terminal flow through the line is a *derived* quantity
(`|p_in|` sending-side / `|p_out|` receiving-side), constructed only if a future
branch-limit milestone needs the true crossing power -- see Section 1 forward
note.

**The one internal representation is a per-step box `p_in ∈ [p_min_t, p_max_t]`.**
That box -- two numbers per link per step -- is the *only* thing the builder's
constraint layer ever sees. There are **no modes inside the builder**: the four
named modes (`scheduled`/`band`/`downward`/`free`) are **upstream helper
functions** that compute the `(p_min_t, p_max_t)` arrays from friendlier inputs
*before* the builder runs (see Operational modes). `p_in` is the single degree
of freedom, bounded by the box; `p_out` is always tied to `p_in` by the affine
loss equality, whose branch is selected per step from the box's zero-crossing
(see Loss model).

**Both `p_in` and `p_out` are always `cp.Variable` objects**, for every helper
and every step -- a representation choice for uniform post-analysis (pull
in/out straight from `build.variables`), *not* a statement about degrees of
freedom. The free-variable count per link per step is a property of the box, not
of any "mode":

| Box shape (per step) | free DOF | how pinned |
|---|---|---|
| degenerate `p_min_t == p_max_t` (e.g. from the `scheduled` helper) | 0 | pinned by **coincident bounds** `p_min_t <= p_in <= p_max_t`; `p_out` by loss equality |
| proper `p_min_t < p_max_t` (`band`/`downward`/`free` helpers) | 1 | `p_in` free within the box; `p_out` derived by loss equality |

So a degenerate box still builds `cp.Variable`s (pinned by coincident bounds,
**not** a separate `p_in == ...` equality and **not** pure numpy). The injection
addend is therefore always a CVXPY expression. Under Convention B (signed
injections) both terminals enter the balance with a **`+`**:
```
p == Cg @ Pg - Pd + ... + inv_baseMVA * (Ch_from @ p_in + Ch_to @ p_out)
```
and likewise as the right-hand addend to `A @ p_flows + p_gen + ... == Pd`.

**Component-method interface (not an omnibus helper).** HVDC is a
formulation-agnostic **model component**: `hvdc.py` exposes named builder
methods that a constructor *calls and composes*, rather than one tuple-returning
helper. The full method inventory is specified in Step 1/Step 2; the two that
bear on this section are the **injection method** (creates the `p_in`/`p_out`
`cp.Variable`s and returns the balance addend above) and `hvdc_cost_expr` (the
cost term, Cost term section). Each OPF constructor (AC / lossy DC / future
SOCP) pulls the pieces it needs and wires the injection into *its own* balance
line and the cost into *its own* objective -- "model the component once, plug
into any network formulation." This is why the addend is owned by the
component, not re-synthesized per constructor.

**The `inv_baseMVA` scaling is a late-bound `cp.Parameter`.** The injection
method builds `inv_baseMVA * (Ch_from @ p_in + Ch_to @ p_out)` against a scalar
`cp.Parameter` **without knowing `baseMVA`**; the calling constructor sets
`inv_baseMVA.value = 1.0 / baseMVA` before `solve()`. This is the project's
first use of `cp.Parameter`, chosen here as a **lazy-construction / late-binding
seam** that keeps the component case-agnostic (the component owns the scaled
addend; the constructor supplies the scale), **not** for fast re-solves over
changing data -- `baseMVA` is fixed for a given problem. The fast-resolve use of
parameterization is a *separate* future item (Milestone 13); this seam does not
pull it forward. Confirmed empirically that a `cp.Parameter` coefficient flows
through the `nlp=True` DNLP/IPOPT path and honors `.value`, so the same scaled
addend works unchanged in AC, lossy DC, and singlenode. The parameter absorbs
the old AC-vs-DC idiom difference (`(1.0/baseMVA) * (...)` vs
`cp.multiply(1.0/baseMVA, ...)`) entirely -- both constructors receive one
already-scaled `cp.Parameter`-carrying expression.

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
IPOPT. `p_in` is the single degree of freedom bounded by the box; `p_out` is
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

**Branch selection reads the box, not the mode.** The single gate is: does the
per-step box `[p_min_t, p_max_t]` straddle zero?
- **Fixed-direction box** (`p_min_t >= 0`, so `p_in >= 0`; or `p_max_t <= 0`, so
  `p_in <= 0`): the sign of `p_in` is fixed by the box, so the matching lossy
  branch is selected for that step.
- **Zero-straddling box** (`p_min_t < 0 < p_max_t`): the flow direction is a
  decision the MVP cannot make affinely, so it falls back to the lossless branch
  (`p_out == -p_in`) for that step and emits a `UserWarning` naming the link and
  the time step.

Because the gate is purely a property of the box, it is **uniform across every
helper** -- there is no per-mode branch-selection logic. The four modes differ
only in *how they fill the box* upstream (see Operational modes); once the box
exists, selection is identical:
- a `scheduled` helper emits a degenerate box `[p_sched_t, p_sched_t]`, whose
  sign is that of `p_sched_t` → fixed-direction → matching branch (unless
  `p_sched_t == 0`, a lossless zero-width box).
- a `downward` helper emits `[0, p_sched_t]` or `[p_sched_t, 0]` → fixed by
  construction → matching branch.
- a `band` helper emits `[p_sched_t - bw, p_sched_t + bw] ∩ [p_min, p_max]`;
  fixed-direction → matching branch, zero-straddling → lossless + warning.
  (Clamping to `[p_min, p_max]` can only shrink the interval, so it never turns
  a fixed-direction band into a straddling one.)
- a `free` helper emits `[p_min, p_max]` (default symmetric `[-p_max, p_max]` →
  straddling → lossless; explicit one-sided box → fixed-direction → matching
  branch).

The zero-crossing gate on the box -- **not** the helper that produced it -- is
the single source of truth.

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

### Operational modes (resolved) -- upstream box-generating helpers

The builder has **one** representation: the per-step box `p_in ∈ [p_min_t,
p_max_t]` (see Representation). The four "modes" are **not** distinct internal
formulations -- they are **convenience helper functions** that compute the
`(p_min_t, p_max_t)` box from friendlier higher-level inputs *before* the
builder runs. Once a helper has produced the box, the builder and the loss gate
treat every link identically; there is no mode branching downstream.

| Helper | Box it produces per step | resulting DOF |
|---|---|---|
| `scheduled` | `[p_sched_t, p_sched_t]` (degenerate; **coincident bounds** pin `p_in`, no separate equality) | 0 |
| `band` (default) | `[p_sched_t - bw, p_sched_t + bw] ∩ [p_min, p_max]` | 1 (0 if the intersection is a point) |
| `downward` | `[0, p_sched_t]` if `p_sched_t >= 0` else `[p_sched_t, 0]` | 1 |
| `free` | `[p_min, p_max]` (default symmetric `[-p_max, p_max]`) | 1 |

The loss branch is then selected **from the box alone** (fixed-direction →
matching lossy branch; zero-straddling → lossless + `UserWarning`), uniformly
for every helper -- see Loss model. So, e.g., a `free` link is **not**
necessarily lossless: a one-sided `[p_min, p_max]` box is fixed-direction and
gets the lossy branch; "always lossless" holds only for the default symmetric
`[-p_max, p_max]`. The zero-crossing gate on the box is the single source of
truth.

`scheduled` is the important special case to state plainly: it produces a
**degenerate (zero-width) box** `p_min_t == p_max_t == p_scheduled_mw`, and the
ordinary box bound `p_min_t <= p_in <= p_max_t` *is* the pin -- there is **no**
separate `p_in == p_scheduled_mw` equality. `downward` is retained purely as a
readable convenience for "no reverse flow"; internally it is just another box
generator with no special status.

`p_scheduled_mw` is the **sending-terminal** setpoint used by the
`scheduled`/`band` helpers to place the box (and carried as a non-binding
reference by `free`/`downward`, e.g. `hvdc_from_dcline` imports -- see Step 1).
Because `p_out` is always derived by the loss equality, delivered power is
*below* `|p_in|` by the loss. Document this in the `HVDCLink` field docs.

### Cost term (resolved)
The per-link cost is the full MATPOWER `dclinecost` polynomial in the transfer
magnitude, `c2 * |p_in|^2 + c1 * |p_in| + c0`. It is built by **`hvdc_cost_expr`,
a named cost method co-located in `hvdc.py`** with the other HVDC component
methods (not built inline inside the injection helper, and not a `cost.py`
function -- see the component-placement note below). The constructor calls it
and adds the result to its own objective. Because `(|x|)^2 = x^2`, the quadratic
term is written directly on `p_in` (no `abs` needed) and the linear term keeps
`cp.abs` so cost is symmetric in flow direction:
```
hvdc_cost_expr(cost_coeffs, p_in):
    c0, c1, c2 = cost_coeffs
    return (c2 * cp.square(p_in)               # quadratic; = c2*|p_in|^2
            + cp.multiply(c1, cp.abs(p_in))    # linear magnitude cost
            + c0)                              # constant (line on)
```
Both `cp.square` and `cp.abs` are convex → legal in the **objective**. Build as
an explicit monomial sum, **not** Horner, so the DCP checker accepts the
quadratic (the same caveat the generator cost documents applies). Use
`cp.multiply`, never `scalar * cp.abs(...)` (CvxpyDeprecationWarning). Since
`p_in` is always a `cp.Variable` (even a degenerate box, where it is pinned by
coincident bounds), the cost term is always a CVXPY expression.

**HVDC cost deliberately does *not* reuse `cost.py`'s `poly_cost_expr`.** That
function builds cost on the **signed** generator variable `Pg` (`cf * x`,
`cf * cp.square(x)`), consumed identically by all three current formulations off
the highest-first `gencost` array. HVDC cost must instead act on the transfer
**magnitude** `|p_in|` -- the linear term needs `cp.abs(p_in)` so the cost is
symmetric in flow direction (a link that can flow either way must not be cheaper
reversed). Feeding `p_in` through `poly_cost_expr` would yield `c1 * p_in`
(signed), silently breaking that symmetry. `hvdc_cost_expr` is therefore a
separate function; a future reader must not "unify" the two.

`HVDCLink.cost_coeffs` is a `(c0, c1, c2)` tuple (default `(0.0, 0.0, 0.0)`),
lowest-first -- the package's user-facing cost-input convention (the same order
`make_singlenode_case` accepts and that every formulation's generator cost flows
through before the `make_*`/import boundary translates it into the highest-first
`gencost`/`dclinecost` array that `poly_cost_expr` consumes; see C2 in Step 1).
The `c0` constant only affects the reported objective (it does not change the
optimum) and is meaningful only while the line is energized; the MVP always
adds it for objective consistency.

**Component placement (why cost lives in `hvdc.py`, not `cost.py`).** Every grid
component should own its full model surface -- data struct, validation,
incidence, constraint-set builders, **and** cost -- in one file, consumed by any
OPF constructor via composition. So `hvdc_cost_expr` lives in `hvdc.py`
alongside the injection and constraint methods, keeping the HVDC component
self-contained and avoiding an `hvdc.py → cost.py` cvxopf-internal import (which
would re-introduce the circular-import risk the import rule guards against).
`cost.py`/`poly_cost_expr` is best understood as the not-yet-refactored
dispatchable-generator component (it predates the component pattern, having been
ported from Pypower); unifying it is Milestone 16 (staged in Step 7). HVDC is
the **reference implementation** of the component pattern that the storage/nd
refactor and future SOCP formulation will copy.

### Formulation coverage (resolved)
- `"ac"` -- real-power injection added to the single `p ==` balance in `_make_step_constraints` (Section 3). No `q ==` change.
- `"lossy_dc"` -- injection added to `A @ p_flows + p_gen + ... == Pd` in `_make_dc_step_constraints` (Section 1).
- `"singlenode_dc"` -- **silently ignored (accepted and dropped).** `hvdc`/`df_hvdc_min`/`df_hvdc_max` are forwarded to the singlenode builders through the shared call site (like `storage`/`nondispatchable`), which drop them without building anything. No warning. `"n_hvdc"` never appears in `build.data` for singlenode builds. See R4.

### Units and detection contract
- `p_in`, `p_out` are in **engineering units (MW)**, like `b`/`p_nd`. Enter the balance divided by `baseMVA`; not rescaled in `extract_results`.
- Detection contract: `"n_hvdc" in build.data`. **Never add `n_hvdc=0` as a default.**
- `hvdc.py` imports **`cvxpy` + `numpy`** -- and **no other cvxopf module**. It
  is a CVXPY-touching model-component module by design (it carries the
  injection/constraint/cost builder methods), so "numpy only" does *not* apply.
  The rule that *does* matter -- and the real content of the `storage.py`
  precedent -- is **no cvxopf-internal imports**: that is what preserves the
  circular-import safeguard (`hvdc.py` is imported by both `ac_problem.py` and
  `dc_problem.py`, themselves deferred-imported by `problem.py`). `cvxpy` is an
  external package and does not threaten that cycle. (CLAUDE.md still describes
  `storage.py`/`nondispatchable.py` as "numpy only"; that accurately reflects
  today's code and is left as-is -- the `cvxpy`+`numpy` component rule is the
  forward pattern, tracked under Milestone 16.)

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

### Step 0 -- `t_case9_dcline` test artifacts (standalone scripts; do FIRST) -- ✅ COMPLETE (2026-07-13)
**As-built note:** the solved fixture (0b) does NOT use `toggle_dcline` -- it is
broken under numpy 2.x across both its `ext2int` and `int2ext` userfcns. The
oracle is instead a self-contained solve: a hand-built `_dcline_to_gens`
(validated gen/bus-equivalent to real pypower in
`scripts/_probe_dcline_transform.py`, Gate 0b-iii) + a custom P-coupling
`formulation` userfcn, with `dclinecost` dropped (matching pypower's own
`t_dcline.py`) and the result cross-checked against pypower's hardcoded expected
array. Full rationale in `scripts/README.md`. The original R9 monkeypatch plan
was abandoned; see R9 and the Step 0b note below.

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
  (`toggle_dcline(ppc, 'on')`) before `runopf`. That path triggers a **chain**
  of numpy-2.x incompatibilities in `toggle_dcline`/`userfcn_dcline_ext2int`
  (see R9), not the single float-index site originally assumed. **Approach
  (revised 2026-07-13):** the scoped-monkeypatch route was attempted and
  abandoned -- each patched site exposed the next (float indices -> `ppc.gencost=`
  attr-set + `zeros(a,b)` -> float `nc` in `range()` -> off-by-one gencost width),
  turning a targeted patch into a full reimplementation-by-patching of a
  function that is effectively unrun on this path. **Instead**, a hand-built
  `_dcline_to_gens` transform reproduces the dcline->dummy-generator conversion's
  *intent* directly (the same "two generators in the loadflow" model Pypower's
  `toggle_dcline` and pandapower's `create_dcline` both implement). It is
  **validated row-for-row against a real (throwaway-patched) `toggle_dcline` run
  in `scripts/_probe_dcline_transform.py` (Gate 0b-iii)** before being wired into
  the committed fixture script, so the committed path never carries the fragile
  patch. pandapower was evaluated as an alternative oracle and shelved: its OPF
  wraps the *same* PYPOWER engine, so it is not an independent implementation and
  carries the same bug-chain risk. Document the transform inline as a
  Pypower/numpy-2.x workaround, not a cvxopf concern.
- The fixture is an **approximate** oracle: Pypower models `loss0` (row 0 has
  `loss0=1`) which the MVP drops, so a cvxopf solve will not match exactly. This
  is expected and consumed accordingly by Gate 6b.

**Gate 0 (offline, own commit) -- ✅ PASSED (2026-07-13):**
- `uv run scripts/generate_testcases.py` produces `case9_dcline.py`;
  `case9_dcline()` imports and loads; `dcline`/`dclinecost` arrays match the
  Pypower source shapes (4 dcline rows, 4 dclinecost rows).
- `uv run scripts/generate_pypower_fixtures.py` produces
  `case9_dcline_pypower_reference.json` with `status == "optimal"`.
- No solve in the test suite here -- these are script runs producing committed
  artifacts. The generated files are what the suite consumes.

### Step 1 -- `src/cvxopf/hvdc.py` (pure logic)
Mirror `storage.py` structure/docstrings:
- `HVDCLink` dataclass: `from_bus, to_bus, p_max_mw, p_min_mw=None, p_scheduled_mw=0.0, bandwidth_mw=0.0, mode="band", loss_percent=0.0, cost_coeffs=(0.0, 0.0, 0.0)`. **These fields are inputs to the upstream box-generating helper (Step 2), not distinct internal formulations** -- `mode` + `p_min_mw`/`p_max_mw`/`p_scheduled_mw`/`bandwidth_mw` are consumed once to produce the per-step box `[p_min_t, p_max_t]`, which is the sole thing the builder sees (see Representation / Operational modes). Docstring: `p_scheduled_mw` is the **sending-terminal setpoint** for `p_in` (the from-bus nodal injection); it places a **degenerate (zero-width) box `[p_sched, p_sched]` only in `"scheduled"` mode** (a pin via coincident bounds, **not** a separate equality) -- in `band`/`free`/`downward` it is a non-binding reference (band centre / reporting / optional warm-start), and `hvdc_from_dcline` imports use it that way (`mode="free"`, optimized over `[Pmin, Pmax]`). `p_out` is always derived, so delivered power is below `|p_in|` by the loss. `cost_coeffs` is `(c0, c1, c2)`. `p_min_mw` is the lower `p_in` bound; when `None`, the helper defaults it per mode (`band`/`free`: `-p_max_mw`; `downward`: `0`) so the named modes are unchanged.
- `_validate_hvdc(links, ext_bus_ids)`: `from_bus != to_bus`; both buses in `ext_bus_ids`; `p_max_mw > 0`; `p_min_mw <= p_max_mw` when given; `bandwidth_mw >= 0`; `loss_percent >= 0`; `c2 >= 0` (convex quadratic) and `c1 >= 0` (nonneg magnitude cost); `mode` in the allowed values. Indexed `ValueError` messages like `_validate_storage`.
- `_make_hvdc_incidence_matrices(links, nb, ext_to_int)` -> `(Ch_from, Ch_to)`, each `(nb, n_hvdc)`; `np.empty((nb,0))` pair for empty input.
- `_hvdc_static_box(links)` -> `(p_min, p_max)`, each `(n_hvdc,)` numpy arrays in
  MW. **This is the sole home of the mode->box mapping** -- the upstream
  box-generating helper (see Operational modes) that turns each link's `mode` +
  `p_min_mw`/`p_max_mw`/`p_scheduled_mw`/`bandwidth_mw` into the static per-link
  box the builder consumes. Per link: `scheduled` -> `[p_sched, p_sched]`
  (degenerate); `downward` -> `[0, p_sched]` if `p_sched >= 0` else `[p_sched,
  0]`; `band` -> `[p_sched - bw, p_sched + bw] ∩ [p_min, p_max]`; `free` ->
  `[p_min, p_max]`. Applies the `p_min_mw=None` per-mode defaults (`band`/`free`:
  `-p_max_mw`; `downward`: `0`) here, so those defaults live in exactly one
  place. The result is what the multistep builder tiles across `T` when
  `df_hvdc_min`/`df_hvdc_max` are not supplied (Step 3), and what the
  single-step builder uses directly; the Step 2 component methods
  (`hvdc_injections` / `dc_operating_constraints`) take these two arrays as
  their box inputs.
The remaining members are the **CVXPY component methods** -- the reusable
builders a formulation constructor calls and composes (this is why `hvdc.py`
imports `cvxpy`; see the corrected import rule in Section 1). They are the
by-method interface (not one omnibus tuple) so each constructor grabs exactly
the pieces its formulation needs -- the pattern the storage refactor and future
SOCP formulation will copy (Milestone 16); HVDC is the reference implementation.
- `hvdc_injections(links, p_min_t, p_max_t, ext_to_int, baseMVA)` -> `(injection_expr, p_in, p_out, inv_baseMVA)`. Creates the per-step `p_in`/`p_out` `cp.Variable`s (each a single `(n_hvdc,)` variable, matching storage/nd), and returns the **already-scaled** balance addend `inv_baseMVA * (Ch_from @ p_in + Ch_to @ p_out)` (Convention B, both `+`). `inv_baseMVA` is a scalar **`cp.Parameter`** the method creates but does **not** set -- the constructor sets `.value = 1.0/baseMVA` before solving (the late-binding seam from Section 1; empirically DNLP-compatible). Returning the scaled term absorbs the old AC `(1.0/baseMVA)*` vs DC `cp.multiply` idiom split into one parameter expression. The constructor adds this addend to *its own* `p ==` / flow-conservation line.
- `dc_operating_constraints(p_in, p_out, p_min_t, p_max_t)` -> `list` of constraints: the box bound `p_min_t <= p_in <= p_max_t` (a degenerate `p_min_t == p_max_t` entry pins by coincident bounds -- **no** separate `p_in == p_scheduled_mw` equality) and the `p_out` loss-branch equality `p_out == coeff_vec * p_in` (the affine branch, `coeff_vec` an `(n_hvdc,)` numpy vector selected from the box's zero-crossing per Section 1 -- never an `abs`-in-equality). No `baseMVA`, no reactive term.
- `ac_operating_constraints(p_in, p_out, p_min_t, p_max_t)` -> **pass-through to `dc_operating_constraints`** (verbatim delegate). For HVDC the AC and DC operating regions are genuinely identical (unity-PF, no reactive coupling), so the AC method is a one-line delegate -- but the `ac_*`/`dc_*` fork is present so the interface *shape* matches what storage/SOCP need (where the AC circle and DC box genuinely diverge). Models the full component pattern without duplicated bodies.
- `hvdc_cost_expr(cost_coeffs, p_in)` -> a CVXPY objective term `c2*cp.square(p_in) + cp.multiply(c1, cp.abs(p_in)) + c0` (signature and body per the Section 1 Cost-term block). Written to accept a single link's `(c0, c1, c2)` and its `p_in` slice; the constructor (or a thin all-links wrapper) sums over the `n_hvdc` links. Convex (legal in the objective); explicit monomial sum, not Horner (same DCP discipline as `poly_cost_expr`, which it deliberately does **not** reuse -- magnitude `|p_in|` vs signed `Pg`; see Cost term). The constructor adds the summed term to *its own* objective.
- `hvdc_from_dcline(dcline_table, dclinecost=None)` -> `list[HVDCLink]`. Column map is now **verified** against the `t_case9_dcline` fixture (see R1) -- header order `fbus tbus status Pf Pt Qf Qt Vf Vt Pmin Pmax QminF QmaxF QminT QmaxT loss0 loss1`. Skip `status==0` rows. Mapping:
  - `from_bus=fbus`, `to_bus=tbus`.
  - `loss_percent = loss1 * 100` (verified: `loss1` is a per-unit fraction; `Pt = Pf - loss0 - loss1*Pf`).
  - `[Pmin, Pmax]` → `p_min_mw` / `p_max_mw` (the fundamental `p_in` box). **`mode="free"`** so the optimizer schedules `p_in` freely within `[Pmin, Pmax]` -- importing a dcline yields a *controllable resource optimized over its rated range*, matching the Pypower semantics, **not** a fixed injection. **`Pf` is carried as a non-binding reference only** (`p_scheduled_mw = Pf`, used for reporting / optional warm-start), **never** as a pin: the MVP does **not** emit a `"scheduled"` link from an import, so `bandwidth_mw` stays at its `0.0` default and no degenerate zero-width band is ever produced. The per-step zero-crossing gate on `[Pmin, Pmax]` then auto-selects lossy (fixed-direction box) vs lossless (straddling box), uniform with every other `free` link -- no separate `"scheduled"`/`"downward"`/`"band"` inference is needed. A row with `Pmin >= 0` or `Pmax <= 0` is naturally fixed-direction and gets the lossy branch; a straddling `[Pmin, Pmax]` is lossless. (For `t_case9_dcline`: row 1 `[2,10]` and row 3 `[0,10]` are both fixed-direction → lossy branch; the optimizer chooses `p_in` in-range rather than being pinned at `Pf`.)
  - `loss0` (fixed loss) is **dropped**; if any active row has `loss0 != 0`, emit a `UserWarning` that the imported model omits fixed converter loss (deferred to Milestone 15) and will not match Pypower exactly.
  - `Qf, Qt, QminF, QmaxF, QminT, QmaxT` (reactive) and `Vf, Vt` (voltage setpoints) are dropped -- MVP is unity-PF, no HVDC voltage control. Note in the docstring.
  - `dclinecost` (optional, model-2 polynomial rows, same row layout as `gencost`) maps to `HVDCLink.cost_coeffs=(c0, c1, c2)`. **The coefficient order flips at this boundary -- note it explicitly (C2):** a MATPOWER model-2 row is `[2, startup, shutdown, n, c_{n-1}, ..., c_1, c_0]`, stored **highest-power-first** (the same layout `poly_cost_expr` consumes across AC/DC/singlenode). `HVDCLink.cost_coeffs` is **lowest-first `(c0, c1, c2)`** -- the package-wide user-facing cost convention (see Section 1 Cost term) -- so `hvdc_from_dcline` must **reverse** when reading a row into the tuple. This is the mirror image of the `make_*` write boundary (lowest-first arg -> highest-first array); do the flip in this one clearly-commented place. **The reversal is `n`-dependent, not a fixed 3-element flip:** read `n` (field 4); a linear row (`n=2`, row `[..., 2, c_1, c_0]`) maps to `(c_0, c_1, 0.0)` (**pad `c2=0`**); a quadratic row (`n=3`, row `[..., 3, c_2, c_1, c_0]`) maps to `(c_0, c_1, c_2)`; reject `n>3` with a clear error (higher-order terms unsupported). A hardcoded 3-element reverse would misread every linear row (reading `c_1` as `c2`, `c_0` as `c1`, `shutdown` as `c0`) -- this is the specific bug the C2 caution and the Gate 1 assertion below guard against. When the `case9_dcline` case file (Step 0a) is the source, `dclinecost` is present and passed through (its row 3 is `n=2, c_1=7.3, c_0=0` -> `(0.0, 7.3, 0.0)`, exercising the linear-row `c2=0` padding); when absent, `cost_coeffs` defaults to `(0.0, 0.0, 0.0)`.
  - **Test artifact source:** `hvdc_from_dcline(case9_dcline()["dcline"], case9_dcline()["dclinecost"])` is the intended entry point for downstream gates -- 4 rows, one inactive (`status==0`) skipped -> 3 links; `loss0!=0` on row 0 emits the documented `UserWarning`.

**Gate 1 (offline unit):** `tests/test_hvdc.py::TestHVDCUnit` -- validation happy/sad, incidence shapes/entries, `_hvdc_static_box` mode->box mappings (`scheduled` -> degenerate `p_min == p_max`; `downward` -> `[0, p_sched]`/`[p_sched, 0]` by sign; `band` -> intersected interval, incl. the `bandwidth_mw=0` degenerate case; `free` -> `[p_min, p_max]`; and the `p_min_mw=None` per-mode defaults), `hvdc_from_dcline` incl. inactive-line skip, and the CVXPY component methods' pure-logic surface: `ac_operating_constraints` returns the *same* constraint list as `dc_operating_constraints` (delegate check), and `hvdc_cost_expr` builds the expected `c2*square + c1*abs + c0` term (assert convex / DCP-valid). **Cost-coefficient reversal + `n`-padding (C2):** assert `hvdc_from_dcline` on a synthetic **quadratic** `dclinecost` row with **distinct nonzero `c0 != c1 != c2`** (e.g. `[2, 0, 0, 3, 5.0, 3.0, 1.0]`) produces `cost_coeffs == (1.0, 3.0, 5.0)` -- `c2` (the highest-power coeff, `5.0`) lands in the quadratic slot, `c0` (`1.0`) in the constant slot. Distinct nonzero values are required so a reversed-index or `c0<->c2` swap actually *fails* the assertion (an all-`c0=0` or all-equal row would let the bug pass silently, exactly the Gate 6b blind spot). And assert a **linear** row (`[2, 0, 0, 2, 7.3, 0.0]`) maps to `(0.0, 7.3, 0.0)` -- the `n=2` `c2=0` padding. No solve.

### Step 2 -- HVDC component methods in `hvdc.py`
The HVDC CVXPY component surface (see the Step 1 method inventory) is a set of
**named methods the constructor calls and composes** -- not one omnibus helper
returning a big tuple. Each method **consumes the per-step box, not a mode**:
its box inputs are two `(n_hvdc,)` numpy arrays `p_min_t`, `p_max_t` -- the box
for this step, already computed upstream by `_hvdc_static_box` (single-step) or
read from `df_hvdc_min`/`df_hvdc_max` (multistep). There is **no mode branching
in any of these methods**: they see only the box. (The upstream helper is what
turned `scheduled`/`band`/`downward`/`free` + `p_scheduled_mw` into these two
arrays; by the time we are here, that distinction is gone.) The constructor
calls `hvdc_injections` for the variables + balance addend, one of
`ac_operating_constraints`/`dc_operating_constraints` for the operating set, and
`hvdc_cost_expr` for the cost term -- adding each to *its own* balance /
constraint list / objective. This is the reference implementation of the
component pattern (Milestone 16).

**`hvdc_injections(links, p_min_t, p_max_t, ext_to_int, baseMVA)` -> `(injection_expr, p_in, p_out, inv_baseMVA)`:**
- **Container shape (matches storage/nd).** `p_in` and `p_out` are each a
  **single `cp.Variable((n_hvdc,))`** for the step -- **not** Python lists of
  per-link scalar Variables. `build.variables["p_hvdc_in"]` is therefore one
  `(n_hvdc,)` Variable single-step, and a `list[cp.Variable]` of length `T`
  (one `(n_hvdc,)` Variable per step) multistep, exactly like
  `variables["b"]`/`variables["p_nd"]`. `extract_results` walks it the same way
  (`var["p_hvdc_in"][t].value` per step).
- **Creates `p_in`/`p_out` and returns the already-scaled balance addend.**
  The addend is (**Convention B -- both `+`**)
  `inv_baseMVA * (Ch_from @ p_in + Ch_to @ p_out)`, where **`inv_baseMVA` is a
  scalar `cp.Parameter`** the method creates but does **not** set. It also
  returns the parameter so the constructor can bind it (`inv_baseMVA.value =
  1.0/baseMVA`) before solving -- the late-binding seam (Section 1;
  empirically DNLP-compatible). Returning the *scaled* term via the parameter
  absorbs the old AC `(1.0/baseMVA)*` vs DC `cp.multiply` idiom split into one
  parameterized expression, so both constructors consume an identical addend.
  Always a CVXPY expression (both terminals are variables regardless of box
  shape). The constructor adds this addend to *its own* `p ==` /
  flow-conservation line.

**`dc_operating_constraints(p_in, p_out, p_min_t, p_max_t)` -> `list`** (and
**`ac_operating_constraints(...)` -> verbatim pass-through to it**): returns
exactly two constraints, no `baseMVA`, no reactive term:
- (1) the **box bound** `p_min_t <= p_in <= p_max_t` -- a single vector
  inequality pair; a degenerate entry `p_min_t[k] == p_max_t[k]` pins that
  link's `p_in` by **coincident bounds**, with **no** separate
  `p_in == p_scheduled_mw` equality. There is no third "scheduled pin"
  constraint -- that case is just a zero-width box.
- (2) the **loss-branch equality**, a single vector equality read from the box.
  The per-link branch can differ (each link's box may or may not straddle
  zero), so it is assembled as `p_out == coeff_vec * p_in` with a per-link
  `coeff_vec` -- an `(n_hvdc,)` numpy array whose entry `k` is chosen
  **pre-construction from link `k`'s box** (see Loss model): `-(1 - loss_frac)`
  if `p_min_t[k] >= 0`, `-(1 + loss_frac)` if `p_max_t[k] <= 0`, else `-1`
  (zero-straddling → lossless, and `warnings.warn` naming the link + step).
  Because `coeff_vec` is numpy and fixed before the equality is built,
  `p_out == coeff_vec * p_in` stays a single affine vector equality -- never an
  `abs`-in-equality, never a per-link Python loop of scalar equalities.

`ac_operating_constraints` is a one-line delegate because HVDC's AC and DC
operating regions are genuinely identical (unity-PF, no reactive coupling); the
fork exists so the interface *shape* matches what storage/SOCP will need (their
AC circle vs DC box genuinely diverge). The constructor appends the returned
list to *its own* constraints.

**`hvdc_cost_expr(cost_coeffs, p_in)`** (Section 1): the per-link polynomial
`c2*cp.square(p_in) + cp.multiply(c1, cp.abs(p_in)) + c0`; the constructor (or a
thin all-links wrapper) sums over links and adds to *its own* objective.
`cp.square`/`cp.abs` are convex → legal in the objective; explicit monomial sum,
not Horner (matches `poly_cost_expr`, which it deliberately does not reuse --
magnitude vs signed `Pg`).

**Gate 2 (offline logic):** extend `test_hvdc.py` -- assertions are stated in
terms of the **box** passed to the methods (the upstream mode→box mapping is
tested separately in Gate 1):
- **degenerate box** (`p_min_t[k] == p_max_t[k]`): `hvdc_injections` builds
  `cp.Variable`s for `p_in`/`p_out` (not numpy) and the pin comes from the box
  bound `p_min_t <= p_in <= p_max_t` in the `dc_operating_constraints` list --
  assert there is **no** separate `p_in == p_scheduled_mw` equality in that
  list; injection addend is a CVXPY expression.
- **fixed-direction lossy box** (`loss_percent>0`): a positive box
  (`p_min_t[k] >= 0`) gives `coeff_vec[k] == -(1-loss_frac)`; a negative box
  (`p_max_t[k] <= 0`) gives `-(1+loss_frac)`.
- **zero-straddling box** (`p_min_t[k] < 0 < p_max_t[k]`): `coeff_vec[k] == -1`
  (lossless) and a `UserWarning` naming link + step fires.
- **mixed batch:** one straddling + one fixed-direction link in the same step
  yields a single vector equality `p_out == coeff_vec * p_in` with the correct
  per-link `coeff_vec` entries (guards against a per-link scalar-loop
  implementation). No solve.
- **sign check:** for a lossless link with `p_in > 0`, assert the balance
  addend injects `+p_in/baseMVA` at from_bus and `-p_in/baseMVA` at to_bus
  (Convention B: both terms `+`, `p_out == -p_in`).

### Step 3 -- `problem.py` wiring
- module-level import + re-export of `HVDCLink`, `hvdc_from_dcline`.
- add `hvdc=None` to `build_opf`; `hvdc=None, df_hvdc_min=None, df_hvdc_max=None` to `build_opf_multistep`.
- **`df_hvdc_min` / `df_hvdc_max` carry the per-step box** -- the sole internal
  representation (see Representation / Operational modes). Two aligned frames,
  each `(T, n_hvdc)`, following the **`df_P`/`df_Q` two-frame precedent** (one
  frame per quantity), **not** the single-frame `df_nd` shape. Cell `[t, k]` is
  link `k`'s `p_min_t` / `p_max_t` at step `t` (engineering units, MW). Columns
  are **positional integers `0..n_hvdc-1`** (an HVDC link spans two buses, so it
  has no single bus-ID key -- this is a **deliberate** departure from `df_nd`'s
  bus-ID columns, matching `df_P`/`df_Q` positional indexing instead; state it
  explicitly so it does not read as an oversight). Validate `df_hvdc_min[t,k] <=
  df_hvdc_max[t,k]` per cell (the same box invariant `_validate_hvdc` checks
  statically).
- **The mode helpers always run to produce a static box; the frames override it
  per step when provided.** Add `_hvdc_static_box(links)` (in `hvdc.py`, Step 1)
  that maps each `HVDCLink`'s `mode` + `p_min_mw`/`p_max_mw`/`p_scheduled_mw`/
  `bandwidth_mw` to its static `(p_min, p_max)` box -- this is the upstream
  box-generating helper the Step 2 helper's inputs come from, and the `scheduled`
  degenerate-box / `band` intersection / `downward` / `free` mappings from
  Operational modes live here. **Fallback (either frame `None`):** run
  `_hvdc_static_box` once and **tile** the result across all `T` steps (a `free`
  link tiles `[p_min, p_max]`; a `scheduled` link tiles `[p_sched, p_sched]`;
  etc.), mirroring the `df_nd` tile-to-fill fallback, with the same `UserWarning`
  when `hvdc is not None` and the frames are `None`. **Time-varying `free`
  bounds** (a box that changes shape per step beyond what the mode helper emits)
  are expressible *only* by supplying the frames explicitly; the tiled fallback
  is always static across `T`.
- forward `hvdc` (single) / `hvdc, df_hvdc_min, df_hvdc_max` (multistep) to **all**
  builders through the single unified call site, passed **keyword-only**
  (`hvdc=`, `df_hvdc_min=`, `df_hvdc_max=`) rather than appended to the positional
  tuple -- including the singlenode builders, which accept the new params and
  **drop them silently** (no warning). This is the storage/nd threading pattern: every builder shares one
  signature, so the params reach singlenode too; singlenode simply does not
  populate `"n_hvdc"` in `build.data`. The silent-ignore contract is thus
  "accepted and dropped," not "omitted from the call path." See R4.
- **do not** branch the dispatch on `formulation`; keep the single call site.

**Gate 3 (wiring):** `tests/test_hvdc.py::TestHVDCWiring` -- two complementary halves guarding the silent-drop convention from both sides:
- *Silent-ignore (singlenode):* three tests (single identical, multistep identical + `df_hvdc_min`/`df_hvdc_max` ignored, no `UserWarning`); assert `"n_hvdc" not in build.data`. Uses fast deterministic singlenode solve.
- *Positive wiring (ac & lossy_dc):* passing `hvdc=[...]` must yield `"n_hvdc" in build.data` with `n_hvdc > 0` and the `p_hvdc_in`/`p_hvdc_out` variables present. This catches a builder that *should* wire HVDC but silently drops it -- the accept-and-ignore convention (R4) makes that failure otherwise invisible. Without this half, Gate 3 only proves silent-ignore is *clean*, never that supported formulations actually *wire*.

### Step 4 -- `dc_problem.py` integration (simpler network formulation first)
- module-level import from `hvdc.py` (matches existing storage/nd imports).
- `_parse_dc_case` gains `hvdc`; validate, build `Ch_from/Ch_to`, store `n_hvdc` + arrays.
- **`_make_dc_step_constraints` composes the HVDC component by calling its methods** -- it does **not** re-synthesize the box/loss math. Given the per-step box arrays `p_min_t`/`p_max_t`, it calls `hvdc_injections(...)` (getting `injection_expr`, the `p_in`/`p_out` variables, and the `inv_baseMVA` parameter), adds `injection_expr` to the single flow-conservation line, and calls `dc_operating_constraints(p_in, p_out, p_min_t, p_max_t)` and appends the returned list (box bound incl. the coincident-bounds degenerate pin, plus the `p_out` loss-branch equality). No per-mode branching -- the box is already resolved upstream by `_hvdc_static_box`/`df_hvdc_min`/`df_hvdc_max`.
- cost: the builder calls `hvdc_cost_expr(cost_coeffs, p_in)` per link and adds the summed term to its objective (like storage aging cost).
- **`inv_baseMVA` binding:** the builder sets `inv_baseMVA.value = 1.0/baseMVA` on the parameter returned by `hvdc_injections` before solving (the late-binding seam, Section 1).
- both single/multistep builders thread `hvdc`/`df_hvdc_min`/`df_hvdc_max`; per-step `p_in`/`p_out` vars; populate `variables["p_hvdc_in"]`, `variables["p_hvdc_out"]`, `data["n_hvdc"]`, etc. Single-step uses `_hvdc_static_box(links)` directly for the box; multistep reads the per-step box `(p_min_t, p_max_t)` from `df_hvdc_min`/`df_hvdc_max` row `t` (falling back to the tiled `_hvdc_static_box` result when either frame is `None`, per Step 3). The Step 2 methods (`hvdc_injections` / `dc_operating_constraints`) receive those two `(n_hvdc,)` box arrays -- there is no per-mode branching in the builder.

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
- `_make_step_constraints` **composes the same HVDC component methods** (identical component-method calls as the DC builder makes -- this is what `ac_operating_constraints` being a pass-through to `dc_operating_constraints` buys). Section 3: add the `injection_expr` returned by `hvdc_injections(...)` (the already-`inv_baseMVA`-scaled `Ch_from @ p_in + Ch_to @ p_out` addend, Convention B both terms `+` -- see Section 1) to the `p ==` line only; **leave `q ==` untouched**. Append the list from `ac_operating_constraints(p_in, p_out, p_min_t, p_max_t)` (box bound incl. the coincident-bounds degenerate pin, plus the `p_out` loss-branch equality) as a new labelled **`Section 4c: HVDC operating constraints`** -- placed after the storage (§4) and nondispatchable (§4b) operating-constraint sections, matching the established convention that all operating constraints follow the §3 balance (not a `3b` between balance and storage). Preserve the single-`p==` rule.
- cost: the builder calls `hvdc_cost_expr(cost_coeffs, p_in)` per link and adds the summed term to `total_cost` alongside storage aging cost.
- **`inv_baseMVA` binding:** set `inv_baseMVA.value = 1.0/baseMVA` before solving, same as the DC builder (Step 4).
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
- `CLAUDE.md`: flip Milestone 7 to complete; add HVDC formulation subsections (Convention-B nodal balance mods with **both** `Ch_from @ p_in` and `Ch_to @ p_out` entering with `+`; `p_in`/`p_out` are signed nodal injections, HVDC terminals modelled as generator-like objects; both are always `cp.Variable`s; the **per-step box `[p_min_t, p_max_t]` is the sole internal representation** and the four named modes are upstream box-generating helpers (`_hvdc_static_box`), not distinct builder formulations; DOF is a property of the box (0 for a degenerate `p_min_t == p_max_t` box, pinned by coincident bounds -- not a separate equality; 1 for a proper box); sign-split affine loss branches selected purely from the box's zero-crossing (lossy iff the box is fixed-direction), with no per-mode branch logic downstream; `p_scheduled_mw` places a degenerate box (a pin via coincident bounds) only in `"scheduled"` mode and is a non-binding reference otherwise; the `cost_coeffs=(c0, c1, c2)` polynomial cost (`cp.square` for quadratic, `cp.abs` for linear); `hvdc_from_dcline` column map verified against `t_case9_dcline` with `loss0`/reactive/voltage columns dropped; `HVDCLink` field table; `hvdc=` on entry points and `df_hvdc_min=`/`df_hvdc_max=` on `build_opf_multistep` (two aligned `(T, n_hvdc)` box frames, `df_P`/`df_Q` precedent, positional columns); results keys `p_hvdc_in`/`p_hvdc_out`/`hvdc_loss`; singlenode silent-ignore contract). Add a `Milestone 15 -- full sign-switching lossy HVDC` (charge/discharge split) row as Future. Add a **`Milestone 16 -- unify grid component model patterns`** row as Future: refactor dispatchable generators into a first-class component module (`dispatchable_generator.py`) matching the storage/nondispatchable/HVDC pattern -- data struct, validation, incidence, constraint-set builder, and cost expression co-located, importing `cvxpy`+`numpy` only (no cvxopf-internal imports), consumed by every OPF formulation constructor (AC/DC/singlenode/future SOCP) via composition rather than re-synthesis; `cost.py`'s `poly_cost_expr` becomes that module's cost function. HVDC (Milestone 7) is the reference implementation of the "model a component once, plug into any network formulation" contract. **Do not** rewrite the existing `storage.py`/`nondispatchable.py` "numpy only" import-chain lines in the Module-responsibilities section -- they accurately describe current code; M16 is the aspirational forward pattern. `what not to do` bullets: no `n_hvdc=0` default; no `q` term; no second `p==`; no hvdc import in singlenode; `cp.multiply` for cost/loss; **never put `cp.abs` (or any non-affine atom) in the `p_out` loss equality -- select an affine branch by pre-construction sign instead**; do not select a lossy branch for a zero-straddling box (`p_min_t < 0 < p_max_t`) -- the lossy branch is valid only when the box is fixed-direction (`p_min_t >= 0` or `p_max_t <= 0`); **both HVDC balance terms enter with `+` (signed injections), not `Ch_to − Ch_from`**; do not multiply `p_in`/`p_out` by `baseMVA` in `extract_results` (engineering units, like storage).

- `README.md`: add the **Milestone 16 -- unify grid component model patterns** entry to the milestone list (same framing as the CLAUDE.md row -- dispatchable generators become a first-class component module, all components consumed by every formulation via composition, HVDC as reference implementation). Keep it terse; the CLAUDE.md row carries the detail.

**Gate 7 (full suite):** `uv run --extra dev pytest tests/` -- expect `702 + new` passed, 0 failed. `uv run ruff format .` and `uv run ruff check .` clean.

---

## Section 3 -- Risks

**R1 -- MATPOWER `dcline`/`LOSS1` mapping (RESOLVED, verified against `t_case9_dcline`).** Previously unverifiable (no dcline code in the repo). The Pypower `t_case9_dcline` fixture confirms the column order and the loss law `Pt = Pf - loss0 - loss1 * Pf`: rows 0 (`loss0=1, loss1=0.01, Pf=10 → Pt=8.9`) and 3 (`loss0=0, loss1=0.05, Pf=10 → Pt=9.5`) both check out, so `loss1` is a per-unit fraction and `loss_percent = loss1 * 100` is correct. Residual risk: only the `t_case9_dcline` fixture was checked; confirm with the researcher that other `dcline` datasets share this scaling before treating `hvdc_from_dcline` as general. The MVP does not depend on it for solve gates (tests build `HVDCLink` directly).

**R2 -- Convention-B sign of the balance addend.** Both HVDC terminals are
signed nodal injections and enter the balance with `+`:
`inv_baseMVA * (Ch_from @ p_in + Ch_to @ p_out)` (the single already-scaled
addend the injection method returns; `inv_baseMVA` is the late-bound
`cp.Parameter` from Section 1). The tempting terminal-flow
form `(Ch_to @ p_to - Ch_from @ p_from)` is a different (line-like) convention
and, combined with the loss branches written in Convention B (`p_out = -p_in`),
produces a latent sign bug (withdrawal at *both* ends). Mitigation: uniform
Convention B throughout; Gate 2 sign check asserts `+p_in` at from_bus and
`-p_in` at to_bus for a lossless link; CLAUDE.md `what not to do` bullet.

Note: because the component now hands both constructors *one* already-scaled
`inv_baseMVA`-carrying addend, the old AC-vs-DC scaling-idiom split this risk
was partly about (`(1.0/baseMVA) * (...)` in AC vs `cp.multiply(1.0/baseMVA,
...)` in DC -- two hand-written forms that could drift in sign or shape) is
**dissolved**: there is exactly one expression, built once, so there is no
second site to get wrong.

Note: there is no numpy-vs-CVXPY dual for any box shape -- `p_in`/`p_out` are
`cp.Variable`s for every box, including a degenerate `p_min_t == p_max_t` box
(pinned by coincident bounds, **not** a separate equality and **not** numpy).
The injection addend and cost term are always CVXPY expressions, which removes
the former type-branching risk entirely.

**R3 -- single-`p==` invariant (AC).** Section 3 owns the only `p ==` constraint. hvdc term must be added inside that expression, bounds in a separate sub-section. Mitigation: CLAUDE.md invariant + targeted test.

**R4 -- single positional call site (silent-ignore mechanism).** All three
builders, including `_build_singlenode_dc_single`/`multistep`, share the
identical positional signature, and `problem.py` dispatches through one unified
call site per entry point. There is no way to forward
`hvdc`/`df_hvdc_min`/`df_hvdc_max` to
ac/lossy_dc but not to singlenode without either (a) branching the dispatch on
`formulation` or (b) giving every builder the new params. We choose **(b)**: it
matches how `storage`/`delta`/`nondispatchable`/`df_nd` are already threaded
through every builder including singlenode. The new params are passed
**keyword-only** at the dispatch site (`hvdc=`, `df_hvdc_min=`, `df_hvdc_max=`),
not appended to the positional tuple -- so adding a component does not force a
silent positional-arity change across all three builder signatures, and a
misaligned arg cannot land in the wrong slot. (The general case -- components
threaded by composition rather than as per-builder positional args -- is an
M16 concern; see the Step 7 doc note.) The singlenode builders accept
`hvdc`/`df_hvdc_min`/`df_hvdc_max` and **drop them silently** -- no warning, and `"n_hvdc"` is
never added to `build.data` for singlenode builds. Consequently "silent ignore"
means **accepted and dropped**, not "signatures unchanged / omitted from the
call path" (the earlier wording, which was mechanically impossible). Mitigation:
Gate 3's silent-ignore half asserts `"n_hvdc" not in build.data` and that no
`UserWarning` fires (its positive-wiring half separately guards that ac/lossy_dc
do *not* drop HVDC -- see Step 3);
the drop is documented in the singlenode builder, the entry-point docstrings,
and CLAUDE.md.

**R5 -- affine loss branch depends on pre-construction direction choice.**
The loss equality is only affine because the flow direction (sign of `p_in`) is
fixed before the problem is built -- i.e. because the per-step box
`[p_min_t, p_max_t]` does not straddle zero. If a future edit lets the direction
be a decision variable while still carrying a lossy branch (a zero-straddling
box with a nonzero loss coefficient), the equality becomes nonconvex/non-DCP and
will be silently wrong (convex) or rejected. Mitigation: CLAUDE.md invariant --
the lossy branch may only be selected when the box is fixed-direction
(`p_min_t >= 0` or `p_max_t <= 0`); a zero-straddling box is **always** lossless
(`p_out == -p_in`). This is a pure property of the box, with no mode taxonomy to
get wrong -- the same single gate the Loss model and
`dc_operating_constraints` (where `coeff_vec` is selected from the box's
zero-crossing; `hvdc_injections` only creates the variables) use. The full
sign-switching lossy model (charge/discharge split) is a separate future
milestone.

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
cp313 wheel and builds from source fail). **Mitigation (revised 2026-07-13 --
scoped monkeypatch ABANDONED):** the scoped-monkeypatch route was attempted and
abandoned mid-session; the bug chain (float indices -> `ppc.gencost=` attr-set +
`zeros(a,b)` -> float `nc` in `range()` -> off-by-one gencost width) turned it
into a full reimplementation-by-patching. **The chosen path is a hand-built
`_dcline_to_gens` transform** (Step 0b) that reproduces the
dcline->dummy-generator conversion directly, **validated row-for-row against a
real (throwaway-patched) `toggle_dcline` run** (`scripts/_probe_dcline_transform.py`,
Gate 0b-iii) before wiring into the committed fixture script -- so no fragile
patch lands in the committed generator. pandapower was evaluated as an
independent oracle and shelved (its OPF wraps the same PYPOWER engine; not
independent, same bug-chain risk). Residual risk: `_dcline_to_gens` reproduces
Pypower's transform by hand, so the 0b-iii row-for-row validation against real
Pypower is what makes the resulting fixture a trustworthy oracle; if it does not
match, the fixture is not usable for Gate 6b. Documented inline in the probe and
(once wired) the fixture script.

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
  generator-like objects with signed **nodal injections `p_in`/`p_out`**
  (Convention B: positive = injection, both balance terms `+`). The **sole
  internal representation is a per-step box `p_in ∈ [p_min_t, p_max_t]`**; the
  four named modes (`scheduled`/`band`/`downward`/`free`) are upstream
  box-generating helpers (`_hvdc_static_box`), **not** distinct builder
  formulations. `p_in` is the single DOF, bounded by the box; `p_out` is tied by
  the loss equality. Both are always `cp.Variable`s: DOF is a property of the box
  (0 for a degenerate `p_min_t == p_max_t` box, pinned by **coincident bounds** --
  **not** a separate equality and **not** numpy; 1 for a proper box). Result keys
  renamed `p_hvdc_from/to` -> `p_hvdc_in/out`. Fixed a latent sign bug
  (terminal-flow addend mixed with Convention-B loss branches). Terminal flow is
  derived on demand for a future branch-limit milestone.
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
- Item 10 (`[Pmin, Pmax]` bounds): resolved -- **the per-step box `[p_min_t,
  p_max_t]` is the sole internal representation** (see Item 7), not a bound
  layered over modes. The four named modes are upstream helpers
  (`_hvdc_static_box`) that *fill* the box: `scheduled` -> degenerate
  `[p_sched, p_sched]`, `band` -> `[p_sched-bw, p_sched+bw] ∩ [p_min, p_max]`,
  `downward` -> `[0, p_sched]`/`[p_sched, 0]`, `free` -> `[p_min, p_max]`;
  MATPOWER `[Pmin, Pmax]` maps straight onto a `free` box. `HVDCLink` gains
  `p_min_mw` (the helper's `p_min_mw=None` per-mode defaults keep the named modes
  unchanged, and live in exactly one place -- `_hvdc_static_box`). The per-step
  zero-crossing gate on the box is the single source of truth for
  lossy-vs-lossless, with no per-mode branch logic downstream.
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
