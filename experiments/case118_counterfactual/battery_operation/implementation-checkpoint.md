# Battery operation in AC and DC: implementation record

This is the pre-execution implementation record. The owner subsequently
authorized commit `a0575f2` and execution from that committed source. The first
12 comparisons are complete and independently reviewed; see [results](REPORT.md).
The uncommitted-source alternative described below was not used.

Scope: first 12 primary solves only (six serial DC F/B, six AC G/B with existing 2+1 recovery). Stops for owner review before prescribed transfers. G is the AC fixed-battery F arm. Exact launch inputs are in protocol.json.

New files:
- experiments/case118_counterfactual/dc.py: public DC models, independent physical/device/input/lock checks, generation/storage/native loss-proxy accounting, one-solve worker.
- experiments/case118_counterfactual/mechanism.py: serial DC orchestration and AC G/B handoff, partial-result preservation, cumulative budgets, optional immutable reviewed-source snapshot.
- tests/test_case118_battery_mechanism.py: analytic audit corruption/unit tests, DC worker extraction, incumbent selection, G/B admission, actual non-solving serial subprocess/receipt/handoff test, source snapshot identity tests.
- plans/case118-toy-battery-mechanism-test.md: selected episodes, matching, transfer proposal, budgets, interpretation and phase gates.

Changed files:
- experiments/case118_counterfactual/model.py: shared independent device-cost reconstruction; original calculations unchanged.
- experiments/case118_counterfactual/runner.py: explicit optional G/B arm chain; source fingerprint includes new nonignored runtime modules; original R1/R2/G/B default and clean-tree CLI gate retained.
- experiments/case118_annual_hierarchy/s5_speculative_process.py: optional DC phase names and distinct non-speculative DirectCompletion evidence; AC starting-vector requirement unchanged.
- tests/test_case118_s5_speculative_integration.py: exercise actual process return/reaping with both AC/DC phase labels.
- experiments/case118_counterfactual/README.md: phase-one execution/accounting/checkpoint description.
- plans/case118-toy-ac-analysis-design.md and plans/case118-tracy-2021-study-plan.md: link the bounded mechanism study into Stage0c.

Verification:151 targeted tests passed in39.94seconds; ruff and git diff --check passed. All six exact Case118 DC models were built read-only and verified DCP; no new native Case118 study solves have run. Independent review identified an AC-specific completion contract being incorrectly applied to DC; corrected with DirectCompletion and covered by the real-process analytic regression.

Execution options considered before launch: the original commit checkpoint or an explicitly owner-approved --snapshot-reviewed-worktree route. The latter retains complete reviewed runtime source bytes/hash, base commit, tracked diff, and untracked review files without staging/committing. Final independent SCIENTIFIC/software rereview was CLEAN; reviewer independently reproduced all151 tests and snapshot hash/byte preservation checks. The owner chose the commit route, as recorded above.

Suggested commit subject: feat(case118): add matched AC/DC battery mechanism comparisons

Suggested body: Add fixed/free battery comparisons for the three selected toy episodes, with independent DC feasibility and cost accounting. Reuse the AC G/B recovery path, retain cumulative resource usage and exact execution provenance, and stop before prescribed-transfer tests for result review.
