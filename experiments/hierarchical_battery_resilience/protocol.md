# Hierarchical battery-resilience protocol

**Status:** S3 frozen experiment executed; policy review pending

This file defines the experimental contract shared by:

- the Phase-1 manual orchestration runner; and
- the Phase-2 public M17 API runner.

The protocol must not be revised merely to make the implemented controller
look better. Material revisions are appended to `experiment_log.md` with their
scientific or engineering rationale. The public implementation is compared
against this frozen reference, not against a retrospectively adjusted study.

## 1. Scientific question

Can a long-horizon convex `lossy_dc` layer communicate battery-energy intent
to short nonlinear AC windows using only device-aligned SoC boundary states,
while allowing each AC window to re-optimize generation, renewable dispatch,
voltage, reactive power, and branch flows?

Success means preserving the long-horizon energy-management intent to the
extent permitted by the modeled AC feasible set. It does not mean reproducing
the DC dispatch trajectory. The AC layer represents modeled steady-state
nonlinear network physics and a locally solved OPF; it is not a general
dynamic or global certificate of physical executability.

## 2. Reproducibility contract

The normative experiment must run from a clean checkout without untracked
workstation data. It uses checked-in, Tracy-derived prepared arrays for fixed
windows. The project owner produced the source dataset from public sources and
has confirmed authority to use and republish the derived inputs. The small
prepared arrays, redistribution provenance, and deterministic transformation
record are committed; the full raw five-year source need not be distributed.

The ignored Tracy source CSV used by `experiments/battery_terminal` is not, by
itself, a reproducible M17 input. It may support a secondary study after the
normative experiment is independently reproducible.

The frozen scenario manifest records:

- scenario name and version;
- exact inclusive timestamps or integer interval indices;
- cadence `delta` and horizon `H`;
- every transformation and random seed;
- case and formulation options;
- complete generator, storage, nondispatchable, HVDC, and load definitions;
- stable device IDs and time-series column order;
- realized initial SoC `e_0` for every storage ID;
- the global outer terminal target at boundary `H`, its equality,
  reserve-floor, or soft policy, and any soft weight for every storage ID;
- confirmation that every shortened replan retains that same configured
  terminal obligation at the same global boundary `H`;
- confirmation that every load is nonsheddable;
- SHA-256 hashes of the final active-load, reactive-load, and
  nondispatchable-availability arrays; and
- package versions used to prepare the artifacts. Each later accepted reference
  run separately records its actual package and solver versions.

### 2.1 Frozen scenario

The normative scenario is `tracy_high_96h_v1`, the existing sustained-energy-
deficit window from `2021-12-18 00:00:00-08:00` through
`2021-12-21 23:00:00-08:00`. It contains 96 one-hour intervals. The checked-in
active-load, reactive-load, and nondispatchable arrays live in
`prepared_scenario/`; `scenario.py` verifies their shapes, column order,
timestamps, cadence, and both file and canonical numeric-array hashes.

The source-to-case factor is `315 / 1138.7624473656565`; all load and resource
stress multipliers are one, load shift and spatial noise are zero, and random
seed zero is recorded even though the zero-noise allocation consumes no random
variation. Loads retain the case9 proportions at buses 5, 7, and 9. Utility
solar is placed at buses 1 and 2, wind at buses 2 and 3, and distributed solar
at buses 5, 7, and 9. Renewable inverter ratings retain the prior experiment's
joint sizing across the low, moderate, and high windows; selecting one
normative trajectory therefore does not alter the reviewed physical fleet.

The manifest's numeric-array hashes are historical preparation-provenance
fields bound into the S3/S3b records. Redistribution integrity is enforced by
the exact CSV file hashes. Clean checkouts parse those decimal files with
pandas' explicit `float_precision="round_trip"` contract and validate a
separate set of round-trip float64 hashes in `scenario.py`; this avoids the
few-ULP platform/version drift of pandas' default parser without rewriting the
historical manifest.

The ignored raw composite is optional provenance input. A clean checkout uses
only the prepared arrays. Maintainers possessing the raw file with SHA-256
`45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f`
can reproduce the artifacts with:

```bash
uv run python -m experiments.hierarchical_battery_resilience.prepare_scenario \
  path/to/9q9wtp_gen_and_load.csv
```

`load_frozen_scenario()` is the sole scenario-materialization boundary. It
verifies the current case9 `baseMVA`, bus array, and branch array against the
manifest and returns the verified case, `OPFOptions`, typed generator, load,
nondispatchable, storage, and HVDC fleets, aligned frames, and typed horizon,
policy, status, and tolerance configuration. S2 must consume that object; it
must not independently translate descriptive manifest fields into constructor
arguments.

## 3. Indexed state and window contract

Let `H` be the number of experiment intervals and `W` the nominal AC window
length.

- `e_k` is the realized, device-aligned SoC vector before interval `k`.
- Controller iteration `k` begins at `e_k`.
- The effective inner length is `W_k = min(W, H - k)`.
- The AC window covers intervals `k, ..., k + W_k - 1`.
- A DC plan created at iteration `j` uses local boundary index `ell`, denoted
  `e_dc[j][ell]`, for the global boundary `j + ell`.
- Under `frozen`, the inherited signpost is `e_dc[0][k + W_k]`.
- Under `replan_every_step`, the inherited signpost is `e_dc[k][W_k]`.
- Both policy-specific expressions refer to global boundary `k + W_k`.
- Current `extract_results()` storage trajectories contain post-step states
  only. Conceptual local boundary 0 is `storage_initial_soc`; boundary
  `ell >= 1` is `results["soc"][ell - 1]`.
- The AC solve predicts `W_k` actions, but only `b_ac[k]`, its first action,
  is executed.
- For the current ideal-storage model,

$$
e_{k+1} = e_k - \Delta t\, b^{AC}_k.
$$

The sign convention is the existing package convention: positive battery
power discharges storage and therefore lowers SoC. The realized state used by
the next iteration is reconstructed from the accepted first action and also
checked against the first post-step SoC returned by the AC build.

Every cross-layer state vector is keyed and aligned by stable storage
`device_id`. Array position is an implementation detail, not the handoff
contract. P1 adds this identity to `StorageUnitIdeal` and publishes both the
aligned IDs and an explicitness mask through builds and results.
Every storage unit participating in M17 requires an explicit, unique, nonempty
ID, including a one-battery experiment. The first scientific scenario may
contain one storage device, but S6 must verify at least two devices in
deliberately different input orders.

Legacy nonhierarchical builds may publish deterministic build-local labels such
as `storage_0` when an ID is omitted. Such a label is positional and stable
only while fleet ordering is unchanged; it is not claimed as cross-build
device identity and is rejected at the M17 boundary.

### 3.1 Hand-checkable `T=3, W=2` example

For intervals `0, 1, 2`, the frozen plan created at iteration 0 has local
boundary states `e_dc[0][0], ..., e_dc[0][3]`. A replanned outer solve at
iteration `k` instead has local boundaries `0, ..., H-k`. In the current
result schema, the corresponding post-step SoC array indices are one less than
each positive boundary index.

| `k` | AC intervals | `W_k` | Global endpoint | Frozen signpost | Replanned signpost | Executed action |
|---:|---|---:|---:|---|---|---|
| 0 | `0, 1` | 2 | 2 | `e_dc[0][2]` | `e_dc[0][2]` | first action for interval 0 |
| 1 | `1, 2` | 2 | 3 | `e_dc[0][3]` | `e_dc[1][2]` | first action for interval 1 |
| 2 | `2` | 1 | 3 | `e_dc[0][3]` | `e_dc[2][1]` | first action for interval 2 |

The final window is truncated; it does not invent a fourth interval. Notice
that iterations 1 and 2 share the same global endpoint but have different
realized starts and window lengths.

### 3.2 `W=1` boundary case

At every iteration `k`, the AC window contains interval `k` only and targets
`e_dc[0][k + 1]` under `frozen` or `e_dc[k][1]` under
`replan_every_step`. Both refer to global boundary `k + 1`. The sole predicted
action is executed. This case is retained as an indexing regression because an
off-by-one signpost cannot hide inside a longer window.

## 4. Three distinct studies

### 4.1 Endpoint realization

Solve one long DC plan. The normative study uses two reviewed, equal-length
half-open subsections:

- `crosses_saturation_boundary_32_50`: intervals `[32, 50)`, crossing a
  storage saturation boundary in the inherited DC trajectory; and
- `within_regime_60_78`: intervals `[60, 78)`, remaining within one of the
  trajectory's decoupled operating regimes.

Each 18-hour AC window receives the DC states at both boundaries. The paired
design tests whether endpoint-conditioned AC can realize the same energy
transfer both across and within the storage trajectory's geometric regimes,
while independently choosing its network dispatch. This is a comparison of
two individual handoffs, not a trajectory replay. The cases were selected from
the prior battery-terminal analysis before the M17 results were observed.

### 4.2 Open-loop sequential execution

Solve the outer DC problem once. Freeze its original boundary-state sequence.
Successive AC windows begin from realized state `e_k`, target the corresponding
fixed DC signpost, execute only their first actions, and never re-solve the
outer layer. This policy is named `frozen`.

### 4.3 Closed-loop replanning

At every iteration, rebuild the remaining-horizon DC problem from realized
state `e_k` using the same deterministic perfect-forecast inputs. The inner AC
window takes its endpoint from that new plan. This policy is named
`replan_every_step`.

Only `frozen` and `replan_every_step` are in the initial protocol. A periodic
`replan_every_n_steps` policy is deferred until a concrete experiment requires
it. Keeping forecasts identical isolates realized-state feedback and AC
correction from forecast revision.

## 5. Terminal policies

The initial comparison includes two predeclared policies:

- `hard_equality`: the AC window requires its final SoC to equal the aligned DC
  signpost; and
- `quadratic_soft`: the same signed endpoint deviation enters once as the
  existing two-sided quadratic terminal cost.

The quadratic weight is fixed at `0.05` objective units/MWh², the previously
approved battery-terminal value whose marginal penalty is 25 objective
units/MWh at a 250 MWh deviation. A nonzero soft deviation is a
successful solve with inter-layer disagreement, not a failed solve.

The baseline has no automatic hard-to-soft retry. Hard and soft are separate
scientific runs. A future `hard_then_soft` policy may be added only as an
explicit policy that retains and links both attempts.

No policy introduces anonymous balance slack, silently changes a terminal
constraint, or treats a relaxed solve as the original solve.

## 6. Outer-plan policies

The initial experiment compares exactly:

- `frozen`; and
- `replan_every_step`.

For `frozen`, the initial long-horizon plan and all original signposts remain
available even after realized AC actions diverge. For `replan_every_step`,
each outer record retains its realized initial state, remaining input slice,
new signpost trajectory, solve status, and objective. Every outer record stores
its creation iteration, each local boundary index, and the corresponding
global boundary index.

The outer terminal obligation is global, not local to a controller iteration.
An outer plan created at iteration `k` spans the remaining intervals through
the original boundary `H` and applies the same configured per-device terminal
target and policy there. Replanning may change the economically optimal path
to that boundary but may not move the boundary, replace the target, or weaken
the policy. The normative baseline uses the energy-neutral hard equality
`e_H = e_0` for every storage device. The scenario manifest freezes the actual
initial value; 50% of capacity is the provisional standard configuration.

## 7. Solve acceptance and failure behavior

No action is executed unless the solve satisfies the M17 baseline
accepted-primal rule and all required first-action and first-post-step SoC
values are finite.
For the baseline, a controlling attempt is executable only when:

- raw status is `optimal` or `optimal_inaccurate`;
- every field required to execute and audit the first interval is present and
  finite; and
- storage recurrence, power balance, voltage, both-terminal thermal, and
  terminal-policy residuals satisfy their frozen tolerances.

`user_limit`, solver exceptions, and incomplete or nonfinite primals are
diagnostic only and are never executed. `optimal_inaccurate` remains eligible
only after the same explicit residual checks; its raw status is retained.

The minimum required fields depend on solve role:

| Solve role | Required fields |
|---|---|
| Outer `lossy_dc` | `b`, `soc`, `Pg`, `p_net`, `p_flows`, fixed load inputs and served load, and every applicable component output and DC diagnostic needed by the frozen residual checks |
| Inner AC | `b`, `soc`, `b_q`, `Pg`, `Qg`, `Vm`, `Va_deg`, `p_net`, `q_net`, all four signed branch-terminal power arrays and both apparent-power arrays, fixed load inputs and served load, and every applicable component output needed for execution and audit |
| Both | finite first action, finite first post-step SoC, matching explicit storage IDs, finite objective and raw status metadata, and every terminal deviation or terminal-cost quantity required by the selected policy |

Conditional devices extend the required set. Nondispatchable generation adds
its active output, curtailment, and AC reactive output; HVDC adds both terminal
injections and loss; sheddable load, if enabled in a later study, adds served
and shed active/reactive power, fractions, per-load and aggregate ENS, and
shedding cost. A result is not executable merely because its battery fields
are present while a required network or participating-device field is missing.

Each individual solve attempt has exactly one outcome:

1. `accepted`;
2. `accepted_soft`;
3. `solver_certified_infeasible`;
4. `solver_failure`; or
5. `unusable_primal`.

The window record separately assigns one diagnosis after considering the
predeclared attempts and diagnostics:

1. `hard_target_met`;
2. `soft_target_met`;
3. `soft_target_deviated`;
4. `target_conditioned_failure`;
5. `target_independent_infeasibility`; or
6. `unresolved_failure`.

An `accepted_soft` attempt can have exactly zero terminal deviation. Signed
and absolute deviation remain numerical fields rather than being encoded in
the attempt outcome.

The manual runner records the raw solver status and exception separately from
both fields. IPOPT failure or `user_limit` is not called a proof of
infeasibility. A diagnostic solve can support a window-level
`target_conditioned_failure` diagnosis but does not retroactively change the
failed attempt's outcome or certify why the first local solve failed. Because
the baseline has no automatic fallback, a failed controlling attempt
terminates the trajectory explicitly at iteration `k` without advancing
`e_k`; nonexecuted diagnostic attempts remain linked to that window.

## 8. Baseline physical and optimization scope

The initial experiment baseline uses:

- case9 network topology;
- a deterministic multi-day load and nondispatchable trajectory;
- long-horizon `lossy_dc` planning with its documented `r*p^2` objective
  penalty, not physical loss withdrawal from nodal balance;
- short multistep AC windows;
- ideal storage with the current SoC dynamics;
- fixed nonsheddable first-class loads;
- zero-cost renewable curtailment as a metric of interest;
- AC `rateA` enforcement at both branch terminals;
- identical perfect forecasts for frozen and replanned comparisons; and
- no contingencies, topology changes, or corrective load shedding.

The horizon is `H=96` and the nominal AC window is `W=5`, with the final four
windows truncated according to `W_k = min(5, 96-k)`. Five steps are used
because that length already passed the prior synthesized-network AC smoke
test recorded in the battery-terminal
[experiment log](../battery_terminal/experiment_log.md#2026-07-27--five-step-ac-network-smoke-test).
This is a predeclared computational choice, not a value tuned against M17
outcomes. Inner solves use the project-default flat initialization.

The frozen acceptance tolerances are: `1e-4` MWh for SoC recurrence, `1e-3`
MWh for hard terminal equality, `1e-6` objective units for soft-terminal cost
reconstruction, `1e-6` p.u. for AC active/reactive balance and DC nodal
balance, `1e-4` MW for DC injection-reporting consistency, `1e-6` p.u. for
voltage bounds, `1e-4` MVA for branch limits, and `1e-7` for normalized
squared branch-limit residuals. Both the dimensional and normalized branch
checks must pass.

### 8.1 Frozen residual definitions

Every maximum below is taken over all predicted steps and the indicated
device, bus, or branch indices. Equality residuals use absolute tolerance
only; no relative tolerance is added to a quantity whose reference value is
zero.

For storage ID `s`, the ideal-state residual is

$$
r^{soc}_{0,s} = e_{0,s} - e^{initial}_s
                 + \Delta t\,b_{0,s},
$$

and, for subsequent predicted steps,

$$
r^{soc}_{t,s} = e_{t,s} - e_{t-1,s}
                 + \Delta t\,b_{t,s}.
$$

The reported SoC-recurrence metric is `max(abs(r_soc))` over steps and storage
IDs.

For each bus, independently reconstruct total device injection in MW/MVAr from
reported generator, storage, nondispatchable, HVDC, and served-load values.
The reconstruction preserves the package's injection sign convention:
generation and discharging storage are positive; served load is negative.

For AC, both the independently reconstructed device injection and reported
`p_net`/`q_net` are initially in engineering units. Divide **both sides** by
`baseMVA` before comparing them. Denoting these per-unit quantities by
`p_device_pu` and `p_net_pu`, and similarly for reactive power, the AC balance
metrics are

$$
\max_{t,i}\left|p^{device}_{t,i}-p^{net}_{t,i}\right|,
\qquad
\max_{t,i}\left|q^{device}_{t,i}-q^{net}_{t,i}\right|.
$$

Because AC `p_net` and `q_net` are network-side injections constrained equal to
component injections, these independently extracted comparisons audit the AC
nodal equalities. They use the AC per-unit balance tolerances.

For lossy DC, reported `p_net` is itself the component-injection expression.
The engineering-unit reporting-consistency diagnostic is therefore kept
separate:

$$
\max_{t,i}\left|p^{device,MW}_{t,i}-p^{net,MW}_{t,i}\right|.
$$

This check uses the DC injection-reporting tolerance and is not evidence of
nodal balance. To audit the DC balance independently, reconstruct the
`(n_b,n_l)` incidence matrix from the frozen case in original branch-row
order, with `A[i,e] = -1` at branch `e`'s from-bus and `+1` at its to-bus.
Using reported MW flows and injections, the physical/model balance metric is

$$
\max_{t,i}\left|
\frac{(A p^{flow}_t)_i+p^{net}_{t,i}}{\mathrm{baseMVA}}
\right|.
$$

This check uses the DC per-unit nodal-balance tolerance. Reporting consistency
and nodal balance are retained as distinct diagnostics; neither may silently
substitute for the other.

For bus voltage bounds from the frozen case, the violation is

$$
\max_{t,i}\max\left(
V_{t,i}-V_i^{max},\;V_i^{min}-V_{t,i},\;0
\right).
$$

For every in-service branch with a finite positive enforced `rateA`, and for
both terminals, let `S` be `hypot(P,Q)` and `r_A` the MVA rating. The
dimensional and normalized squared violations are respectively

$$
\max\max(S-r_A,0),
$$

and

$$
\max\max\left(\frac{S^2-r_A^2}{r_A^2},0\right).
$$

Branches without a positive enforced rating are excluded rather than assigned
an artificial denominator. The maxima preserve both-terminal and original
branch-row identity in the retained diagnostics.

For a hard terminal policy, the residual is the maximum absolute endpoint
difference after aligning target and result by explicit storage ID:

$$
\max_s\left|e^{AC}_{end,s}-e^{target}_s\right|.
$$

For the quadratic-soft policy, endpoint deviation has no acceptance threshold.
Every aligned deviation must be finite, and the reported terminal cost must be
finite and agree, within the frozen absolute cost tolerance, with

$$
w\sum_s\left(e^{AC}_{end,s}-e^{target}_s\right)^2.
$$

The baseline must be feasible without M19 shedding. Later resilience studies
may enable shedding, but must separately report planned demand, AC input and
served demand, active and reactive shedding, per-load and aggregate ENS, and
any no-shedding counterfactual.

## 9. Recorded data

Retain, for every outer plan and AC attempt:

- global iteration, interval slice, `W_k`, outer-plan creation iteration,
  local and global boundary indices, policy names, and storage-ID order;
- initial realized SoC, complete DC signpost sequence, selected terminal
  signpost, complete predicted AC SoC, and executed first action;
- predicted and realized state-transition residuals;
- raw solver status, attempt outcome, window diagnosis, accepted-primal flag,
  solver exception, objective, iterations, setup time, and solve time when
  available;
- generator active/reactive output, battery active/reactive power,
  nondispatchable output and curtailment, voltage magnitude/angle, and branch
  terminal active/reactive/apparent flows;
- both-terminal thermal utilization and normalized constraint residuals;
- DC flow-penalty values separately from independently reconstructed AC active
  losses;
- signed and absolute terminal deviation; and
- fallback linkage, if fallback is approved.

Store each complete outer plan exactly once under a stable `outer_plan_id`.
Each AC attempt references that ID plus its selected local and global boundary
indices; it does not copy the outer signpost trajectory into every window
record. An unaccepted endpoint-study outer solve is returned in the endpoint
study record with zero AC realizations and an explicit termination reason; it
is never hidden by raising before the audit record reaches the caller.

### 9.1 Realized accounting

Realized trajectory totals use only the executed first interval from each
accepted controlling AC window. Generation cost, storage cycling cost,
renewable curtailment, active loss, and later ENS are integrated exactly once
by `delta` over that executed global interval sequence.

Complete predicted-window objectives and component costs remain diagnostics
of their individual solves. AC terminal penalties are planning-policy
diagnostics, not realized operating costs. Outer objectives are retained per
`outer_plan_id` and are never summed across replans.

Frozen and replanned comparisons use the same completed global interval set.
A terminated trajectory reports its partial-horizon totals together with the
number and fraction of global intervals executed; it is not compared as
though it completed `H`.

The primary realized AC active-loss reconstruction for an executed interval is

$$
P^{loss} = \sum_{\ell}
\left(P_{\ell,\mathrm{from}} + P_{\ell,\mathrm{to}}\right).
$$

System active-injection balance is retained as an independent cross-check.
Trajectory summaries include realized generation cost, cycling cost,
renewable curtailment, and active loss; maximum voltage violation; maximum
thermal residual; cumulative absolute signpost deviation; runtime; termination
iteration; completed-interval coverage; and completion status.

Maximum voltage and thermal violations are taken over the executed first
interval of every accepted controlling AC window, consistent with the realized
accounting rule. Both dimensional MVA and normalized squared thermal maxima
are retained. Cumulative absolute signpost deviation sums the absolute,
device-aligned terminal deviations of accepted controlling windows; diagnostic
attempts do not contribute. Runtime sums every retained outer-plan and AC-solve
wall time, including failed controlling and diagnostic attempts, so it records
the actual computational work performed before completion or termination.

## 10. Acceptance tolerances

Numerical tolerances are fixed after S0 characterizes current solver behavior
and before S2 results are interpreted. Separate tolerances are required for:

- SoC dynamics and state handoff;
- hard terminal equality;
- power balance;
- voltage bounds;
- normalized branch-limit residuals;
- manual-versus-public trajectory equivalence; and
- locally solved AC objective/dispatch comparisons.

Matched-state equation tests precede comparisons between independently solved
nonconvex AC optima. Solver success alone is not evidence that a thermal limit
or terminal signpost was modeled correctly.

## 11. Frozen artifacts

S1 produces:

- the approved scenario generator or acquisition path;
- a machine-readable scenario manifest;
- hashes of final prepared arrays;
- this protocol with no unresolved scientific policy choices;
- a short hand-verification table for `T=3, W=2` and `W=1`; and
- an appended experiment-log entry recording the freeze decision.

## 12. S1 freeze decision

The exact horizon, nominal window, terminal policies and weight, solver
initialization, physical fleet, and acceptance tolerances above are frozen for
the manual reference study. Any later change is a new protocol version and
must be appended to `experiment_log.md`; it must not overwrite the S1
artifacts or be justified from favorable controller results.
