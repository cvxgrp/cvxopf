# Physical coordination from modeled primitives

## Purpose

This note states a research proposition motivating CVXOPF:

> A complex, intertemporal energy system can be coordinated directly from its
> physical capabilities, explicit operating costs, reliability priorities, and
> evolving state.

This is a constructive and testable proposition. It begins with the causal
structure of the energy system rather than treating any particular
coordination mechanism as the conceptual baseline. Prices, contracts,
decentralized decisions, and other institutional arrangements may still be
represented when they are relevant to the scientific question.

## Model the causal structure directly

CVXOPF builds dispatch decisions from explicit physical and economic
primitives, including:

- generator operating regions and cost curves;
- load and renewable availability;
- network topology, operating limits, and either physical losses or documented
  loss proxies;
- storage power, energy state, cycling cost, and terminal policy;
- load-shedding eligibility, limits, and consequences; and
- the realized state inherited from earlier decisions and events.

These quantities determine what the system can do and what consequences follow
from its decisions. When they are available, representing them directly is
more scientifically informative than asking an external scalar signal to
stand in for their combined effect.

## Prices and marginal values have a narrower role

The distinction is not between using economics and ignoring economics.
Generator costs, interruption consequences, storage opportunity value, and
terminal obligations are economic parts of the modeled problem.

The distinction concerns the role assigned to prices:

- An **exogenous price forecast** is supplied to the model and used to drive
  decisions.
- An **endogenous marginal value** follows from a specified optimization
  problem and describes the local value of relaxing one of its constraints.

Endogenous marginal values can be useful diagnostics, accounting quantities,
or inputs to an implemented coordination mechanism. They remain conditional
on the modeled network, device fleet, objective, constraints, state, and
operating rules. They do not replace those structures.

## Mathematical formulation and temporal incentives

The widespread use of price-driven asset models is partly encouraged by
tractable linear formulations. If a storage device is separated from the
physical system and assigned an objective such as

$$\min_b \sum_t \pi_t b_t,$$

the supplied price trajectory carries nearly all information about when
charging and discharging should be valuable. By contrast, a system model can
co-optimize production and storage from generator costs, balance, network
constraints, and storage dynamics:

$$\min_{p,b} \sum_t C(p_t).$$

When production cost is strongly convex, redistributing generation across time
can reduce modeled cost directly. Storage can therefore levelize dispatch in
response to load, renewable availability, congestion, and system state without
requiring an externally supplied temporal value signal.

Linear programming does not inherently require exogenous prices. System-wide
linear programs can co-optimize devices and networks, piecewise-linear costs
can produce changing marginal values, and scarcity or congestion can create
temporal and spatial differentiation. The narrower concern is the combination
of linearized production costs, decomposed price-taking asset models, omitted
physical interactions, and the treatment of a resulting price series as a
sufficient statistic for the omitted system.

## Why the distinction matters for rare events

A historical electricity-price trajectory was produced under a particular
network state, asset fleet, market design, demand pattern, and set of operating
rules. A destructive event may change all of them at once. For example, it may
remove a geographically concentrated fraction of generation, damage
transmission, raise demand, constrain fuel or replacement supply, and create a
recovery process lasting months.

Under those conditions, a historical price signal is not merely uncertain. It
may describe a materially different system. Asking it to represent physical
scarcity, customer consequences, sequential damage, and recovery implicitly
asks the signal to reconstruct interactions omitted from the model.

A physical-state model instead updates the affected capabilities and solves
the resulting coordination problem. Uncertainty can be represented through
event ensembles, uncertain damage and recovery trajectories, alternative
technology portfolios, and repeated adaptive solves.

## Long horizons and evolving state

Energy coordination is not a sequence of independent interval decisions.
Storage depletion, repair, fuel availability, cumulative customer impact, and
other state variables connect earlier actions to later feasibility.

Long-horizon models make these connections explicit. They allow the study to
ask whether a policy:

- preserves energy for later scarcity;
- remains viable across sequential or compound events;
- adapts to realized damage and recovery;
- shifts consequences among locations or customer groups; and
- produces acceptable outcomes over the complete event rather than only the
  next dispatch interval.

## Hierarchical coordination

No single formulation must resolve every physical detail over the entire
horizon. CVXOPF's hierarchical method separates complementary tasks:

1. A long-horizon convex model coordinates the intertemporal states that must
   remain globally coherent.
2. A shorter-horizon nonlinear AC model realizes decisions within detailed
   active-power, reactive-power, voltage, and network constraints.
3. Realized state is returned to the planning layer for subsequent decisions.

The layers should exchange only the information required for cross-scale
coordination. Over-specifying the detailed trajectory can convert harmless
approximation differences into artificial infeasibility. Preserving local
degrees of freedom allows the higher-fidelity layer to absorb variation while
respecting the essential state handoff.

The resulting engineering principle is:

> Coordinate invariants; do not micromanage trajectories.

In the current battery hierarchy, state-of-charge signposts provide temporal
coherence while the AC layer remains free to redispatch generation,
nondispatchable output, storage power, reactive support, and voltage state.

## What would constitute evidence

The proposition should be evaluated through controlled studies, not asserted
from the formulation alone. Relevant evidence includes:

- completion of long and compound event trajectories;
- independently audited physical feasibility;
- energy-not-served and other consequence metrics;
- preservation or recovery of critical state variables;
- performance across uncertain damage and recovery ensembles;
- comparisons among technology portfolios and control policies;
- sensitivity to modeling assumptions and objective choices; and
- computational cost, failure modes, and recovery behavior.

Comparisons with price-driven controllers may also be informative, provided
the price inputs, information available to each controller, and acceptance
criteria are specified consistently.

## Boundaries of the claim

Successful coordination would establish the performance of the modeled
physical, economic, and control architecture. One implication would be that an
externally forecast market-clearing price is not mathematically necessary for
that operating problem. The result would not by itself establish that:

- the chosen objective captures every social priority;
- the input costs are uniquely correct;
- one organizational arrangement should govern the system;
- centralized information is complete or costless;
- every relevant behavior has been modeled; or
- the resulting plan is globally optimal when nonlinear AC optimization is
  used.

Those are separate scientific, institutional, and normative questions. One
advantage of the explicit modeling approach is that the assumed objectives,
constraints, priorities, and omissions remain visible and open to revision.

## Research direction

CVXOPF provides a platform for testing whether primitive-based, adaptive, and
physically audited coordination can manage increasingly demanding energy
resilience problems. The next research dimensions include:

- uncertain and geographically resolved damage ensembles;
- sequential and compound events;
- long recovery trajectories;
- technology-portfolio selection under tail risk;
- additional consequence metrics and customer priorities;
- convex screening followed by selected nonlinear AC realization; and
- scalable execution through vectorization, batching, and temporal
  decomposition.

The central question remains empirical:

> How effectively can explicit models of system capabilities, costs,
> consequences, and evolving state coordinate energy provisioning across
> ordinary operation, severe disruption, and recovery?
