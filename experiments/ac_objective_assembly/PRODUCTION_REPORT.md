# Production AC objective assembly qualification

2026-10-08. The owner approved the reviewed public-option runner and committed
it as `5410e627e1cecfc80aa36d2c65cbc0fd6c4d1790`. The separately authorized
two-arm run completed in ignored `results/production_001`, under the unchanged
[production protocol](PRODUCTION_PROTOCOL.md). Both arms used the public builder
and `build.solve()`, not the experimental objective adapter.

## Outcome

| Assembly | Supervised worker seconds | Peak sampled RSS (GiB) | Outcome |
| --- | ---: | ---: | --- |
| Hourly (time-first) | 180.2023 | 11.2276 | Wall limit; no completed solution |
| Component-first | 135.3990 | 10.3588 | Accepted; advisory cycling warning |

Two launches and two finalized supervision records are distinct from **one
accepted solution**. There are no native/audit rejections; the hourly outcome
is a timeout, not an infeasibility finding. One result archive, one native archive
and one matching completion manifest are retained, all for component-first.
The hourly log, expected canonical start, resource samples and supervision are
retained, but no result/native archive or completion manifest exists for it.
Invocation outcome `matrix_complete` means both prescribed attempts finalized,
not that both were accepted.

The input interval was `[1165,1189)` (zero-based, half-open), T=24, delta=1 hour,
with neutral terminal storage and the approved demand multiplier
`7.5416905141946184`. Both arms kept normalized device limits, exact fixed boxes,
cycling/shedding cost coordinates, the same deterministic physical start and
IPOPT settings. Their mathematical-input SHA-256 was
`d6eacfdf56b8c286dd4e9b7f01995c0b89110155634dfa36e8bf28ced8b299f0`.

Component-first returned native IPOPT status 0. Independent physical checks,
transformation/start restoration, forced-shedding witness, result publication
and economic reconstruction passed. Its reconstructed physical objective was
`11852950486.055317`; the canonical objective was `11852950486.059948`.
Cycling epigraph excess and absolute slack were both `0.004628212372312357`,
retained as a warning under the approved protocol. The native-objective
reconstruction error was `1.9073486328125e-6`, below its `0.011853050486059948`
limit. No gate was loosened and no candidate was relabeled.

The retained `Pg`, `Qg`, `b`, `b_q`, `soc`, `p_load_shed`, `Vm` and `Va_deg`
arrays and complete economic audit match
`results/diagnostic_001/call-002/result.json.gz` exactly. This verifies production
reproduction of the successful experimental solution, not a new lower-cost
solution. The hourly arm has no accepted candidate, so there is no matched
two-accepted-arm cost or trajectory comparison.

Supervised worker time totaled `315.6012890409911` of 360 seconds, with two of
two launches used. The wall-limit record includes supervisor polling/termination
overhead beyond the 180-second request; it remains censored and unaccepted.
Component-first stayed within its remaining `179.79772666690405`-second request.
All sampled RSS values stayed below 16 GiB. Process, AC-power and thermal
telemetry passed before invocation and each launch. Sampled RSS is not a
continuous-memory guarantee. No retry, bypass, tuning or extra solve occurred.

## Evidence integrity and replay

The read-only production `--status` replay succeeded at the clean bound commit
before these documentation edits. It independently reproduced acceptance and
audits and verified the source/environment binding, retained starts, native
records and completion hashes. The following hashes pin the retained evidence;
raw artifacts remain ignored and historical records remain unchanged.

| Artifact under `results/production_001/` | SHA-256 |
| --- | --- |
| `binding.json` | `8c9ed5203a9789528cb62905b4ee564b18a646da89f7c8da7d43a98e6cd290ef` |
| `protocol.json` | `e9e92cd208996648d51b52d1d5944739e86492d007647844cf7be5804207e82a` |
| `invocation-start.json` | `6bc0f875b9575d5c386ab8004ab5c0d850891c4d855a5ff2cb40e6c8f14da678` |
| `invocation-finish.json` | `b788d398e42901590d19c8f98cbc6849a302eac3bf35d176b2437bfa464336f1` |
| `report.json` | `37f8d6952a16b9faac0806a80b62f61b654c16dd79487c8eb6c3db567b00578b` |
| `call-001/supervision.json` | `d6f3d3368f5ad7acaf53c05251cf7b9ef71d46b2f5b937405436eed7211f39b4` |
| `call-002/supervision.json` | `04e0a44527bba2cea2fe85b6e7852c29bb0af0028e8b5fa4c13a712221eba344` |
| `call-002/completion.json` | `d084d7a6bda15e4373ad844b8edf9062aa08c7b2f3d0dc9bb33ee80c343ba848` |
| `call-002/result.json.gz` | `629cf59034da983649dd240d1f0d7e96d8378b4041874cfaa5fce8463f99bd36` |
| `call-002/native.json.gz` | `433ed994ac2eab4640e23fdbe7e8badd00764e165555283db16dfe61a38f4fc9` |

Future replay requires the identified execution commit, retained artifacts and
matching numerical environment, not the documentation-updated checkout or a
source-binding bypass. No historical protocol or run bytes were rewritten.

## Disposition

The owner closed this standalone representation/qualification checkpoint and
approved the prospective experimental recovery order: the complete component-
first ladder, then the previous hourly ladder if no independently accepted
solution is obtained. See the
[current policy](../../plans/numerical-preparation-api.md#closeout-and-approved-experimental-policy--2026-10-08)
for source dependencies, acceptance, failure stops and explicit budget requirements.
This documentation does not implement or launch that combined ladder.

The public default remains `hourly`; component-first is opt-in and currently
requires standalone vectorized AC with cost coordinates. Shorter AC cases did
not show a consistent timing advantage. Grouping and induced canonical ordering
change together, so their causal effects are not isolated. This is not evidence
of universal speedup, global optimality or a project-wide cost-coordinate rule.
Convex formulations keep original economic coordinates. Remaining SOCP accuracy
and relaxation questions, prepared hierarchy and docs-site work are separate;
E3 and Stage D remain held.
