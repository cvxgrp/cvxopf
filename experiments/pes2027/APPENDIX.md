# Appendix to Network-Model Fidelity in Intertemporal Storage Dispatch

**Bennet Meyers.** Companion to the PES 2027 manuscript
*Network-Model Fidelity in Intertemporal Storage Dispatch: A Matched Study
with CVXOPF*. This document supplies implementation equations,
numerical criteria, source qualifications and additional results. The
[reproduction guide](README.md) identifies code, inputs, execution records and
commands. The notation and complete matched models below accompany the
manuscript's concise formulation and software description.

## A1 Data and configuration

The study combines the PGLib-OPF IEEE 118-bus network with modeled demand and
nondispatchable-availability profiles from an ongoing western U.S. power-system
modeling study. We select the profiles associated with its Tracy, California
node: 8,760 hourly intervals for 2021 in fixed UTC-08:00. The profile table
contains demand, utility solar, utility wind and distributed residential solar.

### Profile construction

The upstream regional study combines asset inventories and generation models
with load modeling. Its load-modeling methodology is being prepared for a
companion PES 2027 paper. The input channels used here have the following origins:

| Channel | Upstream construction |
| --- | --- |
| Demand | Regional load-modeling work underlying the Tracy node profiles |
| Utility solar | Systems described by the [U.S. Large-Scale Solar Photovoltaic Database (USPVDB)](https://energy.usgs.gov/uspvdb/data/), modeled with [pvlib-python](https://pvlib-python.readthedocs.io/en/stable/) |
| Utility wind | Systems described by the [U.S. Wind Turbine Database (USWTDB)](https://energy.usgs.gov/uswtdb/help/), modeled with [PySAM Windpower](https://nrel-pysam.readthedocs.io/en/main/modules/Windpower.html) |
| Distributed residential solar | County-level pvlib-python modeling informed by EIA small-scale solar estimates; see the [EIA-861M input and allocation documentation](https://github.com/slacgismo/regrow/blob/main/data/EIA861M_SmallScaleSolar.md) |

The EIA-861M reference documents monthly distributed-solar capacity and
generation estimates by state, their filtering to the western regional study
area, and their allocation to regional buses. It describes the upstream
small-scale solar inputs and allocation; county-level PV modeling supplies
the simulated generation profiles used in the regional study. The profile
table supplied to this OPF comparison is the interface to that upstream work.
The [reproduction guide](README.md) identifies its retained file and hash.

### Allocation to Case118

We scale and distribute the selected regional profiles onto Case118 using a
separate, reproducible study mapping. Case118 demand shares and signed Q/P
ratios distribute load, and fixed site weights distribute nondispatchable profiles.
Colocated channels keep distinct device identities. The realized mapping,
not only its random seed, is retained in the
[Stage A tables and manifest](../case118_tracy_2021/stage_a/manifest.json).
The [input report](../case118_tracy_2021/STAGE_A_REPORT.md) describes preparation
and independent reconstruction from the supplied profile rows.

The common profile multiplier is 3.0548333420396085. It sets peak net load to
6,000 MW, while peak gross demand is 10,463.20 MW; annual nondispatchable availability
is approximately 1.913 times requested energy. Source active generation capacity
is 6,515 MW before resizing to 5,000 MW. Positive-capacity generator curvature
is $c_{2,i}=c_{1,i}/(3P_i^{\max})$, retaining source linear/constant costs
and reactive bounds. The network flow (NF) model uses `loss_weight=1.0`.

Ten utility solar and ten utility wind channels occupy 15 sites; distributed
solar occupies the 99 load buses. Nondispatchable inverter ratings are 110% of annual
peak availability. Twenty-two load-side and five utility-generation-side batteries
share fleet energy equally between the two pools. Within-pool capacity weights
follow annual demand or nondispatchable energy shares. Fleet capacity is 14,046.56
MWh, with 4,682.19 MW/MVA aggregate discharge/inverter rating and three-hour
duration. Initial and annual terminal energies are 50% of each battery's capacity;
the depletion scenario changes these to 60% and 25%. The named
`apparent_power_rating_mva` column, consumed by `model_inputs.py`, is the battery
capability authority. Ideal storage permits reactive support even at zero active
power and empty/full energy. Cycling costs 0.01 cost units/MWh of absolute
throughput. All loads permit fractional shedding at 20,763.594 cost units/MWh;
curtailment and reactive dispatch have no separate price.

| Scenario | Source-hour interval | Initial/final individual energy |
| --- | --- | --- |
| Large surplus | [3308,3332) | 50% / 50% |
| Large deficit | [1165,1189) | 50% / 50% |
| Deficit depletion | [1165,1189) | 60% / 25% |
| Surplus to deficit | [8580,8604) | 50% / 50% |
| Deficit to surplus | [2439,2463) | 50% / 50% |
| Forced shedding | [1165,1189) | 50% / 50% |

All scenarios use 24 one-hour intervals. Source-hour intervals use zero-based
indices; local interval $t=1$ begins at the listed start. Forced shedding multiplies both
requested P and Q demand by 7.5416905141946184. Its optimistic input-only
shortage witness is 439,252.83068650117 MWh; this establishes necessary shedding,
not network feasibility. The [selection report](../case118_tracy_2021/e3_selection/REPORT.md),
[boundary decisions](../case118_tracy_2021/E3_BOUNDARY_DECISIONS.md) and
[forced-shedding protocol](../case118_tracy_2021/E3_PLUS_PROTOCOL.md) freeze the
six cases. These selected conditions illustrate behavior rather than estimate
its frequency over the year.

## A2 Notation and mathematical models

We follow the intertemporal modeling conventions of Taylor's
[*Convex Optimization of Power Systems*](https://doi.org/10.1017/CBO9781139924672)
and the sparse bus-injection SOCP relaxation described in Low's
[*Convex Relaxation of Optimal Power Flow, Part I: Formulations and Equivalence*](https://doi.org/10.1109/TCNS.2014.2309732),
Sec. IV-C. The relaxation is standard; the shared CVXPY device implementation,
numerical preparation and matched transfer/rollout comparisons are the focus
of this study. Transformer, shunt and rating conventions follow the retained
MATPOWER/PGLib network and the implementation equations below.

### Notation and common devices

We follow Boyd and Vandenberghe's [Sec. 1.6](https://stanford.edu/~boyd/cvxbook/bv_cvxbook.pdf#page=28)
and [notation list](https://stanford.edu/~boyd/cvxbook/bv_cvxbook.pdf#page=711).
Scalars and vectors use ordinary type; their dimensions and context distinguish
them. Device paragraphs describe a generic scalar device, omitting its device
index. In fleet expressions, $g_{i,t}$, $r_{i,t}$, $b_{i,t}$, $e_{i,t}$ and
$u_{i,t}$ refer to device $i$ of the stated family at time $t$. A subscript $t$
on a declared vector denotes the vector at that time; $x_{i,t}$ is its $i^{\text{th}}$
component. The symbol $\mathbf{1}$ denotes the all-ones vector when used in vector expressions, and $\preceq$ denotes componentwise vector inequality.

Intervals $t=1,\ldots,T$ have duration $\delta>0$ hours; energy boundaries
are $t=0,\ldots,T$. Let $n_g,n_r,n_b,n_d$ count generators, nondispatchable channels,
batteries and loads. Generator and network power is per-unit on base $S_0$ MVA;
nondispatchable, battery and load powers are MW/MVAr, and energy is MWh. Positive
battery power denotes discharge. Unless stated otherwise, operating constraints
hold at every interval.

**Generic generator.** Impose $\underline g\leq g_t\leq\overline g$ and, in
full AC/SOCP, $\underline q^g\leq q_t^g\leq\overline q^g$. At physical MW output $P$,
the cost rate is $c(P)=c_0+c_1P+c_2P^2$, with $c_2\geq0$ and argument
$P=S_0g_t$. The study has no commitment or ramping constraints.

**Generic nondispatchable unit.** For availability $\overline r_t$ (e.g., from a solar or wind power production profile) and MVA rating
$s_r$, impose $0\leq r_t\leq\overline r_t$. Full AC and SOCP add
$r_t^2+(q_t^r)^2\leq s_r^2$, while NF and copper plate use $r_t\leq s_r$.
Curtailment is $\overline r_t-r_t$ and has no separate penalty.

**Ideal battery.** For capacity $\overline e$, MVA rating $s_b$ and
prescribed initial/final energies,

$$
e_t=e_{t-1}-\delta b_t\quad(t=1,\ldots,T),\qquad
0\leq e_t\leq\overline e\quad(t=0,\ldots,T),
$$

$$
e_0=e^{\mathrm{init}},\qquad e_T=e^{\mathrm{final}}.
$$

Full AC and SOCP impose $b_t^2+(q_t^b)^2\leq s_b^2$; NF and copper plate replace this by
$-s_b\leq b_t\leq s_b$. The cycling cost rate is $\alpha|b_t|$, with
$\alpha\geq0$ in cost units/MWh. Unit efficiency and zero self-discharge give
$\delta\sum_{t=1}^T b_t=e^{\mathrm{init}}-e^{\mathrm{final}}$.
Thus equal endpoints allow shifting but no net energy supply. The ideal
inverter permits reactive support at zero real output, including empty/full SoC.

**Shedable load.** Requested active/reactive power is $d_t,h_t\in\mathbb R$,
with $d_t\geq0$. Its interruption fraction satisfies $0\leq z_t\leq1$
when $d_t>0$, and $z_t=0$ when $d_t=0$.
Unserved active power, served active power and served reactive power are

$$
u_t=d_tz_t,\qquad \widetilde d_t=d_t-u_t,\qquad
\widetilde h_t=h_t(1-z_t).
$$

The same fraction relieves active and reactive demand. Shedding has cost rate
$\nu u_t$, with $\nu>0$ in cost units/MWh. Fleet energy not served is
$\mathrm{ENS}=\delta\sum_{t=1}^T\sum_{i=1}^{n_d}u_{i,t}$.

### Objective and nodal injections

Each generic model applies to every device of its family, with that device's
parameters. The common device cost sums over those devices explicitly:

$$
C_{\mathrm{dev}}=\delta\sum_{t=1}^T
\left[
\sum_{i=1}^{n_g}c_i(S_0g_{i,t})
+\sum_{i=1}^{n_b}\alpha_i|b_{i,t}|
+\sum_{i=1}^{n_d}\nu_i u_{i,t}
\right].
$$

Here $c_i(P)=c_{0,i}+c_{1,i}P+c_{2,i}P^2$ is generator $i$'s cost rate;
$\alpha_i$ and $\nu_i$ are the battery and load prices. The index $i$ is local
to each device family. Reactive output and curtailment are unpriced, and hard
energy endpoints introduce no terminal cost.

For network equations, collect the scalar device quantities into column vectors:
$g_t=(g_{1,t},\ldots,g_{n_g,t})\in\mathbb R^{n_g}$,
$r_t=(r_{1,t},\ldots,r_{n_r,t})\in\mathbb R^{n_r}$,
$b_t=(b_{1,t},\ldots,b_{n_b,t})\in\mathbb R^{n_b}$ and
$e_t=(e_{1,t},\ldots,e_{n_b,t})\in\mathbb R^{n_b}$.
The vectors $d_t,h_t,z_t,u_t,\widetilde d_t,\widetilde h_t\in\mathbb R^{n_d}$
are assembled in the same way. Reactive vectors $q_t^g,q_t^r,q_t^b$ have the
corresponding family dimensions and units. This vector use is distinct from
the generic scalar-device paragraphs above.

Let $n$ be the bus count, with incidence matrices
$C_g\in\mathbb R^{n\times n_g}$, $C_r\in\mathbb R^{n\times n_r}$,
$C_b\in\mathbb R^{n\times n_b}$ and $C_d\in\mathbb R^{n\times n_d}$.
Per-unit device injections $\pi_t,\kappa_t\in\mathbb R^n$ are

$$
\pi_t=C_gg_t+\frac{C_rr_t+C_bb_t-C_d\widetilde d_t}{S_0},\qquad
\kappa_t=C_gq_t^g+\frac{C_rq_t^r+C_bq_t^b-C_d\widetilde h_t}{S_0}.
$$

Shunts enter full AC/SOCP admittances, not these device injections. Each model
minimizes $C_{\mathrm{dev}}+C_{\mathrm{net}}^{(m)}$ subject to its device and
network constraints. Only storage couples intervals in this study.

### Network equations and their real implementation

Time is the last variable axis in vectorized assembly. Public extracted `soc`
contains post-interval states;
prepend the supplied initial state to obtain all $T+1$ boundaries.

### Full AC

For complex bus voltages $V_{i,t}=v_{i,t}\exp(\mathrm j\theta_{i,t})$,
with magnitude per-unit and angle radians, collect $V_t\in\mathbb C^n$
and $v_t,\theta_t\in\mathbb R^n$. For per-unit bus admittance
$Y\in\mathbb C^{n\times n}$,

$$
\pi_t+\mathrm j\kappa_t=V_t\odot\overline{YV_t}.
$$

Impose $\underline v\preceq v_t\preceq\overline v$ and reference angle
$\theta_{i_{\mathrm{ref}},t}=0$. Source branch angle-difference bounds
(`ANGMIN`/`ANGMAX`) are not enforced in this study. With rating $F_a$ MVA and
$f_a^{\max}=F_a/S_0$, both terminal powers satisfy

$$
(p_{a,t}^\sigma/f_a^{\max})^2+(q_{a,t}^\sigma/f_a^{\max})^2\leq1,
\qquad \sigma\in\{\mathrm f,\mathrm t\}.
$$

For $Y=G+\mathrm jB$, each nonzero admittance entry $(i,j)$ has lifted
variables $P_{ij,t},Q_{ij,t}$. With $\phi_{ij,t}=\theta_{i,t}-\theta_{j,t}$,

$$
P_{ij,t}=v_{i,t}v_{j,t}(G_{ij}\cos\phi_{ij,t}+B_{ij}\sin\phi_{ij,t}),
$$

$$
Q_{ij,t}=v_{i,t}v_{j,t}(G_{ij}\sin\phi_{ij,t}-B_{ij}\cos\phi_{ij,t}).
$$

Diagonal admittance entries are included. Collect the entries into vectors
$P_t,Q_t$ in the retained sparsity order; these are not branch-terminal powers.
A sparse row-sum map gives $p_t=R_pP_t$ and
$q_t=R_pQ_t$, with separate device-injection balances $p_t=\pi_t$, $q_t=\kappa_t$.
Only the network physics uses DNLP; device operating sets, cost functions and
intertemporal equations remain DCP-valid.

For branch $a$ from $i$ to $j$, let $\phi_t=\theta_{i,t}-\theta_{j,t}$.

$$
p_{a,t}^{\mathrm f}=g_a^{\mathrm{ff}}v_{i,t}^2+
v_{i,t}v_{j,t}(g_a^{\mathrm{ft}}\cos\phi_t+b_a^{\mathrm{ft}}\sin\phi_t),
$$

$$
q_{a,t}^{\mathrm f}=-b_a^{\mathrm{ff}}v_{i,t}^2+
v_{i,t}v_{j,t}(g_a^{\mathrm{ft}}\sin\phi_t-b_a^{\mathrm{ft}}\cos\phi_t).
$$

To-terminal equations interchange $i,j$ and use $y_a^{\mathrm{tt}},y_a^{\mathrm{tf}}$.
Separate lifted terminal powers enforce both-terminal apparent-power disks.
Charging, taps, phase shifts and fixed shunts enter the admittances. Network
absorption is supplied through power balance, not an added AC objective penalty.

### SOCP

SOCP stores $w_{i,t}=|V_{i,t}|^2$ and
$c_{ij,t}+\mathrm js_{ij,t}=V_{i,t}\overline V_{j,t}$ on
unordered branch pairs. Parallel circuits share a voltage product but retain
separate terminal constraints. Sparse real maps of $x_t=(w_t,c_t,s_t)$ give nodal
and terminal powers, expanding $\sum_j\overline{Y_{ij}}W_{ij,t}$ without a dense
voltage-product matrix. Edge cones enforce $c_{ij,t}^2+s_{ij,t}^2\leq w_{i,t}w_{j,t}$;
no global positive-semidefinite or rank-one matrix constraint is imposed.

With explicit time indices, $w_{i,t}=|V_{i,t}|^2$ and
$c_{ij,t}+\mathrm js_{ij,t}=V_{i,t}\overline V_{j,t}$ are intended meanings,
not enforced rank-one identities. For $x_t=(w_t,c_t,s_t)$ stacked vertically,
the implemented constraints include

$$
p_t=M_px_t=\pi_t,\qquad q_t=M_qx_t=\kappa_t,\qquad
(\underline v_i)^2\leq w_{i,t}\leq(\overline v_i)^2,
$$

$$
\left\|(2c_{ij,t},2s_{ij,t},w_{i,t}-w_{j,t})\right\|_2\leq w_{i,t}+w_{j,t}.
$$

The same admittances and both-terminal disks apply. The objective is
$C_{\mathrm{dev}}$; cone slack or cycle-phase inconsistency can prevent
recovery of AC voltages.

### Network flow and copper plate

The network flow formulation follows Boyd and Meyers,
[*Convex Optimization with Smart Grid Examples*](https://doi.org/10.2172/3018252)
(2025).

For $n_\ell$ branches, let $f_t\in\mathbb R^{n_\ell}$ be signed per-unit
flow and $A\in\mathbb R^{n\times n_\ell}$ its incidence matrix, with $-1$
at the from bus and $+1$ at the to bus. Network flow uses

$$
Af_t+\pi_t=0,\qquad -f^{\max}\preceq f_t\preceq f^{\max},\qquad
C_{\mathrm{net}}^{(\mathrm{NF})}=\delta\lambda\sum_{t=1}^T\sum_a\rho_a f_{a,t}^2.
$$

Per-unit resistances are $\rho_a$ and the study uses $\lambda=1.0$ cost units/h.
The per-unit quantities are dimensionless, so $\lambda$ converts the flow proxy
to a cost rate before integration over time.
This convex network flow QP is not phase-angle DC power flow. The proxy
penalizes flow but does not withdraw lost power from nodal balance;
$\mathbf{1}^T\pi_t=0$ still holds. Its cost is reported separately from device cost.

Copper plate removes branches and uses $\mathbf{1}^T\pi_t=0$, with
$C_{\mathrm{net}}^{(\mathrm{CP})}=0$. It retains active-power device constraints;
location no longer restricts delivery.

Full AC and SOCP retain the same reactive capabilities, voltage bounds and
both-terminal thermal limits. The network flow and copper plate models retain
active-power boxes instead of capability disks. Copper plate projects out
branches and removes the nonnegative flow penalty. Consequently SOCP relaxes
matched full AC, and copper plate relaxes matched network flow, without a universal
four-model objective ordering. Global objective values satisfy
$C^\star_{\mathrm{SOCP}}\leq C^\star_{\mathrm{AC}}$ and
$C^\star_{\mathrm{CP}}\leq C^\star_{\mathrm{NF}}$.
Accepted AC solutions are local estimates, not those global certificates.

## A3 Numerical preparation and independent acceptance

Time/space vectorization, exact fixed-coordinate substitution and rescaling
preserve the mathematical problems. SOCP uses device normalization and joint
objective/constraint scaling; NF and copper plate use joint scaling; full AC uses device normalization
and cycling/shedding cost coordinates. All reported values are restored to
physical units. Preparation records bind scales, reduced/full coordinate maps,
native evidence and, for AC, the complete verified IPOPT starting vector.

| Quantity | Setting or maximum residual |
| --- | --- |
| Full AC solver | IPOPT; `tol=1e-7`, adaptive barrier, exact Hessian, zero bound relaxation; native status 0 |
| SOCP solver | CLARABEL stock QDLDL, one thread; absolute gap `1e-10`, relative gap `1e-6` |
| NF and copper plate solver | CLARABEL stock QDLDL, one thread; absolute/relative gap `1e-10` |
| Convex feasibility | `1e-10`; native `Solved` required |
| Iteration limits | IPOPT 3,000; CLARABEL 5,000 |
| AC P/Q balance, voltage | `1e-6` p.u. |
| SOCP, NF and copper plate balance | `1e-4` MW/MVAr |
| Device boxes | `2e-5` MW/MVAr/MWh |
| Capability/branch limits | `1e-4` MVA; normalized residual `1e-7` |
| SOCP voltage squared/cones | `1e-6` p.u. squared |
| SoC recurrence and ENS | `1e-4` MWh |
| Endpoint | `1e-3` MWh; SOCP also `1e-4` MWh |
| Shedding fraction | `1e-8` |

Named component accounting uses $10^{-4}+10^{-10}|C_k|$ cost units.
Unexplained canonical-to-physical total error uses $10^{-4}+\epsilon|C|$, with
$\epsilon=10^{-6}$ for full AC/SOCP and $10^{-10}$ for NF and copper plate. Full AC native canonical
objective reconstruction uses $10^{-7}+10^{-12}|C_{\rm native}|$.
Explained AC cycling-epigraph excess is subtracted from unexplained total error
and separately flagged; physical cycling cost uses actual battery throughput.
Native convergence, physical acceptance, accounting and SOCP voltage recovery
are distinct checks. Exact resolved options and implementation-defined residuals
are in [prepared E3](../case118_tracy_2021/E3_PREPARED_PROTOCOL.md),
[`stage_b.py`](../case118_tracy_2021/stage_b.py),
[`stage_d_physics.py`](../case118_tracy_2021/stage_d_physics.py) and the bound
per-attempt records.

### Matched evidence for numerical preparation

A separate [serial matched study](../numerical_preparation/MATCHED_COMPARISON_REPORT.md)
compared baseline and prepared representations of full AC and SOCP on the
large-deficit and deficit-to-surplus inputs, each at 3- and 24-hour horizons:
eight pairs, or 16 runs. Within each pair, physical inputs, constraints, solver
settings and physical initializations matched. Runs used fresh processes, one
numerical thread and no recovery attempts. Each horizon started and ended at
50% energy capacity per battery; time/space vectorization was held fixed.

The AC preparation bundle combined device normalization, exact fixed-coordinate
substitution, storage/shedding cost coordinates and component-first objective
assembly. SOCP combined device normalization, exact fixed-coordinate substitution
and joint objective/constraint scaling. The intended physical problems and
acceptance checks were unchanged.

| Formulation | Accepted baseline runs | Accepted prepared runs | Baseline/prepared public-solve time |
| --- | ---: | ---: | --- |
| Full AC | 3/4 | 4/4 | 4.93–51.29 times, across three jointly accepted pairs |
| SOCP | 1/4 | 4/4 | 1.64 times, for the one jointly accepted pair |

Public-solve time includes canonicalization, preparation, native solving and
restoration to physical coordinates. Whole-worker AC speedups were 4.85–32.63
times in the three jointly accepted pairs. For the sole jointly accepted SOCP
pair, whole-worker time was essentially unchanged at 2.046 seconds because
setup overhead dominated. The other baseline outcomes were three SOCP
`InsufficientProgress` returns and one 24-hour AC whole-worker timeout at
3,600 seconds; these are not included in accepted-pair speedup ratios.

These single-run comparisons support improved numerical acceptance and AC solve
performance for the tested preparation bundles. They do not isolate the effect
of each transformation, establish a universal speedup or prove global AC
optimality. The [report](../numerical_preparation/MATCHED_COMPARISON_REPORT.md)
links the frozen comparison plan, reproduction commands, complete timings,
physical audits and hash-bound evidence. Execution used commit
`49bdebf09179f0b75602fb98a9d52fbcb7654b68`; the retained figure-data snapshot has
SHA-256 `d4aade84c8c102924c1b3a1af01c24c38702a22304e6c886526db1a58253da7f`.

### Qualified forced-shedding source outcomes

All 24 joint arms converged; 21 passed independent checks. The three E3+
convex arms reached native `Solved` but retain these rejection flags:

| Donor | Canonical minus physical cost | Retained limit |
| --- | ---: | ---: |
| copper plate | 1,147.6255 | 1.1459 |
| network flow | 1.4222 | 1.1786 |
| SOCP | 13,945.1113 | 11,823.7883 |

SOCP also has maximum active/reactive balance errors of 0.00127143 MW and
0.000282342 MVAr against `1e-4`. These are numerically small relative to this
extreme scenario, but they are not strict passes. The owner approved these
donor schedules as qualified evidence with the flags preserved. Each receiving
AC result is checked independently; source qualification does not waive its
physical or accounting gate.

## A4 Transfer and rollout construction

### Fixed battery-power transfer

For each of the six scenarios and three convex donors, we rebuild the matched
24-hour AC problem and fix $b_{i,t}=b_{i,t}^{(m)}$ for every battery $i$ and
interval $t$. We use the retained donor powers without rounding, clipping or
repair. Together with the initial state, these powers determine the complete
energy trajectory through the unchanged ideal-storage recurrence. The terminal
equality is therefore redundant; we remove it through the storage API while
independently checking the original endpoint.

We initialize from the full AC reference, replacing only battery power and
the implied energy trajectory. All other controls remain free to adjust.
We retain the complete starting point in physical and canonical coordinates
and independently restore the solver result to physical units. Each transfer
receives one attempt, with no recovery or retry, under
a 3,600-second wall limit and 16 GiB sampled RSS limit. Fixed-power and
implied-energy checks use tolerances of `1e-4` MW and `1e-4` MWh, respectively;
the original endpoint uses `1e-3` MWh.

The [transfer protocol](../tracy_battery_transfer/PROTOCOL.md) defines the full
construction. Fifteen of eighteen transfers have accepted AC
operating points. The deficit-to-surplus SOCP transfer timed out, and the NF and copper plate
transfers returned IPOPT status 2 with unsatisfied constraints. Those original
solver outcomes remain separate from the subsequent certificates in A6.

### Three-hour energy-signpost rollout

Each scenario has four AC rollouts, one using energy targets from each joint
network-model solution. Unlike fixed-power transfer, these targets leave battery
powers free to adjust within each window. At boundary $k$, the AC problem starts
from the realized energies $e_{i,k}$ and covers intervals $k+1,\ldots,\ell$,
where $\ell=\min(k+3,T)$. Each battery must reach
$e_{i,\ell}=e_{i,\ell}^{(m)}$ at the window endpoint; whenever $\ell=T$, the
original final obligation applies. After the full window passes its audit, we
implement only its first hour and advance the realized state exactly once.
The last two windows consequently shrink to two hours and one hour. Even when
the full AC reference supplies the energy targets, it supplies neither future
dispatch nor network starting points.

All rollouts use the same initialization and recovery procedure. The first
window has a cold start; subsequent windows shift the preceding accepted
controller's solution forward in time. Two primary solver processes work on
different rollouts and share a third process for recovery assistance. After
300 solve-phase seconds, a primary becomes eligible for assistance but
continues solving: the threshold is not a primary timeout. The first result
that satisfies the energy target and passes an independent audit is selected.
A target-free helper result can initialize a subsequent target-conditioned
attempt, but cannot itself advance the trajectory.

Each window first uses an objective assembled by component. If that phase
exhausts its attempts without an accepted result, the same window and target
are retried with an objective assembled hour by hour, without advancing the
realized state between phases. The
[runner guide](../tracy_ac_rollout/README.md) gives the exact attempt order,
budgets and operational continuation.

All 24 trajectories completed 24 hours. The figure reconstruction checks
576 executed actions, full-day physical/accounting residuals, 25-boundary energy
continuity, source identities and finalized controlling evidence. It does not
repeat the complete native-start or recovery-policy audit. Each executed action
contributes to cost and energy once; overlapping prediction objectives and
unexecuted controls do not enter day totals.

## A5 Additional numerical results

The following device costs exclude the network flow penalty. Values are rounded
for reading; the figure snapshot retains unrounded named costs, ENS, curtailment,
source hashes and trajectory checks. A common low planning cost need not imply
low cost when its battery schedule is implemented under AC physics.

### Joint device cost

| Scenario | copper plate | network flow | SOCP | full AC |
| --- | ---: | ---: | ---: | ---: |
| Surplus | 0.00 | 0.00 | 3.12 | 49.20 |
| Deficit | 1,457,135.41 | 1,475,728.59 | 1,540,766.85 | 1,580,849.89 |
| Depletion | 1,457,086.25 | 1,469,495.63 | 1,530,325.30 | 1,561,675.42 |
| Surplus to deficit | 402,513.34 | 467,984.58 | 504,027.63 | 530,314.10 |
| Deficit to surplus | 277.37 | 4,544.63 | 12,689.69 | 52,077.88 |
| Forced shedding | 11,457,931,089.09 | 11,784,826,362.58 | 11,823,788,227.47 | 11,852,950,486.06 |

The forced-shedding convex entries retain the A3 qualifications. Ordinary
joint problems serve essentially all demand. Forced ENS is approximately
551,625, 567,379, 569,255 and 570,661 MWh, respectively.

### AC device cost with fixed donor battery powers

| Scenario | copper plate donor | network flow donor | SOCP donor |
| --- | ---: | ---: | ---: |
| Surplus | 43,495.55 | 43,495.55 | 40,381.96 |
| Deficit | 3,078,821.71 | 1,847,372.72 | 1,586,522.46 |
| Depletion | 1,570,900.08 | 1,565,317.80 | 1,565,051.48 |
| Surplus to deficit | 667,525.12 | 535,227.67 | 532,940.09 |
| Deficit to surplus | No accepted result | No accepted result | Timeout |
| Forced shedding | 11,878,686,200.13 | 11,862,808,013.66 | 11,853,759,714.82 |

All fifteen accepted transfers have higher device cost and nondispatchable
curtailment, and equal or worse load service, than the full AC reference. Neutral
deficit ENS is 71.5543 MWh for copper plate and 12.3355 MWh for network flow;
SOCP serves demand. Depletion supplies 4,916.30 MWh from reserves and reduces
transfer cost penalties to 0.22-0.59%. Copper plate also adds 4.8291 MWh ENS
in surplus to deficit. Forced-transfer ENS is approximately 571,901, 571,136
and 570,700 MWh for copper plate, network flow and SOCP, respectively.

### Executed AC device cost with donor energy signposts

| Scenario | copper plate donor | network flow donor | SOCP donor | full AC donor |
| --- | ---: | ---: | ---: | ---: |
| Surplus | 25,955.70 | 25,962.67 | 23,754.34 | 49.19 |
| Deficit | 1,864,388.75 | 1,662,868.02 | 1,581,831.79 | 1,580,849.90 |
| Depletion | 1,562,191.54 | 1,562,100.32 | 1,562,021.82 | 1,561,675.43 |
| Surplus to deficit | 543,473.55 | 532,676.02 | 531,596.52 | 530,314.10 |
| Deficit to surplus | 85,093.33 | 60,834.24 | 64,457.56 | 52,077.88 |
| Forced shedding | 11,858,213,647.62 | 11,853,480,844.77 | 11,853,054,449.51 | 11,852,950,486.04 |

Every comparable rollout lowers cost and curtailment from fixed transfer.
Neutral-deficit copper plate/network flow ENS becomes 13.5923/3.9069 MWh,
with cost reductions of 39.44%/9.99%. SOCP serves demand at 0.062% above
the full AC reference cost. All depletion rollout costs are within 0.034% of the full AC reference.
Forced-shedding ENS reductions from fixed transfer are 986.244, 449.733 and
34.024 MWh for copper plate, network flow and SOCP. The three previously
obstructed deficit-to-surplus arms also complete.

Energy targets from the full AC reference produce costs within 0.02 units of its cost in all six
cases. This does not require identical intermediate dispatch coordinates.
Fidelity ordering is not universal: deficit-to-surplus network flow targets
outperform SOCP targets. The surplus full AC reference has higher network energy consumption
yet much lower cost than the transfers/convex-target rollouts, so losses alone
do not explain that economic separation.

### Time-series and redispatch summaries

The manuscript's illustrative deficit-to-surplus panel uses common hourly
demand and nondispatchable availability with energy trajectories from the joint
AC reference and copper plate joint optimization. Gray traces are $e_{i,t}/\overline e_i$; the bold fleet trace is
$\sum_i e_{i,t}/\sum_i\overline e_i$, a capacity-weighted fraction, not the
unweighted mean of device percentages. All 25 boundaries are included.
The SoC plotter exports all six scenarios for comparison.

For generator $i$, define $\Delta P_{i,t}^g=S_0(g_{i,t}^{(a)}-g_{i,t}^{(b)})$.
Net adjustment is $N_t=\sum_{i=1}^{n_g}\Delta P_{i,t}^g$ and opposing redispatch is
$R_t=\tfrac12(\sum_{i=1}^{n_g}|\Delta P_{i,t}^g|-|N_t|)$. These summaries distinguish net
change from spatial cancellation without attributing its cause. Per-device
trajectories remain important because aggregate sums can hide opposing changes.

### Movement of battery-energy trajectories toward the full AC reference

For each of the six scenarios and three convex donor models, compare the
donor's joint battery-energy trajectory and the realized AC rollout trajectory
against the full AC reference. Align the same 27 battery identities
and use only the 23 interior hourly boundaries, excluding the prescribed initial
and final states. Define the capacity-normalized mean absolute distance

$$
D(e,e^{\mathrm{AC}})=\frac{1}{n_b(T-1)}
\sum_{t=1}^{T-1}\sum_{i=1}^{n_b}
\frac{|e_{i,t}-e_{i,t}^{\mathrm{AC}}|}{\overline e_i}.
$$

Each battery receives equal weight after normalization by its own capacity.
The table reports $100[1-D(e^{\mathrm{roll}},e^{\mathrm{AC}})/
D(e^{\mathrm{donor}},e^{\mathrm{AC}})]$: positive values mean that rollout
reduces distance to the full AC reference, and negative values mean that it increases it.
This is a descriptive distance from a retained local AC solution, not a
measure of optimality or feasibility.

| Scenario | copper plate | network flow | SOCP |
| --- | ---: | ---: | ---: |
| Large surplus | -15.7% | -15.8% | -18.6% |
| Large deficit | 63.0% | 58.7% | 53.0% |
| Deficit, depletion | 67.7% | 62.3% | 60.8% |
| Surplus to deficit | 40.3% | 33.6% | 41.8% |
| Deficit to surplus | 22.8% | 17.9% | 23.1% |
| Forced shedding | 29.3% | 29.7% | 26.2% |

Individual-trajectory distance decreases in 15 of 18 donor–scenario pairs.
Evaluating each battery separately, 414 of 486 battery–donor–scenario
trajectories have lower mean absolute distance. In contrast, aggregate fleet
energy, $\sum_i e_{i,t}$, moves closer to the full AC reference in all 18 comparisons under
mean absolute MWh distance over the same interior boundaries. The large-surplus
exception therefore illustrates how aggregate agreement can coexist with
greater separation among individual battery trajectories. The forced-shedding
donors retain their A3 qualifications.

The large-surplus scenario is not energy-scarce. With abundant nondispatchable
availability and unpriced curtailment, alternative storage schedules can have
similar economic value. Its movement away from the particular full AC reference
trajectory is consistent with that freedom, rather than by itself indicating
worse performance. This interpretation does not establish exact nonuniqueness
or equal total costs: the surplus cost differences reported above remain.
Trajectory distance and economic performance are distinct quantities.

The calculation uses the donor boundary states retained in the
[rollout binding](../tracy_ac_rollout/results/rollout_001/binding.json),
realized energy independently integrated from each checkpoint's 24 executed
first actions, and full AC reference boundary states in the
[joint-trajectory snapshot](../case118_tracy_2021/results/pes_soc_review_003/soc_data.json).
Checkpoint, window and selected-controller artifacts were checked against the
source hashes in the
[paper figure snapshot](../case118_tracy_2021/results/pes_figures_review_001/figure_data.json).
The comparison performs no new optimization and does not establish convergence
under repeated rollout passes.

### Controls signposted by the full AC reference

The six rollouts signposted by the full AC reference provide a control: when the supplied energy
targets come from the full AC reference itself, how closely does a single rollout pass
reproduce that reference trajectory? We apply the same distance $D$ to these
controls, using the same 27 battery identities and 23 interior boundaries.
Here the donor is the reference, so its initial distance is zero and a
percentage reduction would be undefined. Instead, we report $100D$ in percentage
points of individual capacity, the largest individual battery/boundary deviation
in those units, and the mean absolute fleet-energy difference in MWh.

| Scenario | Individual mean (percentage points) | Individual maximum (percentage points) | Fleet mean (MWh) |
| --- | ---: | ---: | ---: |
| Large surplus | 0.0978 | 3.73 | 3.46 |
| Large deficit | 0.0127 | 1.86 | 0.648 |
| Deficit, depletion | 0.00987 | 0.482 | 0.389 |
| Surplus to deficit | 0.0213 | 1.86 | 0.765 |
| Deficit to surplus | 0.0985 | 1.81 | 14.3 |
| Forced shedding | 0.0113 | 0.308 | 0.805 |

Mean individual differences are below 0.1 percentage points of capacity in all
six scenarios, alongside the cost differences below 0.02 units reported above.
The controls therefore show close agreement in cost and average battery-energy
trajectories after one rollout pass. They do not reproduce every state exactly:
the largest localized difference, 3.73 percentage points, occurs in large
surplus, where the cost changes by only about 0.008 units. As in the donor
comparison above, weakly costed scheduling freedom can leave individual
trajectories different without materially changing the economic outcome.
Exact coordinate reproduction is therefore not required for economic agreement;
the individual and fleet distances describe the remaining scheduling freedom.
This evidence supports approximate one-pass preservation in cost and average
battery energy, not convergence under repeated rollout.

The [control calculation](analyze_rollout_controls.py) reuses the hash-checked
rollout reader and reconstructs energy from executed first actions, with no new
optimization. Its [unrounded result and source hashes](../case118_tracy_2021/results/pes_joint_ac_controls_001/distances.json)
retain all six controls; the [reproduction guide](README.md) gives the command.

## A6 Necessary-condition certificates

The [original loop report](../tracy_socp_transfer_preflight/REPORT.md) and
[multigraph extension](../tracy_socp_transfer_preflight/MULTIGRAPH_REPORT.md)
retain a separate, non-solving interval analysis. At hour 2452, necessary cut
export and thermal/voltage bounds rule out consistent bus angles around
71-72-24-70 for all three deficit-to-surplus fixed donor schedules. The SOCP
point's implied loop angles miss closure by approximately 39.55 degrees even
though its relevant edge cones are essentially tight.

Interval exclusion covers the allowed voltage region and leaves other free
controls within their original operating sets. It certifies incompatibility of
those particular fixed schedules under the matched AC model, not infeasibility
of the full AC reference problem or arbitrary future schedules. Generic SOCP
point nonrecoverability alone is not such a certificate: all six SOCP donor
points have recovery warnings, while five donor schedules admit accepted AC
transfers. An inconclusive certificate screen likewise supplies no feasibility
claim. Selected evidence and derivations are tracked with the checker.

## A7 Historical runtime context

Joint 24-hour AC native solve times span approximately 122-820 seconds.
The six rollouts signposted by the full AC reference use 126-272 seconds of summed native
solve time, lower than the corresponding joint solve in five cases. Their
elapsed times are approximately 780-936 seconds, compared with 145-842 seconds
for joint solves: each rollout launches 24 fresh controlling workers with
repeated admission, auditing and publication.

These are descriptive historical measurements. Rollouts ran in two concurrent
lanes whereas joint solves ran serially, and preparation/source histories differ.
They are not a matched speedup benchmark. Native effort, cumulative worker
effort and elapsed time are distinct quantities; neither favorable native
timing nor these six selected days establishes annual throughput or a preferred
universal horizon.
