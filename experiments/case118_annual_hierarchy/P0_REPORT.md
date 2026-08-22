# P0 streaming-runner closure report

P0 is complete. The authoritative consolidated execution returned
`advance_to_s2` with no failures.

## Execution identity

- Execution-source commit: `81b31894e70ef3c13720b5f44aeddcff6f70bd71`
- Source state at gate start: clean (`git status --porcelain` was empty)
- Combined execution-source SHA-256:
  `0b79859fc0726777e9a00b1b79ab939c507ed15b182b4c944b8a6f2972152b67`
- Result artifact: `P0_RESULTS.json`, 6,702 bytes
- Result SHA-256:
  `6bd6b65b7475bd0ac00ad1e3070b00ac687fbdeaa9f3c929797a9f8df5818198`
- Gate wall time: 22.97 seconds

`P0_RESULTS.json` is the normative machine-readable record. It retains every
sub-gate report, source-file hash, runtime, and the empty failure list.

## Results

Both nominal public-versus-streaming comparisons completed without a mismatch:

| Horizon | Completed intervals | Attempts | Controlling slots |
|---:|---:|---:|---|
| 6 hours | 6 | 54 | slot 0 in every window |
| 24 hours | 24 | 216 | slot 0 in every window |

All seven injected lifecycle cases matched the public M17 controller. They
covered primary acceptance, copied-target-free recovery, target-free and
causal perturbation recovery, certified infeasibility, distinct solver-error
and unusable-primal handling, and complete recovery exhaustion. Every injected
unsuccessful-result payload passed its independent evidence check.

The persistence gate stopped at the declared two-interval observer boundary,
resumed through six intervals, rejected altered window/checkpoint/resource
artifacts, retained the previous checkpoint across an injected publication
failure, recovered the zero-interval boundary, reconstructed the preceding
45-variable causal source, and verified release of all retained outer and AC
builds.

The retrospective S3b copied-recovery artifact and both Case118 S1 network
records were locally available, hash-verified, and exactly re-derived. All four
S1 accepted outer/endpoint audits were independently reconstructed. The two
direct 24-hour AC records remained explicitly unexecuted under the S0 resource
gate. The production streaming dependency closure contained no forbidden
private-controller or M17-experiment import.

## Interpretation and advancement

P0 establishes that the experiment-owned streaming runner reproduces the
public M17 orchestration contract on the frozen compact cases, retains and
recovers its audit state correctly, and applies the same machinery to the
reviewed Case118 outer/endpoint boundary. It does not establish week-, month-,
or year-scale computational viability; those are the scientific questions for
S2 and later stages.

The P0 gate therefore authorizes S2's frozen one-week Case118 hierarchy run.
