# How routing works

ThetaRelay sends each request of a coding agent to one of two tiers:

| Tier | Default model | Served by |
|---|---|---|
| Lite (easy) | `gemma4:e4b` | Ollama on your computer |
| Frontier (hard) | `openai/gpt-6.1-terra` | OpenRouter |

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
   request goes to the lite tier. If not, it goes to the frontier tier.
4. **Errors.** If the judge does not answer, the request goes to the frontier tier. If the lite
   server does not answer or returns a 5xx status, ThetaRelay sends the same request to the
   frontier tier.

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
thinking. The system prompt, the messages and the tools do not change. So the frontier
provider sees the same prefix on each request, and its prompt cache continues to work. Requests
that go to the lite tier add messages after that prefix. The next frontier request still
matches the cached prefix, if the cache has not expired.

Anthropic models cache only when the request marks where the cache ends (`cache_control`).
Agents add this marker only when they know that the model is an Anthropic model. Through
ThetaRelay they do not know, because ThetaRelay picks the model. So for OpenAI-format requests to
an `anthropic/` frontier model, ThetaRelay adds OpenRouter's top-level marker
(`"cache_control": {"type": "ephemeral"}`) if the request has no marker. OpenRouter then puts
the cache boundary at the end of the prompt. The system prompt and the messages do not change.

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
