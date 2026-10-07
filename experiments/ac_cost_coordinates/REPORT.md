# Cost-coordinate diagnostic: startup checkpoint

2026-10-07. The bounded numerical comparison is **not completed**. No IPOPT
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

The declared protocol forbids automatic retries/extensions. A fresh four-arm
execution therefore needs owner authorization and a new source binding/output
directory; do not resume or overwrite `comparison_001`. The current source gate
intentionally does not replay that earlier snapshot as if it were today's
code. No inference about the effectiveness of cost scaling is possible yet.
Nothing is staged or committed.
