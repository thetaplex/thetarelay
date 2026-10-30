# How to control when and how thetacode drafts

## Select a strategy

1. Select a value from the table.
2. Set it with `--strategy` or with `THETACODE_STRATEGY`.

| Value | Use it when |
|---|---|
| `auto` | Default. Use it in most cases. thetacode selects `prediction` for OpenAI text drafts and `accept_swap` for all other drafts. |
| `accept_swap` | You want the frontier model to reply `<ACCEPT/>` and thetacode to return the local draft. |
| `context_only` | You want to give the draft to the frontier model as a hint only. The frontier model always writes the full response. |
| `prediction` | Do not use. See [Known limitations](../reference/known-limitations.md). |

Example:

```sh
thetacode proxy --strategy context_only
```

## Stop drafting for one request

Add the `x-thetacode-bypass` header with the value `1`, `true` or `yes`:

```sh
curl http://127.0.0.1:8788/v1/messages -H "x-thetacode-bypass: 1" ...
```

The `x-headroom-bypass` header has the same effect. thetacode does not send `x-thetacode-*`
headers to the provider.

## Stop drafting for small models

thetacode does not draft when the model name contains a word from a skip list. The default list is
`haiku,mini,nano,flash`.

1. Set the full list as a comma-separated value:

   ```sh
   export THETACODE_SKIP_MODELS=haiku,mini,nano,flash,lite
   ```

2. Restart the proxy.

To draft for all models, set `THETACODE_SKIP_MODELS` to an empty value. If you do not set the
variable, thetacode uses the default list.

## Draft on short prompts

thetacode does not draft when the last message has fewer than 15 characters.

- To change the limit, set `THETACODE_MIN_PROMPT_CHARS`.
- To draft on all prompts, set `THETACODE_SELECTIVE=false`.

## Make drafts faster

If `p95_local_draft_latency_ms` in the statistics is high, make the local context smaller:

1. Decrease `THETACODE_LOCAL_MAX_CONTEXT_CHARS` (default `24000`).
2. Decrease `THETACODE_LOCAL_RECENT_MESSAGES` (default `10`).
3. Decrease `THETACODE_LOCAL_MAX_TOKENS` (default `384`).
4. Restart the proxy.

For all settings, see [Configuration](../reference/configuration.md).
