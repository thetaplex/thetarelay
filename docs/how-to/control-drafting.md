# How to control when and how thetarelay drafts

## Select a strategy

1. Select a value from the table.
2. Set it with `--strategy` or with `THETARELAY_STRATEGY`.

| Value | Use it when |
|---|---|
| `auto` | Default. Use it in most cases. thetarelay selects `prediction` for OpenAI text drafts and `accept_swap` for all other drafts. |
| `accept_swap` | You want the frontier model to reply `<ACCEPT/>` and thetarelay to return the local draft. |
| `context_only` | You want to give the draft to the frontier model as a hint only. The frontier model always writes the full response. |
| `prediction` | Do not use. See [Known limitations](../reference/known-limitations.md). |

Example:

```sh
thetarelay proxy --strategy context_only
```

## Stop drafting for one request

Add the `x-thetarelay-bypass` header with the value `1`, `true` or `yes`:

```sh
curl http://127.0.0.1:8788/v1/messages -H "x-thetarelay-bypass: 1" ...
```

The `x-headroom-bypass` header has the same effect. thetarelay does not send `x-thetarelay-*`
headers to the provider.

## Stop drafting for small models

thetarelay does not draft when the model name contains a word from a skip list. The default list is
`haiku,mini,nano,flash`.

1. Set the full list as a comma-separated value:

   ```sh
   export THETARELAY_SKIP_MODELS=haiku,mini,nano,flash,lite
   ```

2. Restart the proxy.

To draft for all models, set `THETARELAY_SKIP_MODELS` to an empty value. If you do not set the
variable, thetarelay uses the default list.

## Draft on short prompts

thetarelay does not draft when the last message has fewer than 15 characters.

- To change the limit, set `THETARELAY_MIN_PROMPT_CHARS`.
- To draft on all prompts, set `THETARELAY_SELECTIVE=false`.

## Tune the adaptive draft context

ThetaRelay sizes the local draft context dynamically for each conversation
(`THETARELAY_LOCAL_ADAPTIVE_CONTEXT=true` by default). The window keeps its start and grows
while new text fits within the reading budget of `THETARELAY_LOCAL_TIMEOUT`.

To tune the adaptive context:

1. **Adjust the timeout:** `THETARELAY_LOCAL_TIMEOUT` (default `5.0` seconds) controls how much
   new text the local model can read on each turn (80 % of timeout is allocated to prompt reading).
   - If local drafts time out on large tool results, increase `THETARELAY_LOCAL_TIMEOUT` to `8.0`.
   - If drafts are too slow on modest hardware, decrease it to `3.5`.
2. **Set the context ceiling:** `THETARELAY_LOCAL_MAX_CONTEXT_CHARS` (default `64000`) is the upper
   character limit. When the conversation exceeds this ceiling, the window restarts from recent turns.
3. **Switch to a fixed-size stepping window:** If your local server does not benefit from adaptive
   growth, disable adaptive context:

   ```sh
   export THETARELAY_LOCAL_ADAPTIVE_CONTEXT=false
   export THETARELAY_LOCAL_MAX_CONTEXT_CHARS=8000
   export THETARELAY_LOCAL_RECENT_MESSAGES=10
   ```

   When disabled, ThetaRelay moves the window start in steps of half the message limit and half the
   character limit, preserving prompt cache between steps.

## Make drafts faster

If `p95_local_draft_latency_ms` in the statistics is high, make the local context smaller:

1. Decrease `THETARELAY_LOCAL_TIMEOUT` (default `5.0`). With adaptive context (default), the window
   shrinks automatically to fit the smaller timeout.
2. If you set `THETARELAY_LOCAL_ADAPTIVE_CONTEXT=false`, decrease `THETARELAY_LOCAL_MAX_CONTEXT_CHARS`
   (e.g. `6000`) and `THETARELAY_LOCAL_RECENT_MESSAGES` (e.g. `6`) instead.
3. Decrease `THETARELAY_LOCAL_MAX_TOKENS` (default `384`).
4. Restart the proxy.

For all settings, see [Configuration](../reference/configuration.md).
