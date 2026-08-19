# Hierarchical battery-resilience experiment log

No experiment runs have been performed.

Protocol decisions and results should be appended chronologically. Earlier
entries should remain as time-local records when later decisions supersede
them.

## 2026-08-19 — Pre-S0 protocol review

No numerical experiment was run. The initial stub and M17 plan were reviewed
before manual-runner implementation. The draft protocol now distinguishes one
endpoint-realization solve from frozen-plan sequential execution and
closed-loop replanning; defines the indexed realized-state recurrence and
final-window truncation; requires a clean-checkout reproducibility path;
separates hard-target, target-conditioned, target-independent, and solver
failure outcomes; enables M4 `rateA` limits; and explicitly disables M19
shedding in the baseline.

The review also exposed a prerequisite not stated in the original plan:
`StorageUnitIdeal` has no stable device ID. Positional state transfer is not
acceptable for the public hierarchy. The current recommendation is a
backward-compatible optional storage ID with deterministic legacy IDs, while
requiring explicit unique IDs for multi-storage hierarchical control. This
multi-storage-only recommendation is superseded by the unconditional M17 rule
recorded below.

Open decisions before S1 freeze are the normative data path, whether hard and
soft policies remain separate or permit an explicit retained retry, the exact
outer-policy set, the storage-identity compatibility rule, and the numerical
scenario/horizon/tolerance values selected after S0 characterization.

### Follow-up decisions

The baseline will run hard and quadratic-soft terminal policies separately;
there is no automatic hard-to-soft retry. Storage identity is elevated from a
protocol detail to prerequisite device-API work immediately after S0. The
public storage dataclass will preserve positional constructor compatibility
with an appended optional ID, but every M17 participant must supply an
explicit, unique, nonempty ID. Generated labels remain build-local positional
labels and carry no cross-build identity claim.

The Tracy-derived scenario is preferred for scientific interest if its small
prepared arrays may be committed with provenance. Redistribution authority is
still to be confirmed. The exact meaning and scope of the proposed `frozen`
and `replan_every_step` outer policies also remains under discussion.

### Data decision

The project owner confirmed that the Tracy dataset was assembled from public
sources and may be used and republished at their discretion. M17 will therefore
use checked-in prepared arrays from fixed Tracy-derived windows as its
normative reproducible scenario. The committed artifact will include exact
timestamps, transformation provenance, and hashes. Reproduction will not
require the ignored raw five-year source file.

### Outer-policy decision

The initial experiment will test both `frozen` and `replan_every_step`.
`frozen` retains the original DC signposts while feeding each AC window the
realized state and serves as the less computationally intensive open-loop
benchmark. `replan_every_step` rebuilds the remaining-horizon DC plan from
every realized AC state and is the more correct MPC-like feedback policy.
Periodic replanning is not part of the baseline.

### Outer-boundary and accounting review

The protocol review identified that every shortened outer replan must retain a
terminal obligation at the original global boundary `H`; otherwise the outer
problem can rationally consume its remaining stored energy as the horizon
shrinks. The recommended normative choice is a hard, energy-neutral equality
`e_H = e_0` for every storage ID. This recommendation awaits approval.

The review also separated predicted-window diagnostics from realized
trajectory accounting. Only the executed first interval of each accepted AC
window contributes to realized cost, curtailment, loss, cycling, and later ENS
totals. Terminal penalties are planning diagnostics, outer objectives are
never summed across replans, and partial runs report completed-interval
coverage. Complete outer plans will be stored once and referenced by stable ID
rather than copied into every AC-attempt record.

### Outer terminal-policy decision

The standard outer policy is approved as a hard energy-neutral equality:
every storage device ends the global horizon at its realized initial level,
`e_H = e_0`. Every shortened replan retains that same absolute target at the
original boundary `H`. The prepared scenario will freeze the initial value;
50% of capacity is the provisional and familiar practical choice.
