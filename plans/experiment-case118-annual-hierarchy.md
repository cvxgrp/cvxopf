# Experiment — Annual case118 hierarchical AC dispatch

**Status:** Draft for review

## Goal

Determine whether the completed M17 `lossy_dc`→`ac` hierarchy can carry a
storage-coupled operating trajectory over a full 8,760-hour year on a
118-bus network, and identify the computational boundary honestly if it
cannot.

This is a scale and systems experiment, not a new formulation milestone. The
scientific object is the realized sequence of residual-checked AC first
actions coupled by storage state. The experiment must not describe the
long-horizon lossy-DC plan as AC-feasible, and it must not call an interrupted
or partial trajectory an annual solve.

## Why this is interesting

A direct 8,760-step nonlinear AC-OPF on case118 would replicate the nonlinear
network state and constraints at every hour while coupling all intervals
through storage. That is intentionally outside the practical regime of the
current CVXPY DNLP/IPOPT path. M17 replaces that monolith with:

1. one long-horizon convex lossy-DC energy plan;
2. short nonlinear AC windows receiving identity-aligned storage signposts;
3. execution of one accepted AC action per controller iteration; and
4. an auditable realized SoC recurrence.

The experiment asks whether this decomposition is operationally useful at a
network and time scale for which direct AC construction is already
unattractive.

## Immediate architectural facts

The annual run is not assumed feasible merely because each AC window is
short.

- A frozen M17 run still constructs and solves one 8,760-step case118
  lossy-DC problem.
- The public controller executes only the first action from each AC window,
  so a completed annual trajectory requires 8,760 accepted controlling
  windows, not $$8760/W$$ nonoverlapping block solves.
- `shifted_with_recovery` registers nine auditable slots per attempted window.
- Every executed attempt retains its live `OPFBuild`. Retaining thousands of
  case118 AC builds may become the memory limit before solver time becomes the
  limit.
- The repository's MATPOWER case118 has 118 buses, 186 active branches, 54
  generators, and 99 positive-demand buses. Every branch has
  `rateA = 9900 MVA`; therefore the stock case has enforced but practically
  nonbinding thermal limits.

The scaling ladder below must measure these costs before authorizing the full
year.

## Scientific questions

1. Can the annual outer lossy-DC problem be built and solved within declared
   memory and runtime budgets?
2. How do AC construction time, solve time, recovery frequency, and retained
   memory scale with window length on case118?
3. Does the realized AC trajectory preserve storage state and the configured
   annual terminal obligation without load shedding?
4. How far do realized AC states deviate from lossy-DC storage signposts?
5. Which resource fails first as the horizon grows: outer canonicalization,
   outer solve, AC solve throughput, initialization recovery, retained-build
   memory, or scientific feasibility?
6. How do published PGLib-OPF thermal ratings affect feasibility, recovery
   use, and storage dispatch relative to an otherwise identical PGLib-derived
   unlimited network?

## Baseline scope

The first reviewed experiment uses:

- the complete PGLib-OPF `pglib_opf_case118_ieee` network, generator, load,
  cost, voltage, and thermal-rating data;
- hourly trajectories with exactly 8,760 intervals and a timezone-stable
  index;
- explicit first-class loads for every bus with nonzero active or reactive
  demand, including reactive-only channels;
- fixed nonsheddable loads;
- ideal storage with explicit, unique device IDs;
- no HVDC in the baseline;
- no branch outages, contingencies, ramping, minimum up/down logic, or dynamic
  security claims;
- `outer_policy="frozen"` for the annual reference;
- `inner_terminal_policy="hard_equality"`;
- `initialization_policy="shifted_with_recovery"`;
- AC branch-limit enforcement enabled; and
- the fixed M17 accepted-primal and residual gates.

The experiment imports the complete PGLib case rather than transplanting only
its branch ratings into the repository's MATPOWER case118. This preserves the
published relationship among demand, generator capability, costs, voltage
bounds, and thermal limits. The original source, pinned revision, license,
source-file hash, and deterministic conversion output must be checked and
recorded.

PGLib branch angle-difference bounds are not implemented by cvxopf and are
therefore not enforced. The report must identify this as a deviation from the
full PGLib OPF operating set. The experiment may use the PGLib data as a
network case, but it may not claim exact reproduction of PGLib benchmark
objectives or feasibility.

### Conversion acceptance gate

The source acquisition and deterministic MATPOWER-to-Python conversion must
verify, row by row, preservation or explicitly documented treatment of:

- every nonzero active and reactive bus demand, including zero/negative-active
  or leading-reactive entries;
- generator status, active/reactive limits, voltage setpoints, and every
  supported cost model;
- bus conductance and susceptance shunts;
- fixed transformer tap ratios and phase shifts;
- branch status and `rateA`, `rateB`, and `rateC` values;
- bus voltage bounds and reference-bus identity;
- `baseMVA`, external bus numbers, and the deterministic external/internal bus
  mapping; and
- branch angle-difference limits, which are retained as provenance but excluded
  from claims about modeled enforcement.

The gate records source and converted-array hashes and rejects silent row
reordering, dropped devices, unsupported active cost models, or nonfinite
values. `rateB` and `rateC` are preserved as source data but are not enforced by
the current cvxopf model.

`replan_every_step` is not an annual baseline. It would rebuild a shortening
case118 outer plan at every hour and confound the AC-window scaling question
with thousands of large convex replans. It may be evaluated on short horizons
only.

## Scenario construction

### Annual trajectories

The default plan is a deterministic synthetic year committed as a compact
generator specification rather than an opaque 8,760-row artifact. Generation
must be deterministic across platforms and must materialize checked hashes for
all prepared arrays.

The load trajectory should combine:

- an annual seasonal term;
- weekday/weekend structure;
- a diurnal term; and
- a small deterministic weather-like correlated component.

Each bus retains its case118 base-demand share. If $$L_t$$ is the normalized
system multiplier and $$P_i^0,Q_i^0$$ are the case values, then

$$
P^{load}_{t,i}=L_t P_i^0,
\qquad
Q^{load}_{t,i}=L_t Q_i^0.
$$

This preserves each load's power factor and avoids inventing 99 independent
unvalidated profiles.

At least one wind-like and one solar-like nondispatchable trajectory should be
included, with explicit device IDs and buses selected before the protocol is
frozen.

Exploratory S0 pilot work may compare a small, predeclared grid of renewable
and storage penetrations. Those runs are tuning evidence only. Before any
authoritative feasibility run, one deterministic construction rule must be
frozen and the authoritative scenario regenerated from scratch. No capacity,
siting, or profile parameter may be changed in response to an authoritative
outcome.

### Storage fleet

The first proposal is four ideal storage units distributed across electrically
distinct areas of case118. Every unit must define:

- explicit `device_id`;
- bus;
- apparent-power rating;
- energy capacity;
- initial SoC;
- annual terminal policy; and
- cycling cost.

The baseline uses a common 50% initial SoC and an annual equality target at the
same energy. The siting and sizing rule must be independent of optimized
dispatch. The proposed deterministic rule is:

1. assign every active branch the strictly positive distance weight
   $$w_e=\max(|x_e|,10^{-6})$$ per unit, reject nonfinite reactance, and compute
   all-pairs shortest-path distances on the resulting undirected frozen PGLib
   topology;
2. select four load-weighted network medoids by deterministic k-medoids with
   bus-number tie breaking;
3. place one storage unit at each medoid;
4. set aggregate storage power to a frozen fraction of synthetic-year system
   peak and divide it among medoids in proportion to cluster peak load; and
5. use one frozen duration in hours to derive energy capacities.

Renewable buses use an equally explicit graph/load criterion, and aggregate
wind/solar capacity is derived from a frozen annual renewable-energy share and
the prepared profile capacity factors. Exact fractions, duration, clustering
initialization, and tie-breaking are locked after pilot review and before the
authoritative scenario hash is created.

The siting implementation must verify that the active network is connected,
all weights and pairwise distances are finite and nonnegative, and repeated
construction produces identical clusters and medoids. Zero-reactance branches
therefore receive the declared small positive weight rather than creating
zero-distance aliases; negative reactance, if present, contributes through its
absolute magnitude. The k-medoids initialization sequence, update rule, stop
rule, and all distance/objective ties use ascending external bus number.

### Network variants and line limits

The **PGLib-rated network** is the primary scientific scenario. PGLib-OPF
documents its replacement of missing 9,900 MVA placeholders using statistical
line-capacity and maximum-current models. These ratings are not treated as
historical utility limits, but they are a published, reproducible engineering
model suitable for an OPF experiment.

The causal thermal-rating counterfactual is the **PGLib-derived unlimited
network**. It is produced from the verified converted PGLib case by changing
only `rateA` to zero. Every other numeric value, row order, identifier, device,
option, and annual input is identical.

The zero-rating semantics differ by formulation and must remain explicit. AC
omits thermal constraints for `rateA=0`. Lossy DC substitutes the finite
`OPFOptions.branch_limit_sentinel` (default 1,000,000 MW). The counterfactual is
therefore called **effectively unlimited**, not mathematically unconstrained.
One frozen sentinel value is used by every rated/unlimited lossy-DC build and
every public/streaming comparison. Ex post, every effectively-unlimited outer
plan must show maximum absolute branch flow below 10% of the sentinel; failure
invalidates the counterfactual. Rated-versus-effectively-unlimited differences
may then be attributed to the enforced `rateA` constraints within the modeled
operating set.

The checked-in repository MATPOWER `case118()` remains a separate
**implementation and scale comparator** at S0/S1. Because its demand,
generator limits, costs, voltage data, shunts, and transformer data may differ
from PGLib, it is not a congestion counterfactual and no causal rating claim may
use it.

The PGLib active-power-increase case
`pglib_opf_case118_ieee__api` is a possible **congested stress sensitivity**.
It is not the annual baseline: its modified operating point could make annual
profile scaling infeasible for reasons unrelated to the hierarchical method.
It may enter only after the ordinary PGLib case passes static and short-horizon
AC/DC characterization.

No ad hoc branch-rating multiplier is permitted in the baseline. If later
sensitivity analysis scales published ratings, the multiplier set must be
predeclared and every result must remain labeled as an engineered stress case.

## Pre-execution architecture gate

The current `solve_hierarchical_opf()` API returns only after execution ends
and retains every accepted live `OPFBuild`. It cannot provide per-window RSS
measurements, graceful memory-threshold termination, atomic between-window
checkpoints, or release of old AC builds. Therefore the week, month, and year
stages must not use it directly.

Before S2, implement a separately reviewed **experiment-owned streaming
reference runner**. It is not a new public controller abstraction and does not
change `HierarchicalResult`. It must:

- compose the existing public OPF builders and result extraction APIs;
- implement the frozen M17 state, signpost, accepted-primal, recovery, and
  first-action rules without importing private `_hierarchical_solver` helpers;
- retain the annual outer plan once;
- measure process memory immediately before/after each AC build, solve,
  archival write, and build release;
- atomically archive each complete experiment plan/attempt/executed-interval
  record before releasing the AC build;
- permit a declared resource observer to request state-preserving termination
  only between controller iterations;
- resume only after validating source/scenario/checkpoint hashes and the full
  realized state; and
- distinguish archived immutable evidence from the live-build audit tree
  returned by the public M17 API.

The archival attempt schema intentionally does not pretend to contain a live
`OPFBuild`. Before release it records the formulation, dimensions, ordered
variable/constraint/parameter structural signature, assigned starts, complete
IPOPT `x0` evidence, extracted public results, residual audit, solver evidence,
and artifact hash. The P0 review must establish that this is sufficient to
reconstruct every comparison and acceptance decision made by the streaming
runner.

This runner is admitted to S2 only after it reproduces
`solve_hierarchical_opf()` window by window on frozen 6- and 24-hour scenarios:
outer signposts, slot registry, assigned model starts and complete IPOPT `x0`,
statuses, residuals, controlling attempts, executed actions, SoC, and
exact-once summaries. Any non-equivalence blocks scaling work.

The frozen 6- and 24-hour orchestration fixtures are compact case9 scenarios,
not 24 case118 AC windows. S0/S1 already established that the latter would
violate the resource gate without adding network-dependent orchestration
evidence. Case118 receives a separate S1 outer/endpoint structural and archive
equivalence gate. `P0_PROTOCOL.md` freezes both requirements; neither alone is
sufficient for S2.

Nominal equivalence is necessary but not sufficient. P0 must also exercise the
complete recovery and termination state machine through a predeclared matrix:

| Equivalence case | Required behavior |
|---|---|
| Nominal shifted primary | Primary controlling attempt accepted; remaining eight slots are `not_needed_after_acceptance` |
| Primary failure, target-free success, copied success | Target-free result is retained but never executed; copied-target-free attempt supplies the action |
| Copied-start failures followed by target-free perturbation success | Ordered target-free perturbation slots execute until the first accepted controller; later slots are not needed |
| Target-free failure followed by causal perturbation success | Target-free-derived slots are `source_unavailable`; causal perturbations retain their causal source and stop at first acceptance |
| Certified infeasibility | Attempt outcome and window diagnosis match the frozen classifier and no unusable action executes |
| Solver exception/unusable primal | Distinct outcomes, retained evidence, and subsequent policy slots match the public controller |
| Recovery exhaustion | All nine slots are retained, no state advances, and termination reason/iteration agree |

The fault schedules, accepted slot ordinals, perturbation scales/seeds, and
expected record trees are frozen before the equivalence run. The test harness
may instrument a private public-controller solve seam solely to inject the same
deterministic outcomes into both orchestrators; production streaming code may
not import or call that private seam. These are orchestration-equivalence tests,
not claims about natural IPOPT failure frequencies.

The target-free-failure/causal-perturbation fixture begins only after at least
one interval has been accepted and executed. Its causal perturbations must name
the immediately preceding accepted controlling attempt as their source; it may
not fabricate a first-window or retrospective source.

P0 also replays the authoritative M17 S3b recovery window and compares the
streaming archival record with the tracked S3b integrity metadata and the
public-controller S7 interpretation. This retrospective case is additional
evidence that the copied-target-free path matches a real observed recovery,
while the injected matrix supplies deterministic coverage of every remaining
slot and termination path.

An alternative public streaming/observer extension may be proposed, but it
requires its own API and audit-contract review. The experiment cannot claim
checkpointing or memory-safe execution until one of these reviewed mechanisms
exists.

## Scaling ladder

No stage may silently advance after a failed gate.

| Stage | Horizon | Purpose | Advancement gate |
|---|---:|---|---|
| S0 (complete) | 1 and 6 h | Import and hash PGLib case118; freeze the pilot/authoritative scenario boundary; verify AC/DC signs, identities, branch-rating enforcement, residuals, and storage recurrence | Passed; lowest pilot point selected, repository case118 characterized separately, and S1 resource limits frozen |
| S1 (complete) | 24 h outer + bounded 6 h AC | Solve and audit matched 24-hour lossy-DC outer plans; characterize one six-hour endpoint realization; retain direct 24-hour AC as unauthorized | Passed; both outer and endpoint records accepted, provenance matched, direct 24-hour AC remained unexecuted, and resource limits were respected |
| P0 (complete) | 6 and 24 h | Implement and review the streaming reference runner | Passed; clean consolidated execution `81b3189` established nominal and injected equivalence, persistence/reconstruction, historical evidence, Case118 boundary, and dependency gates |
| S2 (complete: resource boundary + exploratory continuation) | 168 h | One-week frozen hierarchy; establish AC throughput and recovery frequency | The frozen 16 GiB invocation stopped cleanly at its resource boundary after 128 intervals; a labeled 24 GiB continuation resumed from that checkpoint and completed a fully accepted 168-interval trajectory |
| Pre-S3 recycling comparison (complete) | 3 × 64 h | Compare uninterrupted, 32-interval, and 16-interval worker lifetimes | Completed 192/192 accepted intervals; exact causal trajectories; observed peak RSS 20,025.5, 19,501.7, and 17,926.3 MiB respectively |
| S3 (pre-execution implementation complete; review pending) | 720 h | One-month frozen hierarchy and memory-retention study using selected `recycle_every_16` policy | Run with planned worker recycling after every 16 completed intervals; complete or terminate with an auditable resource boundary |
| S4 | 8,760-step outer only | Build and solve the exact annual lossy-DC plan before any annual AC loop | Accepted outer primal within predeclared runtime and memory budgets, plus the outer equivalence gate below |
| S4b | bounded qualification plus annual partition | Validate scheduler-neutral sharding and select annual shard boundaries from the frozen S4 outer trajectory | Sequential shard equivalence, fresh-process execution, deterministic merge/audit, and a resource-bounded parallel demonstration all pass before S5 |
| S5 | 8,760 h | Full annual hierarchical execution from the frozen shard manifest | All shards completed and merged into one boundary-continuous annual record, or an explicit partial-horizon scientific record |
| S6 | selected horizons | Optional PGLib active-power-increase congestion sensitivity | Same acceptance and accounting gates as the ordinary PGLib network |
| S7 | — | Analysis, reproducibility record, and conclusions | Independent reconstruction of all reported totals and hashes |

Direct AC is a characterization comparator at small horizons only. Failure to
build or solve larger direct AC cases is itself a recorded scaling result; it
is not a reason to weaken the hierarchical acceptance gate.

S2 is complete as a resource-boundary stage, with the complete scientific
trajectory obtained through an explicitly exploratory continuation. It is not
an uninterrupted pass of the frozen 16 GiB execution policy. The continuation
worker's advancement eligibility records successful numerical and provenance
checks local to that worker; it does not supersede the authoritative
invocation's `resource_limit` classification. Before S3, a modest planned
worker-recycling policy or bounded recycling comparison must be frozen and
must measure restart overhead, per-worker peak RSS, and causal trajectory
continuity. That bounded comparison completed on 2026-08-24: all three
64-interval arms were accepted, recycled trajectories matched the uninterrupted
trajectory exactly, and the 16-interval cadence had the lowest observed peak
RSS without a material wall-time penalty. The comparison was intentionally
observational and made no automatic advancement decision. After reviewing that
evidence, the user selected `recycle_every_16` for S3. This means a planned
worker restart at global completed-interval boundaries 16, 32, ..., 704,
resuming only from the exact verified checkpoint. The 720-interval horizon
therefore has exactly 44 planned restarts and 45 worker invocations; boundary
720 is `study_complete`, not an extra restart. Counters never reset the global
schedule after a recycle. An abnormal resource, solver, or provenance outcome
stops automatic execution, retains the partial record, and requires explicit
review; it never triggers an automatic cadence adjustment. The selection is
specific to the frozen Case118 S3 workflow and is not a universal controller
or solver default.

### Reviewed S0 pilot-window amendment

The original midnight six-hour probe was reviewed and retained but produced
only tolerance-level rated-AC storage movement. Before any replacement OPF,
the S0 protocol was amended to use one common, exogenously selected six-hour
window across the whole pilot grid. The 15% renewable-energy construction
defines the fixed reference net load; all non-wrapping windows use the
low-earlier/high-later mean-difference score and deterministic tie breaking
locked in `S0_PILOT_PROTOCOL.md`.

Meaningful movement must occur in the primary rated AC result. It requires at
least one device to exceed the greater of 100 times the declared power
tolerance and 0.1% of its rating, plus total throughput greater than 0.1% of
aggregate storage capacity. Both comparisons are strict. Midnight and amended
results are separate scientific records and are never combined.

### Annual outer-only equivalence

S4 uses the streaming reference runner's outer-plan construction seam, not
`solve_hierarchical_opf()`, because the public controller would immediately
enter its AC loop. The outer-only build must use the same frozen execution
snapshot, converted case, horizon slice, explicit fleets, storage initial and
global terminal settings, `OPFOptions`, and `LayerSolveConfig` as the public
hierarchy.

Before the annual outer solve is accepted, this seam must reproduce short
public-controller outer plans for both rated and matched-unlimited networks:

- formulation and scalar variable/equality/inequality counts;
- ordered storage and device identities;
- local/global boundary indexing;
- objective and all public primal arrays within frozen tolerances;
- terminal modes, targets, and complete SoC signposts; and
- the independently reconstructed outer residual audit.

The annual record retains the same fields and hashes. A merely similar
standalone lossy-DC build does not satisfy S4.

### Pre-S5 sharding and parallel-execution qualification

S4b establishes that the annual AC realization can be executed as independent,
restartable jobs without changing the scientific problem. It does not add a
cluster scheduler or make scheduler-specific behavior part of `cvxopf`. The
experiment provides a scheduler-neutral, installable execution command that
accepts one frozen annual manifest, one shard identifier, and one output
location. The same command must be usable from a workstation process, a fresh
environment containing the installed package, or an HPC array job. SLURM, PBS,
Kubernetes, resource requests, retries, and artifact transport remain external
execution concerns.

Every shard specification retains:

- the global half-open interval range and immutable scenario/input hashes;
- the source commit and complete model, policy, solver, and recovery settings;
- ordered storage identities and the identity-aligned initial state;
- the S4 outer-plan signposts, including the shard terminal target;
- deterministic initialization transformations, perturbation seeds, and
  stopping rules; and
- an independent output location, atomic checkpoints, and machine-readable
  completion and resource summaries.

Shard boundaries are derived only from the accepted S4 outer plan, never from
observed AC outcomes. Before executing S4b, freeze a deterministic selection
rule that prefers sufficiently separated charging periods whose device-level
SoCs are near 50% of capacity. The rule must define the charging statistic,
normalized distance from mid-SoC, tie breaking, minimum and maximum shard
lengths, and treatment of inactive or stationary devices. Aggregate SoC alone
is insufficient if it conceals a participating device near an energy bound.
The selected boundaries and their exact identity-aligned states are then
materialized in the annual manifest and cannot be adapted in response to shard
outcomes.

The predecessor shard's hard terminal signpost and the successor shard's
initial state are the same outer-plan state. Shards may not optimize, infer, or
silently repair their own boundary conditions. Consequently, successful shards
form one storage-continuous annual trajectory even when their wall-clock
execution order differs.

S4b must pass four gates:

1. **Partition equivalence.** On a bounded frozen reference horizon, an
   uninterrupted execution and a sequential execution of the same intervals as
   shards agree within the existing scientific tolerances on boundary states,
   accepted intervals, residuals, attempts, and reconstructed trajectory
   quantities.
2. **Independent execution.** Every test shard succeeds in a fresh process
   using only its manifest entry and declared immutable inputs; no predecessor
   in-memory state or undeclared local artifact is required.
3. **Merge validation.** The merger rejects missing, duplicated, overlapping,
   misconfigured, or boundary-discontinuous shard records and deterministically
   reconstructs global interval order and all additive and extremal summaries.
4. **Resource-bounded parallel demonstration.** At least two shards run
   concurrently under a predeclared aggregate memory budget, with per-worker
   and aggregate peak memory, CPU time, elapsed wall time, and restart/recovery
   behavior retained. If the qualification host cannot safely support two AC
   workers, sequential qualification may establish architectural shardability,
   but S5 parallel execution remains unauthorized until the parallel gate is
   demonstrated on a suitably provisioned host.

Passing S4b establishes portability and semantic equivalence, not universal
parallel efficiency. S5 records requested versus achieved concurrency and
keeps scheduler waiting time distinct from model construction, solver, merge,
and total elapsed time. The frozen manifest remains runnable serially when
available memory does not permit concurrent AC workers.

Before any S4b or S5 AC execution, implement and verify the experiment-owned
five-minute primary-attempt safeguard frozen in
`experiments/case118_annual_hierarchy/FIVE_MINUTE_TIMEOUT_POLICY.md`. The
300-second budget is a typed manifest/provenance field. Exhausting it retains
the primary attempt and enters the unchanged causal target-free/copied recovery
sequence; it never accepts a timed-out primal, changes the terminal policy, or
silently advances state. Timing reports separate the consumed primary budget,
both recovery solves, construction/canonicalization where available,
worker/restart overhead, and total window latency.

## Measurements

Record separately for every outer plan and AC attempt:

- Python construction time;
- canonicalization/setup time;
- solve time and total wall time;
- solver status and iterations;
- scalar variable, equality, and inequality counts;
- process resident memory before build, after build, after solve, and after
  retention;
- accepted-primal classification and every residual;
- initialization source, recovery slot, and complete IPOPT-start signature;
- serialized artifact size; and
- software, solver, source, scenario, and environment provenance.

Trajectory summaries use executed first intervals exactly once. They include:

- generation and storage-cycling cost;
- renewable curtailment;
- active losses reconstructed from both branch terminals;
- maximum voltage and thermal violations;
- cumulative absolute storage-signpost deviation;
- recovery-use counts and shifted-primary success fraction;
- realized storage throughput and terminal deviation; and
- completed interval count and coverage fraction.

Outer objectives are retained per plan and are never summed with realized AC
operating costs. AC terminal-policy costs are diagnostics, not realized energy
costs.

## Resource and stopping policy

Exact budgets must be frozen after S0 profiling and before S1. The reviewed
streaming runner must support interruption-safe checkpoints between controller
iterations. A stage terminates rather than swapping uncontrollably or
discarding audit data when:

- resident memory exceeds its declared limit;
- wall time exceeds its declared limit;
- the outer solve lacks an accepted primal;
- all AC recovery slots are exhausted;
- state or identity alignment fails; or
- required artifacts cannot be written and verified.

The public M17 API remains the short-horizon equivalence reference and retains
its complete live-build audit tree. The streaming experiment record is a
distinct archival schema; it must never be returned or documented as a
`HierarchicalResult`.

For S1 on the S0 workstation, the frozen limits are 16 GiB process RSS,
45 minutes per AC solve, and two hours overall. A supervising parent samples
child RSS at intervals no longer than one second and terminates the child at a
limit while retaining an explicit resource-boundary record. Direct AC is
authorized only through six hours. The 24-hour direct-AC comparator is not
executed and is classified as `not_authorized_by_s0_resource_gate`, never as
solver failure or infeasibility. Twenty-four-hour lossy DC and bounded
AC/hierarchical construction measurements remain authorized.

## Reproducibility contract

Before each authoritative stage:

1. commit runner and scenario code;
2. require a clean worktree;
3. record the full execution-source commit;
4. use a fresh output directory;
5. record source fingerprints and prepared-array hashes;
6. record Python, CVXPY, NumPy, pandas, Clarabel, cyipopt, and IPOPT versions;
7. write artifacts atomically; and
8. do not edit model or runner source during execution.

Large raw artifacts may remain ignored, but a compact tracked metadata and
summary record must retain artifact names, sizes, SHA-256 hashes, status,
coverage, and the principal scientific measurements.

## Decisions required before freezing S0

1. **Annual data:** approve deterministic synthetic profiles, or select a
   redistributable measured annual source.
2. **Storage fleet:** approve the number, buses, and power/energy sizing rule.
3. **Renewables:** approve device buses, wind/solar capacity, and curtailment
   economics.
4. **Congestion sensitivity:** decide whether the PGLib active-power-increase
   variant belongs in S6 after the ordinary rated case is characterized.
5. **Resource budgets:** select machine-specific memory and wall-time limits
   after the S0 probe.
6. **Streaming architecture:** approve the experiment-owned runner boundary or
   request a separately designed public observer/archive API before S2.

## Success criteria

The strongest outcome is a complete, reproducible 8,760-hour case118 realized
AC trajectory with coupled storage and all M17 audit gates intact.

A shorter outcome can still be scientifically successful if it locates the
first reproducible scaling boundary, quantifies it through the staged ladder,
and distinguishes model infeasibility, local solver behavior, runtime, and
memory retention. The experiment fails only if it reports an ambiguous partial
run, changes the protocol after seeing results, or weakens the accepted-primal
contract to obtain completion.

Every conclusion is conditional on one deterministic synthetic year, one
frozen storage/renewable siting and sizing rule, and one primary PGLib network
case. Annual completion demonstrates operational viability for that scenario;
it is not evidence of annual reliability across weather years, storage
portfolios, networks, contingencies, or solver stacks.
