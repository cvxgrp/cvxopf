---
name: cvxopf-session-working-style
description: How Bennet wants multi-session cvxopf/HVDC work driven
metadata: 
  node_type: memory
  type: feedback
  originSessionId: da9d833f-e393-464c-8586-36830dd69ef5
---

On the cvxopf HVDC milestone, the user drives work in tight, checkpointed
increments and is explicit about scope ("this session we only work on T0").

**Why:** the work is delicate, spans many sessions with handoffs, and has
already been derailed once by a rabbit hole (patching pypower's broken
`toggle_dcline`, which kept exposing new bugs).

**How to apply:**
- Scope the session to one unit up front; don't pull later tasks forward.
- **Verify, don't trust** handoff docs or prior-session claims — re-run and
  confirm on disk (a stale-result scare happened before). The user explicitly
  values "good thing I re-verified."
- Get **approach sign-off before implementing** anything non-trivial; present
  options with tradeoffs and a recommendation. The user reversed course on
  monkeypatch→hand-built and evaluated pandapower via this pattern.
- Watch for rabbit holes; when an approach balloons, stop and surface it rather
  than push through. The user approved abandoning the monkeypatch for exactly
  this reason.
- Checkpoint at the close of each unit; the user often says "checkin at the
  close of X." Commits only when asked.
- Prefer standard tools (Read/Write/Edit) over shell text hacks; use throwaway
  `/tmp` spikes to de-risk before touching committed files, then clean them up.

See [[milestone-7-hvdc-status]] for current progress.
