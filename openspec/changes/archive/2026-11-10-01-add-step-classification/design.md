# Design: add step classification

## Context

Routing today uses only the judge, a failure regex and request size (`src/thetarelay/router.py`).
Tool calls reach the proxy in two places: the request history (calls the agent made) and the
lite reply (the call the lite model proposes). In captured Pi sessions the tools are `bash`,
`read`, `edit` and `write`; most `bash` calls are reads (`sed -n`, `rg`, `ls`) or test runs.

## Goals / Non-Goals

- Goal: one deterministic function used by the lite-action guard, the turn log and the research
  protocol.
- Non-goal: a complete shell parser. The classifier recognises short allowlists and fails closed
  on the rest.

## Decisions

- **Three allowlists, one default.** `read`, `run` and `write` are each an allowlist; everything
  else is `irreversible`. A missed case is then over-cautious, never under-cautious.
  Alternative considered: allowlist for `read`, blocklist for `irreversible`, everything else
  `write`. Rejected in review: a missed irreversible command (`gh pr merge`, `terraform apply`)
  or an unknown MCP tool would count as an ordinary write and pass a guard set to `irreversible`.
- **Split on shell operators, then `shlex`.** Splitting on `&&`, `||`, `;`, `|`, `&` and newline
  before `shlex.split` handles pipelines without a full parser. Each part is classified on its
  own, and the most severe group wins.
- **Redirection checked before splitting.** `2>/dev/null` and `2>&1` are removed first, matched
  to the end of the target, so `> /dev/null.bak` is still a write. Any other `>` makes the part
  at least `write`.
- **Substitution is classified, not rejected.** The substituted command is classified as its own
  command, so `echo $(git rev-parse HEAD)` stays `read`.
- **`run` means a known runner, not any program.** `python script.py` and `uv run …` run code the
  agent wrote in the working tree; an unknown binary could be anything and is `irreversible`.

## Risks / Trade-offs

- [Allowlists start short, so some local commands are `irreversible`] → The turn log records
  each step's group and program (change 03); widen the lists from that data, not by guessing.
- [Project code in `run` can do anything, including network calls] → Accepted: it is code in the
  working tree, the same trust as `write`. The guard (change 05) decides what to do with `run`.
- [Tool names differ between agents and versions] → Match case-insensitively; unknown tool names
  appear in the turn log.
