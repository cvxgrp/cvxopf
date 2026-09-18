# Milestone 24 — Phase-angle DC optimal power flow

**Status:** planned; documentation only, not an implemented formulation.

**Proposed public key:** `formulation="phase_angle_dc"`.

**Depends on:** the existing public build/result API and M16+ component assembly.
Coordinate time-vectorized support with M14 and hierarchical selection with M21;
neither a new hierarchy nor an annual experiment is part of this milestone.

Numbering follows the project-wide roadmap: M22 (load-group penalties) and M23
(unit commitment) are already assigned on the development branch even though
their plans have not yet reached `main`.

## 1. Goal and modeling distinction

Add the familiar lossless, phase-angle DC approximation as a fourth selectable
network model alongside `ac`, `lossy_dc`, and `singlenode_dc`. Preserve all
existing formulation names, defaults, mathematical models, and result contracts.

The new model enforces both nodal conservation and a linear relationship between
branch power and bus voltage-angle differences. In a meshed network this imposes
reactance-dependent routing and cycle consistency, unlike the existing
`lossy_dc` network-flow model, which has nodal balance, branch boxes, and an
objective-only resistance-weighted quadratic flow proxy, but no angle equations.
Do not rename or silently replace that existing model.

The standard approximation assumes approximately unit voltage magnitudes,
small branch angle differences, and negligible branch resistance and charging.
It omits reactive balance and voltage-magnitude optimization. It is not an AC
feasibility certificate, an AC relaxation bound, or a physical DC-grid model.
Whether it gives more useful dispatch or hierarchical signposts than the
existing network-flow approximation is a comparison to make, not a guarantee.

## 2. Mathematical contract

Use the repository's incidence convention: `A[from, e] = -1` and
`A[to, e] = +1`, with positive flow from the branch's from bus to its to bus.
For each in-service branch, in per-unit power and radians:

```text
d_e = 1 / (x_e * tau_e)
f_e = d_e * (theta_from - theta_to - phi_e)

f = diag(d) * (-A.T * theta - phi)       [active branch rows]
A * f + p_component - g_sh = 0
-f_max <= f <= f_max                    [rated active branches]
theta_reference = 0                    [one per passive-network island]
```

Here `x` is series reactance, `tau` is a fixed tap magnitude (MATPOWER zero
means unity), `phi` is the fixed phase shift converted from degrees, and
`g_sh = GS/baseMVA` is constant active shunt demand at unit voltage magnitude.
`p_component` is the aggregate signed net device injection in per-unit,
including generation, loads, storage, nondispatchable generation, and HVDC.
Do not also subtract load outside the component aggregate.

Variables are bus angles `theta`, generator active outputs `Pg`, and enabled
device variables. Branch flows may be explicit variables linked by affine
equalities or affine expressions of angles; select one representation in S0
and keep extraction independent of this internal choice. No `Vm`, `Qg`, or
reactive network variables are introduced. Voltage setpoints do not become
constraints in this formulation.

The objective is the existing time integral of device stage-cost rates plus
once-per-horizon terminal costs. There is **no branch-loss cost or branch-loss
injection** in the baseline model. In particular, do not inherit `loss_weight`
as a hidden quadratic flow penalty. Existing explicitly lossy HVDC devices
retain their own converter-loss model; lossless AC branches do not make those
devices lossless.

All network constraints are affine. With convex quadratic device costs and
the existing affine DC device constraints, the problem is a convex QP; linear
or convex piecewise-linear costs admit an LP representation. Additional user
constraints may change the problem class and must still satisfy DCP. Use
`build.solve()` with the convex solver path (`nlp=False`, default CLARABEL).

## 3. Network and API edge cases

- Preserve external bus/device identity and full MATPOWER branch-row reporting.
  Out-of-service branches carry zero reported flow and impose no angle link,
  rating, or angle-difference constraint; exclude them before dividing by `x`.
- Validate active reactances as finite and nonzero; never replace zero reactance
  by an arbitrary epsilon. Signed finite reactance remains affine. Document any
  narrower supported input domain explicitly, rather than taking `abs(x)`.
  Validate effective tap magnitudes as finite and positive and shifts as finite.
- Compute islands from in-service passive branches, not controllable HVDC links.
  Set one deterministic gauge reference per island, including isolated buses.
  An island without adequate supply is infeasible, not silently supplied by a
  slack generator. A reference angle is a gauge, not an unbounded power source.
- Interpret positive finite `rateA` as an active-MW limit under the DC
  approximation, not a modeled MVA circle. Freeze zero/unlimited/sentinel and
  `enforce_branch_limits` behavior against existing package conventions in S0.
- Support MATPOWER `ANGMIN`/`ANGMAX` as affine bounds on raw bus angle difference
  (not the phase-shift-adjusted difference). Freeze degree conversion,
  unconstrained sentinels, and zero/zero conventions against the selected
  MATPOWER/PYPOWER reference. Keep their activation separate from thermal limits.
- Single-step and multistep builders must compose all existing DC-capable device
  families through the shared registry. Reuse device physics, costs, identity
  alignment, and horizon hooks; do not copy orchestration into a fourth fork.
- Publish `Pg`, `p_net`, `p_flows` in MW and `Va_deg` in degrees, plus the ordinary
  device results. Preserve single-step versus multistep shapes, including T=1,
  inactive branch rows, and unsolved/failed-result schemas. Do not invent reactive
  or voltage-magnitude result fields. Distinguish gauge-normalized angles from
  physically meaningful angle differences.
- Explicitly declare adapter capabilities and handle formulation-specific
  options. Document inapplicable AC/loss-proxy options; do not let a supposedly
  active option silently alter a different mathematical model.

## 4. Implementation sequence

| Stage | Deliverable | Exit evidence |
| --- | --- | --- |
| S0 — Freeze semantics and references | Confirm key, flow representation, option behavior, islands, taps/shifts, shunts, ratings, angle bounds, result schema, and reference version. | Small hand-calculated fixtures and expected outputs; public contract reviewed before implementation. |
| S1 — Network kernel | Sparse incidence/susceptance preparation and affine phase-angle equations in the appropriate network/formulation modules. | Two-bus, triangle, transformer, disconnected and branchless cases; independent balance and angle-flow residual checks. |
| S2 — Public formulation | Register single/multistep builders, component capabilities, convex solve dispatch, and result extraction. | Every present device family, explicit loads and fallback, T=1, non-unit delta, storage recurrence and terminal terms; failure-schema tests. |
| S3 — Scientific equivalence | Matched standard DC-OPF reference and independent result audits. | Objective/feasibility agreement under aligned costs, ratings, shunts, shifts, angle bounds, and gauge; demonstrate meshed routing difference from network flow. |
| S4 — Temporal scaling | Add the new network kernel to the M14 time-vectorized assembly once available on the implementation baseline. | Stepwise/vectorized scientific equivalence and bounded representative construction/solve/memory measurements; no automatic annual launch. |
| S5 — Documentation and completion | One concise example and formulation comparison, public API/roadmap documentation, extension notes. | Regression suite and applicable typing/lint checks clean; M21 handoff documented without changing the current M17 controller. |

Use a formulation-local module (for example `phase_angle_dc_problem.py`) and
shared sparse network preparation. Existing builders must remain independent
of the new builder. Share genuine common operations through helpers rather than
importing one formulation implementation into another. Network matrices should
scale with buses/branches and nonzeros, not dense bus-by-bus-by-time tensors.
Avoid a dense PTDF construction merely to eliminate angle variables.

M14 integration must use its reviewed component bindings and scaling contracts;
do not reproduce an experimental vectorization implementation inside this plan.
Until qualified, an unsupported explicit vectorized request must fail clearly,
not silently fall back to a loop. Stepwise availability may land first; mark
vectorized support pending rather than implying annual-scale qualification.

## 5. Scientific validation and comparison

1. Independently reconstruct branch flows from angles, reactances, taps and
   shifts; reconstruct nodal balance from exported device quantities and shunts.
   Check limits, gauges, device bounds, SoC dynamics, and objective accounting.
2. Use a three-bus mesh with unequal reactances to expose the routing distinction:
   a nodally balanced flow need not satisfy the angle equations. A radial case
   supplies the complementary check, with aligned device/rating assumptions.
3. Compare against a pinned MATPOWER/PYPOWER DC-OPF reference on small standard
   cases. Keep reference generation a development-only workflow; do not add
   PYPOWER as a package dependency or alter existing AC oracle fixtures.
4. Align the full modeled feasible set and objective before comparing optima.
   Check objective and physical residuals first; require coordinate agreement
   only where uniqueness is justified. Record tolerances and reference versions.
5. Include fixed phase shifts, nonunity taps, parallel branches, inactive rows,
   disconnected islands, isolated loads, angle bounds and nonzero active shunts.
   Test invalid inputs and solver-error/unavailable-primal paths without hiding
   failures or substituting zeros for unavailable optimized quantities.
6. Compare all four formulations on a common small scenario, explicitly labeling
   different physics and costs. No claim that DC feasibility implies AC
   feasibility, or that its objective bounds AC, follows from this comparison.
   Loss-proxy versus angle-constraint effects require separate controlled changes
   if a causal explanation of redispatch is sought.

Review to scientific correctness/repeatability and proportional public-API
quality: inspectable fixtures, independent residuals, fixed identities and
backward compatibility. No new adversarial provenance framework is needed.

## 6. Boundaries and completion

Out of scope: changing existing `lossy_dc`, reactive/voltage physics, physical
AC branch-loss approximations, variable taps or phase shifts, topology switching,
contingencies, unit commitment, PTDF API, and new annual execution protocols.
Future M21 may select this formulation as a planning layer after its own
formulation-specific audit and handoff qualification. This milestone does not
silently broaden M17's currently fixed `lossy_dc` to `ac` workflow.

Completion requires the explicit fourth formulation, unchanged existing defaults,
validated device composition and result schemas, independent mathematical
checks and reference comparisons, qualified temporal modes, and documentation
that makes its lossless/phase-angle assumptions unmistakable.

## References

- [MATPOWER DC modeling](https://matpower.app/manual/matpower/DCModeling.html):
  assumptions, fixed taps and phase shifts, shunts, and network equations.
- [MATPOWER standard DC OPF](https://matpower.app/manual/matpower/StandardDCOPF.html):
  angle/generation variables and quadratic-program structure.
- [MATPOWER branch angle-difference limits](https://matpower.org/doc/ref-manual/legacy/functions/makeAang.html):
  linear angle-bound interpretation; pin exact importer conventions in S0.
- Repository architecture: `CLAUDE.md`, `plans/milestone-16-plus-component-adapters.md`,
  and `plans/milestone-21-configurable-hierarchy.md`.
