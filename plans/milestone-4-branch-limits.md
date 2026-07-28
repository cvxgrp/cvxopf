# Milestone 4 — Branch flow limits (AC)

**Status:** placeholder. This is not yet a real implementation plan — it holds
the guidance migrated out of CLAUDE.md until a proper plan is written. When M4
is picked up, replace this file with a full build plan (steps, gates, tests) in
the style of `plans/milestone-7-hvdc.md`.

## Migrated guidance (from CLAUDE.md)

When implementing, add apparent power flow expressions derived from the
`P`, `Q` matrices and enforce per-branch `rateA` constraints. The stub
and `NotImplementedError` in `ac_problem.py` must be replaced. Add tests
that verify the constraint is binding when load is pushed high enough.

## Open dependency (from CLAUDE.md "What not to do")

Do not implement M4 branch flow limits using `P_vec`/`Q_vec` until Milestone 9
is complete. The guidance above references the dense `P`, `Q` matrices; with
`sparse_pq=True` (the M9 default) those matrices do not exist, so the flow
expressions must be rederived over the sparse `P_vec`/`Q_vec` representation
(or the scatter matrix `Rp`) before this milestone can land.
