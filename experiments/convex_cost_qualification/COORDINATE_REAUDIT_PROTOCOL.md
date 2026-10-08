# Coordinate-check correction and retained-result re-audit

Owner approved, 2026-10-07. This revision corrects qualification bookkeeping;
it does not change an optimization problem, solver setting, numerical result,
production default, or the original execution protocol.

## Corrected checks

The canonical primal is restored through fixed-coordinate and preparation maps,
then positive cost-coordinate inverses. A separate calculation clips ordinary
leaves to their **declared CVXPY bound attributes only**. Published arrays and
restored leaves must match this independently predicted projection with the
original `1e-10 + 1e-12 * max(abs(expected))` mapping tolerance. Explicit model
constraints are never silently projected. Malformed layouts, missing fields,
nonfinite values, wrong units or time axes, and unexplained discrepancies remain
hard failures.

Raw-to-box projection is recorded separately in engineering units, including
its maximum magnitude, tolerance and warning. It must satisfy the existing box
tolerance: `2e-5 MW` (20 W), `2e-5 MVAr`, `2e-5 MWh`, or `1e-8` for interruption
fractions. Initial SoC equality is checked separately on the raw restored
canonical boundary at the existing `1e-4 MWh` (100 Wh) tolerance. Its public
time-axis mapping remains exact; the initial boundary is not a published step.

These checks supplement, not replace, the unchanged stated-model feasibility,
economic reconstruction, forced-shedding witness, native full-convergence,
resource and provenance checks. Cycling gaps remain advisory. A projected
generator discrepancy may still cause a material economic discrepancy, which
remains a hard failure.

## Evidence preservation

`reaudit.py` reads only the terminal 96-arm `qualification_001` run bound to
`29f8aea803386149f49854b5fb22b71615f664b9`. It pins its original binding,
protocol, invocation records, report and original analysis, verifies completion
hashes/native archives/supervision/resources, and reconstructs the original
audit under the historical coordinate policy. Installed numerical dependencies,
physical inputs, canonical signatures and restoration maps must still match.
Only the explicitly enumerated checker/reader/test/report source edits are
permitted; their current hashes are recorded in the new binding.

All optimizer entry points are disabled during re-audit. Every converged native
result is rechecked, not merely relabeled. Non-coordinate checks and published
arrays must reproduce the old records exactly. New classifications, analysis
and plots go into a fresh `results/coordinate_reaudit_001` directory. Original
archives, manifests, logs, protocol, report, analysis and plots are not rewritten.
The new binding retains hashes of all original files and the revised policy.
An existing output directory cannot be overwritten. The original source-bound
execution reader continues to reject a changed execution context.

No new solves, retries, default promotion, solver-status relaxation, physical
gate changes, E3 execution or Stage D work is authorized by this correction.
