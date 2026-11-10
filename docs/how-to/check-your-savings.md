# How to check your savings

Use the turn log to see what routing cost and what the frontier model alone would have cost.

1. Set the path of the turn log, then start the proxy:

   ```sh
   export THETARELAY_TURN_LOG=turns.jsonl
   thetarelay proxy
   ```

   The file contains prompt text. Keep it private.

2. Use your coding agent through ThetaRelay as usual. See
   [How to connect a coding agent](connect-coding-agents.md).

3. Run the report:

   ```sh
   thetarelay report turns.jsonl
   ```

4. Read the output. This example has one frontier request and two lite requests:

   ```text
   tier      requests     input    cached  cache_wr    output  cost
   frontier         1     12000     10000         0       800  $0.0225
   lite             2     41000         0         0       800  $0.0440
   actual cost: $0.0665
   frontier-only cost (estimate): $0.2475
   Estimate: lite requests priced at anthropic/claude-opus-5.5 prices with the same token counts (input $5.00/M, output $25.00/M, cache read $0.50/M, cache write $6.25/M; source: OpenRouter)
   saving: 73.1% (estimate)
   reasons:
     judge: easy: 2
     judge: hard: 1
   ```

   - `actual cost` is the cost that the provider reported, summed over all requests.
   - `frontier-only cost (estimate)` is the cost if the frontier model did every request.
   - `saving` is the difference between the two, as a percentage.

## The estimate is a guide

ThetaRelay prices each lite request at the frontier prices with the same token counts. The
frontier model can use a different number of tokens, and its cache can behave differently. Use
the saving as a guide, not as a bill.

## Use the report offline

Give the prices yourself, in USD per million tokens. `--offline` stops the price lookup:

```sh
thetarelay report turns.jsonl --offline --price-input 5 --price-output 25
```

Add `--price-cache-read` and `--price-cache-write` if you know them. Without them, ThetaRelay
uses the input price. If you give no prices with `--offline`, the estimate and the saving are
`n/a`.

A local lite server reports no cost. ThetaRelay counts those requests as cost 0 and prints
the number of them.
