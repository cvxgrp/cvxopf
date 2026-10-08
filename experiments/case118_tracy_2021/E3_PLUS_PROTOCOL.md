# E3+ forced-shedding execution protocol

This separate four-arm cohort adds the existing T=24 forced-shedding fixture as
the sixth comparison case described in [the extension plan](E3_PLUS_PLAN.md).
The original twenty prepared E3 results and all historical qualifications remain
unchanged. Execution requires a reviewed clean owner commit and separate launch
approval; preparation does not authorize optimization.

## Exact fixture and treatments

Use global input hours [1165,1189), hourly intervals, the retained demand
multiplier 7.5416905141946184 applied to explicit P and Q demand, and the existing
50%-initial/50%-hard-terminal battery boundary. All physical fixture fields must
equal the pinned objective-assembly qualification T=24 inputs after excluding
numerical-preparation policy. Do not recalculate the multiplier or import old
starts/solutions. Bind the fixture path/hash, original/modified demand, supply
bound and minimum ENS 439252.83068650117 MWh.

Order: copper plate, lossy DC, SOCP, AC. Use the same production preparation,
fixed solver settings, twenty-slot AC recovery order, strict native-status gates,
physical/accounting gates and advisory cycling warnings as
[prepared E3](E3_PREPARED_PROTOCOL.md). AC's component-first phase precedes hourly;
all convex arms use original economic coordinates and hourly assembly.
No tuning, changed weights or gates, network strengthening, voltage/reactive
penalties or additional depleted-storage fixture is included.

The initial AC start is the fresh builder's stock complete point. Causal
perturbations reuse that E3+ point; phase-specific target-free copies/perturbations
use only independently accepted, supervised diagnostic sources within E3+.
Target-free acceptance never completes a hard-target arm. No ordinary E3 or old
qualification result supplies an initialization or acceptance for the new cohort.

Every converged candidate must also have positive measured ENS no lower than
the input-only shortage witness, allowing the existing 1e-4 MWh witness gate.
Independent replay reconstructs forced demand and applies the same check.
The optimistic witness proves necessary shedding, not network feasibility.

## Approved resource envelope

Owner approved these limits on 2026-10-08 for runner preparation:
3600 seconds per complete attempt, 16384 MiB sampled RSS, 43200 cumulative
supervised worker-seconds, 28 launches, one-second resource sampling.
There are 23 ordinary slots plus five interruption allowances; the independent
total budget can censor the ladder earlier. Clip attempts to remaining worker
time and charge termination overhead. No automatic extension is authorized.

Require process/RSS and valid AC power/thermal monitoring before invocation and
each launch. STOP, monitoring failure, RSS exhaustion or worker/implementation
failure stops for review. Timeouts consume slots; numerical rejection and
timeout do not prove infeasibility. Budget revisions must be explicit and
hash-bound, adopted before another attempt; numerical controls stay fixed.

## Evidence and commands after review/commit

Fresh output is `results/e3_plus/`; never append to prepared E3 or historical
runs. Reuse the supervised immutable attempt journal; retain result/native/
preparation/start artifacts and completion hashes; independently replay saved
vectors without solving. Unfinished or unsupervised attempts cannot be accepted,
advanced or used as seeds. Report accepted arms out of four separately from
disposed/unresolved arms, diagnostics and archives. Comparisons require eligible
accepted arms; preserve missing and one-sided outcomes. Stage D remains held.
Owner stages and commits; no launch is authorized yet.

```sh
uv run --offline --no-sync --extra dev python -m experiments.case118_tracy_2021.run_e3 --plus --show-protocol
uv run --offline --no-sync --extra dev python -m experiments.case118_tracy_2021.run_e3 --plus --commit FULL_COMMIT --preflight
uv run --offline --no-sync --extra dev python -m experiments.case118_tracy_2021.run_e3 --plus --commit FULL_COMMIT --authorize-execution
uv run --offline --no-sync --extra dev python -m experiments.case118_tracy_2021.run_e3 --plus --status
```

Import, protocol display and preflight do not optimize or create run artifacts.
Same-source resume requires explicit `--resume`; clearing a STOP also requires
`--acknowledge-stop`. No source changes while a run is active.
