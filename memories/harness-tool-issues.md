---
name: harness-tool-issues
description: Observed bugs/quirks in the homebrewed Claude Code harness and tool-calling system (for diagnosing the harness)
metadata:
  type: reference
---

The user runs this session through a homebrewed harness + tool-calling system that is still being debugged. Issues observed while editing `plans/milestone-7-hvdc.md`:

1. **Stale / out-of-order tool results.** Multiple `Bash` and `Read` calls returned content that did not reflect the file's actual current state. Example: a `sed -n '400,404p'` showed duplicated/interleaved lines that did not exist on disk; a separate `cat -A` error and a `sed` output came back swapped (error text attached to the wrong command). Authoritative state only became reliable via line-numbered `Grep` (`-n: true`).

2. **`Read` tool reflows from the top / truncates.** Repeated `Read` calls with an `offset` deep into the file kept returning content starting near the top and truncating before the requested lines, making lines ~380+ impossible to see via `Read`.

3. **False `Edit` success/failure reports.** Several `Edit` calls returned `"Edited ..."` when the change had NOT persisted; at least one returned `old_string not found` immediately after another view showed that exact string present. Could not trust an individual `Edit`'s echoed result; had to re-verify every edit with an independent line-numbered `Grep`.

4. **Bash / Edit malformed-input rejection on long or backtick-heavy strings.** A `Bash` call with a multi-part `grep ... && grep ...` was rejected as "Malformed tool input" (command string appeared truncated at a token). An `Edit` with a long, backtick- and quote-heavy `new_string` was likewise rejected as "Malformed tool input" mid-string. Workaround: prefer a single `Write` of the whole file over a large multiline `Edit`; keep `Bash` commands simple and singular. (`cat -A` also errored `illegal option -- A` on this macOS/BSD `cat` — a real platform limit, not a harness bug.)

5. **`Read` with `offset` returns the top-of-file window regardless.** A sharper recurrence of item 2 seen repeatedly this session: `Read` with `offset`/`limit` deep in the file returned the *same top-of-file window* every time, ignoring the offset. `sed -n 'A,Bp' <file>` and `Grep -n` are the reliable substitutes — `Read` was effectively unusable for the tail of a ~600-line file.

6. **Session-length-correlated proxy reconnect / ghost outputs (important).** As a session grows long, responses take longer to generate; the proxy server appears to decide the connection has died and fires multiple reconnect attempts, which surface as the *last* Claude Code output — producing duplicated/paired tool-result blocks (e.g. a `Bash`/`Edit` result arriving together, the same `Grep` echoed as an `Edit` result block) and, in at least one case, a hard **API error** mid-turn. Critically, an edit could still land on disk *during the errored turn* even though the turn surfaced as failed — so after any apparent error/ghost, re-verify on-disk state with `Grep -n`/`sed` before redoing the work (a blind retry could double-apply). The user's read: the proxy-harness setup gets buggier the longer the session runs.

**Mitigation for long sessions:** keep individual responses short (fewer, serial tool calls per turn); verify-then-proceed rather than batch; hand off to a fresh session past a certain length (see [[hvdc-plan-session-handoff]]).

**Working mitigation (all edits):** After any `Write`/`Edit`, verify with `Grep -n` as the single source of truth. Do not trust `Edit`'s own success/failure echo; do not trust `Read` with an offset — prefer `Grep -n` or targeted `sed -n 'A,Bp'` for specific line ranges. After any API error or ghost/duplicate output, re-verify on-disk state before retrying — the edit may have landed despite the error. Memory lives in repo-local `./memories/` (global `~/.claude/...` is outside the tool jail and fails to write). See [[hvdc-plan-mvp-scope]] for the plan work, and [[hvdc-plan-session-handoff]] for the current handoff state.
