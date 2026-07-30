# Milestone 16+ — Typed component adapters and uniform assembly

**Status:** complete — implemented and verified 2026-07-29
**Depends on:** Milestone 16
**Enables:** cheaper formulation growth, Milestone 17 orchestration, future
SOCP integration
**Nature of work:** architecture refactor with no intended change to the
physical models, public result values, or solver selection

## 1. Goal

Complete the abstraction started by Milestone 16. M16 moved each device's
mathematics into its component module, but the formulation builders still
know every component by name and repeat the same assembly sequence:

1. prepare device data;
2. create formulation-owned variables;
3. build and scale injections;
4. add per-step operating and network constraints;
5. accumulate per-step costs;
6. add horizon-level coupling constraints;
7. publish variables, metadata, expressions, and results.

M16+ introduces a small typed adapter/protocol layer for that sequence and
uses it to remove orchestration duplication. Components continue to own their
models, formulations continue to own network equations and variable
lifetimes, and `results.py` continues to own the public result schema.

The target is a lean internal interface, not a plugin framework and not an
object-oriented rewrite of CVXPY.

## 2. Design principles

- **Preserve the device/network boundary.** Components contribute injections;
  formulations own the one real and, where applicable, reactive balance.
- **Builders own variables.** Component modules and adapters must not create
  `cp.Variable` objects.
- **Prepared data is structurally read-only build input.** Preparation
  validates and vectorizes once. Per-step assembly does not reinterpret or
  mutate user dataclasses or prepared arrays.
- **Capabilities are explicit.** Optional cost, reactive, network-constraint,
  and result hooks are represented as capabilities or `None`, not discovered
  with `hasattr` or exception handling.
- **Null is a first-class contribution.** A component may validly contribute
  nothing to a formulation. This is distinct from being unsupported.
- **No speculative generality.** The interface must be justified by the four
  existing component families and the three existing formulations.
- **No physics changes.** Numerical equivalence to the pre-M16+ implementation
  is a release gate.

## 3. Architectural boundary

### 3.1 Component adapter

The implemented private contract separates component-family hooks from
formulation-specific capabilities:

```python
@dataclass(frozen=True)
class FormulationAdapter:
    capability: FormulationCapability
    variable_specs: VariableSpecHook | None = None
    injections: InjectionHook | None = None
    operating_constraints: ConstraintHook | None = None
    network_constraints: ConstraintHook | None = None
    step_cost: StepCostHook | None = None
    step_expressions: StepExpressionHook | None = None
    horizon: HorizonHook | None = None


@dataclass(frozen=True)
class ComponentAdapter:
    name: str
    prepare: PrepareHook
    metadata: MetadataHook
    formulations: Mapping[Formulation, FormulationAdapter]
```

Every adapter declares exactly one `ACTIVE`, `NULL`, or `UNSUPPORTED` binding
for each supported formulation. Active bindings must provide variable,
injection, operating-constraint, and horizon hooks. Cost, network-constraint,
and reporting-expression hooks are optional. Null and unsupported bindings
cannot define hooks.

The protocols receive prepared data and typed step or horizon context. They
bind the shared assembler to the authoritative mathematics in `generator.py`,
`storage.py`, `nondispatchable.py`, and `hvdc.py`; they do not replace the
user-facing dataclasses or duplicate those models.

### 3.2 Prepared component state

Each supplied component collection is prepared once and stored in the typed,
structurally read-only registry:

```python
@dataclass(frozen=True)
class PreparedComponent:
    adapter: ComponentAdapter
    units: tuple[object, ...]
    data: Mapping[str, object]


@dataclass(frozen=True)
class PreparedComponents:
    formulation: Formulation
    components: Mapping[str, PreparedComponent]
    flat_data: Mapping[str, object]
```

`OPFBuild.data` may retain its current flat public/internal compatibility
schema during this milestone. The typed container is the assembly contract;
flattening is an explicit, collision-checked publication step. Mapping
structure is defensively copied and read-only; contained NumPy arrays and
CVXPY objects are not deep-frozen and must not be mutated after preparation.
Prepared component keys are also collision-checked against the
formulation-owned parser namespace before the two mappings are merged.

### 3.3 Per-step contribution

For each active component and network step, the assembler creates variables
from `VariableSpec` objects and produces:

```python
@dataclass(frozen=True)
class InjectionContribution:
    p_pu: cp.Expression | None
    q_pu: cp.Expression | None
    inv_base_mva: cp.Parameter | None = None


@dataclass(frozen=True)
class StepContribution:
    variables: Mapping[str, cp.Variable]
    injection: InjectionContribution
    operating_constraints: tuple[cp.Constraint, ...] = ()
    network_constraints: tuple[cp.Constraint, ...] = ()
    cost: cp.Expression | None = None
    expressions: Mapping[str, cp.Expression] = field(default_factory=dict)
```

The injection channels are bus-scattered, per-unit nodal injections with
positive sign into the network. A component expressed in engineering units
creates an unbound inverse-base parameter inside its injection expression;
the shared assembler alone binds it to `1 / baseMVA`. Reactive absence is
`None`, never scalar zero.

The ordered aggregate combines all component injections, constraints, costs,
and expression maps. Duplicate variable or expression names are rejected.
Publication then merges component variables, metadata, and expressions with
the formulation-owned `OPFBuild` namespaces using explicit collision checks.

### 3.4 Horizon contribution

After the per-step loop, the assembler passes each active component's variable
history to its `horizon` hook exactly once, including a single-step horizon
represented by one-element lists:

```python
@dataclass(frozen=True)
class HorizonContribution:
    constraints: tuple[cp.Constraint, ...] = ()
    terminal_cost: cp.Expression | None = None
    expressions: Mapping[str, cp.Expression] = field(default_factory=dict)
```

Horizon constraints and terminal costs are aggregated once per horizon.
Before horizon assembly or variable publication, every step must contain the
same component names and each component must retain identical variable names
and shapes. Violations raise a step- and component-qualified `ValueError`.
Per-step reporting expressions publish as one expression for a single-step
build and as an ordered list for a multistep build; horizon expressions
publish once and are never stacked or scaled by `delta`. Step, horizon, and
formulation-compatibility expression namespaces are collision-checked before
constructing `OPFBuild`.

## 4. Null-component semantics

The adapter distinguishes three states:

1. **Absent:** the user supplied no units of this component.
2. **Active:** the component contributes variables, constraints, injection,
   cost, or reported quantities.
3. **Null for this formulation:** units were supplied, but the formulation's
   abstraction intentionally eliminates the component and the component has no
   mathematical contribution.

HVDC in `singlenode_dc` is the canonical null model. A single-node
copper-plate formulation removes the two distinct terminal nodes and the
transmission path between them. Retaining line-transfer loss after removing
the line would mix network-detail physics into a model whose defining
abstraction is that the network is absent. Therefore:

- no HVDC variables, bounds, losses, costs, metadata, or results are created;
- no warning is required merely because `hvdc=` was supplied;
- the behavior is deliberate and documented, not an unsupported fall-through;
- conformance tests assert that single-node builds and solutions are identical
  with and without any supplied HVDC collection;
- time-series HVDC bounds are likewise irrelevant to the single-node model and
  must not be validated as though they affected it.

The null capability must be explicit in the formulation/adapter registry so a
future component cannot be silently dropped by omission.

## 5. Formulation assembler

Create a narrow shared assembler used by AC, lossy DC, and single-node DC.
It should own only repeated component mechanics:

- iterate over registered component adapters;
- prepare active component collections;
- request formulation-specific variable specifications;
- create variables in builder scope;
- obtain and scale injection contributions;
- collect operating and device-to-network constraints;
- collect per-step and horizon costs;
- invoke every active or explicitly-null coupling capability consistently;
- retain component variables/expressions for publication.

It must not own:

- AC admittance/trigonometric equations;
- DC flow-conservation or loss equations;
- single-node scalar balance;
- formulation-specific network variables;
- solver defaults;
- public result extraction.

Each formulation should still read as:

```text
prepare network
prepare components
for each step:
    create network variables and equations
    assemble component contributions
    form the one network balance
assemble horizon contributions
construct OPFBuild
```

## 6. Migration stages

### Stage 0 — Characterization and API lock

The supported typing target is Python 3.11, matching `requires-python` and the
Ruff target. The stale Python 3.10 package classifier is removed rather than
advertising a version excluded by package metadata.

Mypy is the project type checker. During migration, strict checking begins at
the private adapter contract module and expands with the typed assembly
surface; this avoids claiming that the legacy formulation builders are already
fully typed.

The injection boundary is fixed in per-unit network units. Components return
bus-scattered nodal real-power and, where applicable, reactive-power
expressions, with positive sign denoting network injection. A component whose
decision variables use engineering units creates an unbound `1 / baseMVA`
`cp.Parameter` inside its coordinated injection expression and returns that
parameter with the expression. Components whose variables are already per
unit return no scaling parameter. The shared assembler owns the network base
and binds every returned parameter exactly once; components never bind it.

Prepared data is structurally read-only assembly input. Its mapping structure
is defensively copied and cannot be modified through the adapter interface.
Contained numerical arrays and CVXPY objects retain their native mutability
but must not be mutated after preparation.

- Record the exact current `OPFBuild.variables`, `.data`, and `.expressions`
  schemas for every formulation, single/multistep, and component combination.
- Add numerical equivalence fixtures for mixed-component builds.
- Add static assertions that component modules create no `cp.Variable`.
- Decide the minimum Python typing target supported by the package.
- Write the typed protocols before moving behavior.

### Stage 1 — Contribution value objects

**Status:** complete — typed values, centralized scale binding, and
generator/ND adapter bindings are in place; formulation builders are unchanged

- Add typed injection, step, and horizon contribution containers.
- Introduce adapters incrementally over the existing component functions
  without changing the three formulation builders. Generator and ND bindings
  establish the contract here; storage and HVDC bindings follow in their
  dedicated migration stages.
- Centralize binding of engineering-unit scaling parameters.
- Prove DCP status and expression units are unchanged.

### Stage 2 — Generators and nondispatchable pilot

**Status:** complete — all six formulation/horizon builder paths use the
generator and nondispatchable adapters for preparation, variable
specification, per-step contributions, horizon hooks, and metadata

- Migrate generators first because they exercise real/reactive injections,
  operating constraints, network constraints, and cost delegation.
- Migrate nondispatchable second because it exercises time-varying prepared
  data, AC/DC channel differences, and absence of cost.
- Keep compatibility wrappers around current module-level functions until all
  builders migrate.

### Stage 3 — Storage horizon path

**Status:** complete — all six formulation/horizon builder paths use the
storage adapter for preparation, variable specification, injections,
operating constraints, cycling cost, horizon constraints, terminal cost, and
metadata

- Migrate storage operating constraints, injections, cycling cost, SoC
  coupling, hard terminal boundary, and soft terminal cost.
- Ensure terminal cost remains a once-per-horizon contribution.
- Route both single- and multistep builds through the same one-element/list
  horizon interface.
- Preserve the M12 result and metadata contracts exactly.

### Stage 4 — HVDC and explicit null capability

**Status:** complete — AC and lossy-DC builders use the HVDC adapter for
preparation, variable specification, signed terminal injections, per-step
box/loss constraints, transfer cost, horizon invocation, and metadata;
single-node builders require the explicitly registered null capability

- Migrate the two-terminal injection and per-step box/loss model.
- Represent HVDC as explicitly null in `singlenode_dc`.
- Preserve the AC/lossy-DC signed-injection convention.
- Do not move full-lossy or sign-switching physics from Milestone 15 into this
  refactor.

### Stage 5 — Shared orchestration

**Status:** complete — all six builders use common ordered registration,
shared preparation and step/horizon execution, generic contribution
aggregation, and shared variable/metadata publication; formulation modules
retain their network variables, equations, loss terms, balance construction,
named reporting expressions, and result construction

- Replace repeated per-component blocks in all three builders with the common
  assembler.
- Delete compatibility wrappers and dead orchestration only after all
  equivalence tests pass.
- Keep network equations visibly formulation-local.

### Stage 6 — Documentation and extension proof

**Status:** complete — the closed-world extension path is documented and a
test-only adapter proves generic variables, scaled injections, constraints,
cost, step and horizon expression publication, metadata, memoryless horizon
behavior, and explicit formulation capability selection

- Document how to add a new component and how to add a new formulation.
- Implement a test-only toy component adapter proving that, once a component
  collection reaches the centralized registry, every formulation builder
  consumes its mathematical contributions generically without
  component-specific assembly logic.
- Show that an AC-network formulation selects AC channels while a
  copper-plate/DC formulation selects DC channels or an explicit null model.

## 7. Test gates

### Gate 1 — typed contract

- Every adapter satisfies the selected protocols under the project's type
  checker.
- No component or adapter creates `cp.Variable`.
- Optional capabilities are explicit and typed.
- No assembly behavior depends on `hasattr`, module-name checks, or broad
  exception handling.

### Gate 2 — structural conformance

- All engineering-unit injections are scaled exactly once.
- Reactive absence is `None`, never scalar zero.
- Every active component coupling hook is called once per horizon, including
  `T=1`.
- Every formulation has exactly one modeled real-power balance; AC has exactly
  one modeled reactive-power balance.
- Terminal storage costs are counted exactly once.

### Gate 3 — numerical equivalence

For single- and multistep AC, lossy DC, and single-node DC:

- objectives, primal variables, and retained net-injection expressions match
  the pre-M16+ baseline within existing solver tolerances;
- convex builds remain DCP;
- AC builds retain the same DNLP expression structure and solution tolerances;
- generator MATPOWER fallback and explicit generator-list paths remain
  equivalent;
- mixed generator/storage/ND/HVDC builds match baseline behavior.

### Gate 4 — null HVDC model

- single-node model structure is identical with and without `hvdc=`;
- single-node objective and dispatch are identical with and without `hvdc=`;
- no HVDC variables, metadata, expressions, or result keys appear;
- AC and lossy DC continue to compose HVDC normally;
- a test proves the null behavior is registry-driven, not caused by a missing
  builder call.

### Gate 5 — extension proof

- A test-only adapter proves that, once a component collection reaches the
  centralized registry, its contributions assemble without component-specific
  logic in `ac_problem.py`, `dc_problem.py`, or
  `singlenode_dc_problem.py`.
- A memoryless component returns no horizon constraints without special-case
  code in a formulation builder.

### Gate 6 — regression

- Ruff passes.
- Type checking passes.
- Full test suite passes.
- Numerical behavior remains locked by the formulation/component
  characterization suite.
- The AC and lossy-DC HVDC examples execute successfully and assert the
  direction-specific proportional-loss law rather than only printing a
  residual.

## 8. Non-goals

- No public third-party plugin API or dynamic entry-point discovery.
- No new optimization variables inside component modules.
- No replacement of CVXPY expressions with an intermediate algebra.
- No change to storage efficiency, generator ramping, ND curtailment cost, or
  HVDC loss physics.
- No result-schema redesign; that belongs to the correctness/API hardening
  workstream.
- No objective time-discretization change; that belongs to the
  correctness/API hardening workstream.
- No parameterized repeated-solve API from Milestone 13.

## 9. Commit sequence

1. `test: characterize component assembly contracts`
2. `refactor: add typed component contribution protocols`
3. `refactor: adapt generators and nondispatchable components`
4. `refactor: adapt storage horizon contributions`
5. `refactor: encode HVDC single-node null model`
6. `refactor: centralize formulation component assembly`
7. `docs: document component and formulation extension paths`

Every commit after the protocol introduction must leave the full suite green.
