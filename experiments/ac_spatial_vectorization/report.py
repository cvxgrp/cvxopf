"""Read-only paired comparisons from audited or explicitly censored work."""

from itertools import groupby
from statistics import median

import numpy as np

from .run import check_publication, read


def compare(root, status):
    outcomes = status["terminal"]
    paired = []
    keys = ("phase", "fixture", "repeat")
    for identity, units in groupby(read(root / "binding.json")["work"], lambda u: tuple(u[k] for k in keys)):
        items = {u["revision"]: outcomes.get(u["id"]) for u in units}
        row = dict(zip(keys, identity, strict=True))
        rows = {side: a["disposition"] if a is not None else "pending" for side, a in items.items()}
        row["dispositions"] = rows
        baseline, batched = (items[k] for k in ("baseline", "batched"))
        wanted = "measured" if row["phase"] == "compile" else "accepted"
        row["comparable"] = all(rows[k] == wanted for k in ("baseline", "batched"))
        if wanted == "accepted" and row["comparable"]:
            for field in ("start_sha256", "native_options_sha256"):
                if baseline["metrics"][field] != batched["metrics"][field]:
                    row.update(comparable=False, reason=f"Matched solve {field} values differ")
                    break
        if row["comparable"]:
            clocks = (("build_seconds", "preparation_seconds", "reduction_seconds", "derivative_seconds")
                      if wanted == "measured" else ("build_to_extraction_seconds",))
            for field in clocks:
                a, b = baseline["metrics"][field], batched["metrics"][field]
                row[field] = dict(baseline=a, batched=b, baseline_over_batched=a/b if b else None)
            row["sampled_peak_rss_mib"] = {s: a["peak_rss_mib"] for s, a in items.items()}
            if wanted == "accepted":
                records = [check_publication(root / a["path"], "solve") for a in (baseline, batched)]
                row["objective_delta_batched_minus_baseline"] = (
                    batched["metrics"]["physical_objective"] - baseline["metrics"]["physical_objective"]
                )
                row["dispatch_and_state_max_abs_delta"] = {
                    key: float(np.max(np.abs(np.asarray(records[1]["result"][key]) - records[0]["result"][key])))
                    for key in ("Pg", "Qg", "Vm", "Va_deg", "b", "soc", "p_load_shed")
                    if all(r["result"].get(key) is not None for r in records)
                }
                row["native"] = {s: a["native_log"] for s, a in items.items()}
                row["physical_residuals"] = {s: r["audit"]["residuals"]
                                             for s, r in zip(("baseline", "batched"), records, strict=True)}
        paired.append(row)
    summaries = {}
    for row in paired:
        if row["phase"] != "compile" or not row["comparable"]:
            continue
        summary = summaries.setdefault(row["fixture"], {})
        for name, values in row.items():
            if isinstance(values, dict) and values.get("baseline_over_batched") is not None:
                summary.setdefault(name, []).append(values["baseline_over_batched"])
    return {**status, "pairs": paired, "compile_ratios": {
        fixture: {name: dict(pairs=len(values), median=median(values), min=min(values), max=max(values))
                  for name, values in summary.items()} for fixture, summary in summaries.items()
    }}
