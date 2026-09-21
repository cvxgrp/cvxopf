"""Hierarchical long-horizon lossy-DC planning and short-horizon AC control.

This compact three-interval example plans battery energy with the convex
``lossy_dc`` formulation, then executes one action at a time through
two-interval AC windows. Only identity-aligned battery state-of-charge
signposts pass between layers; generator dispatch and other device setpoints
are re-optimized by AC. The last AC window is truncated to one interval.

``shifted_with_recovery`` first shifts the preceding accepted AC prediction.
If that target-conditioned solve fails, the controller retains an auditable,
deterministic recovery sequence. Only an accepted target-conditioned AC solve
may supply an executed action.

Usage:
    uv run examples/case9_hierarchical_dc_ac.py
"""

import pandas as pd

from cvxopf import (
    HierarchicalInputs,
    HierarchicalPolicy,
    Load,
    StorageUnitIdeal,
    gen_from_matpower,
    solve_hierarchical_opf,
)
from cvxopf.testcases import case9


def main():
    case = case9()
    loads = tuple(
        Load(
            bus=int(row[0]),
            p_load_mw=float(row[2]),
            q_load_mvar=float(row[3]),
            device_id=f"load-{int(row[0])}",
        )
        for row in case["bus"]
    )
    load_ids = [unit.device_id for unit in loads]
    p_base = [unit.p_load_mw for unit in loads]
    q_base = [unit.q_load_mvar for unit in loads]
    inputs = HierarchicalInputs(
        case=case,
        horizon_steps=3,
        delta=1.0,
        generators=tuple(gen_from_matpower(case["gen"], case["gencost"])),
        loads=loads,
        storage=(
            StorageUnitIdeal(
                bus=7,
                apparent_power_rating=125.0,
                capacity=1_000.0,
                initial_soc=500.0,
                terminal_soc=500.0,
                terminal_constraint="equality",
                device_id="battery-7",
            ),
        ),
        df_load_p=pd.DataFrame(
            [p_base, [1.05 * value for value in p_base], p_base],
            columns=load_ids,
        ),
        df_load_q=pd.DataFrame(
            [q_base, [1.05 * value for value in q_base], q_base],
            columns=load_ids,
        ),
    )
    policy = HierarchicalPolicy(
        ac_window_steps=2,
        outer_policy="replan_every_step",
        inner_terminal_policy="hard_equality",
        initialization_policy="shifted_with_recovery",
    )

    result = solve_hierarchical_opf(inputs, policy)

    print("case9 hierarchical lossy-DC -> AC control")
    print(f"completed: {result.completed}")
    print(f"executed intervals: {result.completed_intervals}/3")
    print(f"outer plans: {len(result.outer_plans)}")
    print(f"AC attempt slots: {len(result.ac_attempts)}")
    print()
    print(f"{'step':>4} {'AC window':>10} {'battery MW':>12} {'SoC MWh':>10}")
    print("-" * 43)
    attempts_by_id = {
        attempt.attempt_id: attempt for attempt in result.ac_attempts
    }
    for step, record in enumerate(result.executed_intervals):
        attempt = attempts_by_id[record.controlling_attempt_id]
        print(
            f"{step:4d} {attempt.local_interval_stop:10d} "
            f"{result.executed_b_mw[step, 0]:12.3f} "
            f"{result.realized_soc_mwh[step + 1, 0]:10.3f}"
        )


if __name__ == "__main__":
    main()
