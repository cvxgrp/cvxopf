# PyPSA and CVXOPF: compare and contrast

**Review date:** 2026-08-26

## Purpose

This document compares PyPSA and CVXOPF based on their current documented
capabilities and design centers. It focuses on the questions relevant to
long-horizon storage, rare and compound resilience events, generator cost
representation, price inputs, network fidelity, and computational scale.

The two packages are not interchangeable competitors. PyPSA is a mature,
large-scale energy-system planning platform centered on linearized network
optimization. CVXOPF is a newer OPF research framework centered on a common
component model across a network-fidelity ladder, including nonlinear AC
optimization and hierarchical long-horizon-to-AC realization.

That distinction becomes more important under black-sky conditions. Linear
approximations are often most trustworthy near the intact operating regimes
for which they were designed. Severe outages, unusual transfer patterns,
reactive-power scarcity, and operation near voltage or thermal boundaries can
move the system precisely where omitted AC interactions determine whether an
otherwise attractive plan is physically realizable.

## Executive summary

| Question | PyPSA | CVXOPF |
|---|---|---|
| Primary design center | Large-scale energy-system planning and dispatch | Multi-fidelity OPF and resilience-method research |
| Basic scaling formulation | Sparse LP with linearized network physics | Convex coarse planning plus selected nonlinear AC realization |
| Other optimization classes | QP, MILP, and piecewise formulations when enabled | Convex QP and nonconvex DNLP; future convex relaxations planned |
| Full nonlinear AC optimization | No; nonlinear power flow is available after optimization | Yes; nonlinear AC-OPF through CVXPY DNLP/IPOPT |
| Role of AC under severe stress | Post-optimization feasibility evaluation at a selected dispatch | Re-optimizes dispatch, reactive support, and voltage state inside the nonlinear AC feasible set |
| Long-horizon storage | Mature and scalable | Implemented, with explicit terminal policies and hierarchical AC handoff |
| Capacity expansion | Mature, including multi-investment periods | Not yet a general planning layer |
| Generator operating costs | Linear, quadratic, and recent PWL support | Polynomial and PWL MATPOWER-style cost curves |
| Time-varying price/cost inputs | First-class static or snapshot-indexed component data; often used as reduced operating signals | Generator cost curves and physical consequences are primary; no external electricity-price series is required |
| Endogenous nodal prices | Yes, as balance-constraint duals | Available in principle for convex formulations; not the central public result contract discussed here |
| Network-fidelity ladder | Linearized optimization followed optionally by nonlinear power flow | Single-node DC, networked lossy DC, and nonlinear AC-OPF under one component architecture |
| Rare-event uncertainty | Native two-stage stochastic planning and scenario workflows | Proposed batched scenario and hierarchical realization workflow; not yet a general stochastic-planning API |
| Demonstrated scale | Large networks, long horizons, and capacity expansion | Case118 month-scale hierarchy; annual scaling work remains active |

## 1. Different design centers

### PyPSA

PyPSA is designed to co-optimize energy-system operation and investment across
many snapshots, technologies, carriers, and network locations. Its documented
problem classes include economic dispatch, Linear Optimal Power Flow (LOPF),
security-constrained LOPF, capacity-expansion planning, two-stage stochastic
optimization, and modeling-to-generate-alternatives.

These models build on a sparse linearized network representation. Depending on
the supplied data and features, `Network.optimize()` constructs an LP, QP, or
MILP. The ordinary continuous model with linear marginal costs is an LP.
[PyPSA optimization overview](https://docs.pypsa.org/latest/user-guide/optimization/overview/)

### CVXOPF

CVXOPF is designed to express device physics once and use those devices across
multiple network formulations:

- full nonlinear AC-OPF;
- a convex networked lossy-DC planning model; and
- convex single-node economic dispatch.

Its central research use is to connect long-horizon energy planning with
shorter high-fidelity AC realization through shared physical state, especially
battery state of charge. See the local [README](../../README.md) and
[software architecture](../../PROJECT_FLOWCHART.md).

The AC layer is not included merely to refine ordinary-condition power-flow
accuracy. It lets the realization choose a different active/reactive dispatch
and voltage state when a coarse plan encounters damaged or highly stressed
network physics.

CVXOPF does not currently match PyPSA's breadth of sector coupling, investment
planning, ecosystem maturity, or demonstrated scale.

## 2. The basic PyPSA scaling approach is an LP

The canonical PyPSA formulation is Linear Optimal Power Flow. With continuous
variables, linear device constraints, linearized network equations, and
constant linear marginal costs, the complete dispatch or capacity-expansion
problem is a sparse LP:

$$\text{linearized network physics} + \text{linear device constraints} + \text{linear cost} \longrightarrow \text{LP}.$$

PyPSA's cycle-based KVL formulation is chosen in part for sparse computational
performance. Nonlinear AC power flow can be run after optimization, but this
does not convert the multi-period planning problem into nonlinear AC-OPF.
[PyPSA optimization API](https://docs.pypsa.org/latest/api/networks/optimize/)

Current PyPSA is not exclusively linear:

- quadratic marginal costs produce a QP;
- unit commitment and modular investments can produce a MILP; and
- piecewise relationships can use LP, incremental, SOS2, or disjunctive
  formulations.

Those features extend an architecture whose characteristic large-scale path
remains LP-centered.

## 3. Network physics

### PyPSA optimization

PyPSA's standard network optimization uses linearized active-power network
constraints. This is appropriate for large-scale planning, market, and
capacity-expansion studies in which tractability across many nodes and
snapshots is the principal requirement.

PyPSA can subsequently run nonlinear power flow on an optimized dispatch. That
post-processing step can identify voltage or reactive-power issues, but it is
not the same as optimizing the dispatch subject to the nonlinear AC feasible
set.

### CVXOPF fidelity ladder

CVXOPF exposes three current formulations:

1. **Single-node DC:** aggregate active-power balance with no network,
   congestion, voltage, reactive power, or transmission loss.
2. **Lossy DC:** convex network-aware active-power planning with nodal balance,
   branch limits, device location, and a resistance-weighted quadratic flow
   penalty. Its nodal conservation remains lossless; the loss term is a proxy
   or regularizer rather than physical branch-loss withdrawal.
3. **AC:** nonlinear voltage-magnitude and angle variables, active and reactive
   nodal balance, fixed transformer data, and two-terminal apparent-power
   branch limits.

The AC formulation is nonconvex and solved locally with IPOPT. An accepted
solution is not a global-optimality certificate.

### Why AC optimization matters for black-sky events

Access to the nonlinear AC formulation is particularly valuable when the
system is far from its ordinary operating regime. Large, geographically
bounded outages can change transfer paths, reactive-power availability,
voltage support, losses, and which terminal of a branch is thermally limiting.
High load and equipment derating can place the system near voltage and
apparent-power boundaries that an active-power linearization does not model.

The distinction between nonlinear power flow and nonlinear AC-OPF is central:

- **Post-optimization power flow** asks whether one previously selected
  dispatch has an AC solution.
- **AC-OPF realization** searches for a different dispatch, reactive
  allocation, and voltage state that satisfies the AC equations while meeting
  the modeled operating objective and constraints.

Thus, a coarse plan that fails a nonlinear power-flow check is not necessarily
physically impossible. It may require redispatch, reactive support, load
service changes, or a different use of storage. Conversely, a plan that looks
adequate under aggregate or linear active-power constraints may fail because
the damaged network lacks voltage support, reactive capability, or
two-terminal apparent-power headroom.

It is more accurate to say that CVXOPF represents the full nonlinear AC-OPF
feasible set than to say that it exhaustively accesses the entire AC solution
space. IPOPT is a local solver; initialization and local nonconvex geometry
still matter. CVXOPF's causal warm-start recovery and independent residual
audits address operational robustness and scientific interpretation, but do
not create a global certificate.

AC-OPF is also not a complete blackout or cascading-failure simulator. The
current quasi-steady-state model does not replace transient stability,
protection, frequency, electromagnetic, unbalanced distribution, or cascading
outage analysis. Its contribution is narrower and still important: it tests
and optimizes steady-state active/reactive network feasibility during the
consequential portions of a long event history.

## 4. Generator costs and the duck curve

### Constant linear cost

Suppose one dispatchable generator has cost

$$C(P_t)=cP_t.$$

Moving one MWh of production from a high-output hour to a low-output hour does
not change generation cost:

$$c(P_{\mathrm{low}}+1)+c(P_{\mathrm{high}}-1)=cP_{\mathrm{low}}+cP_{\mathrm{high}}.$$

If storage is lossy, the shift may increase total cost. A model with one
constant linear marginal generator cost therefore has no inherent reason to
level that generator's output unless storage also:

- crosses into a different generator's merit-order block;
- avoids renewable curtailment or spillage cost;
- relieves congestion;
- avoids a capacity, ramping, reserve, or reliability constraint; or
- serves another explicit objective term.

This is not a storage-model defect. It is the economic consequence of a flat
marginal-cost objective.

### Convex output-dependent cost

For a quadratic generator cost,

$$C(P_t)=c_0+c_1P_t+c_2P_t^2,\qquad c_2>0,$$

the marginal cost rises with output:

$$C'(P_t)=c_1+2c_2P_t.$$

Storage can then reduce total operating cost by shifting production from a
high-marginal-cost hour to a low-marginal-cost hour, subject to efficiency,
network, power, energy, and terminal constraints. Levelization emerges from
the system-cost model rather than from an imposed price trajectory.

A convex PWL cost curve produces the same qualitative effect when storage
moves dispatch between segments with different slopes.

### PyPSA cost capabilities

PyPSA's standard generator data support:

- static or time-varying linear `marginal_cost`;
- static or time-varying `marginal_cost_quadratic`; and
- recent native PWL marginal-cost curves.

The current documented PWL generator operating-cost interface applies to
fixed, non-extendable generator capacity. Depending on the curve and selected
formulation, the representation may remain an LP or may require a stronger
piecewise formulation.
[PyPSA piecewise documentation](https://docs.pypsa.org/latest/user-guide/optimization/piecewise/)

Consequently, it is no longer accurate to say that every current PyPSA model
is restricted to one linear marginal cost per generator. It is accurate to say
that constant linear generator costs are the common and simplest LP-centered
configuration, and that this configuration may not create an endogenous
incentive to level output within one generator's range.

### CVXOPF cost capabilities

CVXOPF treats polynomial and PWL MATPOWER-style generator curves as native OPF
data. The scientific specification can therefore assign output-dependent
generation cost without supplying an external hourly electricity-price
trajectory.

CVXOPF still minimizes the declared objective; it does not flatten net load
for aesthetic reasons. If load leveling itself is the scientific objective,
the model should include an explicit convex peak, ramp, variance, or deviation
penalty.

## 5. Prices as inputs and outputs

### PyPSA

Exogenous price-like quantities are first-class PyPSA inputs. Component
`marginal_cost` may be static, snapshot-indexed, or in supported cases
piecewise. It enters the objective directly for generators, storage units,
stores, links, and processes.

PyPSA's `Store` documentation explicitly identifies external-market trading
prices as one use of signed store marginal cost. PyPSA's stochastic example
also represents gas-price uncertainty through scenario-dependent input costs.
[PyPSA objective](https://docs.pypsa.org/latest/user-guide/optimization/objective/),
[Store component](https://docs.pypsa.org/latest/user-guide/components/stores/),
[stochastic example](https://docs.pypsa.org/latest/examples/stochastic-optimization/)

PyPSA also produces endogenous bus marginal prices as dual values of nodal
balance. Therefore:

| Quantity | PyPSA role |
|---|---|
| Generator fuel or offer price | First-class cost input |
| External-grid purchase or sale price | First-class input through an appropriate component |
| Time-varying price forecast | First-class snapshot-indexed input |
| Scenario-dependent commodity price | First-class stochastic parameter |
| Bus locational marginal price | Endogenous optimization output |

PyPSA does not require an external electricity-price time series for every
solve. It does make such time series ordinary, well-supported model data.

### CVXOPF

CVXOPF's current resilience studies are built from physical and economic
primitives:

- generator cost curves;
- load and renewable availability;
- network limits and losses or loss proxies;
- storage efficiency, cycling cost, and terminal policy;
- load-shedding cost; and
- the evolving system state.

This is useful for black-sky studies because a historical or forecast market
price may be unavailable or scientifically inappropriate after widespread
damage. Storage value can emerge from the modeled damaged system rather than
from a speculative price path.

### Prices as compressed system models

For system planning, an exogenous price trajectory is often being used as a
linear surrogate for a much larger omitted value function. Let $x$ denote a
battery or other flexible-resource decision and let $y$ contain the
remaining network, generation, reliability, and recovery decisions. The full
system value of $x$ is

$$V(x)=\min_y\left\{f(x,y)\;\middle|\;g(x,y)=0,\;h(x,y)\leq 0\right\}.$$

A price-driven model commonly substitutes

$$V(x)\approx \sum_t p_t x_t.$$

At best, $p_t$ is a local derivative of the full value function at one
operating point:

$$p_t \approx \frac{\partial V}{\partial x_t}.$$

It is not generally the value function itself. The substitution assumes that
system value is sufficiently linear, temporally separable, spatially resolved,
and unaffected by the optimized response. It suppresses curvature and
cross-interactions such as

$$\frac{\partial^2 V}{\partial x_t^2},\qquad \frac{\partial^2 V}{\partial x_t\partial x_{t+k}},\qquad \frac{\partial^2 V}{\partial x_{n,t}\partial x_{m,s}},$$

which can encode increasing marginal generation cost, energy coupling,
congestion, shared reserves, recovery bottlenecks, and sequential-event
dependence.

This approximation can be reasonable for a small price-taking participant
under ordinary market conditions. It becomes weaker when the flexible
resource is large enough to change dispatch, congestion, scarcity, or the
identity of the marginal generator. In that case the resource changes the
prices against which it is being optimized, violating the fixed-price
assumption.

The distinction is especially important in black-sky studies. A historical
price series was generated by a different network state, asset fleet, market
design, and damage condition. Asking it to represent widespread outages,
physical scarcity, customer consequences, and months of recovery implicitly
asks an external scalar signal to reconstruct interactions omitted from the
model.

Endogenous prices remain scientifically useful. In a convex system model,
dual variables report local marginal values of explicit constraints and can
support decentralized coordination. However, they:

- are local to the solved operating point;
- change when the active set changes;
- inherit every omission in the underlying model;
- do not automatically value unmodeled resilience consequences; and
- may not support a nonconvex or discrete system optimum through linear prices
  alone.

The preferred scientific ordering for CVXOPF resilience studies is therefore

```text
physical resources, constraints, consequences, and state
                         |
                         v
                  optimized operation
                         |
                         v
             endogenous marginal values
```

The methodological claim is not that prices are unusable. It is that a price
is a local shadow value derived from a model, not a substitute for physical
and intertemporal interactions that should be represented explicitly when
they determine the resilience conclusion.

## 6. Storage and intertemporal operation

### PyPSA strengths

PyPSA has mature intertemporal storage models, including asymmetric charging
and discharging efficiencies, standing losses, inflow, spillage, cyclic state
conditions, and extendable power or energy capacity depending on the selected
component representation.

It is well suited to long-horizon storage dispatch and investment studies when
linearized network physics is appropriate.

### CVXOPF strengths

CVXOPF currently provides an ideal storage device with:

- active/reactive operating limits in AC;
- active-power bounds in DC;
- explicit state-of-charge dynamics;
- cycling cost;
- hard equality or reserve-floor terminal constraints;
- linear or quadratic, one- or two-sided terminal penalties; and
- stable identity for state handoff across separately built problems.

A lossy-storage milestone is planned. PyPSA already ships important lossy
storage capabilities, so lossy storage alone is not a CVXOPF differentiator.

CVXOPF's distinctive emphasis is using a long-horizon convex storage plan to
coordinate audited rolling AC-OPF realization without requiring the AC
dispatch to reproduce the coarse dispatch trajectory exactly.

## 7. Long horizons and compound events

Both packages can represent storage state across long time horizons. CVXOPF's
research thesis emphasizes long horizons as the technical enabler for compound
events:

```text
event 1
   -> damage and operating response
   -> partial recovery and resource consumption
   -> event 2
   -> compound consequence
```

The relevant carried state may include storage energy, asset availability,
repair progress, fuel or spare inventories, and supply-chain delays.

PyPSA has the more mature large-scale planning substrate for expressing long
sequences and investment decisions. CVXOPF has the higher-fidelity nonlinear
AC realization layer and explicit hierarchical state-handoff machinery. A
future scientific workflow could use either package independently or use
their ideas complementarily.

For compound-event studies, these roles operate at different temporal scales.
The long convex trajectory preserves damage, recovery, inventory, and storage
state between events. Selected AC intervals then determine whether the system
can physically realize service and recovery decisions under the stressed
network. The AC intervals must inherit the long-horizon state; treating them
as independent snapshots would erase the compound-event mechanism.

## 8. Uncertainty

### PyPSA

PyPSA documents native two-stage stochastic optimization in which investments
are first-stage decisions and dispatch is scenario-dependent recourse. It also
has established capacity-expansion and multi-investment-period machinery.

This makes PyPSA materially ahead of CVXOPF for general stochastic investment
planning today.

### CVXOPF

CVXOPF does not yet provide a general public stochastic-programming or
capacity-expansion API. The proposed rare-event workflow is:

1. generate spatially and temporally correlated event ensembles;
2. batch or parallelize compatible convex single-node or lossy-DC scenarios;
3. screen portfolios and tail outcomes;
4. retain persistent state across compound events; and
5. realize consequential intervals through audited nonlinear AC-OPF.

This last step is not merely a higher-resolution plot. It can change which
portfolios or policies remain acceptable when the uncertainty ensemble
contains unusual topology, geographically concentrated damage, reactive
scarcity, or near-limit transfers.

The potential Moreau backend is an investigation, not a current dependency or
demonstrated CVXOPF capability. Its relevance is GPU-batched solution of
structurally identical convex event ensembles, not nonlinear AC-OPF.

## 9. Investment planning

PyPSA can co-optimize generation, storage, conversion, and transmission
capacity, including multiple investment periods. This is a core capability.

CVXOPF can currently evaluate prescribed device fleets and candidate
portfolios. Fully optimizing siting, sizing, construction, hardening,
contracting, or repair inventory requires a new planning layer. Continuous
sizing may fit a convex outer formulation; discrete decisions may require
mixed-integer optimization or relax--round--polish.

Any external comparison should state this asymmetry plainly.

## 10. Computational scale

### PyPSA

PyPSA's LP-centered approach, Linopy implementation, and mature solver
interfaces are designed for large spatial and temporal models. Its established
use cases include national and continental energy-system studies.

### CVXOPF

CVXOPF deliberately pays more for physical fidelity. Nonlinear multistep AC
graphs become expensive in construction, memory, and solve time. The current
scaling program includes:

- vectorizing time-indexed model construction;
- coarse-to-fine formulation hierarchy;
- causal warm-start and recovery policies;
- supervised process recycling; and
- parallel temporal decomposition of selected AC realization intervals.

The demonstrated Case118 hierarchy has reached month scale. Annual and
multi-year network studies remain development goals rather than completed
capabilities.

## 11. Solver guarantees and audits

PyPSA LPs and convex QPs can be solved to global optimality within solver
tolerances. MILPs provide global bounds and, when completed, optimality gaps.
Results still depend on model validity, input uncertainty, and numerical
conditioning.

CVXOPF's convex formulations likewise have global convex guarantees when
accepted. Its AC formulation is nonconvex; IPOPT returns local solutions or
failures, not global certificates. CVXOPF therefore retains independent
physical residual audits and has developed causal initialization-recovery
policies for sequential AC operation.

In one controlled branch-limit-neutralized case, cross-evaluation established
that a CVXOPF solution was feasible in the matched Pypower problem and at
least 14.3% cheaper than Pypower's returned point. This proves that particular
Pypower point was suboptimal; it does not prove CVXOPF's point globally
optimal or establish a universal superiority claim.

## 12. When each package is the more natural choice

### Prefer PyPSA when the primary question is

- national or continental capacity expansion;
- sector-coupled energy-system planning;
- many investment periods;
- large stochastic planning models;
- mature LP/MILP workflows; or
- broad technology and carrier coverage under linearized network physics.

### Prefer CVXOPF when the primary question is

- whether a long-horizon plan is realizable under nonlinear AC physics;
- resilience or black-sky operation far from an intact-system linearization;
- active/reactive power, voltage, and apparent-power behavior during critical
  periods;
- formulation research across single-node, network-DC, and AC models;
- storage terminal-value and state-handoff questions;
- hierarchical convex-planning-to-AC control; or
- controlled study of DNLP canonicalization and nonlinear local-solver
  behavior.

### A complementary workflow

A future combined workflow could use PyPSA for mature large-scale investment
planning and CVXOPF for selected nonlinear AC realization. Such a coupling
would require explicit identity, units, topology, state, and acceptance
contracts; it should not be assumed to be automatic.

## 13. Claims that are supported

- PyPSA's basic scaling path is a sparse LP using linearized network physics.
- Current PyPSA can also formulate QPs, MILPs, and piecewise models.
- Exogenous marginal-cost and price time series are first-class PyPSA inputs.
- PyPSA also reports endogenous nodal marginal prices.
- An exogenous price trajectory is a reduced linear signal, not a complete
  representation of the system value function that generated it.
- Endogenous dual prices are useful local diagnostics but inherit the scope,
  assumptions, and omissions of their underlying model.
- Constant linear generator costs may provide no incentive to level output
  within one generator's dispatch range.
- Quadratic or convex PWL generator costs can create endogenous temporal value
  for storage without an external electricity-price series.
- CVXOPF natively optimizes nonlinear AC physics and uses convex formulations
  for longer-horizon coordination.
- AC-OPF realization can redispatch within the nonlinear feasible set; a
  post-optimization nonlinear power flow evaluates only the supplied dispatch.
- PyPSA is substantially more mature for capacity expansion and large-scale
  stochastic planning.
- CVXOPF is substantially more specialized around nonlinear AC realization
  and a common multi-fidelity component architecture.

## 14. Claims to avoid

- **Avoid:** "PyPSA cannot model storage over time."
  It has mature intertemporal storage and investment models.
- **Avoid:** "PyPSA can only solve LPs."
  LP is the basic path, but current PyPSA also supports QP, MILP, and PWL
  formulations.
- **Avoid:** "PyPSA requires an electricity-price series."
  It does not. Price and marginal-cost series are first-class optional inputs.
- **Avoid:** "A supplied price series faithfully preserves omitted network,
  reliability, and recovery interactions."
  It is at most a reduced, regime-dependent signal from another system model.
- **Avoid:** "PyPSA cannot represent output-dependent generator costs."
  Current versions support quadratic and recent PWL operating costs.
- **Avoid:** "CVXOPF already scales better than PyPSA."
  It does not; PyPSA has the stronger demonstrated large-scale planning record.
- **Avoid:** "CVXOPF AC solutions are globally optimal."
  They are accepted local nonlinear solutions.
- **Avoid:** "Lossy storage differentiates CVXOPF from PyPSA."
  PyPSA already supports asymmetric efficiency and standing loss.
- **Avoid:** "The two tools answer the same scientific question."
  Their current design centers and fidelity--scale tradeoffs differ.

## 15. Bottom line

PyPSA scales by keeping the core planning problem sparse and usually linear.
That is a major strength, especially for investment, stochastic, and
sector-coupled studies. Recent QP and PWL features reduce the limitations of a
constant-linear-cost model without changing PyPSA's overall planning focus.

CVXOPF is pursuing a different compromise: retain a common scientific model
across coarse convex formulations and full nonlinear AC-OPF, then use
hierarchy and temporal decomposition to spend AC fidelity only where it can
change the conclusion.

That selective AC access is especially relevant to resilience. Severe damage
and compound stress can invalidate ordinary-condition assumptions precisely
when voltage, reactive power, losses, and apparent-power limits become
decision-determining. The hierarchy is intended to preserve years of state
without paying for years of AC solves, while still allowing the critical
intervals to be re-optimized inside the nonlinear AC formulation rather than
merely checked at a fixed coarse dispatch.

For unprecedented rare and compound events, CVXOPF's intended advantage is
not that it forbids price inputs. It is that storage and reliability value can
be derived from physical scarcity, output-dependent production costs, network
constraints, damage and recovery state, load-shedding consequences, and
terminal obligations without requiring a speculative hourly market-price
trajectory.

From a physics and mathematical perspective, an imported price trajectory
attempts to summarize interactions that have been left outside the model. The
more strongly a decision changes those interactions, the less defensible the
fixed-price approximation becomes. CVXOPF's resilience direction is to model
the consequential interactions explicitly and treat marginal values as
outputs of that model wherever possible.

## Primary external sources

- [PyPSA optimization overview](https://docs.pypsa.org/latest/user-guide/optimization/overview/)
- [PyPSA optimization objective](https://docs.pypsa.org/latest/user-guide/optimization/objective/)
- [PyPSA piecewise constraints](https://docs.pypsa.org/latest/user-guide/optimization/piecewise/)
- [PyPSA generator component](https://docs.pypsa.org/stable/user-guide/components/generators/)
- [PyPSA storage-unit component](https://docs.pypsa.org/latest/user-guide/components/storage-units/)
- [PyPSA store component](https://docs.pypsa.org/latest/user-guide/components/stores/)
- [PyPSA stochastic-optimization example](https://docs.pypsa.org/latest/examples/stochastic-optimization/)
- [PyPSA optimization API](https://docs.pypsa.org/latest/api/networks/optimize/)
