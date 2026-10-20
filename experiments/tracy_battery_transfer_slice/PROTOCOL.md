# Hour-2452 fixed-battery AC comparison

Owner authorization: “Yes, I agree. I want to see that test”, following the
explicit four-case single-hour comparison. This is a new isolated diagnostic,
not a continuation or reclassification of earlier transfer experiments.

Run four full-network AC OPFs for global input hour [2452,2453), in order:
the retained accepted AC schedule (positive control), SOCP, lossy DC and
copper plate. Use the ordinary neutral deficit-to-surplus [2439,2463) source
case. Pin only the selected schedule's battery real powers at hour 2452.
All generation, battery reactive power, renewable dispatch, load interruption
and network states remain decision variables under unchanged operating limits.
Each battery's initial energy is its original initial energy minus the integral
of its own retained real powers over [2439,2452); its next energy follows ideal
dynamics. Do not impose the original end-of-day target at the one-hour endpoint.
Independently check both one-hour energy boundaries and the source's full-day
trajectory/endpoints. No rounding, clipping or repair of battery schedules.

The current fleet has no caller-supplied coupling or HVDC; generators,
nondispatchable devices and loads are memoryless. Battery ideal dynamics are
the only temporal coupling. Refuse unexpected inputs rather than dropping them.
Use the public T=1 vectorized multistep builder so the same production numerical
preparation, cost coordinates, component-first economic objective and audit
helpers apply. This is mathematically one time step. Slice every input frame
by position 13, preserving identities and units. Retain the original economic
objective, coefficients, exact Hessian and unchanged E3 prepared IPOPT options.

Initialize each case from the retained accepted AC logical solution at that
hour, replacing only b and its implied two SoC boundaries. Production start
assignment restores structural fixed entries; no perturbations or dual reuse.
These are retrospective diagnostic starts, not causal controller starts.
No convex solves, homotopy, retry slots, constraint relaxation or penalties.

At most four sequential fresh workers, 300 total process-wall seconds and
8192 MiB sampled process-tree RSS each, 1200 cumulative worker-seconds. The
supervision mechanics (process-tree sampling and group termination) are reused
in this runner with the explicitly stricter 8-GiB RSS ceiling. No production
supervisor is edited. Preparation and audits consume worker time.
Required process/RSS and thermal access is checked before each launch; power
source is informational. A failed/timed-out/unaudited AC control stops all
donors. Donor native rejection or timeout is retained and advances to the next
declared donor; audit/infrastructure/monitoring/RSS/STOP failure stops the run.
No automatic resume, retries, other hours or 24-hour solves.

Before execution freeze unchanged committed production sources, installed
numerical environment, this uncommitted diagnostic's scripts and protocol,
retained source hashes, previous experiment trees, each slice's inputs,
source schedule, raw/assigned start and prospective canonical x0. No commit is
inherently required for this isolated diagnostic using unchanged production.
Retain native/preparation evidence, results, resource journal, solver log and
completion hashes. Independently replay every completed accepted candidate
through fresh construction and the unchanged engineering-unit network/device,
accounting, battery pin and one-hour endpoint gates. Native success without
successful finalized supervision and passing audits is not acceptance.

Report per-case outcome, native exit/iterations/residuals when available,
audited constraint residuals, costs, shedding, battery reactive dispatch,
voltages at buses 70–73 and branch-terminal margins. A success establishes
only this hour's feasibility, not full-day compatibility or global optimality.
Timeout/rejection is not an infeasibility certificate. Historical E3/E3+,
Stage D and transfer artifacts remain untouched. No insertion or live-state
change. All new raw evidence lives in this experiment's ignored results/.

## Owner-approved bookkeeping correction and remaining-case continuation

The initial `results/slice_001` stopped after completing AC control and SOCP.
The worker's rejected-point check omitted `native_status`, while independent
replay included it. The owner explicitly authorized correcting this bookkeeping
and running only the unlaunched lossy-DC and copper-plate cases.

The corrected worker retains that field. Read compatibility is restricted to
the exact rejected legacy call-2 record (native status 2, passed false); it
cannot manufacture acceptance or suppress a changed audit. Preserve all
predecessor records and its finalized failure, plus a hashed copy of its original
scripts/protocol in the adjacent ignored `results/slice_001-source` directory.
Create a fresh `results/slice_002` binding that hashes and independently replays
the two completed predecessor bundles, verifies unchanged production code,
environment, model, mathematical inputs and starts, and launches global calls
3 and 4 only. Carry forward both launches and their supervised worker-seconds
against the original four-launch/1200-second ceilings. No budget reset, copied
result rewriting, numerical rerun, new starts, tolerance changes or source-result
reclassification. Report inherited and new evidence together via the corrected
read-only `--status --output .../results/slice_002` command.
