# usage-report Specification

## Purpose
Show a user, from their own turn log, what routing cost and what the same requests would have
cost on the frontier tier alone.

## Requirements

### Requirement: Report from a turn log

The command `thetarelay report <path>` SHALL print, per tier: requests, input, cached, cache
write and output tokens, and cost; and in total: actual cost, estimated frontier-only cost, and
the saving as a percentage.

#### Scenario: Estimate arithmetic

- **WHEN** a lite line has 1,000,000 input tokens (none cached) and 10,000 output tokens, and the
  frontier prices are 5 USD and 25 USD per million input and output tokens
- **THEN** that line adds 5.25 USD to the frontier-only estimate

#### Scenario: Requests without a reported cost

- **WHEN** 60 lite lines have `cost_usd: null`
- **THEN** they count as 0 in the actual cost, and the report says 60 requests had no reported
  cost

#### Scenario: Discarded lite reply

- **WHEN** a frontier line has `cost_usd: 1.0` and `discarded_lite_cost_usd: 0.1`
- **THEN** it adds 1.1 USD to the actual cost and 1.0 USD to the frontier-only estimate

#### Scenario: JSON output

- **WHEN** the report runs with `--json`
- **THEN** it prints one JSON object with the same numbers as the table

#### Scenario: Mixed log

- **WHEN** the log has 60 lite and 40 frontier requests with `cost_usd` set
- **THEN** the report shows both tiers, the total of `cost_usd`, and the estimate

### Requirement: What a line costs

The actual cost of a line SHALL be its `cost_usd` plus `triage_cost_usd` plus
`discarded_lite_cost_usd`. The frontier-only estimate SHALL count a frontier line without the
discarded lite reply.

#### Scenario: Handed-back triage call

- **WHEN** a line has `cost_usd: 1.0` and `triage_cost_usd: 0.5`
- **THEN** it adds 1.5 USD to the actual cost

### Requirement: Shadow cost shown apart

The report SHALL show the number of lines with a `shadow` object and the sum of their
`shadow.cost_usd` on a separate line, and SHALL NOT add that cost to the actual cost, the
estimate or the saving. With no shadows the line SHALL be left out.

#### Scenario: Shadows present

- **WHEN** three lines have shadows with costs 0.03, 0.01 and none
- **THEN** the report shows `shadow requests: 3, $0.0400 (labelling; not in the totals above)`,
  and `--json` has `shadow_requests: 3` and `shadow_usd: 0.04`

### Requirement: Token pricing per API format

The estimate SHALL price uncached input as `input_tokens` for Anthropic-format lines, and as
`input_tokens` minus cached and cache-write tokens, at least 0, for OpenAI-format lines.
Without a cache price, cached and cache-write tokens SHALL use the input price. Prices given on
the command line are USD per million tokens, and `--price-input` and `--price-output` SHALL be
given together.

#### Scenario: OpenAI-format line with cache

- **WHEN** an OpenAI-format lite line has 1,000 input tokens, 800 of them cached
- **THEN** 200 tokens are priced as uncached input and 800 as cache reads

#### Scenario: Anthropic-format line with cache

- **WHEN** an Anthropic-format lite line has `input_tokens: 200` and `cached_tokens: 800`
- **THEN** 200 tokens are priced as uncached input and 800 as cache reads

#### Scenario: Only one price

- **WHEN** the report runs with `--price-input 5` and no `--price-output`
- **THEN** it exits with an error

### Requirement: Labelled estimate

The report SHALL label the frontier-only cost as an estimate and state its assumption: lite
requests priced at frontier prices with the same token counts.

#### Scenario: Output text

- **WHEN** the report prints the frontier-only cost
- **THEN** the line includes the word "estimate"

### Requirement: Fallbacks by reason

The report SHALL count requests by routing reason, including fallback suffixes such as
`lite_stopped` and `lite_failed`. Each `; `-separated part of a reason SHALL count on its own.

#### Scenario: Fallbacks present

- **WHEN** 5 requests have a reason ending in `; lite_stopped`
- **THEN** the report lists `lite_stopped: 5`, and those requests also count under `judge`

### Requirement: Missing data

The report SHALL skip lines that are not valid JSON and count them, and SHALL show `n/a` for a
cost it cannot compute.

#### Scenario: Prices given offline

- **WHEN** the report runs with `--offline --price-input 5 --price-output 25`
- **THEN** it makes no network request and uses those prices for the estimate

#### Scenario: Price lookup fails

- **WHEN** the models list cannot be fetched
- **THEN** the frontier-only cost shows `n/a` with the reason, and the rest of the report prints

#### Scenario: Prices unavailable offline

- **WHEN** the report runs with `--offline` and no prices are given
- **THEN** the frontier-only cost shows `n/a` and the actual cost is still shown
