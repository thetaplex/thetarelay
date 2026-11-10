# Design: guard lite actions

## Context

`route()` in `src/thetarelay/proxy.py` buffers a lite reply (streamed or not, with
`upstream.aread()`) only when `escalate_on_stop` is on, and escalates with `lite_stopped` when the
reply has no tool call. `steps.tool_calls(raw)` and `classify` (change 01) give the calls and
their groups.

## Decisions

- **Buffer when either check is on.** The lite reply is buffered when `escalate_on_stop` is true
  or the guard level is not `off`. The guard runs on the same buffer, before the stop check.
  Streamed lite replies already reach the agent only after buffering; no new delay.
- **One reason per escalation, in this order:** `lite_malformed`, then `lite_irreversible`, then
  `lite_wrote` (level `writes`), then `lite_stopped`. The first that applies wins.
- **Malformed** means any of: a tool call is signalled but no call can be read; a call names a
  tool that is not in the request's `tools` (Anthropic `tools[].name`, OpenAI
  `tools[].function.name`; a request without `tools` makes every call malformed); its arguments
  are not a JSON object; or it lacks a key listed in the tool's `required` (Anthropic
  `input_schema.required`, OpenAI `function.parameters.required`). Tool names are compared
  exactly, as agents send them. Types and other schema rules are not checked.
- **`run` stays with the lite tier at level `writes`.** Tests are the lite tier's main feedback
  and stay in the working tree.
- **Forced lite mode is not guarded.** With `THETARELAY_ROUTE_MODE=lite` the proxy must not fall
  back (evaluation baseline), so the guard does not run.
- **Turn log:** a discarded reply adds `guarded_step_groups` (its groups) and
  `lite_output_chars`. The frontier request is built from the client's body, as for
  `lite_stopped`.
- **Default `off`** until an evaluation run (change 04) shows the guard pays for itself.

## Risks / Trade-offs

- [Agents that rename tools between requests] → Compared against the current request's
  `tools` only, which is what the model was offered.
- [More frontier traffic at level `writes`] → Opt-in; the turn log shows how often it fires.
