"""Single-step OPF on WECC 2018: 243 buses and two HVDC links.

Run from the repository root:
    uv run python examples/wecc240_single_step.py
    uv run --with matplotlib python examples/wecc240_single_step.py --plot /tmp/wecc240.png
    uv run python examples/wecc240_single_step.py --formulation socp
    uv run python examples/wecc240_single_step.py --formulation lossy_dc

AC is the default; plotting needs only Matplotlib. HVDC bounds are converted
from positive sending power to signed bus injections, preserving the upstream
directions and 3.5% losses. AC finds a local optimum; SOCP reports relaxation
feasibility and AC recovery separately.
"""

import argparse
import time

import numpy as np

from cvxopf import build_opf, extract_results, hvdc_from_dcline
from cvxopf.socp_diagnostics import audit_socp_relaxation, recover_socp_voltage
from cvxopf.testcases import wecc240_2018


def hvdc_links(case):
    """Convert this case's sending-power bounds: [Pmin, Pmax] -> [-Pmax, -Pmin]."""
    dc = case["dcline"].copy()
    dc[:, 9:11] = -dc[:, [10, 9]]
    return hvdc_from_dcline(dc, case["dclinecost"])


def plot_ac(case, result, path):
    """Save a geographic network plot with voltage and branch loading."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    locations = {int(row[0]): (row[2], row[1]) for row in case["geodata"]}
    xy = np.array([locations[int(bus)] for bus in case["bus"][:, 0]])
    branch = case["branch"]
    segments = np.array([[locations[int(f)], locations[int(t)]] for f, t in branch[:, :2]])
    online = branch[:, 10] > 0
    rating = branch[:, 5]
    rated = online & (rating > 0) & np.isfinite(rating)
    apparent_power = np.maximum(result["branch_s_from"], result["branch_s_to"])
    loading = 100 * apparent_power[rated] / rating[rated]
    fig, ax = plt.subplots(figsize=(9, 9), layout="constrained")
    ax.add_collection(LineCollection(segments[online & ~rated], colors="0.75", linewidths=0.8,
                                     label="AC without thermal rating"))
    lines = LineCollection(segments[rated], array=loading, cmap="YlOrRd", linewidths=1.2)
    lines.set_clim(0, 100)
    ax.add_collection(lines)
    dc_segments = [[locations[int(row[0])], locations[int(row[1])]]
                   for row in case["dcline"] if row[2] > 0]
    ax.add_collection(LineCollection(dc_segments, colors="purple", linestyles="--",
                                     linewidths=1.6, label="HVDC"))
    buses = ax.scatter(xy[:, 0], xy[:, 1], c=result["Vm"], cmap="coolwarm", vmin=0.9,
                      vmax=1.1, s=18, edgecolors="0.3", linewidths=0.3, zorder=3)
    fig.colorbar(buses, ax=ax, shrink=0.65, label="Bus voltage (p.u.)")
    fig.colorbar(lines, ax=ax, shrink=0.65, label="AC loading (% of rating)")
    ax.set(title=f"WECC 2018 · AC OPF\nCost: ${result['objective']:,.0f} · local optimum",
           xlabel="Longitude (°)", ylabel="Latitude (°)")
    ax.set_aspect(1 / np.cos(np.deg2rad(xy[:, 1].mean())))
    ax.margins(0.05)
    ax.grid(alpha=0.15)
    ax.legend(loc="upper left", fontsize="small")
    fig.savefig(path, dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formulation", choices=("ac", "socp", "lossy_dc"), default="ac")
    parser.add_argument("--plot", metavar="PATH", help="Save a static AC network plot (e.g. /tmp/wecc240.png)")
    args = parser.parse_args()
    if args.plot and args.formulation != "ac":
        parser.error("--plot requires --formulation ac")
    case = wecc240_2018()
    build = build_opf(case, formulation=args.formulation, hvdc=hvdc_links(case))
    options = dict(tol=1e-8, constr_viol_tol=1e-8, bound_relax_factor=0.0) if args.formulation == "ac" else {}
    started = time.perf_counter()
    build.solve(**options)
    elapsed = time.perf_counter() - started
    result = extract_results(build)
    print(f"Formulation: {args.formulation}")
    print(f"Status: {result['status']}")
    print(f"Canonicalization + solve: {elapsed:.3f} s")
    if result["Pg"] is None:
        raise SystemExit("No dispatch returned")
    if args.formulation == "ac" and result["status"] != "optimal":
        raise SystemExit("AC solve did not converge")
    print(f"Objective: {result['objective']:.6f}")
    print(f"Generation: {result['Pg'].sum():.3f} MW")
    print(f"HVDC sent: {-result['p_hvdc_in']} MW")
    print(f"HVDC received: {result['p_hvdc_out']} MW")
    if args.formulation == "socp":
        audit = audit_socp_relaxation(build, result)
        recovery = recover_socp_voltage(build, result)
        print(f"Relaxation feasible: {audit['feasible']}")
        print(f"Exact voltage-product recovery: {recovery['exact_product_recovery']}")
        print(f"Recovered AC feasible: {recovery['ac_feasible']}")
    elif args.formulation == "ac":
        print(f"Voltage range: {result['Vm'].min():.6f} to {result['Vm'].max():.6f} p.u.")
        print(f"AC branch losses: {(result['branch_p_from'] + result['branch_p_to']).sum():.3f} MW")
        print(f"HVDC losses: {result['hvdc_loss'].sum():.3f} MW")
        print("IPOPT finds a local optimum; global optimality is not established.")
        if args.plot:
            plot_ac(case, result, args.plot)
            print(f"Plot: {args.plot}")


if __name__ == "__main__":
    main()
