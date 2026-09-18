# Tracy 2021 Case118 study plan

The source, active-power scaling, siting rules, and storage sizes in sections
2–3 are approved design choices. Section 5 records the approved starting
economics and remaining operating choices. Battery regularization is selected
through the shorter studies, not fixed for annual execution now. Generate and
validate the realized mapping in Stage A.

## Execution order

1. Close out the completed analytical 8,760-hour study scientifically and in
   Git, including the required data description, heatmaps, and summaries.
   Review and commit this scientific record as **commit 1**.
2. **Pause for experimental-design review.** Use the fresh inspection of the
   toy inputs and completed results to decide which toy-data scientific
   studies, if any, should precede Tracy, and what runner changes they require.
3. Separately triage and promote materials from the completed run's `outputs/`
   into the Git-tracked tree. Review and commit this work as **commit 2**.
   Do not combine these two steps into one commit.
4. Implement and execute any selected toy-data follow-ups under their own
   reviewed protocols, implementation commits, and compute budgets. Review
   their findings and explicitly decide to move on to Tracy.
5. **Close the `big-experiment` PR at the end of Stage 0c.** Record completed
   and explicitly deferred toy follow-ups, finish documentation and regression
   checks, and prepare the closeout handoff. The **user, not the agent**, merges
   `big-experiment`, updates local `main`, verifies the merged state, and creates
   a fresh Tracy-study branch from that `main` before Stage A.
6. Generate the new inputs and obtain **user review 1** of the final input
   comparison and mapping before any Tracy study solves.
7. Run targeted week-to-month DC studies selected using predicted congestion
   and difficult operating periods; review them before an annual DC launch.
8. Solve/audit the full 8,760-hour DC problem and obtain **user review 2** of
   the data-generation choices in light of the resulting operation.
9. Only then qualify Tracy AC and seek approval for annual AC execution.

Both old-study commits and the toy-follow-up disposition must precede the
Stage 0c PR closeout and fresh-branch gate. This Tracy plan may merge with
`big-experiment`: it documents future work and does not imply that Tracy input
generation or execution has begun. Keep the new study's data, decisions, and
results distinct from the analytical benchmark. Toy-data counterfactual AC
studies can use the existing accepted toy DC archive; they do not depend on
a new Tracy annual DC solve.

## 1. Objective and relationship to the completed study

Build the intended 8,760-hour Tracy-driven Case118 experiment with an explicit,
inspectable mapping from the owner's supplied composite to the network.
Preserve its temporal relationships: available nondispatchable energy exceeds
load over long periods, including the year, while individual hours still have
positive net load. Dispatchable capacity is deliberately below peak net load,
so storage must contribute. Congestion is desirable; an infeasible study is not.

Keep the completed analytical-profile experiment and its execution records
intact as a separate computational benchmark. The supplied Tracy composite
was used in the earlier Case9 battery and M17 studies, but not the Case118
pilot, week, month, or annual fixtures. Those Case118 studies used analytical
temporal profiles. Do not relabel their results as Tracy results.

Verify input identity directly from the source through the prepared arrays
to the model inputs. Reuse the execution infrastructure, but regenerate all
data-dependent results and scientific analyses for the Tracy scenario.

## 2. Approved source and aggregate scaling

### Source contract

- File: `experiments/battery_terminal/data/9q9wtp_gen_and_load.csv`.
- SHA-256:
  `45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f`.
- Year: **2021**, all 8,760 consecutive hourly rows, January 1 00:00 through
  December 31 23:00 in the source's fixed UTC-08:00 convention.
- Columns, interpreted as MW: `9q9wtp_load`, `9q9wtp_solar`, `9q9wtp_wind`,
  `9q9wtp_dist_solar`. Preserve their separate identities.
- Time step: one hour. Energy is the sum of interval MW times one hour.
- Existing inspection found no missing, duplicate, nonfinite, or negative
  entries in these four channels for 2021. Recheck during construction.
- Describe this as the owner's Tracy-derived composite, not as four channels
  of independently verified direct measurements.
- No imputation, analytical fallback, temporal noise, year relabeling,
  independent channel normalization, or forced equal wind/solar energy shares.

For the study, load is the demand channel and distributed solar is a separate
available injection, exactly as in the agreed net-load calculation. Do not
subtract distributed solar from load and then also inject it. Preserve any
source-construction metadata explaining gross/net semantics; do not silently
reinterpret this approved accounting convention.

Let source load be L(t), utility solar S(t), wind W(t), and distributed solar
D(t). Define available net load N(t) = L(t) - S(t) - W(t) - D(t).

Apply one common multiplier to all four channels:

    alpha = 6000 MW / max_t N(t)
          = 6000 / 1964.1006
          = 3.0548333420396085.

This targets **6,000 MW peak net load** and preserves the source energy ratios,
relative timing, and sign of net load. It does not require positive annual net
energy. Curtailment is a dispatch decision; available renewable energy is not
the same as renewable energy actually used.

### Approved system and storage sizes

Use the pinned PGLib source at
`experiments/case118_annual_hierarchy/source/pglib_opf_case118_ieee.m`, revision
`dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3`, through the provenance-checked loader.
Do not substitute the package's different PYPOWER-derived Case118 variant.

The PGLib case has 118 buses, 186 in-service branches, 4,242 MW base active load,
and 6,515 MW positive dispatchable capacity at 19 buses. Uniformly scale each
positive dispatchable Pmax by **5000/6515 = 0.7674597083653109**. The source Pmin
values are zero. Keep the 35 zero-Pmax generator entries as reactive-support
resources; scaling active capacity does not decide their Q limits.

Define average load from the entire scaled 2021 gross-load series, not the
base-case snapshot or average positive net load:

    mean_load = mean_t(alpha L(t)) = 3511.6394609974527 MW
    E_total = 4 h * mean_load = 14046.55784398981 MWh
    P_total = E_total / 3 h = 4682.185947996603 MW.

The three-hour rule applies to maximum charge and discharge active power.
AC apparent-power ratings and any resulting P/Q tradeoff must be explicit;
MW is not interchangeable with MVA at nonzero reactive output.

The next storage comparison, not a second automatic annual run, is six
average-load hours: **21069.836765984717 MWh / 7023.278921994905 MW**. Hold siting,
weights, and all other inputs fixed. This increases both energy and power by
1.5, so it is not an energy-only sensitivity. Eight hours is deferred.

### Input reference values

These are source-derived input checks, not solved-network results. Reconstruct
them independently in the input package; retain full precision in computation.

| Metric | Raw Tracy 2021 | Approved scaled candidate |
| --- | ---: | ---: |
| Annual load | 10,069,931.232 MWh | raw value times alpha |
| Annual available utility solar | 11,219,030.212 MWh | raw value times alpha |
| Annual available wind | 7,649,167.150 MWh | raw value times alpha |
| Annual available distributed solar | 393,989.859 MWh | raw value times alpha |
| Annual available ND / load energy | 1.912842 | unchanged |
| Average load | 1,149.536 MW | 3,511.639 MW |
| Peak gross load | 3,425.131 MW | 10,463.204 MW |
| Peak available net load | 1,964.101 MW | 6,000 MW |
| Peak available ND | 7,947.077 MW | 24,276.997 MW |
| Largest instantaneous available surplus | 6,775.605 MW | 20,698.343 MW |

With 5,000 MW fully available dispatchable capacity and no network/loss effects,
the approved candidate has 42 shortfall hours in 22 continuous events. The
longest event lasts six hours; the largest continuous event requires about
4,202.173 MWh, on February 19 01:00–06:00 fixed UTC-08:00. Total positive
shortfall energy across the year is about 13,652.212 MWh. None of these values
alone establishes required storage energy, recharge feasibility, or AC
deliverability. They explain choosing the lower, four-average-load-hour
storage size as the first candidate.

## 3. Approved spatial mapping

Classify buses from the unmodified PGLib active load and positive in-service
generator Pmax, before adding renewables or batteries. Reactive-only generator
entries do not make a bus an active dispatchable-generation bus.

| Original category | Count | Utility-renewable sites to select |
| --- | ---: | ---: |
| Positive load, no active dispatchable generation | 89 | 0 |
| Positive load and active dispatchable generation | 10 | 5 |
| Active dispatchable generation, zero load | 9 | 5 |
| Neither positive load nor active dispatchable generation | 10 | 5 |

The last three categories therefore supply **15 distinct utility-renewable
sites**. Five of nine is the agreed integer rounding of approximately half.
Co-location with load is permitted at existing dispatchable
generation buses. No utility wind/solar is placed at an original load-only bus.

Eligible bus IDs:

- Load + active generation: **12, 31, 46, 49, 54, 59, 66, 80, 100, 103**.
- Active generation without load: **10, 25, 26, 61, 65, 69, 87, 89, 111**.
- Neither: **5, 9, 30, 37, 38, 63, 64, 68, 71, 81**.
- Original load-only buses are the remaining 89 buses.

### Load and distributed solar

For all 99 positive-load buses, define

    load_share[i] = Pbase[i] / sum_j Pbase[j] = Pbase[i] / 4242.
    load[i,t] = load_share[i] * alpha * L(t)
    distributed_solar_available[i,t] = load_share[i] * alpha * D(t).

This is the agreed normalization onto the simplex: nonnegative shares summing
to one, preserving base-load proportions. It is not an unscaled copy of base
MW and not the Euclidean nearest-point projection onto the unit simplex.
No random perturbation is applied to these load/DG shares.

### Utility wind and solar

Select sites uniformly without replacement within each eligible category.
Assign each selected site independently to wind only, utility solar only, or
both, with probability 1/3 each. These are probabilities, not exact quotas.
A site marked both has two separate identified resource channels.

For each resource r separately, start with equal allocation among its host
sites S_r, draw independent multipliers u[i,r] ~ Uniform(0.8, 1.2), and set

    resource_share[i,r] = u[i,r] / sum_{j in S_r} u[j,r].
    available[i,r,t] = resource_share[i,r] * alpha * source[r,t].

Weights are fixed for the whole year. Utility solar and wind each sum to their
own scaled Tracy channel at every hour. Do not confuse the resource-type
probabilities with a wind/solar energy split. Require at least one host for
each resource; an invalid realization must be reported, not silently redrawn.

### Sparse battery placement and allocation

Select **27 distinct battery buses**, using the following disjoint categories:

| Battery category | Eligible population | Selected count | Capacity pool |
| --- | ---: | ---: | --- |
| Original load-only; no dispatchable or utility wind/solar generation | 89 | 22 | Load-side half |
| Selected utility wind/solar buses with no load | 10 | 3 | Renewable-side half |
| Selected utility wind/solar buses with load | 5 | 2 | Renewable-side half |

Distributed solar does not disqualify a load-only battery host. The 22 and 3
counts implement the approved approximate quarter coverage. Do not place
batteries at every load bus or every renewable bus.

- Load-side pool: E_total/2 and P_total/2, allocated across the 22 selected
  hosts in proportion to their Pbase, renormalized over those hosts only.
- Renewable-side pool: E_total/2 and P_total/2, allocated across its five
  selected hosts in proportion to each host's annual available utility solar
  plus wind energy. Exclude DG solar from these weights. Renormalize over
  these five hosts only; the two mixed sites belong solely to this pool.
- Every battery receives the same three-hour E/P ratio. Each pool totals
  **7,023.278921994905 MWh / 2,341.0929739983015 MW** in the primary case.
- Distinguish the physical host category from the capacity accounting label:
  a renewable-side battery can be at a mixed load/renewable bus.

### Reproducible construction and post-construction checks

Use **seed 42** and the above perturbations, followed by a post-construction
sanity check. Use the following implementation convention
to remove otherwise hidden randomness choices, and record it in the manifest:

1. Use `numpy.random.Generator(numpy.random.PCG64(42))`; record NumPy version.
2. Sort candidate external bus IDs. Select the five sites in each category in
   this order: load + generation, generation/no-load, neither. Sort selected
   lists before further processing.
3. Process the sorted union of 15 sites, drawing one categorical assignment
   per site with fixed order `[wind_only, solar_only, both]`.
4. Draw solar multipliers in increasing solar-host ID order, then wind
   multipliers in increasing wind-host ID order. Normalize each separately.
5. Select batteries without replacement in order: 22 original load-only,
   three selected renewable/no-load, two selected renewable/with-load.
   Sort each candidate and selected list. Compute capacity weights without
   additional noise.
6. Save the complete realized bus/device table and numeric weights. The table,
   not seed alone, is the authoritative frozen realization.

Audit eligible populations, counts, disjoint battery pools, resource presence,
nonnegative normalized weights, hourly channel conservation, 5,000 MW total
dispatchable Pmax, exact storage totals/splits, and every battery's E/P ratio.
Check realistic-looking concentration with a bus-role map and capacity table,
without silently changing the declared distribution. Confirm identical output
on repeated construction and sensible identity alignment under input ordering.

Keep the first valid seeded configuration unless network checks expose a
concrete feasibility problem. Diagnose and document any proposed targeted
adjustment and obtain approval before refreezing it. Do not repeatedly change
seeds until congestion disappears or a favorable optimization result occurs.

## 4. Historical placement and the required old/new comparison

### What was done before

The Case9 battery-terminal/M17 implementation used one battery at **bus 7**,
150 MVA / 1,000 MWh, initially 500 MWh. The battery-terminal README explains
the single-site choice: isolate terminal-policy effects without confounding
them with allocation among multiple storage devices. Bus 7 was a load bus;
this was not a demonstrated optimal storage placement. Load and DG shared
fractions 90/315, 100/315, 125/315 at buses 5, 7, 9. A separate common
source-to-case multiplier was 315/1138.7624473656565, approximately 0.276616.
Do not reuse that multiplier for Case118.

The analytical Case118 study placed batteries at **41, 65, 89, 105**, selected
by deterministic load-weighted four-medoids clustering using shortest-path
electrical distance based on branch |x| (with a small positive floor).
Clustering weights used base apparent demand. Storage capacity allocation used
cluster base active demand. Solar went at bus 65, the medoid of the largest
apparent-demand cluster; wind went at bus 105, the storage medoid electrically
farthest from solar. These were pre-dispatch rules, not an optimized siting
result. The annual case used 15% renewable/load energy, equally divided
between wind and solar, storage power at 5% of peak gross load, and four hours
of storage duration at that power. **That four-hour duration is not our new
four hours of average system load.**

Evidence: `experiments/battery_terminal/README.md`, `devices.py`, `scenario.py`;
`experiments/hierarchical_battery_resilience/prepare_scenario.py` and its
prepared manifest; `experiments/case118_annual_hierarchy/scenario.py`,
`S0_PILOT_PROTOCOL.md`, and `S4_PROTOCOL.md`.

### Like-for-like descriptive metrics

As an explicit deliverable, recalculate these metrics for the actual frozen
analytical annual fixture in 0a. In Stage A, calculate the matching Tracy 2021
metrics and assemble the comparison, with scaled Tracy as the primary
apples-to-apples counterpart and raw Tracy as source context:

- Year/calendar, time step, source identity, load/renewable channel definitions.
- Annual and monthly load and available renewable MWh, each channel separately
  and combined, renewable/load ratios, and net energy.
- Peak gross load, coincident peak net load, minimum net load, ramps, and
  monthly max_t(load minus total available ND), not differences of maxima.
- Hours and continuous events exceeding available dispatchable Pmax, peak
  shortfall MW, event duration/energy, and recharge opportunities.
- Dispatchable Pmax, storage charge/discharge MW and apparent MVA where relevant,
  energy MWh, energy divided by average gross load, E/P duration, initial and
  terminal SOC, and spatial coverage by resource role.
- Generator linear/quadratic coefficients and marginal-cost ranges at each
  scenario's actual power caps; battery throughput weights and their units.
  Identify the changed economic assumptions separately from the changed data.
- Base load versus new average/peak load; unchanged branch ratings; nodal
  injection and potential transfer patterns, without claiming uncomputed
  branch feasibility or summing line ratings as system transfer capacity.
- Separately, available versus dispatched renewables, curtailment, losses,
  congestion, cycling, and realized SOC where accepted solve outputs exist.

Do not label an absent old DG channel as an observed zero source series, or
compare a base-case snapshot to an annual mean without identifying the
distinction. Multiple design dimensions change between studies; differences
in their outcomes are not a controlled estimate of the effect of Tracy data
alone. This comparison requires input reconstruction and existing outputs,
not another solve of the historical execution tree. It does not itself require
a full execution audit; any separately necessary old-study closeout analysis
must follow the single-pass, explicitly budgeted process in work package 0a.

### Required comparable final-input heatmaps and tables

This section specifies a shared presentation contract across two stages, not
a requirement to generate both datasets during closeout. **0a produces and
commits the toy-data baseline only**, with final-input arrays, heatmaps,
summary tables, units, calendar metadata, and reproducible plotting logic.
Use the signal definitions, orientation, and summary metrics below so that
these materials **will support the comparison in Stage A**. **Stage A generates
the Tracy counterpart and assembles the actual side-by-side comparison.**
Completing that comparison is not an exit condition for 0a or 0b.

Use the actual final aggregate input signals of the completed analytical
study ("toy data" or "sines and cosines" data), not its unitless
profile formulas or intermediate capacity factors. In Stage A, compare them side by side
with **scaled Tracy aggregates before allocation to buses**, in engineering
units. Verify that summing the eventually prepared bus/device inputs exactly
reconstructs those Tracy aggregates.

For each scenario, produce **24-row by 365-column heatmaps: rows are hour of
day (0–23), columns are days in chronological order**. Required signals are:

- Total gross active load, MW.
- Total available nondispatchable generation, MW, with the included channels
  named explicitly.
- Available net load (load minus total available nondispatchable), MW.
- Separate utility solar, wind, and distributed-solar availability where
  present. Mark the toy scenario's absent DG channel as not modeled.

In Stage A, each pair must share orientation, units, colormap, and color limits computed
jointly across the two scenarios for that signal. Use a common zero-centered
diverging scale for net load. Do not independently normalize each panel, clip
extremes silently, or rescale the old scenario to conceal the actual magnitude
difference. Optional normalized-shape plots are supplementary, never replacements.
The standalone toy plots in 0a need not anticipate Tracy's color limits.
Stage A may render new paired plots from the committed toy arrays with joint
limits, without modifying the historical inputs or the committed 0a record.
Label the toy year/time convention (2025 UTC) and Tracy's 2021 fixed UTC-08:00
calendar explicitly. Day-of-year alignment is a visual comparison, not a
claim of matching dates, weekdays, weather, or simultaneous observations.

Accompany the plots with side-by-side annual and monthly tabular summaries
from the same final arrays: all channel MWh, average/peak load, available-ND
and net-load extrema, energy ratios, shortfall hours/events/energy against
each scenario's dispatchable capacity, and storage sizing/coverage metrics.
Show the exact transformations and distinguish available input power from
dispatched generation or curtailment. Save figure-ready arrays, units,
provenance, and reproducible plotting logic with the reviewed material.

## 5. Starting economics and remaining operating choices

### Approved economic starting point and shorter-study parameters

Intertemporal leveling from convex generation costs is an intended mechanism
of the Tracy study. Start with economically meaningful quadratic generator costs and
**low battery throughput regularization**. The completed toy fixture used
fleet-wide generator c2 = 1e-4 and an explicit battery `aging_weight=1.0`
override. Those historical settings remain part of its record; they are not
the Tracy starting economics. This replaces the earlier recommendation to
reuse them for the first comparison.

**Generators — owner-confirmed starting rule.** Keep the inherited linear coefficients
unchanged, rescale maximum powers as approved, and set each generator's
quadratic contribution to exactly one-third of its linear contribution at
its updated maximum power. Start from the pinned PGLib generator models,
also retaining their inherited constant coefficients. For each
of the 19 active generators, let G_i be its **updated** maximum power in MW
after the approved 5000/6515 capacity scaling. In the repository convention,

    C_i(P) = c0_i + c1_i P + c2_i P^2,
    c2_i = c1_i / (3 G_i),       0 <= P <= G_i.

Thus c2_i G_i^2 = (c1_i G_i)/3: the quadratic contribution at maximum power
is one-third of the linear contribution (approximately 33%, not 33% of their
sum). This is the confirmed Tracy starting point, motivated by the owner's previous
toy-model prescription while preserving the inherited heterogeneous linear
costs. For normalized output x = P/G_i, the equivalent
coefficients are A_i = c1_i G_i and B_i = A_i/3 in C_i = c0_i + A_i x + B_i x^2.
The resulting marginal cost spans c1_i to (5/3)c1_i over the unit's
power range. This is a declared study assumption, not calibrated plant data.

Derive curvature from the updated caps rather than adding the toy c2 = 1e-4
conditioning term. Keep the 35 zero-Pmax entries as reactive-support resources
with their inherited costs; the ratio rule does not apply at G_i = 0. Verify
positive inherited c1_i for every positive-capacity unit before applying the
rule, and surface any source mismatch rather than inventing a coefficient.

**Owner review update: generator curvature is a study parameter.** Preserve
the ability to compare almost-linear and more strongly quadratic costs in the
shorter Tracy studies. Express the setting as the dimensionless ratio rho of
quadratic to linear cost at updated maximum output:

    c2_i(rho) = rho * c1_i / G_i,
    C_i'(P) = c1_i + 2 c2_i(rho) P.

The starting value rho = 1/3 reproduces the approved rule; values near zero
give almost-linear costs, and rho = 0 is exactly linear if explicitly selected.
Keep inherited c0/c1 and updated capacities fixed across these comparisons.
Select the near-zero value and any additional curvature levels in the bounded
shorter-study protocol, together with the battery-weight settings and solve
budget. Do not silently add the toy conditioning coefficient or freeze a full
parameter grid now. Record the chosen ratio and actual per-generator c2 values
in every configuration, using the same costs in its DC and AC models.

**Batteries.** Begin with `StorageUnitIdeal`'s current default
`aging_weight=0.01`, in objective units/MWh of absolute, one-way throughput.
Use the default at initial device construction and record its resolved numeric
value in the prepared inputs. There is no initial scenario-specific override.
The integrated term is delta * sum_{s,t} lambda_s |b_{s,t}|, so charging and
discharging 1 MWh costs 0.02 objective units for ideal storage at this starting
weight. Treat it as low regularization, not a calibrated degradation charge.
Low numerical magnitude alone does not establish a negligible effect on the
solution; assess that effect in Stage B before choosing the annual setting.

The bounded shorter-study protocol will specify both generator-curvature and
battery-regularization comparisons; do not freeze a sensitivity grid or final
coefficients now. Explain every proposed
override and show its operational consequences before carrying it into annual
execution. Keep the selected generator and battery economics consistent
between the DC planner and AC realization.

### Optional last-resort load shedding

The owner requests an option to shed any load using the existing cvxopf
cost-based approach. Configure the identified `Load` devices, rather than
introducing emergency injections or changing the network constraints. With
the option enabled, all 99 positive-demand load channels are eligible for
shedding, with `max_shed_fraction=1.0`; retain an explicit disabled setting
for fixed-load comparisons. The same declared policy applies in DC and AC.
Positive active demand is eligible; associated reactive demand is reduced
proportionally in AC, following the existing model. Distributed solar remains
a separate resource and fixed shunts remain network elements.

Use explicit finite positive `shedding_cost_per_mwh` values to make shedding
the last economic choice after available generation and storage. Retain the
single optimization and existing load-cost implementation described in
[Milestone 19](milestone-19-load-shedding.md). The input review must show the
penalty, units, eligible devices and fraction limits. No numerical penalty or
relative priority between load locations is assigned by this plan; specify
them in the shorter-study protocol and explain their relation to the selected
generator and storage costs.

Check last-resort behavior on the selected shorter windows for the cost
settings being studied. A penalty above generator marginal cost alone does
not establish that behavior in a constrained network with storage. Report
any shedding and distinguish cost-driven shedding from demand that cannot be
served under the retained constraints; unresolved local solves do not prove
the latter. Shedding reduces exposure to demand-driven infeasibility but is
not a guarantee that every AC solve or constrained problem will succeed.

Retain original and served demand, per-load shed power/fraction, energy not
served, and shedding cost separately from generation and battery costs.
Distinguish DC planned shedding from AC implemented shedding, by time and
location. A feasible trajectory with shedding is an operating result with
unserved demand, not evidence that all load was served. Include these fields
in independent audits, retained trajectories and the Tracy dashboard.

### Remaining choices to close before numerical qualification

Construct the active mapping after both old-study commits and the explicit
transition to Tracy in work packages 0a–0c.
Present them together with the input package rather than reopening approved
year, scale, counts, seed, or storage choices.

| Item | Proposed treatment / decision needed |
| --- | --- |
| Reactive load | Recommend Q_i(t) = (Qbase_i/Pbase_i) load_i(t), retaining signs and fixed shunts separately. This pinned case has no zero-P/nonzero-Q load buses. Confirm and record before AC qualification. |
| Dispatchable reactive capability | Recommend retaining source Q limits, including reactive-only units; uniform active-Pmax scaling is not approval to scale Q. Make the decision explicit. |
| Renewable inverter ratings | Choose and label a rating rule/headroom factor; an observed availability maximum is not a measured nameplate. Avoid unintended clipping of the approved source. |
| Battery AC operating set | Resolve apparent MVA rating versus the approved E/3 active-power limit and reactive support. If MVA headroom is larger, do not accidentally enlarge the active limit. |
| Storage dynamics and SOC | Confirm ideal-storage reuse versus separately scoped lossy storage, usable versus nameplate energy, SOC bounds, initial and annual terminal SOC. Proposed default: the existing ideal model and 50%-initial/50%-terminal convention. |
| Economics | Apply the starting generator rule and default battery regularization above. Keep both curvature and battery weight configurable; select annual settings from shorter-study evidence. Explicitly document curtailment treatment and the remaining objective terms, including the DC loss proxy. |
| Load shedding | Provide the approved all-load option through existing `Load` settings. Review its enabled/disabled setting and explicit penalties before solving, and check its intended last-resort behavior alongside the generator/storage cost comparisons. |
| Controller and execution | Provisional baseline: the reviewed three-hour AC/one-hour-stride hierarchy and recovery machinery. Reconsider the experimental scope and runner requirements at the post-0a design pause and after selected toy follow-ups; confirm the Tracy policy before its execution gates. |

The intended primary study seeks full load service with rated branches and
voltage/device constraints, with the explicit shedding option above available
as a last resort. Do not inflate branch ratings or remove reactive limits to
obtain a solution. A diagnostic relaxation, if
separately authorized, is not an accepted operating trajectory. A failed
nonlinear local solve also does not by itself prove physical infeasibility.

## 6. Work packages and gates

### 0a. Completed scientific closeout — commit 1 (`9c26366`)

This is a mandatory predecessor to new-study implementation and generation,
not an optional parallel workstream. Its scope is the completed toy study.
Do not generate Tracy inputs or assemble the toy-versus-Tracy comparison here;
those belong to Stage A, after the prerequisite commits and toy-follow-up
disposition.

**Scientific and Git record TODOs**

- [x] Finish the completed 8,760-hour toy-data study's scientific report and
  formal results record, including accepted coverage, physical-audit status,
  recovery/intervention history, resources, limitations, and what remains
  unknown. Distinguish computational acceptance from formal closeout.
- [x] Describe the actual data used clearly: analytical formulas and their
  parameters, calendar/timezone, final MW scaling, renewable mix, spatial
  allocation, generator/storage choices, and all material transformations.
  Explicitly state that this run did not use the supplied Tracy trajectories.
- [x] Include the toy study's final-input heatmaps and tabular summaries using
  section 4's signal definitions, hour-row/day-column orientation, units, and
  summary metrics. Save their supporting arrays and plotting logic so they
  will be reusable in Stage A's comparison with scaled, pre-allocation Tracy
  signals. The Tracy panels and side-by-side comparison are not 0a deliverables.
  Do not substitute plots of solver outputs for the required input plots.
- [x] Use this fresh view of the toy data to identify the questions worth
  studying next. Relate its imposed daily/seasonal structure, renewable share,
  net-load stress, storage sizing, and spatial placement to the observed
  congestion, AC adjustments, and recovery patterns. Distinguish features
  imposed by data generation from demonstrated physical or economic effects.
  Include these observations and open questions in the closeout report.
- [x] Verify the complete analyzer payload and formal `S5_RESULTS.json`
  against retained evidence, and recover any missing record. Review the
  single-pass analysis/promotion implementation and save/reuse the necessary
  final analysis exactly once. If a full pass is required, state its scope
  and cost and obtain execution approval. Check for an existing durable result
  before launching reconstruction.
- [x] Independently review the scientific record and exact file disposition,
  obtain the owner's closeout/commit approval, and commit the scoped record.
  Record the resulting commit ID. A draft report or staged files alone do not
  satisfy the Git-committed closeout gate.

Exit: the owner has reviewed the scientific/input record, including its data
description and standalone toy-data heatmaps/tables, and that scoped record is
committed. The baseline is ready for later comparison; no completed Tracy
comparison is required or claimed at this point.
Report **commit 1**, then pause for the design review below before starting
the separate `outputs/` triage step. Include
the figures/data needed to make the scientific closeout self-contained here;
the broader historical `outputs/` inventory and curated promotion remain 0b.

#### Design checkpoint: approved analysis questions and remaining runner scope

The owner approved the three-question analysis framework below on 2026-09-17.
After inspecting the toy temporal inputs and economics, the owner retained
this first study but **deferred the separate AC look-ahead-horizon study on the
current toy model**. Its generator curvature (c2 = 1e-4) and battery throughput
weight (1.0) do not represent the intended temporal economics. Keep the first
study bounded and focused on reusable analysis and matched comparisons, rather
than an exhaustive explanation of toy behavior. Its economic findings remain
conditional on the historical objective; replicate the analysis on Tracy's
revised model before drawing conclusions about the intended storage mechanism.
Deferral reflects limited relevance and priority, not evidence that look-ahead
has no effect on network-constrained feasibility, dispatch, or convergence.
The [next design checkpoint](case118-toy-ac-analysis-design.md) proposes the
episode sample, matched comparisons, tooling, and bounded execution scope;
its concrete choices remain proposals until reviewed and approved. Preserve
the [separate horizon design](case118-toy-ac-horizon-design.md) as deferred
planning material. The earlier direction to select toy periods and execute it
in Stage 0c is superseded. Reconsider horizon sensitivity after the shorter
Tracy studies establish the revised economics and operating behavior; it is
not an automatic study or a prerequisite for the Stage 0c closeout.
The retained first study reuses the existing two-main/one-helper setup and
memory gates. Retain the implemented revision-2 slow-solve ladder for
free-battery arm B. In fixed-battery R1/R2/G, the locked schedule
already determines terminal SOC, so target-free is not a substantive relaxation.
Specify and qualify an explicit initialization adaptation for those arms;
the exact choice remains open in the design checkpoint. Do not drop locks or
silently disable dependent helper starts. Preserve
causal/dependency order within each experiment and parallelize independent
work. Qualify recovery attempts against the new arm constraints/objectives;
comparison stages and controlling steps are not counts of total solver attempts.
Use it to guide toy follow-ups, the evidence/tooling preserved in 0b, and the
standard analysis of shorter Tracy runs before annual execution. This records
the scientific direction; exact diagnostic windows, implementation choices,
and numerical execution budgets remain to be specified. Do not assume that
another annual AC rollout or a particular runner implementation is needed.

**1. Where and when does the realization change?** Retain net, total absolute,
and opposing generator changes; battery power and SOC differences; losses,
curtailment, and cost components. Examine their timing and spatial
concentration alongside branch loading, voltage/reactive constraints, and
storage headroom. Extend the annual descriptive summaries into explanations
of particular episodes, preserving units, signs, state-versus-flow distinctions,
and the distinction between observed association and demonstrated mechanism.

**2. What operational value do those changes provide?** Use the existing
matched-window counterfactual proposal as the starting point: small generator
repair, economical small repair, unrestricted generator redispatch, and then
battery rescheduling under common AC constraints and storage endpoints. Use
common AC-evaluated cost components to distinguish an available small repair
from an available economic improvement. Nonconvex solves provide feasible
witnesses and observed improvements, not certified minimum repair or global
value. Evaluate whole matched windows so temporal shifts are compared under
the same energy boundary conditions.

**Approved selection change: select episodes with surrounding context before
selecting exact solve windows.** Inspect retained input, DC, and AC trajectories
first. Three-hour matched comparisons suit the existing controller, but a
battery shift across midday and evening may require a longer view. Reserve
longer counterfactual solves for a specific unresolved question supported by
that inspection. Do not equate the context span with the solve horizon or
silently expand the numerical budget.

**3. Which conditions deserve early attention in Tracy?** Develop period
selection criteria from quantities available before AC execution: net load
and ramps, renewable availability, DC loading patterns, storage headroom, and
signpost movement. Investigate these criteria using the toy AC outcomes, then
test their usefulness on the shorter Tracy runs. Carry the definitions and
selection logic forward; select Tracy periods from Tracy's own prepared inputs
and DC results. Toy outcome-selected periods do not establish prospective
predictive skill or authorize a rule for skipping AC.

The [targeted AC counterfactual proposal](case118-toy-ac-counterfactual-protocol.md)
originally proposes eight matched three-hour windows, with four stages separating
small generator repair, economical small repair, unrestricted generator
redispatch, and additional battery rescheduling. Common DC-derived starting
and ending SOC, fixed renewable real dispatch, and common AC-evaluated costs
define the comparison. The owner approved this diagnostic structure as a
starting point on the **toy fixture**, subject to the episode-first selection
change above. The original eight-window ranking is not a frozen sample for
the follow-up. Record the selected episodes, context spans, exact solve
windows, and total solve budget in its protocol before numerical execution.

Evaluate its sample in light of the input and episode review. An
outcome-selected sample does not estimate annual value;
its common three-hour horizon does not compare horizon lengths; and it does
not establish a prospective rule for skipping AC. A horizon comparison,
renewable-flexibility study, or DC-based difficulty predictor needs its own
question, comparison conditions, and protocol rather than being inferred
from these counterfactuals. The horizon design is now deferred on the toy
fixture; this first study does not select or execute toy horizon comparisons.

Complete this design checkpoint with:

- Studies selected, deferred, or declined, with scientific questions, expected
  information, source fixture, comparison conditions, and bounded scope.
- Required artifacts and extraction support to preserve/promote in 0b.
- Runner changes required for each selected study, separate from optional
  performance improvements. For the proposed counterfactuals, assess matched
  window extraction, schedule locks, repair objectives/budgets, feasible
  incumbent transfer, and cost/residual reporting. Do not assume an annual
  scheduler redesign is necessary.
- Implementation/review/commit sequence, execution budgets and stopping rules,
  and the evidence needed to decide whether to proceed to Tracy.

Keep the completed scientific record fixed. New runner implementations and
experimental results receive separate commits and identities; do not fold
them into closeout commit 1 or artifact-promotion commit 2. Selecting a study
does not bypass its protocol, implementation review, or numerical launch gate.

### 0b. Completed artifact promotion — commit 2 (`0ae9d86`)

Start after 0a is reviewed and committed and the design checkpoint is recorded,
including the separate horizon-study plan, now retained as deferred work.
Use that decision to prioritize the evidence and tooling needed by selected
toy follow-ups. This is a distinct work package with its own file disposition,
review, and commit, not a subsection of commit 1.

The Stage 0b [disposition record](../experiments/case118_annual_hierarchy/S5_ARTIFACT_DISPOSITION.md)
links the exact promotion manifest and complete inventory. Implementation and
verification of the base promotion completed with CLEAN independent scientific
review by `cvxopf-review`. The owner subsequently evaluated the expanded
dashboard and committed the package as `0ae9d86`; the disposition/validation
records distinguish those additions from the original review.

**`outputs/` triage and promotion checklist**

- [x] Inventory materials in `outputs/` produced during the completed run:
  analysis results, figures, tables, diagnostics, notebook exports, scripts,
  and explanatory notes. Record path, purpose, source/scenario identity,
  producing code or reproduction command where recoverable, and dependencies.
- [x] Assign each item a disposition: promote a durable scientific/software
  artifact into an appropriate Git-tracked location; retain a large/raw item
  in its existing archive with a tracked manifest/reference; or mark it as
  temporary, superseded, or unverified with a reason. Identify authoritative
  versions rather than promoting duplicates indiscriminately.
- [x] Propose and review an exact source-to-destination promotion list. Promote
  the selected materials and their necessary provenance/reproduction support;
  update report/notebook links so essential evidence does not depend solely
  on an unexplained ignored `outputs/` path. Respect source-data permissions
  and file sizes; do not bulk-add `outputs/` or alter ignore rules broadly.
- [x] Include the counterfactual proposal in the disposition review. If
  promoted, update its stale run-status language, repair relative links, and
  update this plan's reference to the tracked destination. Incorporate the
  approved episode-first selection change and label the original eight-window
  sample as a proposal awaiting the episode review. Preserve other scientific
  comparison conditions unless a separately reviewed decision changes them.
- [x] Preserve extraction and reporting support needed for the three approved
  questions: time- and device-aligned dispatch/SOC differences, losses and
  component costs, branch and voltage/reactive diagnostics, storage headroom,
  episode context, and pre-AC period-selection covariates. Identify support
  already available and any scoped additions needed in 0c. The separate
  horizon study's initialization/endpoints, target timing, and executed-cost
  needs were inventoried as planning context; they are now archived/deferred
  needs, not current 0c implementation requirements.
- [x] Verify promoted copies match the selected originals, references resolve,
  and figures/tables are tied to the completed toy scenario. Include the
  curated artifacts and disposition index in this separately reviewed commit.
  Preserve the originals.
- [x] Obtain the owner's approval of the triage/promotion file disposition and
  commit it as **commit 2**, separately from the scientific closeout commit.

Exit: the owner has reviewed the triage dispositions, selected artifacts have
been promoted, and that work is separately Git-committed. Report **commit 2**
and any intentionally external archives. Both commits must exist before
toy-follow-up implementation or work package A.

### 0c. Selected toy-data studies and PR closeout gate

The owner also selected a bounded
[three-window study of battery operation in AC and DC](case118-toy-battery-mechanism-test.md)
after the first study of AC dispatch adjustments. Its fixed/free and prescribed-transfer phases
propose 24 primary solves in total, retaining historical toy economics and
three-hour horizons. Complete or explicitly disposition this selected
follow-up at closeout; its detailed design, implementation and numerical
launch checkpoints remain separate. The toy horizon study stays deferred.

The [AC dispatch adjustments](../experiments/case118_counterfactual/ac_dispatch_adjustments/REPORT.md)
comparison and the 12 [battery operation comparisons](../experiments/case118_counterfactual/battery_operation/REPORT.md)
are complete, independently reviewed, and reviewed by the owner. Step 3, the
1 and 5 MWh energy-transfer comparisons, has now run from owner-committed
implementation `dd21216`. See the
[result report](../experiments/case118_counterfactual/battery_operation/TRANSFER_REPORT.md),
including the additional owner-requested June 16 5 MWh AC solve that returned
`optimal` with lower cost. Both attempts are retained. Results await owner
review; this work has not been deferred.

Implement the approved three-question framework in sequence: inspect retained
trajectories and select contextualized episodes; run the separately specified
matched-window diagnostic; assess which pre-AC selection criteria merit tests
on shorter Tracy runs. Retain this first study as a limited demonstration of
the standard analysis to replicate on Tracy. Record the toy horizon study as
explicitly deferred because the current model's temporal economics do not
match the intended research question; no horizon-specific implementation,
period selection, or solves are required in Stage 0c. Record unresolved
questions and avoid expanding toy work merely to explain consequences of those
economic assumptions. This framework is reusable across the toy and Tracy scenarios;
their inputs, numerical results, and scientific claims remain separate.

For each selected study, freeze its protocol, exact source inputs, comparison
arms, sample rule, solver/acceptance settings, and resource budget. Implement
the scoped runner changes in a separate experiment path, verify units,
identity alignment, boundary conditions, and objective/residual accounting,
and independently review and commit the implementation before numerical
execution. Preserve the original toy fixture and accepted execution tree.

For the proposed counterfactual diagnostic, retain the existing toy DC archive
and matched DC SOC endpoints. Keep Tracy scaling, siting, and storage choices
out of these experiments. Episode-first selection supersedes the original
fixed ranking as the selection workflow; settle the number and lengths of
solve windows in the reviewed protocol. Retain the proposal's restricted
renewable dispatch unless an explicit protocol change is approved. Longer
counterfactual windows require a specific unresolved question and a declared
comparison and compute budget.
Retain failed and unresolved attempts alongside feasible witnesses and observed
cost improvements; local solver outcomes are not global optimality or
infeasibility certificates.

Review the findings and remaining questions with the owner. Decide which
methods or runner capabilities to carry into Tracy, which require adaptation
to its different inputs, and whether further toy work is justified. Do not
transfer toy-specific numerical conclusions or silently expand the studies.

**Owner review outcome.** The second experiment was selected and authorized
to test the economic incentive for temporal battery shifts. In the three
selected windows, the AC model admitted meaningful cost-improving battery
cycles while the matched DC model showed no resolved benefit. This answers
the owner's present question about stronger temporal variation in battery
power value in the implemented AC model. It does not isolate individual
network effects or certify global value, and the numerical result is not a
prediction for Tracy. The separate decision on the planned step-3 comparisons
remains open.

Carry forward the following methods and capabilities:

- Keep episode selection with surrounding context, comparisons with common
  boundary conditions, separate cost components, physical audits, and saved
  attempts/results. Bind them to Tracy's inputs and device identities.
- Reuse the existing supervised execution and recovery machinery. Adapt
  dimensions, starts, resource estimates, and reporting to the new generators,
  27 batteries, renewable sites, and load policy. Do not reuse toy solution
  arrays or assume its performance estimates apply unchanged.
- Make generator curvature and battery throughput regularization explicit
  shorter-study parameters as specified in section 5. A changed economic
  configuration requires its own consistent DC plan and AC inputs/signposts.
- Add the optional last-resort load-shedding configuration and report unmet
  demand explicitly. This is a separately requested Tracy capability, not a
  conclusion inferred from the toy experiments.

The exact sensitivity values, shedding costs, selected shorter windows, and
their compute budgets remain for the existing Tracy review gates. This
disposition does not launch Tracy input generation or numerical work.

**End-of-Stage-0c PR closeout checklist**

- [ ] Record each selected toy follow-up as completed and reviewed, explicitly
  deferred, or stopped, with its evidence, remaining questions, and reason.
  Current disposition: the AC dispatch comparisons and the 12 battery
  operation comparisons are complete, independently reviewed, and reviewed by
  the owner. The prescribed energy-transfer comparisons have now run and
  await owner review. The additional owner-requested June 16 5 MWh AC solve
  returned `optimal` with lower cost; both attempts are retained. The toy AC
  look-ahead-horizon study is deferred for the economic-model reason above.
- [ ] Finish documentation and relevant regression checks. Record their
  outcomes, retained limitations, and the methods or capabilities to carry
  into Tracy. Obtain the owner's decision to move on to the new study.
- [ ] **User action, not agent action:** merge `big-experiment`, update local
  `main`, and verify the merged state. Record the merged baseline commit and
  verification outcome in the handoff.
- [ ] **User action, not agent action:** create a fresh Tracy-study branch
  from the verified local `main` before Stage A input generation. Record its
  branch name and baseline commit in the handoff.

Exit: the toy-follow-up disposition and documentation/regression checks are
complete, the owner approves the transition, and the user has completed the
merge, local-main verification, and fresh-branch actions above. This is the
PR closeout gate for the analytical benchmark. Including this future-work
plan in that PR does not begin the Tracy study. Stage A does not begin
automatically after artifact promotion or toy-study review.

### A. Generate inputs and obtain user review 1, without OPF solves

Prerequisite: Stage 0c is closed and the user has created the fresh Tracy-study
branch from verified `main`. Generate Tracy inputs on that branch, with the
merged analytical benchmark as the recorded baseline.

1. Preserve the analytical fixture, outputs, and existing uncommitted work.
   Give the Tracy fixture and output root distinct readable identities.
2. Implement only the declared source adapter and mapping using existing
   component APIs. Fail clearly on absent/wrong data; never call the analytic
   profile generator as a fallback.
3. Persist exact prepared arrays, the 118-bus role table, device IDs, resource
   weights, generator and battery capacities, source provenance, RNG protocol,
   version information, and every transformation. Use full-precision values,
   not rounded summary values, to construct devices.
   Include a per-generator table of inherited c0/c1, original/updated Pmax,
   derived c2, linear/quadratic costs at updated Pmax, their ratio, and marginal
   costs at zero/full output. Record actual battery weights and the complete
   objective with units, defaults, and explicit overrides. Independently check
   the starting one-third rule, each declared curvature setting, and resolved
   battery default against the devices supplied to both formulations; an
   unexpected override is a discrepancy to resolve. Include the load-shedding
   policy, actual penalties, fraction limits and eligible device identities.
4. Produce annual/monthly summaries, annual and representative-week plots of
   all four channels and net load, and a network map showing roles and sizes.
   Deliver the full side-by-side final-input heatmap and tabular comparison
   specified in section 4: scaled Tracy-derived Case118 versus the frozen toy
   scenario. This is where the comparison is first assembled. Reuse the
   committed 0a toy arrays, summaries, and plotting logic; render paired plots
   with shared scales rather than generating new historical toy inputs.
   Include named source-to-prepared row checks and the Dec 18–21 M17 window.
5. Independently reconstruct hourly aggregate channels and selected bus rows
   directly from the CSV and saved mapping, not by rerunning the same builder.
   Check the actual arrays reaching both DC and AC model construction.
6. Add focused tests for source identity, calendar, conservation, device
   alignment, random reproducibility, and rejection of silent substitution.
   Use ordinary repository test commands. Keep this stage's tests input-only;
   numerical solves begin in Stage B.

**User review 1 is an explicit stop after generation, before DC solves.**
Present one review package containing the comparable input heatmaps/tables,
source-to-input transformations, realized bus/device map, capacity allocations,
sanity-check results, and the section 5 choices needed for the proposed DC
screening. Include the economic table and explain the scale of generation
curvature, battery regularization and shedding penalties, not just their
coefficient values.
Ask whether these actual inputs and data-generation choices are the
intended study. Record the owner's approval or requested revisions against a
specific fixture version/digest; showing the package is not approval. Revise
and re-present affected material before proceeding if the owner changes it.

Exit: source, prepared curves, realized mapping, and model inputs agree; the
sanity check passes; DC-relevant operating choices are resolved; and the owner
approves this concrete realization and a bounded targeted-DC protocol. A
matching digest alone does not establish source fidelity.

### B. Targeted week-to-month DC studies before the annual DC solve

Do not jump from initial data generation to an 8,760-hour DC solve. Use the
frozen input series, realized siting, branch ratings/topology, and available
generator/storage capacities to predict where congestion and difficult
periods are likely. Screen regional injection/export patterns and constrained
corridors as well as system-wide net load. Label transfer/congestion scores
as model-based indicators, not proof of AC deliverability; any additional
optimization-based screening belongs inside the approved solve budget.

Before solving, document a small set of **contiguous, non-wrapping windows on
the order of weeks to months** (for example, 2–4 weeks around selected events
and a 1–3 month span for persistent stress), their selection rationale, and
their complementary coverage. Include predicted import/export bottlenecks,
the peak/sustained deficit, high gross load, renewable surplus, large ramps,
and potential SOC depletion/recharge difficulty; retain an ordinary-period
control. Several criteria may select the same window. Do not substitute only
isolated hours or short prefixes for these coupled studies, and do not use
results from a not-yet-authorized annual solve to select them.

Run the existing rated lossy-DC formulation on those windows with the same
full-year-derived capacities, allocation weights, and physical/economic rules.
Vary only the declared generator-curvature and battery-weight settings in
matched comparisons; keep the load-shedding policy explicit and consistent
unless its penalty is itself being checked. Do not resize resources to each
window. Predeclare initial/terminal SOC,
context/padding, solve-count/time/memory limits, tolerances, and stopping rules.
For potentially consequential finite-window boundary effects, use a bounded,
declared boundary sensitivity rather than interpreting an arbitrary initial
SOC or terminal target as a physical property of the year.

Within this bounded shorter-study program, assess generator curvature starting
from rho = 1/3 and battery regularization starting from 0.01. Include an
explicitly specified almost-linear generator setting. Select a small set of
comparisons and solve budget in the protocol, varying curvature and throughput
weight separately and, where justified, together to examine their interaction.
An exhaustive parameter grid is not required. Match windows, inherited linear
costs, capacities, physical constraints, and storage endpoints. Compare
generation leveling and marginal-cost variation,
battery power/throughput and SOC, and generation, regularization, and other
objective components, including shedding, separately. Changing cost
coefficients changes the objective: do not interpret a lower total objective
under lower curvature or throughput weight as an operating
improvement by itself. Inspect congestion and endpoint restrictions alongside
the incentives when explaining idle or active storage. Use this evidence to
recommend annual curvature and battery-weight settings; the starting values
are not automatically the final choices. Check the intended last-resort
shedding behavior under the settings proposed for annual use.

Report residuals and load service; branch utilization/binding intervals and
locations; dispatch, available/used renewables and curtailment; losses;
storage power, SOC, depletion/recharge, and terminal effects; numerical
conditioning, wall time, and memory. Distinguish observed DC difficulty from
predicted difficulty and from untested AC behavior. Successful windows do not
prove annual feasibility. Diagnose failures without silent siting, seed,
rating, or source changes.

Exit: the owner receives the targeted-DC findings, the curvature/regularization
sensitivity and proposed annual economic settings, and any other revisions,
plus a full-year DC runtime/resource estimate. Obtain explicit approval for
the annual DC solve only after the windows are reviewed. A changed fixture
must return through the affected input-review and qualification checks.

### C. Full 8,760-hour DC solve and user review 2

After the targeted-DC gate, solve and physically audit the new annual convex
outer problem. Persist its full primal trajectory, complete analysis, source
identity, and SOC signposts. Inspect annual boundary conditions and continuity,
not just successful solver status.

**User review 2 is an explicit stop after the Tracy annual DC solve, before any
Tracy AC solve.** Re-present the data-generation choices with their full-year DC
consequences:

- The exact reviewed final-input heatmaps/tables and proof that those arrays
  reached the solved model; make any approved changes since review 1 visible.
- Hours-by-days output heatmaps for dispatch, renewable use/curtailment, storage
  charging/discharging and SOC as useful, distinctly labeled as outputs.
- Annual/monthly balance and cost summaries; congestion locations, severity,
  and duration; losses; dispatchable shortfalls and storage response; boundary
  effects and residual audits; observed computational difficulty. Include any
  planned shedding, its times/locations, energy not served and separate cost.
- A clear assessment of whether the scaling, siting, renewable allocation,
  and storage design produced the intended regime: useful congestion and
  storage activity with feasible rated-network DC operation. Explain the
  remaining limitations of using DC evidence to predict AC feasibility.

Ask the owner to retain or revise the data-generation/configuration choices
and separately approve a bounded AC qualification. Record the decision against
the annual result and fixture. Do not treat completed DC execution as automatic
permission for AC. If the configuration changes, regenerate affected inputs
and results rather than reusing stale signposts or concealing the revision.

Exit: accepted annual DC evidence and explicit owner acceptance of the
configuration in light of it. Only then derive the new shard boundaries and
states using the reviewed rule for the approved trajectory; never copy old
states or assume old boundaries remain appropriate.

### D. Bounded AC qualification, separately authorized after user review 2

Freeze the selected windows, initial-state provenance, solve-count/time/memory
budgets, recovery policy, acceptance tolerances, and stopping rules before
launch. Resolve the remaining AC operating choices from section 5 first.

Use the annual DC evidence and inputs to select a small AC qualification set
spanning ordinary operation, predicted/observed congestion, peak net load,
the sustained February deficit, high gross load, renewable surplus, large
ramps, and actual new shard starts. Allow conditions to overlap so this is
not an unnecessarily large test matrix. Include coupled windows long enough
to expose depleted SOC and recharge needs, not only isolated peak snapshots.
Exercise one representative shard join and checkpoint/restart path, reusing
unaffected correctness evidence.

Check load service, branch terminal MVA, voltage and reactive limits, storage
SOC and P/Q limits, losses, curtailment, boundary continuity, and residuals.
When shedding is enabled, audit served demand and shedding bounds/costs and
compare AC implemented shedding with the DC plan. Report full-service and
shedding-assisted operation separately, retaining the same declared cost
configuration. Any additional cost sensitivity requires an explicit bounded
comparison with consistent DC plans rather than mismatched signposts.
Track the distinction between physical infeasibility, a restrictive outer
signpost/controller policy, and local solver failure. Detached diagnostic
windows cannot be stitched into an accepted annual path.

If a problem arises, identify whether it is siting/deliverability, active or
reactive power, stored energy, recharge, boundary conditions, or numerical
behavior. Bring a documented targeted revision; do not quietly redraw sites,
retune source channels, expand budgets, or jump to larger storage. The six-hour
storage sensitivity is the declared next size, not a universal remedy.

Exit: independently reviewed qualification evidence plus a credible annual
runtime/resource estimate and unresolved-risk summary. Representative success
screens configurations; it does not establish full-year AC feasibility.

### E. Annual AC execution, after an explicit launch decision

Reuse the vectorized outer builder, controller, checkpoint/archive machinery,
supervision, and reviewed recovery policy. Do not repeat the full historical
S0-through-S4b engineering campaign or introduce a new scheduler research
project as part of this input replacement.

Create new scenario-bound execution records and authority bound to the already
accepted Tracy outer dispatch, SOC signposts, and shard states from package C.
Do not repeat the annual DC solve merely to launch AC. Generate the new AC
actions, recovery outcomes, and scientific metrics. No toy-study numerical
solution or checkpoint becomes part of the Tracy trajectory.

The old run's approximately 139.7 active supervisor hours is budgeting context,
not a forecast. In particular, moving from four to 27 storage devices and to
many renewable devices changes model size as well as operating conditions.
Use measured qualification memory and timing to budget before launch. Freeze
the implementation checkpoint by the project's reviewed procedure; commit
only when the owner authorizes it.

### F. One final analysis and a clearly identified report

At completion, perform the required independent physical/trajectory analysis,
save its complete result, and promote that same validated object without
repeating full reconstruction. Reuse the one-pass promotion path reviewed
during old-study closeout, verifying any subsequent changes before use.

The owner requested a dashboard similar to the toy explorer, adapted to the
new run. Preserve reusable loaders, metrics, calendar views, and stress/timing
plots during Stage 0b. Design the Tracy dashboard when its inputs and runner
outputs are available: use its actual time axis, fleet and renewable inputs,
storage configuration, and study-specific diagnostics. Keep the frozen toy
explorer as a separate reference; the future dashboard is not implemented in 0b.

Write the Tracy report and rebuild its plots, dashboard, stress correlations,
and any counterfactual selection from the new accepted results. Preserve
the analytical benchmark separately and qualify comparisons as described
above. No conclusion about Tracy is inherited from the analytical run.

## 7. Handoff and completion criteria

Begin with **work package 0a: old-study scientific/Git closeout
(commit 1)** and its **experimental-design pause**, followed by **0b: separate
`outputs/` triage/promotion (commit 2)** and **0c: selected toy studies and the
PR closeout gate**. The user performs the merge, local `main` update and merged
state verification, and fresh Tracy-study branch creation. After those gates,
hand off the new-study plan plus one compact list of the open operating choices in
section 5. Use the existing builder and independent reviewer for a scoped
build-review loop at each work package.

For each implementation checkpoint, review source fidelity and scientific
meaning as well as software correctness. At a clean handoff, collect proposed
commit text and a file-by-file disposition for owner review.
Keep scientific closeout commit 1, the design checkpoint, artifact-triage
commit 2, toy-study reviews/transition, the Stage 0c PR closeout and fresh-branch
gate, user review 1, targeted-DC review/annual-DC
approval, user review 2, bounded AC approval, and annual AC launch approval
distinct. No input check or completed DC solve is implicit permission to spend
another annual AC run's compute budget.

The final study is complete only when the owner-approved Tracy realization
has a full accepted trajectory with audited boundaries and physical residuals,
durably saved analysis, visible input lineage, and a report whose claims match
the actual scenario.

## 8. Decision provenance

For the rationale behind the design choices, consult these source tasks:

- `cvxopf-coordinator`: `01a065c7-592c-7840-ae6a-78408aedb5c4` (owner decisions).
- `cvxopf-discuss`: `01a08988-3eef-79d0-bdaa-6c8458658878`.
- `cvxopf-review`: `019fa526-1222-7443-8c5b-a13f55f6b56b`.
- `cvxopf-build-2`: `01a0b007-bfeb-7083-b1b4-5172d6b0b20a` and
  `plans/s5-tracy-recovery-plan.md`.

The specifications and gates in this document govern the study; earlier
proposals are background references, not additional execution requirements.
