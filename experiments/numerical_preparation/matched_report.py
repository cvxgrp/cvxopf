"""Non-solving all-arm table and paired figures from immutable publications."""
import time

from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from experiments.case118_tracy_2021.stage_d import read
from . import matched_model as model


def table(progress, root):
    result = []
    for row in progress["rows"]:
        arm, disposition = row["arm"], row["disposition"]
        latest = next((a for a in reversed(progress["attempts"]) if a["arm_id"] == arm["id"]), None)
        attempts = [a for a in progress["attempts"] if a["arm_id"] == arm["id"]]
        record = None
        if latest and (root / latest["path"] / "completion.json").exists():
            record = read(root / latest["path"] / "result.json.gz")
        audit = {} if record is None else record.get("audit", {})
        physical, economic = audit.get("physical", {}), audit.get("economics", {})
        native = {} if record is None else record.get("native") or {}
        result.append(dict(arm=arm, accepted=bool(disposition and disposition["accepted"]),
            disposition=None if latest is None else latest["classification"],
            worker_classification=None if record is None else record["classification"],
            native_status=native.get("status"), native_message=native.get("status_msg"),
            iterations=native.get("iterations", native.get("num_iters")),
            physical_cost=economic.get("physical_objective"),
            canonical_cost=economic.get("canonical_objective"),
            cycling_slack=economic.get("cycling_epigraph_excess"),
            cycling_warning=economic.get("cycling_warning"),
            costs=physical.get("costs"), residuals=physical.get("residuals"),
            ens_mwh=physical.get("metrics", {}).get("energy_not_served_mwh"),
            timings=None if latest is None else latest.get("timings"),
            supervised_seconds=None if latest is None else latest.get("worker_seconds"),
            attempt_count=len(attempts),
            all_attempt_supervised_seconds=sum(a.get("worker_seconds", 0.) for a in attempts),
            sampled_peak_rss_mib=None if latest is None else latest.get("peak_sampled_rss_mib")))
    return result


def render(root, progress):
    """Write only a new named analysis bundle; never modify numerical evidence."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory = root / "reports" / f"report-{time.time_ns()}"
    directory.mkdir(parents=True, exist_ok=False)
    rows = table(progress, root)
    atomic_immutable_json(directory / "summary.json", dict(progress=progress, all_arm_table=rows,
        warning="bounded case study; local AC solutions, not global optima; native timers are diagnostic"))
    fig, axes = plt.subplots(1, 2, figsize=(12, 6), layout="constrained")
    pairs = sorted({row["arm"]["pair"] for row in rows})
    labels = []
    for index, pair in enumerate(pairs):
        members = [row for row in rows if row["arm"]["pair"] == pair]
        arm = members[0]["arm"]
        labels.append(f"{arm['scenario']} / {arm['formulation'].upper()} / T={arm['T']}")
        for row in members:
            shift = -.18 if row["arm"]["treatment"] == "baseline" else .18
            color = "#777777" if shift < 0 else "#2878b5"
            if row["accepted"]:
                axes[0].barh(index+shift, row["timings"]["phases"]["public_solve"], height=.32, color=color)
                axes[1].barh(index+shift, row["physical_cost"], height=.32, color=color)
            else:
                axes[0].text(0, index+shift, row["worker_classification"] or row["disposition"] or "pending", fontsize=7)
    for axis in axes:
        axis.set_yticks(range(len(pairs)), labels)
        axis.invert_yaxis()
        axis.grid(axis="x", alpha=.2)
    axes[0].set_xlabel("Public solve call (seconds): preparation + native solve + inversion")
    axes[1].set_xlabel("Independently reconstructed physical objective")
    fig.suptitle("Baseline (gray) vs prepared (blue); accepted outcomes only, failures retained in table")
    fig.savefig(directory / "paired.png", dpi=150)
    plt.close(fig)
    atomic_immutable_json(directory / "manifest.json", dict(
        source_binding_sha256=model.digest(root / "binding.json"),
        evidence=[dict(path=a["path"], completion_sha256=model.digest(root / a["path"] / "completion.json"))
                  for a in progress["attempts"] if (root / a["path"] / "completion.json").exists()],
        files={name:model.digest(directory / name) for name in ("summary.json", "paired.png")}))
    return dict(report=str(directory), figure=str(directory / "paired.png"))
