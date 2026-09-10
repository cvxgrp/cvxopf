# S5 validation-cost correction and proposed continuation

Status: controlled interruption complete; correction under review; restart not
authorized by this record. No scientific inputs, solver settings, acceptance
gates, timeout budgets, shard boundaries, or concurrency are changed.

## Retained execution boundary

The owner requested a controlled pause on 2026-09-10 after identifying growing
between-window validation time. SIGTERM was sent to the S5 root supervisor via
its supported catchable interruption path. The supervisor joined both workers
and retained `supervisor_interrupted` supervision and root outcomes. No study
worker remained live afterward. The periodic monitor was paused.

Original execution commit: `41abf63a2b6c76e0dbd02255237bf3c7ec2097d7`.
Original execution-source fingerprint:
`d467706698ea006352d5f2683f1d84aadb5e60837f88111ad688058bd3191383`.
All paths below are relative to the ignored execution directory
`results/s4b_annual_ac/`.

| Retained record | SHA-256 |
| --- | --- |
| `shard-000/checkpoint.json` (254 completed; next global interval 254) | `9979ae7009193068341cecbfbf6f97c05da905ddf725fc247799dd24bde83181` |
| `shard-001/checkpoint.json` (251 completed; next global interval 933) | `25b7ed23cb5512c0a99e34c3432c33e125cc7c5c86e9be2ba406635100b28450` |
| `progress.json` | `fae23cddb235069c44fa016e926deb542a3a6eb3542535d5a8a3203d9c6d11b2` |
| `root-outcome-000.json` | `8f144c28100b7b4b8fd3bfd7184b51cbed4dfe7df16fdbe1325cb8c1c537b545` |
| `supervision-wave-000-000.json` | `b66e46096db290ecc914a86d6686460a30d3bcce9916d68c1cb6448a7c968dca` |

The 505 accepted intervals remain intact. The two worker exit codes are 1 from
the recorded intentional interruption, not newly inferred solver failures.

## Limited correction

Previously each archived window rebuilt the complete annual signpost mapping;
each of its 8,761 lookups also rehashed the complete signpost array. Now one
integrity check precedes direct, identity-aligned materialization, and each
shard-validation pass reuses that detached mapping across its archives.
There is no persistent cache across passes. Archive hashes, dimensions,
acceptance gates, controller identities, and physical state chains are still
checked in full.

A read-only check after the correction validated all 254 and 251 retained
windows through `verify_shard_artifacts()` in 5.10 and 5.41 seconds respectively;
annual mapping construction took 0.00314 seconds. These are one-off local
validation timings, not solver speedups or an authoritative execution benchmark.
No OPF solve was needed for this retained-archive check.

Verification: three added regressions pass (single-check exact annual mapping,
signpost drift rejection, and per-pass reuse with archive corruption rejection).
The affected S4b/S5/streaming suite reports 137 passed and one pre-execution-only
failure: `test_default_output_and_numerical_authority_remain_absent` asserts
that the now-populated S5 output directory does not exist. No artifact was
removed to satisfy that obsolete launch-state assertion. Ruff lint/format,
strict mypy with explicit package bases for both changed production modules,
documentation artifact-hash checks, and `git diff --check` pass.

## Reviewed source-version continuation still required

The existing `--reviewed-resume` path intentionally permits only matching
execution contexts and source fingerprints. This correction must not bypass
that check, rewrite the old checkpoints to impersonate new execution, or replace
the original authority. This document is not a machine execution authority.

Before restarting, review and commit the correction, then explicitly bind the
old execution/authority and retained checkpoint hashes above to the exact new
clean execution commit/source fingerprint in a separately reviewed source-version
continuation contract. Root, shard-worker, and final-analysis provenance must
all preserve both execution segments. Resume only the incomplete wave at global
intervals 254 and 933; do not discard or rerun accepted intervals. Record the
validation-only change and its scope while retaining cumulative resource/time
evidence. Restart and regular monitoring follow only after that gate is ready.
