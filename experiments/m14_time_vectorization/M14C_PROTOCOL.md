# M14c vectorized lossy-DC protocol

## Status and authority

M14c is open. It implements the first production formulation on the completed
M14b time-last assembly contract and is the remaining blocker for resuming the
Case118 annual S4 outer solve. The retained stepwise/CPP formulation remains
available and remains the default; callers select the new path explicitly with
`temporal_assembly="vectorized"`.

M14c is authorized by:

- the immutable M14a legacy characterization;
- the M14a.1 formulation-owned bound decisions;
- the completed M14b assembly and result-projection contract; and
- `M14B_COMPONENT_BOX_RESULTS.json`, SHA-256
  `2bdf5eda5d545a49e66afd01eeca7083bd3d81d54dfb5604ba62667c270815bf`.

The combined M14a.1/M14b registry authorizes the complete lossy-DC leaf-bound
set. M14a.1 authorizes the formulation-owned dispatchable-generation and
branch-flow boxes with authority `m14a1_qualified`. M14b authorizes the tested
storage real-power and SoC, nondispatchable real-power, load-shed-fraction, and
HVDC from-terminal-power component boxes with authority `m14b_qualified`. All
coupled constraints and equations remain explicit. No M14c result authorizes
AC or single-node migration.

## Production formulation contract

The vectorized lossy-DC builder constructs one CVXPY object per logical
horizon variable or expression, with time on the last axis. In particular:

- `Pg` has shape `(n_generator, T)`;
- `p_flows` has shape `(n_branch, T)`;
- interval component variables and expressions use `(n_device, T)`;
- storage SoC uses `(n_storage, T + 1)`; and
- public inputs and results remain time first through the frozen M14b
  projection registry.

The formulation owns batched branch-flow conservation, nodal active-power
balance, branch limits, and the resistance-weighted loss proxy. It consumes
one aggregated vectorized component contribution containing affine nodal
injections, operating constraints, temporal constraints, stage-cost rates,
terminal costs, variables, expressions, and result projections. It must not
call scalar component hooks once per interval or reconstruct a length-`T`
CVXPY object list for compatibility.

The objective remains exactly:

```text
delta * sum_t(component_stage_cost_rate_t + dc_loss_cost_rate_t)
+ component_terminal_cost
```

Dispatchable generation cost is already part of the aggregated component stage
cost and is not added separately. Stage costs are integrated exactly once and
horizon costs are added exactly once. Units, device identities, storage
boundary indexing, terminal policies, HVDC sign/loss conventions, load service
and energy-not-served accounting, nondispatchable availability, and the public
result schema remain unchanged.

Every vectorized convex build and solve records and enforces SCIPY
canonicalization. A caller-supplied conflicting backend is rejected. The
stepwise path continues to use CPP, and backend differences are explicit in
all structural and performance comparisons.

## Verification and acceptance

### Frozen numerical tolerances

The M14c mathematical-equivalence gate uses an absolute tolerance of `2e-5`
and a relative tolerance of `1e-9` for objectives, declared component costs,
and uniquely determined numeric result fields. Independent physical and
accounting residuals must not exceed `1e-5`. Exact identities, shapes, schemas,
boundary indices, classifications, and provenance fields admit no tolerance.
Nonunique coordinates may differ only when both solutions independently pass
the complete feasibility, accounting, and objective gates and the field is
explicitly classified as nonunique.

These are M14c stepwise-versus-vectorized mathematical tolerances. They do not
replace the S4 public-controller-versus-streaming seam tolerance of `1e-9` or
any frozen S4 acceptance tolerance.

### Frozen bounded-scaling resources and stop rules

The execution sequence is fixed. Unit, `T=1`, and short-horizon mathematical
equivalence gates run first on the M14 branch. The reviewed implementation is
then integrated into `big-experiment`. Only there does the bounded Case118
ladder run, using the deterministic first 24, 168, and 720 hourly rows of the
frozen S4 load, reactive-load, and nondispatchable-availability inputs. Each
prefix retains the exact S4 network, device fleet and identities, one-hour
timestep, policy, solver configuration, and construction options; the frozen
terminal policy is applied at that prefix's final boundary. No separate M14
scaling fixture is authorized.

Each prefix runs in order in a fresh supervised worker with current child RSS
sampled every second. The limits are:

| Horizon | Child RSS | Worker wall time | Total supervisor wall time |
|---:|---:|---:|---:|
| 24 | 16,384 MiB | 600 s | 900 s |
| 168 | 16,384 MiB | 1,800 s | 2,400 s |
| 720 | 16,384 MiB | 3,600 s | 4,500 s |

Construction, canonicalization/solve, audit, serialization, and release phases
are retained separately where observable. RSS has priority over worker wall
time and total wall time as the primary classification when triggers coincide,
while every observed trigger is retained. Missing RSS evidence is a resource-
measurement failure. Construction failure, solver exception, certified
infeasibility, unusable primal, residual rejection, provenance mismatch, and
resource termination remain distinct classifications.

The ordered ladder stops after the first point that is not fully accepted; later
horizons are not attempted automatically. No automatic retry, limit increase,
or backend substitution is permitted. A reviewed rerun uses a fresh output
directory and is recorded as a separate attempt.

### Prefix-ladder implementation checkpoint

The supervised ladder is implemented by
`run_m14c_prefix_ladder.py`, with typed prefix construction and limits in
`m14c_prefix_fixture.py` and independent reconstruction in
`m14c_prefix_analysis.py`. The authoritative default output root is
`experiments/m14_time_vectorization/results/m14c_case118_prefix_ladder_conditioned`, which
must not exist before launch. Execution requires a clean committed descendant
of integration commit `360aaf5f75d7bf2d4b2ec1672d319af90bd8626e`, the
unchanged pre-ladder integration record, and a passing retained S4 seam gate.

The root retains immutable `execution-context.json` and
`outer-equivalence.json`, an atomic `ladder-progress.json`, and, when the
supervisor reaches a catchable terminal state, immutable `ladder-result.json`.
Each attempted point uses a fresh `prefix-NNNN/` worker directory containing
its exact execution context, worker log, and immutable supervision record.
The worker result can be absent after launch failure or supervisor
interruption. A verified outer-plan archive is written only after an accepted
solve, but the final supervision may still be nonaccepted if a resource or
interruption event occurs after that publication. Source,
integration lineage, annual fixture hashes, prefix input/scenario hashes,
policy, solve configuration, representation, backend, software/machine
context, resource policy, phase samples, and artifact hashes are retained.
The progress and terminal registries record the ordered attempted and accepted
horizons and the first stopping horizon. Catchable parent interruption
terminates and joins the worker before retaining an explicit, nonpromotable
partial lifecycle. An unfinalized progress root can be reconstructed as a
partial scientific record but is never resumed or promoted, including the
narrow state where all three prefixes are accepted but the immutable terminal
record has not yet been published. If interruption occurs while terminating a
worker for an already observed resource crossing, the first frozen resource
trigger remains the primary classification and the interruption is retained
as auxiliary evidence. `worker_launch_failure` is reserved for failure of the
fresh-process launch itself; ordinary active-marker, sampling, termination, or
wait errors after launch are retained separately as `supervisor_failure`
unless an already observed resource trigger has priority.

Execution sources and analysis sources have separate fingerprints: the former
binds every module that can affect construction, solve, supervision, or
archives, while the latter adds the analyzer itself. Analyzer-only corrections
therefore remain explicit without misrepresenting the worker source identity.

The analyzer reproduces every accepted audit and terminal signpost from the
archived public result, checks structural counts and the complete
worker/supervisor provenance chain, and validates retained abnormal outcomes
without relabeling them. It may immutably promote only a complete accepted
ladder as `M14C_PREFIX_LADDER_RESULTS.json`. Promotion records
`qualified_for_annual_review=true` but deliberately retains
`annual_execution_authorized=false`; review and a separate authority update
remain required before S4 can launch. Partial analysis remains in the ignored
execution directory and cannot occupy the authoritative tracked destination.

The 2026-08-31 S4 conditioning amendment adds `c2 = 1e-4` to every
dispatchable generator while preserving inherited `c0` and `c1`. This changes
the frozen input/scenario hashes and supersedes the earlier unconditioned
prefix result. The ordered ladder described here must therefore be rerun from
the conditioned fixture; the prior accepted run remains historical evidence
only and cannot authorize annual execution. The exact diagnostic runner,
tested alternatives, selection criterion, and bounded economic perturbation
are frozen in `M14C_GENERATOR_CONDITIONING_PROTOCOL.md`; the amendment
conditions generator dispatch and does not assert a unique storage or
branch-flow trajectory.

M14c advances only after all of the following pass:

1. structural tests for time-last network variables, sparse operators, leaf
   boxes, component aggregation, objective composition, identities, and
   boundary indexing;
2. `T=1` equivalence with the existing lossy-DC single-step mathematics while
   preserving multistep result axes;
3. short-horizon stepwise-versus-vectorized equivalence for objective,
   component costs, complete public results, and independently reconstructed
   physical and accounting residuals, allowing only documented nonunique
   optimal coordinates;
4. stable infeasible, solver-failure, and unusable-primal schemas and
   classifications;
5. coverage of dispatchable generation, storage terminal modes, fixed and
   sheddable loads, nondispatchable generation, and conditional HVDC paths;
6. explicit SCIPY provenance on vectorized solves and CPP provenance on legacy
   comparison solves;
7. the 24-, 168-, and 720-step Case118 scaling ladder within the frozen
   resource limits above;
8. the non-promotional `stepwise + CPP` profile of those same frozen prefixes,
   compared with the accepted `vectorized + SCIPY` artifacts under
   `M14C_PROFILING_PROTOCOL.md`; and
9. strict mypy, Ruff, the complete test suite, and `git diff --check`.

Independent audits remain authoritative for scientific acceptance. Source and
canonical object counts are characterized by representation rather than
required to match the stepwise graph.

## Annual M14c/S4 gate

After the branch-local unit, `T=1`, and short-horizon gates pass from a clean
committed implementation, the reviewed M14c commit is integrated into
`big-experiment`, where the tracked S4 fixture and supervisor live. The
integration checkpoint must record the exact M14c and `big-experiment` source
commits, rerun those branch-local gates, verify the unchanged S4 fixture,
policy, solver, scenario, and provenance hashes, and then run the ordered
frozen-S4 vectorized prefix ladder. Before annual authority is updated, the
accepted vectorized artifacts are compared with fresh supervised
`stepwise + CPP` executions of the same prefixes under
`M14C_PROFILING_PROTOCOL.md`. The M14 branch does not recreate a parallel S4
fixture or supervisor.

The first authorized 8,760-step execution then uses the exact frozen Case118
S4 fixture, supervisor, provenance, archive, equivalence, and analysis
contract. It reuses S4's frozen limits rather than introducing a newly reviewed
envelope: 16,384 MiB child RSS, 7,200 seconds worker wall time, 10,800 seconds
total supervisor wall time, and one-second polling. One accepted execution
serves simultaneously as M14c's terminal scale gate and the candidate
authoritative S4 outer result; it is not repeated ceremonially.

Two distinct equivalence gates apply. M14c compares the stepwise and vectorized
lossy-DC formulations for mathematical equivalence. After integration, S4
separately compares the public hierarchical controller's retained outer plan
with the experiment streaming seam on its characterized 24-hour fixture. A
pass of either gate does not substitute for the other.

Failure remains explicit construction, canonicalization, resource, solver, or
audit evidence and is never selectively promoted. A retry requires ordinary
review and a fresh output directory.

## Delivery order

1. Implement and test the vectorized lossy-DC network graph and typed build.
2. Register the production vectorized component bindings using the frozen M14b
   representation decisions.
3. Enable the explicit public `temporal_assembly="vectorized"` dispatch and
   preserve the stepwise default.
4. Complete structural, `T=1`, short-horizon, failure, and component-matrix
   equivalence gates on the M14 branch.
5. Integrate the reviewed implementation into `big-experiment` and verify the
   unchanged S4 fixture and execution contract.
6. Run the ordered 24-, 168-, and 720-hour frozen-S4 vectorized prefix ladder
   and freeze its evidence.
7. Run and review the non-promotional `stepwise + CPP` profile against those
   accepted prefix artifacts.
8. Authorize the single annual M14c/S4 execution only after review of all
   cheaper gates.

## Non-goals

M14c does not vectorize AC or single-node DC, alter OPF mathematics, choose a
temporal mode automatically, remove the stepwise path, weaken any M17/S4 audit,
or claim a runtime or RSS improvement before measurement. AC and single-node
work remains M14d.
