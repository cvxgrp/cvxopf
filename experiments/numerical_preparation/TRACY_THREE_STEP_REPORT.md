# Matched Tracy T=3 variable-consistency results

Executed 2026-10-07 under the [three-call protocol](TRACY_THREE_STEP_PROTOCOL.md),
at clean commit `92bf2ad89c3da71ec80a2c786dd2a7dd9f71e94d`. This report is a
post-execution assessment, not permission for more calls or default adoption.

## Outcome and what it means

All three prepared convex outcomes are independently accepted: SOCP, lossy DC,
and copper plate. Canonical-to-restored and canonical-to-public comparisons
have **zero discrepancy for every checked physical variable**. Initial storage
boundary errors are at most `4.55e-13` MWh. Both retained AC candidates also
exactly reproduce their checked public variables from archived IPOPT coordinates
and pass the independently reconstructed stated-model physical audit. Their
separate canonical-cost gates still fail; historical evidence is not rewritten.

Thus this comparison found no coordinate restoration, unit conversion, transpose,
storage sign/boundary, or device-order inconsistency in these retained physical
variables. It does **not** establish equality of dispatch across network models,
global AC optimality, or absence of all possible implementation defects. The
AC canonical-objective discrepancy remains a separate unresolved numerical/
accounting issue. These three calls are not additional AC qualification or
baseline-versus-prepared performance comparisons.

## Exact matching and provenance

All five candidates use the same global input hours `[1165,1168)`, T=3,
delta=1 hour, 54 generators, 27 selected storage devices, original ND and load
identities, rho=1/3, throughput cost 0.01, optional shedding, and their own
neutral terminal equality at hour 1168. Initial and terminal fleet SoC are
`7023.2789219949` MWh; every device has a terminal equality, not just the fleet.
This is not a first-three-hour slice of a 24-hour optimization.

Resolved mathematical inputs match exactly after stripping preparation and
excluding only formulation. Common input SHA-256:
`c97d3e5c49484e910b9484a3c7700bfb24816104b02998e3952af02703581e46`.

SOCP uses `prepared_socp`: normalization, exact fixed-coordinate removal,
`joint5`. Both DC models use `prepared_dc`: exact removal and `joint5`, without
device normalization. Each uses its qualification fixture's frozen stock
CLARABEL 0.11.1 QDLDL settings. The original canonical dimensions are 3171,
1701, and 1143 respectively; each removes exactly 135 selected fixed coordinates.
There is no solver monkey-patching, AC solve, recovery call, or retry.

Raw evidence is ignored under `results/tracy_three_step_001/`; the completed
invocation is `invocations/invocation-1791395820053736000`. The complete five-way
aligned arrays, identity axes and all ten pairwise device/hour delta arrays
are in `report.json`. Individual archives retain the full original canonical
primal, original-leaf layout, restored raw values, preparation evidence, public
results and independent audits. Completion manifests agree with retained archives.
All 226 files in `results/qualification_001/` were hash-compared before and after
execution and are byte-for-byte unchanged. E3 and Stage D were not resumed.

| Retained file (relative to new execution root) | SHA-256 |
| --- | --- |
| `binding.json` | `06d231f5771bee48c91a8eb54bac06d641fe05a1760b70a756aa7ef2eba17d1c` |
| `protocol.json` | `a43e3a8b74ea25a642dbe1f87d636edb095dddc2a637ae61ac93ec217d88d9a0` |
| `call-001/result.json.gz` | `d2aa22346cf8eb1b737ece36dd58cdb2c6083d03fca2592adec270e58df941f0` |
| `call-002/result.json.gz` | `546617665ed0f68b74c6bec7729d5e5caa8c65875278be8e93c21b60dbccc7e2` |
| `call-003/result.json.gz` | `fe7255e76a8553f7a54c9bdaecd8a89d5ff3162e6c73aaa0fa8807b34e1575ca` |
| `report.json` | `442d98184ca10fdd3f86a2aa9845a227af76a2a9825174d7f154c0afd03d1425` |

Historical AC inputs/result/x0/request/completion/supervision hashes are pinned
in the new binding. AC call 024 result SHA is
`a567d6497572e78bdf4df4581beb7f461581436dc9a309bcae763be984f6190c`;
call 025 is
`fb7ea17d25ee1dff5a48187f185a37d57cfb1109a248dda2f84337c2befef7fd`.

## Within-formulation variable checks

Fresh canonical layouts follow original variable identities through the CVXPY
inverse chain, including DC attribute-to-constraint variable replacement.
Native reduced primals are expanded with archived scales and fixed values.
Every original convex leaf agrees with its retained restored model value.
The independently computed public projection uses time-last to time-first
transpose, base-MVA multiplication for Pg/Qg/DC branch flows, unchanged
engineering units for storage/ND, and exclusion of the initial SoC boundary.
Historical AC uses its retained canonical start layout to locate final primal
coordinates; combined-AC fixed coordinates are expanded before projection.

| Candidate | Checked public primal coordinates | Mapping result |
| --- | --- | --- |
| SOCP | Pg, Qg, b, b_q, soc, p_nd, q_nd, shedding fraction, w, W_re, W_im | Exact agreement |
| Lossy DC | Pg, b, soc, p_nd, shedding fraction, p_flows | Exact agreement |
| Copper plate | Pg, b, soc, p_nd, shedding fraction | Exact agreement |
| AC baseline / combined | Pg, Qg, b, b_q, soc, p_nd, q_nd, shedding fraction, Vm, Va_deg | Exact agreement |

All expected array shapes and common device/hour axes pass. DC reactive primal
channels are absent, not numerical zero; copper-plate `p_net` is a T-vector
aggregate, not a bus-by-hour matrix. Public bus injections, served/shed load
reporting, curtailment and branch-terminal reporting are checked separately by
the independently reconstructed original-unit physical audit.

Every new outcome passes generator/ND/storage bounds, storage recurrence and
per-device terminal targets, applicable apparent-power capability, load/ENS
reporting, the formulation's own network balance/limits, and component plus
total cost accounting under unchanged protocol thresholds. Representative maxima:

| Residual | SOCP | Lossy DC | Copper plate |
| --- | ---: | ---: | ---: |
| Real balance (MW) | 1.81e-12 | 1.85e-13 | 2.44e-12 |
| Reactive balance (MVAr) | 1.14e-11 | Not modeled | Not modeled |
| SoC recurrence (MWh) | 3.85e-13 | 2.31e-13 | 2.78e-13 |
| Terminal SoC (MWh) | 4.55e-13 | 2.27e-13 | 2.27e-13 |

Canonical original-space primal residuals are at most `7.22e-13`; objective
reconstruction errors are at most `8.88e-16`. These transformed-coordinate
diagnostics are not substitutes for the original-unit checks above.

## Dispatch consistency versus dispatch equality

All five candidates serve essentially all `9935.199347732` MWh of demand.
Tiny positive ENS remains a numerical quantity, not exactly zero. The economic
and dispatch summaries below are computed from the aligned physical arrays;
storage throughput is `sum(abs(b))*delta`, counting charge and discharge.

| Candidate | Generation (MWh) | ND dispatch (MWh) | Storage throughput (MWh) | Physical total cost |
| --- | ---: | ---: | ---: | ---: |
| AC baseline | 7237.540995 | 2851.772834 | 619.605695 | 185493.526807 |
| AC combined | 0.000016565 | 10271.229496 | 531.121881 | 5.314001 |
| SOCP | 8.15e-11 | 14324.196837 | 7.68e-8 | 1.11e-8 |
| Lossy DC | 3.33e-16 | 9935.199348 | 6.77e-14 | 2.299552 |
| Copper plate | 5.58e-14 | 9935.199348 | 2.50e-10 | 1.75e-11 |

The lossy-DC total is essentially entirely its `dc_loss_cost=2.2995518400203094`;
generator/storage/shedding costs sum to about `5.31e-14`. Its quadratic flow-loss
proxy is an objective regularizer, **not a real-loss term in nodal balance**.
Thus both DC models' total renewable dispatch equals served demand up to roundoff,
despite different flows and per-device renewable allocations.

For the new convex outcomes, maximum individual real storage power is only
`5.17e-9` MW (SOCP), `4.11e-15` MW (lossy DC), and `1.82e-11` MW (copper plate).
Their per-device SoC trajectories are effectively constant. The combined AC
candidate instead has maximum individual `abs(b)=97.825801` MW and cycles
265.560940 MWh each of charging and discharging. Its fleet sum over hours is
`[-261.727818, 140.114538, 121.613280]` MW; per-device trajectories and endpoints
are still independently consistent. This does not prove storage cycling is
unnecessary for the stated AC network.

Maximum per-device/hour differences (descriptive, not pass/fail gates):

| Pair | Pg (MW) | b (MW) | SoC (MWh) | p_nd (MW) |
| --- | ---: | ---: | ---: | ---: |
| AC baseline − combined | 501.151189 | 90.783376 | 82.865363 | 928.937601 |
| AC combined − SOCP | 6.28e-7 | 97.825801 | 97.825801 | 396.459312 |
| AC combined − lossy DC | 6.28e-7 | 97.825801 | 97.825801 | 320.729014 |
| AC combined − copper plate | 6.28e-7 | 97.825801 | 97.825801 | 679.877604 |
| SOCP − lossy DC | 9.34e-12 | 5.17e-9 | 5.17e-9 | 337.381269 |
| Lossy DC − copper plate | 1.82e-15 | 1.82e-11 | 1.82e-11 | 491.059067 |

The two DC outcomes have the same fleet renewable dispatch but differ by up to
491.059067 MW for an individual ND device/hour. Copper plate has no network or
flow-loss regularizer; lossy DC has both flow constraints and the regularizer.
Different feasible renewable allocations in this unpriced channel are not a
unit or identity error. No common-variable trajectory-equality gate was imposed.

### SOCP's returned point is consistent but not rank-tight

SOCP's hourly net real injections are `[1565.261038, 1413.332229, 1410.404223]`
MW, summing to `4388.997490` MWh over the three one-hour intervals. The extra ND
dispatch is accounted for by the lifted network, not missing in the device
balance. Its independently checked relative edge-product gap reaches
`0.245923686`, exceeding the existing rank-product diagnostic tolerance `1e-5`.
This gap is distinct from feasible SOCP cone inequalities, all of which pass.
Non-rank-tightness of this returned point does not, by itself, establish an
optimal-value gap between the SOCP and AC problems; another relaxed optimum
could have different voltage products.

Consequently the cheap relaxed solution is **not demonstrated to be an AC
solution**. Its large lifted real losses and reactive dispatch must not be
presented as realizable full-AC operation. No voltage recovery or extra AC solve
was attempted. The comparison supports model-variable consistency, not physical
equivalence of the network formulations.

### Retained AC canonical-cost discrepancies remain separate

| AC candidate | Native canonical cost | Physical component sum | Absolute difference |
| --- | ---: | ---: | ---: |
| Baseline | 185502.634838 | 185493.526807 | 9.108030 |
| Combined | 121.190340 | 5.314001 | 115.876339 |

Both were classified as accounting-rejected under the **historically frozen**
`1e-4 + 1e-10*abs(physical cost)` gate. That overly tight relative term is not a
recommendation for current best practice. The newer total-accounting criterion
uses `1e-6` relative tolerance (adopted prospectively for prepared SOCP in the
integration plan). A descriptive application of
`1e-4 + 1e-6*abs(physical cost)` gives limits `0.185594` for baseline and
`0.000105314` for combined AC. Their discrepancies, `9.108030` and `115.876339`,
still exceed those limits. Thus this particular accounting disagreement is not
explained solely by the old `1e-10` relative term. This sensitivity calculation
does not change the historical protocol or reclassify either archive.

They are independently feasible physical candidates for descriptive comparison;
neither is certified as a global or local economic optimum by this assessment.
The unresolved auxiliary/canonical accounting discrepancy is separate from
the correctly mapped physical trajectories, and does not invalidate their
independently checked stated-model feasibility or prevent physical-cost ranking.

## Time-series dispatch interpretation

The non-solving [plotter](plot_tracy_three_step.py) reads the pinned report and
binding, verifies completion manifests and their retained public arrays, and
renders into the separate ignored directory `results/tracy_three_step_plots_002/`.
Power is constant over each one-hour interval; energy is shown at boundaries
1165, 1166, 1167 and 1168, including the supplied initial state. No smoothing,
resampling, optimization or historical gate mutation is performed.
The plot manifest records source and image hashes. Reproduction requires the
ignored retained archives and Matplotlib (the existing notebook extra):

```sh
MPLCONFIGDIR=/private/tmp/cvxopf-tracy-mpl uv run --offline --no-sync --extra notebook \
  python -m experiments.numerical_preparation.plot_tracy_three_step \
  --output experiments/numerical_preparation/results/tracy_three_step_plots_fresh
```

| Figure | Contents |
| --- | --- |
| [Fleet trajectories](results/tracy_three_step_plots_002/fleet.png) | All five methods: Pg, ND, gross storage charge/discharge, fleet energy and real network injection |
| [AC device trajectories](results/tracy_three_step_plots_002/ac_devices.png) | All 54 generator and 27 battery traces, shared scales between AC candidates; selected buses highlighted |
| [Every battery's real power](results/tracy_three_step_plots_002/battery_power.png) | 27 identity-matched small multiples, all five methods |
| [Every battery's SoC](results/tracy_three_step_plots_002/battery_soc.png) | All four boundaries for all 27 devices, expressed as percent of each capacity |
| [Every real-capable generator](results/tracy_three_step_plots_002/generator_power.png) | All 19 positive-P-capacity rows; the other 35 rows have fixed-zero Pg |
| [Reactive fleet trajectories](results/tracy_three_step_plots_002/reactive.png) | Generator, battery and renewable Q for AC and SOCP; DC channels are absent |

The baseline AC candidate supplies `2376.95`, `2422.29`, `2438.31` MW of
conventional generation. It uses less renewable power, charges all batteries
in the first interval and discharges them afterward. Combined AC supplies
only about `5.5e-6` MW of conventional generation in each interval and serves
demand predominantly from renewables. Its storage redistribution differs
markedly across devices, even though both candidates return **each battery**
to 50% capacity. Baseline's maximum device SoC is 63.748%, versus 56.478% for
combined; absolute MW and normalized energy views reveal different features
because the battery capacities differ.

Combined AC is not a "generators off" solution: conventional generator reactive
injection totals `776.86`, `813.31`, `831.56` MVAr, versus baseline's net absorption
`214.06`, `186.25`, `120.93` MVAr. Battery and renewable reactive allocations
also change substantially. There are no optimized binary commitment/on-off
variables in this experiment; the plotted quantities are continuous dispatch.
The 35 fixed-zero-P generator rows can still provide reactive support.

Gross storage flows matter: combined AC's second interval includes `3.833123`
MW of charging and `143.947660` MW of discharging on **different devices**.
Their net `140.114538` MW alone hides this spatial redistribution. This is not
simultaneous charge/discharge within a single ideal signed-power device.
The convex real-storage traces are effectively zero, but SOCP batteries provide
substantial reactive power; a flat real-power/SoC trace is not an idle inverter.

Both AC candidates use matched physical inputs and starts, but different
numerical representations. Their radically different economic costs are not
merely different trajectories at the same optimum. Combined AC is the cheaper
of these two independently feasible physical candidates (`5.314001` versus
`185493.526807`). Preparation changed IPOPT's returned dispatch, but these
plots alone cannot identify the solver-path cause or establish either candidate's
optimality. The convex renewable-backed solutions provide useful model
comparators, not proof that storage cycling is unnecessary under AC physics.
SOCP's unverified AC realizability remains as discussed above.

## Resources, verification and limits

Exactly three launches and three optimizer calls; three completed archives,
three completion manifests and three finalized supervision records agree.
No unresolved, interrupted or timed-out outcome. Total worker wall time is
`7.154451376` seconds against 540; preflight recorded AC power, 100% battery and
no recorded thermal/performance warning. No diagnostic worker remained afterward.

| Call | CLARABEL iterations | Native solve seconds | Worker wall seconds | Peak sampled RSS (MiB) |
| --- | ---: | ---: | ---: | ---: |
| SOCP | 16 | 0.045371 | 3.066586 | 166.5625 |
| Lossy DC | 22 | 0.012760 | 2.045385 | 158.3750 |
| Copper plate | 14 | 0.004183 | 2.042480 | 166.4375 |

Every native status is full `Solved`; no ceiling was reached. One-second RSS
sampling can miss brief allocation peaks, so these are sampled peaks, not exact
maximum memory. Native convergence is numerical evidence, not a verified dual
certificate or certified global lower bound. No performance-speedup conclusion
is drawn from comparison with older AC or 24-hour runs.

The non-solving `status()` check independently replayed all new manifests,
physical gates, original-leaf layouts, native expansion, raw/public projections,
storage boundaries and resource samples at the clean execution checkpoint.
Writing this post-run report makes the checkout dirty; the source-bound status
command intentionally refuses a changed/dirty execution context. Historical
inspection after later commits should verify the pinned manifests and assess
archives without bypassing that source-bound restriction or changing evidence.
This reviewable report and its non-solving plotter/tests are added after
execution. Neither immutable execution tree is changed. No production source,
policy, tolerance, scientific input, or default was changed.
