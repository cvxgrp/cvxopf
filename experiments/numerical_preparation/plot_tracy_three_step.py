"""Plot pinned, device-aligned retained trajectories; never construct or solve OPF.

Run with ``uv run --extra notebook python -m
experiments.numerical_preparation.plot_tracy_three_step``. Output goes into a
fresh ignored directory, outside both immutable execution trees. Real powers
are interval-constant; storage energy is plotted at all four boundaries. DC
reactive channels remain absent, not zero. No historical gate is reevaluated.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "results/tracy_three_step_001"
HISTORY = HERE / "results/qualification_001"
OUTPUT = HERE / "results/tracy_three_step_plots_002"
PINS = {
    "binding.json": "06d231f5771bee48c91a8eb54bac06d641fe05a1760b70a756aa7ef2eba17d1c",
    "report.json": "442d98184ca10fdd3f86a2aa9845a227af76a2a9825174d7f154c0afd03d1425",
}
METHODS = {
    "ac_baseline": ("AC baseline", "#c33d43", "-"),
    "ac_combined_ac": ("AC combined", "#007f70", "-"),
    "socp": ("SOCP (lifted)", "#7759af", "--"),
    "lossy_dc": ("Lossy DC", "#287abe", "-."),
    "singlenode_dc": ("Copper plate", "#d99720", ":"),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as stream:
        return json.load(stream)


def verify_hash(root, name, expected):
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()) or digest(path) != expected:
        raise ValueError(f"pinned artifact mismatch: {path}")


def load_retained(source=SOURCE, history=HISTORY):
    """Verify pinned report/binding, manifests and their public-array agreement.

    This is artifact inspection, not source-bound scientific audit replay.
    No imports of project builders, solver bridges or experiment runners.
    """
    for name, expected in PINS.items():
        verify_hash(source, name, expected)
    binding, report = read(source / "binding.json"), read(source / "report.json")
    for name, expected in binding["historical_pins"].items():
        verify_hash(history, name, expected)
    for label, root, number in (("socp", source, 1), ("lossy_dc", source, 2),
                               ("singlenode_dc", source, 3),
                               ("ac_baseline", history, 24),
                               ("ac_combined_ac", history, 25)):
        directory = root / f"call-{number:03d}"
        completion = read(directory / "completion.json")
        for name, expected in completion["artifacts"].items():
            verify_hash(directory, name, expected)
        supervision = read(directory / "supervision.json")
        record = read(directory / "result.json.gz")
        if (supervision["classification"] != "exited" or supervision["returncode"] != 0
                or report["candidates"][label]["result"] != record["result"]):
            raise ValueError(f"unfinished or inconsistent candidate: {label}")
    validate_arrays(report, binding)
    return report, binding


def validate_arrays(report, binding):
    axes = report["comparison"]["axes"]
    if axes["hours"] != [1165, 1166, 1167] or axes["boundary_hours"] != [1165, 1166, 1167, 1168]:
        raise ValueError("unexpected time axes")
    inputs = binding["calls"][0]["mathematical_inputs"]
    for family, bound in (("generators", "generators"), ("storage", "storage"),
                          ("nondispatchable", "nondispatchable")):
        a, b = axes[family], inputs[bound]
        if len(a) != len(b) or any(x["bus"] != y["bus"] for x, y in zip(a, b)):
            raise ValueError(f"unaligned {family} bus axes")
        if family != "generators" and any(x["device_id"] != y["device_id"] for x, y in zip(a, b)):
            raise ValueError(f"unaligned {family} device axes")
    if inputs["delta"] != 1 or any(s["capacity"] <= 0 for s in inputs["storage"]):
        raise ValueError("unexpected interval/capacity")
    fields = {"Pg": "generators", "b": "storage", "soc": "storage",
              "p_nd": "nondispatchable", "p_load_served": "loads"}
    for label in METHODS:
        candidate = report["candidates"][label]
        if not candidate["common"]["passed"] or not candidate["coordinate_checks"]["passed"]:
            raise ValueError(f"candidate lacks retained physical/coordinate checks: {label}")
        result = candidate["result"]
        for field, family in fields.items():
            array = np.asarray(result[field], float)
            if array.shape != (3, len(axes[family])) or not np.isfinite(array).all():
                raise ValueError(f"invalid array: {label}/{field}")
        for field, family in (("Qg", "generators"), ("b_q", "storage"),
                              ("q_nd", "nondispatchable")):
            value = result.get(field)
            if label in {"lossy_dc", "singlenode_dc"}:
                if value is not None:
                    raise ValueError("DC reactive channels must be absent")
            elif np.shape(value) != (3, len(axes[family])) or not np.isfinite(value).all():
                raise ValueError(f"invalid reactive array: {label}/{field}")


def trajectories(result, storage):
    """Engineering units; gross fleet flows cannot cancel across devices."""
    b = np.asarray(result["b"], float)
    soc = np.vstack(([s["initial_soc"] for s in storage], result["soc"]))
    capacity = np.asarray([s["capacity"] for s in storage])
    return dict(b=b, soc=soc, soc_percent=100 * soc / capacity,
                charge=np.maximum(-b, 0).sum(axis=1),
                discharge=np.maximum(b, 0).sum(axis=1))


def render(report, binding, output):
    output = Path(output).resolve()
    if any(output.is_relative_to(root.resolve()) for root in (SOURCE, HISTORY)):
        raise ValueError("output cannot modify immutable source execution trees")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=False)
    plt.rcParams.update({"font.size": 10, "axes.titlesize": 11,
                         "axes.spines.top": False, "axes.spines.right": False})
    inputs, axes = binding["calls"][0]["mathematical_inputs"], report["comparison"]["axes"]
    storage = inputs["storage"]
    data = {k: report["candidates"][k]["result"] for k in METHODS}
    traces = {k: trajectories(v, storage) for k, v in data.items()}
    files = []

    def decorate(ax, ylabel=None):
        ax.set_xlim(0, 3)
        ax.set_xticks(range(4))
        ax.grid(alpha=.18)
        if ylabel:
            ax.set_ylabel(ylabel)

    def power(ax, values, **style):
        values = np.asarray(values)
        ax.step(range(4), np.r_[values, values[-1]], where="post", **style)

    def save(fig, name, title, subtitle):
        fig.suptitle(title, fontsize=15, y=.99)
        # Reserve physical text spacing even for the shorter reactive figure.
        subtitle_y = min(.945, .99 - .4 / fig.get_figheight())
        fig.text(.5, subtitle_y, subtitle, ha="center", fontsize=10)
        fig.supxlabel("Hours since input hour 1165 (one-hour dispatch intervals)", y=.014)
        fig.tight_layout(rect=(0, .04, 1, .91))
        path = output / name
        fig.savefig(path, dpi=150, facecolor="white")
        files.append(name)
        plt.close(fig)

    fig, grid = plt.subplots(2, 3, figsize=(12.5, 8))
    titles = ["Conventional generation Pg", "Renewable dispatch p_nd", "Gross battery charging",
              "Gross battery discharging", "Fleet energy above initial SoC", "Net real network injection"]
    for ax, title in zip(grid.flat, titles):
        ax.set_title(title)
        decorate(ax, "MWh" if ax is grid[1, 1] else "MW")
    for label, (name, color, linestyle) in METHODS.items():
        x, tr = data[label], traces[label]
        values = [np.sum(x["Pg"], axis=1), np.sum(x["p_nd"], axis=1), tr["charge"],
                  tr["discharge"], tr["soc"].sum(axis=1)-tr["soc"][0].sum(),
                  np.asarray(x["p_net"])]
        if values[-1].ndim == 2:
            values[-1] = values[-1].sum(axis=1)
        for i, (ax, value) in enumerate(zip(grid.flat, values)):
            style = dict(color=color, linestyle=linestyle, linewidth=2, label=name)
            if i == 4:
                ax.plot(range(4), value, marker="o", markersize=3, **style)
            else:
                power(ax, value, **style)
    power(grid[0, 1], np.sum(data["socp"]["p_load_served"], axis=1),
          color="#333333", linestyle=(0, (1, 1)), label="Served demand", linewidth=2)
    grid[0, 0].legend(fontsize=9)
    grid[0, 1].legend(handles=[grid[0, 1].lines[-1]], fontsize=9)
    save(fig, "fleet.png", "Matched Tracy T=3: five retained dispatch trajectories",
         "Same inputs [1165,1168); SOCP injection is lifted-network accounting, not verified AC losses")

    gen_highlights = np.argsort(np.max(data["ac_baseline"]["Pg"], axis=0))[-4:]
    storage_highlights = [i for i, s in enumerate(storage) if s["bus"] in {1, 54, 80, 92}]
    fig, grid = plt.subplots(2, 3, figsize=(12.5, 8), sharex=True, sharey="col")
    for row, label in enumerate(("ac_baseline", "ac_combined_ac")):
        for col, field in enumerate(("Pg", "b", "soc_percent")):
            ax = grid[row, col]
            values = np.asarray(data[label][field]) if field == "Pg" else traces[label][field]
            ids = axes["generators"] if field == "Pg" else axes["storage"]
            highlights = gen_highlights if field == "Pg" else storage_highlights
            for j in range(values.shape[1]):
                style = dict(color=".72", linewidth=.8, alpha=.65)
                if j in highlights:
                    position = list(highlights).index(j)
                    style = dict(color=plt.get_cmap("tab10")(position), linewidth=1.8,
                                 label=f"bus {ids[j]['bus']}")
                if field == "soc_percent":
                    ax.plot(range(4), values[:, j], marker="o", markersize=2, **style)
                else:
                    power(ax, values[:, j], **style)
            decorate(ax, "SoC (% capacity)" if field == "soc_percent" else "MW")
            ax.set_title(METHODS[label][0]+": "+["all 54 Pg", "all 27 batteries b", "all 27 battery SoC"][col])
            if row == 0:
                ax.legend(fontsize=8, ncol=2)
            if field == "b":
                ax.axhline(0, color=".3", linewidth=.7)
    save(fig, "ac_devices.png", "The two AC candidates: same devices, different dispatch",
         "Shared vertical scales; gray traces retain all devices; b > 0 discharges; each battery returns to 50%")

    def device_panels(field, indices, identities, name, title, units):
        rows = (len(indices)+3)//4
        fig, grid = plt.subplots(rows, 4, figsize=(12, 2*rows+1.3), squeeze=False,
                                 sharex=True, sharey=True)
        for ax, j in zip(grid.flat, indices):
            for label, (method, color, linestyle) in METHODS.items():
                values = traces[label]["soc_percent"] if field == "soc_percent" else np.asarray(data[label][field])
                style = dict(color=color, linestyle=linestyle, linewidth=1.6, label=method)
                if field == "soc_percent":
                    ax.plot(range(4), values[:, j], marker="o", markersize=2, **style)
                else:
                    power(ax, values[:, j], **style)
            ax.set_title(f"bus {identities[j]['bus']} · row {j}", fontsize=10)
            decorate(ax)
            ax.tick_params(labelsize=8)
        for ax in list(grid.flat)[len(indices):]:
            ax.set_visible(False)
        fig.legend(*grid[0, 0].get_legend_handles_labels(), loc="upper center",
                   bbox_to_anchor=(.5, .935), ncol=5, fontsize=9)
        fig.supylabel(units)
        save(fig, name, title, "Identity-matched rows; common vertical scale; all five methods (overlapping near-zero traces retained)")

    device_panels("b", list(range(27)), axes["storage"], "battery_power.png",
                  "All 27 batteries: interval real power", "MW · positive discharges, negative charges")
    device_panels("soc_percent", list(range(27)), axes["storage"], "battery_soc.png",
                  "All 27 batteries: four-boundary energy trajectories", "SoC (% capacity)")
    flexible = [i for i, g in enumerate(inputs["generators"]) if g["p_max_mw"] > 0]
    device_panels("Pg", flexible, axes["generators"], "generator_power.png",
                  "All 19 generators with positive real-power capacity", "Pg (MW) · remaining 35 rows have fixed zero Pg")

    fig, grid = plt.subplots(1, 3, figsize=(12.5, 4.8))
    for ax, field, title in zip(grid, ("Qg", "b_q", "q_nd"),
                                ("Generator reactive dispatch", "Battery reactive dispatch", "Renewable reactive dispatch")):
        for label in ("ac_baseline", "ac_combined_ac", "socp"):
            method, color, linestyle = METHODS[label]
            power(ax, np.sum(data[label][field], axis=1), color=color,
                  linestyle=linestyle, linewidth=2, label=method)
        ax.set_title(title)
        ax.axhline(0, color=".4", linewidth=.8)
        decorate(ax, "MVAr · positive injection")
    grid[0].legend(fontsize=9)
    save(fig, "reactive.png", "Reactive power changes the interpretation of near-zero Pg",
         "Fleet sums; DC channels are not modeled; SOCP reactive dispatch is not verified AC-realizable")
    manifest = dict(source_sha256=PINS, historical_pins=binding["historical_pins"],
                    script_sha256=digest(Path(__file__)),
                    input_hours=axes["hours"], boundary_hours=axes["boundary_hours"],
                    methods=METHODS, generator_highlight_rows=gen_highlights.tolist(),
                    storage_highlight_rows=storage_highlights,
                    plots={name: digest(output / name) for name in files})
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    report, binding = load_retained()
    print(render(report, binding, args.output))


if __name__ == "__main__":
    main()
