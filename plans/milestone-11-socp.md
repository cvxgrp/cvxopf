# M11 — Sparse voltage-product SOCP relaxation

Status: implementation started, 2026-10-01. Gate A's sparse network-map increment
and Gate B's shared-device capability groundwork are implemented and tested;
the public builder, common extraction, and explicit numerical audit/recovery
slice is now implemented for review. The bounded evidence and architectural
checkpoint are in [the report](../experiments/m11_socp/REPORT.md). Gate E1's
analytic two-bus and matched independent Case9/14 reference suite was accepted
and committed at `5dd963bb8`; see [its report](../experiments/m11_socp/E1_REPORT.md).
Gate E2's matched AC/SOCP comparison validation/evidence is now implemented and
executed for review; see [its report](../experiments/m11_socp/E2_REPORT.md).
All four pairs passed containment and independent accounting; direct recovery
of the optimized meshed points remains unsuccessful, as reported rather than
required for this gate. The full suite passes 3389 tests and six subtests.
Owner review/acceptance of E2 remains pending. On 2026-10-02 the owner inserted
Gate E3: a matched 24-hour, four-formulation Tracy/Case118 resilience comparison,
specified below. Window/boundary selection and a bounded execution protocol
precede that study's numerical launch. The existing Tracy rolling-horizon and
annual AC execution remain on hold; this plan update does not restart them.

The two checkpoints below preserve the earlier increment history; their pending
items are superseded where noted by the current builder/diagnostics report.

### Historical implementation checkpoint — Sparse network maps

`src/cvxopf/_voltage_product.py` consumes the existing `BranchAdmittance` and
full Ybus outputs, providing deterministic pair/orientation metadata and real
CSR maps for nodal and both-terminal powers. The maps apply directly to the
time-batched lifted state without constructing a graph per interval. No device
orchestration, admittance formulas, result extraction, or solver path is copied.
SciPy is declared as a direct dependency (already transitively required by
CVXPY); the offline lock refresh changes no package versions.

Verification: `uv run --extra dev pytest tests/test_voltage_product.py
tests/test_network.py -q` passes 90 tests (20 new, 70 existing). These tests
compare direct complex-voltage physics, not optimized dispatch, and cover the
Gate A topology/transformer/shunt cases, terminal units, and time batching. The
environment emits its existing CVXOPT/CyIpopt OpenMP import warning; these tests
do not invoke either solver. Actual branch-limit enforcement, public option
rejection (including `sparsity_tol`), component assembly, diagnostics, and the
first-complete-builder architectural checkpoint remain pending.

### Historical implementation checkpoint — Shared-device capability groundwork

The closed formulation registry now declares SOCP on all five component families,
reusing their existing AC device bindings. `supports_reactive_power()` separates
P/Q channel support from solver class. A typed `SquaredVoltageNetworkState`
prevents substituting AC magnitude state for lifted squared voltage. Generator
setpoint selection is shared, with only its affine constant binding differing.
The bound registry explicitly records `socp_explicit_policy` for applicable
device box families; no new leaf-bound qualification is claimed. The reactive
vectorized device path checks those registry decisions.

`tests/test_socp_components.py` adds 27 checks of stepwise/vectorized contribution
parity, all five device registrations, hard/soft storage terminal policies,
reactive shedding, costs, shared publication/projection, network-state type
rejection, setpoint selection, and bound policy. A focused 14-file regression run
passes 507 tests, including existing AC/DC vectorized builds, component contracts,
generator tests, bound qualification, and result projection tests. Configured
mypy checks and lint on changed source/new tests pass. The broader tests exercise
existing AC/DC solves only; no SOCP optimization or Tracy execution is claimed.

This is not the first-complete-builder checkpoint. Neutral network preparation,
public SOCP dispatch and option validation, network cone assembly, common result
extraction integration, and separate audit/recovery operations remain to be wired.

## 1. Decision and scope

Add a standalone `socp` formulation using a sparse bus-injection voltage-product
relaxation. Reuse the existing admittance model and component framework. Build
the time-vectorized model first, with AC-like active and reactive device channels
and a convex solver. Do not build a radial-only DistFlow model, a dense SDP, or
a new device hierarchy.

The first version retains the existing meanings of transformer taps, phase
shifts, bus shunts, line charging, branch status, voltage controls, both-terminal
branch ratings, and every currently supported device model. Retention means
parity with existing AC behavior, not implementing deferred device features.
In particular, M11 does not add the future full-lossy/reactive HVDC model.

Deliver explicit relaxation residuals and voltage-recovery diagnostics alongside
the dispatch. A feasible relaxation is not automatically an executable AC action.
M17's controller and hard-target acceptance gate remain unchanged. Configurable
hierarchy integration belongs to [M21](milestone-21-configurable-hierarchy.md).

## 2. Reference check

Steven H. Low, [*Convex Relaxation of Optimal Power Flow, Part I: Formulations
and Equivalence*](https://arxiv.org/abs/1405.0766), is an appropriate primary
reference for this choice. Section IV-C, especially OPF-socp (23) and Remark 6,
describes the partial Hermitian bus-injection matrix with a positive-semidefinite
2-by-2 block on each edge: precisely the proposed voltage-product cone.
Section IV-D supplies the spanning-tree recovery construction. Sections IV-B
and IV-E distinguish edge rank conditions, cycle consistency, and relaxation
tightness. The branch-flow formulation in Section V is not our implementation
starting point.

The tutorial presents a simplified network model and discusses extensions in
Section III-B. It is not a specification of this repository's transformer and
rating conventions, nor a guarantee of exactness for our device mix or meshed
cases. Use `network.py` and independent AC identities to establish those details.

## 3. Mathematical contract

### Lifted state and sparse topology

For each interval t, introduce real variables

\[
w_{i,t}=|V_{i,t}|^2,\qquad
c_{ij,t}=\Re W_{ij,t},\qquad s_{ij,t}=\Im W_{ij,t},
\quad W_{ij,t}\approx V_{i,t}\overline{V_{j,t}}.
\]

Store one off-diagonal product per unique unordered in-service bus pair, with a
deterministic orientation. The opposite orientation is its complex conjugate;
the diagonal is w. Preserve an explicit original-branch-row-to-pair map and
orientation sign. Parallel and reverse-oriented branches share products but
retain their own terminal coefficients and ratings.

Build the pair set from active branch topology, not only nonzero aggregated
Ybus entries: parallel admittances can cancel while terminal flows still need
the pair. Handle diagonal/self-loop contributions explicitly if accepted by the
existing input contract; never create an independent diagonal product. Preserve
external/internal bus mappings and inactive output rows. Empty pair sets must
work without constructing invalid zero-sized CVXPY variables.

Use `(nb, T)` for w and `(npairs, T)` for c and s. No dense `nb × nb × T`
decision array and no Python loop constructing a separate network per hour.

### Edge cones and voltage bounds

For each stored pair and interval impose

\[
\left\|\begin{bmatrix}2c_{ij,t}\\2s_{ij,t}\\w_{i,t}-w_{j,t}\end{bmatrix}\right\|_2
\le w_{i,t}+w_{j,t},\qquad w_{i,t},w_{j,t}\ge0.
\]

This is the DCP representation of \(c^2+s^2\le w_iw_j\); do not express the
product inequality directly as a CVXPY constraint. Voltage bounds are
\(V_{i,\min}^2\le w_{i,t}\le V_{i,\max}^2\), after validating the same
physical voltage domain as AC. Enabled magnitude setpoints become
\(w_{i,t}=V_{i,\mathrm{set}}^2\), preserving existing controlled-bus selection,
setpoint validation, and conflict behavior. Do not impose a new 1-pu slack
magnitude. Angle reference is a recovery gauge, not an extra relaxation variable.

### Affine injections and branch powers

M11 requires `sparsity_tol == 0`. Reject nonzero values at the SOCP build
boundary, including when `enforce_branch_limits=False`; do not silently ignore
the option or implement a thresholded relaxation. Existing AC can omit selected
admittance contributions when a positive tolerance is used with branch limits
disabled, so identical option values alone do not establish matched physics.

Use the existing Ybus and `make_branch_admittance` coefficients, without
assuming Ybus is symmetric (phase-shifting transformers need not satisfy that).
For every bus,

\[
S_{i,t}=P_{i,t}+\mathrm{i}Q_{i,t}
=\sum_j\overline{Y_{ij}}W_{ij,t}.
\]

For branch row l with from bus f and to bus k,

\[
S_{l,f,t}=\overline{y_{ff,l}}w_{f,t}
             +\overline{y_{ft,l}}W_{fk,t},\qquad
S_{l,k,t}=\overline{y_{tt,l}}w_{k,t}
             +\overline{y_{tf,l}}\overline{W_{fk,t}}.
\]

These identities follow directly from \(I=YV\) and \(S=V\overline I\).
Implement real sparse affine maps, with orientation signs applied explicitly.
Do not insert transformer phase shifts into W a second time: they already live
in the branch coefficients. Keep taps, charging, bus shunts, and status handling
in the authoritative network preprocessing. Do not duplicate their formulas.

Balance the real and imaginary nodal injections against the shared component
aggregate. For every branch currently subject to a positive finite rateA limit,
enforce both

\[
\|(P_{l,f,t},Q_{l,f,t})\|_2\le \mathrm{rateA}_l/\mathrm{baseMVA},\qquad
\|(P_{l,k,t},Q_{l,k,t})\|_2\le \mathrm{rateA}_l/\mathrm{baseMVA}.
\]

Respect the existing branch-limit option and unrated/inactive conventions.
Internal network quantities stay per-unit; report terminal powers and limits in
MW/MVAr/MVA using the existing conversion boundary. Report relaxed branch losses
as the sum of signed terminal real powers. Include bus-shunt consumption in the
system energy accounting, without counting it again as a device load.

### Objective and device feasible sets

Reuse all existing device variables, operating sets, injection maps, costs, and
temporal constraints applicable to AC: generator P/Q limits and costs,
nondispatchable availability/reactive capability, storage P/Q capability and SoC
coupling, terminal policies, aging, shedding and its reactive treatment, and
the currently implemented HVDC behavior. Preserve identities, delta scaling,
initial state, terminal boundary indexing, and cost decomposition.

SOCP physically represents real-energy withdrawal through its relaxed network
balance. Do not add the lossy-DC resistance/flow objective proxy by default.
That proxy is not physical withdrawal, as documented in the
[Stage C report](../experiments/case118_tracy_2021/STAGE_C_REPORT.md).
An explicitly requested DC-only loss-cost option must not silently alter the
SOCP objective; document and validate its non-applicability. Any future optional
SOCP regularizer must be separately named and disables unmatched lower-bound
claims. M11 adds none.

## 4. Narrow architecture changes

The current formulation literals, registry checks, result dispatch, and many
adapter branches recognize only AC and the two DC formulations. Several
`formulation == "ac"` tests select reactive channels rather than nonconvex
network physics. Adding one formulation string is insufficient.

Make these three distinctions explicit with a small closed, typed policy:

| Concern | AC | SOCP | Existing DC |
| --- | --- | --- | --- |
| Device active/reactive channels | P and Q | Same P and Q | Existing P-only behavior |
| Network voltage representation | Magnitude and angle | Squared magnitude and edge products | Existing DC state |
| Solver class | DNLP/IPOPT | Convex conic | Convex |

Add a narrow reactive-channel capability helper rather than renaming SOCP to AC.
Audit every AC-specific branch individually: channel selection may be shared;
voltage equations, initialization, solver dispatch, and result semantics may not.
Keep `FormulationCapability` ACTIVE/NULL/UNSUPPORTED semantics intact, extending
each component registry explicitly for `socp`.

Add a typed squared-voltage network state alongside `ACNetworkState` and
`DCNetworkState`. Magnitude-setpoint coupling needs a small representation-aware
binding: equality in v for AC, equality in w to the squared constant for SOCP.
Reuse the device's authoritative setpoint data and selection. Do not pass
`sqrt(w)` as a fake AC decision variable or duplicate device operating equations.
Extend StepContext/VectorizedContext validation to reject mismatched states.

Expected implementation boundaries:

- `network.py`: sparse pair/orientation metadata or a small adjacent pure-NumPy
  helper; existing admittance coefficients remain authoritative.
- A new SOCP network builder: sparse affine maps, cones, balance, voltage and
  branch constraints; shared component preparation and assembly.
- `_temporal_assembly.py`, `_component_adapter.py`, `_component_adapters.py`:
  closed formulation registration, channel capability, and voltage-state binding.
- `problem.py`: public dispatch, reactive-input validation, option applicability,
  and `OPFBuild(is_convex=True)`. Use `OPFBuild.solve`, not direct `prob.solve`.
- `results.py`: lightweight lifted-output extraction and common device/branch
  reporting through registered projections.
- A focused diagnostics helper: separately invoked relaxation audits and voltage
  recovery, independent of routine result extraction.

Use the existing convex default solver and time-vectorized SCIPY canonicalization
path. SOCP must not enter DNLP/IPOPT or inherit AC automatic sparse-dispatch
policies. Assert DCP compliance on complete builds, not only individual cones.

Construct the vectorized core from the outset. Provide the single-step entry
point through T=1 with existing public shape conventions. Preserve the public
explicit stepwise option using a thin reference assembly over the same network
and device equations; do not maintain a second mathematical implementation.
Default SOCP multistep assembly is vectorized. Test equivalence on small horizons;
do not make long stepwise benchmarks a milestone requirement.

### Infrastructure reuse and architectural acceptance

Adding SOCP introduces new network mathematics and representation-specific
diagnostics, not another implementation of component orchestration, temporal
accounting, input alignment, or device reporting.

| Responsibility | Authoritative infrastructure M11 must use |
| --- | --- |
| Public input normalization and device-series alignment | `problem.py`; do not repeat alignment inside the SOCP builder. |
| Case indexing and physical admittances | `network.py` and existing case validation; retain their physical and indexing contracts. |
| Component discovery and preparation | `component_requests()` and `prepare_components()`. |
| Device variables, injections, and constraints | `assemble_component_vectorized()` and `aggregate_vectorized_contributions()`; stepwise uses `assemble_component_step()`, `aggregate_step_contributions()`, and the existing horizon assembly/aggregation. |
| Stage-cost integration and terminal costs | Existing `integrate_*` helpers in `_component_assembly.py` and the aggregated horizon terminal cost, added once; no SOCP-specific reconstruction of device objectives. |
| Variable, expression, and metadata publication | Existing `publish_*` helpers in `_component_assembly.py`, preserving namespace validation and presence semantics. |
| Internal-to-public temporal shapes | `ResultProjectionRegistry`, `ResultProjectionSpec`, `vectorized_component_result_projections()`, and registry merging; no parallel transpose/reshape path. |
| Device results and unsuccessful-result schemas | Shared machinery in `results.py`, including `_initialize_results()` and `_add_device_results()`; extend formulation/channel coverage instead of copying it. |
| Branch-terminal result conversion | Reuse or narrowly generalize `_add_ac_branch_results()` in `results.py`; its unit conversion and magnitude reporting are representation-independent. |
| Leaf versus explicit bounds | `_temporal_assembly.py`'s formulation/box-family decision registry and `box_representation_decision()`. |
| Generator voltage-setpoint selection | One shared selection rule owned by `generator.py`; representation-specific binding only. |
| Solver defaults and execution | `OPFBuild.solve()`. |

Implementation checklist:

- Do not copy an existing formulation builder wholesale. SOCP owns lifted
  network variables, affine network maps, cones, and balance assembly; it must
  not acquire copied generator preparation, storage recurrence, load reporting,
  or device cost logic. If necessary shared preparation currently lives inside
  `ac_problem.py`, extract only a small genuinely shared helper to an appropriate
  neutral module. SOCP must not import the AC builder's private parser. This is
  not authorization for a broad builder refactor.
- Extend the bound decision registry explicitly for each applicable SOCP box
  family, including squared voltage where appropriate. Record the representation
  and qualification authority; do not silently inherit AC/DC decisions or label
  new SOCP choices as already M14-qualified. Exercise the existing focused bound
  qualification path where required, without changing existing formulations'
  decisions.
- Register network projections and merge them with component projections.
  Preserve interval versus boundary views, shape validation, and duplicate-name
  rejection. Do not add device-specific result reshaping in the SOCP extractor.
- Extend the common unsolved, partial-primal, and failed-result schemas. Preserve
  applicable fields, device identities, absent-component behavior, and existing
  `None`/NaN conventions when only some values are available. Do not substitute
  zero arrays or create a separate SOCP failure-schema implementation.
- Share branch-terminal conversion across AC and SOCP; narrowly rename/generalize
  the current AC-oriented helper if needed. Keep units, original row ordering,
  partial-value handling, and existing AC outputs unchanged.
- Extract/reuse a single generator setpoint-selection rule: the first active
  generator in list order at each controlled bus supplies the constant. AC binds
  it to v; SOCP binds its square to w. Preserve status, controlled-bus, mapping,
  and disabled-option behavior. Do not maintain separate generator-selection
  loops for the two representations.
- Keep result extraction lightweight: it reads/publishes values and performs
  routine conversions, but never solves, repairs, audits, or recovers voltages
  implicitly. Relaxation auditing and voltage recovery are separate explicit
  operations. Independent audits reconstruct numerical physics from returned
  values and authoritative input data, not merely builder constraint residuals.

## 5. Results, residuals, and recovery

Publish w, real/imaginary edge products, oriented pair identities, branch-row
mapping, and all existing applicable device results. Public interval arrays are
time-first; storage boundary/result conventions stay unchanged. Use explicit
names and units for squared voltage and edge products. `sqrt(w)` may be reported
as relaxed voltage magnitude, but ordinary AC angle/voltage results must not
imply that a globally consistent voltage vector was solved for.

Keep three separate outcomes:

1. Solver termination and numerical status, including inaccurate statuses.
2. Independently checked relaxation feasibility.
3. Voltage recovery and independently checked AC feasibility of that candidate.

Do not derive any of these solely from another. Retain solver residual/gap data
when available and use null/unavailable, not zero, when absent. On infeasible,
unbounded, missing-value, or nonfinite outcomes, do not publish fabricated
voltages, objective comparisons, or recovery success.

### Relaxation audit

Recompute from returned numeric values rather than CVXPY constraint expressions:

- Nodal P/Q balance, voltage bounds and enabled setpoints, both-terminal ratings.
- Device bounds/capability, shedding, storage transitions and terminal policy,
  using the same authoritative parameters but independent numeric evaluation.
- Signed determinant gaps
  \(g_{ij,t}=w_{i,t}w_{j,t}-c_{ij,t}^2-s_{ij,t}^2\): negative values violate the
  cone; positive values measure slack, not constraint violation.
- Absolute and scale-normalized edge gaps, using a declared denominator such as
  \(\max(\epsilon,|w_iw_j|,c^2+s^2)\), plus standard-cone violation. Store epsilon,
  units, maxima, worst identities/intervals, and per-interval summaries.

Declare tolerances before numerical gates and record them in results. Rank-like
tightness tolerances are separate from feasibility tolerances; no rounding a
small negative w into an apparently valid result without reporting the violation.

### Voltage recovery

Use a deterministic spanning forest of active pairs. Match existing supported
island/reference semantics; choose a gauge per supported connected component.
For oriented tree edge i→j, use
\(\theta_j=\theta_i-\operatorname{atan2}(s_{ij},c_{ij})\), with conjugation for
reverse traversal. Form \(\widehat V_i=\sqrt{w_i}e^{\mathrm{i}\theta_i}\).

Report wrapped angle inconsistency on every non-tree edge (equivalently a
fundamental-cycle basis), reconstructed product errors, and edge magnitude gaps.
Near-zero products make their phase undefined: mark that diagnostic unavailable
or failed under a declared threshold rather than accepting `atan2(0,0)`.
Positive operating voltage bounds avoid the usual zero-voltage degeneracy.

Edge-tight cones are insufficient on meshes: cycle angles must also be
consistent. Conversely, angles can be cycle-consistent while product magnitudes
remain slack. Only both conditions support exact product recovery, within
declared tolerances. Recovery from an inexact relaxation remains a candidate,
not a proof or an accepted AC point.

Recompute injections and both branch-terminal powers directly from recovered
complex voltages and the original admittance coefficients, retaining the solved
device dispatch and storage trajectory. Audit all AC physical/device/target
constraints and report violations. No implicit power-flow adjustment, dispatch
repair, penalty continuation, or IPOPT polishing belongs in this first version.
Future AC initialization can consume the complete primal candidate explicitly;
it must not mistake terminal SoC alone for a network-state handoff.

## 6. Bounds and scientific claims

For a genuinely matched pair, every feasible AC point lifts to a feasible SOCP
point with the same objective. Hence, mathematically,

\[
f^*_{\mathrm{SOCP}}\le f^*_{\mathrm{AC}}\le f(x_{\mathrm{AC,feasible}}).
\]

Matching includes network/status/rating options, all device data and costs,
reactive inputs, shedding permissions, horizon and delta, initial storage state,
terminal policies, and any externally supplied coupling constraints. Reject or
exclude unsupported coupling expressions from a bound claim. A shared case name
or horizon alone is not enough. Record the comparison configuration and objective
components so that matching is inspectable.

Every matched AC reference must also use `sparsity_tol == 0`, regardless of
whether branch limits are enabled. Reject nonzero-tolerance AC references from
M11 containment and bound comparisons; the full-admittance SOCP is not claimed
to relax that thresholded AC balance model. This restriction does not change
the existing standalone AC API.

A numerical SOCP primal objective is not by itself a certified lower bound:
it is an upper bound on the relaxation optimum if primal-feasible. Label it a
numerical relaxation optimum estimate unless supported by a suitable validated
dual bound/certificate and reported tolerances. A locally returned AC objective
is an upper-bound candidate only after independent AC feasibility checks.
Negative or unexplained gaps trigger an audit, not clipping or an exactness claim.

Likewise, certified infeasibility of a valid relaxation would imply infeasibility
of its matched AC problem, but an inaccurate solver status, failed recovery, or
failed local AC solve is not that certificate. M11 does not establish the cause
of Tracy's unresolved W=1 failure.

## 7. Implementation sequence and acceptance gates

After review of the current builder/diagnostics checkpoint, complete Gate E1's
standard reference suite before expanding into Gate E2's matched device/horizon
comparisons. Independent network validation and internal AC/SOCP containment
answer different questions; neither substitutes for the other.

### Gate A — Network identities and topology

Implement coefficient maps and lift known voltage vectors without optimization.
Compare nodal and terminal powers against direct complex AC calculations on
public/synthetic fixtures, covering non-unit taps, phase shifts, charging, bus
shunts, parallel/reversed branches, cancellation in aggregate Ybus, inactive
rows (including data ignored by the existing inactive semantics), and ratings
binding at either terminal. Include empty-edge and supported island cases.
Assert row identity, conjugation signs, units, and system loss/shunt accounting.

### Gate B — Shared devices and convex assembly

Add registration and representation-aware voltage binding, then the vectorized
network core and entry points. Test each existing component alone and in mixed
cases, P/Q capability and input validation, enabled/disabled voltage setpoints,
shedding, storage initial/cross-step/terminal behavior, and objective decomposition.
Verify all assembled problems are DCP and route to the convex backend. Preserve
existing AC/DC tests and unsupported/null capability behavior.

Test rejection of nonzero `sparsity_tol` in SOCP single-step and multistep builds
(both temporal assemblies), with branch limits enabled and disabled. In
particular, disabling branch limits must not bypass the restriction. Test that
matched-comparison validation rejects a nonzero-tolerance AC reference even
with branch limits disabled, and accepts otherwise matched zero-tolerance pairs.

### Architectural checkpoint — First complete builder

After the first complete builder and result path, review the implementation
against every ownership-table row and checklist item before expanding numerical
experiments. Identify the concrete reused helper/registry for each responsibility
and explain any small neutral extraction. Reject duplicated orchestration,
alignment, temporal accounting, device reporting, or setpoint selection rather
than deferring it as cleanup. No new framework is required.

Check that bound decisions and result projections cover applicable SOCP families,
and that common unsolved, partial-primal, and failed-result behavior is tested
alongside solved extraction. Test shared branch conversion and setpoint selection
for AC compatibility. Verify extraction does not invoke auditing, recovery, or a
solver. Pass this architectural checkpoint as well as the mathematical gates;
numerical agreement alone does not establish architectural acceptance.

### Gate C — Temporal and result contracts

Compare single-step, T=1 vectorized, and small explicit-stepwise builds. Use
T>1 fixtures with changing load/renewable availability and binding storage
coupling. Check non-unit delta, ordering, empty device sets, public time-first
shapes, and all SoC boundary views. Verify expression-object construction does
not grow by creating one independent graph per interval; record variable/cone
counts and a small bounded build/canonicalization/solve timing comparison.
Numerical problem size should scale with `(nb + npairs + devices) * T`.

### Gate D — Diagnostics with positive and negative controls

Use a known rank-one lifted feasible AC point as a recovery-positive control.
Add a triangle with individually tight edge products but inconsistent cycle
angles, and a cycle-consistent case with deliberately slack edge cones. Both
must fail exact-recovery classification for the correct reason. Perturb balance,
ratings, targets, and cone feasibility to exercise each residual. Cover undefined
phases, nonfinite/missing values, and inaccurate solver outcomes.

### Gate E1 — Small standard SOCP reference suite

Establish a stable, public, Python-CI-compatible suite before the matched-pair
evidence slice. Keep its initial scope to the following fixtures:

| Fixture | Reference and acceptance purpose |
| --- | --- |
| Deterministic two-bus case | Derive the optimum independently of the builder, with declared assumptions and physical loss accounting. Choose and verify a case whose optimal lifted point supports successful voltage recovery; do not assume exactness merely because it is radial. |
| Existing repository Case9 | Generate an independent, explicitly matched PowerModels reference for meshed-network objective and relaxation feasibility. |
| Existing repository Case14 | Generate an independent, explicitly matched PowerModels reference covering its transformer/shunt data and meshed-network diagnostics. |
| Existing synthetic triangle controls | Retain Gate D's tight-but-cycle-inconsistent and cycle-consistent-but-slack controls. Each must fail exact recovery for the correct reason; these are diagnostic controls, not external optimum oracles. |

Use PowerModels.jl's `SOCWRConicPowerModel` as the independent implementation of
the squared-voltage/complex-product cone model. Its stock OPF is not automatically
the same relaxation: default angle-difference constraints, associated cuts, and
angle-derived voltage-product bounds must be explicitly excluded from the
reference construction to match this first version. Retain voltage bounds, edge
cones, physical admittances, generator P/Q boxes and costs, and both-terminal
apparent-power ratings. Do not strengthen or otherwise change the production
SOCP model to match a reference default.

Before generating references, record an inspectable model-matching checklist:
exact case arrays and baseMVA, bus/branch identities and product orientation,
status and zero/unrated branch conventions, taps/phases/shunts/charging, voltage
bounds and setpoint policy, generator limits/costs, demand, units and objective
scaling. Use the exact repository Case9/14 inputs rather than similarly named
PGLib variants, and record any external parser normalization; unreviewed input
changes invalidate the comparison. No shedding, extra devices, DC loss-cost
proxy, or reference-only regularizer in these network-reference cases.

Generate references offline in an isolated, pinned environment. Commit compact
fixtures under `tests/fixtures/` with input hashes, PowerModels/Julia/solver
versions, reference-construction settings, solver tolerances and termination,
objective/components, complete lifted network and generator primal, and signed
both-terminal powers with identities and units. Keep reusable generation code
and its pinned environment under `scripts/socp_reference/`, and analytic cases
and independent test-oracle helpers under `tests/`. Matching notes, execution
plans, experiment-specific analysis and evidence reports stay in
`experiments/m11_socp/`; raw logs remain in its ignored `results/`. CI reads the
committed fixtures and runs the Python implementation; it neither installs Julia
nor downloads or regenerates references. No new runtime dependency is required.

Predeclare objective and physical-residual tolerances before inspecting numerical
agreement. Independently reconstruct reference and returned-primal balance,
voltage/device/rating constraints, cone feasibility and objective accounting;
compare optimum estimates within the declared objective tolerance. Compare
individual dispatch/voltage values only where uniqueness is established, not as
a default golden-array requirement. Record edge tightness, cycle inconsistency
and recovered AC residuals separately; a meshed reference need not recover an
AC-feasible point. Neither solver success nor objective agreement alone passes
the gate, and no numerical primal objective is labeled a certified lower bound.

Predeclare solver settings, maximum solves, per-solve limits, a small total
generation/diagnostic budget and stopping criteria before execution. Stop for
review after this suite; do not expand cases, retry indefinitely or relax
tolerances to obtain agreement. Report unavailable references and discrepancies
explicitly. PGLib's three-bus `case3_lmbd` is a possible later extension, not
required in this first slice; published rounded SOC gaps are sanity checks,
not precise golden fixtures.

Primary reference implementation and benchmark sources:

- [PowerModels network formulations](https://lanl-ansi.github.io/PowerModels.jl/stable/formulations/)
  and [voltage-product cone implementation](https://github.com/lanl-ansi/PowerModels.jl/blob/master/src/form/wr.jl).
- [Default OPF assembly](https://github.com/lanl-ansi/PowerModels.jl/blob/master/src/prob/opf.jl)
  and [shared angle constraints/cuts](https://github.com/lanl-ansi/PowerModels.jl/blob/master/src/form/shared.jl).
- [PGLib cases](https://github.com/power-grid-lib/pglib-opf) and
  [published baseline](https://github.com/power-grid-lib/pglib-opf/blob/master/BASELINE.md).

Pin exact source revisions in generated-fixture provenance; the links above
identify the implementation, not a frozen regeneration environment.

### Gate E2 — Small matched AC/SOCP evidence

Build on the accepted standard suite with Case9/14 and one deterministic small
multistep storage/shedding fixture. Validate matching of network/options, device
inputs and costs, shedding permissions, horizon/delta, initial storage state and
terminal policies before comparing objectives. Reject thresholded AC references
and unsupported caller coupling as specified in Section 6. No private Tracy
files are required in CI. Lift independently feasible AC candidates to verify
containment and identical objective components. Solve the matched relaxation,
report physical losses, shedding, objective components and estimate/available
dual evidence, residuals, rank and cycle diagnostics, and recovered AC residuals.
Retain the complete device primal in the lift. Include a mesh; do not require
its relaxation to be exact. Use objective tolerances rather than identical
dispatch where multiple optima exist.

Before execution, predeclare the fixture list, solver settings, per-solve and
total diagnostic budgets, and stopping criteria. No automatic expansion into a
Case118 annual sweep. Record hardware/software, sizes, build/canonicalization/
solve time, and solver termination separately. Finish with a compact experiment
report, reproducible commands, and a full regression test run appropriate to the
changed interfaces. No milestone closure from solver status alone.

### Gate E3 — Matched 24-hour Tracy resilience comparison

**Owner-directed insertion, 2026-10-02; design approved in principle, execution
not yet authorized.** After E2 review, compare `singlenode_dc`, `lossy_dc`,
`socp`, and `ac` on four fixed Tracy/Case118 periods. Each arm solves one complete
24-hour multistep OPF at hourly resolution: **16 primary problems**, not an MPC
rollout, hierarchy, sequence of one-hour controls, or sharded execution. All
interior decisions are optimized jointly. The annual DC trajectory supplies
window-selection context only, not intermediate SoC targets or fixed dispatch.

The scientific question is what network-model fidelity changes about resilience
outcomes and their limiting mechanisms. Direct voltage reconstruction is a
supporting diagnostic, not the primary success criterion. A formulation can
provide useful evidence about resource adequacy, shedding, storage or network
limitations even when its returned point cannot be directly realized in AC.
The troublesome W=1 hard-endpoint AC case does not establish that these longer,
jointly optimized problems are difficult or infeasible; do not use it to predict
their outcome. Diagnosing that exact failed problem remains a separate question.

#### Select the four windows without new optimization

Reuse the already selected Stage D event anchors, rather than selecting new
events from SOCP/AC results. Original times use the source's **fixed UTC−08:00**
calendar, not daylight-saving local time:

| Existing event | Original six-hour start | Global hour t0 |
| --- | --- | ---: |
| Large surplus | 2021-05-19 12:00 | 3324 |
| Large deficit | 2021-02-19 01:00 | 1177 |
| Surplus → deficit | 2021-12-24 13:00 | 8581 |
| Deficit → surplus | 2021-04-13 04:00 | 2452 |

Source identities, selection scores and the 27 storage IDs are retained in
[the original selection](../experiments/case118_tracy_2021/stage_d_selection/selection.json).
Use the accepted Stage C lossy-DC annual archive identified there, not a new
annual solve, the old analytical/synthetic study, or substitute Tracy inputs.

For each anchor, inspect 24-hour candidates `[s,s+24)` containing the entire
original event `[t0,t0+6)`, with integer `t0-18 <= s <= t0`, clipped to the year.
The old look-ahead padding is context, not an implemented interval or a binding
new-window requirement. These standalone problems need not fit inside a
historical execution shard. Preserve all 24 input intervals and 25 SoC boundary
indices; never wrap the year or truncate a window silently.

Use the annual per-device SoC trajectories to identify candidates reasonably
aligned with 50%-to-50% operation while preserving the event's meaning. Show
input/net-load plots, all device SoC fractions and capacity-weighted fleet SoC,
including each candidate's start/end deviations from 50%. Fleet averages must
not conceal batteries at opposite extremes. Predeclare the endpoint-closeness
score, device weighting and deterministic tie-break before automatic ranking;
review the selected dates and event placement with the owner before freezing.
No selection based on new formulation objectives, feasibility, or solve speed.
These are four deliberately selected events, not an annual representative sample.

#### Common physical inputs and energy boundaries

Primary design: each battery starts at **50% of its own modeled usable energy
capacity** and has a hard terminal equality at **50% at boundary 24**. This is
per-device equality, not a fleet sum. Intermediate SoC is unconstrained except
by ordinary device bounds/dynamics; do not import the annual interior trajectory.

If an event is poorly represented by this 24-hour energy-neutral design, present
that mismatch using the annual/input context. The owner may approve a separately
labeled boundary-condition leg, such as net depletion during scarcity or net
accumulation during surplus. Freeze its explicit identity-aligned initial/final
vectors and rationale; apply the same vectors to all four formulations. Do not
silently replace or pool primary and alternative legs, change only a failed
arm, or force a scenario into an inappropriate 50%-to-50% interpretation.
Every added leg adds four primary problems and needs an explicit budget update.

Retain the approved Stage A data mapping/fleet and Stage C economics
(generator curvature on, rho=1/3; battery throughput lambda=0.01), source
identities, costs, shedding permissions/prices, ratings and delta=1 h. Match
the physical device inputs and identities across all four models. Preserve
their intentional semantics: copper plate has no network; DC has no reactive
optimization; AC/SOCP use P/Q capability and voltage/apparent-power limits.
Do not invent reactive outputs or branch flows for a model that omits them.
Keep existing null-device capabilities, including single-node HVDC elimination
if applicable. AC/SOCP must use full admittances and matched physical options.
No resource resizing, new regularizer, or formulation-specific retuning.

#### Outcomes and interpretation

Report time- and identity-aligned overlays/tables of:

- Served demand, shedding and ENS: timing, duration, magnitude, and location;
  include any configured customer distinctions without introducing new classes.
- Per-device and fleet SoC, charge/discharge power, throughput, depleted/full
  states, and whether replenishment is achieved by the end of the window.
- Available/used renewables and curtailment, dispatchable generation and headroom.
- Common economic costs by component, with the lossy-DC resistance/flow proxy
  reported separately. Raw objectives with different terms are not AC gaps.
- Formulation-specific branch congestion, physical terminal losses where
  represented, voltage/reactive margins and binding device constraints.
  Distinguish the DC proxy from physical energy withdrawal.
- Solver status, independently audited primal feasibility, and computational
  effort. For SOCP retain rank/cycle diagnostics and recovered AC residuals
  separately; failed reconstruction does not negate its relaxation results.

Ask which resilience conclusions persist across formulations and which change
when network or reactive/voltage physics enters. Do not assume monotonic ENS,
cost or storage changes across the four models, or unique dispatch at equal
cost. Attribute differing outcomes to the actual model differences, not to a
single omitted mechanism without further controlled evidence. A feasible AC
point is a local feasible reference, not a certified global optimum. A matched
SOCP infeasibility claim needs adequate numerical infeasibility evidence; a
failed solve, timeout, or failed voltage recovery is not that evidence.

The comparison may identify prospective screening indicators near operating
regime transitions (shedding onset, storage depletion, generator/network limits).
Four selected events do not validate a rule for skipping AC, annual prevalence,
or dynamic/voltage stability. Record these as hypotheses for subsequent testing.

#### Implementation, execution and review sequence

1. Review E2; prepare the four-window selection and any proposed boundary legs
   from retained inputs/annual DC states, with no OPF solve required.
2. Obtain owner acceptance of exact windows and boundary policies. Freeze a
   dedicated protocol under `experiments/case118_tracy_2021/`, cross-linked here:
   settings/initialization per solver, tolerances, per-arm wall/iteration/RSS
   limits, total attempt budget, run order, and failure/retry rules. Baseline is
   16 primary solves; recovery or sensitivity attempts are not implicit.
3. Implement using public vectorized multistep builders, existing Tracy input
   preparation and independent audits. Reuse E2 matching/accounting checks where
   applicable; extend shared helpers rather than copy device or network models.
   Use existing resource supervision as appropriate, not the MPC rollout or
   speculative-recovery scheduler. Keep permanent test support under `tests/`
   and experiment-specific reporting in the experiment directory.
4. Review and commit the bounded runner/protocol before separately authorizing
   execution. Keep raw primals, logs and numerical diagnostics in its ignored
   `results/` subdirectory; retain exact source/input/settings identities and
   incomplete outcomes. No tuning or expanded run matrix without review.
5. Report the 16-arm disposition and resilience comparisons, with missing AC
   answers left explicitly unresolved. Bounded computational failures are
   reportable outcomes, not a reason for indefinite retries or suppression of
   useful other-model evidence. Stop for scientific review and the next-step
   decision; this study neither resumes the old rollout nor authorizes annual AC.

## 8. Explicit non-goals

- No resumption of the held Tracy rolling-horizon run, annual AC execution,
  penalty sweep, or claim that its study is complete. Gate E3 is a separate
  standalone matched-window study with its own pre-execution review.
- No generic hierarchy rewrite or SOCP-to-AC automatic control-action acceptance.
- No new storage, HVDC, shedding, or reactive capability physics.
- No DC loss proxy added to SOCP, hidden regularizer, or unmatched
  cross-formulation bound. E3 retains the existing lossy-DC proxy separately.
- No dense/chordal SDP, cycle strengthening, angle-bound strengthening, or
  automatic relaxation tightening in this first implementation.
- No assertion of radial or meshed exactness merely from topology or solver success.

M11 is ready to close only when these gates establish the standalone formulation,
its shared-device parity, vectorized construction, and honest diagnostic/result
contracts. Planning this milestone is separate from completing it.
