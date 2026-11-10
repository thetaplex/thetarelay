# Design: shadow routing

## Context

`route()` in `src/thetarelay/proxy.py` writes one turn-log line per request (`recorder.record`)
when the reply ends: at the end of `relay()` for streams, before returning for JSON replies.
Change 03 added `step_groups` and keeps the relayed bytes of a stream (`seen`). `decide()` in
`router.py` returns early for `lite` and `frontier` modes and does not ask the judge there.
`_outbound(..., "lite", ...)` builds lite-tier requests with only the lite key.

## Decisions

- **Builds on 03.** This change is cut from `spec/03-log-routing-signals` and merges after it.
- **One turn-log line per request, written after the shadow.** When a request has a shadow, its
  line is written once both the frontier reply has reached the agent and the shadow has
  finished, by a background task. The agent's reply is never delayed. Alternative: a second
  line for the shadow. Rejected: every report counts lines as requests.
- **Shadow wait is capped** at `THETARELAY_LITE_TIMEOUT`. On timeout or error the line gets
  `"shadow": {"error": "<short reason>"}`. Background tasks are kept in a set and cancelled at
  shutdown; a cancelled shadow writes its line with `"error": "shutdown"`.
- **Which requests get a shadow:** requests whose decision tier is `frontier` and that are not
  triaged (triaged requests already get `triage_verdict`), chosen with probability
  `THETARELAY_SHADOW_SHARE`. Escalations (`lite_failed`, `lite_stopped`, guard) already have a
  lite reply and get no shadow.
- **What is logged, without file contents.** For each tool call of the frontier reply and of the
  shadow reply: `name`, `group`, `target` (the `path`/`file_path` argument, or the shell
  `command`, cut to 200 characters), and `args_sha256` (SHA-256 of the arguments as canonical
  JSON: sorted keys, no spaces). `match` is true when both replies have the same number of calls
  and each pair has the same name and target. Exact-argument agreement can be computed offline
  from the hashes, so the match rule can change without logging edit text.
- **Judge in every mode.** When the shadow share is above 0.0, `decide()` also asks the judge in
  `frontier`, `lite`, `random` and `triage` modes and records `p_easy`, `judge_ms` and
  `judge_error`; the tier and reason are unchanged. A judge error never changes routing.

## Risks / Trade-offs

- [Hosted lite tier costs money per shadow] → Default 0.0; the docs say so.
- [Turn-log lines arrive out of order] → Each line already has `ts`; analysis sorts by it.
