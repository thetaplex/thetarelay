# Cost and latency model

Speculative drafting is not free. Each draft adds time and input tokens. This page explains when
the savings are larger than the added cost.

## Terms

| Symbol | Meaning | Default |
|---|---|---|
| *p* | Acceptance rate: accepted drafts / all drafts | — |
| *T*<sub>draft</sub> | Local draft time | 260 ms |
| *N*<sub>out</sub> | Output tokens of a typical response | 220 |
| *N*<sub>in</sub> | Input tokens that the draft adds | 280 |
| *R* | Frontier output speed | 65 tokens/s |
| *P*<sub>in</sub> | Input price | 3 USD per 10<sup>6</sup> tokens |
| *P*<sub>out</sub> | Output price | 15 USD per 10<sup>6</sup> tokens |

An accepted draft replaces *N*<sub>out</sub> output tokens with 2 tokens (`<ACCEPT/>`).

## Latency break-even

Every draft costs *T*<sub>draft</sub>. Only accepted drafts save decode time. Drafting saves time
when:

*p* × (*N*<sub>out</sub> − 2) / *R* > *T*<sub>draft</sub>

With the defaults: (218 / 65) s = 3354 ms. 260 / 3354 = 7.8 %. Thus drafting saves time if the
frontier model accepts more than approximately 8 % of drafts.

## Cost break-even

Every draft adds *N*<sub>in</sub> uncached input tokens. Only accepted drafts save output tokens.
Drafting saves money when:

*p* × (*N*<sub>out</sub> − 2) × *P*<sub>out</sub> > *N*<sub>in</sub> × *P*<sub>in</sub>

With the defaults: (280 × 3) / (218 × 15) = 840 / 3270 = 25.7 %. Thus drafting saves money if
the frontier model accepts more than approximately 26 % of drafts.

The cost break-even is higher than the latency break-even. Each injected token is cheap, but each
draft adds more tokens than a typical response.

## Why the prefix cache is important

The cost model assumes that the system prompt and the earlier messages stay in the provider
cache. If thetarelay changed the earlier messages, the full prompt would become uncached input. In
an 80 000-token context, this would cost much more than the draft saves. For this reason,
thetarelay changes only the end of the last user message. See
[How speculative drafting works](how-speculative-drafting-works.md).

## The `prediction` strategy

OpenAI Predicted Outputs do not add a block to the messages. thetarelay records no input overhead
for them. The frontier model writes all output tokens, so thetarelay records no output token
savings. The provider bills rejected prediction tokens as output tokens.

## How `bench` uses this model

`thetarelay bench` sends the built-in scenarios through the real proxy code. A mock transport
replaces the local model and the frontier model. Each scenario has fixed latencies and token
counts. The mock frontier accepts the draft if the scenario marks the draft as accurate.

Thus `bench` shows that the proxy code works and that the arithmetic is correct. It does not
measure a real acceptance rate. To measure your acceptance rate, use `thetarelay eval` with a
target model, or read `acceptance_rate_percent` from `/thetarelay/stats` in real use.

The `/thetarelay/stats` values use the estimates from [Configuration](../reference/configuration.md).
Set `THETARELAY_INPUT_USD_PER_M`, `THETARELAY_OUTPUT_USD_PER_M` and `THETARELAY_FRONTIER_OUTPUT_TPS`
to the values of your model.
