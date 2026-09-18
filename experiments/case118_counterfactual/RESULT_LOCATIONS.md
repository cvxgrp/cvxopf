# Result locations

The AC dispatch adjustments experiment and the battery operation experiment
were incorrectly saved under the repository's `outputs/` directory.
Their files now live here with the experiment. This move changes organization
and explanatory wording, not numerical results or the scope of either study.

| Contents | Current location |
| --- | --- |
| AC dispatch report, launch protocol, context, verification and plotting scripts | `ac_dispatch_adjustments/` |
| Successful AC dispatch solve records | `results/ac_dispatch_adjustments_20260917/` |
| Initial launch failure before any solve | `results/ac_dispatch_adjustments_20260917_failed_launch/` |
| Battery operation report, launch protocol and verification | `battery_operation/` |
| Battery window selection and surrounding context | `battery_operation/screening/` |
| AC and DC battery comparison solve records | `results/battery_operation_20260917/` |

All 171 files were checked against their original byte counts and SHA256 hashes
immediately after moving. `RESULT_LOCATIONS.json` records those original hashes
and the old-to-new path mapping. Reports, analysis scripts and descriptive
figure labels were then corrected; their original hashes document the move,
not their edited contents. Raw execution records, launch protocols, source
contexts, and numerical verification outputs are unchanged.

Some unchanged records contain the former paths and the incorrect historical
label. Rewriting them would invalidate the recorded hashes, so they retain the
original text. Current documentation calls the experiments by their scientific
questions. Links at the old local paths preserve references in the execution
records and earlier conversation; the actual files are here. The verification
scripts also resolve these recorded paths through `retained_files.py`, so they
do not depend on those compatibility links.

As in the annual experiment, `results/` holds retained local solver artifacts
and is Git-ignored. Reports, protocols, selection data, context, figures and
analysis scripts outside it are available for the next commit. No new commit
or optimization is part of this correction.

From the repository root, verify the retained numerical results without solving:

```sh
uv run --extra dev python -m experiments.case118_counterfactual.ac_dispatch_adjustments.check_results
uv run --extra dev python -m experiments.case118_counterfactual.battery_operation.check_results
```

Each script writes its checks to stdout. The dispatch checker reconstructs the
four comparisons' costs; its retained `verification.json` separately records
execution time, memory and artifact checks. The battery checker reproduces its
retained `verification.json` exactly. Source checks compare the committed
runtime used for each run with its recorded fingerprint. Adding these analysis
scripts does not redefine the runtime that
produced the historical results.
