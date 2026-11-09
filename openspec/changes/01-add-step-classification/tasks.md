# Tasks

## 1. Tool-call groups

- [x] 1.1 Write failing tests for the "Four step groups", "Agent-internal and web tools" and
  "Unknown and unparsable steps fail closed" scenarios in `tests/test_steps.py`; verify they fail
  with `just test tests/test_steps.py`
- [x] 1.2 Add `src/thetarelay/steps.py` with the group type, the tool-name tables and
  `classify(name, arguments)` for non-shell tools; verify the 1.1 tests pass

## 2. Shell commands

- [x] 2.1 Write failing tests for the "Most severe part", "Heredoc bodies are data", "Read-only
  shell programs", "Output redirection and substitution", "Project code runs", "Local writes"
  and "Everything else is irreversible" scenarios; verify they fail
- [x] 2.2 Implement shell classification (heredoc stripping, redirect handling, substitution,
  splitting, per-program rules); verify the 2.1 tests pass

## 3. Replies

- [x] 3.1 Write failing tests for "Both API formats" and "Group of a reply": Anthropic and
  OpenAI, JSON and streamed, several calls, text only, unparsable call; verify they fail
- [x] 3.2 Implement `reply_calls(raw)` and `reply_group(raw)`; verify the 3.1 tests pass

## 4. Evaluation

- [x] 4.1 Add `scripts/step_eval/extract.py`: collect unique tool calls from captured
  `bodies.jsonl` files into `scripts/step_eval/calls.jsonl`, skipping known ones and leaving new
  labels empty; verify by running it on the `app_eval` and `routing_eval` captures
- [x] 4.2 Propose a label for every extracted call and hand the file to the maintainer for
  review; verify every line has a reviewed label
- [x] 4.3 Add `tests/test_step_eval.py`: dataset check (fields, labels, secret patterns) and the
  under-classification gate; verify `just check` runs it and passes
- [x] 4.4 Add `scripts/step_eval/report.py`: confusion matrix and over-cautious calls by program;
  verify its output on the dataset, and widen the allowlists for any read or run program that
  appears more than once as `irreversible`

## 5. Wrap-up

- [x] 5.1 Add a line about `scripts/step_eval` to `CONTRIBUTING.md` (no `CHANGELOG.md` entry: no
  user-facing behaviour yet); verify `just check` passes
- [x] 5.2 Run `openspec validate 01-add-step-classification --strict`; verify it passes

## Workflow follow-up

- Archive the change with `openspec archive 01-add-step-classification` after merge.
- The dataset covers only Pi until OpenCode and Claude Code sessions are captured; record this
  as an open item in the archive commit.
