# Battery terminal-policy experiment

This experiment compares storage-controller behavior under different terminal
policies, with particular attention to high-load, low-renewable conditions.
The network is an imagined nine-bus reduction centered on Tracy, California,
and uses the electrical structure of `case9`.

Place local input data in `data/`. The directory contents are ignored by Git
so that source or restricted datasets cannot be committed accidentally.

## Experimental network pattern

The case9 generator terminals are interpreted as remote bulk-generation
interconnections, not population centers. Dispatchable generators remain at
buses 1, 2, and 3. Utility-scale renewable plants connect at those same remote
terminals:

| Resource | Spatial pattern |
|---|---|
| Dispatchable generation | Existing terminals at buses 1, 2, and 3 |
| Utility solar | 20% at bus 1 and 80% at bus 2 |
| Wind | 20% at bus 2 and 80% at bus 3 |
| Distributed solar | Load-proportional at buses 5, 7, and 9 |
| Load | Base-case load proportions at buses 5, 7, and 9 |

The base load fractions are:

| Bus | Fraction | Base power factor |
|---:|---:|---:|
| 5 | 28.57% | 0.949 |
| 7 | 31.75% | 0.944 |
| 9 | 39.68% | 0.928 |

Reactive load preserves each loaded bus's base-case ratio
`Qd_base / Pd_base`.

Storage siting is a separate experimental axis. The first comparison should
use one battery at bus 7 so that the effect of the terminal policy is not
confounded with allocation among several storage devices. Later comparisons
may use load-proportional storage at buses 5, 7, and 9, generation-colocated
storage at buses 1, 2, and 3, or transmission-junction storage at buses 4, 6,
and 8.

Bus 1 retains its dispatchable slack-capable generator. Utility renewable
units may share a terminal with a dispatchable generator; the bus represents
an aggregated interconnection substation rather than a single plant.

## Scenario construction

All source power channels first receive one fixed source-to-case normalization:

```text
source_to_case_scale = 315 MW / 1138.762447 MW = 0.2766160763
```

Here 315 MW is the total base load in case9 and 1138.762447 MW is the mean of
the 43,787 observed Tracy load values. The same factor is applied to load,
utility solar, wind, and distributed solar. It is fixed across windows so
seasonal and inter-window magnitude differences are preserved.

The normalized trajectories may then be scaled to construct seasonally
distinct low- and high-stress cases:

```text
load_scenario = source_to_case_scale * load_scale * load + load_shift_mw
resource_scenario = source_to_case_scale * resource_scale * resource
```

Load, utility solar, wind, and distributed solar have independent scale
factors. `load_shift_mw` is expressed in case-scale MW and is applied after
normalization. A scenario uses a contiguous window with complete observations.

Spatial multiplicative noise is optional and seeded. One perturbed set of
spatial fractions is drawn per scenario and held fixed over time. Fractions are
renormalized within each resource class, so noise changes spatial placement
without changing the intended aggregate trajectory. `spatial_noise_std` is the
standard deviation of the independent Gaussian perturbations applied in log
space before renormalization.

The scenario generator owns only time-series transformation and spatial
allocation. It does not choose:

- dispatchable-generator limits;
- storage power, energy, or initial state of charge;
- terminal-policy parameters; or
- nondispatchable inverter ratings.

Those choices determine adequacy and controller behavior and must remain
explicit in each experiment specification.

```python
from experiments.battery_terminal.scenario import (
    ScenarioConfig,
    generate_scenario,
    read_source_data,
    select_complete_window,
)

source = read_source_data("experiments/battery_terminal/data/source.csv")
window = select_complete_window(
    source,
    "2022-12-18 00:00:00-08:00",
    "2022-12-21 23:00:00-08:00",
)
scenario = generate_scenario(
    window,
    ScenarioConfig(
        load_scale=1.10,
        solar_scale=0.60,
        wind_scale=0.60,
        dist_solar_scale=0.80,
        spatial_noise_std=0.05,
        random_seed=17,
    ),
)

# OPF-ready time-series frames
df_P = scenario.df_P       # active load at buses 1,...,9
df_Q = scenario.df_Q       # reactive load at buses 1,...,9
df_nd = scenario.df_nd     # availability keyed by renewable-site identity
```

The current nondispatchable identities are:

```text
utility_solar_bus_1
utility_solar_bus_2
wind_bus_2
wind_bus_3
dist_solar_bus_5
dist_solar_bus_7
dist_solar_bus_9
```

## Representative windows

The initial study uses three complete, midnight-aligned 96-hour windows:

| Name | Fixed-PST interval | Physical regime |
|---|---|---|
| `low` | 2022-03-19 through 2022-03-22 | Renewable surplus with short deficits |
| `moderate` | 2019-02-04 through 2019-02-07 | Energy-balanced with a large peak deficit |
| `high` | 2021-12-18 through 2021-12-21 | Sustained energy deficit |

The moderate window has a higher instantaneous net-load peak than the high
window. The high classification refers to sustained energy inadequacy, not
peak power.

```python
from experiments.battery_terminal.scenario import (
    select_representative_window,
)

window = select_representative_window(source, "moderate")
```

## Provisional device specification

The first adequacy screen uses:

| Device | Provisional specification |
|---|---|
| Generator 1, bus 1 | 10–105 MW; original case9 cost and reactive bounds |
| Generator 2, bus 2 | 10–130 MW; original case9 cost and reactive bounds |
| Generator 3, bus 3 | 10–115 MW; original case9 cost and reactive bounds |
| Storage, bus 7 | 150 MVA, 1,000 MWh, 500 MWh initial SoC |

Total dispatchable capacity is 350 MW. This level requires battery support in
the moderate and high windows but not for aggregate adequacy in the low
window.

Renewable inverter ratings are fixed across all scenarios in a comparison at
110% of each site's maximum available real power. The multiplier leaves
reactive-power headroom at peak availability. Ratings must be computed jointly
from every scenario being compared; sizing each scenario independently would
change the physical system along with the operating condition.

The device factory does not select a terminal policy. Equality, shortfall, and
soft-cost configurations must be passed explicitly for each controller run.

## Meaning of `lossy_dc`

The package name `lossy_dc` denotes a loss-penalized DC formulation. It does
not withdraw resistive losses from the nodal real-power balances. Those
balances retain the lossless DC conservation equation, so total modeled nodal
injection sums to zero.

Resistance enters through the objective term

```text
loss_weight * sum_e r_e * p_e^2
```

where `p_e` is branch real-power flow. This convex quadratic discourages flow
on resistive branches and changes the economic dispatch, but it is not an
energy sink. Consequently, quantities described as load, generation, storage
energy, or curtailment in this experiment should not be interpreted as
including physical transmission-energy withdrawal. AC feasibility and
physical active-power losses require a formulation whose network equations
represent them.

## Reproducing the results

The source CSV is intentionally local and ignored by Git. Place the Tracy data
at:

```text
experiments/battery_terminal/data/9q9wtp_gen_and_load.csv
```

From the repository root, reproduce the complete lossy-DC experiment with:

```bash
uv run python -m experiments.battery_terminal.reproduce
```

The command writes these ignored artifacts under
`experiments/battery_terminal/results/`:

- `policy_sweep.csv`: seven terminal policies over the three representative
  windows;
- `policy_trajectories.csv`: stepwise SoC, battery power, generation, and
  curtailment for the policy sweep;
- `scenario_inputs.csv`: total active load, available renewable power, and
  net load for each prepared 96-hour representative window;
- `terminal_value_sweep.csv`: terminal equality targets from 0 through
  1,000 MWh in 50 MWh increments; and
- `soft_weight_sweep.csv`: linear and quadratic soft-terminal response paths;
- `horizon_study.csv`: no-policy, equality, and quadratic results over nested
  12-, 24-, 48-, 72-, and 96-step horizons;
- `horizon_locality.csv`: pairwise SoC divergence and common-boundary
  diagnostics relative to the no-policy controller; and
- `moderate_24_initial_soc.csv`: single-node and network-DC feasibility across
  the fixed initial-SoC bracket;
- `moderate_lookback.csv` and `moderate_prefix_capacity.csv`: feasibility as
  preceding hours are added and the maximum entry SoC those prefixes can
  create;
- `low_breakpoint.csv`: a refined terminal-value grid around the low-window
  upper-SoC active-set transition; and
- `ac_study.csv` and `ac_locality.csv`: the cold-start, staged high-window AC
  policy comparison and its SoC-boundary diagnostics;
- `subset_study.csv`, `subset_comparison.csv`, `subset_additivity.csv`, and
  `subset_trajectories.csv`: endpoint-fixed DC reconstruction, per-battery
  DC/AC power and SoC traces, and AC realization of equal-length subsections
  that do and do not cross an internal SoC boundary;
- `resolution_study.csv`, `resolution_comparison.csv`, and
  `resolution_energy_validation.csv`: current-objective behavior when the same
  high-window 24-hour trajectory is represented at 1-hour, 30-minute, and
  15-minute resolution;
- `metadata.json`: source-file SHA-256 hash, package versions, formulation,
  time-step duration, and every study grid.

Both sweeps use the fixed representative windows, default scenario scaling,
device specification, and lossy-DC formulation documented above. The runners
retain their complete CVXPY builds and extracted trajectories when imported
from Python; the CSV files contain the scalar results used for comparisons.
Solver-infeasible terminal targets remain rows in the value-function table
with their status and unavailable numerical fields left blank. Convex OPF
builds use the project's CLARABEL default; the solver actually selected during
the run is also recorded in `metadata.json`.

The horizon study uses nested suffixes of each 96-hour representative window.
Every suffix ends at the same timestamp within its window, while its initial
SoC is reset to 500 MWh. This holds terminal operating conditions fixed as the
horizon grows, but it is a sequence of finite-horizon planning problems rather
than one trajectory revealed progressively through time.

The AC study first solves the 12-hour no-policy, quadratic, and equality cases
independently. It proceeds to the matching 24-hour cases only if all three
12-hour solves return usable optima. AC builds use the project's IPOPT default
without warm starts or experiment-specific solver settings. These are
nonconvex local solutions, not global-optimality certificates. AC physical
active-power loss is reported from nodal energy accounting and is distinct
from the `lossy_dc` objective penalty described above. AC branch thermal limits
are not yet implemented.

The subset study takes state intervals `[32, 50]` and `[60, 78]` from the
96-hour high-window `lossy_dc` equality solution. Both contain 18 dispatch
steps. The first contains the full-SoC state 41; the second contains no
internal empty or full state. Each short problem inherits its initial and
terminal SoCs from the long solution. DC acceptance is based on restricted
feasibility and objective equality, not solely on pointwise trajectory
equality under possible nonuniqueness. AC uses the same inherited endpoint
states as an operational comparison; AC and DC objectives are not equated.

The resolution study uses zero-order hold, so refining the time grid does not
change source or load energy. Storage dynamics use the matching `delta`.
Reported generation, curtailment, and storage-throughput energies explicitly
include `delta`. The optimized objective is left unchanged and therefore
retains the package's current convention: stage terms are summed once per step
without multiplication by `delta`, while terminal cost is added once per
horizon.

## Executable report

`report.py` is a marimo notebook that combines the final narrative, MathJax
formulation, result tables, and figures. It reads the ignored tables generated
by the reproduction command; it does not rerun optimization reactively.

Validate every notebook cell in script mode:

```bash
uv run experiments/battery_terminal/report.py
```

Open the interactive notebook editor:

```bash
uv run --extra notebook marimo edit \
    experiments/battery_terminal/report.py
```

Export a static HTML report:

```bash
uv run --extra notebook marimo export html \
    experiments/battery_terminal/report.py \
    -o experiments/battery_terminal/results/report.html
```

The exported report is generated output and remains ignored with the other
result artifacts.
