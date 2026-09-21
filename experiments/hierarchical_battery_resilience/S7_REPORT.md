# M17 S7 public-reference equivalence report

## Result

The public `solve_hierarchical_opf()` implementation reproduces all five
reviewed manual-reference trajectories within the predeclared absolute
`1e-6` implementation-equivalence tolerance. The authoritative comparison
contains zero failed checks.

This closes the central M17 implementation-equivalence gate. The public
controller preserves the manual reference's outer energy plans, AC-window
state handoffs, accepted first actions, realized SoC trajectory, failure
behavior, initialization-recovery sequence, and executed-interval accounting.
It does not merely reach similar aggregate objectives.

## Cases

| Public policy | Reference | Completed intervals | Result | Maximum finite difference |
|---|---|---:|---|---:|
| frozen, hard equality, `flat_only` | S3 | 96 / 96 | completed | `5.82e-11` |
| frozen, quadratic soft, `flat_only` | S3 | 96 / 96 | completed | `9.09e-13` |
| replanned, hard equality, `flat_only` | S3 | 35 / 96 | same interval-35 AC failure | `3.38e-12` |
| replanned, quadratic soft, `flat_only` | S3 | 95 / 96 | same interval-95 outer infeasibility | `4.43e-12` |
| replanned, hard equality, `shifted_with_recovery` | S3b | 96 / 96 | completed | `9.09e-13` |

The reported maxima range across every common finite numeric field checked in
outer plans, AC attempts, public results, executed-interval records, realized
states/actions, and non-runtime trajectory summaries. They are four or more
orders of magnitude below the comparison tolerance.

## What was compared

The runner verified the tracked hashes of the S3 and S3b references before
solving. It then compared:

- every outer plan's creation interval, local/global boundary indices, SoC
  signposts, status, outcome, and common result fields;
- every S3 controlling AC attempt and every one of the 864 S3b attempt slots,
  including role, order, transformation, perturbation scale/seed, causal source
  location, slot state, solver status/outcome, initial state, target, and common
  result fields;
- every executed first action and interval-level physical/economic record;
- realized SoC and battery-power trajectories;
- completion, termination iteration and layer/outcome classification; and
- every non-runtime trajectory aggregate.

Runtime is retained as execution evidence but is not an equivalence field.

## Reviewed schema distinctions

Three representational differences are normalized explicitly without
weakening any finite comparison:

1. The manual soft runner says `accepted_soft`; the public closed outcome type
   says `accepted` and separately retains the quadratic-soft policy and signed
   terminal deviation. These represent the same accepted outcome class.
2. Manual JSON writes unavailable failed-solve scalars as `null`; public result
   extraction uses `NaN`. Both are treated as unavailable, never as finite.
3. The S3 failed hard-replanning window retains one additional target-free
   diagnostic. Public `flat_only` intentionally has one controlling slot and
   does not perform recovery. S7 compares all 36 controlling attempts and
   requires the same interval-35 failure; the extra S3 record remains labeled
   as a manual-only diagnostic.

The S3b comparison requires and passes the complete nine-slot registry in all
96 windows.

## Execution history and provenance

The first execution attempt from commit `3119a9a` completed its scientific
solves but produced no artifact because the comparator mishandled a skipped
S3b slot with no audit. The corrected run from `4e95a83` preserved a complete
comparison artifact, but its reported differences exposed the three
cross-schema normalization requirements above. That artifact is retained
locally as a failed-comparator record and is not the authoritative S7 result.

The authoritative run executed all five cases anew from clean commit
`5fb84cf6e78f3c84b6105a024d08a05d94622fe4`. It did not resume or reuse either
earlier execution. The ignored detailed artifact is identified by the tracked
[`S7_RESULTS_METADATA.json`](S7_RESULTS_METADATA.json).

The authoritative artifact captured Python and package versions, including
`cyipopt`, but did not capture the linked IPOPT version. A subsequent query in
the presumed unchanged local environment reported IPOPT 3.14.19; the metadata
labels that value as a post-run observation rather than execution-captured
provenance. Future S7 executions capture it directly.

## Interpretation

The result establishes implementation equivalence for the frozen Tracy-derived
scenario and recorded solver stack. In particular, the hard shifted-recovery
public policy reproduces the causal S3b controller rather than merely sharing
its final completion status.

This remains scenario-specific evidence. It does not claim global AC
optimality, universal IPOPT robustness, or recursive feasibility for arbitrary
networks and forecasts. The reproduced `flat_only` failures remain useful
negative controls, while the reproduced shifted-recovery completion supports
the selected M17 reference workflow.
