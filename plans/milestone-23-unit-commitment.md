# Milestone 23 — Unit commitment

**Status:** planned

**Depends on:** Milestone 16 and M16+ (first-class dispatchable generators,
typed component contributions, and shared formulation assembly), Milestone 5
and Milestone 12 (storage state and terminal-state policies)

**Related to:** Milestone 14 (time-vectorized long-horizon convex models),
Milestone 17 (validated lossy-DC-to-AC realization), and Milestone 21
(configurable typed formulation hierarchies)

**Rounding-method reference:** Dolores Gómez, Simone Göttlich, Alfredo
Ríos-Alborés, and Pilar Salgado, “Relax-and-round strategies for solving the
unit commitment problem with AC power flow constraints,” *Optimization and
Engineering* (2026),
[doi:10.1007/s11081-026-10089-2](https://doi.org/10.1007/s11081-026-10089-2),
reviewed 2026-09-04. The paper calls raw $0.5$ rounding naive rounding and
introduces the plant-parameter and power-space rescalings adopted below as
MVP comparison policies. Its empirical results motivate the comparison but do
not establish their performance in cvxopf's different convex planning and
perspective-cost model.

## 1. Goal

Add a continuous convex relaxation of generator on/off commitment to the
`lossy_dc` and `singlenode_dc` formulations, together with a deterministic,
audited **relax–round–polish** solve procedure.

The completed convex procedure produces:

1. a fixed on/off commitment schedule;
2. a feasible polished convex dispatch under that schedule; and
3. polished storage state-of-charge (SoC) signposts.

An explicitly selected hierarchical workflow may then pass the fixed
commitment schedule and SoC signposts into short-window AC-OPF realization:

$$
\underbrace{\text{DC relax}\rightarrow\text{partial round}\rightarrow
\text{resolve}\rightarrow\text{final round}\rightarrow\text{DC polish}}
_{\text{long-horizon commitment and storage planning}}
\quad\longrightarrow\quad
\underbrace{\text{short-window AC-OPF realization}}
_{\text{fixed commitment, nonlinear network physics}}.
$$

The AC solve is not the polish step. Polishing occurs in the convex planning
formulation after every relaxed commitment value has been converted to a fixed
Boolean decision. AC subsequently reoptimizes continuous active power,
reactive power, and storage operation under the fixed commitment schedule and
the ordinary M17-style SoC targets.

This milestone establishes a bounded, inspectable heuristic for producing a
physically realizable schedule. It does not claim to solve the mixed-integer
unit-commitment problem globally.

## 2. MVP scientific model

### 2.1 Commitment disjunction

For each eligible in-service dispatchable generator $g$ and interval $t$,
introduce a commitment variable $u_{g,t}$. The exact on/off feasible set is

$$
u_{g,t}\in\{0,1\},
\qquad
P_g^{\min}u_{g,t}\leq P_{g,t}\leq P_g^{\max}u_{g,t}.
$$

Thus,

$$
P_{g,t}\in\{0\}\cup[P_g^{\min},P_g^{\max}].
$$

The MVP applies commitment only to generators with
$P_g^{\min}>0$ and $P_g^{\max}>0$. A generator with zero minimum output
already admits zero dispatch without a disconnected feasible set and does not
need a commitment variable. A statically out-of-service generator remains
unavailable; unit commitment must not override `DispatchableGenerator.status`.

The initial relaxation replaces the Boolean condition with

$$
0\leq u_{g,t}\leq1.
$$

Together with the scaled power bounds, this is the convex hull of the bounded
single-interval on/off output disjunction. In a multistep problem, commitment
and storage remain jointly coordinated through the common network balances,
objective, and storage dynamics even though the MVP has no direct temporal
logic on $u$.

### 2.2 Commitment-aware generator costs

The relaxed cost must preserve the distinction between an offline generator
and a fractionally committed generator. For an online convex quadratic
production cost

$$
f_g(P)=c_{2,g}P^2+c_{1,g}P+c_{0,g},
\qquad c_{2,g}\geq0,
$$

use its closed perspective on the relaxed domain:

$$
\widetilde f_g(P,u)
=c_{2,g}\frac{P^2}{u}+c_{1,g}P+c_{0,g}u.
$$

The quadratic-over-linear term must use a DCP-recognized CVXPY atom or its
equivalent conic representation. The implementation must characterize the
$u=0$, $P=0$ closure and solver behavior before this expression becomes the
production path.

For a convex piecewise-linear cost represented by supporting lines
$a_{g,k}P+b_{g,k}$, introduce an epigraph cost $z_{g,t}$ with

$$
z_{g,t}\geq a_{g,k}P_{g,t}+b_{g,k}u_{g,t}
\qquad\text{for every piece }k.
$$

These perspective formulations reduce to the existing online production cost
when $u=1$ and to zero production cost when $u=0$. They also retain a useful
convex-relaxation lower bound for controlled reference cases. Reusing the
existing cost data must not silently leave the constant cost $c_0$ active when
the generator is off.

Existing `startup` and `shutdown` fields remain inert in the MVP because the
initial model has no startup or shutdown variables. They must not be charged
as ordinary per-interval production costs.

### 2.3 Reactive capability in fixed AC realization

Relaxed commitment variables are available only in convex problem instances.
The AC layer receives fixed Boolean commitments, not relaxed values. For a
fixed commitment $\widehat u_{g,t}$, the generator operating bounds become

$$
P_g^{\min}\widehat u_{g,t}
\leq P_{g,t}\leq
P_g^{\max}\widehat u_{g,t},
$$

$$
Q_g^{\min}\widehat u_{g,t}
\leq Q_{g,t}\leq
Q_g^{\max}\widehat u_{g,t}.
$$

An offline generator therefore supplies neither active nor reactive power.
Any interaction with generator voltage-setpoint enforcement must be explicit:
an offline generator cannot control its bus voltage merely because its static
MATPOWER status is in service.

## 3. Problem configuration boundary

Ordinary OPF construction and solving must remain exactly compatible when
unit commitment is not selected. Unit commitment is an explicit problem-level
policy, not a reinterpretation of every positive `p_min_mw` value.

Freeze a typed configuration before implementation. It must include at least:

- whether commitment is enabled;
- the selected rounding-score policy;
- the ambiguity half-width $\epsilon$, with $0<\epsilon<0.5$;
- a deterministic final tie rule at rounding score $0.5$;
- the supported planning formulation;
- solver configuration for each convex solve; and
- explicit behavior when a relaxed or polished solve is not accepted.

The relaxation is initially supported only by `lossy_dc` and
`singlenode_dc`. Selecting relaxed unit commitment with `ac` must fail before
problem construction with a clear capability error. A separate hierarchical
entry point may consume a completed fixed commitment schedule in AC.

The configuration and result vocabulary must distinguish:

- **static availability**: `DispatchableGenerator.status`;
- **relaxed commitment**: $u_{g,t}\in[0,1]$;
- **fixed commitment**: $\widehat u_{g,t}\in\{0,1\}$; and
- **continuous dispatch**: $P_{g,t}$ and, in AC, $Q_{g,t}$.

## 4. Relax–round–polish algorithm

### 4.1 Relax

Solve the complete convex planning problem with every eligible commitment
variable free in $[0,1]$. Retain the accepted status, objective, commitment
matrix, dispatch, storage trajectory, solver evidence, and independent
residual audit.

For controlled cases using the perspective cost and otherwise matching the
exact discrete model, the relaxed objective is a lower bound on the exact
Boolean optimum. State all assumptions whenever reporting that bound.

### 4.2 Rounding score and partial round

M23 must implement three named rounding-score policies. For every eligible
generator and interval, compute:

1. **Raw commitment (`raw_u`, naive control):**

   $$
   r^{\mathrm{raw}}_{g,t}=u_{g,t}.
   $$

2. **Minimum-commitment rescaling (`rescaled_u`, Re-RUC):**

   $$
   u_g^{\min}=\frac{P_g^{\min}}{P_g^{\max}},
   \qquad
   r^{\mathrm{RUC}}_{g,t}
   =\frac{u_{g,t}}{u_g^{\min}}
   =\frac{P_g^{\max}}{P_g^{\min}}u_{g,t}.
   $$

3. **Power-space rescaling (`rescaled_power`, Re-Power):**

   $$
   r^{\mathrm{power}}_{g,t}
   =\frac{P_{g,t}}{P_g^{\min}}.
   $$

The Re-RUC and Re-Power names and formulas follow Gómez et al. (2026). The
first normalizes relaxed commitment by the generator-specific minimum
reference $P^{\min}/P^{\max}$. The second performs the rounding inference in
power space rather than $u$ space. Scores may exceed one and must be retained
without clipping in the scientific record; any score at least one is plainly
on under a $0.5$ threshold. A clipped copy may be used only for display.

All three scores are diagnostics on every solve, regardless of which policy
controls rounding. They are not probabilities. The selected policy alone
determines which commitments are fixed.

`raw_u` is the required naive control, not a presumed recommended default.
Do not select a default policy before the Stage 6 comparisons. If no policy
has a defensible operating region across the predeclared cases, keep rounding
policy explicit rather than manufacturing a universal recommendation.

Classify the selected score as unambiguous when

$$
|r_{g,t}-0.5|\geq\epsilon.
$$

Fix each unambiguous commitment to one when $r_{g,t}>0.5$ and zero when
$r_{g,t}<0.5$. Apply the declared tie rule on the inclusive boundary when
needed. Leave scores inside the ambiguity band free. Record $u$, $P$, all
three scores, the selected policy, classification, fixed value if any, and
configured $\epsilon$ by stable generator identity and interval.

### 4.3 Resolve

Resolve the same convex problem with the unambiguous commitments fixed and
the ambiguous commitments still relaxed. No other model data, objective
term, or constraint may change between the first and second relaxed solves.

### 4.4 Final round

Round every commitment that remains relaxed after the second solve using the
selected score recomputed from that solve, the $0.5$ threshold, and the
deterministic tie rule. The MVP performs no candidate search at this stage.

The threshold is a heuristic, not a consequence of the convex-hull model. A
relaxed $u$ is a duty fraction in the convex-combination interpretation, not a
probability that the corresponding Boolean decision should be on. For a
quadratic online cost

$$
f(P)=c_2P^2+c_1P+c_0,
$$

the preferred online operating point for a fixed average output is

$$
\overline P^\star=
\operatorname{clip}\left(
\sqrt{c_0/c_2},P^{\min},P^{\max}
\right).
$$

At average output $P^{\min}$, the corresponding relaxed commitment is

$$
u^\star=\frac{P^{\min}}{\overline P^\star}.
$$

Consequently, raw-$u$ $0.5$-threshold rounding selects off whenever
$\overline P^\star>2P^{\min}$, even though committing the generator may be the
best exact Boolean decision. Partial-round distance from $0.5$ does not cure
this failure: it can cause the wrong decision to be fixed with greater
confidence. Re-RUC and Re-Power are model-aware responses to this failure,
not guaranteed solutions to the coupled commitment problem. This known
limitation and the behavior of all three policies must remain visible in
results and tests.

### 4.5 DC polish

Fix every commitment variable to the resulting Boolean schedule and solve the
complete convex planning problem one final time. This solve recomputes
generation dispatch, storage actions, and SoC signposts under the implementable
schedule.

The polish must fix commitment to the rounded Boolean values, not to the
original fractional relaxation. If the fixed schedule is infeasible or the
solve fails acceptance, return an explicit unsuccessful result with all prior
attempts retained. The MVP must not silently unfix decisions, alter demand,
or conceal infeasibility through an undeclared recovery solve.

## 5. Typed identity, results, and audits

Commitment schedules cross formulation and solve boundaries and therefore
require stable generator identity. Add an optional generator `device_id`
following the existing storage identity pattern. Ordinary OPF builds may
retain collision-safe positional labels for compatibility, but a hierarchical
commitment handoff requires explicit, unique, nonempty generator IDs.

Retain typed records for:

- the normalized configuration;
- every eligible generator and the reason for eligibility;
- the first relaxed solve and its commitment values;
- partial-round classifications and fixed decisions;
- the second relaxed solve and remaining values;
- final-round decisions;
- the fixed-policy DC polish;
- the relaxed lower bound when its assumptions hold;
- the polished objective and reported heuristic gap;
- independent feasibility residuals for every accepted solve;
- polished dispatch and SoC signposts; and
- explicit failure stage and classification.

The heuristic gap must not be called a mixed-integer optimality gap unless the
relaxation is a valid lower bound for the exact matched problem and the polish
is a feasible upper bound. Report absolute and relative definitions explicitly.

Unit-commitment variables, commitment-aware costs, and rounding records must
be available by stable name without requiring callers to inspect private
CVXPY expression trees.

## 6. Component and representation architecture

Commitment is an extension of the dispatchable-generator feasible set and
cost, not a separate grid device. Generator-owned code must define:

- relaxed and fixed commitment variables or parameters;
- commitment-scaled active and reactive operating bounds;
- perspective polynomial and piecewise-linear production costs;
- future temporal commitment contributions; and
- generator-level commitment expressions and metadata.

The shared M16+ assembler must compose those contributions. Do not reproduce
commitment constraints or costs independently inside the lossy-DC,
single-node, and AC network builders.

The stepwise and time-vectorized convex builders must represent the same
mathematical problem. Because the intended application includes long-horizon
planning, vectorized support is part of the milestone rather than an optional
performance follow-up. Verify commitment, objective, dispatch, storage, and
rounding equivalence across representations on controlled horizons before
using the vectorized path for scale evidence.

All relaxed device expressions and constraints must pass direct per-object
DCP checks as well as the assembled problem check. The fixed-commitment AC
device contribution remains DCP-valid; DNLP remains confined to nonlinear AC
network physics.

## 7. AC handoff and compatibility with M17

M17's validated default continues to pass SoC signposts only. M23 must not
silently add commitment to the existing M17 call or reinterpret its frozen
experimental record.

The new explicitly configured workflow passes two typed planning outputs:

1. the fixed generator commitment schedule over each AC look-ahead window;
2. identity-aligned SoC signposts from the fixed-policy DC polish.

Commitment is a hard obligation in the MVP. SoC retains the existing declared
target semantics of the selected hierarchical policy. The AC layer
reoptimizes continuous generator and storage actions; it does not inherit the
DC active-power schedule as a hard setpoint.

The handoff record must align generator IDs, storage IDs, global intervals,
local AC-window intervals, and storage-state boundaries. A mismatch is a
pre-solve error.

Small bounded combinatorial repair of commitment inside an AC window is
future work. If later implemented, every alternative fixed schedule and AC
attempt must be retained, and the search must have an explicit finite budget.
It must not be described as part of the M23 MVP.

## 8. Scope boundaries

The MVP does not include:

- startup and shutdown decision variables or costs;
- minimum-up or minimum-down times;
- commitment-dependent ramp limits;
- spinning, operating, or contingency reserves;
- integer counts for clustered or aggregated generating fleets;
- stochastic or scenario-coupled commitment;
- mixed-integer global optimization as the production path;
- convex–concave penalties that encourage relaxed values toward bounds;
- candidate polishing or combinatorial search around ambiguous values; or
- automatic AC-layer commitment repair.

These omissions matter when interpreting results. In particular, the MVP is a
joint on/off dispatch model, not a complete production-cost model. For an
aggregated network such as a bus-level WECC representation, one Boolean value
per aggregate generator may also be too coarse: future work may require an
integer committed-unit count or a documented continuous committed-capacity
interpretation.

## 9. Verification program

### 9.1 Mathematical and atomic tests

- Verify the relaxed on/off feasible set and its boundary cases analytically.
- Verify offline, online-at-minimum, online-interior, and online-at-maximum
  generator behavior.
- Verify DCP status directly for commitment bounds, quadratic perspectives,
  PWL epigraphs, and complete convex problems.
- Verify quadratic and PWL perspective costs against independent numerical
  evaluation, including the $u=0$ closure.
- Verify that disabled unit commitment reproduces the exact pre-M23 problem
  graph, objective, variables, results, and solver behavior expected by the
  existing compatibility tests.

### 9.2 Exact small references

For small generator/time grids, enumerate every Boolean commitment schedule.
For each schedule, solve the fixed continuous problem and retain the best
accepted result as an exact reference over the enumerated set. Compare:

- the relaxed lower bound;
- the two relaxed commitment matrices;
- all three rounding-score matrices and their classifications;
- the rounded schedule produced by each required policy;
- the polished feasible objective;
- the heuristic gap to the enumerated optimum; and
- cases where threshold rounding produces either an infeasible schedule or a
  feasible but materially suboptimal schedule.

Enumeration is a scientific oracle, not a production solution method.

The required feasible-but-suboptimal adversarial reference is a one-interval,
60 MW single-node system with:

| Parameter | Generator 1 | Generator 2 |
|---|---:|---:|
| $P^{\min}$ | 60 MW | 0 MW |
| $P^{\max}$ | 200 MW | 100 MW |
| $c_0$ | 1,000 cost units/h | 0 |
| $c_1$ | 20 cost units/MWh | 100 cost units/MWh |
| $c_2$ | 0.05 cost units/(MW$^2$ h) | 0 |

Generator 1 has

$$
\overline P_1^\star=\sqrt{1000/0.05}\approx141.4\ \text{MW},
\qquad
u_1^\star=60/141.4\approx0.424.
$$

The relaxed solution therefore supplies the 60 MW demand with Generator 1 at
an objective of approximately 2,049 cost units/h. With $\epsilon=0.05$, the
raw-$u$ partial-round rule classifies $u_1^\star$ as unambiguous and fixes it
off. The polish then uses Generator 2 at 6,000 cost units/h. The model-aware
scores are instead

$$
r_1^{\mathrm{RUC}}
=\frac{200}{60}(0.424)\approx1.41,
\qquad
r_1^{\mathrm{power}}=\frac{60}{60}=1,
$$

so both rescaled policies fix Generator 1 on and polish at 2,380 cost units/h,
which matches the exact enumerated Boolean optimum. The test must verify all
three objective values, every score and fixed decision, and the retained
heuristic gaps. The raw policy must remain as the failing control rather than
being changed merely to make this fixture pass.

### 9.3 Integrated convex cases

Test both `singlenode_dc` and `lossy_dc` with:

- positive generator minimum outputs that make shutdown economically useful;
- network congestion that changes which generators should be committed;
- convex quadratic and PWL generator costs;
- nondispatchable generation and explicit loads;
- storage whose polished SoC signposts change with commitment; and
- stepwise versus vectorized construction.

At least one case must leave values inside the configured ambiguity band after
the first solve so that the second relaxed solve is exercised rather than
present only as dead orchestration.

### 9.4 Hierarchical realization

On a bounded multistep case, verify that:

- the fixed DC commitment schedule is applied to every corresponding AC
  interval;
- offline generators have zero active and reactive dispatch;
- the polished SoC signposts retain exact identity and boundary alignment;
- the AC layer remains free to redispatch committed generators and storage;
- only accepted, independently audited first actions are executed; and
- the original SoC-only M17 workflow remains unchanged.

This establishes execution of the proposed interface. It does not establish
that relax–round–polish is generally superior to mixed-integer unit
commitment.

## 10. Implementation stages

| Stage | Deliverable |
|---|---|
| S0 | Freeze the exact MVP mathematics, cost perspective, eligibility rule, three rounding-score policies, score-space epsilon semantics, tie rule, typed configuration, result records, and independent enumeration protocol. |
| S1 | Add stable generator identity and commitment-aware generator-owned data, variables, bounds, expressions, and per-object DCP tests. |
| S2 | Implement and verify quadratic-perspective and PWL-perspective generator costs, including zero-commitment closure and exact small evaluations. |
| S3 | Integrate the relaxed model into stepwise `singlenode_dc` and `lossy_dc` builds with exact disabled-policy compatibility. |
| S4 | Implement the mathematically equivalent time-vectorized convex path and pass controlled representation-equivalence gates. |
| S5 | Implement the retained relax–partial-round–resolve–final-round–polish controller for raw-$u$, Re-RUC, and Re-Power scoring, with explicit failure semantics. |
| S6 | Validate all three rounding policies against enumerated exact references and integrated storage/network cases; characterize heuristic gaps, feasible-but-suboptimal decisions, and infeasible rounding failures. |
| S7 | Add the explicitly configured fixed-commitment plus SoC-signpost AC handoff without changing the M17 compatibility path. |
| S8 | Complete bounded hierarchical validation, documentation, examples, flowchart updates, and the milestone handoff. |

## 11. Future research after the MVP

Candidate follow-on work includes:

- convex–concave procedures or other bounded continuous heuristics that push
  relaxed commitments toward zero or one;
- polishing both alternatives for a small set of ambiguous values;
- explicitly budgeted local combinatorial search in DC or AC;
- startup/shutdown variables, minimum-up/down times, ramp history, and
  commitment-dependent reserves;
- integer unit counts for aggregated fleets;
- comparison with a matched mixed-integer unit-commitment implementation; and
- experiments testing whether the gain from an exact global discrete optimum
  is material relative to input and model uncertainty.

Each method needs a separate named policy, retained attempts, matched inputs,
and predeclared acceptance criteria. Do not fold a successful experimental
heuristic silently into the default MVP.

## 12. Completion criteria

Milestone 23 is complete only when:

1. unit commitment is an explicit, typed, opt-in configuration;
2. relaxed commitment is supported in both convex formulations and rejected
   clearly as a direct AC formulation;
3. generator output bounds and supported convex costs use the documented
   commitment-aware convex relaxation;
4. raw-$u$, Re-RUC, and Re-Power scores are all retained, and the complete
   deterministic relax–round–polish procedure can use each named policy while
   retaining every solve and decision;
5. the final result contains a fixed Boolean schedule and an independently
   accepted polished convex dispatch;
6. storage SoC signposts are recomputed by the fixed-policy DC polish;
7. stepwise and vectorized implementations pass mathematical-equivalence
   gates;
8. exact small enumeration quantifies success and failure of all three
   rounding policies, including the required raw-$u$ adversarial failure;
9. an explicitly configured AC realization consumes the fixed commitment and
   polished SoC handoffs without freezing DC dispatch;
10. the existing M17 workflow and all non-commitment builds preserve their
    compatibility contracts; and
11. documentation states the MVP omissions and makes no global mixed-integer
    optimality claim.
