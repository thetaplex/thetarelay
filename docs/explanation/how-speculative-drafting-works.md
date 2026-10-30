# How speculative drafting works

## The problem

A frontier model writes output tokens slowly. A 220-token code edit at 65 tokens per second takes
more than 3 seconds. Output tokens also cost more than input tokens. Many coding-agent turns are
simple. A small local model can write the same response in less time.

thetacode lets a local model write a draft first. The frontier model then checks the draft. If the
draft is correct, the frontier model writes only a short acceptance token. thetacode then returns
the draft as the response.

## The request path

1. The coding agent sends a request to thetacode.
2. Headroom compresses the context, if Headroom is active.
3. thetacode decides if the request is eligible for a draft. It does not draft for bypass headers,
   small models, short prompts, or requests that already contain a draft.
4. thetacode converts the request to an OpenAI Chat Completions request for the local model. It
   sends only the recent messages, a limited number of characters, and a maximum of 16 tools.
5. The local model returns text, tool calls, or both.
6. thetacode adds the draft to the request and sends it to the frontier model.
7. thetacode examines the response and returns the draft or the frontier response.

If the local model fails or is too slow, thetacode sends the original request. The agent gets a
normal response.

## The strategies

### `accept_swap`

thetacode adds a `<speculative_draft>` block and an instruction to the last user message. The
instruction tells the frontier model: if the draft is correct, write only `<ACCEPT/>`. If the
draft is not correct, write the full response.

If the response is `<ACCEPT/>`, thetacode replaces it with the draft. The agent receives a normal
Anthropic or OpenAI response that contains the draft text and tool calls.

thetacode accepts small differences. It removes `<think>` blocks of reasoning models. It accepts a
response that starts with the token and is not much longer than the token.

### `prediction`

OpenAI [Predicted Outputs](https://platform.openai.com/docs/guides/predicted-outputs) let the
server use a predicted text to decode faster. thetacode puts the draft text in the `prediction`
field. It does not change the messages. The frontier model always writes the full response. This
strategy works only for OpenAI text drafts.

### `context_only`

thetacode adds the draft to the last user message as a hint. There is no acceptance token. The
frontier model always writes the full response. This strategy can improve quality, but it does not
decrease output tokens.

### `auto`

thetacode uses `prediction` for OpenAI drafts that contain text only. It uses `accept_swap` for
all other drafts.

## Why thetacode changes only the last user message

Anthropic and OpenAI keep a prompt prefix cache. A cached input token costs approximately 10 % of
a normal input token. The cache works only if the start of the prompt does not change.

Headroom's `CacheAligner` keeps the system prompt and the earlier messages stable. thetacode adds
the draft at the end of the last user message only. Thus the system prompt and all earlier
messages stay the same, and the cache continues to work. The draft adds uncached input tokens
only.

## Streaming

With streaming, the frontier response arrives in parts. thetacode must decide early if the
response is `<ACCEPT/>` or a full response.

thetacode keeps the first parts of the stream. It stops this and sends all parts to the agent when
one of these conditions occurs:

- The response starts a tool call.
- The text is not the start of `<ACCEPT/>`.
- The text is more than 8 characters longer than `<ACCEPT/>`.

If the stream ends and the text is `<ACCEPT/>`, thetacode sends a new stream. This stream contains
the draft as normal Anthropic or OpenAI stream events. The agent cannot see a difference from a
normal stream.

When the frontier model does not accept the draft, the delay is very small. thetacode holds only
the first few characters.

## Inside Headroom

As a Headroom extension, thetacode has two parts:

- A turn hook. Headroom calls it after compression and before it sends the request upstream. The
  hook gets the draft and adds it to the messages.
- An ASGI middleware. It examines the streaming response and swaps `<ACCEPT/>` for the draft.

thetacode reports its savings to the Headroom statistics with the name `thetacode_speculative`.

## Related pages

- [Cost and latency model](cost-and-latency-model.md)
- [Known limitations](../reference/known-limitations.md)
