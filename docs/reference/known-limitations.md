# Known limitations

This list applies to version 0.2.0. Last examined: 2026-11-02.

| Limitation | Effect |
|---|---|
| The judge accuracy is measured only on 59 hand-written steps. | On real agent sessions, it can send hard steps to the local model. Examine the turn log before you rely on it. |
| ThetaRelay does not check the quality of lite responses. | If the lite model writes a wrong tool call or wrong code, the agent receives it. A failed tool result then moves the conversation to the frontier tier. |
| The failure detection uses text patterns. | Failures without words such as `FAILED`, `error:` or `Traceback` are not detected. |
| The judge adds time to each request. | Approximately 0.3 to 3 s with `tev1` on an Apple M3 Pro, more on the first request after the model loads. |
| Usage and cost are not logged for streamed requests. | Use the OpenRouter dashboard for streamed sessions. |
| The two tiers can see different model names in the history. | Some agents show the model name of the response. |
| The lite server and the frontier server must have the Anthropic Messages API and the OpenAI Chat Completions API. | Ollama and OpenRouter have both. Other servers can fail on one of the two formats. |
