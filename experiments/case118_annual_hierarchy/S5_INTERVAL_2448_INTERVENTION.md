# S5 interval-2448 operator intervention

Status: implementation checkpoint. No intervention is authorized until a
separate reviewed contract binds the exact clean implementation commit and S5
source fingerprint.

## Scope

The stopped annual study reached global interval 2448 in shard 003. Its normal
five-minute primary attempt timed out. The restarted target-free solve then ran
for 4,474.63 seconds without producing an accepted source, after which the live
worker reached causal perturbation slot 6. That solve remained open for another
observed 2,944.50 seconds before the owner stopped the study through the
supported SIGTERM path. The checkpoint remained at interval 2448 and no
accepted action was lost. The open-slot duration is a retained wall observation
from the final recovery-phase publication to the terminal wave-supervision
record; it is not represented as a completed solver time.

A separate, read-only diagnostic reconstructed the identical initial SoC,
outer signpost, preceding controller, profiles, policy, solver configuration,
and complete IPOPT starts. Causal slots 6 and 7 timed out at 300 seconds. Slot
8—scale `0.01`, seed `17244823`—returned an accepted hard-target AC solution in
225.14 solver-path seconds (233.71 seconds end to end). Its full production
result, 9,120-coordinate IPOPT start, structural signature, causal source, and
unchanged M17 acceptance audit are retained at
`outputs/diagnostics/interval2448-YBoD5T/`.

The three sequential diagnostic processes consumed 847.66 seconds of process
wall time across 848.40 seconds of observed diagnostic latency. This diagnostic
ran concurrently with the live recovery, so neither quantity is added to the
live target-free and interrupted-slot latency. Final accounting retains live
recovery, diagnostic compute, diagnostic elapsed time, and their overlap as
separate fields.

The owner authorizes using that exact accepted slot-8 result once, at interval
2448, rather than repeating already observed work. This is an operator-assisted
continuation of the frozen study, not a change to the general recovery policy,
timeout, solver, target, acceptance gate, shard schedule, or concurrency.

## Guardrails

The intervention implementation must:

- bind the stopped progress, root outcome, wave supervision, shard checkpoints,
  live primary timeout, and interrupted recovery phase by literal SHA-256;
- copy and bind the complete diagnostic directory into the ignored execution
  tree before changing a checkpoint;
- accept only `ac-2448-08-perturbed_causal`, scale `0.01`, seed `17244823`,
  sourced from `ac-2447-00-primary_controlling`;
- independently validate the complete accepted result and unchanged residual
  gates against the frozen outer signpost;
- publish the immutable window archive before atomically advancing shard 003 by
  exactly one interval;
- retain the bypassed ordinary slots explicitly as operator-bypassed—not as
  fabricated solver failures, construction errors, or unobserved results;
- bind the old execution segment and this one-time action to an exact new clean
  source commit through a reviewed transition contract and numerical authority;
- reject any other interval, attempt, diagnostic hash, checkpoint, source
  context, or repeat application; and
- preserve the intervention as a distinct timing and lifecycle event in final
  independent analysis, without double-counting the overlapping diagnostic.

An identical retry may finish an archive-first/checkpoint-last transaction.
No descendant source commit or second operator intervention is implicitly
authorized. Once the checkpoint advances to 2449, ordinary S5 execution resumes
with the unchanged nine-slot controller. The immutable intervention record
remains valid only while the live shard checkpoint is either the stopped state,
the exact post-intervention state, or a fully validated forward extension whose
window registry contains that exact post-intervention prefix; it cannot be used
to roll back or reapply the action.
