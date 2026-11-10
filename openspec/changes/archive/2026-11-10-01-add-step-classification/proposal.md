# Proposal: add step classification

## Why

Several planned changes need to know what an agent step does: the lite-action guard must
recognise risky actions, and the turn log must record the step mix that the research protocol's
phase 0 measures. Failure detection needs the list of read tools. Today ThetaRelay treats every
tool call the same.

## What Changes

- Classify every tool call in a request or a reply into one of four groups, from least to most
  severe:
  - `read`: cannot change state (file reads, searches, `git diff`, web fetches).
  - `run`: runs project code in the working tree (tests, builds, `uv run`).
  - `write`: changes files in the working tree or local git state.
  - `irreversible`: anything else, including anything that reaches beyond the working tree
    (`git push`, publishing, deploys, cloud and container CLIs, `ssh`, `curl`) and unknown tools.
- Each of `read`, `run` and `write` is an allowlist. A tool call that matches none of them is
  `irreversible`, so a missed case is always handled with the most care.
- Heredoc bodies (`cat > f <<'EOF' … EOF`) are data, not commands. Agents write most new files
  this way; in captured Pi sessions, splitting on newlines read Python lines as commands.
- An evaluation on real tool calls from captured sessions, run in CI.
- No routing behaviour changes in this change. Other changes use the classification.

## Capabilities

### New Capabilities

- `step-classification`: the four-group classification of tool calls, for both API formats and
  the tool names of Pi, OpenCode and Claude Code.
- `step-classification-eval`: a labelled set of real tool calls and a check that no call is
  classified less severe than its label.

### Modified Capabilities

None.

## Impact

- `src/thetarelay/router.py`: a classification function and its allowlists.
- Tests: one or more per spec scenario.
- `scripts/step_eval/`: the labelled dataset and the evaluation; `justfile` runs it in `check`.
- First dataset: 259 unique tool calls (118 shell commands) from the `app_eval` and
  `routing_eval` captures, all from Pi. OpenCode and Claude Code calls must be captured before
  the dataset covers every agent.
- Until the allowlists grow from turn-log data, some local commands are classified
  `irreversible`. Consumers that escalate `irreversible` steps escalate more often at first.

## Decisions taken in review

- **Four groups, not three.** Test and build runs are among the most common steps and the main
  source of failure signals. Keeping them apart from edits lets the turn log measure them and
  lets the guard allow them without allowing edits.
- **Fail closed to `irreversible`.** A blocklist of irreversible commands would let a missed one
  (`gh pr merge`, `terraform apply`, an MCP tool that sends email) count as an ordinary write.
- **`rm` is `write`.** Removing an untracked file cannot be undone, but the command alone does
  not show whether the file is tracked. `write` means "stays in the working tree", not "can
  always be undone".
