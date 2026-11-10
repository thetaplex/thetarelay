# How routing works

ThetaRelay sends each request of a coding agent to one of two tiers:

| Tier | Default model | Served by |
|---|---|---|
| Lite (easy) | `anthropic/claude-haiku-5.5` | Ollama on your computer, or a hosted provider such as OpenRouter |
| Frontier (hard) | `anthropic/claude-opus-5.5` | OpenRouter |

One model writes the full response. ThetaRelay never mixes the output of two models in one
response, and it never shows one model's output to another model for approval. Each response
is a normal response of the model that wrote it.

## The decision

For each request, ThetaRelay applies these rules in sequence. The first rule that applies sets
the tier.

1. **Size.** If the request is larger than `THETARELAY_LITE_MAX_REQUEST_CHARS`, it goes to
   the frontier tier. The lite model cannot read it.
2. **Recent failure.** If a tool result in the last `THETARELAY_STAY_REQUESTS` agent requests
   shows a failure (for example `FAILED`, `Traceback`, `error:` or a non-zero exit code), the
   request goes to the frontier tier. A failure usually means debugging, which is hard.
3. **Judge.** ThetaRelay asks the judge model one question: can a small, fast model do the next
   step? If the judge gives a probability of "easy" above `THETARELAY_EASY_THRESHOLD`, the
   request goes to the lite tier.
4. **Triage.** Otherwise the request goes to the frontier model with a short note after the
   last message: if a smaller model can do the next step correctly, reply only `<EASY/>`. If
   the frontier model does the step, its reply goes to the agent unchanged, with no extra call.
   If it answers `<EASY/>`, ThetaRelay drops that answer and sends the original request, without
   the note, to the lite tier. With `THETARELAY_ROUTE_MODE=judge`, this rule is off and the
   request goes to the frontier tier.
5. **Errors.** If the judge does not answer, the request goes to the frontier tier. If the lite
   server does not answer or returns a 5xx status, ThetaRelay sends the same request to the
   frontier tier.
6. **Stopping.** If `THETARELAY_ESCALATE_ON_STOP` is `true` (the default) and the lite response
   contains no tool call (so it would end the agent's turn), ThetaRelay discards it and sends the
   request to the frontier tier. This holds after a passing test run too: the frontier model
   decides when the work is done.
7. **Lite guard.** If `THETARELAY_LITE_GUARD` is `irreversible` or `writes` (default `off`),
   ThetaRelay checks each lite reply before the agent sees it. It discards the reply and sends
   the request to the frontier tier if a tool call is malformed (unreadable, not an offered
   tool, arguments that are not a JSON object, or a missing required key), if a step is
   irreversible (for example `git push`), or, at level `writes`, if a step edits files. Test
   runs stay with the lite tier. The check runs before the stop check, and not in
   `THETARELAY_ROUTE_MODE=lite`.

A wrong decision has two different costs. An easy step sent to the frontier tier costs money
only. A hard step sent to the lite tier can cost quality. For this reason every uncertain case
goes to the frontier tier.

## What the judge reads

The judge (`tev1` on Ollama by default) reads at most 2048 tokens. It cannot read the
conversation. ThetaRelay gives it a summary that it makes from the request, without a model:

- `goal`: the latest message from the user, without agent reminders, at most 300 characters.
- `recent_steps`: one line for each of the last 12 steps (user message, assistant text, tool
  call, tool result). A failed tool result shows the line with the failure.
- `last_message`: the end of the last message, at most 800 characters.
- `failed_results`: the number of failed tool results in the conversation.

Agents send the full conversation with each request. So ThetaRelay keeps no state between
requests, and a restart does not change any decision.

## Prompt cache

ThetaRelay changes only the `model` field of a request. For the lite tier, it also turns off
thinking. A triage request also has the note after the last message; if the client set no cache
marker, the last real message gets one, so the cached prefix ends before the note. The system prompt, the messages and the tools do not change. So the frontier
provider sees the same prefix on each request, and its prompt cache continues to work. Requests
that go to the lite tier add messages after that prefix. The next frontier request still
matches the cached prefix, if the cache has not expired.

Anthropic models cache only when the request marks where the cache ends (`cache_control`).
Agents add this marker only when they know that the model is an Anthropic model. Through
ThetaRelay they do not know, because ThetaRelay picks the model. So for OpenAI-format requests to
an `anthropic/` frontier model, ThetaRelay adds OpenRouter's top-level marker
(`"cache_control": {"type": "ephemeral"}`) if the request has no marker. OpenRouter then puts
the cache boundary at the end of the prompt. The system prompt and the messages do not change.

## Shadow routing

Set `THETARELAY_SHADOW_SHARE` above 0 and `THETARELAY_TURN_LOG` to a file, to learn what the lite tier
would have done. For that share of the requests that go to the frontier tier (and are not triaged, escalated from the lite tier, or larger than
`THETARELAY_LITE_MAX_REQUEST_CHARS`), ThetaRelay
also sends the same request to the lite server, with the lite body and key. The agent never sees the lite
reply. ThetaRelay logs the tool calls of both replies and whether they agree (`shadow` in the
[turn log](../reference/http-api.md#turn-log)). The judge is also asked in every route mode, and its answer is
logged, so you can compare it with the lite reply. That adds the judge's latency (up to
`THETARELAY_JUDGE_TIMEOUT`) to every request. Shadows come mainly in `frontier` and `judge` modes: in `hybrid`
mode almost every frontier request is triaged.

Each shadow is one more request to the lite server. A local server gets more load. A hosted lite tier costs money
for every shadow. The default is 0.0 (off).

## Measured judge accuracy

On 59 coding-agent steps written and labelled by hand (30 easy, 29 hard), at a threshold of
0.7 (2026-11-02):

| Judge | Hard steps sent to lite | Easy steps sent to lite |
|---|---|---|
| `tev1` (4B) | 0 of 29 | 29 of 30 |
| `tev1:0.8b` | 0 of 29 | 15 of 30 |
| `typesafe/jev-1.14` (OpenRouter) | 0 of 29 | 29 of 30 |

These steps are clearly easy or clearly hard. Real agent steps are less clear. This result
does not show that the lite model does the easy steps correctly. Use the turn log to examine
real sessions.
