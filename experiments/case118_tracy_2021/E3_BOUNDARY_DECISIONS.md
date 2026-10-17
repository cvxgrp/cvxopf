# E3 boundary decisions

## Deficit depletion — owner decision, 2026-10-02

Following the [no-solve window proposal](e3_selection/REPORT.md), the owner
selected **60% initial → 25% terminal SoC** for a separately labeled large-deficit
depletion leg. This decision supplements the original selection checkpoint;
its ranking, source hashes and generated evidence remain unchanged.

For each of the 27 Stage A batteries with identity `device_id[k]` and modeled
usable capacity `capacity_mwh[k]`:

- Initial boundary: `soc[0, k] = 0.60 * capacity_mwh[k]`.
- Terminal boundary: hard equality `soc[24, k] = 0.25 * capacity_mwh[k]`.
- Interior boundaries: ordinary storage bounds and dynamics only; no annual
  trajectory targets.

Use identical identity-aligned initial and terminal vectors in `singlenode_dc`,
`lossy_dc`, `socp` and `ac`. This is a per-device condition, not a fleet-sum
condition, soft penalty, or terminal lower bound. It requires a net withdrawal
of 35% of each battery's capacity over the horizon; it does not establish
physical feasibility or prescribe the hourly dispatch.

The annual lossy-DC fleet endpoints of 57.2% → 24.8% motivate this boundary
choice but are not the new boundary vectors. Individual annual states differ;
this is a controlled depletion scenario, not an annual dispatch replay.

## Run matrix and remaining approvals

Retain the 16 primary 50%-to-50% arms (four events × four formulations).
Add four large-deficit depletion arms, separately labeled and reported:
**20 proposed problems total**, with no implicit retries, recovery solves or
additional sensitivity legs. Do not replace or pool the energy-neutral deficit
results with the depletion results.

The proposed deficit window is `[1165, 1189)`, February 18, 2021 13:00 through
February 19, 2021 13:00, fixed UTC−08. The owner subsequently accepted all four
proposed windows. The boundary/date decisions do not authorize execution or
resume the held Stage D study.

The [bounded numerical protocol](E3_PROTOCOL.md) and runner freeze the explicit
27-device vectors, source identities, solver settings, per-arm resource limits,
total attempt budget and run order for all 20 problems. Resource ceilings other
than the approved 16-GiB RSS limit remain proposals. The runner and protocol
still require review and commit, followed by separate execution authorization.
