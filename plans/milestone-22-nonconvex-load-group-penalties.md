# Milestone 22 — Nonconvex load-group penalties

**Status:** planned

**Depends on:** Milestone 19 (first-class loads and load shedding), Milestone
16+ (typed component contribution and shared assembly contracts)

**Related to:** Milestone 17 (hierarchical DC/AC realization), Milestone 18
(relax–round–polish for non-ideal storage), and Milestone 21 (configurable
formulation hierarchies)

## 1. Goal

Add scientifically explicit policies for interactions among groups of
sheddable loads when the desired rule is nonconvex. The reference policy is:

> Load group A or load group B may be shed as a last resort, but both groups
> may not be shed in the same decision interval.

The primary solution strategy is **relax–round–polish**:

1. solve a bounded convex-hull or McCormick relaxation;
2. convert the relaxed group decisions into an admissible discrete policy;
3. fix that policy and polish the continuous OPF; and
4. independently audit the polished physical solution.

The milestone must work first in the convex single-node and lossy-DC
formulations and then support fixed-policy AC polishing. It must not require a
mixed-integer solver for ordinary use. Small mixed-integer or enumerated
problems may be retained as scientific reference oracles.

This milestone does not claim that every nonconvex customer policy has a
useful convex relaxation. It establishes one typed, auditable pattern and
measures when that pattern succeeds or fails.

## 2. Scientific questions to freeze before implementation

The experiment protocol must distinguish the following questions rather than
allowing an API default to answer them implicitly.

### 2.1 Meaning of a group

A group is an identified set of existing `Load.device_id` values. Membership
must be explicit, stable, unique within the policy, and independent of list
position. Decide whether overlapping groups are forbidden in the first
implementation; the recommended initial contract forbids them.

Negative active loads are fixed net injections under M19 and are not eligible
shedding capacity. Zero and reactive-only loads likewise contribute zero
active shedding capacity.

### 2.2 Scope of exclusivity

These are different policies:

- **per interval:** A or B may be shed at each time, and the selected group may
  change between intervals;
- **per event/window:** one group is selected for the complete event;
- **block or minimum-duration:** selection may change, but only at declared
  boundaries or after a minimum dwell time; and
- **priority rather than exclusivity:** one group should be exhausted before
  the other, but both may be shed if adequacy requires it.

The reference implementation should begin with per-interval and whole-window
exclusivity. Switching costs, dwell constraints, and priority policies should
be added only with separate names and tests.

### 2.3 Hard rule versus soft penalty

Do not conflate:

1. the hard complementarity rule

   \[
   S_{A,t}S_{B,t}=0,
   \]

   with

2. a positive joint-shedding penalty

   \[
   \kappa_t S_{A,t}S_{B,t}.
   \]

The first prohibits simultaneous shedding. The second permits it at a cost
and may be preferable when strict exclusivity would create artificial
infeasibility. Both are nonconvex, but their relaxations, rounding rules, and
scientific interpretations differ.

## 3. Reference group quantities

For group \(g\) and interval \(t\), define aggregate active shedding

\[
S_{g,t}=\sum_{i\in g}s_{i,t}
\]

and the synchronized upper bound

\[
U_{g,t}=\sum_{i\in g}
\max(P^{\mathrm{load}}_{i,t},0)\,f_i^{\max}.
\]

The group model must reuse the M19 load variables and parameters. It must not
construct a second load-shedding decision or an independently updated copy of
load demand.

Every aggregate and bound must remain inspectable by stable group and device
identity. Parameter updates must preserve the existing atomic positive/zero/
negative load semantics.

## 4. Convex-hull relaxation for exclusive groups

Introduce a selector \(z_t\). The exact disjunction is

\[
z_t\in\{0,1\},
\qquad
0\leq S_{A,t}\leq U_{A,t}z_t,
\qquad
0\leq S_{B,t}\leq U_{B,t}(1-z_t).
\]

Relaxing \(z_t\in[0,1]\) gives the convex hull of the bounded two-group
disjunction for one interval. Equivalently, when both upper bounds are
positive,

\[
\frac{S_{A,t}}{U_{A,t}}+
\frac{S_{B,t}}{U_{B,t}}\leq1.
\]

Use the extended selector formulation because it exposes the quantity that
must be rounded and handles synchronized bounds without division by a zero
parameter. Zero-capacity groups require explicit deterministic behavior, not
an epsilon denominator.

For convex single-node and lossy-DC problems, the relaxed objective is a valid
lower bound on the corresponding exact disjunctive optimum when all other
model assumptions match. Do not describe an AC relaxed or polished local
solution as a global bound.

## 5. McCormick relaxation for a soft interaction penalty

For the soft product \(w_t=S_{A,t}S_{B,t}\), use finite synchronized bounds
and the complete McCormick envelope. For nonnegative group shedding,

\[
\begin{aligned}
w_t &\geq 0,\\
w_t &\geq U_{A,t}S_{B,t}+U_{B,t}S_{A,t}-U_{A,t}U_{B,t},\\
w_t &\leq U_{A,t}S_{B,t},\\
w_t &\leq U_{B,t}S_{A,t}.
\end{aligned}
\]

The relaxed objective may include \(\Delta\sum_t\kappa_t w_t\). The plan must
record the units of \(\kappa_t\), whether it is a stage-cost rate, and how it
changes with time resolution. Terminal or event-level penalties must remain
outside the time integral when appropriate.

The McCormick relaxation can be weak, especially when minimizing a positive
product over broad bounds. Report envelope slack and polished product cost;
do not present the relaxed auxiliary value as the realized interaction cost.

## 6. Typed public and private contracts

### 6.1 Group policy object

Design an immutable typed object with, at minimum:

- stable policy ID;
- ordered group IDs and their member `Load.device_id` values;
- policy kind: `exclusive` or `joint_penalty`;
- decision scope: `per_interval` or `whole_window` initially;
- optional time-aligned interaction weights;
- rounding-policy configuration; and
- explicit emergency or infeasibility policy.

Do not overload `Load.shedding_cost_per_mwh`. Individual value-of-lost-load
costs and cross-group interaction policy are distinct model concepts.

### 6.2 Relaxation and rounding records

Retain typed records for:

- relaxed selectors and group shedding;
- relaxation status, objective, bounds, and residuals;
- rounding inputs, deterministic rule, seed where applicable, and output;
- every polished candidate attempted;
- the accepted fixed group schedule;
- polished objective and independent residual audit; and
- explicit failure classification.

Records must distinguish optimized quantities from post-solve evaluation
metrics.

### 6.3 Component boundary

Group interactions are constraints or costs over variables owned by multiple
`Load` objects. Determine during Stage 0 whether they belong in:

- a typed load-fleet horizon contribution; or
- a small cross-component policy layer invoked after ordinary load assembly.

Do not force a group policy into one individual load adapter, reconstruct load
variables, or add formulation-specific copies to all three builders. If M16+
lacks a generic fleet-level interaction hook, complete that contract narrowly
and test it with another synthetic interaction.

## 7. Rounding policies

Rounding must be named, deterministic, and scientifically inspectable. The
initial candidates should include:

1. **threshold:** choose A when \(z_t\geq\tau\), otherwise B;
2. **candidate polish:** near a declared ambiguity band, polish both fixed
   alternatives and retain the best accepted result;
3. **whole-window enumeration:** solve A-only and B-only polished problems;
4. **block rounding:** choose one group over predeclared contiguous blocks;
   and
5. **feasibility repair:** revise only failed or ambiguous blocks using a
   deterministic, bounded candidate sequence.

Do not silently switch to simultaneous shedding when exact exclusivity was
requested. If the policy permits an emergency third mode, name it explicitly,
assign its cost deliberately, and report its use.

For per-interval policies, naive exhaustive enumeration scales as \(2^T\).
Small exhaustive cases are reference oracles only. Longer horizons should use
threshold/block rounding, dynamic programming where the temporal policy
allows it, or a separately configured mixed-integer oracle.

## 8. Polishing and formulation hierarchy

Polishing fixes the rounded group selection and rebuilds or updates the
continuous problem with the corresponding shedding bounds. The polish must:

- use the original objective, physical constraints, and synchronized inputs;
- apply no residual penalty that changes the declared policy;
- pass the formulation's independent acceptance audit; and
- report infeasibility rather than substituting the relaxed solution.

The reference hierarchical workflow is:

```text
single-node or lossy-DC relaxation
                |
                v
       deterministic rounding
                |
                v
 fixed-policy convex polish and audit
                |
                v
      fixed-policy AC realization
```

The AC layer consumes a fixed group schedule; it does not round. If AC polish
fails, try only a predeclared causal candidate sequence. An accepted alternate
proves the original rounded schedule was not required; repeated local failure
does not prove physical infeasibility without stronger evidence.

## 9. Evaluation metrics

At minimum, report:

- active and reactive service by device and group;
- group energy not served;
- simultaneous relaxed shedding and its duration;
- selector fractionality and distance to the rounded schedule;
- relaxed, convex-polished, and AC-polished objectives;
- relaxation and rounding gaps where mathematically valid;
- group switches, dwell durations, and longest shedding event;
- number of candidate polishes and recovery attempts;
- solver, canonicalization, and total wall time; and
- all ordinary network, voltage, thermal, balance, and terminal-state audits.

Renewable curtailment, storage throughput, congestion exposure, voltage
support, and recovery latency remain evaluation metrics unless a separately
named model explicitly prices or constrains them.

## 10. Verification and scientific experiments

### 10.1 Algebraic unit cases

- Compare the selector formulation with the analytic convex hull.
- Exercise positive, zero, and time-varying group bounds.
- Verify complete McCormick inequalities at corners and interior points.
- Confirm stable identity alignment and reject missing, duplicate, or
  overlapping memberships according to the frozen policy.
- Verify exact delta scaling of time-varying interaction penalties.

### 10.2 Exact small reference study

On a small single-node problem, enumerate every admissible group choice and
compare against:

- the relaxed lower bound;
- each rounding policy;
- the fixed-choice polished optimum; and
- an optional pinned mixed-integer oracle.

Use cases with zero gap, positive gap, ambiguous \(z\), and exact-policy
infeasibility.

### 10.3 Network-location study

Use a congested lossy-DC case where customer groups occupy different buses.
Show how transmission constraints change the selected group relative to the
single-node result. Separate customer priority from network deliverability.

### 10.4 AC realization study

Fix the rounded selection and solve the corresponding AC problem. Audit
active/reactive balance, voltage, branch limits, group service, and exact
exclusivity. Compare the AC realization with the convex plan without requiring
identical dispatch trajectories.

### 10.5 Temporal-policy study

Compare per-interval, whole-window, and block selection on a multistep event.
Measure switching, rounding gap, polished cost, customer-class ENS, and
runtime. Do not choose the preferred policy solely because it is easiest to
solve.

## 11. Staged implementation plan

### S0 — Freeze semantics and baselines

- choose the reference customer groups and event;
- freeze membership, exclusivity scope, emergency behavior, and units;
- retain fixed-load and independent-shedding numerical baselines;
- decide the fleet-level architectural hook; and
- define acceptance residuals and result schemas before implementation.

### S1 — Identity-aligned group objects and reporting

- add the typed group-policy input without changing feasible sets;
- aggregate device shedding and capacity by stable identity;
- publish group service and ENS expressions; and
- cover empty, zero-capacity, negative-load, and reactive-only groups.

### S2 — Exclusive convex-hull relaxation

- add continuous selectors and bounded hull constraints;
- support per-interval and whole-window selectors;
- preserve single-node, lossy-DC, and AC builder architecture; and
- validate the relaxation against analytic and enumerated cases.

### S3 — Typed rounding and fixed-policy polish

- implement threshold, ambiguity-band candidate, and whole-window enumeration
  policies;
- retain every candidate and audit;
- add deterministic feasibility repair; and
- establish relaxed-versus-polished reporting.

### S4 — Soft joint-shedding penalty

- implement the complete McCormick envelope;
- integrate time-varying penalty rates exactly once;
- report relaxed and realized product costs separately; and
- characterize relaxation strength across controlled bounds.

### S5 — Lossy-DC and AC realization

- demonstrate location-sensitive selection under congestion;
- consume fixed group schedules in AC;
- add formulation-specific physical audits; and
- classify planning mismatch separately from local-solver failure.

### S6 — Temporal and hierarchical experiment

- compare per-interval, block, and whole-event policies;
- evaluate rounding quality, switches, ENS allocation, runtime, and recovery;
- test convex planning followed by fixed-policy AC realization; and
- retain the complete reproducible experiment record.

### S7 — Documentation and extension proof

- document group-policy semantics and relax–round–polish limitations;
- add runnable single-node and hierarchical examples;
- show how a new cross-load penalty uses the same typed interaction boundary;
- document solver requirements for optional exact reference oracles; and
- update the architecture diagram without implying a general-purpose mixed-
  integer plugin system.

## 12. Completion gates

Milestone 22 is complete when:

1. group membership and all results align by stable load identity;
2. the exclusive relaxation matches the analytic convex hull and small exact
   enumeration baselines;
3. rounding is deterministic, typed, and fully retained;
4. every accepted solution comes from a fixed-policy physical polish;
5. hard exclusivity and soft joint penalty remain separate public concepts;
6. convex lower-bound claims are made only where valid;
7. single-node, congested lossy-DC, and fixed-policy AC studies pass their
   independent audits;
8. time-varying penalties obey the package's objective-time convention;
9. unsuccessful relaxed and polished solves retain stable result schemas; and
10. documentation reports relaxation limits, rounding gaps, and any emergency
    policy use without presenting them as exact convex behavior.

## 13. Explicit non-goals

- a universal mixed-integer or complementarity solver interface;
- a claim of global optimality for AC polished solutions;
- arbitrary overlapping or nested customer-group logic in the first release;
- stochastic or adversarial customer selection;
- unit commitment, restoration sequencing, or distribution-network switching;
- hiding exact-policy infeasibility through unreported simultaneous shedding;
- treating every post-solve metric as an objective; or
- replacing customer-policy decisions with a single undifferentiated VOLL.
