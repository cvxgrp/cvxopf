# Milestone 21 — Configurable and extensible formulation hierarchies

**Status:** planned

**Depends on:** Milestone 17 (validated lossy-DC→AC controller and audit
contract), Milestone 16+ (typed component adapters and shared formulation
assembly)

**Enabled by:** Milestone 11 (SOCP network model) for the first scientifically
meaningful three-layer hierarchy

## 1. Goal

Generalize the fixed two-layer Milestone 17 controller into a typed,
auditable hierarchy whose layers can use supported network formulations with
explicit state and target handoffs.

The milestone must support, at minimum:

1. selecting either `singlenode_dc` or `lossy_dc` as a planning layer;
2. retaining the validated `lossy_dc`→`ac` M17 workflow without behavioral or
   numerical drift;
3. composing three named layers, with the reference candidate
   `singlenode_dc`→`socp`→`ac`; and
4. adding a supported formulation without rewriting orchestration in every
   existing layer.

This is not permission to treat arbitrary optimization problems as
interchangeable. A formulation participates only through a reviewed typed
layer adapter, a declared handoff capability, formulation-specific acceptance
checks, and scientific validation of its role in the hierarchy.

## 2. Why this follows M17

M17 deliberately fixes one scientifically tested architecture:
long-horizon convex `lossy_dc` planning supplies storage-energy signposts to a
short nonlinear AC realization window. Its policy choices, causal IPOPT
initialization, residual gates, and S3/S3b evidence are specific to that
workflow.

M21 preserves M17 as the compatibility baseline and generalizes only after
that implementation is complete. This prevents formulation flexibility from
weakening the exact state alignment, failure semantics, and provenance that
make the hierarchy scientifically interpretable.

SOCP is a particularly useful future layer because it can represent more of
the AC network physics—voltage magnitude, reactive power, and apparent-power
limits—while retaining a convex solve. It may serve as:

- a stronger outer planner than lossy DC;
- an intermediate network-feasibility screen between coarse scheduling and
  nonlinear AC realization;
- a source of better AC initialization data; or
- a relaxation bound or diagnostic that helps distinguish modeled
  infeasibility from local nonlinear-solver failure.

Those are hypotheses to test, not guarantees assumed by the abstraction.

## 3. Architectural boundary

### 3.1 Layer specification

Introduce an ordered, immutable layer specification with explicit fields for:

- stable layer ID;
- supported formulation ID;
- temporal horizon and advancement policy;
- typed solver configuration;
- accepted-status and formulation-specific residual policy;
- declared inputs received from the preceding layer; and
- declared outputs offered to the following layer.

The first public formulation set should remain closed and typed. Dynamic
entry-point discovery or third-party plugin registration is outside this
milestone.

### 3.2 Formulation adapter

Each supported formulation owns an adapter responsible for:

- building the appropriate single- or multistep OPF through existing public
  builders;
- extracting formulation-neutral state and target quantities;
- retaining formulation-specific variables and diagnostics;
- reconstructing its independent acceptance residuals; and
- describing which handoff capabilities it can consume and produce.

Device physics remains component-owned under M16+. The hierarchy adapter must
not duplicate generator, load, storage, nondispatchable, or HVDC equations.

### 3.3 Handoff protocol

Do not reduce all inter-layer communication to an untyped dictionary. Define
small typed payloads for scientifically meaningful transfers, initially:

- realized storage state keyed by stable device identity;
- storage-energy boundary targets or envelopes;
- optional active-power schedules used as initialization hints rather than
  constraints;
- optional voltage/reactive/network state used only when both adjacent
  formulations define a valid mapping; and
- aligned global interval and boundary indices.

Every payload declares whether a field is a hard obligation, soft target,
bound, initialization hint, or diagnostic. Downstream layers must not silently
promote hints into constraints or discard obligations.

### 3.4 Hierarchy execution and audit tree

Replace M17's hard-coded outer/inner control flow with generic ordered-layer
execution while preserving:

- causal information boundaries;
- exact storage identity and state alignment;
- local/global time indexing;
- retained builds, attempts, solver evidence, and residual audits;
- no execution from an unaccepted controlling solve;
- explicit termination at the layer where failure occurs; and
- non-double-counted accounting from physically executed intervals only.

Every layer-to-layer edge receives its own retained handoff record. A
three-layer result must make it possible to determine whether failure arose in
the coarse planner, intermediate screen, final realization, handoff mapping,
or numerical solver.

## 4. Compatibility requirements

The generalized implementation must reproduce M17 through a compatibility
constructor or canonical two-layer configuration:

```text
lossy_dc (long horizon, CLARABEL)
    └── storage-SoC signposts
        ac (short receding window, IPOPT)
```

Compatibility requires the same:

- public M17 policy meaning;
- outer and inner mathematical problems;
- signpost selection and terminal policies;
- initialization-attempt sequence and deterministic seeds;
- accepted-primal gates;
- executed actions, trajectories, accounting, and termination behavior; and
- retained audit information, modulo an explicitly documented additive
  generalized-layer wrapper.

M21 must not silently migrate M17 users to a new default hierarchy.

## 5. Reference configurations

### 5.1 Configurable two-layer proof

Exercise at least:

```text
singlenode_dc → ac
lossy_dc      → ac  # exact M17 compatibility baseline
socp          → ac  # after M11 is available
```

The single-node configuration is useful for validating formulation selection,
but it must be described honestly: it omits network congestion and losses and
therefore may produce targets that the downstream networked layer cannot
realize.

### 5.2 Three-layer proof

The initial three-layer scientific candidate is:

```text
singlenode_dc scheduling
    └── energy targets or envelopes
        socp network screening
            └── screened targets and optional AC initialization hints
                ac nonlinear realization
```

The experiment must compare this hierarchy with direct `lossy_dc`→`ac` and,
where meaningful, `socp`→`ac`. It must measure whether the intermediate layer
improves feasibility classification, AC acceptance, initialization recovery,
runtime, or realized cost—not merely demonstrate that three solves can be
called in sequence.

## 6. Scientific questions

Before selecting defaults, answer:

1. Which SOCP relaxation is implemented, and under what network assumptions
   is it exact or potentially inexact?
2. What does an accepted SOCP result certify, and what does relaxation
   infeasibility certify?
3. Which SOCP outputs are physically meaningful targets versus merely
   relaxation variables or bounds?
4. Does SOCP screening reject signposts that AC cannot realize, or only move
   the nonlinear solver toward a different basin?
5. Does a coarse single-node layer add value beyond extending the horizon at
   low cost?
6. How do soft handoff deviations affect recursive feasibility at every
   remaining layer?
7. Is the additional solve time justified by fewer failed or recovered AC
   windows?

No relaxation result may be described as an AC-feasible operating point unless
an AC-feasibility recovery or exactness test establishes that claim.

## 7. Performance requirements

- Reuse prepared immutable inputs and formulation-independent component data.
- Do not rebuild unrelated layers when a receding-horizon policy requires only
  a downstream re-solve.
- Record build, canonicalization, and solver time separately per layer.
- Avoid converting large arrays through generic dictionaries in the inner
  loop; typed payloads should retain aligned arrays and stable IDs.
- Preserve opportunities for CVXPY `Parameter`-based repeated solves, without
  claiming DPP or fast re-solves until measured for each formulation.
- Establish scaling behavior in horizon length, number of layers, network
  size, and number of recovery attempts.

## 8. Stages

| Stage | Outcome |
|---|---|
| S0 | Characterize the completed M17 implementation and M11 SOCP API; inventory formulation-specific build, result, residual, and state-transfer contracts. |
| S1 | Freeze typed layer, capability, handoff, and generalized audit schemas; define the exact M17 compatibility mapping. |
| S2 | Refactor M17 orchestration behind the ordered-layer engine without changing its two-layer behavior. |
| S3 | Add selectable `singlenode_dc`, `lossy_dc`, and—when available—`socp` planning adapters with negative capability tests. |
| S4 | Implement retained edge handoffs, per-layer failure attribution, and independent residual audits. |
| S5 | Prove two-layer configurability and exact M17 regression equivalence. |
| S6 | Implement and test the reference three-layer `singlenode_dc`→`socp`→`ac` hierarchy. |
| S7 | Run the predeclared scientific comparison and decide whether any hierarchy beyond M17 merits a recommended configuration. |
| S8 | Document extension, performance, limitations, and a test-only formulation adapter that proves orchestration extension without builder edits. |

Each stage stops at a clean, reviewed commit. S2 cannot remove the dedicated
M17 path until compatibility gates pass. S6 cannot begin until the SOCP model's
scientific meaning and residual checks are frozen.

## 9. Required tests

- exact M17 two-layer trajectory and audit equivalence;
- layer-order, unique-ID, capability, and solver/formulation validation;
- identity-aligned multi-storage handoffs;
- mismatched horizon, interval, device, and unit rejection;
- unsupported handoff rejection rather than silent field loss;
- failure and construction-error retention at every layer and edge;
- `T=1`, truncated final windows, and different adjacent-layer horizons;
- deterministic initialization and retry provenance;
- single-node omissions represented explicitly in capabilities;
- SOCP relaxation residuals and AC recovery/exactness diagnostics;
- three-layer accounting without objective or energy double counting; and
- extension proof without edits to every existing formulation builder.

## 10. Non-goals

- arbitrary user-defined optimization problems;
- a dynamic plugin marketplace or entry-point registry;
- automatic discovery of a scientifically valid layer order;
- silently converting every upstream variable into a downstream constraint;
- claiming that more layers necessarily improve feasibility or optimality;
- replacing contingency analysis, topology control, or stochastic planning;
- changing the frozen M17 scientific record; or
- making SOCP-specific claims before Milestone 11 establishes the relaxation.

## 11. Completion gate

M21 is complete only when:

1. the canonical M17 configuration remains numerically and semantically
   equivalent to the dedicated implementation;
2. at least two outer formulations are selectable through the public typed
   contract;
3. a three-layer hierarchy runs through the same generic orchestration and
   retains complete per-layer and per-edge provenance;
4. unsupported formulation/handoff combinations fail before solving;
5. the scientific comparison reports both benefits and failure modes; and
6. documentation explains how to add a formulation adapter without modifying
   every existing layer or component.
