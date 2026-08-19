# Hierarchical battery-resilience protocol

**Status:** draft for review; scenario-specific numerical choices remain open
before S1 is frozen

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
- package and solver versions used for the accepted reference run.

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
contract. `StorageUnitIdeal` does not yet expose `device_id`; S0 must
characterize that gap, and the storage-identity prerequisite slice immediately
after S0 must implement it before the scenario or manual runner is frozen.
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

Solve one long DC plan. Select one aligned subsection and give one AC window
the DC states at both boundaries. This tests whether the endpoint-conditioned
AC problem can realize the same energy transfer while independently choosing
its network dispatch. It is one handoff, not a trajectory replay.

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

The quadratic weight and its engineering interpretation must be fixed in the
scenario manifest before accepted runs. A nonzero soft deviation is a
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
record.

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

## 12. Decisions required before S1 freeze

1. After the prepared scenario is selected: exact horizon, nominal window
   length, AC terminal soft weight, solver initialization, and acceptance
   tolerances.
