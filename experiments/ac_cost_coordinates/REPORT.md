# Tracy AC cost-coordinate comparison

## Completed matched diagnostic: comparison_002

2026-10-07. **Combined preparation plus cost coordinates succeeds on this
matched three-hour case.** Its independently reconstructed physical cost is
0.840752632, versus 5.314000915 for the prepared original-coordinate control.
The canonical-versus-physical objective gap falls from 115.876338592 to
0.000060499. This is also a substantially different, cheaper physical dispatch,
not merely a correction to the reported objective.

The new invocation is bound to clean commit
`e15e01b54d922be440d4b4d0c2ab8940a7f3982e`, with exact source, package, input,
start and protocol hashes in `results/comparison_002/binding.json`. All four
arms use the same mathematical inputs and physical starts for Tracy hours
`[1165, 1168)`, energy-neutral terminal targets, and unchanged IPOPT settings.
The physical cycling and shedding weights were **not** changed. The original
failed startup and historical studies remain untouched.

| Arm | Physical cost | Native canonical cost | Absolute cost gap | Outcome | Iterations | Worker seconds |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| Unprepared, original coordinates | 185493.526807319 | 185502.634837578 | 9.108030259 | Converged, physical audit passed; accounting rejected | 321 | 118.292 |
| Unprepared, cost coordinates | unavailable | unavailable | unavailable | Wall timeout; unresolved | unavailable final count | 180.058 |
| Prepared, original coordinates | 5.314000915 | 121.190339507 | 115.876338592 | Converged, physical audit passed; accounting rejected | 26 | 9.183 |
| Prepared, cost coordinates | 0.840752632 | 0.840813131 | 0.000060499 | Accepted: convergence, physical, preparation and accounting checks passed | 45 | 15.305 |

Three native result archives and three matching completion manifests were
retained. The timeout has supervision, samples, log and start evidence, but
**no native result or completion manifest**; its terminal log is not an accepted
solution. Timeout is not proof of infeasibility. Both fresh controls exactly
reproduce their historical native primals and objectives.

The accepted arm satisfies the preregistered prospective total-cost gate
`1e-4 + 1e-6*abs(physical_cost)` (limit 0.000100841). It also passes the older
`1e-4 + 1e-10*abs(physical_cost)` check: this outcome does not depend on relaxing
the relative coefficient. The old coefficient remains unchanged in the common
physical/component audit; that check compares reconstructed physical costs,
not IPOPT's loose auxiliary objective. No historical acceptance is reclassified.

### Physical trajectories

Compared with the prepared original-coordinate control:

- Absolute battery throughput falls from **531.121881 to 84.060764 MWh**, about
  84% lower. Storage cost falls from 5.311218806 to 0.840607641.
- Almost all accepted real-power cycling is at `storage_bus_82`: charge
  42.030 MW, then discharge 9.442 and 32.587 MW. The other 26 batteries together
  contribute only about 0.00169 MWh real-power throughput. This does not remove
  their reactive support role.
- Conventional generation remains effectively zero in both prepared solutions.
  Generation cost is 0.000023348 and shedding cost 0.000121644 in the accepted
  treatment. ENS is approximately `5.86e-9 MWh`, i.e. numerical-scale shedding.
- Fleet battery reactive injection changes from `[132.448, 162.320, 179.772]`
  to `[108.956, 200.131, 210.222]` MVAr. Renewable real/reactive schedules also
  change; renewable curtailment falls from 18641.824 to 18630.445 MWh.
- Maximum per-device differences are 97.826 MW in battery real dispatch,
  15.652 MVAr in battery reactive dispatch, 97.826 MWh in end-state SoC,
  156.431 MW in renewable real dispatch, and 31.283 MVAr in renewable reactive
  dispatch. These are descriptive differences, not failed trajectory gates.

Every battery returns to its declared initial/terminal SoC. Accepted maximum
SoC recurrence error is `1.12e-13 MWh`; physical AC balance residuals are
`5.43e-9 p.u.` real and `2.96e-8 p.u.` reactive, below their `1e-6 p.u.` gates.
Device boxes, apparent-power limits, branch limits/reporting and voltage checks
all pass. Constraints and costs are reconstructed in original physical units;
the device/fixed-coordinate transformation check independently confirms all
135 expected fixed coordinates and the captured-start map.

![Matched fleet trajectories](results/comparison_002/fleet-trajectories.png)

[Device-aligned battery trajectories](results/comparison_002/battery-device-trajectories.png)
show why fleet sums alone are insufficient. SoC plots prepend the frozen initial
state to the three extracted end states. Only the three converged archived
primals are plotted; the timeout is not filled in or interpolated.

### Numerical interpretation and limits

Fresh non-solving canonicalization reproduces the captured initial point. The
maximum initial objective gradient is 4,766,115.382 for original coordinates
and 12,458.1564 for the accepted cost-coordinate treatment. The inferred default
gradient-based objective scale changes from `2.0981e-5` to `0.00802687`. These
scales are calculated from the installed default rule, **not directly observed
native scaling telemetry**. Automatic cycling-auxiliary and shedding objective
coefficients become one by exact coordinate substitution. The accepted
cycling epigraph excess is `6.05e-5` cost units, with maximum per-coordinate
cost slack `8.19e-7`; it accounts for the entire remaining objective gap to
`6.34e-16` reconstruction error.

This supports the cost/auxiliary-conditioning hypothesis and demonstrates a
better feasible physical schedule for the combined treatment. It does not
isolate battery from shedding rescaling, prove that the remaining battery
cycling is necessary, certify global optimality, or establish a general
improvement. Cost coordinates alone did not converge within the bound. No
production defaults, tolerances or policies are promoted by this diagnostic;
no further numerical calls were launched.

### Resources, postprocessing and verification

Four launches used **322.839 cumulative worker-seconds** against 720 allowed.
Each attempt was capped at 180 seconds; the timeout was disposed at 180.058
seconds because of polling/termination overhead. Peak sampled RSS was
2561.219 MiB against 16384 MiB. AC power, thermal sampling and RSS access passed
before launches; no experiment worker remained after completion.

After all solves finished, the committed analyzer encountered a field-name
error: the common audit exposes `residuals`, not `checks`. This did not affect
workers, acceptance or their archives. The exact original analyzer is retained
at `comparison_002/source-snapshot/analyze.py`. A separate ignored
`analysis_recovery.py` corrected only the field lookup, used an absolute
experiment import, and recorded its own hash. It independently replayed all
completed evidence while the numerical source snapshot was still clean, then
generated `analysis.json` and both figures. No optimizer was called by this
postprocessing recovery.

Independent scientific review verified source/settings/input/start alignment,
archive/resource/audit replay, physical cost and SoC reconstruction, device axes,
both figures, control reproduction, and inferential limits, with no material
findings. The durable analyzer correction and a non-solving summary regression
are now included for owner review; 46 related tests and Ruff pass. These
post-run reporting edits are not represented as the executed clean snapshot.
The runner's strict source/commit gate intentionally rejects a later edited
checkout; verified replay was retained before edits, not bypassed afterward.

Selected SHA-256 anchors (relative to `results/comparison_002/`):

| Artifact | SHA-256 |
| --- | --- |
| binding.json | `191ecf08f406ad211db9c8435035c0694f84994a34c2f3c36c24adabb995da8b` |
| call-004/result.json.gz | `6aab58f310fcbbacf352db0c66f5f2fb2b6a98c138e7cd5225d305e6e483ba79` |
| analysis.json | `5cbbbb285660e7c730f3a3a1b4574be27a2341a28f54bcaae3f64f9203db7dc8` |
| replay-checkpoint.json | `cd4290ecae72c4aa058199d66eb803c6356e0cd9b8223d62724c920f464c6c70` |
| analysis_recovery.py | `9d5fd64bd32c958e8fc54f59f0af9be18d9bffcee252fe6a66ff482b625f0fb2` |

No files are staged or committed by the agent. Raw evidence remains ignored.

## Prior startup checkpoint: comparison_001

At the earlier 2026-10-07 startup checkpoint, the bounded comparison was **not completed**. No IPOPT
optimization occurred and no arm is accepted. Production defaults, economic
weights, physical constraints, and historical run artifacts are unchanged.

## Implemented diagnostic

The four-arm protocol compares original and cost-valued coordinates, each with
unprepared AC and combined device normalization/exact-fixed elimination, on
Tracy global hours `[1165, 1168)`. Signed battery dispatch and shedding fraction
are replaced by exactly invertible cost coordinates; CVXPY constructs its own
absolute-value auxiliary. Physical starts, costs and all constraints are
preserved, and extracted values are inverse-mapped before physical auditing.

Non-solving checks passed at both historical AC dispatches and synthetic signed
dispatch/shedding points. Tests confirm automatic cycling-auxiliary and shedding
objective gradients of one, retained fixed-box identities, archive round trips,
and the correct initial/end-state SoC plotting convention. The prospective
economic accounting gate is `1e-4 + 1e-6*abs(physical_cost)`; no historical
acceptance gate or disposition is rewritten.

## Retained startup failure

Raw local evidence: `results/comparison_001/`. HEAD was
`92bf2ad89c3da71ec80a2c786dd2a7dd9f71e94d`; the working tree was explicitly
recorded as uncommitted and its source/package hashes bound before launch.

The first worker reached the verified canonical-start callback. The reused
`atomic_gzip_json` writer publishes the file and then requires an integer
`iteration` field. The new callback omitted that field, so the callback raised
before the helper reached IPOPT. The same omission in the result payload caused
the worker to exit while archiving the exception. Both gzip files exist, but
there is **no completion manifest**, no native result, and no IPOPT iteration
log. Those files are not accepted or completed numerical evidence.

Observed supervision: one process launch, exit code 1, 3.080675208 worker-seconds,
three RSS samples, peak sampled RSS 265.46875 MiB (16 GiB ceiling). AC power and
thermal sampling passed before launch (CPU 45.29°C, GPU 47.92°C). No later arm
was launched. A read-only process check confirmed no experiment worker remained.
The retained `optimizer_calls=1` counts entry into the shared solve helper,
**not** execution of IPOPT: the start observer raised before its numerical call.
The original parent incorrectly reported `outcome="exited"` with success exit
status; supervision and the worker log establish the failure independently.

All original experiment sources were copied into
`results/comparison_001/source-snapshot/` before repair, and checked byte-for-byte
against the original binding hashes. The original invocation, archives, logs and
supervision remain unchanged. Selected SHA-256 anchors:

| Artifact relative to comparison_001 | SHA-256 |
| --- | --- |
| binding.json | `4e99d468b1aa42cf3e4c3471a3aa10016571d0373821d11285709376322c2875` |
| call-001/x0.json.gz | `3829dcd888cadfa250fb56d62c46e4bac401d9eea83ea8834acc0d555d8c8d5b` |
| call-001/result.json.gz | `00bfeb2d34a05c14f9db5f3c1c42e6a390009645255e772afca69dce78b337f3` |
| call-001/supervision.json | `eea36b22aa2649a6e8f512e2e7d1dbc41ece24b02a291928f96fb27998420f10` |
| call-001/worker.log | `893dc12110118d21dc71f4a9faca091fd1f0df256e731099f0860000c1555b74` |
| source-snapshot/run.py | `446f339c3a621ac1517a5e3f53263edbdb40b9fe8ff406a2b23166e2a7ca1b08` |

## Corrected checkpoint and next execution

The runner now includes `iteration` in both gzip payloads. A non-solving mocked
worker lifecycle test exercises the actual start/archive/completion writer
contract and verifies artifact hashes. Failed child exits now produce
`worker_failure` and a nonzero parent exit code. No numerical transformation or
solver control changed in this repair.

Verification: 19 experiment tests plus 26 existing matched-variable/plot tests
passed (45 total); Ruff and corrected non-solving preflight passed. All 256
retained files in the historical qualification and matched-convex runs still
match the pre-task hash snapshot. Independent pre-execution review was clean
after correcting the SoC plot; the startup repair and this report also passed
independent review with no remaining findings.

At that checkpoint, a fresh execution required owner authorization under the
no-retry protocol. The owner subsequently committed the correction and approved
the separate `comparison_002` invocation above. `comparison_001` was not resumed,
overwritten or reclassified. No numerical efficacy inference was available from
that startup failure itself.
