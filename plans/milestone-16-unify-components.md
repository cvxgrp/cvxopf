# Milestone 16 — Unify grid component model patterns

**Status:** in progress — investigation complete; generator component module
landed additively; constructor integration is next
**Branch:** `unify-model`
**Nature of work:** cleanup, review, and standardization — *not* a mechanical
relocation. Where formulations diverge in how they express the same physical
quantity, we investigate the divergence, document it, and (with a prior
inclination toward standardization) resolve it or record a justified exception.

Reference implementation: `src/cvxopf/hvdc.py` (Milestone 7). Every decision
below is anchored to that module's shape.

---

## 1. Goal

Bring **every** grid component into alignment with the component pattern that
HVDC establishes: dataclass, validation, incidence, per-step operating
constraints, cross-step coupling constraints, injection builder, and cost
expression — all co-located in one module that imports only `cvxpy`, `numpy`,
and stdlib (no other cvxopf module). Every OPF formulation constructor (AC,
lossy DC, singlenode DC, future SOCP) consumes each component by
**composition**, calling its methods and wiring them into that formulation's
network model, rather than re-synthesizing the equations per formulation.

Components in scope:
- **Dispatchable generators** (`generator.py` now exists; constructor wiring is
  still pending)
- **Storage** (`storage.py`; partially component-shaped)
- **Nondispatchable** (`nondispatchable.py`; partially component-shaped)
- **HVDC** (`hvdc.py`) — the reference; not refactored, used as the template

---

## 2. The component interface contract (target shape)

Each component module exposes, in this order (mirroring `hvdc.py`):

1. **Dataclass** — user-facing parameters (e.g. `HVDCLink`, `StorageUnitIdeal`).
2. **`_validate_*(units, ext_bus_ids)`** — raises `ValueError` with indexed messages.
3. **`_make_*_incidence_matrix(...)`** — `(nb, n)` bus-mapping matrix.
4. **Static vectorizer(s)** — pull dataclass fields into `(n,)` numpy arrays
   (e.g. `_hvdc_static_box`), and any timeseries parser (`_parse_nd_timeseries`).
5. **`injections(units, vars..., ext_to_int)`** — returns
   `(injection_expr, inv_baseMVA)` where `injection_expr` is the scaled
   nodal-balance addend and `inv_baseMVA` is an **unbound `cp.Parameter`** the
   constructor binds before solving. Never instantiates `cp.Variable`.
6. **`ac_operating_constraints(...)` / `dc_operating_constraints(...)`** — the
   per-step feasible region, forked by formulation. Pass-through delegation
   where the two coincide (HVDC does this; the fork exists so the interface
   shape is uniform).
7. **`coupling_constraints(...)`** — cross-step (temporal) constraints. Returns
   `[]` for memoryless components. **New in M16** (see §4).
8. **`*_cost_expr(...)`** — the component's contribution to the objective.

**Invariant (from HVDC):** the module never creates `cp.Variable`s. The
constructor creates all variables in its own scope and passes them in. This is
non-negotiable and is what keeps the multi-step builders (which create T sets
of per-step variables) in control of variable lifetime.

---

## 3. Constraint taxonomy (explicit, per component)

This section is the heart of the plan. Every constraint a component contributes
falls into exactly one of these categories, and the plan keeps them
structurally distinct. **Conflating a cross-step coupling constraint with a
per-step operating constraint is the primary anti-pattern this milestone guards
against.**

### 3.1 Per-step operating region (formulation-specific)
The device's instantaneous feasible set at a single time step. Forked into
`ac_operating_constraints` / `dc_operating_constraints`.

| Component | AC | lossy_dc | singlenode_dc |
|---|---|---|---|
| Storage | apparent-power circle `b²+b_q²≤S²` | real-power box `\|b\|≤S` | real-power box `\|b\|≤S` |
| Nondispatchable | circle `p_nd²+q_nd²≤P²` ∧ `0≤p_nd≤R` | `0≤p_nd≤R` only | `0≤p_nd≤R` only |
| Generator | bounds `Pgmin≤Pg≤Pgmax` | `Pgmin≤p_gen[gen_bus]≤Pgmax` + `nogen==0` | `Pgmin≤Pg≤Pgmax` |
| HVDC | box + loss-branch equality | box + loss-branch equality | (dropped) |

### 3.2 Per-step bounds that are not the operating region
- Storage **SoC bounds** `0≤soc≤capacity` — per-step, same in all formulations.
- Nondispatchable **available-power bound** `p_nd≤R_t` — per-step,
  time-varying via `df_nd`, but **independent across steps** (NOT coupling).

### 3.3 Cross-step coupling constraints (temporal) — **first-class in M16**
Constraints that link variables at *different* time steps. Built after the
time-step loop, never inside the per-step builder.

- **Storage SoC dynamics** — the defining temporal constraint of storage:
  ```
  soc[0][s] == initial_soc[s] − b[0][s]·δ
  soc[t][s] == soc[t−1][s] − b[t][s]·δ    (t ≥ 1)
  ```
  Currently `_make_storage_soc_constraints`. In M16 this becomes
  `storage.coupling_constraints(b_list, soc_list, ...)`. It is the single most
  important constraint category to keep isolated and clearly named — it is why
  storage is a temporal device at all.
- **Generator / ND / HVDC** — return `[]` today. The `coupling_constraints`
  slot is retained so future ramp limits (generators), min-up/min-down, or
  HVDC ramp constraints have a defined home without re-architecting.

### 3.4 Injection into nodal balance
Each component contributes an addend to the single `p ==` / `q ==` (AC/DC) or
scalar balance (singlenode). Section owns exactly one `p ==` and one `q ==`
constraint; components supply addends, the constructor sums them. HVDC and ND
reactive terms: HVDC is unity-PF (real only); ND has `q_nd` in AC only.

### 3.5 Cost contribution
- Generator: `poly_cost_expr` (DCP-critical monomial form — preserve verbatim).
- Storage: L1 aging `Σ_t λ·|b_t|`.
- Nondispatchable: **none** (no cost, no curtailment penalty — do not add).
- HVDC: optional polynomial on `|p_in|`.

---

## 4. Decisions locked with the user

- **(A) Coupling as first-class interface method.** Every component exposes
  `coupling_constraints(...)`; storage returns SoC dynamics, others return `[]`.
  Makes the temporal-vs-per-step distinction structural. ✅ agreed.
- **(B) Generator-bounds divergence.** Investigate, document, and raise the
  three different mechanisms; **prior inclination toward standardization**.
  Carry it as an explicit investigation step with a written finding, not a
  foregone conclusion. ✅ agreed.
- **Sequencing.** One milestone, several commits; **incremental with generators
  as the pilot** (least entangled: `cost.py` is nearly standalone), then
  storage, then nondispatchable. Green tests between each. ✅ confirmed.
- **Balance composition.** Section 3 sums per-component injection addends into
  the single `p ==`/`q ==`; generator's injection builder returns `Cg @ Pg`.
  Preserves the "exactly one `p ==`" contract. ✅ confirmed.
- **Generator cost boundary.** New `src/cvxopf/generator.py` owns the
  builder-facing generator cost interface, but imports and delegates to
  `poly_cost_expr` in `cost.py`. `cost.py` remains the single authoritative
  implementation of polynomial and piecewise-linear generator costs. This is
  a deliberate, conservative variation from components whose costs are simple
  enough to live directly in the device module. ✅ confirmed.
- **"List the generators" — primary API with case-file fallback.** Real
  `list[DispatchableGenerator]` dataclass parallel to `HVDCLink`, with a
  `gen_from_matpower(gen, gencost, ...)` importer parallel to `hvdc_from_dcline`.
  This is the **main cvxopf generator API**. `build_opf` gains an optional
  `generators=` parameter. **`None` semantics differ from the other components**
  (see §9): for storage/ND/HVDC, `None` = "no such devices"; for generators,
  `None` = "read from the case dict's `gen`/`gencost` via `gen_from_matpower`"
  (convenience + standard-test-file interop; a system always has generators).
  Component symmetry required (conformance test): all four share the dataclass +
  eight-member interface; generators and HVDC additionally have MATPOWER
  importers (they map to the `gen`/`gencost` and `dcline` tables); storage and
  ND have no importer (no standard MATPOWER representation). ✅ confirmed.
- **`cp.Parameter` scaling seam.** Adopt uniformly across all components (sets
  up Milestone 13 parameter work); replaces the current mix of `1.0/baseMVA`
  float and `cp.multiply(1.0/baseMVA, ...)`. ✅ confirmed.
- **DCP preservation.** Add an explicit test that the convex paths (DC,
  singlenode) still pass CVXPY's DCP check after generators become a component,
  proving the monomial cost construction survived. ✅ confirmed.

---

## 5. Inconsistencies found — investigate, document, resolve

These surfaced while reading all four constructors. Each gets a written finding
in the milestone's final report; the prior inclination is to standardize.

1. **Generator bounds — three mechanisms (decision B).**
   - AC: `bounds=[Pgmin,Pgmax]` on a per-generator `Pg` variable.
   - DC: `p_gen` is a *nodal* `(nb,)` variable; bounds applied as
     `p_gen[gen_bus]>=Pgmin`, `p_gen[gen_bus]<=Pgmax`, plus `p_gen[nogen]==0`.
   - singlenode: per-generator `Pg` with explicit `Pg>=Pgmin`/`Pg<=Pgmax`.
   - **Investigate:** why is DC nodal rather than per-generator? Is the nodal
     representation load-bearing for flow conservation `A@p_flows + p_gen == Pd`,
     or incidental? **Prior lean:** standardize on a per-generator `Pg` with an
     explicit generator-incidence `Cg @ Pg` into balance (DC's `nogen` zeroing
     then falls out for free), unless the investigation shows the nodal form is
     required. Written finding either way.
2. **`baseMVA` scaling seam — three idioms.** `1.0/baseMVA` float (AC storage),
   `cp.multiply(1.0/baseMVA, ...)` (DC storage/ND/singlenode), `cp.Parameter`
   (HVDC). Standardize on the `cp.Parameter` seam (decision above).
3. **Storage detection guard asymmetry.** AC uses `"ns" in d and d["ns"] > 0`;
   DC uses `"ns" in d and d["ns"] > 0`; singlenode uses bare `"ns" in d`.
   CLAUDE.md's contract is `"ns" in build.data` (presence, never `ns=0`).
   Standardize the guard to bare presence and confirm no `ns=0` is ever written.
4. **`storage_bus` internal-vs-external.** AC/DC store `ext_to_int[u.bus]`
   (internal); singlenode stores `u.bus` (external). Pick one, document it.
5. **Nondispatchable single-step validation double-call (AC).** `_parse_case`
   validates ND twice (once in the shared block, once in the ND block).
   Remove the duplicate.
6. **HVDC singlenode contract.** singlenode accepts `hvdc=` and silently drops
   it (`n_hvdc` never added to `build.data`, no warning). This is the one
   documented exception to "every formulation consumes every component."
   Preserve and document; the component interface does not force a
   `singlenode_operating_constraints` on components a formulation drops.
7. **Two generator "types" — converge on one (standardization goal).**
   `make_singlenode_case(generators=[...])` already takes a **list of dicts**
   (`{"P_max_MW":..., "cost_coeffs":...}`), and the README advertises it. M16
   introduces `DispatchableGenerator` as the real component. **Decision: converge
   on one generator type — `DispatchableGenerator`.** Special cases are the
   enemy. `make_singlenode_case` should accept `list[DispatchableGenerator]`
   (or build them internally from a lightweight form), so there is exactly one
   generator representation across the whole package. If a convenience dict form
   is retained at the `make_singlenode_case` boundary for ergonomics, it must
   funnel into `DispatchableGenerator` immediately — never coexist as a second
   first-class type. The `build_opf(generators=...)` and
   `make_singlenode_case(generators=...)` parameters must take the same type.
   This is an outward-facing change; call it out in the migration notes.

---

## 6. Singlenode collapse — the third-variant question ✅ RESOLVED: Option 1

**Decision:** Option 1 (collapse singlenode into the `dc_*` path via a `ones`
incidence). General principle from the user: favour code reuse unless it is
very inefficient for some concrete reason. The `ones @ x` matmul over a
handful of devices is not a meaningful cost, so reuse wins. If profiling ever
shows the collapsed form is a bottleneck (it will not at these sizes), revisit.


Singlenode is not "DC without a network": it has **no incidence matrices** and
wires injections as scalar sums (`cp.sum(b_t)`, `cp.sum(p_nd_t)`, `cp.sum(Pg)`)
into a scalar balance. Two ways to avoid a third `singlenode_*` operating
method per component:

- **Option 1 (my lean):** singlenode constructor passes a *collapsed incidence*
  (`Cs → ones(1, ns)`, etc.) so it reuses `dc_operating_constraints` and the
  same `injections` method — `cp.sum` becomes `ones @ x`. One fork
  (`ac_*`/`dc_*`) suffices; singlenode picks `dc_*`. Changes singlenode's
  `cp.sum` idiom to a matmul (behaviourally identical).
- **Option 2:** add a genuine third method where components differ. More
  explicit, more surface area.

**Needs sign-off.** Recommendation: Option 1, because it makes singlenode a
true special case of DC (collapsed network) rather than a parallel code path,
which is the standardization spirit of this milestone.

---

## 7. Proposed commit sequence (incremental, green between each)

0. **Investigation commit (no behaviour change).** Written findings for §5
   items 1–6; decision on §6. Baseline test run recorded.
1. **Generators pilot.** New `generator.py`: `DispatchableGenerator`,
   `_validate_*`, `_make_generator_incidence`, `gen_from_matpower`,
   `injections`, `ac_/dc_operating_constraints`, `coupling_constraints`→`[]`,
   `gen_cost_expr` (delegates to `cost.poly_cost_expr`). Rewire all three constructors to
   compose it. Resolve bounds inconsistency (§5.1). DCP test (decision).
2. **Storage.** Move operating region (AC circle / DC box), SoC bounds,
   injection, aging cost, and `coupling_constraints` (SoC dynamics) into
   `storage.py`. Rewire constructors. `storage.py` gains `cvxpy` import
   (update the import-chain doc in CLAUDE.md — it currently says numpy-only).
3. **Nondispatchable.** Same treatment; operating region, injection into
   `nondispatchable.py`; `coupling_constraints`→`[]`. Rewire.
4. **Cross-cutting cleanup.** Uniform `cp.Parameter` seam, detection-guard
   standardization, `storage_bus` convention, remove ND double-validate.
5. **Docs + final report.** Update CLAUDE.md (import chains, module
   responsibilities, the numpy-only lines for storage/ND), flip Milestone 16
   to complete, write `experiments/`-style findings if warranted, update memory.
   **README doc-clean (explicit scope):**
   - Fix the **duplicated roadmap bullets** — M16 appears twice ("...dispatchable
     generators, storage, nondispatchable → first-class composable components"
     and "...matching HVDC pattern") and M15 appears twice; collapse each to one.
   - Update Project Structure: add `generator.py`; storage/ND are no longer just
     "dataclass and helpers" — they carry constraint/cost/injection builders.
   - Reconcile the `generators=` naming per §5.7 (one generator type,
     `DispatchableGenerator`, across `build_opf` and `make_singlenode_case`);
     update the Quick Start singlenode example accordingly.
   - Add a short generators/`DispatchableGenerator` usage example mirroring the
     storage / ND / HVDC example blocks.

---

## 8. Test strategy

- Full suite green after **every** commit (`uv run --extra dev pytest tests/`;
  baseline 512 passed).
- New: DCP-check test for convex paths post-generator-refactor.
- New: a per-component interface conformance test (each component exposes the
  eight interface members; memoryless ones return `[]` from
  `coupling_constraints`).
- Storage SoC-dynamics test must explicitly assert the coupling is present and
  correct across steps (guarding §3.3).
- No new Pypower fixtures; correctness is via existing equivalence/consistency
  tests. Behaviour must be **identical** — this is a refactor, values unchanged.

---

## 9. Non-goals

- No new physics (no ramp limits, no lossy storage, no HVDC reactive).
- No change to results-dict keys.
- The public `build_opf` signature gains one optional parameter `generators=`
  (a `list[DispatchableGenerator] | None`). **Contract (confirmed):**
  - `generators=<list>` → the primary cvxopf API path; use the list.
  - `generators=None` (default) → fall back to `gen_from_matpower(case)`, so
    existing calls and standard MATPOWER test files keep working unchanged.
  - **Invariant pinned by test:** `build_opf(case, generators=gen_from_matpower(case))`
    must produce a problem identical to `build_opf(case)` — same `build.data`
    arrays (`Pgmin`, `Pgmax`, `gencost`, `Cg`, `gen_bus`), same objective, same
    solution. The list path and the fallback path must not diverge.
  - Note the deliberate asymmetry vs. storage/ND/HVDC (whose `None` means "none
    present"): generators are load-bearing for feasibility, so `None` means
    "read from case," never "no generators." Do not later "fix" this into
    `None = no generators`.
- No SOCP (Milestone 11) in this milestone — but the interface is deliberately
  shaped so SOCP integrates for free later. SOCP is a convex relaxation whose
  network physics are themselves DCP (cone constraints on lifted variables, no
  DNLP bypass), so it is the first fully-DCP network formulation. Because the
  device/network DCP boundary (see CLAUDE.md) guarantees every device model is
  already DCP, the SOCP constructor will compose the existing device methods
  unchanged; its only new code is the cone network model plus a
  `socp_operating_constraints` fork for the few (if any) components whose
  feasible region differs in the lifted space. Verifying M16 leaves each
  device's constraints/cost individually `is_dcp()`-true is what makes this
  payoff real — so the per-object DCP conformance test (§8) is also SOCP
  groundwork.
