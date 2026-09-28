# Experiments and reproducibility

This folder contains study code, protocols, reports, and selected compact
evidence. Large input files and raw execution records are generally retained
locally, excluded from Git, usually under each experiment's `results/`.
**A repository clone is therefore not a complete archive of every study.**
Consult each study's README and protocol for its commands and requirements.

## What can be reproduced from a clone?

There are three distinct tasks: running a new instance of a scenario,
recomputing a report from archived results, and replaying an exact historical
problem and initialization. Their file requirements differ.

| Example | Availability and additional requirements |
|---|---|
| [DNLP versus Pypower](dnlp_vs_pypower/README.md) | Toy problem and reference results are tracked; new solves require the documented software and native solvers. |
| [Battery terminal policies](battery_terminal/README.md) | Requires the owner-provided, ignored `battery_terminal/data/9q9wtp_gen_and_load.csv`. |
| [M17 hierarchical battery resilience](hierarchical_battery_resilience/README.md) | Prepared Tracy-derived scenario inputs are tracked. Running that frozen scenario does not require the original raw CSV; reproducing historical comparisons also requires the referenced local result archives. |
| [Case118 annual hierarchy](case118_annual_hierarchy/RESULT_LOCATIONS.md) | Network source and deterministic synthetic-profile generation are tracked. This annual scenario is **not Tracy-derived**. Historical analysis and downstream replays require retained outer plans, signposts, and execution records. |
| [Case118 vectorization replays](case118_vectorization_replay/README.md) | Exact historical windows depend on retained sample selections, physical states, targets, initialization artifacts, and source results. See also the [space/time replay](case118_spacetime_pq_replay/README.md). |

## What the owner should provide for full reproduction

For a particular study, supply the following where applicable, including
upstream artifacts referenced by its manifests—not just the final report:

- **Inputs:** missing raw or prepared data, network/device configurations,
  timestamps, units, and identity mappings. Raw source data is additionally
  needed to verify preparation from scratch when only derived inputs are tracked.
- **Historical solve inputs:** outer plans and signposts, selected windows,
  initial states and endpoint targets, preceding accepted solutions, and
  recovery-source solutions. Include retained named starts and complete solver
  starting vectors with coordinate layouts where the replay uses them.
- **Execution evidence:** raw results and attempt records, including rejected
  and failed attempts; audits, checkpoints, and intervention records. Include
  timing/resource logs and operator-event notes for performance claims.
- **Code and environment:** the recorded source commit (plus any recorded
  uncommitted source snapshot), effective settings and random seeds, dependency
  versions, and native solver/linear-algebra versions. The current lockfile
  alone does not describe every historical environment.
- **An inventory and instructions:** existing manifests, file hashes, and
  restoration locations. Follow study-specific `RESULT_LOCATIONS.md` and
  relocation manifests when present; do not rewrite hashed historical records
  merely to change paths. Hashes identify files but do not supply their contents.

Package missing files as a separate, shareable archive with appropriate data
permissions, rather than committing large raw runs. The required bundle is
study-specific; it need not include unrelated experiments or local material.

## What reproduction means numerically

Use the recorded code, settings, and acceptance tolerances, not today's package
defaults. Reanalysis of retained evidence and fresh numerical execution are
different checks. Nonconvex solves can reach different trajectories across
solver stacks, and timing depends on hardware and operating conditions;
providing the full archive does not promise bit-for-bit reruns or identical
runtimes. Report those differences rather than silently replacing historical
evidence.
