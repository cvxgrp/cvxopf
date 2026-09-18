# Case118 toy AC analysis: design checkpoint

Prepared 2026-09-17. Original design review CLEAN; subsequently approved by owner.
Owner-directed reuse of the existing parallel recovery setup is recorded below;
independent scientific review of that execution amendment is CLEAN.
The separate look-ahead-horizon addition reflects the owner's clarified scope
and has also received a CLEAN independent scientific design review.
The completed [AC dispatch adjustments](../experiments/case118_counterfactual/ac_dispatch_adjustments/REPORT.md)
and [battery operation in AC and DC](../experiments/case118_counterfactual/battery_operation/REPORT.md)
comparisons are recorded separately. The design below does not authorize
additional windows beyond those explicitly selected.

**Owner scope update, 2026-09-17:** retain this first AC analysis study, with
the intention to replicate its methods on Tracy results. Defer the separate
toy AC look-ahead-horizon study. Inspection revealed that toy generator
curvature (c2 = 1e-4) and battery throughput weight (1.0) do not represent the
intended temporal economics. Keep this study bounded; its purpose is reusable
analysis and conditional matched comparisons, not exhaustive investigation
of an unintended economic regime. Stage 0b is committed as `0ae9d86`.

The owner approved the three questions and episode-first selection workflow
in the [Tracy study plan](case118-tracy-2021-study-plan.md). This checkpoint
proposes the concrete scope and choices below. It does not launch solves.
The completed toy record is committed in `9c26366`; preserve its fixture,
results, and claims. New methods and results belong to separate work.

## Purpose and selected work

**Battery operation follow-up:** after the AC dispatch comparison and inspection
of saved battery trajectories, the owner selected three three-hour windows for a
[study of battery operation in AC and DC](case118-toy-battery-mechanism-test.md).
It compares fixed/free schedules and two prescribed energy transfers: 24
primary solves proposed across the three windows, with a result checkpoint
between phases and separately bounded recovery. This is additional scoped
mechanism work, not a revival of the deferred horizon study or authorization
to launch the earlier full six-window R1/R2/G/B proposal.

Develop a standard analysis that explains the completed toy AC realization,
tests the operational value of selected adjustments, and supplies useful
period-selection criteria for shorter Tracy runs. The analysis should be
understandable to colleagues and students and require only the extraction,
comparison, and reporting tools needed for these questions.

| Question | Selected work | What it can establish |
| --- | --- | --- |
| Where and when does realization change? | Annual overview plus contextualized episode reports from retained data | Magnitude, location, timing, physical margins, and associations |
| What operational value do changes provide? | Small matched AC counterfactual study | Feasible small-repair witnesses and observed cost improvements under explicit restrictions |
| Which conditions deserve early Tracy attention? | Inspect a small set of pre-AC covariates against toy outcomes; carry candidate criteria to shorter Tracy runs | Exploratory selection hypotheses, subsequently tested on new inputs |

Deferred: another annual toy rollout; an exhaustive horizon sweep; renewable-flexibility
counterfactuals; alternative storage sizing; scheduler redesign; a trained
difficulty predictor or automatic AC-skipping policy. A longer common-endpoint
comparison can be proposed if the episode evidence leaves a specific temporal
question unresolved. The previously requested scoped AC horizon study is now
also deferred on this toy fixture. These items are not prerequisites for Tracy.

### Deferred addition: separate AC look-ahead-horizon study

The [separate horizon design](case118-toy-ac-horizon-design.md) remains a record
of the earlier proposal, superseded by the scope update above. Do not select
toy horizon periods, implement horizon-specific tooling, or run horizon
comparisons as part of Stage 0c. Reconsider the question using Tracy's revised
economics and shorter-study evidence under a later explicit decision and
protocol. The horizon-specific recovery notes below are retained design
context, not active implementation requirements or numerical authorization.

## 1. Describe episodes before choosing solve windows

Use the committed `s5_closeout/hourly_comparison.csv`, inputs, and summaries
for the annual overview. Read selected accepted AC archives for detailed
episodes; do not rerun the completed annual physical audit. Verify the selected
archive identities against their checkpoints and the accepted S5 record.

Proposed first inspection: six episode cards, three diagnostic episodes and
three ordinary comparisons. An episode is a physical operating pattern with surrounding context, not a solver invocation.
Start with a focal study day and the preceding/following 12 hours (48 hours
total), clipped at year boundaries. Mark shard boundaries and any state resets;
do not describe concatenated shards as one uninterrupted causal trajectory.
The context can be extended from retained data when the pattern extends beyond
the card; record the reason. It does not set the counterfactual horizon.

Use three diagnostic categories: sustained opposing generator redispatch with
relatively small battery changes, coherent battery/SOC timing shifts, and
joint large generator/battery changes. Rank sustained effects rather than one
maximum hour. Proposed candidate-index scores are six-hour rolling means of
opposing generator MW and battery L1 MW, with percentile ranks over complete
six-hour spans within one shard. Show both scores and their component ranks;
use generator-minus-battery rank for spatial candidates, battery rank for
temporal candidates, and the smaller rank for joint candidates. These labels
are relative rankings, not proof of distinct mechanisms. Group adjacent
high-score hours into events before choosing cards, record the grouping rule,
and merge overlapping candidates so one episode is not counted repeatedly.
Inspect SOC paths to distinguish a newly created phase shift from inherited
state divergence; a high battery score alone does not establish a coherent
charge/discharge episode.

For each diagnostic episode, propose an ordinary comparison in a similar
season, time of day, and weekday class, with nonextreme/central adjustment
scores. Avoid selecting only the easiest hours. Show load, renewable, and
headroom differences rather than imply exact matching; do not match away the
DC indicator being investigated. These are descriptive comparisons, not
randomized controls. Recovery and interventions are annotations; numerical
difficulty is not itself physical importance. No economic-benefit ranking is
available before the counterfactuals. Pre-AC stress criteria have their own
annual screening track in section 3 and need not force additional solve cards.

Prepare a candidate table with actual scores, ranks, timestamps, source hashes,
and selection explanations. Inspect the cards with the owner, then freeze the
exact solve-window list before new counterfactual outcomes are observed. The
original eight-window rank rule is background, not the selection contract.

### Common episode report

- **Inputs and state:** load, available/used renewable power, net load and ramps;
  DC and realized battery powers and SOC by device; common time axis, signed
  power convention, and both start/end boundary states.
- **Dispatch changes:** generator net, L1, and opposing changes, plus bus/device
  contributions; battery signed and L1 power differences; SOC signed/L1
  differences in MWh. Show absolute units and normalization by load or fleet
  power/energy capacity. Do not integrate SOC differences as throughput.
- **Physical context:** DC real-flow utilization and AC both-terminal apparent
  utilization, identified branch rows/endpoints, loading breadth, voltage
  margins, generator reactive margins, and inverter capability margins.
  The two flow utilizations have different meanings; do not subtract them as
  the same quantity. A value near a limit is not a shadow price or proof that
  the limit caused redispatch. Do not rank noise in saturated annual maxima.
- **Storage headroom:** per-device energy room to upper/lower bounds, active
  charge/discharge room, and AC apparent-power room accounting for reactive
  output. Fleet sums can hide a locally constrained device. State whether a
  margin is physical or a diagnostic normalization.
- **Accounting:** losses, curtailment, generation and storage-aging costs;
  reconcile net generation change with losses, renewable dispatch, battery
  injection, and any modeled shunts. Use the actual component cost convention,
  not assumed market dollars or the difference of unlike DC/AC objectives.
- **History and limitations:** selected controller/recovery, source-policy
  segment, inherited SOC discrepancy, and intervention/boundary annotations.
  Distinguish executed first actions from unused look-ahead trajectories.

The current schema retains Pg/Qg, Vm, battery P/Q/SOC, renewable P/Q,
branch-terminal P/Q/S, and load arrays. The compact closeout table lacks much
of this device-level detail. The new work is selective extraction and common
reporting, not new physical data generation.

## 2. Matched AC comparisons

Use the [existing counterfactual proposal](case118-toy-ac-counterfactual-protocol.md)
as the mathematical starting point. Preserve equal exogenous inputs, AC
constraints, and DC-derived initial/terminal SOC in every arm. Fix renewable
real dispatch to its accepted DC schedule initially; reactive controls remain
free within their original capability. Do not silently introduce curtailment
to rescue a restricted arm.

| Arm | Battery real power | Generator freedom and purpose |
| --- | --- | --- |
| R1 | Fixed DC schedule | Minimize integrated generator L1 departure from DC |
| R2 | Fixed DC schedule | Minimize common AC operating cost within the R1 departure budget plus a declared small allowance |
| G | Fixed DC schedule | Minimize the same operating cost without the departure budget |
| B | Free, equal endpoints | Minimize the same operating cost with battery rescheduling |

Departure is `Dg = delta * sum(abs(Pg - Pg_DC))` in MWh. Report net/L1/opposing
changes separately; minimizing L1 does not certify minimum opposing redispatch.
R2 avoids mistaking an expensive R1 tie choice for the value of flexibility.
Report whole-window `C_R2 - C_G`, `C_G - C_B`, and `C_R2 - C_B`, in common
cost units, as percentages with stated denominators, and per MWh served.
The G-then-B ordering assigns interactions to B; it is not a unique causal
allocation. Retain accepted candidates as feasible incumbents in the nested
arms so a failed or worse local solve cannot erase known feasible performance.

These economic comparisons retain the historical toy costs, including the
1.0 throughput penalty. Small battery-flexibility value under that objective
does not establish small value under Tracy's intended economics. Carry the
comparison method to Tracy and recompute its results there; do not retune the
toy objective within this study or transfer its numerical conclusions.
Report generation-cost change, absolute-throughput change, the associated
lambda-times-throughput cost change, and total cost change separately. Shifting
the timing of an existing cycle at unchanged throughput adds no cycling
penalty. Weak curvature relative to the cost of an additional cycle therefore
does not prove why storage was idle; changing marginal units, congestion,
losses, and AC device constraints can still affect its value. A small G-to-B
improvement is conditional on the selected short window and common endpoints,
which themselves can exclude valuable longer energy shifts.

These windows start from DC SOC, not the historical realized AC state; this
keeps fixed DC battery schedules endpoint-compatible. They answer a matched
local question, not an exact replay of the historical action. Large realized
state divergence is important context and must not be hidden by this reset.

Proposed scope: six three-hour windows, one per reviewed episode/comparison,
with four comparison stages each (24 stage opportunities, not a cap of 24
solver attempts). This deliberately reduces the
original eight-window proposal; add a window only if the inspection identifies
a distinct question and the owner approves the change. The selected start must
admit three hours within one shard. No annual savings extrapolation from this sample.
Whole-day context does not justify calling three-hour gains the value of a
daily storage schedule. Longer solves remain deferred pending a specific
question and revised budget.

Report effect sizes and solver/cost accuracy rather than inventing a binary
materiality threshold after seeing results. A feasible improvement establishes
an available benefit; no improvement or an unresolved local solve does not
establish absence of benefit or infeasibility.

## 3. Selection criteria for the shorter Tracy studies

Keep input-only, DC-derived, and AC-outcome fields separate in the data table.
Candidate pre-AC criteria are net-load level and signed/absolute ramps,
persistent energy deficit and recharge opportunity, renewable availability/share
and spatial export distribution, DC loading breadth/location and p95 utilization,
per-device storage headroom, and signpost movement relative to device power.
Record each quantity's horizon and information availability. AC losses,
realized SOC, recovery, and AC constraint margins are outcomes/context, not
inputs to a claim of advance selection.

Use the full retained hourly table for transparent plots, high/low indicator
bins, and ranked candidate lists, with ordinary periods visible. Do not claim
screening performance from the six outcome-selected cards alone. Distinguish
physical adjustment, economic benefit, and solver effort as separate outcomes; do not force them into a single criticality score.
Document how season, hour, and execution policy may confound toy associations.
Freeze any proposed selection rule before testing it on the shorter Tracy
runs, including low-score comparisons and reporting misses. If predictive skill
is assessed, separate evaluation periods by events/weeks rather than random
hours among autocorrelated neighbors. Tracy uses its own prepared inputs and
outer solutions; neither toy dates nor thresholds
are presumed transferable. This checkpoint does not require a predictive
model or new annual study to justify moving forward.

## 4. Artifacts and minimal implementation

Stage 0b disposition should prioritize the original counterfactual proposal,
the useful dispatch/stress extraction and dashboard helpers, and their fixed
source references. Preserve original files; curate reusable code rather than
promote duplicate snapshots indiscriminately. The committed closeout arrays
and tables are already available and need not be copied again.

| Component | Reuse | Scoped addition |
| --- | --- | --- |
| Episode extraction/report | Closeout tables, fixture/outer loaders, checkpoint references, archive schema | Selected first-action device/branch arrays, margins, common episode cards and selection table |
| Matched-window construction | Public multistep builder and existing exact slicing pattern | Identity-aligned schedule locks, common endpoints, R1 objective and R2 departure budget |
| Initialization/solutions | Existing named-variable/start utilities and accepted-result structures | Full incumbent transfer checked against each arm, explicit choice between new and retained candidates |
| Audit/report | Existing physical audit and component result extraction | Independent lock/budget checks, common-cost reconstruction, comparative tables |
| Execution | Existing two-main/one-helper supervisor and implemented recovery ladder | Bind experiment jobs/arm contracts to existing scheduling, recovery, and auditing; no scheduler redesign |

Generator model variables use per-unit values while reported Pg and battery
power use MW. Preserve production cost expressions when constructing the R1
objective. Use `build.solve()`; qualification must verify that added locks and
the modified objective reach the solved problem and that extraction remains
correct. Reuse the physical acceptance tolerances, rather than weakening them
for the diagnostic.

Tests should establish units/identity alignment, DC SOC reconstruction,
equal comparison inputs, non-unit-delta departure/cost accounting, and
incumbent feasibility after arm transfer. Check unresolved-stage reporting
without requiring a production service framework. Do not duplicate the annual
runner or repeat unaffected historical qualification.

## 5. Proposed sequencing, budgets, and stopping points

1. Stage 0b disposition and its separate commit are complete (`0ae9d86`).
   Apply the owner scope update: first analysis retained, toy horizon deferred.
2. Implement and review read-only episode extraction/reporting in Stage 0c;
   inspect the candidate cards and choose exact matched windows.
3. Specify the short counterfactual protocol, implement its minimal runner,
   independently review the necessary tests, and commit the implementation.
4. After numerical approval, compare the four sets of operating restrictions
   in one selected window. Use its evidence to confirm the remaining study budget and ask
   whether additional windows would add distinct scientific information.
   The proposed six-window scope is not a quota: any reduction or extension is
   an explicit owner decision, preserving attempts and ordinary comparisons.
5. Execute only the approved remaining stages and review the findings. Record
   reusable methods, limitations, and unresolved questions, plus the explicit
   toy horizon deferral, at Stage 0c exit. No horizon study is required to close.

### Owner-directed execution setup: two main lanes and one shared helper

Reuse the implemented S5 **two main lanes and one shared helper**, with at most
three concurrent AC solver processes. Use `causal_first_speculative_v1`, revision
2 including its persistence amendment, as the recovery baseline. Its target-free
branch applies to B and the horizon study; recovery for fixed-battery R1/R2/G
requires the explicit applicability decision below. The owner confirmed reuse
of the execution setup for both studies. This replaces
this draft's earlier serial/no-escalation proposal and its 15-minute stage cap.
Use the implemented policy at the closeout baseline, not the stale review-status
wording of its historical proposal. Bind new study-specific execution records;
the historical annual execution authority does not authorize these studies.

Preserve the established escalation behavior:

- A primary becomes eligible for assistance after 300 solve-clock seconds
  and keeps running; an unsuccessful returned primary becomes eligible
  immediately. Help is subject to the shared queue and memory gate.
- For B and the horizon study, keep the causal perturbation, target-free,
  copied, and perturbed target-free
  sequence, the bounded helper budgets, the 1,800-second retry of a timed-out
  target-free source, and the reviewed uncapped secondary/final escalation.
  Target-free results supply starts only and cannot supply accepted actions.
- Keep helper fairness, first independently accepted winner, loser cancellation
  and reaping, attempt/start identities, and explicit unavailable-source slots.
  Uncapped attempts remain subject to whole-study resource/operator stops.

#### Recovery applicability by arm

| Arm/study | Is removing the terminal SOC equality a substantive relaxation? | Recovery treatment |
| --- | --- | --- |
| R1, R2, G | No: explicit hourly battery-power locks plus initial SOC and recurrence already determine the endpoint | Retain 2+1 infrastructure; specify and qualify an arm-specific initialization policy rather than automatically inherit the full target-free branch |
| B | Generally yes: battery power is free and the endpoint restricts its feasible trajectory | Retain target-free as a source-only recovery path; restore and audit the hard target before accepting a controlling candidate |
| Horizon study, including H=1 | Generally yes: battery power is not separately locked | Retain the existing target-free recovery ladder with per-trajectory causal starts |

For ideal storage, `E_end = E_start - delta * sum(b)`. In R1/R2/G,
`b = b_DC` and the DC initial state imply the DC endpoint, subject to the
archived numerical residuals checked during qualification. Removing the
terminal equation changes only a redundant constraint representation, not
the intended feasible trajectories or objective. R2's departure cap remains
in place. A numerical initialization benefit is possible but unestablished;
the annual free-battery recovery evidence does not justify automatically
spending the 300-second, 1,800-second, and uncapped target-free budgets here.
Do not drop battery locks or the repair cap to make that source solve useful.

H=1 is different: its battery movement is fixed **through the terminal
equality**, not by an additional battery-power lock. Removing that equality
frees battery movement, so target-free remains relevant even at H=1.

**Fixed-battery initialization approved during implementation.** Use
explicitly declared starts or perturbations around the primary
initialization (R1) or a mapped feasible prior-arm incumbent (R2/G, when
available). These are not preceding-hour causal starts or target-free
solutions. Define their source identities, availability, budgets, and audits
in the [counterfactual implementation](../experiments/case118_counterfactual/README.md),
then qualify it before execution. The redundant-equation target-free heuristic
was not selected for R1/R2/G. B retains its target-free recovery path.

Do not simply disable one source slot and leave the rest implicit: copied and
perturbed target-free starts depend on an accepted target-free source, while
isolated windows lack a preceding causal controller. Removing that branch
without replacement could leave no helper alternatives. Preserve the shared
scheduler, memory gates, and dependent-stage ordering while resolving this
initialization choice; this clarification does not implement a new policy.

Retain 16 GiB per-worker-tree and 24 GiB simultaneous aggregate solver-tree
RSS ceilings, the 22 GiB pressure threshold, and the 8 GiB helper launch reserve.
Record supervisor memory separately as in the retained setup. Three available
lanes do not guarantee three simultaneous solves when the memory gate prevents
helper launch. Qualify longer horizons under those same limits rather than
silently increasing them.

Parallelize independent work: different matched windows may occupy the two
main lanes, while R1 -> R2 -> G -> B dependencies and incumbent transfers remain
ordered within each window. A one-window comparison may leave a main lane idle.
In the horizon study, independent episode/horizon trajectories can occupy
main lanes, but successive executed hours within each trajectory stay causal.
Do not race successive states or treat the helper as a third main experiment.

**Required experiment adaptation:** every attempt must build the current arm's
objective, schedule locks, repair budget, data, and state correctly. A
source-only target-free attempt may relax the designated terminal SOC target
as the existing ladder prescribes; it must retain the arm's other restrictions
and objective. Every controlling candidate must restore the hard target and
pass both the physical and added arm-constraint audits. The recovery ranking
must include those added restrictions. Do not let an annual-runner helper
silently solve the unmodified economic objective or drop a schedule lock.
For fixed-battery R1/R2/G, use the applicability decision above; do not treat
removing a redundant endpoint equation as a demonstrated relaxation benefit.

Isolated counterfactual windows have no preceding causal controller: mark the
causal-perturbation slots unavailable as the existing policy does at a fresh
start. A previous arm's feasible incumbent is an explicitly mapped primary
initialization/cost incumbent, not a preceding-hour trajectory. Preserve its
role and validate it against the destination arm. The horizon study uses each
trajectory's own preceding accepted controller and shape-correct shifted start.

Qualify this narrow job/build/audit adaptation with the existing scheduler;
do not replace the scheduler to accommodate the new experiments. Verify that
helper winners satisfy the same comparison contract as main-lane winners and
that worse new candidates cannot erase retained feasible incumbents.

The first selected window has four comparisons. The broader six-window proposal
would have 24 comparisons total, including those first four;
recovery, target-free sources, replays, and canceled competitors are additional
solver attempts. Retain both counts, total solve effort, and concurrent elapsed
time. The earlier budgets of 90 minutes for the first window and six hours for
the broader study are provisional
planning envelopes to reassess against this ladder; they are not stage deadlines
or launch authorizations. Before launch, agree the aggregate attempt/work and
wall-time budgets and specify how a study stop interrupts uncapped contenders.
No unrecorded retries or automatically expanded ladder are permitted.

A restricted comparison that exhausts its permitted procedure is unresolved.
Specify which independent later arms may still run and which cost comparisons
become unavailable. Stop and report resource, identity/accounting, or defective
physical/lock-audit failures. Do not replace a selected window after its outcome.

Before launch, bind the shared solver settings and reviewed recovery policy,
specify primary/incumbent initialization for the comparison arms, and choose
the R2 repair allowance (MWh), constraint/cost tolerances, and feasible-incumbent
transfer behavior. The allowance must be small relative to the reported repair
and distinguishable from numerical residuals; establish that in low-cost
qualification rather than guess a universal constant here. Record these
choices together with window identities and budgets in one reviewable protocol.

Completion does not require every restricted solve to succeed. It requires
an honest comparison record, independently checked feasible candidates,
explicit unresolved/deferred questions, and a useful standard analysis for
shorter Tracy runs. The separately planned toy horizon study is deferred.
Renewable-flexibility extensions
remain separate decisions, not automatic responses to an uninteresting result.

At the end of Stage 0c, finish documentation/regression checks and the toy
follow-up disposition. The user merges `big-experiment`, updates and verifies
local `main`, and creates the fresh Tracy-study branch. Stage A input generation
begins only after that baseline handoff, as specified in the governing plan.

## Review record

Scientific register. `cvxopf-discuss` supplied read-only scientific input on
episode grouping and ordinary comparisons, six-window scope, physical margins,
nonconvex interpretation, materiality, and transfer to Tracy; incorporated here.
`cvxopf-review` independently reviewed this checkpoint, the governing-plan
amendments, and the original counterfactual proposal: CLEAN, no actionable
findings. It checked comparison meaning, episode/window separation, ordinary
comparisons, screening limitations, stage counts and budgets, minimal tooling,
and the end-of-0c branch gate. Exact grouping/ties/windows/starts, repair
allowance, tolerances, and failure progression remain for the execution
protocol. The checkpoint is ready for owner design approval, followed by
Stage 0b artifact disposition, not numerical launch.
No new OPF solve, full archive audit, artifact promotion, or commit is performed
by preparing this checkpoint.

The owner subsequently directed reuse of two main lanes, one shared helper,
and the implemented revision-2 recovery ladder. Independent narrow review
against the policy and memory-gate implementation returned CLEAN, with no
actionable findings. It checked experiment dependencies, arm-preserving
recovery/audits, missing causal predecessors, incumbent retention, and corrected
stage/attempt/work budgeting. The earlier serial execution proposal is superseded.

A subsequent read-only scientific applicability check confirmed the storage
recurrence argument and actual target-free construction. It distinguishes
fixed-battery R1/R2/G from free-battery B and all horizon variants, including
H=1, and identifies the helper-source dependency when target-free is omitted.
The owner requested this distinction be recorded and subsequently approved
explicit primary/prior-arm perturbations when authorizing the implementation.

After the owner deferred the toy horizon study, `cvxopf-discuss` assessed the
revised scope read-only and agreed with the scientific prioritization. Its
qualifications are incorporated above: preserve conditional findings from the
first study, distinguish added cycling from timing rearrangement, use the
first comparison as an information-value checkpoint, and transfer methods rather than toy
numerical conclusions. This assessment is not an independent implementation
review or execution authorization.
