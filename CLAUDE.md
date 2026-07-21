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

Results keys: `status`, `objective`, `Pg`, `Qg`, `Vm`, `Va_deg`,
`p_net`, `q_net`; plus `p_hvdc_in`, `p_hvdc_out`, `hvdc_loss` (derived,
`= p_hvdc_in + p_hvdc_out`, always >= 0) when `hvdc` is not None.

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

**Storage variables** (present only when `storage` is not None):
- `b` — real power (ns,) MW, positive = discharging
- `b_q` absent — DC has no reactive power
- `soc` — state of charge (ns,) MWh
- Operating set: `|b_t[s]| <= S_max[s]` (real power bound; UserWarning emitted)
- Nodal balance modified: `A @ p_flows + p_gen + (1/baseMVA) * Cs @ b_t = Pd`

**Nondispatchable variables** (present only when `nondispatchable` is not None):
- `p_nd` — real power (nnd,) MW, non-negative, bounded above by available power
- `q_nd` absent — DC has no reactive power
- Operating set: `0 <= p_nd_t[n] <= R_t[n]` (available power upper bound only;
  apparent power rating is stored but not used as a constraint in DC)
- Nodal balance modified: `A @ p_flows + p_gen + (1/baseMVA) * Cs @ b_t + (1/baseMVA) * Cnd @ p_nd_t = Pd`
- Storage term absent when `storage=None`; ND term absent when `nondispatchable=None`

**HVDC variables** (present only when `hvdc` is not None):
- `p_hvdc_in`, `p_hvdc_out` — from/to signed nodal injections (n_hvdc,) MW,
  Convention B (positive = injection into the grid). Identical model to AC:
  box bound + proportional-loss coupling `p_out == -(1 - loss_frac) * p_in`
  on fixed-direction links.
- Flow conservation modified: `A @ p_flows + p_gen + ... + (1/baseMVA) * (Ch_from @ p_in + Ch_to @ p_out) = Pd`
  — both terminals enter with `+`.
- Optional polynomial cost per link (same as AC).
- HVDC term absent when `hvdc=None`

Results keys: `status`, `objective`, `Pg`, `p_flows`, `p_net`; plus
`p_hvdc_in`, `p_hvdc_out`, `hvdc_loss` when `hvdc` is not None.

Note: `Vm`, `Va_deg`, `Qg`, `q_net` are **absent** from DC results.
Code consuming results from either formulation should use
`results.get('Vm')` rather than `results['Vm']`.

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
p_nd when nondispatchable present.

Results keys: status, objective, Pg, p_net
(p_flows, Vm, Va_deg, Qg, q_net absent)

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
  in a DCP-verifiable form (e.g. `norm(hstack([1, x]), 2)` instead of
  `sqrt(1 + square(x))`; explicit monomial sums instead of Horner's method —
  see the `poly_cost_expr` note under Units).
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
- **Storage variables** (`b`, `b_q`, `soc`) and **nondispatchable variables**
  (`p_nd`, `q_nd`) are in **engineering units** internally (MW, MVAr, MWh).
  They are **not** divided by `baseMVA` at declaration and are **not**
  multiplied by `baseMVA` in `extract_results`. They enter the nodal balance
  divided by `baseMVA` at the point of constraint construction — that division
  is the only place `baseMVA` appears for these variables.
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

### Pypower is not a dependency
Never add pypower to `pyproject.toml`. See fixture generation below.

### Storage units

`StorageUnitIdeal` lives in `storage.py`, which has zero imports from other
cvxopf modules. This avoids circular imports since both `ac_problem.py` and
`dc_problem.py` import from it, and both are imported (deferred) by
`problem.py`. `StorageUnitIdeal` is re-exported from `problem.py` for the
public API.

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

Never add a second `p ==` or `q ==` constraint from outside this function.

Storage keys are absent from `build.data` when `storage=None`. The
detection contract is `"ns" in build.data`. Never add `ns=0` as a default.

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

Nondispatchable keys are absent from `build.data` when `nondispatchable=None`.
The detection contract is `"nnd" in build.data`. Never add `nnd=0` as a default.

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
| 4 — Branch flow limits | 🔲 Stubbed | `OPFOptions.enforce_branch_limits=True` raises `NotImplementedError` in AC |
| 5 — Battery/storage model hook | ✅ Complete | `StorageUnitIdeal`; `storage=` and `delta=` on `build_opf` / `build_opf_multistep` |
| 6 — Lossy DC OPF and multi-formulation architecture | ✅ Complete | |
| 7 — HVDC transmission links | ✅ Complete | `HVDCLink`; `hvdc=` on `build_opf` / `build_opf_multistep`, `df_hvdc_min=`/`df_hvdc_max=` on multistep; `hvdc_from_dcline` MATPOWER importer. Signed nodal injections (Convention B), proportional loss on fixed-direction links; applies to `ac` and `lossy_dc`, silently dropped by `singlenode_dc`. Gate 6b is consistency-based, not a Pypower value-match (see below + `plans/milestone-7-hvdc.md`). `LOSS0`/reactive/voltage-control deferred to M15. |
| 8 — Nondispatchable generators | ✅ Complete | `NondispatchableUnit`; `nondispatchable=` and `df_nd=` on `build_opf` / `build_opf_multistep` |
| 9 — Sparse P/Q variables for AC-OPF | ✅ Complete | `OPFOptions.sparse_pq`; default `True` |
| 10 — Single-node DC dispatch | ✅ Complete | `"singlenode_dc"` formulation; `make_singlenode_case` convenience constructor |
| 11 — SOCP (convex) network model | 🔲 Future | |
| 12 — Extend battery parameters: final SoC, penalty vs constraint | 🔲 Future | |
| 13 — Implement cvxpy parameters for problem data | 🔲 Future | Faster resolves of same problem over new data |
| 14 — Vectorize time constraints | 🔲 Future | currently built with iterative loop |
| 15 — Full lossy HVDC (sign-switching converter losses) | 🔲 Future | charge/discharge-style split of `p_in`; adds fixed converter loss (`LOSS0`); enables losses in `free` and zero-straddling `band` steps |
| 16 — Unify grid component model patterns | 🔲 Future | Refactor all grid components (dispatchable generators, storage, nondispatchable) into first-class component modules matching the HVDC pattern; components consumed by every formulation via composition. HVDC (M7) is the reference implementation. |
| 17 — Hierarchical DC→AC receding-horizon dispatch | 🔲 Future | The capstone: long-horizon `lossy_dc` plan passes **SoC signposts only** (not other setpoints) into the terminal cost/constraint of a short 3–5 step AC-OPF, slid forward as a receding horizon. The true implementation of the project vision. Depends on M16 (shared components) and M12 (terminal-SoC hard/soft machinery). |

### Milestone 4 — Branch flow limits (AC)
When implementing, add apparent power flow expressions derived from the
`P`, `Q` matrices and enforce per-branch `rateA` constraints. The stub
and `NotImplementedError` in `ac_problem.py` must be replaced. Add tests
that verify the constraint is binding when load is pushed high enough.

### Milestone 5 — Battery/storage model

`StorageUnitIdeal` in `src/cvxopf/storage.py`. Passed as `storage=` to
`build_opf` and `build_opf_multistep`. Time step duration `delta` (hours,
default 1.0) is a separate global parameter.

AC formulation uses an apparent power circle constraint
`b_t^2 + b_q_t^2 <= S_max^2`. DC formulation uses a real power bound
`|b_t| <= S_max` with a `UserWarning`. SoC dynamics are cross-step equality
constraints generated by `_make_storage_soc_constraints` after the time step
loop. Aging cost `lambda * sum_t |b_t|` follows Nnorom et al. (2026).

`StorageUnitLossy` (asymmetric charge/discharge efficiency) is deferred.

### Milestone 7 — HVDC transmission links
**Status: complete.** All steps (T0–T7) done: test artifacts, HVDC data
struct + validation + incidence, DC and AC formulation integration, results
extraction with the Gate 6b consistency test, public API re-exports
(`HVDCLink`, `hvdc_from_dcline`), runnable examples (`examples/case9_hvdc_ac.py`,
`examples/case9_hvdc_dc.py`), the Ybus-agreement test
(`TestHVDCYbusAgreement` — DC lines contribute nothing to Ybus), and docs.
See `plans/milestone-7-hvdc.md` and, for how the dcline fixture
oracle is built (pypower's `toggle_dcline` is unusable under numpy 2.x),
`scripts/README.md`.

**Gate 6b is consistency-based, not a Pypower value-match.** A solved cvxopf
`formulation="ac"` run on `case9_dcline()` does not reproduce the committed
Pypower fixture: the two land in different local optima of the (near-)same
nonconvex AC-OPF, and the fixture point is a *suboptimal* point of Pypower's
own feasible set (proven by the `case9_dcline` cross-eval, EX12). 6b therefore
asserts internal consistency (nodal balance ≈ 0, the proportional-loss law on
fixed-direction links, `hvdc_loss >= 0`, the `loss0` `UserWarning`). The
investigation is distilled and committed as the final report
`experiments/dnlp_vs_pypower/` (a 2×2 PWL-cost × DC-line study with a
reproducible proof-by-code demo).

Model HVDC links as controllable point-to-point power injections between
two buses, subject to capacity limits. Follows the MATPOWER `dcline`
table format. Applies to both AC and lossy DC formulations. Supports
multi-step scheduling (the power transfer on each DC link can vary per time
step).

The build plan lives in `plans/milestone-7-hvdc.md`. Key modeling decisions
recorded there: HVDC terminals are modelled as **generator-like objects**
with signed nodal injections `p_in`/`p_out` as the fundamental variables
(Convention B — positive = injection into the grid, both balance terms enter
with `+`). The loss model is **proportional only** (`loss_percent`), applied
via sign-split affine branches selected pre-construction: lossy in
`scheduled`/`downward`/fixed-direction `band` steps, lossless in `free` and
zero-straddling `band` steps. A non-affine `abs`-in-equality loss constraint
is **not** DCP-valid and must never be used — select an affine branch by the
known flow direction instead. Fixed converter loss (`LOSS0`) and full
sign-switching lossy behavior are deferred to Milestone 15.

**Standard test case (`pypower.t.t_case9_dcline`) — MVP vs M15 handling.**
The `dcline` table format is now verified against this Pypower fixture; the
loss law is `Pt = Pf - loss0 - loss1*Pf`, confirming `loss1` is a per-unit
fraction (`loss_percent = loss1 * 100`). What the MVP (Milestone 7) models
from this case, and what it does not:

| `dcline` column(s) | MVP (M7) | Full lossy (M15) |
|---|---|---|
| `Pf` | sending-terminal setpoint (`p_scheduled_mw`, pins `p_in`) | same |
| `Pmin`/`Pmax` | `p_in` box bounds (`band`/`downward` presets) | same |
| `loss1` (proportional) | modelled on fixed-direction steps | modelled everywhere |
| `loss0` (fixed/no-load) | **dropped**; `UserWarning` when nonzero | modelled via charge/discharge split |
| `Qf,Qt,Qmin*,Qmax*` (reactive) | **dropped** (unity-PF MVP) | out of scope (unity-PF) |
| `Vf,Vt` (terminal voltage setpoints) | **dropped** (no HVDC voltage control) | out of scope |
| `dclinecost` | `cost_coeffs=(c0,c1,c2)` polynomial | same |

**Consequence:** importing `t_case9_dcline` will **not** reproduce Pypower's
solution exactly — row 0 has `loss0=1`, which the MVP ignores. This is a
documented, intended approximation (see the plan doc + Milestone 15), not a
bug. Fixtures for this case are generated with the existing
`scripts/generate_pypower_fixtures.py` script (see "Fixture generation").

### Milestone 15 — Full lossy HVDC (sign-switching converter losses)
Extends Milestone 7 to carry losses when the flow direction is itself a
decision (i.e. `free` mode and zero-straddling `band` steps). Research 
direction: a charge/discharge-stylesplit of `p_in` into non-negative 
positive/negative parts (same machinery as the deferred lossy battery 
model), which keeps the loss equality affine while
letting the direction vary. Deferred because the MVP (Milestone 7) covers the
dominant proportional loss on fixed-direction links, and the fixed-loss sign
and `dcline` `LOSS0` units are cleaner to settle alongside this split. Also, fixed
converter loss (MATPOWER `LOSS0`), which was already prototyped in the 
`dnlp_vs_pypower` experiment. Finally, add reactive power support, propose apparent
power circle to match energy storage.

### Milestone 16 — Unify grid component model patterns
Bring **every** grid component into alignment with the component pattern that
HVDC (Milestone 7) establishes: data struct, validation, incidence,
constraint-set builder(s), and cost expression co-located in one module,
importing `cvxpy` and `numpy` only (no other cvxopf module — the
circular-import safeguard is about cvxopf-internal imports, not `cvxpy`). Every
OPF formulation constructor (AC, lossy DC, singlenode, future SOCP) consumes
each component by **composition** — calling its constraint-set and cost methods
and wiring them into that formulation's own network model — rather than
re-synthesizing the equations per formulation.

This is a cross-cutting refactor touching several existing components, each of
which predates the pattern:

- **Dispatchable generators.** `cost.py` today is effectively the
  dispatchable-generator component that never got first-class treatment,
  because the generator model was ported wholesale from Pypower. Its
  `poly_cost_expr` becomes that module's cost function.
- **Storage.** The AC apparent-power circle vs. DC real-power box operating
  regions currently live embedded in the AC and DC constructors; they move into
  storage's module as formulation-specific constraint methods
  (`ac_operating_constraints` / `dc_operating_constraints`), so each
  constructor grabs the one matching its formulation instead of re-synthesizing
  it.
- **Nondispatchable.** Same treatment — its operating region and injection
  wiring move into the component module and are consumed by composition.

Components that need formulation-specific feasible regions expose them as
distinct methods (e.g. storage's AC circle vs. DC box), and each constructor
grabs the one matching its formulation. HVDC (Milestone 7) is the reference
implementation of this "model a component once, plug into any network
formulation" contract — including the `ac_*`/`dc_*` method fork (with
pass-through delegation where the two forms coincide) and the late-bound
`cp.Parameter` scaling seam between component and constructor.

Note: the `storage.py → numpy only` / `nondispatchable.py → numpy only` lines
in the Module-responsibilities import chain above accurately describe the
**current** code and are left as-is until this refactor lands; M16 is the
aspirational forward pattern, not a description of today's modules.

### Milestone 17 — Hierarchical DC→AC receding-horizon dispatch
The capstone milestone: the concrete implementation of the project vision
stated in the README ("solve the convex `lossy_dc` formulation over the full
planning horizon ... then use the AC formulation over a short receding horizon
to verify and correct for true network physics, with SoC targets inherited
from the convex layer as boundary constraints").

Two-layer structure:

- **Upper layer — long-horizon plan.** Solve `lossy_dc` (convex, globally
  optimal) over the full multi-day horizon. Extract the SoC trajectory
  `soc*(t)`.
- **Signposts, not setpoints.** Only the **SoC waypoints** are passed down to
  the AC layer — *not* generator dispatch, voltages, or branch flows. The AC
  layer re-optimizes everything else against true network physics; it is only
  told what stored energy to arrive at, at each checkpoint. Passing full
  setpoints down would over-constrain the AC problem and defeat the purpose.
  This discipline is the core design decision of the milestone.
- **Lower layer — short AC window.** A 3–5 step AC-OPF over a receding horizon.
  The inherited SoC signpost enters as a **terminal constraint**
  (`soc[end] == soc*`) or a **terminal cost** (`ρ · ‖soc[end] − soc*‖`) — the
  hard/soft choice is a design axis to expose, and reuses the terminal-SoC
  machinery from Milestone 12.
- **Receding horizon.** The AC window advances, re-inheriting the next signpost
  from the DC plan at each step.

Dependencies and rationale:

- **Depends on M16.** A two-layer solver that shares device models across the
  DC and AC formulations should be built *after* the components compose
  uniformly (M16). Building it earlier would re-entrench per-formulation
  duplication.
- **Depends on M12** for the terminal-SoC hard-constraint-vs-soft-penalty
  machinery the AC window consumes.
- **Subsumes the convex-tracks-AC validation study.** The open-loop special
  case (single AC window, no recession; replay the DC SoC plan through AC and
  measure the feasibility/correction gap) is the natural validation artifact of
  this milestone — it is the currently-unfilled "temporal × cross-formulation"
  cell. The `case9_storage_{ac,dc}_24h.py` examples already supply ~80% of its
  inputs (identical 24h scenario in both formulations, each self-verifying its
  own SoC dynamics and operating region).

This milestone is why the formulation ladder, storage SoC coupling, M16
composability, and cheap multi-formulation runs exist — it is where that
infrastructure is cashed in.

### Milestone 8 — Nondispatchable generators

`NondispatchableUnit` in `src/cvxopf/nondispatchable.py`. Passed as
`nondispatchable=` to `build_opf` and `build_opf_multistep`. Available
power time series supplied via `df_nd` (multistep only).

AC formulation uses an apparent power circle constraint
`p_nd_t^2 + q_nd_t^2 <= P_max^2` intersected with `0 <= p_nd_t <= R_t`.
DC formulation uses only the real power bound `0 <= p_nd_t <= R_t`;
apparent power rating is stored but not enforced as a constraint.
No cost term. No curtailment penalty. No SoC dynamics.

Variables `p_nd` and `q_nd` are in engineering units (MW, MVAr) internally,
matching the storage convention. They enter the nodal balance divided by
`baseMVA` and are not rescaled in `extract_results`.

Results include `p_nd`, `q_nd` (AC only), and `curtailment = R_t - p_nd`.

### Milestone 9 — Sparse P/Q variables for AC-OPF
Controlled by `OPFOptions.sparse_pq` (default `True`).

When `sparse_pq=True`, `P` and `Q` are declared as flat `(nnz,)` CVXPY
variables `P_vec` and `Q_vec` over the Ybus sparsity pattern rather than
dense `(nb, nb)` matrices. This eliminates `2*(nb²-nnz)` trivially-zero
variables and the `P[Z]==0` / `Q[Z]==0` equality constraints that exist
only to compensate for the dense declaration. For case118, this reduces
P+Q variable count from ~27,848 to ~594.

Nodal injections use a precomputed `(nb, nnz)` scatter matrix `Rp` (stored
in `OPFBuild.data`) such that `p = Rp @ P_vec` and `q = Rp @ Q_vec`.

When `sparse_pq=False`, the legacy dense formulation is used unchanged.
This path is preserved for research comparison and timing benchmarks;
`notebooks/benchmark_opf.py` times both paths across all test cases.

Files changed: `ac_problem.py`, `problem.py` (`OPFOptions`),
`tests/test_problem_single.py`, `tests/test_problem_multistep.py`,
new `tests/test_sparse_pq.py`, new `examples/case9_sparse_vs_dense_ac.py`,
updated `notebooks/benchmark_opf.py`.

Do not set `sparse_pq=True` and then access `build.variables["P"]` —
the key will not exist. Use `build.variables.get("P_vec")` or check
`build.variables` keys when writing formulation-agnostic code.

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
- Do not use `build_acopf` or `build_acopf_multistep` — they are
  deprecated; use `build_opf(..., formulation="ac")` instead
- Do not change the DNLP variable formulation without understanding the paper
- Do not regenerate fixture files in CI
- Do not pin `numpy` in `pyproject.toml` — the numpy pin exists only in
  the fixture generation script
- Do not remove the `validate_case` call from `_parse_case` in
  `ac_problem.py` or `_parse_dc_case` in `dc_problem.py`
- Do not treat all CVXPY variables as per-unit — `b`, `b_q`, `soc`,
  `p_nd`, and `q_nd` are in engineering units (MW, MVAr, MWh); only
  generator and power flow variables are in per-unit
- Do not divide `b`, `b_q`, `p_nd`, or `q_nd` by `baseMVA` at variable
  declaration or inside constraint loops — the only `baseMVA` division for
  these variables is in the nodal balance term
- Do not multiply `b`, `b_q`, `soc`, `p_nd`, or `q_nd` result values by
  `baseMVA` in `extract_results` — they are already in engineering units
- Do not import `ac_problem` from `dc_problem` or vice versa
- Do not set `nlp=True` for convex formulations (DC, SOCP, fast-decoupled)
- Do not set `nlp=False` for the AC formulation
- Do not access `build.variables["P"]` or `build.variables["Q"]` for AC builds
  without checking `sparse_pq` — with the default `sparse_pq=True` these keys
  do not exist; use `build.variables.get("P_vec")` instead
- Do not implement Milestone 4 branch flow limits using `P_vec`/`Q_vec`
  until Milestone 9 is complete — Milestone 4 notes currently reference `P`, `Q`
  matrices and must be updated as part of Milestone 9
- Do not import `StorageUnitIdeal` from `problem.py` inside `ac_problem.py`
  or `dc_problem.py` — import from `storage.py` directly
- Do not import `NondispatchableUnit` from `problem.py` inside `ac_problem.py`
  or `dc_problem.py` — import from `nondispatchable.py` directly
- Do not add `delta` to `StorageUnitIdeal` — it is a global problem parameter
- Do not add `ns=0` to `build.data` when `storage=None` — breaks detection
- Do not add `nnd=0` to `build.data` when `nondispatchable=None` — breaks detection
- Do not use `numpy_array * cp.abs(cp_var)` for the aging cost — use
  `cp.multiply(numpy_array, cp.abs(cp_var))` to avoid CvxpyDeprecationWarning
- Do not add a second `p ==` or `q ==` constraint after `_make_step_constraints`
  returns — it owns all balance constraints including storage and nondispatchable
  injection terms
- Do not implement `StorageUnitLossy` without a separate plan — separate
  charge/discharge variables require structural changes to `_make_step_constraints`
- Do not add a cost term or curtailment penalty for nondispatchable generators
- Do not add `q_nd` to DC variables or results — nondispatchable reactive power
  is AC only
- Do not use `nd_available` in single-step `build.data` or `nd_p_available`
  in multistep `build.data` — these keys are mutually exclusive; check which
  is present before reading
- Do not pass `df_nd` to `_parse_case` or `_parse_dc_case` — it is processed
  separately in the multistep builder after the parse function returns
- Do not emit a `UserWarning` when `apparent_power_rating` is not used as a
  constraint in the DC nondispatchable path — no warning is needed here
- Do not call `validate_case` inside `_parse_singlenode_dc_case` — it does
  not call `validate_case` by design, because `make_singlenode_case`
  produces a dict with an empty branch table that `validate_case` rejects
- Do not store `Pd_series` as shape `(T, nb)` for `singlenode_dc` — it is
  shape `(T,)` because the formulation has no per-bus structure
- Do not add `n_hvdc=0` to `build.data` when `hvdc=None` — breaks detection
  (`"n_hvdc" in build.data`)
- Do not add a `q`/reactive term for HVDC — the MVP is unity power factor
- Do not enter the HVDC balance terms as `Ch_to - Ch_from` — both terminals
  are signed injections (Convention B) and enter with `+`
- Do not put `cp.abs` (or any non-affine atom) in the `p_out` loss equality —
  select an affine branch by the box's pre-construction sign instead
- Do not select a lossy loss branch for a zero-straddling box
  (`p_min_t < 0 < p_max_t`) — the lossy branch is valid only when the box is
  fixed-direction (`p_min_t >= 0` or `p_max_t <= 0`)
- Do not multiply `p_hvdc_in`/`p_hvdc_out` by `baseMVA` in `extract_results` —
  they are in engineering units (MW), like storage
- Do not import `HVDCLink` from `problem.py` inside `ac_problem.py` or
  `dc_problem.py` — import from `hvdc.py` directly
- Do not forward `hvdc`/`df_hvdc_min`/`df_hvdc_max` to `singlenode_dc` as a
  live component — the singlenode builders accept and silently drop them
  (`"n_hvdc"` never added to `build.data`, no `UserWarning`)
- Do not model MATPOWER `LOSS0` (fixed converter loss) in the MVP — it is
  dropped with a `UserWarning`; full fixed-loss modelling is Milestone 15
