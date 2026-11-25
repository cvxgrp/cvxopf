# Large-case AC spatial vectorization

**Status:** first slice implemented and reviewed (`6ab39dac7`). The PR includes
build-only profiling and matched AC solves. Cases, settings, and limits for
new benchmark runs still need approval before launch.

## Starting evidence

The starting point is a reported **build-only smoke test on a 400+ bus
case**. The handoff says no solves were run. The original command, case
identifier and input, execution revision, effective build options, and raw
measurements were not located in the checkout or originating discussion. In
particular, it is not known whether the test stopped after constructing the
CVXPY problem or also timed DNLP compilation. Recover the original artifact
before calling any comparison a replay. If it cannot be recovered, choose a
separately identified, authorized large fixture and call that a new baseline.

A **different** record is the November 18 pasted log for a single-step,
243-bus, 451-branch AC OPF. It reports recommended preparation
(`normalize_device_limits`, `exact_fixed_boxes`, and `cost_coordinates`),
CVXPY subexpression warnings numbered 5–12, and a native IPOPT return. It
does not identify the exact case input, source revision, or raw constraint
list. The warning is emitted while constructing a CVXPY `Problem` and is
about expression-tree size. Neither the warning nor the log's native and
end-to-end times is an isolated compilation measurement. Do not use this log
to fill in the 400+ bus smoke-test record.

The [Case118 four-way study](../experiments/case118_spacetime_pq_replay/FOUR_WAY_STUDY_REPORT.md)
tested spatial batching of **Ybus P/Q equations**, alongside time
vectorization. It did not batch physical branch terminals. Its results do not
predict the benefit of this change.

## What the baseline builder did

Source inspection at `841d764e2` on `vectorize`. The semantic groups below are
leads from the 243-bus report, not a verified mapping of all eight warning
indices. Resolve every index against the exact `Problem.constraints` list
that emitted it, including any preparation rebuild. Do not assume two named
groups correspond to two constraint objects.

| Reported group | What the current AC path builds |
| --- | --- |
| Four terminal flow equations: from/to P/Q | [`_make_branch_terminal_flow`](../src/cvxopf/ac_problem.py) builds one expression per physical branch and end, then stacks them into four lifted equalities. Time-vectorized assembly batches time *within* each branch; it still loops over branches. Each end's P and Q expressions reuse its sine, cosine, and voltage product, so four warning indices need not imply four independent causes. |
| P/Q nodal balances | [`_make_step_constraints` and `_build_ac_vectorized`](../src/cvxopf/ac_problem.py) already batch each balance over buses. Under the default `vectorize_pq=True`, the Ybus-entry equations are also batched. Distinguish the P/Q row-sum equations from the device-injection balances. The latter sum contributions from the [shared component assembler](../src/cvxopf/_component_assembly.py); inspect their actual children before attempting a rewrite. |
| From/to thermal limits | In the current default single-step builder, zero-based constraints **#11 and #12** are the from-end and to-end limits of the **first in-service branch with a positive finite rating**, when one exists. [`_make_branch_limit_constraints`](../src/cvxopf/ac_problem.py) appends one pair per rated branch in row order. Each inequality reads that terminal's lifted P/Q variables; neither spans the network. |

That numbering is a source-code observation, not an attribution of the
historical warnings. It gives no reason to batch thermal limits in the first
slice: the four terminal definitions stack expressions across branches, while
each limit acts on one terminal. Revisit the limits only if profiling shows
that building or reducing the collection of per-branch limits is costly.

CVXPY's recursive count follows expression arguments; a reused child can
contribute to multiple counts. For the exact fixture, record each warning's
constraint role, shape, and node count, and trace shared child identities
within the problem. Report both recursive occurrences and unique nodes for
the affected groups. Fewer warning messages alone are not evidence of less
compilation work.

## Proposed order

1. **Batch the terminal definitions first.** In the shared AC terminal
   builder, gather voltages and angles by physical branch row. Form one
   elementwise P/Q expression for each orientation, reusing angle differences
   and voltage products where it stays clear. Use the authoritative
   `BranchAdmittance` coefficients; do not derive terminal flows from
   aggregated Ybus entries. Preserve the four lifted variables, names,
   shapes, units, and zero-valued offline rows. Keep single-step `(nl,)`,
   time-last `(nl, T)`, and stepwise publication unchanged. Avoid a second
   production implementation: use the base revision and independent numeric
   checks as references. Stop at this checkpoint and measure it separately.
2. **Batch the two terminal-limit groups only if the baseline or first
   checkpoint justifies it.** Gather the existing constrained rows and
   apply the same elementwise normalized P/Q limit for each end; one
   constraint object per end need not change the number of scalar limits.
   Preserve row-specific normalization errors and option behavior. Return
   no limits for an empty mask. Keep these measurements separate from the
   terminal-definition change.
3. **Leave nodal balances alone unless a specific child is shown to be
   expensive.** Keep the one P balance and one Q balance with component-owned
   injections. Dense `Ybus`, the off-pattern index list, the `Rp` row-sum map,
   and device incidence arrays are separate memory leads. If profiling shows
   they dominate, propose a separate change with an inventory of metadata and
   downstream consumers. Do not conflate sparse storage or CVXPY's automatic
   sparse dispatch with expression batching.

No new public flag; do not broaden the meaning of `vectorize_pq`. Preserve
the AC formulation, device and cost assembly, starts, numerical preparation,
and solver/default policies. Do not alter other formulations, hierarchy
policy, or retained study inputs and evidence.

## Physical equivalence without a solve

- Assign the **same physical state** to both graphs, including non-flat
  angles, unequal voltage magnitudes, and distinct values across time. Match
  rows by branch identity and original order, and variables by physical
  coordinate rather than by canonical array position. Compare all four
  terminal expressions against the old implementation and an independent
  complex-voltage calculation: compute each end's terminal current from its
  two admittance coefficients and evaluate $S = V\overline{I}$ at that end.
  Test parallel branches, tap ratios and phase shifts, reversed row
  orientation with its corresponding coefficients, charging, and offline
  rows. Vary the assigned lifted flows too, so equality residuals are
  compared away from zero as well as at the matching flows.
- Check the exact mask (in service and positive finite `rateA` when limits
  are enabled); preserve the existing validation of invalid ratings. Compare
  signed limit slack for the same lifted values and both ends, with cases on
  each side of the limit and independently binding from/to ends. Cover zero
  ratings, offline rated branches, disabled limits, an empty mask, and
  adversarial rating normalization. Compare values in per-unit and verify
  reported MW/MVAr/MVA and result order; use tight numerical tolerances
  declared before comparison, exact masks and shapes, and exact offline zeros.
- Use small fixtures to cover single-step and multistep (stepwise and
  vectorized, including `T=1`), sparse/dense P/Q storage, and the existing
  Ybus batching override. Check objective and unaffected nodal/device
  residuals on matched states, lift-variable schemas, and start mapping.
  Because batched indexing changes the differentiated graph, compare
  Jacobian actions at matched states against finite differences on a small
  fixture, including repeated bus indices and multiple time steps. Where
  the compile-only interface exposes Hessian actions, compare those too
  with aligned multipliers and coordinates. Equal scalar counts alone do
  not establish equivalent derivatives or identical canonical ordering.

## Matched build and compilation checks

After approval, compare the base and each candidate checkpoint one change at
a time: a small control, then the recovered large smoke fixture or an agreed
replacement. Use the 243-bus fixture only if its exact input and options are
available. Freeze case bytes/hash, revision, public entry point, horizon,
options and starts, versions/native libraries, thread environment, and
backend, including the effective automatic sparse-dispatch policy. Run a
small number of cold repeats in fresh serial processes, alternating order.
Report individual measurements and spread; a change within repeat variation
is not a demonstrated improvement.

Report three separate wall times:

1. **Public model construction:** from entry to the public builder until it
   returns the completed `OPFBuild`.
2. **DNLP reductions:** from application of the public AC path's effective
   reduction chain until it returns the reduced solver data. If this is the
   only compilation phase timed, report **reduction time**, not full compilation time.
3. **Derivative setup:** time construction of the derivative oracle, including
   Jacobian and, when configured, Hessian setup. The IPOPT adapter can defer
   this work until after the reductions. Stop before the native IPOPT solve;
   if setup cannot be isolated without solving, report it as unmeasured.

Record warning attribution, graph counts, modeled scalar sizes, canonical dimensions and stored
derivative entries where available, and peak RSS with its measurement scope.
Retain failures or memory-limit outcomes and report matched-result and timing
evidence separately. Compare counts by role; object counts can shrink while
scalar constraints are unchanged, and canonical ordering can differ.

## Matched AC solves

Run baseline and candidate through `build.solve()` to see whether the setup
gains carry through to an accepted AC solution. Use an agreed small control
and larger fixture, with single-step and bounded multistep cases as the
common resource limits allow. Use the same case data, starting physical values,
effective solver and preparation settings, threads, and resource limits in
both arms. Compare the original builder with the PR candidate; if another
slice is added, use the preceding checkpoint to isolate its effect.

Measure end-to-end wall time from public model construction through result
extraction, and report native IPOPT time and iterations separately. Recompute
the objective and check feasibility in physical units using criteria agreed
before running. Report all outcomes, including timeouts, failures, and
solutions that fail the audit. Report speedup ratios only for pairs where
both arms pass. If accepted local solutions differ materially in cost or
dispatch, report that difference rather than treating them as interchangeable.

## Decisions before benchmarking

- Can we recover the original smoke artifact and use its case locally? If
  not, choose the replacement and agree what compact evidence may be retained.
- Agree on the solve fixtures and horizons, starting states, solver settings,
  time and memory limits, and acceptance criteria before launch.
- Review the build-only measurements before expanding into thermal limits or
  nodal balances. Propose sparse network maps separately if the memory
  measurements justify it.
