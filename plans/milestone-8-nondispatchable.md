# Milestone 8 — Nondispatchable generators

`NondispatchableUnit` in `src/cvxopf/nondispatchable.py`. Passed as
`nondispatchable=` to `build_opf` and `build_opf_multistep`. Available
power time series supplied via `df_nd` (multistep only).

AC formulation uses an apparent power circle constraint
`p_nd_t^2 + q_nd_t^2 <= P_max^2` intersected with `0 <= p_nd_t <= R_t`.
DC formulation uses only the real power bound `0 <= p_nd_t <= R_t`;
apparent power rating is stored but not enforced as a constraint.
No cost term. No curtailment penalty. No SoC dynamics.

Variables `p_nd` and `q_nd` are in engineering units (MW, MVAr) internally,
matching the storage convention. They enter the nodal balance divided by
`baseMVA` and are not rescaled in `extract_results`.

Results include `p_nd`, `q_nd` (AC only), and `curtailment = R_t - p_nd`.
