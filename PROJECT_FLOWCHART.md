# cvxopf software architecture

This diagram records the software structure implemented by API hardening and
Milestones 16+, 17, and 19. The first diagram follows one OPF build; the M17
controller shown below composes those same public build, solve, and result
boundaries without moving device or network equations into orchestration.

```mermaid
flowchart TD
    user["<b>User inputs</b><br/>MATPOWER case · options · device models<br/>load and availability time series · Δt"]

    subgraph public_api["Public API and validation"]
        api["<b>Build API</b><br/>build_opf · build_opf_multistep"]
        validation["<b>Common validation</b><br/>finite positive Δt · horizon shapes<br/>formulation and device inputs"]
        dispatch{"<b>Select formulation</b>"}
    end

    subgraph formulations["Formulation-local problem builders"]
        direction LR
        ac["<b>AC</b><br/>voltage and angle variables<br/>nonlinear P/Q network equations<br/>one P and one Q balance<br/>two-terminal branch flows and thermal limits"]
        ldc["<b>Lossy DC</b><br/>branch-flow variables<br/>convex quadratic loss penalty<br/>nodal P balances"]
        sn["<b>Single-node DC</b><br/>copper-plate model<br/>one aggregate P balance"]
    end

    subgraph shared["Shared typed component assembly"]
        requests["<b>Component requests</b><br/>ordered adapter and device collections"]
        prepare["<b>Prepare and validate once</b><br/>structurally read-only prepared data"]
        registry["<b>Capability registry</b><br/>active · null · unsupported by formulation"]
        step["<b>Per-step assembly</b><br/>variable specifications · injections<br/>constraints · stage-cost rates · reporting expressions"]
        scale["<b>Injection scaling contract</b><br/>validate and bind each component-created<br/>1/baseMVA parameter exactly once"]
        horizon["<b>Once-per-horizon assembly</b><br/>coupling constraints · terminal costs<br/>horizon reporting expressions"]
        publish["<b>Publish build state</b><br/>collision-checked compatibility fields<br/>and generic component expressions"]
    end

    subgraph devices["Component-owned mathematical models"]
        direction LR
        gen["<b>Dispatchable generation</b><br/>active and reactive dispatch<br/>operating limits · production cost"]
        storage["<b>Storage</b><br/>active and reactive power · SoC<br/>cycling cost · horizon coupling<br/>terminal constraints and costs"]
        nd["<b>Nondispatchable generation</b><br/>availability-limited active power<br/>inverter reactive support<br/>curtailment reporting"]
        load["<b>Loads</b><br/>signed active/reactive demand · stable identity<br/>optional served fraction · VOLL cost<br/>ENS and service reporting"]
        hvdc["<b>HVDC</b><br/>two-terminal active-power transfer<br/>bounds · losses · transfer cost<br/>explicit single-node null model"]
    end

    compose["<b>Compose optimization problem</b><br/>formulation-local network physics<br/>+ typed component contributions"]
    objective["<b>Formulation objective assembly</b><br/>shared Δt integration of stage-cost rates<br/>+ once-per-horizon terminal costs"]
    build["<b>OPFBuild</b><br/>CVXPY problem · variables · data<br/>expressions · formulation metadata"]

    subgraph solve_results["Solve and stable results"]
        solver{"<b>Select solver</b><br/>according to formulation"}
        ipopt["<b>IPOPT</b><br/>nonconvex AC / DNLP"]
        convex["<b>Convex solver</b><br/>lossy and single-node DC"]
        extract["<b>Extract results</b><br/>schema initialized from built model"]
        success["<b>Usable core primal solution</b><br/>populate available arrays and scalar costs"]
        unsuccessful["<b>No usable core primal solution</b><br/>retain known exogenous inputs and device keys<br/>primal arrays = None · scalar costs = NaN"]
        results["<b>Stable results</b><br/>formulation-aware result dictionary"]
    end

    user --> api --> validation --> dispatch
    dispatch --> ac
    dispatch --> ldc
    dispatch --> sn

    validation --> requests
    requests --> registry
    registry -->|active| prepare --> step
    registry -->|null: retain explicit empty prepared entry| step
    registry -. selects hooks .-> gen
    registry -. selects hooks .-> storage
    registry -. selects hooks .-> nd
    registry -. selects hooks .-> load
    registry -. selects hooks .-> hvdc
    gen --> step
    storage --> step
    nd --> step
    load --> step
    hvdc --> step
    step --> scale --> horizon --> publish

    ac --> compose
    ldc --> compose
    sn --> compose
    scale --> compose
    horizon --> compose
    compose --> objective --> build
    publish --> build

    build --> solver
    solver --> ipopt
    solver --> convex
    ipopt --> extract
    convex --> extract
    extract --> success
    extract --> unsuccessful
    success --> results
    unsuccessful --> results

    classDef public fill:#e8f1ff,stroke:#2563a6,color:#102a43
    classDef formulation fill:#fff4dd,stroke:#b7791f,color:#4a2c0a
    classDef assembly fill:#e8f8ef,stroke:#27864b,color:#123d24
    classDef component fill:#f2eafe,stroke:#7650a8,color:#321c52
    classDef output fill:#fdecec,stroke:#b74b4b,color:#551d1d

    class api,validation,dispatch public
    class ac,ldc,sn,compose,objective formulation
    class requests,prepare,registry,step,scale,horizon,publish assembly
    class gen,storage,nd,load,hvdc component
    class build,solver,ipopt,convex,extract,success,unsuccessful,results output
```

## Component contribution lifecycle

The shared assembler coordinates the lifecycle, while component modules retain
their device mathematics and formulation builders retain network physics.

```mermaid
sequenceDiagram
    participant F as Formulation builder
    participant A as Shared assembler
    participant C as Typed component adapter
    participant M as Component model
    participant B as OPFBuild

    F->>A: Component requests + preparation context
        A->>C: Read formulation binding: active, null, or unsupported
    alt Active capability
        C->>M: Validate and prepare device collection
        M-->>A: Structurally read-only prepared data
    else Null capability
        A->>A: Retain explicit empty component contribution
    else Unsupported capability
        A-->>F: Reject the supplied component collection
    end

    loop Once per time step for active components
        F->>A: Step context + formulation network state
        A->>C: Request variable specifications
        A->>A: Create builder-owned CVXPY variables
        A->>C: Request injections, constraints, costs, and expressions
        C->>M: Evaluate component mathematics
        M-->>A: Typed step contribution
        A->>A: Bind injection scaling exactly once
        A-->>F: Variables + P/Q injections + constraints + costs + expressions
        F->>F: Form formulation-local network balances
    end

    F->>A: Horizon context + component variable histories
    A->>C: Invoke horizon hook exactly once, including T = 1
    C->>M: Build coupling constraints, terminal costs, and expressions
    M-->>A: Typed horizon contribution
    A-->>F: Coupling constraints + terminal costs + horizon expressions
    F->>F: Integrate stage-cost rates using Δt
    F->>F: Construct the CVXPY objective, constraints, and problem
    F->>A: Publish component variables, metadata, and expressions
    A-->>F: Collision-checked compatibility state
    F->>B: Package problem and published state as OPFBuild
```

## Architectural invariants

- Components contribute signed network injections; formulations own network
  equations and the unique power balances.
- Component adapters expose declarative variable specifications derived from
  component-owned models; shared assembly instantiates the `cvxpy.Variable`
  objects.
- Prepared component mappings are defensively copied and read-only; contained
  NumPy arrays and CVXPY objects are not deeply frozen.
- Device models construct per-unit injection expressions using unbound
  inverse-base `cvxpy.Parameter` objects; shared assembly validates and binds
  each parameter exactly once.
- Every configured component has an explicit formulation capability. A null
  capability is intentional model elimination, not accidental omission.
- HVDC is active in AC and lossy DC and explicitly null in single-node DC,
  where both terminals and the intervening network have been collapsed.
- Stage-cost rates are integrated using the time-step duration. Terminal costs
  are applied once per horizon and remain outside that time integral.
- Per-step reporting expressions become ordered histories in multistep builds;
  horizon expressions are published once and are not time-scaled.
- `OPFBuild` is the boundary between model construction and solving; result
  extraction does not reconstruct the model.
- Result schemas are determined from the built model, so unsuccessful solves
  retain configured-device keys with explicit unavailable values.
- A repository-supported component is registered once for generic mathematical
  assembly across formulations. Exposing a new component through the public API
  still requires ordinary input and parser plumbing; this is a closed-world
  internal architecture, not a dynamic plugin interface.

## Hierarchical controller

```mermaid
flowchart LR
    inputs["<b>HierarchicalInputs</b><br/>case · identity-aligned devices<br/>full-horizon time series"]
    policy["<b>HierarchicalPolicy</b><br/>outer replanning · AC terminal policy<br/>initialization and recovery"]
    outer["<b>Long-horizon lossy DC</b><br/>convex energy plan<br/>battery SoC signposts"]
    outer_gate{"<b>Accepted outer primal?</b><br/>status · finite fields<br/>DC residuals"}
    inner["<b>Short-horizon AC</b><br/>nonlinear network realization<br/>target-conditioned by SoC only"]
    gate{"<b>Accepted-primal gate</b><br/>status · identity · finite fields<br/>physics residuals"}
    recovery{"<b>Recovery slot remains?</b>"}
    execute["<b>Execute first action</b><br/>advance realized SoC once"]
    terminate["<b>Terminate trajectory</b><br/>retain failure audit<br/>do not advance state"]
    audit["<b>HierarchicalResult</b><br/>plans · every AC attempt · starts<br/>executed intervals · accounting"]

    inputs --> outer
    policy --> outer
    outer --> outer_gate
    outer_gate -->|accepted: device-ID-aligned SoC boundary| inner
    outer_gate -->|unsuccessful: no AC window| terminate
    policy --> inner
    inner --> gate
    gate -->|accepted controlling solve| execute
    gate -->|unsuccessful attempt| recovery
    recovery -->|yes: next declared slot| inner
    recovery -->|no: sequence exhausted| terminate
    execute -->|next controller iteration| outer
    outer --> audit
    inner --> audit
    execute --> audit
    terminate --> audit
```

The outer layer communicates battery energy states, not dispatch setpoints.
Each AC window therefore re-optimizes generation, reactive power, storage
power, and other device behavior under the modeled nonlinear AC physics. Only
the first action from an accepted target-conditioned AC solve is executed.
An unsuccessful outer plan terminates before an AC window is registered. An
unsuccessful AC attempt advances only to the next policy-declared recovery
slot; exhausting those slots terminates without advancing state. Failed
attempts remain distinct audit records and are never silently relaxed or used
as control actions.

M17 intentionally fixes this reviewed `lossy_dc` to `ac` workflow. Future
selectable formulations and additional hierarchy layers belong to M21. Both
layers continue to use the ordinary public OPF builders and the typed component
assembly shown above.
