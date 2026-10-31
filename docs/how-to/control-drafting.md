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

## Make drafts faster

If `p95_local_draft_latency_ms` in the statistics is high, make the local context smaller:

1. Decrease `THETARELAY_LOCAL_TIMEOUT` (default `5.0`). With the adaptive context (default), the window then becomes smaller.
2. If you set `THETARELAY_LOCAL_ADAPTIVE_CONTEXT=false`, decrease `THETARELAY_LOCAL_MAX_CONTEXT_CHARS` and `THETARELAY_LOCAL_RECENT_MESSAGES` instead.
3. Decrease `THETARELAY_LOCAL_MAX_TOKENS` (default `384`).
4. Restart the proxy.

For all settings, see [Configuration](../reference/configuration.md).
