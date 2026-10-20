# Hour-2452 comparison: all four outcomes

The approved four-case comparison is complete. Exactly four single-hour AC
solves ran: two inherited completed solves and two previously unlaunched cases.
No solve was repeated. `results/slice_002` combines immutable predecessor
`results/slice_001` with global calls 3 and 4. Unchanged production commit:
`b11c4418fbd8f81294a1a26a1c053bb896f3b216`, with separately hashed uncommitted
diagnostic scripts. Earlier study artifacts and production sources are untouched.

| Pinned battery schedule | Outcome | IPOPT iterations | Supervised worker seconds |
| --- | --- | ---: | ---: |
| Retained accepted AC | Independently audited accepted | 80 | 14.364636124984827 |
| SOCP | Native rejection (status 2) | 629 | 40.02151908300584 |
| Lossy DC | Native rejection (status 2) | 529 | 39.69970887499994 |
| Copper plate | Native rejection (status 2) | 849 | 64.15869524999994 |

Only the original AC control is independently accepted (1/4 total, 0/3
donor transfers). The continuation finalized `four_case_complete` with no
exception. All four numerical attempts finalized with verified manifests,
resource journals and successful process supervision. There were no timeouts,
RSS ceilings, new audit failures or retries. The original bookkeeping failure
remains preserved separately; it is not an extra numerical attempt.

Total supervised worker time is 158.24455933299055 seconds: inherited
54.38615520799067 plus local 103.85840412499988, below the unchanged
1200-second cumulative ceiling. All four are below 300 seconds each; maximum
sampled process-tree RSS is 1098.28125 MiB, below 8192 MiB.

The AC control exited `Optimal Solution Found.` Fresh native replay agrees
exactly with its retained unchanged physics, cost-accounting, power-pin,
implied-energy and one-hour endpoint audits; all pass. Economic objective
7793.989383844578; shedding 5.945142867840258e-12 MWh. This establishes
only this pinned hour's feasibility, not a global optimum or full-day result.

All three donor-pinned AC solves exited exactly:
`Converged to a point of local infeasibility. Problem may be infeasible.`
None is an infeasibility certificate or accepted physical point. Their retained
native primals, constraints, multipliers and preparation evidence remain
available, but no passing engineering-unit physical audit is asserted.

## Final IPOPT summary residuals

Each cell with a slash is scaled / unscaled. These are final solver-summary
residuals, not restoration-phase iteration columns or the independent
engineering-unit physics audit. Constraint violation is identical in both
reported columns.

| Schedule | Constraint/primal violation | Dual infeasibility | Complementarity | Overall NLP error |
| --- | ---: | --- | --- | --- |
| AC control | 2.2410858502297515e-8 | 1.1085111495390265e-8 / 1.3810005272437700e-6 | 2.6943470075258982e-11 / 3.3566596416448047e-9 | 2.2410858502297515e-8 / 1.3810005272437700e-6 |
| SOCP | 0.93495431027790765 | 99.999999939323374 / 12458.156392744570 | 1.0000177626702329e-9 / 1.2458377690427608e-7 | 99.999999939323374 / 12458.156392744570 |
| Lossy DC | 0.80401654888756990 | 99.999998755123187 / 12458.156245215057 | 4.0719329293576728e-7 / 5.0728777285484925e-5 | 99.999998755123187 / 12458.156245215057 |
| Copper plate | 0.98432298224709491 | 100.00000017352230 / 12458.156421921438 | 4.5499902789711864e-7 / 5.6684490515284770e-5 | 100.00000017352230 / 12458.156421921438 |

The control's independent audit reports real/reactive AC balance residuals
1.8201581042376348e-9 and 2.241084985143971e-8 per unit, respectively (gate
1e-6); zero battery-circle excess; power-pin difference 7.105427357601002e-15
MW; implied-energy difference 1.4210854715202004e-14 MWh; hour-2453 endpoint
difference 1.1368683772161603e-13 MWh. Its cycling warning is false.
Its bus-70/71/72/73 voltages are 1.0480132052403235, 1.0599999999936778,
0.9400000000101445 and 1.0594824081644696 per unit. The tightest sending-end
branch is 54–56 at approximately its 155-MVA rating; the tightest receiving-end
branch is 70–71 at approximately its 166-MVA rating. No penalties, gates or
physical limits were changed to obtain this result. Control battery reactive
dispatch and full arrays remain in its original immutable result archive and
the combined read-only report.

## Bookkeeping defect and authorized correction

The runner's worker archived the native rejection check as
`{passed: false, reason: "native numerical rejection"}`. The existing independent
replay helper returns the same check plus `native_status: 2`. The runner's exact
dictionary comparison therefore raised `ValueError: independent audit differs`
before case 3. This is a diagnostic bookkeeping defect, not disagreement about
an accepted physical result. The immutable root finish record preserves the
failure. Both workers have finalized successful process supervision, verified
resource journals, and independently verified completion-manifest hashes.

A read-only examination reproduced this exact missing-field difference while
independently confirming the control's full audit and unchanged bindings. The
owner then explicitly authorized the correction and only the two unlaunched
cases. The worker now retains `native_status`. Legacy compatibility applies
only to the exact rejected predecessor call-2 check and can never accept it.
The original scripts/protocol are retained under `results/slice_001-source`,
with their hashes bound into the new continuation. The mathematical model,
all four slice/start identities and the production numerical environment are
unchanged across the correction. The old records, failure and budgets were
not overwritten, relabelled or reset.

## Verification and disposition

94 focused non-solving tests passed, including the missing-field regression,
unchanged-prefix replay, carried-forward worker budget and duplicate-launch
guard; lint and formatting checks passed. The final fresh read-only status
command succeeded, independently reaudited the control and reconstructed all
four finalized classifications. It verified the predecessor and source-snapshot
hashes, completed evidence, resource journals and earlier experiment hashes.
Process inspection confirmed no execution supervisor or numerical worker
remains. New raw evidence comprises only call-003 and call-004 in slice_002;
calls 1 and 2 are read directly from slice_001, not copied or rerun.

Reproduce the combined read-only report, without optimization:

```sh
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python -m experiments.tracy_battery_transfer_slice.run --status --output experiments/tracy_battery_transfer_slice/results/slice_002
```

The donor attempts also fail natively in this isolated hour, so difficulty is
not observed only in a 24-hour solve. This test does not identify a physical
infeasibility certificate, rule out other AC operating points, or establish
full-day compatibility. Source E3/E3+, historical Stage D and all prior
transfer/recovery/feasibility outcomes remain untouched. Changes remain
uncommitted in this experiment; no production code, Git stage or commit changed.

This report does not authorize further numerical execution, retries, other
hours, schedule modifications, source-result relabelling, staging or commits.
