# Milestone 18 — Convex lossy storage

**Status:** planned

**Depends on:** Milestone 5 (ideal storage), Milestone 12 (terminal SoC),
Milestone 16 (component ownership)

## 1. Goal

Add a lossy storage device with:

- separate nonnegative charge and discharge powers;
- asymmetric charge and discharge efficiencies;
- a storage-loss term;
- the existing storage-owned operating, network-injection, temporal, cost,
  terminal-policy, and reporting interfaces; and
- a convex primary formulation.

The primary formulation should not require binary variables or
charge/discharge complementarity. A two-step relax-round-polish procedure is
retained as a deliberate fallback if simultaneous charge and discharge appear
in an implemented model despite the throughput regularization and zero-cost
curtailment design.

## 2. Sign and state conventions

Let positive net battery power denote injection into the grid. For each storage
unit and interval,

\[
p_t^{\mathrm{ch}}\geq 0,\qquad
p_t^{\mathrm{dis}}\geq 0,
\]

\[
b_t=p_t^{\mathrm{dis}}-p_t^{\mathrm{ch}}.
\]

The energy transition has the form

\[
q_{t+1}
=
q_t
+
\Delta t\left(
\eta_{\mathrm{ch}}p_t^{\mathrm{ch}}
-
\frac{p_t^{\mathrm{dis}}}{\eta_{\mathrm{dis}}}
-
p_t^{\mathrm{loss}}
\right).
\]

Before implementation, specify whether \(p_t^{\mathrm{loss}}\) is:

1. a constant auxiliary load while the device is active;
2. a convex function of stored energy;
3. proportional self-discharge; or
4. another explicitly documented convex loss model.

Do not overload charge/discharge inefficiency with standby or self-discharge
losses. They are physically distinct effects and must remain separately
inspectable.

## 3. Primary convex formulation

Preserve the literature-justified L1 battery-throughput penalty:

\[
\lambda\sum_t
\left(
p_t^{\mathrm{ch}}+p_t^{\mathrm{dis}}
\right),
\qquad \lambda>0.
\]

Because the split powers are nonnegative, this is their sum-absolute penalty.
It assigns a strictly positive cost to internal charge/discharge circulation.

Renewable curtailment is a **metric of interest**: track and report it, but
assign it zero objective weight. This is part of the formulation, not merely a
reporting preference. A positive curtailment cost could make dissipation
through battery round-trip losses artificially preferable to curtailment and
thereby defeat the intended exactness result.

The primary implementation must remain continuous and convex. Do not add
complementarity constraints or binary mode variables to the ordinary solve.

## 4. Exactness argument

Suppose both split powers are positive at an interval. For a sufficiently
small \(\epsilon>0\), define

\[
\tilde p_t^{\mathrm{ch}}
=p_t^{\mathrm{ch}}-\epsilon,
\qquad
\tilde p_t^{\mathrm{dis}}
=p_t^{\mathrm{dis}}
-\eta_{\mathrm{ch}}\eta_{\mathrm{dis}}\epsilon.
\]

This transformation:

1. preserves the energy-state transition;
2. reduces throughput by
   \((1+\eta_{\mathrm{ch}}\eta_{\mathrm{dis}})\epsilon\); and
3. increases net grid injection by
   \((1-\eta_{\mathrm{ch}}\eta_{\mathrm{dis}})\epsilon\).

When the additional injection can be absorbed by zero-cost renewable
curtailment, the transformed point is feasible and has strictly lower
objective value. Therefore a point with simultaneous charging and discharging
cannot be optimal.

The implementation and documentation must state the exactness result together
with its assumptions. In a network model, the proof must account for storage
and curtailable-generation locations and binding network constraints. Do not
silently promote a single-node feasible-direction argument to every congested
AC network.

## 5. Diagnostic for split operation

Every lossy-storage solve must make simultaneous operation observable. Define a
numerical diagnostic such as

\[
m_{t,s}=\min\left(
p_{t,s}^{\mathrm{ch}},
p_{t,s}^{\mathrm{dis}}
\right).
\]

Report or retain enough information to evaluate \(m_{t,s}\). The plan must set
an engineering-unit tolerance, separate from raw solver feasibility
tolerances, before deciding whether a solution contains material split
operation.

The fallback is triggered only by material simultaneous operation. It is not a
default second solve and must not hide small solver residuals by silently
changing the returned solution.

## 6. Fallback: relax, round, and polish

If the primary solution contains material simultaneous operation, introduce a
mode helper \(z_{t,s}\) through

\[
0\leq z_{t,s}\leq 1,
\]

\[
p_{t,s}^{\mathrm{ch}}
\leq
\overline p_s^{\mathrm{ch}}z_{t,s},
\qquad
p_{t,s}^{\mathrm{dis}}
\leq
\overline p_s^{\mathrm{dis}}(1-z_{t,s}).
\]

These are the McCormick/big-\(M\) mode bounds with the physical charge and
discharge ratings supplying tight bounds. The fallback procedure is:

1. **Relax.** Solve the convex problem with \(z_{t,s}\in[0,1]\).
2. **Round.** Map each \(z_{t,s}\) to a fixed mode in \(\{0,1\}\).
3. **Rebuild.** Replace the helper-variable constraints with fixed flow
   directions: one split power is constrained to zero at each interval.
4. **Polish.** Remove \(z\) entirely and resolve the remaining continuous
   problem.

The polished problem is convex and has exact flow directions. The relaxed
helper variable is an algorithmic device and must not become part of the
public storage model or final result contract.

### Open fallback decisions

- Rounding at \(z=0.5\): deterministic tie rule versus selection from the
  relaxed charge/discharge magnitudes.
- Whether modes with both relaxed split powers below tolerance should be fixed
  to idle by setting both powers to zero, or assigned an arbitrary direction.
- Behavior when the rounded fixed-direction problem is infeasible.
- Whether to allow a bounded number of alternative roundings before reporting
  fallback failure.
- Whether fallback invocation is explicit user policy, automatic with a clear
  result flag, or diagnostic-only in the first implementation.

These choices affect semantics and reproducibility and must be resolved before
implementation.

## 7. Device and formulation ownership

The lossy storage device must own:

- its split variables and net network injection;
- AC and DC operating constraints;
- SoC coupling and loss dynamics;
- cycling and terminal costs;
- terminal constraints;
- validation and metadata; and
- result extraction metadata or component-owned result contributions,
  following the component contract established by Milestone 16.

AC should retain the storage apparent-power capability coupling between net
real injection and reactive power. Lossy DC and single-node DC omit reactive
power but use the same charge/discharge and energy-state semantics.

Do not duplicate loss dynamics, throughput logic, or fallback mode construction
across formulation builders.

## 8. Result contract

Retain, at minimum:

- charge power;
- discharge power;
- net battery injection;
- storage loss;
- SoC;
- reactive power where modeled;
- throughput cost;
- terminal cost;
- simultaneous-operation diagnostic;
- whether fallback was invoked; and
- whether the returned solution is the primary or polished solution.

Renewable curtailment remains separately reported as a metric of interest.

## 9. Verification gates

### Gate 1 — Algebra and convexity

- Unit-test signs and units in the energy transition.
- Verify DCP compliance for every supported convex formulation.
- Directly test that the perturbation preserves the SoC transition and reduces
  throughput.
- Validate efficiencies, loss parameters, ratings, and time-step duration.

### Gate 2 — Primary exactness behavior

- Show that simultaneous charging and discharging are absent, to the stated
  engineering tolerance, in single-node, lossy-DC, and AC studies with
  zero-cost renewable curtailment.
- Exercise high- and low-renewable conditions, terminal constraints, terminal
  costs, and multiple storage devices.
- Test the assumptions explicitly: curtailment headroom, network feasibility,
  positive throughput weight, and round-trip efficiency.

### Gate 3 — Limitations

- Include a counterexample or limitation test when a positive curtailment
  penalty or binding network constraint removes the improving feasible
  direction.
- Do not describe the exactness result more broadly than these tests and the
  mathematical assumptions support.

### Gate 4 — Fallback

- Construct a controlled fixture that triggers material split operation.
- Verify the relaxed mode bounds use physical ratings.
- Verify rounding is deterministic under the selected policy.
- Verify the polished model contains no helper variable and enforces one fixed
  direction per interval.
- Compare primary, relaxed, and polished objective values and constraint
  residuals.
- Exercise and document rounded-polish infeasibility behavior.

### Gate 5 — Compatibility

- Existing `StorageUnitIdeal` behavior and results remain unchanged.
- Terminal policies from Milestone 12 compose identically with ideal and lossy
  storage.
- AC, lossy-DC, and single-node builders consume the same storage-owned
  horizon contract.

## 10. Out of scope

- Flow-battery on/off operating states beyond the charge/discharge direction
  fallback.
- Degradation state dynamics beyond the existing throughput cost.
- Integer unit commitment.
- A general-purpose mixed-integer storage API.
- Changes to the zero-cost renewable-curtailment policy.

## 11. First-implementation decisions (owner, 2026-10-21)

These decisions resolve the items Section 2 requires before implementation
and fix the scope of the first implementation. Sections 1-10 remain the
governing milestone plan.

### 11.1 Device and energy dynamics

Public dataclass `StorageUnitLossy` in its own component module
`storage_lossy.py`. Fields are those of `StorageUnitIdeal` (`bus`,
`apparent_power_rating`, `capacity`, `initial_soc`, `aging_weight`, the
Milestone 12 terminal fields, `device_id`) with identical semantics, plus:

- `charge_efficiency` in (0, 1];
- `discharge_efficiency` in (0, 1];
- `self_discharge_per_month` in [0, 1): fraction of stored energy lost per
  month, where a month is `HOURS_PER_MONTH = 730` hours (8760 / 12).

The loss model is proportional self-discharge (Section 2, option 3). The
parameter is a fixed-duration monthly rate, matching how battery
self-discharge is usually specified, so device physics does not depend on
the problem's `delta` (owner decision, 2026-10-28; previously an hourly
rate). Per-interval retention is the constant

    r = (1 - self_discharge_per_month) ** (delta / HOURS_PER_MONTH)

computed at preparation, so the transition stays affine:

    soc[t+1] = r * soc[t] + delta * (eta_ch * p_ch[t] - p_dis[t] / eta_dis)

Self-discharge acts on the energy stored at the start of the interval.
Conversion loss and self-discharge are reported separately (Section 2):

    conversion_loss[t] = delta * ((1 - eta_ch) * p_ch[t]
                                  + (1 / eta_dis - 1) * p_dis[t])   (MWh)
    self_discharge[t]  = (1 - r) * soc[t]                          (MWh)

### 11.2 Operating set: identical to ideal storage on net power

The model is exactly `StorageUnitIdeal` except for charge/discharge
efficiency, self-discharge, and the throughput regularization (owner
decision, 2026-10-21). `p_ch >= 0`, `p_dis >= 0`, and net injection
`b = p_dis - p_ch` (an expression, not a variable) obeys the ideal operating
set with `S = apparent_power_rating`: AC/SOCP `b^2 + b_q^2 <= S^2`, DC
`|b| <= S`. SoC bounds `0 <= soc <= capacity` are unchanged.

No charge/discharge mode constraint is imposed: the integer mode constraint
is dropped, not relaxed. Simultaneous operation is excluded only by the
throughput regularization, under the Section 4 assumptions.

Considered and not adopted: the projected relaxed-mode constraint
`p_ch + p_dis <= S` (the binary-mode bounds of Section 6 with `z` relaxed
to [0, 1]). It bounds circulation by the physical rating, but together with
`p_ch >= 0` and the AC/SOCP circle it creates a degenerate active set
(linearly dependent constraint gradients) whenever discharge reaches the
rating; CLARABEL then returned `optimal_inaccurate` on case9 SOCP.

Consequence (Gate 3): when the Section 4 assumptions fail, for example a full
battery facing uncurtailable surplus, the split powers are bounded only by
the net rating. On case9 single-node with a full 40 MW unit, 30 MW of
surplus was absorbed with `p_ch` about 207 MW; with the hull constraint the
same problem was infeasible. The `ls_split` diagnostic flags such solutions.

### 11.3 Throughput regularization

Stage-cost rate `aging_weight * (p_ch + p_dis)`, time-integrated by shared
assembly. `aging_weight` must be strictly positive (Section 3), unlike
`StorageUnitIdeal`, which admits zero. The default is 1e-2, matching ideal
storage.

### 11.4 Public API and component wiring

- New `lossy_storage=` argument on `build_opf` and `build_opf_multistep`,
  threaded through every formulation parser to `component_requests()`.
- New `LOSSY_STORAGE_ADAPTER`, `ACTIVE` in `ac`, `socp`, `lossy_dc`, and
  `singlenode_dc`.
- `StorageUnitIdeal`, its adapter, and its result keys are unchanged.
- Names use the `ls_` prefix to stay distinct in the shared flattened
  namespaces: variables `ls_p_ch`, `ls_p_dis`, `ls_soc`, and `ls_b_q`
  (AC/SOCP); detection by `"n_ls" in build.data`.
- Vectorized multistep assembly represents every lossy-storage box as an
  explicit constraint. No new `VariableBoxFamily` or leaf-bound
  qualification is added.
- Only the default (disabled) numerical-preparation policy is supported for
  lossy storage. Builds that combine lossy storage with any other policy are
  rejected with a clear error.

### 11.5 Results and diagnostic

Result keys (engineering units, per Section 8): `ls_p_ch`, `ls_p_dis`,
`ls_b` (net injection), `ls_b_q` (AC/SOCP), `ls_soc`, `ls_cost`,
`ls_conversion_loss`, `ls_self_discharge`, terminal deviation and cost when
configured, and the split-operation diagnostic

    ls_split = min(p_ch, p_dis)   (MW, per unit and interval).

The engineering tolerance for material split operation (Section 5) is
`SPLIT_TOLERANCE_MW = 1e-3` (owner, 2026-10-21). `extract_results` raises
`SimultaneousOperationError` when any `ls_split` value exceeds it, naming
the largest offending unit and interval. The complete results dict is
attached to the exception, so the solution is never altered or hidden. No
second solve or fallback is triggered.

### 11.6 Scope of the first implementation

In scope: Sections 2-5 and 7-8 as resolved above, with Gates 1, 2, 3, and 5.
Gate 2 includes the reduction check: with unit efficiencies and zero
self-discharge, the optimal objective equals the `StorageUnitIdeal`
objective.

Deferred: the Section 6 fallback and Gate 4, hierarchical-controller
support, leaf-bound qualification, and non-default numerical-preparation
policies.
