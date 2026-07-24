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

It is designed for power systems research, with a focus on extensibility to
multi-step optimization and energy storage models.

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

`src/cvxopf/`: `problem.py` (public API), `ac_problem.py` / `dc_problem.py` / `singlenode_dc_problem.py` (per-formulation builders), `network.py`, `cost.py`, `data.py`, `results.py`, and one module per grid component (`storage.py`, `nondispatchable.py`, `hvdc.py`, `generator.py`). `testcases/` holds MATPOWER cases (case9–case118, PWL and dcline variants). `tests/`, `examples/`, `notebooks/`, and `scripts/` are top-level. Run `find src tests examples -name '*.py'` for the current file list.

---

## Running tests

Always use `uv run` so the correct virtual environment and extras are used:

```bash
uv run --extra dev pytest tests/ -v
```

Expected result: all tests pass (baseline currently 816; run to confirm)

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
  proportional-loss coupling `p_out == -(1 - loss_frac) * p_in` on
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
| AC | `status`, `objective`, `Pg`, `Qg`, `Vm`, `Va_deg`, `p_net`, `q_net`; plus `p_hvdc_in`, `p_hvdc_out`, `hvdc_loss` (derived, `= p_hvdc_in + p_hvdc_out`, always >= 0) when `hvdc` is not None |
| Lossy DC | `status`, `objective`, `Pg`, `p_flows`, `p_net`; plus `p_hvdc_in`, `p_hvdc_out`, `hvdc_loss` when `hvdc` is not None. `Vm`, `Va_deg`, `Qg`, `q_net` absent |
| Single‑node DC | `status`, `objective`, `Pg`, `p_net`. `p_flows`, `Vm`, `Va_deg`, `Qg`, `q_net` absent |

Code consuming results from more than one formulation should use
`results.get('Vm')` rather than `results['Vm']` — DC and single‑node omit
the AC‑only keys.

Do not change this formulation without understanding the DNLP paper.

### `"lossy_dc"` — Lossy DC OPF (convex QP)

Reference: *Convex Optimization with Smart Grid Examples*,
https://doi.org/10.2172/3018252

Objective: minimize `G + loss_weight * L`
- `G = sum_k (c0_k + c1_k * Pg_k + c2_k * Pg_k^2)` — generation cost
- `L = sum_e r_e * p_flows_e^2` — line losses
- `loss_weight` is user-configurable via `OPFOptions.loss_weight` (default 1.0)

Constraints:
- `A @ p_flows + p_gen == Pd` — flow conservation at every bus
- `|p_flows[e]| <= f_max[e]` — branch flow limits
- Generator output bounds

Variables: `p_flows`, `p_gen`

**Device models in DC** – No reactive term (`b_q`, `q_nd` absent). Storage uses a real‑power bound `|b_t| ≤ S_max` (emits a `UserWarning`). Nondispatchable units have only the real‑power bound `0 ≤ p_nd_t ≤ R_t` (apparent rating stored but not enforced). HVDC model is identical to AC (box bounds plus proportional‑loss coupling). Results omit `Vm`, `Va_deg`, `Qg`, `q_net` (see the results-key table under `"ac"`).

There is no Pypower oracle for DC validation. Correctness is verified via
internal consistency checks: flow conservation, bound feasibility, T=1
equivalence with single-step.

### `"singlenode_dc"` — Single-node DC dispatch (convex QP)

Collapses the entire network to a single bus. No branch flows, no
transmission constraints, no line losses, no reactive power. Enforces
scalar real power balance:

    sum(Pg) + (1/baseMVA)*sum(b) + (1/baseMVA)*sum(p_nd) == Pd_total

where Pd_total = sum(bus[:, PD]) / baseMVA.

Objective: minimize generation cost G (same polynomial cost as AC and
lossy DC) plus storage aging cost when storage is present.

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
| `"fast_decoupled"` | Fast-decoupled AC (convex) |
| `"socp"` | SOCP relaxation (convex) |

To add a new formulation: implement `_build_<name>_single` and
`_build_<name>_multistep` in a new `src/cvxopf/<name>_problem.py`,
add them to `_get_single_builders()` and `_get_multistep_builders()`
in `problem.py`, and add `_extract_<name>_results` in `results.py`.

---

## Public API

### Entry points

```python
build_opf(case, *, formulation="ac", options=None,
          storage=None, delta=1.0,
          nondispatchable=None) -> OPFBuild

build_opf_multistep(case, df_P, df_Q, *, T, formulation="ac",
                    options=None, coupling_constraints=None,
                    storage=None, delta=1.0,
                    nondispatchable=None, df_nd=None) -> OPFBuild
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
| `enforce_branch_limits` | bool | False | AC only (stub) |
| `loss_weight` | float | 1.0 | DC only |
| `branch_limit_sentinel` | float | 1e6 | DC only |
| `sparse_pq` | bool | True | AC only |

`delta` is not an `OPFOptions` field. It is a separate parameter on
`build_opf` and `build_opf_multistep`, only meaningful when `storage` is
not None. Validated (`delta > 0`) only when storage is present; silently
ignored otherwise.

### `OPFBuild` fields

| Field | Type | Description |
|---|---|---|
| `prob` | `cp.Problem` | The CVXPY problem |
| `variables` | dict | Named CVXPY variables. AC keys depend on `sparse_pq` (`P_vec`/`Q_vec` or `P`/`Q`). When `storage` is not None, adds `b`, `b_q` (AC only), `soc` as `cp.Variable (ns,)` single-step or `list[cp.Variable]` multistep. When `nondispatchable` is not None, adds `p_nd`, `q_nd` (AC only) as `cp.Variable (nnd,)` single-step or `list[cp.Variable]` multistep. All storage keys absent when `storage=None`; all ND keys absent when `nondispatchable=None`. |
| `data` | dict | Pre-computed numpy arrays and metadata. When storage is present, adds `ns`, `Cs`, `storage_bus`, `storage_apparent_power_rating`, `storage_capacity`, `storage_initial_soc`, `storage_aging_weight`, `storage_delta`. When nondispatchable is present, adds `nnd`, `Cnd`, `nd_bus`, `nd_apparent_power_rating`, and either `nd_p_available` (single-step) or `nd_available` (multistep). Detection: `"ns" in build.data` for storage; `"nnd" in build.data` for nondispatchable. |
| `formulation` | str | `"ac"` or `"lossy_dc"` |
| `is_convex` | bool | Drives solver defaults in `solve()` |

### `StorageUnitIdeal` fields

| Field | Type | Default | Description |
|---|---|---|---|
| `bus` | int | required | External (MATPOWER) bus ID |
| `apparent_power_rating` | float | required | S_max (MVA); AC: circle constraint; DC: real power bound |
| `capacity` | float | required | Energy capacity Q (MWh) |
| `initial_soc` | float | required | Initial state of charge (MWh); 0 ≤ initial_soc ≤ capacity |
| `aging_weight` | float | 1e-2 | L1 cycling penalty weight λ ($/MW); 0.0 = zero-cost storage |

`delta` (hours per time step) is **not** a field on `StorageUnitIdeal`. It is a
global problem parameter passed to `build_opf` / `build_opf_multistep` (default 1.0).

### `NondispatchableUnit` fields

| Field | Type | Default | Description |
|---|---|---|---|
| `bus` | int | required | External (MATPOWER) bus ID |
| `p_available` | float | required | Available real power (MW); >= 0. Used directly in single-step. In multistep, serves as a constant fallback if `df_nd` is not provided. |
| `apparent_power_rating` | float | required | P_max (MVA); inverter nameplate rating. AC: radius of apparent power circle. DC: stored but not used as a constraint. Must be > 0. |

`df_nd` (available power time series) is **not** a field on `NondispatchableUnit`.
It is a separate parameter on `build_opf_multistep`, with shape `(T, nnd)` and
column names equal to external bus IDs. If `nondispatchable` is not None but
`df_nd` is None, `p_available` is tiled across all T steps and a `UserWarning`
is emitted.

---

## Module responsibilities

`problem.py` is the **only** public-facing module. It imports from
`ac_problem.py` and `dc_problem.py` inside functions (not at module level)
to avoid circular imports. The import chain is:

```
problem.py    →  storage.py              (StorageUnitIdeal, re-exported)
problem.py    →  nondispatchable.py      (NondispatchableUnit, re-exported)
problem.py    →  ac_problem.py           (deferred, inside functions)
problem.py    →  dc_problem.py           (deferred, inside functions)
ac_problem.py →  storage.py             (StorageUnitIdeal, _validate_storage,
                                          _make_storage_incidence_matrix,
                                          _make_storage_soc_constraints)
ac_problem.py →  nondispatchable.py     (NondispatchableUnit,
                                          _validate_nondispatchable,
                                          _make_nd_incidence_matrix,
                                          _parse_nd_timeseries)
ac_problem.py →  network.py, cost.py, data.py   (unchanged)
dc_problem.py →  storage.py             (same as ac_problem.py)
dc_problem.py →  nondispatchable.py     (same as ac_problem.py)
dc_problem.py →  network.py, cost.py, data.py   (unchanged)
results.py    →  problem.py             (OPFBuild type only, unchanged)
storage.py    →  numpy only             (no other cvxopf imports)
nondispatchable.py → numpy only         (no other cvxopf imports)
```

`ac_problem.py` must not import from `dc_problem.py` and vice versa.

The `storage.py → numpy only` / `nondispatchable.py → numpy only` lines above
describe the **current** code and are left as-is until the Milestone 16
component refactor lands (which will give those modules a `cvxpy` import); M16
is the aspirational forward pattern, not a description of today's modules. See
`plans/milestone-16-unify-components.md`.

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

Every device contribution — operating constraints, cross-step coupling
constraints, injection terms, and cost expressions for generators, storage,
nondispatchable units, and HVDC — must pass the ordinary DCP rules on its own.
No device model may rely on DNLP.

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
- **SOCP (Milestone 11) integrates for free.** SOCP is a convex relaxation
  whose *network* physics are themselves DCP (second-order cone constraints on
  lifted variables — no DNLP bypass anywhere), making it the first fully-DCP
  network formulation. Because every device is already DCP, the SOCP
  constructor composes the existing device methods unchanged; the only new work
  is the cone network model plus a `socp_operating_constraints` fork for the
  (few, if any) components whose feasible region differs in the lifted space.
  Getting the M16 component contract right pre-pays SOCP's integration cost.

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
  (`p_nd`, `q_nd`), and **HVDC variables** (`p_hvdc_in`, `p_hvdc_out`) are in
  **engineering units** internally (MW, MVAr, MWh).
  They are **not** divided by `baseMVA` at declaration and are **not**
  multiplied by `baseMVA` in `extract_results`. They enter the nodal balance
  divided by `baseMVA` at the point of constraint construction — that division
  is the only place `baseMVA` appears for these variables. **Do not** divide
  them by `baseMVA` at declaration or inside constraint loops, and **do not**
  multiply them by `baseMVA` in `extract_results` — both are latent unit bugs.
- Generator cost expressions receive `Pg` in **MW** — the `baseMVA` scaling
  is applied before building cost expressions in both AC and DC.
- `poly_cost_expr` in `cost.py` uses an explicit monomial sum (not Horner's
  method) so that CVXPY's DCP checker can verify convexity for quadratic costs.
  Horner's method produces `(affine * affine)` products when leading coefficients
  are zero, which CVXPY rejects as not DCP even though the polynomial is convex.
  This matters for the DC formulation; AC bypasses DCP via DNLP/IPOPT.

### Multi-step structure
`build_opf_multistep` builds a **single `cp.Problem`** containing T sets
of per-step variables and constraints. The objective is the sum of per-step
costs. Coupling constraints (e.g., battery SoC dynamics) are passed via
`coupling_constraints` and appended without modification.

### Incidence matrices
There are two distinct incidence matrices in `network.py`:

- `make_incidence_matrix(case)` — generator-to-bus matrix `Cg`, shape
  `(nb, ng)`. Used in both AC and DC to link generator variables to buses.
- `make_branch_node_incidence_matrix(case)` — branch-node matrix `A`,
  shape `(nb, nl)`. Used in DC for flow conservation `A @ p_flows + p_gen = Pd`.

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
It applies uniformly to all storage units in a given problem.

The aging cost uses `cp.multiply(aging_weight, cp.abs(b_t))` — never
`numpy_array * cp.abs(cp_var)` or `np.multiply(...)`. NumPy intercepts `*`
via `__array_ufunc__` and routes through CVXPY's deprecated matrix
multiplication path, causing `CvxpyDeprecationWarning`.

`_make_step_constraints` (AC) is organised into five labelled sections in
fixed order, with Section 4b added for nondispatchable constraints:
  1. Reference bus angle fix
  2. Power flow definitions
  3. Nodal power balance (exactly one `p ==` and one `q ==` constraint;
     all injection terms — storage and nondispatchable — combined here)
  4. Storage operating constraints
  4b. Nondispatchable operating constraints
  5. Voltage setpoint pinning

This function owns all balance constraints (Section 3 emits exactly one
`p ==` and one `q ==`).

Storage keys are absent from `build.data` when `storage=None`; the detection
contract is `"ns" in build.data`.

### Nondispatchable units

`NondispatchableUnit` lives in `nondispatchable.py`, which has zero imports
from other cvxopf modules. Same circular-import reasoning as `storage.py`.
`NondispatchableUnit` is re-exported from `problem.py` for the public API.

Nondispatchable units have no cost, no aging weight, no SoC dynamics, and
no coupling constraints across time steps. The only cross-step structure is
the time-varying available power `R_t[n]`, which is supplied via `df_nd`.

In multistep, `df_nd` column names are external bus IDs (integers). This is
an intentional asymmetry with `df_P`/`df_Q` (which use positional indices) —
nondispatchable units are sparse across buses, so bus-ID-as-column is more
natural. This convention may be revisited in a future API release.

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
| 4 — Branch flow limits | 🔲 Stubbed | `OPFOptions.enforce_branch_limits=True` raises `NotImplementedError` in AC. See `plans/milestone-4-branch-limits.md` (placeholder). |
| 5 — Battery/storage model hook | ✅ Complete | `StorageUnitIdeal`; `storage=` and `delta=` on `build_opf` / `build_opf_multistep`. AC apparent-power circle, DC real-power box; SoC cross-step coupling; L1 aging cost. See `plans/milestone-5-storage.md`. |
| 6 — Lossy DC OPF and multi-formulation architecture | ✅ Complete | |
| 7 — HVDC transmission links | ✅ Complete | `HVDCLink`; `hvdc=` on `build_opf` / `build_opf_multistep`, `df_hvdc_min=`/`df_hvdc_max=` on multistep; `hvdc_from_dcline` MATPOWER importer. Signed nodal injections (Convention B), proportional loss on fixed-direction links; applies to `ac` and `lossy_dc`, silently dropped by `singlenode_dc`. Gate 6b is consistency-based, not a Pypower value-match. `LOSS0`/reactive/voltage-control deferred to M15. See `plans/milestone-7-hvdc.md` (incl. the `dcline` column map and MVP-vs-M15 subtable) and `experiments/dnlp_vs_pypower/`. |
| 8 — Nondispatchable generators | ✅ Complete | `NondispatchableUnit`; `nondispatchable=` and `df_nd=` on `build_opf` / `build_opf_multistep`. AC circle ∩ `0≤p_nd≤R_t`; DC real-power bound only; no cost/curtailment penalty. See `plans/milestone-8-nondispatchable.md`. |
| 9 — Sparse P/Q variables for AC-OPF | ✅ Complete | `OPFOptions.sparse_pq` (default `True`); flat `P_vec`/`Q_vec` over Ybus pattern with scatter matrix `Rp`. See `plans/milestone-9-sparse-pq.md`. |
| 10 — Single-node DC dispatch | ✅ Complete | `"singlenode_dc"` formulation; `make_singlenode_case` convenience constructor |
| 11 — SOCP (convex) network model | 🔲 Future | |
| 12 — Extend battery parameters: final SoC, penalty vs constraint | 🔲 Future | |
| 13 — Implement cvxpy parameters for problem data | 🔲 Future | Faster resolves of same problem over new data |
| 14 — Vectorize time constraints | 🔲 Future | currently built with iterative loop |
| 15 — Full lossy HVDC (sign-switching converter losses) | 🔲 Future | charge/discharge-style split of `p_in`; adds fixed converter loss (`LOSS0`); enables losses in `free` and zero-straddling `band` steps; reactive-power support proposed. See `plans/milestone-15-full-lossy-hvdc.md`. |
| 16 — Unify grid component model patterns | 🟡 In progress | Investigation complete and additive `DispatchableGenerator` module landed; constructor integration is next, followed by storage and nondispatchable. HVDC (M7) is the reference implementation. See `plans/milestone-16-unify-components.md` and `memories/M16-in-flight-record.md`. |
| 17 — Hierarchical DC→AC receding-horizon dispatch | 🔲 Future | The capstone: long-horizon `lossy_dc` plan passes **SoC signposts only** (not other setpoints) into the terminal cost/constraint of a short 3–5 step AC-OPF, slid forward as a receding horizon. The true implementation of the project vision. Depends on M16 (shared components) and M12 (terminal-SoC hard/soft machinery). See `plans/milestone-17-hierarchical-dc-ac.md`. |

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
The numpy pin is required because pypower uses `numpy.in1d` which was
removed in numpy 2.0. Do not run this script with the main package
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

1. Read `CLAUDE.md` (this document) before touching code
2. Run `uv run --extra dev pytest tests/` first to confirm baseline
3. Check `git log --oneline -10` to orient on recent work

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
- Do not forward `hvdc`/`df_hvdc_min`/`df_hvdc_max` to `singlenode_dc` as a
  live component — the singlenode builders accept and silently drop them
  (no `"n_hvdc"` key, no `UserWarning`)
- Do not skip the singlenode structural exceptions: `_parse_singlenode_dc_case`
  does not call `validate_case` (empty branch table by design), and
  `Pd_series` is shape `(T,)`, not `(T, nb)` — the formulation has no per-bus
  structure
