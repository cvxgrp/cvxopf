# AC cost-coordinate qualification: executed evidence

2026-10-07, `socp`, clean execution commit
`d7713602384d5c3f3b2dc73d95af58fd86a4d5bc`. The owner committed the reviewed
runner and approved proceeding with the declared severe forced-shedding variants.
The original [protocol](PROTOCOL.md), solver settings and gates were unchanged.

**Matrix complete; original strict-gate qualification is incomplete.** All 22 arms returned native
IPOPT status 0 and passed the original-unit physical checks. Eleven arms passed
all acceptance gates; eleven were rejected for cycling auxiliary cost discrepancy.
Both cost coordinates passed 7/10 prescribed arms versus 2/10 for original
coordinates. The cycling-only and shedding-only ablations each passed their one
prescribed stock T=3 arm; neither has broader initialization/horizon/shedding
coverage. There is no basis here for a project-wide default or global-optimality
claim. Production, original E3, held Stage D and historical archives are unchanged.
The owner subsequently approved this bounded result as a success and made the
cycling-gap check advisory for subsequent work; see the prospective decision below.
All acceptance counts and table verdicts here retain the executed protocol's meaning.

## Execution and evidence integrity

Fresh ignored evidence lives in `results/qualification_001/`. The parent reports
`matrix_complete`, with 22 launches, 22 disposed arms, 22 independently replayed
completed archives, 22 retained result archives and 22 matching completion
manifests. No unfinished, unresolved, timed-out or infrastructure-failed attempts
remain. Accepted arms are counted separately from completed archives.

One serial worker and one numerical-library thread were used throughout. Cumulative
supervised worker time was **744.271438 seconds** of 3,960; maximum sampled worker
RSS was **10,215.71875 MiB (9.976 GiB)** of 16 GiB; the longest attempt was
151.775858 seconds of 180. No retry, settings change, budget extension, RSS stop
or wall timeout occurred. Actual temperature, AC power and RSS access passed
prelaunch checks and per-launch telemetry is retained. Sampled RSS is not a
continuous-memory guarantee. Invocation start/finish timestamps include parent
assembly, repeated non-solving replay and inter-arm checks, not just worker time.

Terminal parent replay and fresh analysis replay completed at the clean bound
commit. Independent `cvxopf-review` source-bound replay subsequently reproduced
the terminal report exactly before this tracked report was edited. Every native
objective independently reconstructs within the prospective
`1e-7 + 1e-12*abs(native objective)` gate; the largest absolute reconstruction
error is `1.90735e-6` in a roughly `1.1853e10` objective. Generator and shedding
component accounting pass in every arm. All eleven economic rejects fail the
separate cycling-component/absolute-auxiliary-slack gate, not physical feasibility.

`analysis.json` and ten figures were published successfully and remain immutable.
An invalid downstream `jq` display filter closed stdout, producing a
`BrokenPipeError` only in the analyzer's final CLI print after publication. The
published analysis and figure hashes were inspected; analysis was not rerun or
overwritten. This display failure did not affect any solve, audit or artifact.

The observational `replay-checkpoint.json` pins the clean-source terminal evidence
before documentation edits. It is not a substitute for native archives or reviewer
replay. Future `--status`/`--analyze` calls still require the exact bound clean
sources/environment; editing this report intentionally invalidates replay in the
current checkout. Use the identified execution commit for any future replay, not
a source/context bypass. The original report bytes are retained in that commit.

| Artifact under `results/qualification_001/` | SHA-256 |
| --- | --- |
| `binding.json` | `04eca2fef07d8670b7f1069a7e3c66bd69793530b5c454f9ff034c349c7169f4` |
| `protocol.json` | `8cbaa984b2bd828fe4a46f0ef8a91d90e96c5bc5a59ca5782c6d25f5f01ca43a` |
| `report.json` | `275f17658b403df265c459d2b7bf23e49192ae144ca19a5091f371701b4e8167` |
| `analysis.json` | `b069aefcce85d643dda61bc72259ee1186836cbe81ebd6074bfaa5b2355a1b57` |
| `invocation-start.json` | `ac19615e85dd563f91095d8d3cc714e3d8998441682c950bedb2839a13b7df77` |
| `invocation-finish.json` | `b10deab321f0ba2404ca3bd859913bf8421198aabf3fc88ddd923481c5cc97ea` |
| `replay-checkpoint.json` | `b30547321acc94e83c0d93f26fb727d8d3078936bd540b18774def32d1a124b3` |

## Stock T=3 ablation

All arms use combined device normalization plus exact-fixed removal on Tracy
`[1165,1168)`, the same stock physical start and original mathematical inputs.
The economic gate is prospective `1e-4 + 1e-6*abs(physical cost)` for the total
and **each** component, plus absolute cycling auxiliary slack; no old `1e-10`
relative total-cost acceptance rule was used.

| Arm / coordinates | Physical cost | Cycling cost gap | Battery throughput MWh | IPOPT iterations | Worker s | Accepted |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 01 original | 5.314000915 | 115.876339 | 531.121881 | 26 | 10.203 | no |
| 02 cycling only | 1.092730796 | 0.000068745 | 109.256207 | 137 | 54.063 | yes |
| 03 shedding only | 0.840846242 | 0.000085171 | 84.064212 | 46 | 16.357 | yes |
| 04 both | 0.840752632 | 0.000060499 | 84.060764 | 45 | 16.294 | yes |

The original control and both-coordinate candidate reproduce the earlier diagnostic
physical costs and throughput. Either single substitution fixes accounting for
this start, but shedding-only reaches nearly the same low-throughput schedule as
both; cycling-only finishes at a higher physical cost and needs more iterations.
Changing shedding coordinates reduces the reconstructed initial maximum canonical
objective gradient from `4.7661e6` to `1.2458e4`; cycling-only leaves that maximum
unchanged. The inferred default gradient objective scales are `2.0981e-5` versus
`8.0269e-3`. These are objective-only reconstruction/inference, not native scaling
telemetry or a complete causal diagnosis. In particular, materially priced
shedding need not be active for its canonical coefficient to affect conditioning.
The initial-gradient maximum also remains far above one after both substitutions:
the rest of the objective/preparation was deliberately unchanged.

## Remaining matched pairs

Every entry below converged and passed physical checks. Costs and trajectory
differences describe retained candidates, including economic rejects; equality of
local AC solutions is not an acceptance gate. Imported historical starts use the
declared exact-fixed/initial-boundary resets, not an unchanged native warm start.

| Arms / fixture and start | Original physical cost | Both physical cost | Cycling gap original / both | Accepted original / both |
| --- | ---: | ---: | ---: | --- |
| 05–06 T=3, historical unprepared start | 3.844698874 | 0.840552701 | 37.734481 / 1.84021e-7 | no / yes |
| 07–08 T=3, historical prepared start | 3.100705013 | 0.841282555 | 31.233663 / 0.000225821 | no / no |
| 09–10 surplus T=3 `[3308,3311)` | 0.000753372 | 4.44613e-7 | 0.000138945 / 1.98414e-7 | no / yes |
| 11–12 ramp-up T=3 `[8580,8583)` | 3.007709771 | 4.41539e-7 | 82.238170 / 2.01212e-7 | no / yes |
| 13–14 original deficit T=6 `[1165,1171)` | 5524.613072 | 5523.236002 | 0.000078069 / 0.000122065 | yes / yes |
| 15–16 device-bearing Case9 T=3 | 25024.814181 | 25024.814174 | 1.53023e-6 / 4.63670e-7 | yes / yes |
| 17–18 forced Tracy T=3 `[1165,1168)` | 820038181.145537 | 820038181.154013 | 0.185399267 / 0.002467607 | no / no |
| 19–20 forced Tracy T=6 `[1165,1171)` | 1869086164.856289 | 1869086164.850106 | 0.018399127 / 0.000011079 | no / yes |
| 21–22 forced Tracy T=24 `[1165,1189)` | 11852950486.151940 | 11852950486.055317 | 0.025503861 / 0.004628212 | no / no |

The three both-coordinate rejects are arm 08 (cycling limit `0.000100841`),
arm 18 (`0.000121187`) and arm 22 (`0.000261902`). Their smaller discrepancies
remain failures under the declared gate; no gate was relaxed after seeing them.
These are historical strict-gate verdicts, not rejection under the subsequent
owner-approved warning policy.
The original T=6 and Case9 arms pass, demonstrating that original coordinates
do not universally fail. T=6 both finishes in 36.739 seconds versus 119.290 for
original, but forced T=6 both takes 41.831 versus 23.459 seconds. There is no
uniform runtime advantage.

Generation-cost coverage is established by accepted Case9 arm 16. Forced-shedding
inputs really shed in every candidate: ENS original/both is approximately
39,473.54749 / 39,473.54749 MWh (T=3), 89,973.65907 / 89,973.65907 (T=6), and
570,660.88008 / 570,660.88007 (T=24), all above their input-only conservative
bounds. Both reduces cycling error by about 75x, 1661x and 5.5x respectively,
but only forced T=6 passes. Accordingly the declared **all-horizon accepted
forced-shedding coverage is false**, not absence of shedding. A total-relative
test alone would accept the cycling errors underneath these large shedding bills;
the component gate correctly exposes them.

## Matched trajectories

`analysis.json` retains identity-aligned complete device arrays, all pairwise
deltas, global input hours and SoC boundary hours. All axis checks pass.
Ten six-panel fleet figures show generation, battery net power, absolute
throughput, boundary-inclusive SoC, battery reactive dispatch and real load shed.
All ten were visually inspected. They deliberately include economic rejects and
warn that fleet sums can conceal device counterflows.

| Figure | Matched group |
| --- | --- |
| [01](results/qualification_001/group-01-fleet.png) | stock original T=3 ablation |
| [02](results/qualification_001/group-02-fleet.png) | T=3 historical unprepared start |
| [03](results/qualification_001/group-03-fleet.png) | T=3 historical prepared start |
| [04](results/qualification_001/group-04-fleet.png) | surplus T=3 |
| [05](results/qualification_001/group-05-fleet.png) | ramp-up T=3 |
| [06](results/qualification_001/group-06-fleet.png) | original deficit T=6 |
| [07](results/qualification_001/group-07-fleet.png) | device-bearing Case9 T=3 |
| [08](results/qualification_001/group-08-fleet.png) | forced-shedding T=3 |
| [09](results/qualification_001/group-09-fleet.png) | forced-shedding T=6 |
| [10](results/qualification_001/group-10-fleet.png) | forced-shedding T=24 |

Both and shedding-only stock T=3 battery trajectories nearly coincide (maximum
device real-power delta `0.00138 MW`), while unpriced reactive/renewable coordinates
still differ. Both suppresses real cycling to numerical zero in the surplus and
ramp-up prefixes. Forced cases have closely matched conventional generation and
total ENS, but that is not device-dispatch identity: forced T=24 maximum per-device
deltas are `0.0000394 MW` generation, **218.278 MW battery power, 168.043 MWh SoC**,
`137.254 MW` renewable dispatch and `0.250846 MW` load shed. Fleet plots alone
would conceal much of this. These differences are descriptive nonconvex/unpriced
selection evidence, not proof that any dispatch is globally optimal or necessary.

## Conclusion and handoff

The original diagnostic win survives multiple new inputs, and the ablation points
especially to shedding-coordinate conditioning in the low-cost T=3 example.
However, cost coordinates alone with unchanged solver settings do **not** meet
the full declared AC qualification: historical-start sensitivity and forced
T=3/T=24 cycling auxiliary accuracy remain. Preserve these failures as evidence,
not reasons to retune an already executed protocol or declare infeasibility.

### Owner decision: retain cycling-gap checks as warnings

After reviewing these results on 2026-10-07, the owner approved the bounded AC
cost-coordinate result as a success. For subsequent qualification and integration,
the cycling-component discrepancy and absolute cycling auxiliary-slack check are
**advisory economic-accuracy warnings**, not reasons to reject an otherwise valid
returned solution. Keep their current thresholds and report the measured gap,
threshold, physical cycling cost and affected arm; do not hide a warning beneath
a large shedding bill or label it an accurate cycling-cost result.

Solver status, original-unit physical feasibility, restoration, provenance,
supervision/resource requirements and independent canonical-objective
reconstruction remain hard requirements. Generator/shedding accounting failures,
nonfinite evidence and unexplained objective discrepancies are not covered by this
decision. A total-objective discrepancy attributable to the flagged cycling
auxiliary gap must not indirectly reintroduce cycling-gap rejection; subsequent
policy implementation must distinguish that explained discrepancy from unrelated
accounting failures. Report physical dispatch costs separately from the native
canonical objective, retaining both and their component decomposition. Warning-only
accounting does not certify economic precision or local/global optimality.

This is an explicit prospective policy decision, not a tolerance relaxation or a
retroactive scientific rerun. The frozen protocol, audit implementation, archives,
manifest classifications, analysis and strict-gate coverage counts remain unchanged.
In particular, the three both-coordinate cases remain visible as warnings for
careful interpretation, rather than failed physical solutions. No production
return-path change is needed at this checkpoint: its cycling-gap rejection policy
has not yet been implemented. Carry this separation into the next reviewed runner
and later reusable production implementation; do not silently reuse the frozen
runner's rejection semantics or promote experimental monkey-patching.

The next planned work is separately reviewed convex qualification (SOCP, lossy DC,
copper plate), including actual cycling/shedding component checks and preparation
interactions with the owner-approved warning semantics, before production extraction.
The bounded success is not project-wide default approval. Any follow-up AC economic-accuracy
diagnostic needs a prospective protocol and separate bounded execution approval.
No further solve, production promotion or E3 action was launched here.

Independent scientific results/report review by `cvxopf-review` is **clean**
(turn `01a11898-8430-7bd1-90a5-96b5ae91f2a9`). The reviewer verified pre-edit
source-bound replay, all counts and gates, costs/resource figures, device-level
trajectory differences, all seven listed artifact hashes and ten figure hashes,
and whitespace checks; no actionable findings remain. No new solves or reviewer
edits were made. Clean review means the evidence/report is sound, not the rejected
treatment becoming qualified. Only this tracked report changes at the results
checkpoint; raw records, analysis, figures and replay checkpoint stay ignored.
The subsequent owner-decision documentation review is also **clean**
(`cvxopf-review` turn `01a118a6-4399-76b0-bf4e-f02655b26b8d`), including the
prospective warning semantics and preservation of historical verdicts.
The agent has not staged or committed anything. Proposed owner commit subject:
`Record AC cost-coordinate results and approve cycling-gap warnings`.

---

# Historical implementation checkpoint (before this execution)

2026-10-07, on `socp` after diagnostic-results commit
`b5393260c5b4f561d00123fbd28d89855e038455`.

**At that checkpoint no new numerical solves had run.** This was the runner/protocol
checkpoint for owner review, not AC qualification or execution permission. The fresh output
directory `results/qualification_001` had not been created. Production sources,
defaults, original E3, held Stage D and retained historical results are unchanged.

## Implemented scope

The [protocol](PROTOCOL.md) and `fixture.py` declare 22 ordered arms: separate
cycling/shedding ablations; matched stock and two imported historical physical
starts; additional original Tracy periods and a six-hour horizon; existing
Case9 generation-cost coverage; and paired forced-shedding Tracy variants at
T=3, T=6 and T=24. All arms use the existing combined AC numerical preparation.
No unprepared replay, convex qualification, production extraction or defaults
change is included; those remain separate work.

The shared experimental coordinate helper now accepts an explicit cycling-only
or shedding-only selection. Its original Boolean interface still selects both
or neither, preserving the four-arm construction. Accounting now determines
cycling units from the cycling map specifically, not the presence of any map.
No explicit epigraph or economic-weight change is introduced.

`run.py` reuses the verified IPOPT boundary, atomic archives, supervisor,
single-thread setup and actual temperature/AC-power/RSS monitoring. It requires
a reviewed clean full commit for execution, rejects resume/overwrite, preserves
numerical rejection/timeouts, stops on infrastructure/resource/operator failure,
and does independent evidence replay before counting acceptance or advancement.

`audit.py` rebuilds canonical layouts, complete x0, identity-bound fixed maps
and the native objective; restores engineering-unit dispatch; retains existing
physical audits; and checks total and individual native cost components.
Economic replay evaluates the canonical objective and its objective-only gradient
without constructing the network Jacobian or Lagrangian Hessian. Absolute cycling auxiliary slack prevents component/coordinate cancellation
from hiding economic error under a large shedding bill. `analyze.py` reports
all outcomes, separate treatment dispositions and actual component activation,
aligned full trajectories/deltas, and per-group fleet plots. Nonconvex cost or
trajectory equality is not imposed. Matrix completion is not qualification.

## Non-solving evidence

All 22 fresh fixture assemblies passed physical objective/constraint substitution
equivalence. Every matched group's physical-input and physical-start hashes agreed.
Historical start import uses pinned, supervised call 024/025 archives, then the
existing rule sets exact fixed Pg/ND and initial SoC to their input values;
free physical coordinates are unchanged.

The prescribed input-only stress recipe produces these **severe counterfactuals**:

| Tracy interval | Demand multiplier | Conservative unavoidable ENS lower bound |
| --- | ---: | ---: |
| T=3 `[1165,1168)` | 7.394074673 | 15,501.994257 MWh |
| T=6 `[1165,1171)` | 7.394074673 | 49,630.594471 MWh |
| T=24 `[1165,1189)` | 7.541690514 | 439,252.830687 MWh |

The large multipliers follow from intentionally overestimating supply using the
entire battery fleet's apparent-power ratings in every hour, ignoring neutral
terminal/energy restrictions and reactive obligations. These variants test
economic conditioning under unavoidable, materially priced shedding; they are
not plausible observed Tracy demand forecasts or evidence of AC feasibility.
Review this severity explicitly before execution. A milder stress recipe would
be a prospective protocol change, not a post-result adjustment.

The retained diagnostic winning primal (`comparison_002/call-004`) was read and
reconstructed without optimizing, invoking old source-bound replay, changing
its archive, or reclassifying historical evidence. It passes the new independent
canonical reconstruction and each component gate: native cost 0.840813131,
physical cost 0.840752632, canonical reconstruction error `4.44e-16`, cycling auxiliary
L1 excess 0.000060499. This validates the new reader/audit against known retained
evidence, not the as-yet-unexecuted 22-arm matrix.

## Review and execution handoff

Ceilings: 22 launches, 180 seconds per worker, sampled 16 GiB RSS, 3,960 cumulative
worker-seconds. There are no retries, automatic extensions, tuning or optimization
calls in preflight, replay or analysis. Native status 0 plus all independent gates
is required; numerical failure/timeout is not proof of infeasibility.

Non-solving verification: **163 focused tests passed**, including the new runner,
existing cost-coordinate diagnostic, qualification infrastructure and matched
Tracy variable/plot regressions. Ruff and whitespace checks pass. Existing
numerical-package import/DC metadata/mock-inaccurate-status warnings remain.
No optimizer was called by these tests, fixture preflight or retained-primal replay.
The first independent scientific review identified two P2 issues: supervised
timeout during archive publication aborted the matrix, and economic replay built
unused network derivative machinery. Both are corrected, with regressions for
timeout publication/parent advancement, successful-worker missing completion,
manifest/archive inconsistency, and objective-only evaluation versus the installed
stock oracle. Partial publication remains retained and unaccepted, with separate
archive/manifest/completion/acceptance counts. Independent scientific re-review
by `cvxopf-review` is clean (turn `01a1187b-15ab-7423-bcb2-e96eeb06e0e5`):
163 non-solving tests, lint/whitespace checks, retained-primal component/start/map
replay and both corrections verified. The reviewer confirmed no new solves or
execution directory. This establishes implementation readiness only; owner
commit, acceptance of the severe stress variants, and separate execution approval
remain required.
Exact commands (from the repository root, using the project environment):

```sh
uv run --extra dev python -m experiments.ac_cost_qualification.run --preflight
uv run --extra dev python -m experiments.ac_cost_qualification.run --authorize-execution --commit <reviewed-full-SHA>
uv run --extra dev python -m experiments.ac_cost_qualification.run --status
uv run --extra dev python -m experiments.ac_cost_qualification.run --analyze
```

The launch command above is documentation only, not current execution authority.
The default output is fresh/never overwritten. Read-only replay requires the
bound sources/environment, as do the existing runners; no cross-source bypass
is introduced. Raw records and generated figures remain ignored under `results/`.
