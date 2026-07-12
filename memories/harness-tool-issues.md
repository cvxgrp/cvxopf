---
name: harness-tool-issues
description: Observed bugs/quirks in the homebrewed Claude Code harness and tool-calling system (for diagnosing the harness)
metadata:
  type: reference
---

The user runs this session through a homebrewed harness + tool-calling system that is still being debugged. Issues observed in prior turns while editing `plans/milestone-7-hvdc.md`:

1. **Stale / out-of-order tool results.** Multiple `Bash` and `Read` calls returned content that did not reflect the file's actual current state. Example: a `sed -n '400,404p'` showed duplicated/interleaved "Item 8/9/10" lines that did not exist on disk; a separate `cat -A` error and a `sed 400,$` output came back swapped (the error text attached to the wrong command). The authoritative state only became reliable via line-numbered `Grep` (`-n: true`).

2. **`Read` tool reflows from the top / truncates.** Repeated `Read` calls with an `offset` deep into the file (e.g. offset 380, 385, 348) kept returning content starting near the top of the file and truncating before reaching the requested lines, making it impossible to see lines ~380+ via `Read`. Had to fall back to `Grep -n` and `sed` to inspect the tail.

3. **False `Edit` success/failure reports.** Several `Edit` calls returned `"Edited ..."` (success) when the change had NOT persisted to disk, and at least one returned `old_string not found` immediately after another tool view had shown that exact string present. Net effect: could not trust an individual `Edit`'s echoed result; had to re-verify every edit with an independent line-numbered `Grep`.

4. **Bash heredoc / quoting flakiness.** A `Bash` call with a multi-part `grep ... && grep ...` command was rejected as "Malformed tool input" (the command string appeared truncated at a `plans` token). `cat -A` also errored (`illegal option -- A`) on this macOS/BSD `cat` — a real platform limitation, not a harness bug, but worth noting the harness surfaced the stderr out of order.

**Working mitigation:** After any `Write`/`Edit`, verify with `Grep` using line numbers (`-n: true`) as the single source of truth. Do not trust `Edit`'s own success/failure echo, and do not trust `Read` with a large offset — prefer `Grep -n` or targeted `sed` for inspecting specific line ranges. Per repo instructions, memory lives in repo-local `./memories/` (the global `~/.claude/...` path is outside the tool jail and fails to write). See [[hvdc-plan-mvp-scope]] for the plan work this happened during.