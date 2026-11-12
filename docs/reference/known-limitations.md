# Known limitations

This list applies to version 0.2.0. Last examined: 2026-11-12.

| Limitation | Effect |
|---|---|
| Cost savings are tested only with Claude Opus 5.5 and Claude Haiku 5.5. | With other models, routing can cost more than no routing. See [Tested models](tested-models.md). |
| The judge accuracy is measured only on 59 hand-written steps. | On real agent sessions, it can send hard steps to the lite model. Examine the turn log before you rely on it. |
| ThetaRelay checks only the kind of lite tool calls, not their content. | With `THETARELAY_LITE_GUARD` on, malformed and irreversible calls (and, at `writes`, file edits) go to the frontier tier. A well-formed call with wrong code still reaches the agent; a failed tool result then moves the conversation to the frontier tier. The guard is off by default. |
| Failure detection relies on what the agent reports. | The error flag, an exit status line and test runner summaries come first; without them, `run` and `write` results fall back to words such as `FAILED`, `error:` or `Traceback`. A failure the agent reports in none of these ways is not detected. A shell command that prints a file containing an exit status line, such as `exit code 3`, can be counted as failed. |
| The judge adds time to each request. | Approximately 0.3 to 3 s with `tev1` on an Apple M3 Pro, more on the first request after the model loads. |
| The two tiers can see different model names in the history. | Some agents show the model name of the response. |
| The lite server and the frontier server must have the Anthropic Messages API and the OpenAI Chat Completions API. | Ollama and OpenRouter have both. Other servers can fail on one of the two formats. |
