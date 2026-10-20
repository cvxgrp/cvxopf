# Four operating conditions with optional shedding

Declared 2026-10-06 before execution. Run the original Tracy E3 energy-neutral
24-hour SOCP windows with both stock CLARABEL QDLDL and Faer, serially in that
order per window. These are eight solves, not the earlier four-arm comparison.

| Condition | E3 arm | Interval |
| --- | ---: | --- |
| Large surplus | 2 | [3308,3332) |
| Large deficit | 6 | [1165,1189) |
| Net-load ramp up, surplus to deficit | 14 | [8580,8604) |
| Net-load ramp down, deficit to surplus | 18 | [2439,2463) |

Retain the original optional load-shedding variables, penalties, eligibility,
and bounds in every window. No fixed-load ablation, objective multiplier, cost
change, or boundary-state change. Only the previously tested equivalent problem
interventions apply: normalized storage/ND capability cones; five-pass joint
objective/constraint scaling; exactly fixed Pg/p_nd coordinates expressed as
equalities and substituted. No near-fixed coordinate elimination.

Set full relative-gap tolerance to 1e-6. Keep absolute-gap and feasibility
tolerances at 1e-10, reduced tolerances at 1e-10, max_iter=5000, one thread,
minimum-step cutoff 1e-8, SCIPY canonicalization, verbose and no warm starts,
as in the prior stock-backend check. No custom native arithmetic or SuperLU.
Require native Solved, full native convergence checks, original E3 physical
and cost audit, and canonical-to-physical objective reconstruction error <=1e-4
for complete acceptance. Report each gate independently; do not relabel prior
records or change production acceptance. No AC feasibility claim.

Bind the source commit, experiment sources, installed extension, original E3
binding/completion/result hashes and mathematical input digests. Reconstruct
both ramp inputs through the same E3 builder as surplus/deficit. Save canonical,
reduced and scaled matrices, exact mapping checks, scale vectors, native settings,
iteration trace, native and restored primals, duals/slacks, public results,
physical/cost audits, timings and sampled RSS. Independently rebuild and audit
retained solutions offline; verify identical prepared matrices across backends.

E3 stopped before arm 18: ramp down is bound to its original frozen study
definition and verified Tracy inputs, with the reconstructed input digest;
historical completion/result hashes are explicitly null. It has no prior solved
comparator. The other three conditions retain their historical artifact checks.

Use the existing isolated worktree, immutable ignored results/four_conditions_001,
fresh serial workers, 180 s /4096 MiB per worker, half-second RSS polls. Stop
on process/resource/provenance failure; retain ordinary solver rejection and
continue the declared matrix without retry. No production/source changes during
execution, package installation, promotion, or additional numerical study.
Save a final report and index entry, including any failure or repair.
