# AC cost-coordinate qualification: implementation checkpoint

2026-10-07, on `socp` after diagnostic-results commit
`b5393260c5b4f561d00123fbd28d89855e038455`.

**No new numerical solves have run.** This is the runner/protocol checkpoint for
owner review, not AC qualification or execution permission. The fresh output
directory `results/qualification_001` has not been created. Production sources,
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
