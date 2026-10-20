# Joint scaling convergence and contrasting deficit

2026-10-05. The unchanged joint scaling rule improves original-unit numerical
evidence in both surplus and deficit conditions. The remaining full-convergence
failure in each scaled arm is the requested gap test, not the native primal or
dual feasibility test. Physical feasibility and component accounting are strong,
but schedules remain sensitive and none of these results is a certified optimum
or an AC-feasible realization.

This follow-up contains offline analysis of the retained surplus arms and exactly
two new serial solves on the frozen E3 Large deficit case, arm 006, [1165,1189).
The original shedding policy, paired bounds, costs, device cones, CLARABEL
settings, acceptance tests and 180-second/4-GiB supervision remain unchanged.
There were no retries or post-result tuning. Production and the main checkout
were untouched.

## Why the scaled arms did not return Solved

The v0.11.1 full test requires either absolute or relative gap below its
threshold, both native feasibility residuals below their threshold, and an
embedding ratio at most one. All requested gap/feasibility tolerances here are
1e-10. The retained native values and rounded log embedding ratios give:

| Scaled arm | Absolute gap | Relative gap | Relative gap divided by tolerance | Native primal residual | Native dual residual |
|---|---:|---:|---:|---:|---:|
| Surplus, shedding | 1.322e-8 | 4.230e-9 | 42.3 | 4.515e-12 | 1.737e-12 |
| Surplus, fixed load | 3.007e-7 | 9.622e-8 | 962.2 | 1.129e-13 | 7.580e-17 |
| Deficit, shedding | 5.843e-4 | 3.792e-10 | 3.79 | 2.086e-12 | 4.482e-15 |

Both gap alternatives fail, while native feasibility and embedding checks pass.
All meet the reduced convergence tests. Each scaled log ends with an additional
iteration, unchanged metrics and a zero step, far below the 5,000-iteration
budget. Thus this is not an iteration or external resource-limit stop.

The precise underlying error branch cannot be recovered from these artifacts.
CLARABEL can replace an error status with `AlmostSolved` during postprocessing;
a zero final step can follow a failed KKT update/solve or an undersized step.
The original internal error and factorization/refinement diagnostics were not
retained. We can identify the blocking convergence test, but cannot distinguish
those internal causes or claim a diagnosed floating-point floor.

Sources: [v0.11.1 convergence checks](https://github.com/oxfordcontrol/Clarabel.rs/blob/v0.11.1/src/solver/implementations/default/info.rs)
and [solver termination flow](https://github.com/oxfordcontrol/Clarabel.rs/blob/v0.11.1/src/solver/core/solver.rs).

## Materiality in original units

Every scaled physical residual is below 0.071% of its existing tolerance.
Active/reactive balance errors are at most 7.11e-12 MW / 6.25e-11 MVAr;
storage recurrence and terminal errors remain below 5.41e-13 MWh. These errors
are negligible against the declared physical gate. This is SOCP feasibility,
not recovered AC feasibility.

For surplus, canonical-versus-physical cost discrepancies are 3.91e-10 and
1.03e-8. The remaining shedding-arm stationarity maximum, 4.01e-5, lies in
imaginary voltage products. Storage power, energy and epigraph stationarity
are below 7.60e-12. With fixed load, the maximum over all coordinates is
2.97e-11. The prior significant storage-epigraph accounting problem is not
present at comparable magnitude.

That does not establish coordinate accuracy. The two scaled surplus policies
have effectively zero shedding and physical costs differing by 4.57e-9, yet
maximum differences are 0.859 MW in storage power, 3.675 MWh in SoC and
32.597 MW in renewable dispatch. Their curtailed-energy totals differ by
207.920 MWh, while total storage throughput differs by only 7.92e-7 MWh.
Renewable curtailment is unpriced. These observations are consistent with
weakly identified/unpriced directions; they are not a proof of exact
nonuniqueness or an attribution of trajectory differences to residual error.
Tiny residuals and cost agreement do not guarantee the same downstream signposts.

## Unchanged rule on the deficit case

The joint-scaling source hash is unchanged from the surplus experiment. The new
baseline and scaled arms have exactly identical original canonical arrays,
cone dimensions, objective offset and physical-input identity. Both use
normalized device cones; the historical squared-cone graph is not claimed
canonically identical. The historical E3 input and artifact chain was verified.

| Measure | Normalized cones baseline | Plus joint scaling |
|---|---:|---:|
| Native status | AlmostSolved | AlmostSolved |
| Physical audit | Pass | Pass |
| Full diagnostic acceptance | No | No |
| Physical objective | 1,540,768.090115 | 1,540,766.729809 |
| Absolute native gap | 7.355975 | 0.000584293 |
| Original stationarity infinity norm | 5.300e-4 | 1.340e-4 |
| Original complementarity | 10.359718 | 0.000697985 |
| Canonical/physical objective discrepancy | 0.197948 | 0.0000131545 |
| Storage epigraph stationarity / coefficient | 0.019306 | 3.958e-7 |
| Estimated condition number of A | 908,324 | 28.94 |
| Iterations | 38 | 38 |
| Native solver seconds | 0.925 | 0.879 |

The scaled original stationarity maximum is now a load-shedding fraction
coordinate, not storage. Storage power/SoC/epigraph errors are below 4e-9.
The objective decreases by 1.3603 cost units (about 0.0000883%): generation
decreases by 0.9382, shedding by 0.40334 and storage by 0.01877. These are
measured attained costs, not independently certified objective-error bounds.
The baseline fails cost agreement as well as native status; the scaled arm
passes cost agreement and is rejected only by the native-Solved requirement.

Storage throughput changes from 29,391.2508 to 29,389.3739 MWh. Pointwise
battery power and SoC nevertheless differ by up to 23.978 MW and 23.978 MWh.
Again, close aggregate cost/throughput is not coordinate equivalence.

The approximately 0.171-second external scaling overhead exceeds the native
time saving. Scaling plus interface time is 1.072 versus 0.950 seconds;
worker wall times including audits/condition estimation are 5.725 versus
4.669 seconds. Maximum sampled RSS was 514.234 MiB. This remains an accuracy
result, not a demonstrated performance win. No settings were changed to obtain
`Solved`, and no new production policy was selected.

## Reproducibility and verification

Runner: `joint_deficit.py`; offline analysis: `joint_stopping_analysis.py`.
The shared experiment worker now accepts an explicit case and reference checker;
its old surplus defaults remain unchanged. Frozen joint rule source SHA-256:
`f1c9eb1ca4308696baf32ccde8a137c28873b57bf46f7459ef53ae7bf3c9ce25`.

Immutable raw results: `results/joint_deficit_001/`. Independent reconstruction
verified all registered hashes, original and transformed matrices, native/mapped
vectors, and exact reproduction of the rule's scale vectors. The two original
canonical archives are byte-identical. Offline checks invoke no optimizers.

- Deficit summary SHA-256:
  `18091c776bc03ada439dd213b6f5b0df06bebc56528f1faf7de9cc10535d2b76`.
- `results/joint_stopping_surplus_001.json` SHA-256:
  `9b84045d1f1c063d3667e6f448b0ddec839a191ff428b3c57f5a7e97287a8ddf`.
- `results/joint_stopping_deficit_001.json` SHA-256:
  `29e2eca48a8b4132db56097da740d0952053e1daab236b2360977361f3262314`.
- Base commit: `dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`; code is
  intentionally uncommitted in the authorized isolated worktree, source-hashed
  in each execution context. Prior artifacts remain unchanged.
- All 86 focused tests pass, with the two previously recorded warnings. Ruff
  lint, changed-file formatting and whitespace checks pass. No staging or commit.
