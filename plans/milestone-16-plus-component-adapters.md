# Milestone 16+ — Typed component adapters and uniform assembly

**Status:** planned
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
- **Prepared data is immutable build input.** Preparation validates and
  vectorizes once. Per-step assembly does not reinterpret user dataclasses.
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

Introduce a private typed adapter, provisionally:

```python
@dataclass(frozen=True)
class ComponentAdapter:
    name: str
    prepare: PrepareHook
    metadata: MetadataHook
    variables: VariableSpecHook
    injections: InjectionHook
    operating_constraints: OperatingHook
    coupling_constraints: CouplingHook
    network_constraints: NetworkHook | None = None
    step_cost: StepCostHook | None = None
    terminal_cost: TerminalCostHook | None = None
```

The exact callable signatures should be settled with `Protocol` definitions
and type checking before migration. The important contract is the data flow,
not these provisional field names.

An adapter describes how a formulation assembles one component collection. It
does not replace the user-facing dataclasses and does not absorb the component
math currently living in `generator.py`, `storage.py`,
`nondispatchable.py`, and `hvdc.py`.

### 3.2 Prepared component state

Use a typed internal container rather than passing an ever-growing flat
dictionary through the assembly layer:

```python
@dataclass(frozen=True)
class PreparedComponent:
    adapter: ComponentAdapter
    units: Sequence[object]
    data: Mapping[str, object]
```

`OPFBuild.data` may retain its current flat public/internal compatibility
schema during this milestone. The typed container is the assembly contract;
flattening is an explicit publication step.

### 3.3 Per-step contribution

Normalize the output of per-step assembly:

```python
@dataclass
class StepContribution:
    variables: dict[str, cp.Variable]
    p_injection: cp.Expression | None
    q_injection: cp.Expression | None
    constraints: list[cp.Constraint]
    network_constraints: list[cp.Constraint]
    cost: cp.Expression | None
    expressions: dict[str, cp.Expression]
```

Scaling parameters, when required, should be bound inside the common assembly
helper from a supplied `baseMVA`, rather than returned to three formulation
builders and assigned independently.

### 3.4 Horizon contribution

After the per-step loop, the common orchestration layer passes the collected
variables to every active component's coupling hook, including for a
single-step horizon represented by a one-element list. Storage terminal cost
is then composed exactly once as a horizon contribution.

This removes the current semantic difference in which single-step builders
call only storage coupling while multistep builders call every component
coupling hook.

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

- Record the exact current `OPFBuild.variables`, `.data`, and `.expressions`
  schemas for every formulation, single/multistep, and component combination.
- Add numerical equivalence fixtures for mixed-component builds.
- Add static assertions that component modules create no `cp.Variable`.
- Decide the minimum Python typing target supported by the package.
- Write the typed protocols before moving behavior.

### Stage 1 — Contribution value objects

- Add typed injection, step, and horizon contribution containers.
- Adapt the existing component functions behind adapters without changing the
  three formulation builders.
- Centralize binding of engineering-unit scaling parameters.
- Prove DCP status and expression units are unchanged.

### Stage 2 — Generators and nondispatchable pilot

- Migrate generators first because they exercise real/reactive injections,
  operating constraints, network constraints, and cost delegation.
- Migrate nondispatchable second because it exercises time-varying prepared
  data, AC/DC channel differences, and absence of cost.
- Keep compatibility wrappers around current module-level functions until all
  builders migrate.

### Stage 3 — Storage horizon path

- Migrate storage operating constraints, injections, cycling cost, SoC
  coupling, hard terminal boundary, and soft terminal cost.
- Ensure terminal cost remains a once-per-horizon contribution.
- Route both single- and multistep builds through the same one-element/list
  horizon interface.
- Preserve the M12 result and metadata contracts exactly.

### Stage 4 — HVDC and explicit null capability

- Migrate the two-terminal injection and per-step box/loss model.
- Represent HVDC as explicitly null in `singlenode_dc`.
- Preserve the AC/lossy-DC signed-injection convention.
- Do not move full-lossy or sign-switching physics from Milestone 15 into this
  refactor.

### Stage 5 — Shared orchestration

- Replace repeated per-component blocks in all three builders with the common
  assembler.
- Delete compatibility wrappers and dead orchestration only after all
  equivalence tests pass.
- Keep network equations visibly formulation-local.

### Stage 6 — Documentation and extension proof

- Document how to add a new component and how to add a new formulation.
- Implement a test-only toy component adapter to prove that a new memoryless
  real-power component can compose without edits to each builder.
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

- A test-only component can be registered and assembled without modifying
  `ac_problem.py`, `dc_problem.py`, and `singlenode_dc_problem.py`
  independently.
- A memoryless component returns no horizon constraints without special-case
  code in a formulation builder.

### Gate 6 — regression

- Ruff passes.
- Type checking passes.
- Full test suite passes.
- Public examples produce unchanged results.

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

