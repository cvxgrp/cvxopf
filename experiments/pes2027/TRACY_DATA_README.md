# Tracy hourly data for the Case118 PES experiments

This ZIP supplies the one source CSV omitted from the CVXOPF
repository. Use your existing clone; the network, device mapping, scenario
definitions and experiment code are already there.

**Install.** From the root of your clone (replace the ZIP path if needed):

```sh
unzip -n ~/Downloads/pes2027-tracy-input.zip 'experiments/*'
shasum -a 256 experiments/battery_terminal/data/9q9wtp_gen_and_load.csv
```

Expected SHA-256: `45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f`.
The unzip command leaves an existing CSV alone; if its hash differs, stop
and check which file is installed.

**What it is.** The CSV contains modeled hourly demand, utility solar, utility
wind and distributed solar for the Tracy, California node of a western U.S.
regional study. CVXOPF takes the 8,760 hours of 2021 in fixed UTC−08:00,
scales the four profiles together, and allocates them to the **PGLib IEEE
118-bus test network**, not to a physical model of Tracy's grid.
`experiments/pes2027/APPENDIX.md`, §A1, explains the upstream sources and
the allocation. There is no need to download the public network separately.

**Check the installation.** In the project environment (see the repo README
for the native IPOPT prerequisite), run:

```sh
uv sync --frozen --extra dev --extra notebook
uv run --no-sync python -c 'from experiments.case118_tracy_2021.stage_b import verified_inputs; p = verified_inputs(); print(p.load_p_mw.shape, p.nd_available_mw.shape)'
```

Expected: `(8760, 99) (8760, 119)`. This rebuilds the annual device inputs
from the CSV and checks them against the committed array hashes and device
tables. No solver runs. To save the arrays and allocation plots, follow
**Reproduce the input package** in `experiments/case118_tracy_2021/README.md`.

**Run one paper case.** Here is the large-deficit, 24-hour network-flow joint
optimization with the paper's 50% initial and final battery energies. It
uses the scenario already selected in the repo and solves it afresh:

```sh
uv run --no-sync python - <<'PY'
import json
from cvxopf import build_opf_multistep, extract_results
from experiments.case118_tracy_2021 import e3, e3_prepared as model

selection = json.loads((e3.HERE / "e3_selection/selection.json").read_text())
study = e3.make_study(selection)
arm = next(a for a in study["arms"] if a["start"] == 1165
           and a["leg"] == "energy_neutral" and a["formulation"] == "lossy_dc")
inputs = model.kwargs_for_arm(model.verified_inputs(), arm, study["storage_device_ids"])
build = build_opf_multistep(**inputs)
build.solve(solver="CLARABEL", canon_backend=build.canonicalization_backend,
            warm_start=False, **model.solver_options("lossy_dc", model.DEFAULT_PROTOCOL))
result = extract_results(build)
print(result["status"], result["objective"])
PY
```

**Scope.** That example uses the published window directly. The original
supervised joint-study runner also verifies *how the windows were chosen*
against saved annual DC runs. The forced-shedding extension checks a separate
saved fixture, and historical transfers, rollouts and figure replay read saved
donor or result records. Those are not missing Tracy data and are not included
here. For those workflows see `experiments/pes2027/README.md`.
