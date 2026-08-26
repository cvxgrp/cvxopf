# CLAUDE.md — Developer Guide for AI Coding Agents

This file provides context for AI coding agents working in the `cvxopf`
repository. Read this before making any changes.

---

## What this project is

`cvxopf` is a Python package for optimal power flow (OPF) using CVXPY,
supporting multiple formulations:

- **AC-OPF** via CVXPY's disciplined nonlinear programming (DNLP) framework,
  solved via IPOPT (nonconvex)
- **Lossy DC OPF** as a convex QP, solved via CLARABEL
- **Single-node DC dispatch** as a convex copper-plate QP, solved via CLARABEL

It is designed for long-horizon power-system resilience research. The package
combines reusable device models, multistep optimization, and hierarchical
convex-to-AC execution so broad planning studies retain a deliberate path back
to nonlinear network physics.

The package is developed by the CVX Group at Stanford.

---

## Design aesthetic (read this first)

This project follows a specific engineering aesthetic, articulated by Stephen
Boyd (creator of CVXPY and disciplined convex programming) in recorded remarks
on the *inControl* podcast (Ep. 10, from 9:55). The load-bearing lines:

> "The real value of math in applied settings ... is what it gives you is
> **Clarity of Thought**."

> "When people just hack something together to knock off 87 requirements, it's
> going to be horrible code ... you cannot extend it. Whereas ... people who
> ... took the time to work out ... the [right] abstractions [get] beautiful,
> lean code that has very high probability of being correct. It's extensible.
> It's maintainable. ... the cost of ownership ... is a lot less."

The move that matters: before implementing, find the case where "the 87 things
we've been asked to implement are actually all instances of only three
different things," and implement *those three* correctly. (He offers Linux as
the model.)

**This is the operative standard for this codebase**, and existing decisions
are instances of it: the **device/network DCP boundary** (one abstraction lets
every device compose into every formulation), **Milestone 16** (three
near-duplicated device implementations reduced to one component pattern), and
**correctness honesty** (Pypower-validation and HVDC Gate-6b surfaced and
explained discrepancies rather than loosening a tolerance).

Practical implications:
- Prefer finding the underlying abstraction over adding a special case. A
  special case is a signal you may not have found the right abstraction yet.
- Lean, correct, extensible beats fast-and-working-looking. Cost of ownership
  is a first-class concern.
- If a change makes the code harder to debug, extend, or reason about, that is
  a real cost even if it "works."

Source: inControl podcast, Ep. 10, Stephen Boyd (from 9:55).
https://www.incontrolpodcast.com/1632769/episodes/12444508-ep10-stephen-boyd-linear-matrix-inequalities-convex-optimization-disciplined-convex-programming-rock-roll


---

## Repository layout

`src/cvxopf/` contains the public build and hierarchical APIs, formulation
builders, shared typed component assembly, result extraction, and one module
per grid component (`generator.py`, `storage.py`, `nondispatchable.py`,
`hvdc.py`, and `load.py`). `testcases/` holds MATPOWER cases (case9–case118,
including PWL and dcline variants). `tests/`, `examples/`, `experiments/`,
`notebooks/`, `plans/`, and `scripts/` are top-level. Use `rg --files` for the
current inventory; do not treat this summary as an exhaustive file list.

---

## Running tests

Always use `uv run` so the correct virtual environment and extras are used:

```bash
uv run --extra dev pytest tests/ -v
```

Expected result: all tests pass; use the collected count from the current
branch rather than a fixed historical test count.

To run a single test file:

```bash
uv run --extra dev pytest tests/test_network.py -v
```

To run with coverage:

```bash
uv run --extra dev pytest tests/ --cov=cvxopf --cov-branch --cov-report=term-missing
```

Do not use plain `pytest` without `uv run --extra dev` — it may use the
wrong environment and fail to find dependencies.

---

## Running the notebook

```bash
uv run --extra notebook marimo run notebooks/cvxopf_demo.py
```

Requires the `notebook` extra: `marimo`, `networkx`, `matplotlib`.
Install with: `uv sync --extra dev --extra notebook`

---

## Critical: how to solve OPF problems

**Always use the `build.solve()` convenience method. Never call
`build.prob.solve()` directly.**

`build.solve()` sets the correct solver defaults based on the formulation:

| `is_convex` | `formulation` | Solver default | `nlp` default |
|---|---|---|---|
| `False` | `"ac"` | `cp.IPOPT` | `True` |
| `True` | `"lossy_dc"` | `cp.CLARABEL` | `False` |
| `True` | `"singlenode_dc"` | `cp.CLARABEL` | `False` |

```python
build = build_opf(case9(), formulation="ac")
build.solve()                  # correct — IPOPT, nlp=True (quiet)
build.solve(verbose=True)      # correct — shows CVXPY + IPOPT output

build = build_opf(case9(), formulation="lossy_dc")
build.solve()                  # correct — CLARABEL, nlp=False
```

**Why `nlp=True` matters for AC:** AC-OPF problems are nonconvex and will
fail CVXPY's DCP check. `nlp=True` bypasses the DCP check and invokes DNLP
canonicalization instead. Calling `build.prob.solve(solver=cp.IPOPT)`
without `nlp=True` will raise a `DCPError`.

**Why `nlp=False` matters for DC:** Lossy DC OPF is a convex QP. Setting
`nlp=True` on a convex problem is incorrect and may produce wrong results.

**Verbose and IPOPT output:** IPOPT prints its banner and iteration log at
the C level, unaffected by CVXPY's `verbose` flag. `build.solve()` bridges
this: on the AC path, `verbose=False` (the default) injects IPOPT's own
`print_level=0` and `sb="yes"` to silence it, and `verbose=True` injects
neither so IPOPT's output prints alongside CVXPY's. Both are `setdefault`, so
an explicit `print_level=` still wins. (CLARABEL on the DC path is quiet by
default and needs no such bridge.)

---

## Formulations

### `"ac"` — Full AC-OPF (DNLP)

The formulation uses auxiliary `(nb, nb)` matrices `P` and `Q` to express
power flows via elementwise trig expressions on the Ybus sparsity pattern.
Nodal injections `p`, `q` are row sums of `P`, `Q`. Generator variables
`Pg`, `Qg` are linked via the incidence matrix `Cg`.

Branch-terminal real and reactive powers are defined from the same exact
MATPOWER branch admittances and retained as lifted per-unit expressions.
By default, every positive finite `rateA` is enforced as an apparent-power
limit at both terminals. Set `OPFOptions(enforce_branch_limits=False)` only
when the ratings should be inert; reporting remains available.

Variables: `theta`, `v`, `p`, `q`, `Pg`, `Qg`, and either:
- `P_vec`, `Q_vec` — shape `(nnz,)` flat vectors over the Ybus sparsity
  pattern when `OPFOptions.sparse_pq=True` (default). Nodal injections are
  recovered via a precomputed `(nb, nnz)` scatter matrix `Rp`:
  `p = Rp @ P_vec`, `q = Rp @ Q_vec`. Eliminates `nb²-nnz` trivially-zero
  variables and their fixing constraints.
- `P`, `Q` — shape `(nb, nb)` dense matrices when `OPFOptions.sparse_pq=False`.
  Off-sparsity entries are fixed to zero via `P[Z]==0`, `Q[Z]==0` constraints.
  Use for research comparison and timing measurements against the sparse path.

**Storage variables** (present only when `storage` is not None):
- `b` — real power (ns,) MW, positive = discharging
- `b_q` — reactive power (ns,) MVAr, positive = injecting
- `soc` — state of charge (ns,) MWh
- Operating set: `b_t[s]^2 + b_q_t[s]^2 <= S_max[s]^2` (apparent power circle)
- Nodal balance modified: `p = Cg @ Pg - Pd + (1/baseMVA) * Cs @ b_t`
- Reactive balance modified: `q = Cg @ Qg - Qd + (1/baseMVA) * Cs @ b_q_t`

Voltage magnitude and reactive dispatch currently enter the physical AC
equations and operating limits but ordinarily have no separate objective
preference. A value at a voltage or reactive bound may therefore be physically
required, economically nonunique, or selected by a local nonlinear solve; do
not assume it is a defect or add an ad hoc penalty. Milestone 20 will
characterize the distinction before adding any optional voltage/reactive
regularization. See
`plans/milestone-20-ac-voltage-reactive-regularization.md`.

**Nondispatchable variables** (present only when `nondispatchable` is not None):
- `p_nd` — real power (nnd,) MW, non-negative, bounded above by available power
- `q_nd` — reactive power (nnd,) MVAr
- Operating set: `p_nd_t[n]^2 + q_nd_t[n]^2 <= P_max[n]^2` (apparent power
  circle) and `0 <= p_nd_t[n] <= R_t[n]` (available power upper bound)
- Nodal balance modified: `p = Cg @ Pg - Pd + (1/baseMVA) * Cs @ b_t + (1/baseMVA) * Cnd @ p_nd_t`
- Reactive balance modified: `q = Cg @ Qg - Qd + (1/baseMVA) * Cs @ b_q_t + (1/baseMVA) * Cnd @ q_nd_t`
- Storage terms absent when `storage=None`; ND terms absent when `nondispatchable=None`

**HVDC variables** (present only when `hvdc` is not None):
- `p_hvdc_in` — from-terminal signed nodal injection (n_hvdc,) MW,
  Convention B (positive = injection into the grid)
- `p_hvdc_out` — to-terminal signed nodal injection (n_hvdc,) MW, Convention B
- Both are always `cp.Variable`s (even for a degenerate `p_min == p_max` box,
  which is pinned by coincident bounds, not a separate equality)
- Operating set (per link): box bound `p_min_t <= p_in <= p_max_t`, plus the
  passive proportional-loss coupling: `p_out == -(1 - loss_frac) * p_in`
  for nonpositive `p_in`, and `p_out == -p_in / (1 - loss_frac)` for
  nonnegative `p_in`, so the sum of signed grid injections is nonpositive, on
  fixed-direction links (affine branch selected pre-construction from the
  box's zero-crossing; lossless coupling `p_out == -p_in` on zero-straddling
  or lossless links). `loss_frac = loss_percent / 100`.
- Real balance modified: `p = ... + (1/baseMVA) * (Ch_from @ p_in + Ch_to @ p_out)`
  — **both terminals enter with `+`** (signed injections, Convention B), never
  `Ch_to - Ch_from`. No reactive term (unity-PF MVP).
- Optional polynomial cost `c2 * p_in^2 + c1 * |p_in| + c0` per link
  (`cost_coeffs`, `cp.square`/`cp.abs`); zero-cost when `cost_coeffs` is zero.
- HVDC terms absent when `hvdc=None`

| Formulation | Result keys |
|---|---|
| AC | `status`, `objective`, `Pg`, `Qg`, `Vm`, `Va_deg`, `p_net`, `q_net`, `branch_p_from`, `branch_q_from`, `branch_p_to`, `branch_q_to`, `branch_s_from`, `branch_s_to`; plus `p_hvdc_in`, `p_hvdc_out`, `hvdc_loss` (derived, `= -(p_hvdc_in + p_hvdc_out)`, always >= 0) when `hvdc` is not None |
| Lossy DC | `status`, `objective`, `Pg`, `p_flows`, `p_net`; plus `p_hvdc_in`, `p_hvdc_out`, `hvdc_loss` when `hvdc` is not None. `Vm`, `Va_deg`, `Qg`, `q_net` absent |
| Single‑node DC | `status`, `objective`, `Pg`, `p_net`. `p_flows`, `Vm`, `Va_deg`, `Qg`, `q_net` absent |

Code consuming results from more than one formulation should use
`results.get('Vm')` rather than `results['Vm']` — DC and single‑node omit
the AC‑only keys.

Do not change this formulation without understanding the DNLP paper.

### `"lossy_dc"` — Lossy DC OPF (convex QP)

Reference: *Convex Optimization with Smart Grid Examples*,
https://doi.org/10.2172/3018252

Objective: minimize
`delta * sum_t (G_t + loss_weight * L_t) + terminal_cost`
- `G = sum_k (c0_k + c1_k * Pg_k + c2_k * Pg_k^2)` — generation cost
- `L = sum_e r_e * p_flows_e^2` — line losses
- `loss_weight` is user-configurable via `OPFOptions.loss_weight` (default 1.0)

`G_t` and the weighted loss proxy are stage-cost rates. The terminal term is
a once-per-horizon boundary cost and is not scaled by `delta`.

Constraints:
- `A @ p_flows + Cg @ Pg == Pd` — flow conservation at every bus
- `|p_flows[e]| <= f_max[e]` — branch flow limits
- Generator output bounds

Variables: `p_flows`, `Pg`

**Device models in DC** – No reactive term (`b_q`, `q_nd` absent). Storage uses a real‑power bound `|b_t| ≤ S_max` (emits a `UserWarning`). Nondispatchable units retain both explicit real-power bounds, `0 ≤ p_nd_t ≤ R_t` and `p_nd_t ≤ S_max`; whichever upper bound is smaller is active. HVDC model is identical to AC (box bounds plus proportional‑loss coupling). Results omit `Vm`, `Va_deg`, `Qg`, `q_net` (see the results-key table under `"ac"`).

There is no Pypower oracle for DC validation. Correctness is verified via
internal consistency checks: flow conservation, bound feasibility, T=1
equivalence with single-step.

### `"singlenode_dc"` — Single-node DC dispatch (convex QP)

Collapses the entire network to a single bus. No branch flows, no
transmission constraints, no line losses, no reactive power. Enforces
scalar real power balance:

    sum(Pg) + (1/baseMVA)*sum(b) + (1/baseMVA)*sum(p_nd) == Pd_total

where Pd_total = sum(bus[:, PD]) / baseMVA.

Objective: minimize the time integral of generation and storage-aging
stage-cost rates plus any once-per-horizon terminal cost.

Variables: Pg (ng,) per-unit, b/soc when storage present,
p_nd when nondispatchable present. (Results keys: see the table under `"ac"`.)

Accepts make_singlenode_case() to build a minimal case dict without
requiring a full MATPOWER case. Also accepts any standard MATPOWER case
dict — the branch table is present but ignored.

The default solver is CLARABEL (nlp=False).

### Future formulations

The dispatch architecture in `problem.py` accepts new formulation keys
without API changes. Planned future formulations:

| Key | Description |
|---|---|
| `"socp"` | SOCP relaxation (convex) |

To add a new formulation, follow the complete formulation-extension contract
under **Module responsibilities**. In brief: implement and register both
network builders and the result extractor, then declare the formulation's
capability explicitly on every component adapter.

---

## Public API

### Entry points

```python
build_opf(case, *, formulation="ac", options=None,
          storage=None, delta=1.0,
          nondispatchable=None, hvdc=None, generators=None,
          loads=None) -> OPFBuild

build_opf_multistep(case, df_P=None, df_Q=None, *, T, formulation="ac",
                    options=None, coupling_constraints=None,
                    storage=None, delta=1.0,
                    nondispatchable=None, df_nd=None,
                    hvdc=None, df_hvdc_min=None, df_hvdc_max=None,
                    generators=None, loads=None,
                    df_load_p=None, df_load_q=None) -> OPFBuild
```

### Deprecated aliases (will be removed in a future release)

```python
build_acopf(...)              # use build_opf(..., formulation="ac")
build_acopf_multistep(...)    # use build_opf_multistep(..., formulation="ac")
```

Both emit `DeprecationWarning` when called.

### `OPFOptions` fields

| Field | Type | Default | Applies to |
|---|---|---|---|
| `enforce_vset` | bool | False | AC only |
| `sparsity_tol` | float | 0.0 | AC only |
| `init_flat` | bool | True | AC only |
| `enforce_branch_limits` | bool | True | AC two-terminal `rateA` limits; requires `sparsity_tol=0` |
| `loss_weight` | float | 1.0 | DC only |
| `branch_limit_sentinel` | float | 1e6 | DC only |
| `sparse_pq` | bool | True | AC only |

`delta` is not an `OPFOptions` field. It is a separate parameter on
`build_opf` and `build_opf_multistep`. It must always be a finite, strictly
positive real scalar, regardless of whether a temporal device is present.
Booleans are not accepted as numeric time-step durations. Storage uses
`delta` in its SoC dynamics, and shared component assembly multiplies the sum
of all stage-cost rates by `delta`. Terminal costs are not time-scaled.

### `OPFBuild` fields

| Field | Type | Description |
|---|---|---|
| `prob` | `cp.Problem` | The CVXPY problem |
| `variables` | dict | Named CVXPY variables. AC keys depend on `sparse_pq` (`P_vec`/`Q_vec` or `P`/`Q`). When `storage` is not None, adds `b`, `b_q` (AC only), `soc` as `cp.Variable (ns,)` single-step or `list[cp.Variable]` multistep. When `nondispatchable` is not None, adds `p_nd`, `q_nd` (AC only) as `cp.Variable (nnd,)` single-step or `list[cp.Variable]` multistep. All storage keys absent when `storage=None`; all ND keys absent when `nondispatchable=None`. |
| `data` | dict | Pre-computed numpy arrays and metadata. When storage is present, adds `ns`, `Cs`, `storage_bus`, `storage_apparent_power_rating`, `storage_capacity`, `storage_initial_soc`, `storage_device_ids`, `storage_device_id_is_explicit`, `storage_aging_weight`, `storage_delta`. When nondispatchable is present, adds `nnd`, `Cnd`, `nd_bus`, `nd_apparent_power_rating`, and either `nd_p_available` (single-step) or `nd_available` (multistep). `storage_bus` and `nd_bus` always use formulation-internal indexing; singlenode therefore uses collapsed bus `0`. Detection: `"ns" in build.data` for storage; `"nnd" in build.data` for nondispatchable. Empty component lists are normally absent; explicit `loads=[]` is the deliberate exception and publishes a complete zero-load schema. |
| `formulation` | str | `"ac"`, `"lossy_dc"`, or `"singlenode_dc"` |
| `is_convex` | bool | Drives solver defaults in `solve()` |

### `StorageUnitIdeal` fields

| Field | Type | Default | Description |
|---|---|---|---|
| `bus` | int | required | External (MATPOWER) bus ID |
| `apparent_power_rating` | float | required | S_max (MVA); AC: circle constraint; DC: real power bound |
| `capacity` | float | required | Energy capacity Q (MWh) |
| `initial_soc` | float | required | Initial state of charge (MWh); 0 ≤ initial_soc ≤ capacity |
| `aging_weight` | float | 1e-2 | L1 cycling penalty weight λ (objective units/MWh); 0.0 = zero-cost storage |
| `device_id` | str or None | None | Stable cross-build identity when supplied. Omitted IDs receive collision-safe build-local positional labels and are marked non-explicit in metadata. |

`delta` (hours per time step) is **not** a field on `StorageUnitIdeal`. It is a
global problem parameter passed to `build_opf` / `build_opf_multistep` (default 1.0).

### `NondispatchableUnit` fields

| Field | Type | Default | Description |
|---|---|---|---|
| `bus` | int | required | External (MATPOWER) bus ID |
| `p_available` | float | required | Available real power (MW); >= 0. Used directly in single-step. In multistep, serves as a constant fallback if `df_nd` is not provided. |
| `apparent_power_rating` | float | required | S_max (MVA); inverter nameplate rating. AC: radius of apparent-power circle. DC: explicit real-power upper bound, separate from availability. Must be > 0. |
| `device_id` | str or None | None | Stable external identity. Required only when `df_nd` is supplied. |

`df_nd` (available power time series) is **not** a field on `NondispatchableUnit`.
It is a separate parameter on `build_opf_multistep`, with shape `(T, nnd)` and
columns that exactly match unique, nonempty unit `device_id` values. Column
order is arbitrary and is aligned to device-list order. If `nondispatchable`
is not None but `df_nd` is None, `p_available` is tiled across all T steps and
a `UserWarning` is emitted; static fallback does not require IDs.

### `Load` fields and multistep input modes

`Load` is a first-class fixed active/reactive demand channel with required
external `bus`, signed `p_load_mw`, and unique nonempty `device_id` fields.
`q_load_mvar=None` denotes an undefined reactive channel and is reported as
numerical zero while remaining distinguishable in metadata. A finite positive
`shedding_cost_per_mwh` activates a builder-owned interruption fraction bounded
by `max_shed_fraction` and the current positive-demand eligibility mask.

For multistep builds, `loads=None` selects the legacy MATPOWER-compatible mode
and requires positional `df_P` (plus `df_Q` for AC). Supplying `loads`, including
`loads=[]`, selects explicit-load mode: legacy frames are rejected, and
`df_load_p`/`df_load_q` columns must exactly match `Load.device_id` values.
Omitted explicit trajectories use tiled static device values. DC formulations
retain explicit reactive trajectories for reporting, emit a warning, and do
not use reactive power in optimization.

---

## Module responsibilities

`problem.py` owns the public OPF build boundary, while `hierarchical.py` owns
the public hierarchical-control boundary. Package-level exports in
`cvxopf.__init__` provide convenience imports. Formulation modules must remain
independent of one another. The principal dependency direction is:

```
problem.py    →  storage.py              (StorageUnitIdeal, re-exported)
problem.py    →  nondispatchable.py      (NondispatchableUnit, re-exported)
problem.py    →  generator.py            (DispatchableGenerator, case normalization)
problem.py    →  load.py                 (Load, MATPOWER conversion, time-series preparation)
problem.py    →  ac_problem.py           (deferred, inside functions)
problem.py    →  dc_problem.py           (deferred, inside functions)
problem.py    →  singlenode_dc_problem.py (deferred, inside functions)
formulation builders → _component_adapters.py (central component registry)
_component_adapters.py → component modules (typed bindings to owned models)
formulation builders → _component_assembly.py (generic contribution assembly)
ac_problem.py →  network.py, data.py
dc_problem.py →  network.py, data.py
generator.py  →  cost.py                (authoritative polynomial/PWL expressions)
hierarchical.py → problem.py            (reviewed outer/inner build API)
results.py    →  problem.py             (OPFBuild type boundary)
storage.py    →  cvxpy, numpy           (no other cvxopf imports)
nondispatchable.py → data.py, cvxpy, numpy
hvdc.py       →  data.py, cvxpy, numpy
load.py       →  cvxpy, numpy
```

`ac_problem.py` must not import from `dc_problem.py` and vice versa.

All five public device families—dispatchable generation, storage,
nondispatchable generation, HVDC, and loads—follow the typed M16+ component
pattern. See `plans/milestone-16-plus-component-adapters.md`.

See [`PROJECT_FLOWCHART.md`](PROJECT_FLOWCHART.md) for the as-built
problem-construction architecture, component lifecycle, ownership boundaries,
and Milestone 17 scope boundary.

### Extending components and formulations

The adapter layer is a private, closed-world repository architecture, not a
third-party plugin API. Once a component collection reaches the centralized
registry, every formulation builder consumes its mathematical contributions
generically. Adding a new public component still requires ordinary API
plumbing through `problem.py` and the formulation parser signatures so that
the collection can reach that registry.

To add a repository-supported component:

1. Put its authoritative data model, validation, injections, feasible set,
   temporal coupling, and costs in its component module.
2. Bind those functions to a `ComponentAdapter` in
   `_component_adapters.py`. Declare each formulation as `ACTIVE`, `NULL`, or
   `UNSUPPORTED`; an active binding supplies variable specifications,
   injections, operating constraints, and a horizon hook, even when that
   horizon hook returns an empty contribution.
3. Thread its public collection and any time-series inputs through
   `problem.py` and the formulation parser signatures, then register the
   collection in `component_requests()`. Do not add component-specific
   construction, constraints, injections, or costs to any formulation
   builder.
4. Extend result extraction only for new public result fields. The shared
   assembler publishes component expressions automatically beside the
   formulation-owned compatibility fields.

The existing formulation-local named expressions are a compatibility path
that preserves the established `OPFBuild.expressions` schema. New components
should contribute per-step expressions through the adapter hook and horizon
expressions through `HorizonContribution`; shared publication places both in
`OPFBuild.expressions`. Single-step expressions remain scalar expressions,
multistep expressions become ordered lists, and horizon expressions are
published once without time scaling. Migration of existing compatibility
fields is separate work and must preserve the public schema and numerical
behavior exactly.

Component variables remain builder-owned: adapters return `VariableSpec`
objects and never construct `cp.Variable`. Engineering-unit nodal injections
use a component-created, unbound inverse-base parameter; the shared assembler
alone binds it to `1 / baseMVA`. AC bindings may return both real and reactive
channels, while DC bindings return real power with reactive power represented
by `None`, never scalar zero. Component variable, metadata, step-expression,
and horizon-expression names share flattened public namespaces with
formulation-owned fields; duplicate names are rejected rather than
overwritten.

To add a formulation, implement its single- and multistep network builders,
register them in `problem.py`, and add its result extractor. The builders own
network variables, physics, balances, and formulation-specific loss terms,
but must obtain device requests from `component_requests()` and consume only
the generic step and horizon aggregates. Add the new formulation capability
to every component adapter explicitly; use `NULL` only when eliminating the
component is the intended physical model, and `UNSUPPORTED` when a supplied
component must be rejected.

Generator polynomial costs are limited to degree two; use the shared
piecewise-linear representation for more general convex cost curves. External
device time-series identity alignment occurs once at the public `problem.py`
boundary. `OPFBuild.expressions` carries modeled expressions needed by result
reporting so objective terms are not reimplemented in `results.py`.

---

## Working with DCP in CVXPY

Disciplined Convex Programming (DCP) is the ruleset CVXPY uses to certify a
problem is convex. The convex formulations here (`lossy_dc`, `singlenode_dc`,
future `socp`) are DCP-valid end to end. The `ac` formulation bypasses the
whole-problem DCP check with `nlp=True` and uses DNLP via IPOPT — but this
bypass exists for **one reason only** (see the boundary invariant below).

### The device/network DCP boundary (load-bearing invariant)

**Every device model must be DCP-valid in every formulation, including AC.**
DNLP is invoked *only* for the network physics — the nonconvex power-flow
equations in the full AC-OPF (the `cp.nlp.cos`/`cp.nlp.sin` trig relations in
Section 2 of `_make_step_constraints` that link `P`/`Q` to `theta`/`v`). That
is the sole place DNLP rules apply.

Every device contribution — operating constraints, horizon-level temporal
constraints, injection terms, and cost expressions for generators, storage,
nondispatchable units, HVDC, and loads — must pass the ordinary DCP rules on
their own. No device model may rely on DNLP. Temporal constraints include state
transitions and temporal boundary conditions; keep those categories distinct
inside the device implementation.

Why this invariant matters:

- **Devices compose into any formulation unchanged.** Because a device model is
  DCP, the same operating-constraint / injection / cost methods plug into AC,
  lossy DC, singlenode, and future SOCP without a DNLP variant. This is exactly
  what makes the Milestone 16 "model a component once, plug into any network"
  contract possible.
- **Agents never need to understand DNLP.** DNLP knowledge is confined to
  `ac_problem.py` Section 2. Anyone writing or reviewing a device model only
  needs the DCP rules below. (Do not change Section 2's DNLP flow definitions
  without understanding the paper — already a hard rule in "What not to do".)
- **SOCP (Milestone 11) reuses the device layer.** SOCP is a convex relaxation
  whose *network* physics are themselves DCP (second-order cone constraints on
  lifted variables — no DNLP bypass anywhere), making it the first fully-DCP
  network formulation. Because every device is already DCP, the SOCP
  constructor can compose the existing DCP device contributions. The new work
  remains substantial—network variables, cone constraints, audits, results,
  and explicit component capability declarations—but does not require a second
  implementation of every device model.

When you add or edit a device model, assert `is_dcp()` on its constraints and
cost **directly** (per-object checks below) — a device term that only passes
inside the AC problem because IPOPT ignores DCP is a latent bug: it will fail
the moment the same device is used in a convex formulation.

**The key fact for writing and debugging code: DCP attributes can be inspected
on any expression, constraint, or objective individually — not just on the
whole problem.** When a convex build fails its DCP check, do not only call
`prob.is_dcp()`; localise the violation by checking the offending piece
directly.

Per-object checks:

```python
expr.is_dcp()          # is this expression DCP?
expr.curvature         # 'CONSTANT' | 'AFFINE' | 'CONVEX' | 'CONCAVE' | 'UNKNOWN'
expr.sign              # 'NONNEGATIVE' | 'NONPOSITIVE' | 'ZERO' | 'UNKNOWN'
expr.is_convex()       # curvature-specific predicates
expr.is_concave()
expr.is_affine()
constraint.is_dcp()    # is this single constraint DCP?
objective.is_dcp()     # is Minimize(...)/Maximize(...) DCP?
prob.is_dcp()          # whole-problem check
```

DCP rules in brief:

- **Objective** must be `Minimize(convex)` or `Maximize(concave)`.
- **Constraints** may only be `affine == affine`, `convex <= concave`, or
  `concave >= convex`. An equality between non-affine expressions is never DCP
  (this is why the HVDC loss coupling must use an affine branch, never
  `abs`-in-equality — see the HVDC notes).
- Curvature and sign are computed compositionally and are **always correct but
  conservative**: an expression that is mathematically convex may still be
  flagged `UNKNOWN` if the DCP rules cannot verify it. The fix is to rewrite it
  in a DCP-verifiable form (see the CVXPY DCP docs for the standard rewrites;
  the project-specific instance is the explicit-monomial-sum vs. Horner's-method
  `poly_cost_expr` note under Units).
- `expr1 * expr2`, `expr1 / expr2`, `expr1 @ expr2` are DCP only when one side
  is constant.

**In tests and troubleshooting:** assert `expr.is_convex()` /
`constraint.is_dcp()` on the specific term you built, not just the assembled
problem. This pinpoints which component's constraint or cost broke DCP and
keeps a passing convex formulation from silently regressing. A whole-problem
`prob.is_dcp()` assertion is a good coarse gate, but the per-object checks are
what make a DCP regression debuggable.

---

## Key design decisions

### Bus indexing
All internal computation uses 0-based consecutive bus indices.
`reindex_case_to_consecutive` in `network.py` handles remapping.
The `ext_to_int` mapping is stored in `OPFBuild.data`.
MATPOWER test cases use 1-based bus IDs; reindexing is always applied.

### Units
Variable units are **not** uniform across all CVXPY variable types:

- **Conventional generator and power flow variables** (`Pg`, `Qg`, `p_flows`,
  `p`, `q`) are in **per-unit** internally (divided by `baseMVA`) and scaled
  to engineering units (MW, MVAr) in `extract_results`.
- **Storage variables** (`b`, `b_q`, `soc`), **nondispatchable variables**
  (`p_nd`, `q_nd`), **HVDC variables** (`p_hvdc_in`, `p_hvdc_out`), and load
  power parameters and expressions are in **engineering units** internally
  (MW, MVAr, MWh). The load interruption-fraction variable is dimensionless.
  They are **not** divided by `baseMVA` at declaration and are **not**
  multiplied by `baseMVA` in `extract_results`. They enter the nodal balance
  divided by `baseMVA` at the point of constraint construction — that division
  is the only place `baseMVA` appears for these variables. **Do not** divide
  them by `baseMVA` at declaration or inside constraint loops, and **do not**
  multiply them by `baseMVA` in `extract_results` — both are latent unit bugs.
- Generator cost expressions receive `Pg` in **MW** — the `baseMVA` scaling
  is applied before building cost-rate expressions in both AC and DC.
- The objective convention is
  `delta * sum_t(stage_cost_rate_t) + terminal_cost`. Generator, storage
  cycling, HVDC, and lossy-DC regularization terms are rates; shared assembly
  owns their time integration. Named integrated costs are retained in
  `OPFBuild.expressions` for auditing.
- `poly_cost_expr` in `cost.py` uses an explicit monomial sum (not Horner's
  method) so that CVXPY's DCP checker can verify convexity for quadratic costs.
  Horner's method produces `(affine * affine)` products when leading coefficients
  are zero, which CVXPY rejects as not DCP even though the polynomial is convex.
  This matters for the DC formulation; AC bypasses DCP via DNLP/IPOPT.

### Multi-step structure
`build_opf_multistep` builds a **single `cp.Problem`** containing `T` sets
of per-step variables and constraints. The objective integrates per-step cost
rates using the global interval duration `delta`, then adds horizon-boundary
costs once. Component-owned horizon hooks contribute temporal dynamics and
terminal policies. The optional `coupling_constraints` argument carries
additional caller-supplied constraints and is appended without modification.

### Incidence matrices
There are two distinct incidence matrices in `network.py`:

- `make_incidence_matrix(case)` — generator-to-bus matrix `Cg`, shape
  `(nb, ng)`. Used in both AC and DC to link generator variables to buses.
- `make_branch_node_incidence_matrix(case)` — branch-node matrix `A`,
  shape `(nb, nl)`. Used in DC for flow conservation
  `A @ p_flows + Cg @ Pg = Pd`.

Do not confuse them. See the module-level comment in `network.py`.

A third incidence matrix `Cnd`, shape `(nb, nnd)`, maps nondispatchable units
to buses. It is constructed by `_make_nd_incidence_matrix` in
`nondispatchable.py` and stored in `build.data["Cnd"]`. A fourth, `Cs`, shape
`(nb, ns)`, maps storage units to buses. Both follow the same structure as `Cg`.

### Storage units

`StorageUnitIdeal` lives in `storage.py` with zero cvxopf imports (both
`ac_problem.py` and `dc_problem.py` import from it, so a cvxopf import here
would risk a cycle) and is re-exported from `problem.py` for the public API.

`delta` (time step duration, hours) is a global problem parameter on
`build_opf` / `build_opf_multistep`, not a field on `StorageUnitIdeal`.
It applies uniformly to all storage units in a given problem and is validated
at the public problem boundary before formulation dispatch.

The aging cost uses `cp.multiply(aging_weight, cp.abs(b_t))` — never
`numpy_array * cp.abs(cp_var)` or `np.multiply(...)`. NumPy intercepts `*`
via `__array_ufunc__` and routes through CVXPY's deprecated matrix
multiplication path, causing `CvxpyDeprecationWarning`.
This expression is a stage-cost rate; shared assembly multiplies its horizon
sum by `delta`, so `aging_weight` has objective units/MWh of throughput.

The AC network builder owns reference-angle constraints, nonlinear branch-flow
definitions, nodal balance, voltage limits, and branch operating limits.
Device operating sets and injections reach those balances through shared
component assembly; do not reconstruct device constraints inside the network
builder.

Storage keys are absent from `build.data` when `storage=None`; the detection
contract is `"ns" in build.data`.

### Nondispatchable units

`NondispatchableUnit` lives in `nondispatchable.py`, which imports the shared
device-frame alignment helper from `data.py`.
`NondispatchableUnit` is re-exported from `problem.py` for the public API.

Nondispatchable units have no cost, no aging weight, no SoC dynamics, and
no coupling constraints across time steps. The only cross-step structure is
the time-varying available power `R_t[n]`, which is supplied via `df_nd`.

In multistep, `df_nd` columns are stable device IDs rather than bus IDs. This
permits multiple colocated units and prevents an external table from silently
changing meaning when the device list is reordered. The same identity contract
is used independently by `df_hvdc_min` and `df_hvdc_max`.

Nondispatchable keys are absent from `build.data` when `nondispatchable=None`;
the detection contract is `"nnd" in build.data`.

`"nd_p_available"` (shape `(nnd,)`) and `"nd_available"` (shape `(T, nnd)`)
are mutually exclusive in `build.data`: single-step builds populate the former,
multistep builds populate the latter. Code reading either key must check which
is present.

---

## Milestones

| Milestone | Status | Notes |
|---|---|---|
| 0 — Repository skeleton | ✅ Complete | |
| 1 — Port and modularize working code | ✅ Complete | |
| 2 — Pypower fixture generation and validation | ✅ Complete | |
| 3 — Multi-step problem builder | ✅ Complete | |
| 4 — AC branch terminal flows and limits | ✅ Complete | Exact signed terminal-flow reporting in MATPOWER row order; positive finite `rateA` enforced as an apparent-power limit at both terminals by default. See `plans/milestone-4-branch-limits.md`. |
| 5 — Battery/storage model hook | ✅ Complete | `StorageUnitIdeal`; `storage=` and `delta=` on `build_opf` / `build_opf_multistep`. AC apparent-power circle, DC real-power box; SoC cross-step coupling; L1 aging cost. See `plans/milestone-5-storage.md`. |
| 6 — Lossy DC OPF and multi-formulation architecture | ✅ Complete | |
| 7 — HVDC transmission links | ✅ Complete | `HVDCLink`; `hvdc=` on `build_opf` / `build_opf_multistep`, `df_hvdc_min=`/`df_hvdc_max=` on multistep; `hvdc_from_dcline` MATPOWER importer. Signed nodal injections (Convention B), proportional loss on fixed-direction links; applies to `ac` and `lossy_dc`, with an explicit null capability in `singlenode_dc` because network collapse eliminates both terminals. Gate 6b is consistency-based, not a Pypower value-match. `LOSS0`/reactive/voltage-control deferred to M15. See `plans/milestone-7-hvdc.md` (incl. the `dcline` column map and MVP-vs-M15 subtable) and `experiments/dnlp_vs_pypower/`. |
| 8 — Nondispatchable generators | ✅ Complete | `NondispatchableUnit`; `nondispatchable=` and `df_nd=` on `build_opf` / `build_opf_multistep`. AC circle ∩ `0≤p_nd≤R_t`; DC retains separate availability and apparent-power-rating bounds; no cost/curtailment penalty. See `plans/milestone-8-nondispatchable.md`. |
| 9 — Sparse P/Q variables for AC-OPF | ✅ Complete | `OPFOptions.sparse_pq` (default `True`); flat `P_vec`/`Q_vec` over Ybus pattern with scatter matrix `Rp`. See `plans/milestone-9-sparse-pq.md`. |
| 10 — Single-node DC dispatch | ✅ Complete | `"singlenode_dc"` formulation; `make_singlenode_case` convenience constructor |
| 11 — SOCP (convex) network model | 🔲 Future | |
| 12 — Extend battery parameters: final SoC, penalty vs constraint | ✅ Complete | Storage-owned terminal equality or zero-shortfall constraints and linear/quadratic, one-/two-sided terminal costs, consistently composed across formulations. See `plans/milestone-12-storage-terminal-soc.md`. |
| 13 — Extend CVXPY parameterization for problem data | 🔲 Future | Faster repeated solves of the same graph over new data |
| 14 — Time-vectorized multistep formulations | 🟧 Next / blocking | Add an explicit time-last tensor assembly mode using SCIPY canonicalization alongside the retained stepwise/CPP path, preserving formulation, failure, audit, and result contracts while enabling direct profiling of both temporal representations. Vectorized lossy DC is the first delivery and blocks resumption of the Case118 annual S4 outer solve after macOS killed the repeated annual graph under extreme compressed-memory pressure. See `plans/milestone-14-time-vectorization.md`. |
| 15 — Full lossy HVDC (sign-switching converter losses) | 🔲 Future | charge/discharge-style split of `p_in`; adds fixed converter loss (`LOSS0`); enables losses in `free` and zero-straddling `band` steps; reactive-power support proposed. See `plans/milestone-15-full-lossy-hvdc.md`. |
| 16 — Unify grid component model patterns | ✅ Complete | Generators, storage, nondispatchable units, and HVDC share formulation-specific injection and operating-set APIs, temporal coupling slots, and device-owned cost boundaries. Includes first-class `DispatchableGenerator`, MATPOWER fallback, stable identity for external ND/HVDC tables, and collapsed singlenode reuse. See `plans/milestone-16-unify-components.md` and `memories/M16-in-flight-record.md`. |
| 17 — Hierarchical DC→AC receding-horizon dispatch | ✅ Complete | The capstone controller passes **identity-aligned SoC signposts only** (not other setpoints) from long-horizon `lossy_dc` planning into short AC-OPF windows, executes only residual-checked target-conditioned first actions, supports causal shifted initialization with audited recovery, and retains the complete plan/attempt tree. M17 fixes the validated `lossy_dc`→`ac` workflow; configurable formulations and additional layers are M21. See `plans/milestone-17-hierarchical-dc-ac.md`. |
| 18 — Convex lossy storage | 🔲 Future | Separate charge/discharge powers, asymmetric efficiency, and storage loss while retaining a convex primary model. Positive throughput regularization plus zero-cost renewable curtailment excludes simultaneous operation under stated assumptions; relax-round-polish remains an explicit fallback. See `plans/milestone-18-lossy-storage.md`. |
| 19 — First-class loads and explicit load shedding | ✅ Complete | Fixed active/reactive withdrawals use the shared device architecture, with MATPOWER conversion and identity-aligned explicit time series; configured loads add an affine served-fraction feasible set, proportional reactive relief, a sufficiently large linear value-of-lost-load cost, and conditional served/shed/ENS results in the same single solve. Controlled phase-transition, adequacy, AC/DC congestion, and multistep storage/renewable/terminal behavior are scientifically verified. No lexicographic or feasibility-restoration solve. See `plans/milestone-19-load-shedding.md`. |
| 20 — AC voltage and reactive-dispatch regularization | 🔲 Future | Characterize whether reactive/voltage bound activity reflects physical support, unpriced nonuniqueness, or local-solver selection. Then add optional, normalized, time-integrated AC operating preferences with exact disabled-policy compatibility and measured economic displacement. No voltage-stability, market-pricing, or global-uniqueness claim. See `plans/milestone-20-ac-voltage-reactive-regularization.md`. |
| 21 — Configurable and extensible formulation hierarchies | 🔲 Future | Generalize the completed M17 controller behind typed layer adapters and explicit, identity-aligned handoffs while preserving exact `lossy_dc`→`ac` compatibility. Support selectable planning formulations and validate a reference `singlenode_dc`→`socp`→`ac` hierarchy after M11 freezes SOCP relaxation and audit semantics. This remains a closed set of reviewed repository formulations, not an unrestricted plugin framework. See `plans/milestone-21-configurable-hierarchy.md`. |
| 22 — Nonconvex load-group penalties | 🔲 Future | Add identity-aligned interactions among groups of sheddable loads, beginning with mutually exclusive customer-group shedding and soft bilinear joint-shedding penalties. Use convex-hull or McCormick relaxation, typed deterministic rounding, and fixed-policy physical polishing; validate with exact small references, congested lossy-DC cases, and AC realization. See `plans/milestone-22-nonconvex-load-group-penalties.md`. |

---

## Dependencies

### Runtime (installed with the package)
| Package | Constraint | Reason |
|---|---|---|
| `cvxpy` | `>=1.9` | DNLP interface (`cp.nlp.cos`, `cp.nlp.sin`) introduced in 1.9 |
| `numpy` | none | Array math, Ybus construction |
| `pandas` | none | Time-series load input |
| `cyipopt` | none | Python interface to IPOPT |

### System prerequisite (user must install manually)
IPOPT system library. Platform-specific instructions are in `README.md`.
On Linux, `liblapack-dev`, `libblas-dev`, and `gfortran` are also required
or `cyipopt` will fail to build with a linker error.

### Development extras
`pytest`, `pytest-cov` — installed via `pip install -e ".[dev]"` or
`uv run --extra dev`.

---

## Fixture generation

The Pypower reference fixtures in `tests/fixtures/` are static committed
files. They are **not** regenerated in CI. To regenerate them locally:

```bash
uv run scripts/generate_pypower_fixtures.py
```

This runs in an isolated sandbox with `pypower==5.1.19` and `numpy==2.2.6`.
The NumPy pin is required because PYPOWER uses `numpy.in1d`, which was removed
in NumPy 2.3. Do not run this script with the main package
environment.

Regenerate fixtures only if:

- A new test case is added to the package
- A suspected bug in an existing fixture needs to be ruled out

---

## Known acceptable discrepancies vs Pypower

case14 generator 3 (bus 6) and generator 4 (bus 8) Pg and Qg values may
appear as `~2e-9` in cvxopf where Pypower returns `0.00`. This is an
IPOPT interior-point solver artifact — the solver does not return exact
zeros at bounds. These are within the documented test tolerances and are
not bugs. They are noted in the `test_vs_pypower_reference.py` module
docstring.

---

## Fresh coding sessions

1. Read `CLAUDE.md` before touching code.
2. Inspect `git status --short` and preserve unrelated user changes.
3. Check `git log --oneline -10` and the relevant milestone or experiment
   plan to orient on recent work.
4. Run proportionate baseline verification before editing; use the full suite
   when the change or active stage requires it.

---

## What not to do

- Do not add `pypower` to `pyproject.toml` or any runtime dependency
- Do not call `build.prob.solve()` directly — use `build.solve()`
- Do not use `build_acopf` / `build_acopf_multistep` — deprecated; use
  `build_opf(..., formulation="ac")`
- Do not change the DNLP variable formulation without understanding the paper
- Do not regenerate fixture files in CI
- Do not pin `numpy` in `pyproject.toml` — the pin exists only in the fixture
  generation script
- Do not remove the `validate_case` call from `_parse_case` (`ac_problem.py`)
  or `_parse_dc_case` (`dc_problem.py`)
- Do not import `ac_problem` from `dc_problem` or vice versa
- Do not import a component data class (`StorageUnitIdeal`,
  `NondispatchableUnit`, `HVDCLink`) from `problem.py` inside `ac_problem.py`
  or `dc_problem.py` — import from its own module (`storage.py`,
  `nondispatchable.py`, `hvdc.py`) directly
- Do not set `nlp=True` for convex formulations (DC, singlenode, SOCP), nor
  `nlp=False` for the AC formulation
- Do not break the detection-by-presence contract: never add `ns=0`, `nnd=0`,
  or `n_hvdc=0` to `build.data` when the corresponding component is absent —
  detection is `"ns"`/`"nnd"`/`"n_hvdc" in build.data`
- Do not add a second `p ==` or `q ==` constraint after
  `_make_step_constraints` returns — it owns all balance constraints, including
  storage, nondispatchable, and HVDC injection terms
- Do not implement `StorageUnitLossy` without a separate plan — separate
  charge/discharge variables require structural changes to
  `_make_step_constraints`
- Do not enter the HVDC balance terms as `Ch_to - Ch_from` — both terminals
  are signed injections (Convention B) and enter with `+`
- Do not put `cp.abs` (or any non-affine atom) in the HVDC `p_out` loss
  equality — select an affine branch by the box's pre-construction sign instead
- Do not select a lossy loss branch for a zero-straddling box
  (`p_min_t < 0 < p_max_t`) — the lossy branch is valid only for a
  fixed-direction box (`p_min_t >= 0` or `p_max_t <= 0`)
- Do not treat HVDC as a live component in `singlenode_dc`. Its registered
  capability is explicitly `NULL` because collapsing both terminals removes
  the link from the physical model (no `"n_hvdc"` key and no warning).
- Do not skip the singlenode structural exceptions: `_parse_singlenode_dc_case`
  does not call `validate_case` (empty branch table by design), and
  `Pd_series` is shape `(T,)`, not `(T, nb)` — the formulation has no per-bus
  structure
