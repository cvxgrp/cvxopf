# Milestone 14 — Time-vectorized multistep formulations

## Status

**Required next milestone.** The Case118 annual hierarchy experiment is paused
at S4 until the annual lossy-DC outer problem passes the M14 construction,
canonicalization, solve, equivalence, and resource gates below.

## Motivation

`build_opf_multistep()` currently constructs one collection of CVXPY variables,
constraints, injections, and cost expressions per time step. This preserves a
clear single-step component contract, but the Python object graph and CVXPY
canonicalization graph grow poorly with horizon length.

The Case118 S4 annual outer experiment made that limitation observable. Three
annual workers reached the same late construction/canonicalization phase
and received `SIGKILL` after roughly 8.5 minutes. The detached attempt ruled out
the Codex command resource group: macOS recorded

```text
memorystatus: killing largest compressed process python3.11 [...] 198602 MB
```

while the experiment supervisor had sampled a peak resident set of 11,973 MiB
and had triggered neither its 16 GiB RSS limit nor its wall-time limits. The
machine has 36 GiB of physical memory. Repeating the same per-step expression
graph with a larger supervisor allowance is therefore not a credible annual
execution strategy.

M14 replaces repeated time-step expression construction with time-indexed
matrix expressions and sparse temporal operators while preserving the
scientific formulation and public result contract.

## Scientific and compatibility boundary

Time vectorization is an implementation transformation. It must not silently
change:

- engineering units, signs, device identities, or row ordering;
- the feasible set, objective terms, or terminal-policy semantics;
- branch-rating treatment, loss proxy, or nodal conservation;
- storage recurrence, initial state, or terminal state;
- unsuccessful-solve classification and retained input metadata;
- single-step behavior or intentional multistep `T=1` behavior;
- extracted result keys, values, shapes, or time ordering; or
- the accepted M17 hierarchy and audit rules.

Numerical agreement is evaluated using predeclared absolute and normalized
residual tolerances appropriate to each quantity. Solver trajectories and raw
floating-point serialization are not required to be byte-identical.

## Scope and sequence

### M14a — Freeze the baseline

Before changing construction:

1. Characterize the existing variable, constraint, parameter, expression,
   result, and failure schemas for all three formulations.
2. Record scalar-variable, equality, inequality, and cone dimensions for
   representative component combinations and horizons.
3. Retain matched short-horizon objective, trajectory, and residual fixtures.
4. Measure construction, canonicalization, solve, extraction, peak RSS, and
   artifact size over an increasing horizon ladder.
5. Preserve the failed S4 attempts as resource-boundary evidence; do not
   reinterpret them as solver infeasibility.

### M14b — Vectorized horizon assembly contract

Introduce an internal horizon-level assembly path with:

- variables whose leading dimension is time;
- batched component injections, operating sets, and stage costs;
- sparse difference/selection operators for temporal coupling;
- vectorized integration of stage costs and reported component costs;
- stable device axes and explicit time axes; and
- a deliberate compatibility adapter for the existing public
  `OPFBuild.variables`, expressions, and extraction contracts.

The compatibility adapter must be designed and tested explicitly. It must not
materialize thousands of new CVXPY objects merely to recreate the old internal
list representation. If an internal/public representation must change, freeze
that change through a separately reviewed typed contract before implementation.

### M14c — Vectorized lossy DC

Implement the annual-experiment blocker first:

- time-by-branch flow variables;
- batched nodal active-power balance;
- batched branch limits and resistance-weighted loss proxy;
- vectorized dispatchable-generation costs and bounds;
- vectorized fixed/sheddable load channels;
- vectorized nondispatchable availability and curtailment reporting;
- vectorized storage power, SoC recurrence, cycling cost, and terminal policy;
- vectorized HVDC boxes, injections, costs, and supported loss semantics; and
- exact preservation of the lossy-DC audit and result schema.

The implementation should use sparse linear operators where they materially
reduce graph size. Replacing a left-deep Python sum with a balanced sum is a
useful local correction but does not, by itself, complete this stage.

### M14d — Single-node DC and AC

Apply the same horizon contract to single-node DC and AC after the lossy-DC
path is stable. AC requires an additional design gate because IPOPT starting
coordinates, original-variable names, canonicalization-added coordinates, and
the M17 causal initialization audit are part of the accepted public contract.

Annual S4 may resume after M14c passes its gates; it does not need to wait for
AC time vectorization. M14 as a repository milestone is complete only after
the declared single-node and AC scope also passes, or after a reviewed plan
revision explicitly narrows that scope.

## Verification ladder

For each implemented formulation:

1. **Structural unit tests:** dimensions, axes, identities, bounds, and sparse
   temporal operators.
2. **`T=1` tests:** intentional multistep results agree with the existing
   single-step formulation while retaining multistep shapes.
3. **Short-horizon equivalence:** old and vectorized builders agree on
   objectives, results, component costs, and independent residual audits.
4. **Failure equivalence:** infeasible, solver-failure, and unusable-primal
   records preserve their stable schemas and classifications.
5. **Component matrix:** generators, storage, loads/shedding,
   nondispatchable generation, and conditional HVDC paths.
6. **Scaling ladder:** increasing Case118 horizons through 24, 168, 720, and
   8,760 steps, stopping safely at predeclared resource boundaries.
7. **Hierarchy regression:** M17 focused tests and the retained S7 equivalence
   gate remain clean.

Strict mypy, Ruff, the complete test suite, and `git diff --check` remain
required repository gates.

## Annual S4 resumption gate

The Case118 `big-experiment` branch remains on hold until all of the following
are true:

1. the exact frozen S4 annual inputs build through the vectorized lossy-DC
   path;
2. the 24-hour public-versus-streaming outer equivalence gate passes;
3. the vectorized annual model retains the expected formulation identity,
   dimensions, storage identities, terminal target, and provenance hashes;
4. construction, canonicalization, and solve remain within a newly reviewed
   memory and wall-time envelope;
5. no OS memory-pressure termination occurs;
6. the independently reconstructed outer audit is accepted; and
7. execution occurs from a clean committed source with a fresh output
   directory.

The new annual result must identify the vectorized execution commit. It is not
a continuation of any failed S4 worker.

## Non-goals

M14 does not:

- weaken the annual experiment or M17 acceptance gates;
- introduce approximate temporal aggregation;
- change the one-hour S4 time resolution;
- claim that vectorization alone makes direct annual AC OPF practical;
- redesign the hierarchy or shard policy;
- add uncertainty, contingencies, or alternative storage physics; or
- erase the scientific value of the observed memory boundary.

## Completion criteria

M14 is complete when the reviewed vectorized paths preserve the frozen
mathematics and result contracts, pass the verification ladder, and demonstrate
materially improved time-axis construction/canonicalization scaling. M14c is
complete—and S4 may resume—when the exact 8,760-step Case118 lossy-DC outer
problem clears the annual resumption gate.
