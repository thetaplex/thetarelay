# Known limitations

This list applies to version 0.1.0. Last examined: 2026-11-06.

| Limitation | Effect |
|---|---|
| Cost savings are tested only with Claude Opus 5.5 and Claude Haiku 5.5. | With other models, routing can cost more than no routing. See [Tested models](tested-models.md). |
| The judge accuracy is measured only on 59 hand-written steps. | On real agent sessions, it can send hard steps to the lite model. Examine the turn log before you rely on it. |
| ThetaRelay does not check the quality of lite tool calls. | If the lite model writes a wrong tool call or wrong code, the agent receives it. A failed tool result then moves the conversation to the frontier tier. |
| The failure detection uses text patterns. | Failures without words such as `FAILED`, `error:` or `Traceback` are not detected. |
| The judge adds time to each request. | Approximately 0.3 to 3 s with `tev1` on an Apple M3 Pro, more on the first request after the model loads. |
| The two tiers can see different model names in the history. | Some agents show the model name of the response. |
| The lite server and the frontier server must have the Anthropic Messages API and the OpenAI Chat Completions API. | Ollama and OpenRouter have both. Other servers can fail on one of the two formats. |
| The usage report counts only costs in the turn log. | Extra lite requests the proxy sends on its own, such as shadow requests when that feature is on, add cost that the report may not show. |
