# Time-only replay result locations

Maintained tools are the experiment's `analyze.py`, `sample.py`, `run.py`, and
`worker.py`; their presence does not authorize a new run or sample generation.
The accepted report is [REPORT.md](REPORT.md), with selected evidence in `artifacts/`.

`results/case118_vectorization_replay/` retains the original frozen sample,
phases, starts, results, telemetry, lifecycle records, and comparison outputs.
Copies selected for `artifacts/` are intentional. Original records remain
unchanged and are not competing maintained source files.

`RESULT_LOCATIONS_20260921.json` is the exact old/current file mapping with
preserved sizes and hashes. Paths inside historical evidence stay unchanged.
The owner approved retaining these locations after review; the original root
rename did not authorize the broader historical move. Raw results remain ignored.
