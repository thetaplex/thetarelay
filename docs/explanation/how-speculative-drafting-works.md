# How speculative drafting works

## The problem

A frontier model writes output tokens slowly. A 220-token code edit at 65 tokens per second takes
more than 3 seconds. Output tokens also cost more than input tokens. Many coding-agent turns are
simple. A small local model can write the same response in less time.

thetarelay lets a local model write a draft first. The frontier model then checks the draft. If the
draft is correct, the frontier model writes only a short acceptance token. thetarelay then returns
the draft as the response.

## The request path

1. The coding agent sends a request to thetarelay.
2. Headroom compresses the context, if Headroom is active.
3. thetarelay decides if the request is eligible for a draft. It does not draft for bypass headers,
   small models, short prompts, or requests that already contain a draft.
4. thetarelay converts the request to an OpenAI Chat Completions request for the local model. It
   adds the drafting instructions as a separate system message and sends a maximum of 16 tools.
   It sends a window of the conversation, not the full conversation (see below).
5. The local model returns text, tool calls, or both.
6. thetarelay adds the draft to the request and sends it to the frontier model.
7. thetarelay examines the response and returns the draft or the frontier response.

If the local model fails or is too slow, thetarelay sends the original request. The agent gets a
normal response.

## The strategies

### `accept_swap`

thetarelay adds a `<speculative_draft>` block and an instruction to the last user message. The
instruction tells the frontier model: if the draft is correct, write only `<ACCEPT/>`. If the
draft is not correct, write the full response.

If the response is `<ACCEPT/>`, thetarelay replaces it with the draft. The agent receives a normal
Anthropic or OpenAI response that contains the draft text and tool calls.

thetarelay accepts small differences. It removes `<think>` blocks of reasoning models. It accepts a
response that starts with the token and is not much longer than the token.

### `prediction`

OpenAI [Predicted Outputs](https://platform.openai.com/docs/guides/predicted-outputs) let the
server use a predicted text to decode faster. thetarelay puts the draft text in the `prediction`
field. It does not change the messages. The frontier model always writes the full response. This
strategy works only for OpenAI text drafts.

### `context_only`

thetarelay adds the draft to the last user message as a hint. There is no acceptance token. The
frontier model always writes the full response. This strategy can improve quality, but it does not
decrease output tokens.

### `auto`

thetarelay uses `prediction` for OpenAI drafts that contain text only. It uses `accept_swap` for
all other drafts.

## Why thetarelay changes only the last user message

Anthropic and OpenAI keep a prompt prefix cache. A cached input token costs approximately 10 % of
a normal input token. The cache works only if the start of the prompt does not change.

Headroom's `CacheAligner` keeps the system prompt and the earlier messages stable. thetarelay adds
the draft at the end of the last user message only. Thus the system prompt and all earlier
messages stay the same, and the cache continues to work. The draft adds uncached input tokens
only.

## How thetarelay sizes the local context

The local model must read its prompt before it can write a draft. On a laptop, this takes
approximately 1 second for each 1 300 characters, so a long conversation can use all of
`THETARELAY_LOCAL_TIMEOUT`.

Local servers such as Ollama also keep a prompt cache. If a request starts with exactly the same
messages as the previous request, the server reads only the new messages. thetarelay uses this:

- The window keeps the same first message from turn to turn, and new messages are added at the
  end. Each draft then costs only the time to read the new text.
- The drafting instructions are in a system message at the start. If they were added to the last
  user message, that message would change on the next turn and the cache would stop at it.
- If the new text cannot be read before the timeout, or the window becomes larger than
  `THETARELAY_LOCAL_MAX_CONTEXT_CHARS`, the window starts again from the newest messages that the
  local model can read in time.
- thetarelay measures the read speed on each draft. A slower model or computer gets a smaller
  window.

This applies to `thetarelay proxy` and the Headroom extension. `eval` and `bench` use a fixed
window. To use the fixed window in the proxy, set `THETARELAY_LOCAL_ADAPTIVE_CONTEXT=false`.

## Streaming

With streaming, the frontier response arrives in parts. thetarelay must decide early if the
response is `<ACCEPT/>` or a full response.

thetarelay keeps the first parts of the stream. It stops this and sends all parts to the agent when
one of these conditions occurs:

- The response starts a tool call.
- The text is not the start of `<ACCEPT/>`.
- The text is more than 8 characters longer than `<ACCEPT/>`.

If the stream ends and the text is `<ACCEPT/>`, thetarelay sends a new stream. This stream contains
the draft as normal Anthropic or OpenAI stream events. The agent cannot see a difference from a
normal stream.

When the frontier model does not accept the draft, the delay is very small. thetarelay holds only
the first few characters.

## Inside Headroom

As a Headroom extension, thetarelay has two parts:

- A turn hook. Headroom calls it after compression and before it sends the request upstream. The
  hook gets the draft and adds it to the messages.
- An ASGI middleware. It examines the streaming response and swaps `<ACCEPT/>` for the draft.

thetarelay reports its savings to the Headroom statistics with the name `thetarelay_speculative`.

## Related pages

- [Cost and latency model](cost-and-latency-model.md)
- [Known limitations](../reference/known-limitations.md)
