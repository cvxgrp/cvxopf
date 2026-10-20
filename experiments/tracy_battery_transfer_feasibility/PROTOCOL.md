# Fixed-schedule AC feasibility diagnostic

Owner authorization: “Please proceed”, following the rejected-point postmortem
and recommendation of a separately approved feasibility-only AC diagnostic.
This is not a continuation of either economic transfer experiment.

## Question

Can the three retained battery schedules admit an independently audited AC
point when the economic objective is removed? Keep the complete T=24 window,
global hours [2439,2463), all physical inputs, device identities, operating
constraints, battery hourly power pins, initial energy and ideal dynamics.
The battery API continues to omit the redundant terminal equality; audit the
original endpoint. Focus reporting on hours 2452–2453, buses 70–73 and branch
8–9, but do not decompose, shorten, soften or otherwise change the model.

## Objective and representation

Reuse the economic transfer constructor, its normalized limits, exact fixed
boxes and existing cycling/shedding coordinate substitution. In the private
substituted graph only, replace the objective by Minimize(0). No economic
coefficient or load eligibility changes. Constraints remain the identical
substituted constraint objects. Unpriced objective-only cycling epigraphs are
not introduced: they are not part of the physical feasible set. The private
zero-objective graph disables a second cost-coordinate transformation and uses
ordinary DNLP smoothing; normalization and exact fixed-coordinate removal stay
enabled. This objective-ablation representation is explicitly distinct from
the economic solver graph. Reconstruct engineering-unit coordinates using the
retained substitution scales. No production source or solver registry patches.

Use unchanged E3 prepared IPOPT options, exact Hessian, bound_relax_factor=0,
max_iter=3000 and single-thread numerical environment. No convex solve, added
voltage/reactive preference, continuation, schedule repair, gate relaxation,
warm-start dual reuse or economic polishing.

## Trials and budgets

Donor order: SOCP, lossy DC, copper plate. For each donor, try the retained
matched AC center with the exact donor battery power/implied energy, then the
stock production flat center as fallback. Neither center is randomly perturbed;
no seed is applicable. They are retrospective diagnostic starts, not causal
controller starts. Stop each donor at its first independently audited,
supervised feasible point. Preserve fallback skips, failures and timeouts.

At most six new launches, 600 total process-wall seconds per worker, 16384 MiB
sampled process-tree RSS, 3600 cumulative supervised worker-seconds, one fresh
worker at a time. Preparation and audits consume the worker wall budget. The
old 21-launch ladder and its budgets remain finalized and untouched. Reuse its
resource supervisor, with required process/RSS access and thermal preflight.
Power source is informational only. Stop the whole diagnostic on audit failure,
infrastructure/monitoring failure, RSS ceiling, operator STOP or interruption.
No automatic retry/resume. An unfinished or unsupervised point is never accepted.

## Evidence and acceptance

Before execution freeze source/environment hashes, this diagnostic's uncommitted
script/protocol snapshot, all three historical run trees, retained donor
sources, physical input/schedule/start identities, scaled leaf layout, complete
prospective canonical x0, fixed-coordinate map and trial registry. Bind unchanged
committed production code at b11c4418fbd8f81294a1a26a1c053bb896f3b216; do not claim
the new uncommitted diagnostic is a clean committed experiment. No commit is
required for this isolated diagnostic using unchanged production source.

For completed workers retain native primal, constraint values and multipliers,
preparation, actual start evidence, log, resource journal and completion hashes.
Fresh non-solving replay must agree with start/layout/fixed map and native
constraint values/objective, invert cost-valued leaves, independently recompute
the unchanged Tracy engineering-unit network/device/accounting gates and power,
energy and original-endpoint gates. Report the original economic cost only as
accounting of the point, never as optimized or comparable objective quality.
Native status zero, all physical/pin gates and finalized successful supervision
are required. Do not mark the original economic problem optimal during replay.

A success establishes a feasible operating estimate for this fixed schedule,
not an economic optimum; it does not relabel historical outcomes. A rejection,
timeout or exhausted diagnostic is not an infeasibility certificate. Preserve
E3/E3+ qualifications and historical Stage D. No insertion or live-state update.
