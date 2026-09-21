"""Demonstrate the controlled single-node load-service phase transition.

The theorem applies here because dispatch is continuous, generation cost is
convex and nondecreasing, full service is feasible, minimum output is zero,
and there is no network, storage, or intertemporal opportunity value. The
generator-only maximum marginal cost is therefore a conservative sufficient
VOLL reference for this example, not a universal certificate for arbitrary
networked or multistep problems.

Below the relevant marginal cost, partial economic shedding occurs. Above the
maximum supported generator slope, full service is cheaper and increasing the
coefficient further does not change dispatch.

Usage:
    uv run examples/singlenode_load_shedding_phase_transition.py
"""

from cvxopf import (
    DispatchableGenerator,
    Load,
    build_opf,
    extract_results,
    max_generation_marginal_cost,
)
from cvxopf.testcases import make_singlenode_case


def main():
    generator = DispatchableGenerator(
        bus=1,
        p_max_mw=100.0,
        cost_coeffs=(0.0, 2.0, 0.05),
    )
    case = make_singlenode_case(0.0, [generator])
    bound = max_generation_marginal_cost([generator])

    print("single-node load-service phase transition")
    print(f"maximum generator marginal cost: {bound:.1f} objective units/MWh")
    print()
    print(f"{'VOLL':>8} {'generation':>12} {'shed':>10} {'ENS':>10}")
    print("-" * 44)
    for voll in (9.0, bound + 0.1, 2.0 * bound, 5.0 * bound):
        load = Load(
            bus=1,
            p_load_mw=80.0,
            device_id="demand",
            shedding_cost_per_mwh=voll,
        )
        build = build_opf(
            case,
            formulation="singlenode_dc",
            generators=[generator],
            loads=[load],
        )
        build.solve()
        result = extract_results(build)
        print(
            f"{voll:8.1f} {result['Pg'][0]:12.1f} "
            f"{result['p_load_shed'][0]:10.1f} "
            f"{result['energy_not_served']:10.1f}"
        )


if __name__ == "__main__":
    main()
