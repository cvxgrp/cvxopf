# Numerical preparation qualification: corrected disposition

The frozen 25-call matrix completed on 2026-10-07. **SOCP, lossy DC and
single-node DC qualify within this protocol; AC does not qualify.** After
independent review, the corrected count is **23 accepted, 2 accounting-rejected,
25 disposed**, with no unfinished or unlaunched calls. This is an explicitly
corrected analysis, not a rewrite of the original execution records.

| Formulation | Calls | Corrected accepted | Disposition |
| --- | ---: | ---: | --- |
| SOCP | 1–5 | 5 | Qualified; all four applicable historical pair gates pass |
| Lossy DC | 6–11 | 6 | Qualified; all three matched pairs pass |
| Single-node DC | 12–17 | 6 | Qualified; all three matched pairs pass |
| AC | 18–25 | 6 | Not qualified; Tracy calls 24 and 25 fail canonical accounting |

## Execution and preservation

The [protocol](QUALIFICATION_PROTOCOL.md) fixed the inputs, starts, policies,
solver settings and gates before execution. Call 001 ran at
`bd936f3ac9367ae2e37b5cc68b80f8cddd69706b`. Its first invocation failed during
independent replay because live solver statistics were unavailable on the
deliberately unsolved audit build. The reviewed metadata-only fix did not repeat
that call. The explicit retained source transition bound calls 002–025 to
`5c5590acb38adda3f166d9f6eb06a66972bdf0cd`, preserving the original binding,
protocol, first invocation and all call-001 bytes. Original call-001 effort
remains charged.

Retained evidence root: `results/qualification_001/` (ignored by Git).
The continuation invocation is `invocation-1791388307365008000`; its finish
record says `matrix_complete`. All 25 calls have matching completion manifests,
compressed archives and finalized supervision. Before this correction, a fresh
independent reconstruction exactly matched the original report. The original
226 files are preserved, including every original audit and acceptance label.

| Retained file | SHA-256 |
| --- | --- |
| `binding.json` | `6b7e8fe8126f9ced99efd0ed6c4c94d322dab15e85ad02192acacf6adf0c587c` |
| `protocol.json` | `d1178dbb97ba1590c064952128db699066a6f4f0a93eb3bcdff2b55cc72c5374` |
| Original `report.json` | `13bf374c103d27040fb01fe9b3f1f5db79df7ba797c7dbf3958e56971dfaa8fa` |

The original report's `accepted=25` is superseded by the corrected analysis
here, not silently edited. Do not resume this completed root or adopt a further
source transition. Its source-bound status reader requires its execution
source/environment; the later accounting correction does not bypass that
guard. Inspection of retained scalar costs below is non-solving analysis and
does not claim a new execution context or historical acceptance.

## Accounting correction

The original AC audit compared physical component costs to CVXPY's reevaluated
public objective. That objective does not retain slack in the smooth canonical
cost auxiliaries. Independent review found that the protocol's separate
canonical-to-physical objective comparison was missing. Native IPOPT status 0
and physical feasibility alone therefore did not justify all eight AC
acceptance labels.

`audit.canonical_ac_accounting()` now checks the retained native `obj_val`
against the independently reconstructed sum of component costs, using the
unchanged AC gate `1e-4 + 1e-10*abs(physical cost)`. The AC bridge evaluates the
original canonical objective directly; there is no substitution offset to add.
Missing/nonfinite costs fail closed. Existing component, physical, native-status
and transformation checks remain mandatory. Convex audits are unchanged.

Correction verification: 150 focused qualification/Stage B/Stage D tests pass;
Ruff and whitespace checks pass. Regression tests cover both discrepancy signs,
missing/nonfinite native costs, missing/nonfinite/overflowed physical costs,
the unchanged relative gate and both retained Tracy discrepancies. These are
infrastructure/audit tests, not additional qualification optimizer calls.

| AC call | Canonical–physical discrepancy | Allowed | Corrected gate |
| --- | ---: | ---: | --- |
| 18, Case9 T=1 baseline | 1.25001e-7 | 1.00883e-4 | Pass |
| 19, Case9 T=1 normalized | 1.42232e-7 | 1.00883e-4 | Pass |
| 20, Case9 T=1 combined | 1.89522e-7 | 1.00883e-4 | Pass |
| 21, Case9 T=3 baseline | 8.87388e-6 | 1.02502e-4 | Pass |
| 22, Case9 T=3 normalized | 1.11092e-6 | 1.02502e-4 | Pass |
| 23, Case9 T=3 combined | 1.53022e-6 | 1.02502e-4 | Pass |
| 24, Tracy baseline | 9.1080302588 | 1.1854935268e-4 | Reject |
| 25, Tracy combined | 115.8763385922 | 1.0000053140e-4 | Reject |

These checks were reproduced directly from the manifest-verified native and
physical-cost evidence, without solving. A minimal read-only reconstruction in
the normal project environment is:

```python
from experiments.numerical_preparation import audit as a, run_qualification as r

for call_id in range(18, 26):
    directory = r.OUTPUT / f"call-{call_id:03d}"
    manifest = r.read(directory / "completion.json")
    for name, expected in manifest["artifacts"].items():
        assert r.digest(directory / name) == expected
    record = r.read(directory / "result.json.gz")
    print(call_id, a.canonical_ac_accounting(record["native"], record["audit"]["common"]))
```

All four Case9 AC pair checks pass. The original Tracy pair compared physical
costs 185,493.526807319 and 5.314000914998394: difference 185,488.212806404,
versus allowed 0.185593526807319. Its ENS difference, 8.91286e-8 MWh, passes.
With corrected acceptance, that pair is **ineligible** for accepted-objective
equivalence because neither Tracy outcome passes every required gate. The
physical difference remains an observed diagnostic, not a qualified comparison.

Both Tracy calls return IPOPT status 0 and pass physical/network/device and
terminal audits; inputs and assigned physical starts match. Nevertheless, do
not describe them as two accurately resolved local optima: the canonical
accounting discrepancies limit that interpretation. Their physical trajectories
differ materially (including conventional generation and renewable dispatch),
showing sensitivity to numerical representation, not proving a production
transformation defect, infeasibility, global optimality or speedup.

## Resource and decision boundaries

The 25 launches consumed 197.047162 worker-seconds, including call 001, below
the 4,500-second budget. Longest supervised worker: call 24, 121.437403 seconds,
below 180 seconds. Peak sampled RSS: 2,488.78125 MiB (2.43045 GiB), below the
16-GiB ceiling. All resource journals agree with supervision; no worker remains.
There were no retries, replacement calls, resource-limit disposals or extra
optimization calls during correction.

SOCP historical deficit/ramp-up controls retain their original rejection labels;
their separate retrospective comparator eligibility is not historical acceptance.
The rejected historical depletion control supplies no accepted-optimum pair gate.
DC trajectories need not be identical under nonunique optima; the prescribed
cost/ENS gates pass and trajectory deltas remain in the original report.

Qualification does not enable production defaults, qualify prepared hierarchy
execution, authorize a new E3 run, resume original E3/Stage D, retire the isolated
worktree, or authorize new AC diagnostics. Those remain separate owner decisions.
No source staging or commit is performed by the agent.
