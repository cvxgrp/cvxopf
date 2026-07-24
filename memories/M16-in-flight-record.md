---
name: m16-in-flight-record
description: Running build log for Milestone 16 (unify grid component model patterns) — decisions, findings, commit checkpoints
metadata:
  type: project
---

# Milestone 16 — in-flight build record

Running log of the M16 build (unify grid component model patterns). Plan lives
at `plans/milestone-16-unify-components.md`. Reference implementation: HVDC
(`src/cvxopf/hvdc.py`). See also [[cvxopf-session-working-style]] and
[[feedback_commit_workflow]].

## Locked decisions (all confirmed with user)

1. Incremental, one milestone / several commits; **generators as pilot** →
   storage → nondispatchable; green tests between each.
2. Balance composition: components supply injection addends; constructor sums
   into the single `p ==`/`q ==`. Generator injection returns `Cg @ Pg`.
3. New `src/cvxopf/generator.py` owns the builder-facing generator cost
   interface and delegates to `cost.poly_cost_expr`. `cost.py` remains the
   single source of truth for polynomial and PWL cost modeling.
4. `DispatchableGenerator` dataclass + `gen_from_matpower` importer = the
   **primary generator API**. `build_opf` gains optional `generators=`.
   `None` → fall back to `gen_from_matpower(case)` (asymmetric vs storage/ND/
   HVDC where `None` = none present; generators are load-bearing).
   With an explicit list, `gen`/`gencost` may be absent from the network case;
   a temporary normalized case preserves the existing validator/reindexer and
   never mutates caller data.
5. **Converge on ONE generator type.** `make_singlenode_case(generators=[dicts])`
   must funnel into `DispatchableGenerator`; no second first-class type.
   Standardization is the goal; special cases are the enemy.
6. Uniform `cp.Parameter` `baseMVA` scaling seam across all components.
7. Coupling constraints are a first-class interface method
   (`coupling_constraints(...)`); storage returns SoC dynamics, others `[]`.
8. Singlenode collapses into the `dc_*` path via a `ones` incidence (favour
   reuse unless very inefficient).
9. Add per-object DCP conformance tests (also SOCP groundwork).
10. Injection API is network-specific and fixed-arity:
    `ac_injections` / `dc_injections` return
    `(p_expr, q_expr_or_None, scaling_or_None)`. Future SOCP uses the AC-network
    method; future reactive HVDC remains representable without splitting real
    and reactive incidence/sign logic across functions.

## Constraint taxonomy (five categories — keep structurally distinct)

1. Per-step operating region (formulation-specific: AC circle / DC box).
2. Per-step bounds that aren't the operating region (storage SoC bounds; ND
   available-power bound — NOT coupling).
3. Cross-step coupling (storage SoC dynamics — the defining temporal
   constraint; others `[]`).
4. Injection into nodal balance.
5. Cost contribution (ND has none — never add).

## Inconsistencies to investigate/resolve (plan §5)

1. Generator bounds — 3 mechanisms (AC var-bounds / DC nodal p_gen+nogen /
   singlenode per-gen). Investigate WHY DC is nodal; prior lean = standardize
   to per-gen `Pg` + explicit `Cg @ Pg`.
2. baseMVA seam — 3 idioms → cp.Parameter.
3. Storage detection guard asymmetry (`"ns" in d` vs `... and d["ns"]>0`).
4. `storage_bus` internal (AC/DC) vs external (singlenode).
5. ND double-validate in AC `_parse_case`.
6. HVDC dropped by singlenode (documented exception — preserve).
7. Two generator types → converge (decision 5).

## Commit checkpoints

- `8a0a3a5` — docs: M16 plan + DCP boundary invariant + M17 roadmap entry
  (branch `unfiy-model`). Pre-implementation groundwork.
- `c3ed408` — commit 1: add `src/cvxopf/generator.py` (DispatchableGenerator
  component, HVDC-pattern). Additive/inert — not yet wired into constructors,
  so 816 baseline unaffected. Validated in isolation: Cg matches
  network.make_incidence_matrix; bounds match MATPOWER read; gencost round-trips
  exactly on case9/case14 (incl. startup/shutdown, F8). Ruff clean.

## Status

**Current:** generator component and cost representation committed in
`35fd0b8`; generator integration is staged locally. AC, lossy DC, and
single-node builders now compose generator-owned parsing, incidence,
injection, operating constraints, and cost delegation. Lossy DC uses
per-generator `Pg` with `Cg @ Pg`; `make_singlenode_case` accepts the shared
dataclass; the public API accepts `generators=` with MATPOWER fallback.
Explicit lists may accompany network-only cases without `gen`/`gencost`; a
temporary case copy feeds the existing validator and reindexer. Full suite on
2026-07-24: 846 passed, 29 expected project warnings.

**Current storage slice (staged locally):** `storage.py` now owns static data
vectorization, paired AC/DC injections, operating constraints (including SoC
bounds), cross-step SoC coupling, and L1 cycling cost. AC, lossy DC, and
single-node builders compose these methods; single-node uses collapsed
incidence. Component-level DCP, fixed-arity injection, and trajectory tests
are added.

**Next:** full-suite review and commit the storage slice, then begin
nondispatchable generation.

**Cost-boundary review 2026-07-24:** `cost.py` already implements and tests
both `MODEL=2` polynomial and `MODEL=1` piecewise-linear costs, including the
documented lower-convex-hull treatment of nonconvex PWL data. Leave that
implementation alone. `generator.py` imports and delegates to it, giving OPF
builders one component-facing cost entry point without duplicating any cost
modeling. The remaining integration issue is data preservation:
`gen_from_matpower` must not discard a `MODEL=1` row when constructing the
generator component representation. **Resolved locally:** the component now
uses the explicit discriminator `cost_type="polynomial"|"piecewise_linear"`,
with `cost_coeffs` for polynomial data and `(power, cost)` `cost_points` for
PWL data. Mixed MODEL=1/MODEL=2 MATPOWER rows round-trip exactly through
`gen_from_matpower`/`generator_gencost`; evaluation still delegates exclusively
to `cost.py`.

**Standardization directions (apply during impl):**
- F1: per-generator `Pg (ng,)` + `Cg @ Pg` injection everywhere; DC drops
  `p_gen`, `gen_bus`-indexed bounds, `nogen_buses`.
- F2: `cp.Parameter` baseMVA seam for all components.
- F3: bare `"ns" in d` guards (drop redundant `> 0`).
- F4: internal indexing for `storage_bus` + `nd_bus`.
- F5: remove duplicate ND validate at `ac_problem.py:163`.
- F6: preserve singlenode HVDC drop.
- F7: `make_singlenode_case` takes `list[DispatchableGenerator]`.

## Findings log

### F8 — startup/shutdown cost not carried by DispatchableGenerator (NEW, needs user call)
Discovered during commit-1 round-trip test. `gen_from_matpower` -> 
`generator_gencost` reproduces case9 gencost EXACTLY except gencost column 1
(STARTUP: case9 has 1500/2000/3000). Cost coefficients (cols 4-6), MODEL,
NCOST all round-trip perfectly, so `poly_cost_expr` output is identical and the
problem is unaffected. Root cause: `DispatchableGenerator` has no
startup/shutdown field. These costs are only meaningful under unit commitment
(binary on/off), which cvxopf does NOT model — they are currently inert (never
enter any objective/constraint). **Options:** (1) add `startup`/`shutdown`
fields for data fidelity even though inert; (2) document that they are dropped
because continuous OPF never uses them. **Consequence either way:** the planned
invariant test is on the *problem* (`generators=gen_from_matpower(case)` vs
`generators=None` -> identical objective/constraints/data arrays used by the
solver), NOT on the raw gencost array, so it passes regardless. **RESOLVED (user chose add-the-fields):** added `startup`/`shutdown` to
`DispatchableGenerator`, carried through `gen_from_matpower` (read cols 1,2)
and `generator_gencost` (write cols 1,2). Rationale: future convex relaxations
of unit commitment would need this data; cleaner to carry from the start than
retrofit. Fields documented as inert in the current continuous OPF. gencost
now round-trips EXACTLY on case9 (ng=3) and case14 (ng=5).


### Baseline (2026-07-20)
`uv run --extra dev pytest tests/ -q` → **816 passed**, 29 warnings (all
expected `df_Q ignored` in DC paths). NOTE: CLAUDE.md header still says "512
passed" — stale, fix in doc pass.

### F1 — Generator-bounds divergence (§5.1) — RESOLVED, standardize
The divergence is a **variable-representation** difference, not just bound
syntax:
- AC: `Pg` per-generator `(ng,)`, `bounds=[Pgmin,Pgmax]` (`ac_problem.py:276`).
- DC: `p_gen` **nodal** `(nb,)`, bounds via `p_gen[gen_bus]>=/<=` (`dc_problem.py:270`)
  + `p_gen[nogen]==0` (`:274`).
- singlenode: `Pg` per-generator `(ng,)`, `Pg>=/<=` (`:132-133`).

**Why DC is nodal (load-bearing reason):** flow conservation
`A @ p_flows + p_gen == Pd` requires `p_gen` shape `(nb,)` because `A` is
`(nb,nl)` and `Pd` is `(nb,)`. AC avoids this by linking via
`p == Cg @ Pg - Pd`, keeping `Pg` per-generator.

**Resolution (confirms plan's prior lean):** generator component owns
per-generator `Pg (ng,)` with `bounds=[Pgmin,Pgmax]`, exposes injection
`Cg @ Pg (nb,)`. DC flow conservation becomes `A @ p_flows + Cg @ Pg == Pd`,
dropping `p_gen`, the `gen_bus`-indexed bounds, AND `nogen_buses` (redundant —
`Cg` has no column at non-gen buses, so zero-injection there is automatic).
Three mechanisms → one. Verify during impl that `p_gen[nogen]==0` truly carried
no extra meaning (it does not — `Cg @ Pg` structurally cannot inject there).
Links to [[cvxpy-affine-equality-rule]] (bounds stay affine — DCP-clean).

### F2 — baseMVA scaling idioms (§5.2) — RESOLVED, standardize to cp.Parameter
Three idioms coexist:
- AC storage/ND: `(1.0 / baseMVA) * (Cs @ b_t)` — Python float `*` CVXPY expr
  (`ac_problem.py:414-417`).
- DC/singlenode storage/ND: `cp.multiply((1.0 / baseMVA), ...)`
  (`dc_problem.py:261-262`, `singlenode:127-128`).
- HVDC (all formulations): unbound `cp.Parameter`, bound via
  `inv_bMVA.value = 1.0/baseMVA` (`ac:527,743`, `dc:354,562`).

**Standardize to the HVDC cp.Parameter seam** (decision 6; sets up M13).
Correctness note: the AC `float * expr` form does NOT trigger the
`__array_ufunc__` CvxpyDeprecationWarning that CLAUDE.md warns about for the
aging cost — that warning needs a *numpy array* on the left; here it's a Python
float. So this is a consistency fix, not a latent-bug fix. Don't report it as a
bug.

### F3 — detection-guard asymmetry (§5.3) — RESOLVED, standardize to bare `in`
- AC/DC: `"ns" in d and d["ns"] > 0` (belt-and-suspenders).
- singlenode: bare `"ns" in d`.

**Both are currently correct**: parse functions only add `ns`/`nnd`/`n_hvdc`
to `d` when the component is present AND non-empty (the whole block is guarded
by `if storage is not None:` etc.), so `d["ns"]` is never 0 when the key exists.
The `and d["ns"] > 0` is redundant, not load-bearing. CLAUDE.md's contract is
detection by presence (`"ns" in build.data`, never `ns=0`). **singlenode's bare
`in` form is the one matching the documented contract** — standardize AC/DC
toward it (drop the redundant `> 0`). Counterintuitive: singlenode is the
correct one here, not the outlier.

### F4 — storage_bus / nd_bus internal vs external (§5.4) — RESOLVED, use internal
AC (`ac_problem.py:183`) and DC (`dc_problem.py:173`) store
`ext_to_int[u.bus]` (**internal** 0-based); singlenode (`singlenode:249`)
stores raw `unit.bus` (**external**). `nd_bus` has the same split. Latent
inconsistency — `build.data["storage_bus"]` means different numbering per
formulation; harmless only because nothing cross-formulation reads it yet.
**Standardize to internal** — every other internal array in `build.data`
(`Cs`, `Cg`, `gen_bus`, `ref`, `pv`) is internal-indexed, so singlenode is the
outlier. Move `storage_bus` AND `nd_bus` in lockstep.

### F5 — ND double-validate in AC _parse_case (§5.5) — RESOLVED, remove dup
`_validate_nondispatchable` called twice in AC `_parse_case`: `:163` (shared
block) and `:195` (ND block). DC has no duplication. Idempotent so harmless.
**Remove the `:163` call**; keep `:195` (it sits in the ND-specific block that
builds the incidence/params).

### F6 — HVDC dropped by singlenode (§5.6) — CONFIRMED, preserve exception
singlenode accepts `hvdc=`/`df_hvdc_min=`/`df_hvdc_max=` (`:303,445-447`) but
has ZERO other `hvdc` references — no validate, no injection, `n_hvdc` never
added to `data`. True silent drop, exactly as CLAUDE.md documents. The one
sanctioned exception to "every formulation consumes every component."
**Preserve.** Cross-ref [[hvdc-silent-ignore-dispatch-constraint]] (records
why the dispatch site forces singlenode to accept these kwargs).

### F7 — two generator types (§5.7) — RESOLVED, converge on DispatchableGenerator
`make_singlenode_case(generators: list[dict])` uses dict form
`{"P_max_MW", "cost_coeffs", "P_min_MW"?}` (`testcases/singlenode.py:15,104-114`)
and builds a full MATPOWER `gen`/`gencost` array from them. This is a genuine
second generator representation. **Converge:** `make_singlenode_case` accepts
`list[DispatchableGenerator]` and reuses the same dataclass→array path that
`gen_from_matpower` inverts. One representation, two directions (build array
from dataclass; parse dataclass from array). Outward-facing API change — note
in migration. Users of `testcases/__init__.py:25` (`make_singlenode_case(250.0,
generators)`) will need updating.
