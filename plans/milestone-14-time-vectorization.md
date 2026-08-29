# Milestone 14 — Time-vectorized multistep formulations

## Status

**In progress; M14c branch-local implementation complete and integration under
review.** The frozen
legacy Case9 and Case118 scaling ladders completed, and the formulation-
specific leaf-bound gate passed. The typed horizon, one-call assembly,
aggregation/publication, and public-result projection slices are implemented;
the reusable seven-gate component-box harness and fresh-process runner are also
complete. The authoritative component-box record qualified all nine tested
lossy-DC and single-node decisions and freezes the assembly contract for M14c.
The M14a baseline is bound to:

- execution commit `1dd5e36dcae5ad9c8176b1d1202f1055acf95c03`;
- analyzer commit `457ec9200050eae2a60dd470a131155a0ddaa53f`; and
- promoted-record SHA-256
  `44f6f0b9f3c3b51621f6952dd2efa1eb2d169757895acaba75bba6f04f5edb53`.

The immutable M14a.1 result is bound to execution commit
`1f45651fa8450cdfc50e55dd024eb1fbcc4b7c06` and SHA-256
`6efcb1c077fd8201435faf6df11512d4174b0c24edd06c82ffccee3a131a4614`.
All paired and binding-probe gates passed. Lossy DC selects leaf bounds for
`Pg` and `p_flows`; single-node DC selects them for `Pg`. AC records isolated
compatibility for `Pg`, `Qg`, and `v` but retains explicit inequalities because
the isolated fixture does not retire the production lifted-DNLP and terminal-
policy risk.

The immutable M14b component-box result is bound to execution commit
`028b46e2f0af16fbff90a9bc991770f0267280a5`, source fingerprint
`45e2e91e95594b3418065c256ef476c70d14f7f679d1606a71f2fc34c57ac03b`, and
promoted-record SHA-256
`2bdf5eda5d545a49e66afd01eeca7083bd3d81d54dfb5604ba62667c270815bf`;
the result was promoted in commit
`252b8c8d0cf243c4661c7aa0700f28f039afb8d2`.
All seven formulation-local pairs passed and all nine tested component-box
decisions select leaf bounds. The result is limited to the tested lossy-DC and
single-node boxes under SCIPY canonicalization and CLARABEL; it neither
authorizes AC migration nor establishes a runtime or RSS advantage.

The reviewed branch-local M14c implementation is commit
`0ef895b5e665fdb3a8fffab60292329ed22fd32b`. It is being integrated into
`big-experiment` from parent commit
`6a9cd130b7817f2ac6fbca2ce0de634da8967b25`. The integration retains the
unchanged frozen S4 scenario, policy, and solver hashes and explicitly selects
the vectorized lossy-DC path with SCIPY canonicalization. The 24-, 168-, and
720-hour prefix ladder and annual execution remain unexecuted. A reviewed clean
integration commit authorizes only the ordered prefix ladder. Annual execution
remains unauthorized until accepted 24/168/720-hour evidence is reviewed and
the integration authority record is explicitly updated.

The Case118 annual hierarchy experiment remains paused at S4 until the annual
lossy-DC outer problem passes the remaining M14 construction,
canonicalization, solve, equivalence, and resource gates below.

## Motivation

`build_opf_multistep()` currently constructs one collection of CVXPY variables,
constraints, injections, and cost expressions per time step. This preserves a
clear single-step component contract and correctly uses CVXPY's CPP
canonicalization backend, which is preferred for large graphs composed of many
separate expressions and constraints. The existing design is not a backend
selection defect; its Python and CVXPY object graph simply reaches a practical
scaling boundary at sufficiently large horizons.

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

M14 adds a second representation needed for the next scaling regime: compact,
time-vectorized N-dimensional expressions with the time axis last, paired with
CVXPY's SCIPY canonicalization backend as recommended for large sparse and
vectorized problems. It preserves the scientific formulation and public result
contract. The existing stepwise representation remains supported for
compatibility, profiling, and problem classes where it performs better.

## Representation and canonicalization contract

The legacy and vectorized paths are intentionally matched to different CVXPY
canonicalization backends:

| Model representation | Canonicalization backend | Role |
|---|---|---|
| Per-step variables, expressions, and constraints constructed in a Python loop | CPP | Correct existing implementation and equivalence baseline |
| Time-vectorized arrays and N-dimensional expressions | SCIPY | Target M14 implementation and scaling path |

Changing only the backend on the existing loop-built graph is not the M14
intervention. Likewise, vectorizing the model while forcing CPP would ignore
the backend intended for this representation and cannot support general
expressions with dimension greater than two. Cross-combinations may be retained
as diagnostics where CVXPY supports them, but they are not required
authoritative baselines.

The vectorized path passes `canon_backend=cp.SCIPY_CANON_BACKEND` explicitly.
It must not rely on an implicit fallback or treat CVXPY's N-dimensional SCIPY
selection warning as a defect.

### Permanent dual temporal formulations

M14 retains both temporal assembly modes as first-class implementations:

- `stepwise`: the current frame-by-frame construction with one collection of
  CVXPY objects per interval; and
- `vectorized`: one horizon-level construction whose final logical axis is
  time.

These are **temporal assembly modes**, not network formulations. Each must be
selectable with `ac`, `lossy_dc`, and `singlenode_dc` as its implementation
coverage is completed. Selection is explicit and retained in `OPFBuild.data`,
solve provenance, results, benchmarks, and experiment artifacts. There is no
silent horizon-length heuristic that changes assembly mode.

The current `stepwise` behavior remains the public compatibility default during
M14. Changing a default later requires a separately reviewed API decision with
release notes and equivalence evidence. The Case118 S4 annual outer explicitly
selects `vectorized`; short M17/S3-style AC windows may continue to select
`stepwise` unless direct profiling supports a different choice.

Temporal assembly and canonicalization backend are separate recorded choices.
Their expected primary pairings are `stepwise` + CPP and `vectorized` + SCIPY,
but profiling may evaluate other supported pairings. An unsupported pairing,
such as a CPP request for an N-dimensional expression it cannot canonicalize,
must fail validation before solve rather than silently switching backends.

This dual contract is scientifically useful. A three-step nonlinear AC problem
can favor a different assembly/backend pairing from an 8,760-step convex
lossy-DC problem. M14 therefore reports construction, canonicalization, solver,
memory, and numerical behavior by the complete tuple:

```text
(network formulation, temporal assembly mode, canonicalization backend, T)
```

For every **time-varying** per-step variable, parameter, or expression, M14
appends time as the final axis:

| Per-step logical shape | Vectorized logical shape |
|---|---|
| scalar `()` | `(T,)` |
| vector `(n,)` | `(n, T)` |
| matrix `(m, n)` | `(m, n, T)` |

Genuine CVXPY tensors are permitted and expected when a per-step object is
already matrix-shaped. Flattening an intrinsic matrix axis into a two-
dimensional workaround is not the default design; it requires characterization
showing a material advantage while preserving explicit, deterministic index
semantics.

The lifting rule does not apply to static network/device data, incidence or
admittance matrices, integrated horizon-cost scalars, or fixed boundary data.
Storage state is the deliberate boundary exception: storage power has shape
`(n_storage, T)`, while SoC has shape `(n_storage, T + 1)` because it includes
both initial and post-step boundary states.

Existing public time-series inputs and extracted results retain their time-
first shapes `(T, ...)`. Preparation moves the public time axis to the final
internal axis once; extraction moves it back once. Device and network axes,
identity ordering, and intentional multistep `T=1` shapes remain unchanged.

### Constants, parameters, and temporal variability

Axis lifting is determined by a field's declared temporal semantics, not by
whether CVXPY represents it as a `Variable`, `Parameter`, `Constant`, or
implicitly promoted NumPy value. Every prepared model field belongs to one of
three closed temporal classes:

| Temporal class | Meaning | Internal shape rule |
|---|---|---|
| `static` | One value applies to the complete horizon | Retain the native scalar/vector/matrix data; do not allocate a length-`T` copy |
| `interval` | One value may differ for every dispatch interval | Append the final axis `T` |
| `boundary` | One value may differ at every state boundary | Append the final axis `T + 1` |

Static generator limits, voltage bounds, branch ratings, storage ratings and
capacities, cost coefficients, incidence matrices, admittance matrices, and
device identities therefore do **not** become time-expanded arrays. They are
applied to vectorized variables through ordinary linear algebra or explicit
broadcasting. A static device vector with native shape `(n,)` may receive a
singleton-axis view `(n, 1)` in algebraic expressions. Where a CVXPY leaf
attribute requires an array with exactly the variable's dimensions, the same
static vector may be exposed through a zero-stride `np.broadcast_to` view with
logical shape `(n, T)`. It must not be tiled or repeated into a physical
length-`T` copy. The broadcast view changes neither the stored data cardinality
nor its temporal classification.

The initial SoC and terminal target are static boundary conditions with shape
`(n_storage,)`; the modeled SoC trajectory is a boundary variable with shape
`(n_storage, T + 1)`. Time-varying load, renewable availability, and any future
dynamic operating envelopes are interval fields with time last internally.

The classification is explicit and schema-owned. It must not be inferred from
the observed values: an interval input whose values happen to be constant over
one scenario remains an interval field. Conversely, a static generator maximum
must not be expanded merely because it participates in every interval.

Future temperature derating or other time-dependent generator limits can add
an interval `p_max` input with public shape `(T, n_generator)` and internal
shape `(n_generator, T)` without changing the vectorized generator equations.
The existing static `p_max` continues to use native shape `(n_generator,)` and
a singleton-axis broadcast view.

M14 does not promote ordinary model constants to CVXPY `Parameter` objects as
a side effect. That remains M13's re-solve/parameterization scope. Existing
CVXPY parameters retain their ownership and gain a time axis only when their
declared field is interval- or boundary-varying. The same temporal schema must
remain valid if M13 later changes a constant field into a parameterized one.

### Variable attributes and box constraints

CVXPY variable attributes are the preferred candidate representation for
eligible elementwise boxes because they give the canonicalizer direct leaf-
domain information and avoid adding large explicit constraint objects to the
vectorized graph. M14 does not assume that this representation is superior for
every formulation or solver stack; it must pass the formulation-specific
qualification gate below before replacing explicit inequalities.

For a variable with logical shape `native_shape + (T,)` or
`native_shape + (T + 1,)`:

- static scalar bounds remain scalars when CVXPY accepts them;
- static non-scalar bounds use zero-stride `np.broadcast_to` views with exactly
  the variable dimensions;
- interval- or boundary-varying bounds use their complete time-last arrays
  directly; and
- lower and upper faces declare temporal class independently, so a static zero
  face can remain a zero-stride view while the opposite face varies by
  interval or boundary; and
- both faces are validated for finiteness, identity alignment, exact target
  shape, and elementwise ordering before variable construction.

Candidate applications include independent generator active/reactive limits,
bus-voltage boxes, DC storage real-power boxes, nondispatchable
availability/rating boxes, load-shed fractions, and HVDC boxes wherever the
complete feasible set is an elementwise interval. It does not replace coupled
apparent-power circles, network equations, storage recurrence, terminal
obligations, or other non-box constraints.

Implicit leaf bounds do not publish ordinary constraint objects or their dual
values. M14 may use them only where the public contract does not require a
named constraint or its dual. Independent primal audits continue to reconstruct
and check every physical bound from the retained result and authoritative input
arrays.

CVXPY requires non-scalar leaf-bound arrays to have the exact variable
dimensions; it does not accept a singleton `(n, 1)` array as an implicit bound
for `(n, T)`. Preparation and model assembly must avoid a tiled Python-side
copy of static input data. Canonical bound vectors necessarily scale with the
number of bounded scalar variables, however, and M14 measures that canonical
memory separately rather than claiming zero-copy behavior through the complete
canonicalization and solver pipeline. A future M13 parameterized bound may
require a different representation if CVXPY leaf bounds cannot be backed by a
mutable `Parameter`; M14 does not preempt that decision.

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

Optimization equivalence is robust to nonunique optima. Paired stepwise and
vectorized solves must agree within tolerance on accepted objective value,
declared component-cost totals, acceptance audits, terminal obligations, and
state trajectories or other primal quantities only where those quantities are
known or predeclared to be uniquely determined. A coordinate-wise mismatch in
a genuinely nonunique dispatch, voltage, flow, or auxiliary variable is not by
itself a formulation mismatch. Each alternative primal must independently pass
the complete physical residual audit and achieve the accepted optimal objective
and declared cost decomposition. The comparison record identifies which fields
are equality-gated, residual-gated, or classified as nonunique alternatives;
it may not waive a mismatch after inspecting the outcome.

### Representation-aware structural equivalence

Scientific equivalence does not require accidental identity of CVXPY's source
or canonical graph. The following invariants must agree between stepwise and
vectorized formulations:

- physical degrees of freedom and their device/network identities;
- public result dimensions, ordering, and boundary indexing;
- physical equations and feasible-set obligations represented;
- objective terms, integration units, and reconstructed component totals;
- initial, terminal, and cross-step state semantics; and
- independently reconstructed primal residuals and acceptance decisions.

The following are representation-specific characterized quantities and are
not required to equal the legacy values:

- Python/CVXPY variable and expression object counts;
- `len(problem.constraints)` and explicit constraint-object categories;
- leaf-domain versus explicit-bound representation;
- canonical auxiliary-variable and cone-row counts; and
- canonical sparse-matrix dimensions and nonzero counts.

M14 records these quantities separately for every assembly/backend pairing.
Before an authoritative annual run, it freezes the expected **vectorized**
structure and canonical dimensions for the exact S4 fixture. The existing S4
legacy counts—including 6,000 scalar variables, 2,932 scalar equalities, 7,584
explicit scalar inequalities, and 364 constraint objects—remain the stepwise
24-hour characterization; they are not imposed on the vectorized graph.

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
6. Freeze a typed temporal-assembly selector and provenance field without
   changing the existing `stepwise` default.

### M14a.1 — Qualify leaf bounds by formulation

Before broad leaf-bound migration, construct paired vectorized fixtures whose
only intentional difference is explicit box inequalities versus variable
`bounds=` attributes. For lossy DC, single-node DC, and AC separately:

1. compare feasible-set behavior, objective, public results, and independently
   reconstructed residuals;
2. record source-object counts, canonical dimensions/nonzeros, construction
   and canonicalization memory/time, and solver behavior;
3. exercise static and fully time-varying boxes; and
4. retain explicit inequalities for any formulation/component combination
   whose numerical or solver behavior regresses.

This gate explicitly includes the AC/DNLP risk demonstrated previously when a
`Qg` leaf bound changed the canonical structure and a formerly successful solve
failed. A convex-path result does not authorize the same representation for AC.
Leaf-bound selection is therefore formulation- and component-specific and is
retained in structural provenance.

### M14b — Vectorized horizon assembly contract

Introduce an internal horizon-level assembly path with:

- variables and expressions whose final axis is time;
- the scalar-to-vector, vector-to-matrix, and matrix-to-tensor lifting rule
  frozen above;
- batched component injections, operating sets, and stage costs;
- vectorized temporal coupling, using sparse difference/selection operators
  where they are the natural representation;
- vectorized integration of stage costs and reported component costs;
- stable device axes and explicit time axes;
- explicit SCIPY canonicalization for convex vectorized solve paths, while AC
  remains on its separately gated DNLP/IPOPT path; and
- a deliberate compatibility adapter for the existing public
  `OPFBuild.variables`, expressions, and extraction contracts.

Static preparation and metadata hooks remain shared. Every active
component/formulation binding gains a vectorized horizon builder for its
time-varying variables, injections, operating set, network coupling, stage-cost
vector, and reporting expressions. The formulation builder then aggregates the
complete horizon once; it does not call the scalar step builder `T` times.
The existing stepwise hooks and builder remain executable rather than becoming
test-only dead code.

M14b uses the immutable M14a.1 decisions as a closed representation registry,
not as blanket authorization for every component-owned interval. Lossy DC may
use qualified leaf bounds for formulation-owned `Pg` and `p_flows`, and
single-node DC may use them for formulation-owned `Pg`. AC retains its current
production representation: no new AC leaf-bound migration is authorized by
the isolated M14a.1 fixture. In particular, the existing production voltage
leaf attribute is preserved as an established compatibility behavior; it does
not authorize moving `Pg`, `Qg`, or component boxes into leaf attributes.

The following component-owned boxes require focused, formulation-specific
qualification before they may use leaf attributes in a vectorized builder:

| Component box | Required probe | Default until qualified |
|---|---|---|
| Storage real power and SoC in lossy DC and single-node DC | Both power faces, both SoC faces, recurrence, initial state, and equality/shortfall/soft terminal behavior | Explicit inequalities |
| Nondispatchable real power | Zero availability, availability-limited and rating-limited coordinates, and time-varying availability with identity alignment | Explicit inequalities |
| HVDC from-terminal power in lossy DC | Positive-only, negative-only, zero-straddling, degenerate, and time-varying boxes while preserving the selected affine loss coupling | Explicit inequalities |
| Load-shed fraction | Zero-width ineligible entries, active upper faces, time-varying eligibility, served-load reconstruction, and shedding cost | Explicit inequalities |

Lossy DC and single-node DC qualify independently where the component is active,
even when their equations are shared. Single-node HVDC is an intentional null
capability and has no box to qualify. AC component boxes remain explicit for
M14 regardless of convex probe results unless a later production-structure
DNLP gate explicitly changes that decision. AC storage real power belongs to
the coupled `b`/`b_q` apparent-power circle rather than an independent box.
Coupled circles, storage recurrence and terminal constraints, HVDC loss
equalities, and network equations are never candidates for leaf bounds.

These focused probes are small deterministic M14b gates, not new scaling
studies. Each compares explicit and leaf representations through binding
faces, objective/component costs, public results, independent physical
residuals, canonical structure, and solver classification. A box may remain
explicit without blocking vectorization; qualification controls only its
representation.

The authoritative M14b matrix completed all seven pairs and froze leaf bounds
for lossy-DC storage power/SoC, nondispatchable power, load-shed fraction, and
HVDC input power, plus single-node storage power/SoC, nondispatchable power, and
load-shed fraction. The registry records those component decisions with
`m14b_qualified` authority, distinct from the formulation-owned
`m14a1_qualified` dispatchable-generation and branch-flow decisions. The
combined registry, rather than M14b alone, authorizes the complete lossy-DC
leaf-bound set. This closes M14b and opens M14c; AC retains the explicit
component-box decisions above.

The compatibility adapter must be designed and tested explicitly. It must not
materialize thousands of new CVXPY objects merely to recreate the old internal
list representation. If an internal/public representation must change, freeze
that change through a separately reviewed typed contract before implementation.

The implemented compatibility boundary uses an immutable, source-specific
projection registry on each vectorized `OPFBuild`. Every extracted variable or
expression declares its internal native shape, public native shape, and
interval, boundary, or horizon view. Interval values move time first exactly
once; storage SoC explicitly omits the retained initial boundary when restoring
the existing `(T, n_storage)` result; horizon totals remain native; and `T=1`
stays unsqueezed. Public native shapes may differ only by removing explicitly
declared singleton axes, never by permuting or reinterpreting physical axes.
Component model expressions are interval-valued; terminal expressions and
integrated component costs are horizon-valued. Missing declarations, missing
required sources even without an accepted primal, or dimensional drift fail
extraction. The adapter retains the original horizon CVXPY objects and never
reconstructs a per-step object list.

### M14c — Vectorized lossy DC

**Open.** The authoritative implementation and execution contract is frozen in
`experiments/m14_time_vectorization/M14C_PROTOCOL.md`. M14c consumes the
completed M14b representation registry and retains explicit caller selection,
the stepwise default, and formulation-specific SCIPY provenance.

Implement the annual-experiment blocker first:

- branch-flow variables with shape `(n_branch, T)`;
- batched nodal active-power balance;
- batched branch limits and resistance-weighted loss proxy;
- vectorized dispatchable-generation costs and bounds;
- vectorized fixed/sheddable load channels;
- vectorized nondispatchable availability and curtailment reporting;
- vectorized storage power, SoC recurrence, cycling cost, and terminal policy;
- vectorized HVDC boxes, injections, costs, and supported loss semantics; and
- exact preservation of the lossy-DC audit and result schema.

M14c follows one reproducible execution sequence. Unit, `T=1`, and
short-horizon stepwise/vectorized gates run on this branch. The reviewed
implementation is then integrated into `big-experiment`, which owns the S4
fixture and supervisor. Its bounded ladder uses, in order, the deterministic
first 24, 168, and 720 hourly rows of the frozen S4 exogenous inputs while
retaining the exact S4 network, fleet, identities, timestep, policy, solver,
and construction options; the terminal policy applies at each prefix's final
boundary. The 8,760-hour run is authorized only after those post-integration
prefix gates pass. No separate pre-integration Case118 scaling fixture is part
of the authoritative ladder.

The implementation should use genuine N-dimensional expressions and sparse
linear operators according to the natural algebra of each component. Replacing
a left-deep Python sum with a balanced sum is a useful local correction but
does not, by itself, complete this stage.

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

1. **Structural unit tests:** dimensions, time-last axes, identities, bounds,
   tensor semantics, and temporal operators.
   Tests distinguish `static`, `interval`, and `boundary` fields, reject
   undeclared or incorrectly oriented time axes, and demonstrate that
   preparation and model assembly do not create tiled Python-side copies of
   static inputs. Canonical bound-vector memory is measured separately because
   it necessarily scales with bounded scalar-variable count. Qualified boxes
   test both static broadcast views and fully time-varying arrays; independent
   primal-bound audits remain mandatory.
2. **`T=1` tests:** intentional multistep results agree with the existing
   single-step formulation while retaining multistep shapes.
3. **Short-horizon equivalence:** old and vectorized builders agree on
   objectives, declared component costs, acceptance audits, and uniquely
   determined result quantities. Genuinely nonunique primal coordinates may
   differ when both solutions independently pass the full residual and
   optimality gates under the predeclared comparison classification.
4. **Failure equivalence:** infeasible, solver-failure, and unusable-primal
   records preserve their stable schemas and classifications.
5. **Component matrix:** generators, storage, loads/shedding,
   nondispatchable generation, and conditional HVDC paths.
6. **Formulation-specific scaling ladders:**
   - lossy DC progresses through the exact 24-, 168-, 720-, and 8,760-step S4
     problem ladder;
   - single-node DC progresses through a separately predeclared large horizon
     sufficient to demonstrate scaling, including 8,760 when inexpensive; and
   - AC uses bounded case9 and Case118 horizons selected from existing timing
     and memory evidence, stopping at declared resource limits rather than
     requiring an annual AC solve.
7. **Hierarchy regression:** M17 focused tests and the retained S7 equivalence
   gate remain clean.
8. **Backend verification:** the legacy baseline records CPP and the vectorized
   path records SCIPY; performance comparisons never conflate a representation
   change with an undocumented backend change.
9. **Profiling matrix:** representative short and long horizons report results
   by network formulation, temporal assembly mode, canonicalization backend,
   and `T`. At minimum, include short AC windows and the lossy-DC scaling
   ladder; do not extrapolate the annual DC result to short nonlinear AC.

Strict mypy, Ruff, the complete test suite, and `git diff --check` remain
required repository gates.

## Annual S4 resumption gate

The Case118 `big-experiment` branch remains on hold until all of the following
are true:

1. the exact frozen S4 annual inputs build through the vectorized lossy-DC
   path;
2. the 24-hour public-versus-streaming outer equivalence gate passes;
3. the vectorized annual model matches the predeclared scientific invariants
   and its separately frozen vectorized structure/dimension registry, storage
   identities, terminal target, and provenance hashes;
4. execution provenance records the explicit SCIPY canonicalization backend;
5. construction, canonicalization, and solve remain within the existing frozen
   S4 limits: 16,384 MiB child RSS, 7,200 seconds worker wall time, 10,800
   seconds total supervisor wall time, and one-second polling;
6. no OS memory-pressure termination occurs;
7. the independently reconstructed outer audit is accepted; and
8. execution occurs from a clean committed source with a fresh output
   directory.

The new annual result must identify the vectorized execution commit. It is not
a continuation of any failed S4 worker. Because the tracked S4 fixture and
supervisor live on `big-experiment`, the reviewed M14c implementation must
first be integrated there at an explicit checkpoint. That checkpoint records
both source commits, verifies the unchanged S4 fixture/policy/solver/scenario
hashes, and reruns the cheaper M14c gates before annual authorization; M14 does
not create a duplicate S4 execution stack on this branch.

### One annual execution, two gates

M14 does not require an annual qualification solve followed by a duplicate S4
solve. All cheaper M14c unit, equivalence, structural, backend, and bounded
scaling gates must pass first. The first authorized 8,760-step execution is then
launched through the exact frozen S4 supervisor, fixture, provenance, resource,
archive, and analysis protocol from a clean commit containing the reviewed M14c
implementation.

The M14c equivalence gate compares the stepwise and vectorized lossy-DC
mathematics. The S4 equivalence gate separately compares the public
hierarchical controller's retained outer plan with the streaming experiment
seam. Both must pass after integration; neither is evidence for the other.

That single execution serves simultaneously as the terminal M14c scaling gate
and the candidate authoritative S4 outer run. If every predeclared M14c and S4
gate passes, its immutable outer artifact is promoted as the authoritative S4
result and no repeat computation is required. If it fails, the retained record
remains M14/S4 resource, construction, solver, or audit evidence according to
its actual classification; it is never selectively promoted. Any later retry
requires the ordinary reviewed S4 rerun decision and a fresh output directory,
not a ceremonial repetition of an already accepted result.

## Non-goals

M14 does not:

- weaken the annual experiment or M17 acceptance gates;
- introduce approximate temporal aggregation;
- change the one-hour S4 time resolution;
- claim that vectorization alone makes direct annual AC OPF practical;
- redesign the hierarchy or shard policy;
- add uncertainty, contingencies, or alternative storage physics; or
- erase the scientific value of the observed memory boundary;
- remove the stepwise temporal formulation or silently select a temporal mode
  from the horizon length.

## Completion criteria

M14 is complete when the reviewed vectorized paths preserve the frozen
mathematics and result contracts, pass the verification ladder, and demonstrate
materially improved time-axis construction/canonicalization scaling. M14c is
complete—and S4 may resume—when the exact 8,760-step Case118 lossy-DC outer
problem clears the annual resumption gate. Completion retains both stepwise and
vectorized modes as supported, profiled implementations.
