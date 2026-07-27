"""
Compare storage terminal policies on the same three-step dispatch problem
under single-node DC and full AC formulations.

The terminal state is the post-dispatch state after the final interval.
Equality fixes that state, shortfall enforces a reserve floor, and a soft
shortfall cost permits a miss at an explicit objective penalty.

Usage:
    uv run examples/case9_storage_terminal.py
"""

import pandas as pd

from cvxopf.problem import StorageUnitIdeal, build_opf_multistep
from cvxopf.results import extract_results
from cvxopf.testcases import case9


def main():
    case = case9()
    T = 3
    df_P = pd.DataFrame([case["bus"][:, 2]] * T)
    df_Q = pd.DataFrame([case["bus"][:, 3]] * T)

    policies = {
        "none": {},
        "equality": {
            "terminal_soc": 50.0,
            "terminal_constraint": "equality",
        },
        "shortfall": {
            "terminal_soc": 50.0,
            "terminal_constraint": "shortfall",
        },
        "soft shortfall": {
            "terminal_soc": 50.0,
            "terminal_cost": "shortfall_linear",
            "terminal_weight": 1.0,
        },
    }

    for formulation in ("singlenode_dc", "ac"):
        print()
        print(formulation)
        print(
            f"{'policy':<18} {'final SoC':>12} "
            f"{'deviation':>12} {'penalty':>12}"
        )
        print("-" * 57)
        reactive_load = df_Q if formulation == "ac" else None
        for name, policy in policies.items():
            unit = StorageUnitIdeal(
                bus=5,
                apparent_power_rating=50.0,
                capacity=100.0,
                initial_soc=50.0,
                aging_weight=1e-2,
                **policy,
            )
            build = build_opf_multistep(
                case,
                df_P,
                reactive_load,
                T=T,
                formulation=formulation,
                storage=[unit],
                delta=1.0,
            )
            build.solve()
            results = extract_results(build)

            deviation = results.get("storage_terminal_deviation")
            deviation_text = (
                "—" if deviation is None else f"{deviation[0]:.3f}"
            )
            penalty = results.get("storage_terminal_cost")
            penalty_text = "—" if penalty is None else f"{penalty:.3f}"
            print(
                f"{name:<18} {results['soc'][-1, 0]:>12.3f} "
                f"{deviation_text:>12} {penalty_text:>12}"
            )


if __name__ == "__main__":
    main()
