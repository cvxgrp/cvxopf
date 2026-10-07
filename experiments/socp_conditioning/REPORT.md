# Initial SOCP conditioning diagnostic: Case9 gate stopped the ladder

Date: 2026-10-02. Isolated checkout based on
`dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`; experiment code was intentionally
uncommitted, with exact source hashes retained. No production sources or live
E3 records were changed. All four solves ran serially in fresh workers.

## Result

All four Case9 arms returned native CLARABEL `Solved`, passed the independent
physical/accounting audit, and reconciled native and original objectives after
applying the canonical constant offset. However, the **objective-scaled-only**
arm failed the predeclared cross-arm objective-agreement gate. The runner stopped
before launching any Case118 diagnostic. This is a retained partial diagnostic,
not a completed scaling qualification or a change to the ongoing E3 study.

| Variant | Original-unit objective | Difference from baseline | Iterations | Maximum P imbalance (MW) |
|---|---:|---:|---:|---:|
| Baseline | 7680.961785934 | 0 | 48 | 1.106e-9 |
| Normalized cones | 7680.960199947 | -0.001585987 | 18 | 1.998e-13 |
| Objective / 1e6 | 7681.038326748 | +0.076540814 | 19 | 1.332e-13 |
| Both | 7680.960205445 | -0.001580489 | 18 | 1.279e-13 |

The objective-agreement threshold was 0.007780962 (absolute 1e-4 plus relative
1e-6 of the baseline). It has not been changed. The objective-only discrepancy
is about 0.001% of total cost: economically tiny here, but larger than the frozen
diagnostic threshold. Native `Solved` and physical feasibility do not by
themselves imply the same attained objective accuracy across scalings.

## What the complete native evidence adds

| Variant | Native gap, original cost units | Reconstructed dual residual infinity norm, original objective units |
|---|---:|---:|
| Baseline | 4.429e-8 | 3.517e-5 |
| Normalized cones | 3.566e-7 | 2.328e-8 |
| Objective / 1e6 | 4.858e-5 | 8.592e-4 |
| Both | 4.242e-6 | 2.301e-6 |

The dual residual is explicitly `P*x + c + A.T*z`, multiplied by the objective
divisor to return to original objective units. Its coordinates remain those of
each representation, so maxima across different representations are descriptive,
not a coordinate-invariant accuracy certificate. Baseline and objective-only
have the same representation, making that pair directly comparable.

The objective-only arm's largest dual residuals are on squared-capability
auxiliaries. Its lower-cost competitors pass the original physical audit. The
small native primal-dual gap is therefore not a rigorous objective-error bound;
dual feasibility and scaling still matter. This is consistent with numerical
conditioning effects, not evidence that positive whole-objective scaling changes
the mathematical minimizers.

Normalized direct cones reduce the largest canonical RHS from 225 to 20 in this
smoke case and reduce iterations from 48 to 18. They and the combined arm agree
in objective to about 5.5e-6. This is encouraging small-case evidence, not yet a
Case118 result or an AC performance claim. Wall timings are not used to select
the variant; another study was running concurrently. Supervised worker peaks
were approximately 152–154 MiB.

## Retained evidence and verification

Raw results: `results/diagnostic_001/`. Every arm retains native x/s/z,
canonical sparse A/P and b/c, layout, original-unit public results, full audits,
solver diagnostics, logs, completion record and supervision. Hashes, identical
physical inputs, and matching before/after execution contexts were verified.

- Summary SHA-256: `158c5f3c04f55a367db99514d1a6bb2c3ea337bd5df03c0655b2a7c8f06bf0a2`
- Protocol SHA-256: `308e8eb57ad53b02e596895c19077a57df7cb6e928c8b18ada76f08c76a8d669`
- Runner SHA-256: `c08b69155ba83d39bcc3f162045d7ce06192c2c4995bf0e05b88f99e5becfd92`
- 114 focused/affected tests passed, including 19 diagnostic tests.
- Ruff lint and formatting passed; whitespace checks passed.
- Existing CVXPY OpenMP import warning and zero-norm SOC-violation warning remain.

## Proposed continuation, requiring review

Retain this failed smoke-comparison gate unchanged. Review a prospective amendment
allowing the Case118 four-arm matrix as **descriptive diagnostic evidence**, with
the failed objective-only smoke outcome explicitly carried forward. Preserve all
original physical gates, settings and scaling factors; do not loosen the numerical
threshold to make this record pass. No such continuation or AC solve has run.
