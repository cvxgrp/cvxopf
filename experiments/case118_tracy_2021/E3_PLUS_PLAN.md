# E3+: the existing 24-hour forced-shedding case

Owner decision, 2026-10-08: use the previously prepared forced-shedding T=24
fixture unchanged as the sixth comparison case. This is a counterfactual
stress variant of the large-deficit interval, not a newly selected observed
window. The completed five-case prepared E3 cohort remains unchanged.

## Physical fixture: reuse, do not recalibrate

Use global input hours **[1165,1189)**, T=24, delta=1 hour. Preserve the
existing generator, renewable, storage, network, shedding price and permission
data, `rho=1/3`, battery throughput cost `0.01`, and per-device 50%-initial,
50%-hard-terminal SoC. Do not add a depletion variant.

The retained objective-assembly qualification binding identifies the fixture:

- Reference: `../objective_assembly_qualification/results/qualification_002/binding.json`.
- Binding SHA-256: `4d4ec1cb451207f4d0b0a17047258f902110c886bc73513ec3a1a152248b2d1f`.
- T=24 forced-shedding rows 25–32; all share the same demand multiplier
  **7.5416905141946184**.
- The unchanged modified-input witness has positive required shedding in
  every hour and an integrated ENS lower bound of **439252.83068650117 MWh**.

Resolve the retained fixture and compare reconstructed demand, supply inputs,
device identities, boundary targets and physical configuration against its
frozen records. Compare physical inputs with numerical representation removed;
historical hash schemas and representation policies need not be identical.
Bind the original and modified demand frames, exact multiplier and witness.
Do not choose a new multiplier, retune the stress severity or choose inputs
using solve outcomes. The optimistic supply bound proves unavoidable shedding,
not network feasibility or optimal shedding.

## Four new arms, not a rerun of the original twenty

Run copper plate, lossy DC, SOCP and AC on the same forced-shedding inputs.
Use the current prepared E3 production representations and fixed solver
settings from [the prepared protocol](E3_PREPARED_PROTOCOL.md):

- AC: device normalization, exact fixed-coordinate removal, cycling/shedding
  cost coordinates; the complete component-first recovery phase precedes the
  hourly phase.
- SOCP: normalization, exact removal and joint5; original economic coordinates
  and hourly assembly.
- Both DC formulations: exact removal and joint5; original economic
  coordinates and hourly assembly.

Reuse the existing prospective start construction, recovery roles, source
eligibility rules and independent native restoration/audit machinery. Do not
promote old qualification solutions to new E3+ acceptances or choose a favorable
historical start. Initialization must be declared before execution; causal
sources must belong to the declared E3+ recovery history, not the completed
ordinary E3 cohort. Target-free outcomes remain diagnostic only and cannot
complete a hard-target arm.

Keep the existing physical and economic acceptance checks and advisory cycling
warnings. Additionally require measured ENS to be positive and at least the
retained shortage lower bound, with the existing 1e-4 MWh witness allowance.
This is a witness check, not a substitute for network/device feasibility.
Preserve rejection, timeout, unfinished and one-sided outcomes without relabeling.

## Evidence and implementation boundary

Fresh output: `results/e3_plus/`. Do not create execution artifacts until launch
approval. Do not resume, edit or append to `results/e3_prepared/`, historical
`results/e3/`, or any qualification directory. Stage D remains held.

Keep implementation in this Tracy experiment and focused tests. Extend the
existing supervisor with an explicit, immutable E3+ cohort selection rather
than copying its journal/resource/recovery implementation or monkey-patching
module globals. Bind the physical fixture reference, protocol, clean source,
environment, policies and starts prospectively. Retain the same result/native/
preparation archives, completion hashes, supervision and resource evidence.
Independent replay must reconstruct this forced fixture, not ordinary demand.

Non-solving tests must cover exact fixture preservation, input matching,
shortage-witness enforcement, cohort selection and output isolation, recovery
source eligibility, replay and artifact consistency. Guard optimizer entry
points; no numerical reruns are authorized by this preparation checkpoint.

Once evidence exists, expose this sixth case in the E3 marimo viewer with a
distinct E3+ provenance label. Keep the original five cases accessible; do not
require all four new arms to accept before displaying completed one-sided
outcomes. Plot only independently eligible accepted trajectories, with missing
or rejected arms clearly identified. Cost/trajectory comparisons require the
relevant accepted arms. No numerical failure proves infeasibility; SOCP
acceptance does not prove AC realizability or a certified global optimum.

## Approved resources and launch checkpoint

Reuse the most recently adopted E3 wall/RSS/total ceilings, with a launch
allowance sized for this extension. The owner approved these limits for runner
preparation on 2026-10-08; they are not launch authorization:

| Resource | Approved E3+ ceiling |
| --- | ---: |
| Wall time per complete attempt | 3600 seconds |
| Sampled worker RSS | 16384 MiB |
| Cumulative supervised worker time | 43200 seconds |
| Launches | 28 |
| Resource sampling interval | 1 second |

There are at most 23 ordinary slots (three convex calls and twenty AC slots,
including phase-specific target-free diagnostics), plus five interruption
allowances. The independent total budget may censor the ladder before all
slots are used. Final attempts are clipped to remaining time and termination
overhead is charged. No automatic budget extension or extra retry is permitted.

Review, an owner commit and separate launch approval precede optimization;
process/RSS and required power/thermal monitoring must work before launch.
The existing notebook first pass remains available while E3+ is prepared.
