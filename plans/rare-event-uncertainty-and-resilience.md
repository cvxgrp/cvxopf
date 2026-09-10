# Rare-event uncertainty and long-range resilience planning

## Purpose

This note summarizes the proposed treatment of uncertainty for rare,
high-consequence resilience events. The objective is to use CVXOPF to identify
long-range investments and adaptive operating policies that remain effective
across uncertain event footprints, severities, durations, damage states, and
recovery trajectories.

The intended result is generally not one universally optimal plan. It is a
cost--resilience frontier, together with the decisions that are optimal for
declared risk preferences and those that remain robust when far-tail event
probabilities are deeply uncertain.

This responds to two connected questions:

1. How should uncertainty be treated when studying very rare events, and what
   long-range decisions are optimal under a spectrum of major heat-wave and
   other black-sky risks?
2. How should confidence, uncertainty, and technology selection be treated
   for sequential and compound stressors, such as a tropical storm occurring
   during a heat wave under long-term load growth?

## Event classes

The framework should support a range of black-sky events rather than focus on
one hazard.

### Extreme heat waves

Heat waves are broad, evolving events that may simultaneously:

- increase active and reactive demand;
- derate thermal generation and transmission capacity;
- alter solar and battery performance;
- impose water or fuel constraints;
- increase correlated equipment failures; and
- trigger wildfire-related outages or public-safety restrictions.

Important uncertainties include geographic extent, temperature trajectory,
duration, nighttime relief, load response, equipment derating, correlated
failures, and forecast accuracy. Infrastructure may remain intact while the
system operates near physical limits for days or weeks.

### Destructive storms and long restoration

A destructive event may produce geographically concentrated asset loss and a
recovery lasting months or years. One illustrative trajectory is:

- 80% of solar capacity becomes unavailable at an affected node;
- half of the lost capacity is restored within three months; and
- the remaining capacity returns over the following fifteen months.

The uncertain quantities include storm path, local intensity, asset exposure,
damage fraction, correlated damage to nearby assets, accessibility, repair
resources, replacement-part availability, and restoration time.

### Compound events

The most consequential studies may combine hazards, such as an extreme heat
wave occurring while the network is still recovering from storm damage. The
framework should therefore represent persistent damage states as well as
shorter operational stress.

A representative sequence could contain:

1. a long-term load-growth trajectory that establishes the background demand;
2. a summer heat wave that raises load and derates generation, transmission,
   and storage;
3. a tropical storm whose path damages geographically exposed resources and
   network elements;
4. a constrained restoration period with limited crews, access, spares, fuel,
   and replacement equipment; and
5. persistent or recurrent high temperatures while the system remains only
   partially restored.

The hazards and impacts must not be sampled as independent marginals when
seasonality, geography, or physical mechanisms correlate them. A compound
event model needs a joint or conditional scenario construction.

## Long horizons as the technical enabler

Long-horizon planning is not merely a larger OPF calculation. It is the
technical capability that lets sequential and compound events be represented
as one connected physical history.

Independent event studies tend to begin each event from an artificially clean
state. They reset storage, restore damaged assets, replenish fuel and repair
inventories, make crews newly available, and remove the effects of preceding
operational decisions. Those resets can erase the mechanism that makes a
compound event consequential.

A long-horizon model instead preserves state across the sequence:

- battery state of charge and other stored-energy states;
- damaged, derated, repaired, and partially restored assets;
- fuel, spare-equipment, and material inventories;
- crew availability and repair progress;
- supply-chain delays;
- deferred maintenance or interrupted service; and
- the system's realized operating state entering the next stressor.

The scientific chain is therefore:

```text
event 1
   -> damage and operating response
   -> partial recovery and resource consumption
   -> event 2
   -> compound consequence
```

The horizon also enables anticipatory decisions. If a heat wave is forecast
after a storm, the policy may preserve battery energy, change repair
priorities, procure temporary generation, or accept a smaller present cost to
avoid a more consequential future outage. Separate event studies cannot
discover these tradeoffs.

The central architectural proposition is:

> Long-horizon optimization enables compound-event resilience studies by
> preserving physical, operational, and recovery state across events.

## Structure of the uncertainty

A common causal representation is:

```text
hazard realization
        |
        v
geographic exposure
        |
        v
asset damage or derating
        |
        v
time-varying availability and recovery
```

The uncertainty model should distinguish:

1. **Event occurrence:** the frequency of an event class.
2. **Spatial realization:** path, footprint, intensity field, and affected
   nodes or corridors.
3. **Conditional impact:** capacity loss or derating, demand response, and
   correlated failures given exposure.
4. **Duration and recovery:** hazard persistence, partial restoration,
   repair time, and supply-chain constraints.
5. **Information arrival:** what is forecast before the event, observed
   immediately afterward, or learned progressively during recovery.

Spatial and temporal correlations are essential. Independent asset outages
would not adequately represent a storm path, regional heat wave, or shared
repair bottleneck.

## Scientific treatment of very rare events

For a nominal "1-in-1,000" event, the occurrence probability may be the
least defensible model input. The analysis should not imply that a far-tail
return period is known precisely.

Report two complementary views:

1. **Conditional resilience:** given that an event of a specified class
   occurs, evaluate performance across plausible paths, severities, damage
   realizations, and recovery trajectories.
2. **Probability-weighted tail risk:** where a defensible hazard distribution
   exists, combine probability and consequence using expected loss,
   exceedance probabilities, CVaR, or related measures.

For poorly characterized hazards, stress testing, sensitivity analysis,
robust optimization, distributionally robust optimization, and minimax regret
are more defensible than assigning a precise probability. Severe events
outside the historical record can be explored as black-sky scenarios without
claiming that their occurrence rates are accurately calibrated. A genuinely
unforeseen black swan does not have a trustworthy prespecified distribution.

## Confidence bounds and uncertainty reporting

"Confidence bounds" can refer to different uncertainties that should not be
combined into one interval.

### Outcome variability

The event ensemble produces a distribution of physical and economic outcomes
across paths, intensities, damage levels, load-growth assumptions, and
recovery trajectories. Report medians, quantiles, exceedance probabilities,
conditional tail expectations, and worst credible outcomes.

### Sampling uncertainty

Only a finite number of scenarios can be evaluated. Repeated sampling,
bootstrap intervals, convergence diagnostics, and effective sample size can
quantify uncertainty in estimated means, quantiles, exceedance rates, and
CVaR. Rare-tail estimation may require stratified or importance sampling
rather than ordinary Monte Carlo sampling.

### Model and epistemic uncertainty

Hazard distributions, fragility curves, load-growth trajectories, recovery
models, and correlation assumptions may themselves be uncertain. A narrow
statistical confidence interval does not capture this. Compare alternative
model families, perturb important assumptions, or use an ambiguity set in a
distributionally robust formulation. Report whether technology rankings and
decisions remain stable across those alternatives.

### Numerical and optimization uncertainty

Solver status, residual audits, local-solution sensitivity, and relaxation or
rounding gaps are distinct from event uncertainty. They should be retained and
reported separately so that optimization artifacts are not mistaken for
physical tail variability.

The report should therefore distinguish scenario spread, finite-sample
uncertainty, model sensitivity, and numerical solution quality rather than
calling all four "confidence."

## Decision hierarchy

The optimization must separate decisions by when they are made and what
information is available.

### Pre-event decisions

- storage siting, power rating, and energy capacity;
- geographic diversification of generation;
- firm capacity and reserve requirements;
- transmission reinforcement and infrastructure hardening;
- elevated-temperature equipment ratings;
- spare-equipment, fuel, and supply-chain contracts;
- demand-response capability; and
- customer-priority and load-shedding policies.

### Adaptive operating decisions

- generation dispatch and renewable curtailment;
- storage charging, discharging, and reserve management;
- demand response and prioritized load service;
- network topology and transfer decisions; and
- revised energy targets as forecasts and system state evolve.

### Recovery decisions

- repair prioritization and crew allocation;
- temporary generation and network configurations;
- replacement procurement;
- staged resource restoration; and
- operating policies during partial recovery.

### Adaptive investments

Some investments can be deferred until hazard trends, realized damage, or
recovery rates become observable. The long-range result may therefore be an
adaptive pathway with decision triggers rather than one irreversible plan
selected at the beginning of the study.

## Selecting technology combinations

The technology question is naturally a portfolio problem. Candidate resources
may include:

- short- and long-duration storage;
- geographically diversified renewable generation;
- firm or dispatchable generation;
- transmission reinforcement and alternate delivery paths;
- microgrids and distributed resources serving critical loads;
- demand response and customer-priority programs;
- hardened assets and elevated-temperature equipment ratings;
- mobile generation and spare transformers;
- fuel, replacement-component, and repair-service contracts; and
- repair crews, inventories, and other restoration resources.

The initial study can compare a frozen set of candidate portfolios. A later
planning layer can optimize continuous capacities and discrete siting,
construction, hardening, and contracting choices.

Technology selection should not be based only on expected outage cost. The
analysis can instead produce a Pareto frontier or minimize cost subject to
explicit limits on quantities such as:

- expected and tail energy not served;
- critical-load interruption;
- outage depth and duration;
- geographic or customer-class disparity;
- restoration time; and
- performance across an ambiguity set of compound-event models.

The useful answer is not only which portfolio has the lowest expected cost.
It is which combinations prevent severe outcomes, protect critical services,
recover effectively, and retain their advantage when hazard, damage,
correlation, recovery, and load-growth assumptions change.

## Optimization formulations

A generic risk-aware planning problem has the form

\[
\min_{x,\pi}
\left[
C_{\mathrm{investment}}(x)
+ \mathbb{E}_{\xi} C_{\mathrm{operation}}(x,\pi,\xi)
+ \lambda\,R(x,\pi,\xi)
\right],
\]

where:

- \(x\) contains long-range pre-event decisions;
- \(\pi\) is an adaptive operating and investment policy;
- \(\xi\) describes the event and information realization; and
- \(R\) is a tail-risk measure determined by the scientific question.

Candidate treatments, in increasing order of commitment, include:

1. **Scenario analysis:** evaluate a common plan over a frozen spectrum of
   events.
2. **Stochastic optimization:** optimize expected performance with
   scenario-dependent recourse.
3. **Risk-aware optimization:** include CVaR, exceedance limits, or explicit
   resilience constraints.
4. **Robust or distributionally robust optimization:** protect performance
   over an uncertainty set, including uncertainty in event probabilities.
5. **Adaptive planning:** optimize staged decisions tied to observable
   thresholds or updated information.

No risk measure is neutral. Expected cost, conditional tail loss, worst-case
loss, regret, and threshold reliability encode different preferences and
should be reported explicitly rather than collapsed into an unexplained
single objective.

## Role of the hierarchical architecture

The CVXOPF hierarchy makes large scenario studies plausible:

- single-node or network-DC formulations can screen many years, portfolios,
  and hazard realizations;
- detailed AC solves can focus on consequential event intervals, damaged
  regions, and restoration transitions; and
- shared physical states, especially storage state of charge, can coordinate
  the fidelity layers.

It is unnecessary to run every ordinary interval at full AC fidelity. The
expensive model should be concentrated where network physics may change the
resilience conclusion.

For compound events, the coarse long-horizon trajectory must remain the
authoritative carrier of state between high-fidelity intervals. Detailed AC
subproblems should inherit the correct damage state, resource availability,
and energy state rather than being initialized as independent events.

## Batched convex optimization for uncertainty ensembles

The recent open-source release of
[Moreau](https://github.com/moreau-project/moreau) introduces a potentially
important computational path. Moreau is a GPU-native conic solver designed
for parameterized, batched, and differentiable convex optimization, with a CPU
backend as well. It was designed by core CVXPY developers around CVXPY's
canonicalization, parameterization, and execution model rather than added as
an unrelated GPU solver interface.

The principal opportunity is not necessarily to accelerate one optimization
problem. It is to solve large batches of structurally identical convex
problems while reusing the common model structure and limiting repeated
canonicalization and host--device transfer.

### Natural batch dimensions

Rare-event and compound-event studies generate candidate batches across:

- hazard paths and geographic footprints;
- event intensity and duration;
- conditional damage fractions;
- recovery trajectories;
- load-growth assumptions;
- alternative fragility and recovery models;
- bootstrap or importance-sampling realizations;
- risk-aversion and penalty weights;
- compatible technology portfolios represented through parameters; and
- combinations of these dimensions.

A representative workload has the form

\[
\text{portfolio}
\times \text{event path}
\times \text{damage realization}
\times \text{recovery model}
\times \text{statistical replicate}.
\]

These are often not unrelated problems. Within an event family they may have
the same variables, constraint structure, and objective form, differing only
in parameter values.

### Scientific value

Greater convex-solve throughput can improve the uncertainty study itself by
allowing us to:

- sample conditional tails more densely;
- reduce finite-sample uncertainty in estimated metrics;
- retain more geographically detailed paths and damage fields;
- evaluate more sequential and compound-event realizations;
- preserve alternative fragility and recovery models;
- perform targeted or importance sampling in consequential tail regions;
- test technology-portfolio ranking stability; and
- estimate exceedance probabilities and CVaR more reliably.

This distinction is important:

> Additional computation can reduce sampling uncertainty; it does not remove
> uncertainty in the hazard, fragility, damage, load-growth, or recovery
> model.

Model uncertainty still requires sensitivity analysis, competing model
families, ambiguity sets, or distributionally robust formulations.

### Placement in the hierarchy

Moreau would apply only to compatible convex layers:

- single-node economic dispatch;
- lossy-DC network planning;
- a future SOCP network formulation;
- convex technology-sizing models; and
- convex relaxations used before rounding and polishing.

It would not replace IPOPT for nonlinear AC realization. The intended flow is:

```text
batched event and portfolio parameters
                    |
                    v
GPU-batched convex outer solves
(single-node / lossy DC / future SOCP)
                    |
                    v
tail screening and consequential-case selection
                    |
                    v
parallel detailed AC realization and audit
```

This reinforces the multiscale strategy: use large batches of lower-cost
convex models to explore the uncertainty space, then concentrate nonlinear AC
work on the event intervals and portfolios capable of changing the scientific
conclusion.

### Structural requirements and limits

A solver batch requires a common canonical structure. Scenario variation
should be expressed through typed parameters where scientifically valid,
including:

- load and renewable availability;
- generator and storage derating;
- branch capacity;
- damage and recovery multipliers; and
- cost, penalty, or risk weights.

Topology changes, different device fleets, incompatible horizon lengths, or
different constraint families may require separate compilations and batches.
Cases should therefore be grouped by a declared structural signature. We
should not alter physical semantics merely to force unlike scenarios into one
batch.

CVXPY disciplined parameterized programming compliance, parameter-update
cost, canonicalization reuse, GPU memory, and host--device transfer must be
measured rather than assumed. End-to-end throughput is the relevant metric,
not solver-kernel time alone.

### Differentiability

Moreau's differentiable interface may eventually provide sensitivities of
optimized outcomes to storage capacity, transmission ratings, resource
availability, recovery time, load growth, technology cost, or risk-policy
parameters. These derivatives could guide continuous technology sizing,
identify consequential uncertain inputs, and support adaptive scenario
selection.

Sensitivity interpretation will require care near active-set transitions,
nonsmooth costs, and tail-risk statistics. Differentiability is therefore a
research opportunity, not a prerequisite for the first batching pilot.

### Adoption gate

No immediate production dependency is proposed. A controlled evaluation
should:

1. instrument current construction, canonicalization, solve, transfer, and
   extraction time;
2. identify DPP-compliant event parameters and graph-breaking dimensions;
3. establish CPU/Clarabel reference solutions and independent residual audits;
4. compare Moreau CPU and GPU objective, variable, and residual parity;
5. benchmark batch size, horizon, network size, memory use, GPU utilization,
   and end-to-end throughput; and
6. test one representative uncertainty ensemble before deciding whether to
   retain Moreau as an optional acceleration backend.

## Outputs and evaluation metrics

Candidate portfolios should be compared using both cost and physical outcome
metrics, including:

- energy not served by geography and customer class;
- depth and duration of service loss;
- violations of critical-service thresholds;
- storage exhaustion, throughput, and replenishment time;
- renewable curtailment and stranded generation;
- voltage-support and congestion exposure;
- restoration time and repair-resource requirements;
- expected consequence and conditional tail consequence;
- worst credible consequence; and
- regret relative to the best plan for each realized event.

Each reported statistic should be accompanied, where applicable, by:

- a finite-sample uncertainty interval;
- sensitivity across alternative hazard and recovery models;
- the number and weighting of contributing scenarios; and
- numerical acceptance and optimization-quality diagnostics.

The final result should identify:

- investments justified across nearly all modeled tail assumptions;
- investments justified only beyond a stated severity or risk tolerance;
- plans that depend strongly on a particular path or recovery assumption;
- dominated portfolios; and
- the value of retaining adaptive options.

## Practical research sequence

1. Define one controlled heat-wave family and one destructive-storm family.
2. Define at least one sequential compound event, such as load growth plus a
   heat wave, tropical-storm damage, partial recovery, and recurrent heat.
3. Specify which physical, operational, inventory, and recovery states persist
   throughout the complete horizon.
4. Generate spatially and temporally correlated footprints, impacts, and
   recovery paths.
5. Freeze a small candidate set of technology portfolios and adaptive
   operating policies.
6. Evaluate those candidates across the event spectrum using coarse models.
7. Select consequential intervals, transitions, and realizations for detailed
   AC realization without resetting their inherited state.
8. Quantify outcome distributions, finite-sample uncertainty, model
   sensitivity, numerical quality, and portfolio regret separately.
9. Compare expected, conditional-tail, robust, and regret-based rankings.
10. Determine which uncertainty dimensions materially change the preferred
    decisions.
11. Instrument structurally repeated convex workloads and evaluate an
    optional Moreau CPU/GPU batching pilot with numerical-parity gates.
12. Use batching, if validated, to increase ensemble size and reduce
    finite-sample uncertainty without narrowing the represented model
    uncertainty.
13. Add continuous sizing decisions where convex formulations permit them.
14. Add discrete siting, construction, hardening, or contracting decisions
    through an appropriate outer method, potentially relax--round--polish or a
    mixed-integer planning model.
15. Re-evaluate selected portfolios with fixed-decision AC polishing.

## Current capability and required extension

CVXOPF can already evaluate operating policies and prescribed candidate
portfolios across long scenarios, with detailed AC realization of selected
periods. Fully optimizing investment selection requires an additional
planning layer for siting, sizing, construction, contracting, and other
discrete or long-lived decisions.

Accordingly, the near-term question is candidate-portfolio evaluation and
robust ranking. The longer-term question is end-to-end optimization of the
portfolio and its adaptive policy.

## Intended answer to the motivating question

The framework should ultimately answer:

> Which long-range investments and adaptive policies are optimal for an
> explicitly stated risk posture, and which remain valuable when the
> probabilities, footprints, damage levels, and recovery trajectories of
> major heat waves and other black-sky events are deeply uncertain?

and:

> Which technology combinations minimize outages and outage impacts when
> sequential and compound stressors act on a system that may still be damaged,
> resource-constrained, or incompletely recovered from preceding events, and
> how confident are we that those conclusions persist across sampling, model,
> and numerical uncertainty?

The most defensible answer will be a cost--resilience frontier and a set of
robust decisions, not a single plan presented as universally optimal under a
fragile far-tail probability estimate.
