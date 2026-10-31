"""Native Headroom Proxy Extension (`headroom.proxy_extension`).

Allows ThetaRelay to run directly *inside* `headroom proxy` or `headroom wrap` via:
    headroom proxy --proxy-extension thetarelay
or:
    HEADROOM_PROXY_EXTENSIONS=thetarelay headroom wrap claude
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import SpeculativeConfig
from .engine import (
    AdaptiveDraftWindow,
    DraftResult,
    SpeculativeMetrics,
    build_anthropic_response_from_draft,
    build_openai_response_from_draft,
    extract_text_from_content,
    inject_draft_into_body,
    response_accepted_draft,
)

logger = logging.getLogger("thetarelay.headroom_ext")


class ThetaRelayTurnHook:
    """Headroom TurnHook that runs local speculative drafting AFTER Headroom's
    context compression pipeline (SmartCrusher, CodeAwareCompressor, CCR) has shrunk
    the context window!
    """

    name = "thetarelay_speculative"
    stream_safe = True  # Allow running on_request on streaming turns too!

    def __init__(
        self,
        config: SpeculativeConfig,
        metrics: SpeculativeMetrics,
        draft_store: dict[int, DraftResult],
        sync_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.metrics = metrics
        self.draft_store = draft_store
        self._sync_transport = sync_transport
        self.window = AdaptiveDraftWindow()

    def on_request(self, ctx: Any) -> None:
        """Called by Headroom after compression, right before upstream dispatch.

        Note: `TurnHook.on_request` in Headroom is synchronous (`def on_request(self, ctx)`),
        so for async local drafting we either run the draft in our outer ASGI middleware
        or via a dedicated sync/thread bridge if needed. To avoid blocking the event loop,
        when running inside Headroom we use `ThetaRelayASGIMiddleware` + `TurnHook` together:
        if the ASGI middleware already attached a draft or if we draft on the compressed
        messages using a thread-pooled httpx client, we inject it cleanly here!
        """
        provider = getattr(ctx, "provider", "anthropic")
        model = getattr(ctx, "model", "")
        messages = getattr(ctx, "messages", [])
        tools = getattr(ctx, "tools", None)

        if not messages:
            return

        body_view: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
        }

        # Run local draft synchronously in a separate thread if not already drafted,
        # OR use httpx.Client so we get the POST-COMPRESSED messages from Headroom!
        self.metrics.requests_total += 1
        self.metrics.drafts_attempted += 1

        draft = self._call_local_drafter_sync(body_view, provider)
        if draft.skipped:
            self.metrics.drafts_skipped += 1
            return
        if draft.error or not draft.has_draft:
            self.metrics.drafts_failed += 1
            return

        self.metrics.drafts_succeeded += 1
        self.metrics.local_draft_latency_ms_sum += draft.latency_ms
        self.metrics.local_draft_latencies_ms.append(draft.latency_ms)

        updated_body, active_mode = inject_draft_into_body(body_view, provider, draft, self.config)
        if active_mode == "openai_prediction":
            self.metrics.openai_prediction_injected_count += 1
            self.metrics.prefix_cache_preserved_count += 1

        ctx.messages = updated_body["messages"]
        # Store draft keyed by ctx.tags id AND in ctx.tags['_headroom_savings_attribution']
        # metadata or draft_store so streaming ASGI middleware can intercept <ACCEPT/>
        if isinstance(getattr(ctx, "tags", None), dict):
            ctx.tags["_thetarelay_draft"] = draft
            ctx.tags["_thetarelay_mode"] = active_mode
            ledger = ctx.tags.get("_headroom_savings_attribution")
            if isinstance(ledger, list):
                self.draft_store[id(ledger)] = draft
            try:
                from headroom.proxy.savings_attribution import STAGE_TIMING_TAG

                timings = ctx.tags.get(STAGE_TIMING_TAG)
                if isinstance(timings, dict):
                    timings["ext:thetarelay_local_draft"] = (
                        timings.get("ext:thetarelay_local_draft", 0.0) + draft.latency_ms
                    )
            except ImportError:
                pass

    def _call_local_drafter_sync(self, body: dict[str, Any], provider: str) -> DraftResult:
        """Synchronous counterpart to `call_local_drafter` for Headroom's sync `on_request` hook."""
        import time
        import uuid

        from .engine import convert_to_openai_draft_payload, should_speculate

        eligible, reason = should_speculate(body, provider, self.config)
        if not eligible:
            return DraftResult(skipped=True, skip_reason=reason)

        new_chars = 0
        if self.config.local_adaptive_context:
            payload, new_chars = self.window.build_payload(body, provider, self.config)
        else:
            payload = convert_to_openai_draft_payload(body, provider, self.config)
        url = f"{self.config.local_base_url}/chat/completions"
        req_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.local_api_key}",
        }

        started = time.perf_counter()
        try:
            with httpx.Client(
                timeout=self.config.local_timeout_seconds,
                transport=self._sync_transport,
            ) as client:
                resp = client.post(url, json=payload, headers=req_headers)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self.window.observe(new_chars, elapsed_ms)
            if resp.status_code != 200:
                return DraftResult(latency_ms=elapsed_ms, error=f"http_{resp.status_code}")
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return DraftResult(latency_ms=elapsed_ms, error="empty_choices")

            msg = (choices[0] or {}).get("message") or {}
            draft_text = str(msg.get("content") or "")
            raw_tool_calls = msg.get("tool_calls") or []
            normalized_tools: list[dict[str, Any]] = []
            for tc in raw_tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                args_raw = fn.get("arguments", "{}")
                try:
                    args_parsed = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except json.JSONDecodeError:
                    args_parsed = {"raw": args_raw}
                normalized_tools.append(
                    {
                        "id": tc.get("id") or f"toolu_draft_{uuid.uuid4().hex[:8]}",
                        "name": fn.get("name", ""),
                        "input": args_parsed if isinstance(args_parsed, dict) else {},
                    }
                )
            usage = data.get("usage") or {}
            completion_tokens = int(
                usage.get("completion_tokens")
                or max(1, (len(draft_text) + len(json.dumps(normalized_tools))) // 4)
            )
            return DraftResult(
                text=draft_text,
                tool_calls=normalized_tools,
                latency_ms=elapsed_ms,
                draft_tokens_est=completion_tokens,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self.window.observe(new_chars, elapsed_ms)
            return DraftResult(latency_ms=elapsed_ms, error=str(exc))

    async def on_response(
        self, ctx: Any, response: dict[str, Any], call_model: Any
    ) -> dict[str, Any] | None:
        """Called on buffered responses; swaps `<ACCEPT/>` with the full local draft."""
        tags = getattr(ctx, "tags", None)
        if not isinstance(tags, dict):
            return None
        draft: DraftResult | None = tags.get("_thetarelay_draft")
        if not draft or not draft.has_draft:
            return None

        provider = getattr(ctx, "provider", "anthropic")
        if provider == "anthropic":
            resp_text = extract_text_from_content(response.get("content", []))
            if response_accepted_draft(resp_text, self.config):
                saved_tok, _, _ = self.metrics.record_turn_outcome(
                    draft=draft, config=self.config, accepted=True
                )
                saved_usd = (saved_tok / 1_000_000.0) * self.config.estimated_output_usd_per_million
                if hasattr(ctx, "record_savings"):
                    ctx.record_savings(
                        "thetarelay_speculative",
                        tokens=saved_tok,
                        usd=saved_usd,
                        details={"accepted_draft": True},
                    )
                return build_anthropic_response_from_draft(response, draft)
            else:
                self.metrics.record_turn_outcome(draft=draft, config=self.config, accepted=False)
        elif provider == "openai":
            choices = response.get("choices") or []
            if choices:
                msg = (choices[0] or {}).get("message") or {}
                resp_text = str(msg.get("content") or "")
                if response_accepted_draft(resp_text, self.config):
                    saved_tok, _, _ = self.metrics.record_turn_outcome(
                        draft=draft, config=self.config, accepted=True
                    )
                    saved_usd = (
                        saved_tok / 1_000_000.0
                    ) * self.config.estimated_output_usd_per_million
                    if hasattr(ctx, "record_savings"):
                        ctx.record_savings(
                            "thetarelay_speculative",
                            tokens=saved_tok,
                            usd=saved_usd,
                            details={"accepted_draft": True},
                        )
                    return build_openai_response_from_draft(response, draft)
                else:
                    self.metrics.record_turn_outcome(
                        draft=draft, config=self.config, accepted=False
                    )

        return None


class ThetaRelayStreamingResponseMiddleware:
    """ASGI Middleware that intercepts streaming SSE responses inside Headroom when a
    speculative draft is active on the request's scope, swapping `<ACCEPT/>` with
    the drafted SSE events on the fly and recording Headroom scope savings/timing.
    """

    def __init__(
        self,
        app: Any,
        config: SpeculativeConfig,
        metrics: SpeculativeMetrics,
        draft_store: dict[int, DraftResult],
    ) -> None:
        self.app = app
        self.config = config
        self.metrics = metrics
        self.draft_store = draft_store

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not any(
            path.endswith(p) for p in ("/v1/messages", "/v1/chat/completions", "/chat/completions")
        ):
            await self.app(scope, receive, send)
            return

        provider = "anthropic" if path.endswith("/v1/messages") else "openai"
        state = scope.setdefault("state", {})
        ledger = state.setdefault("headroom_savings_attribution", [])

        is_sse = False
        passthrough = False
        buffered_messages: list[dict[str, Any]] = []
        sse_buffer = bytearray()
        accumulated_text = ""
        saw_non_text = False
        message_start_event: dict[str, Any] | None = None
        usage_delta: dict[str, Any] | None = None
        accept_token = self.config.accept_token

        async def wrapped_send(message: dict[str, Any]) -> None:
            nonlocal \
                is_sse, \
                passthrough, \
                accumulated_text, \
                saw_non_text, \
                message_start_event, \
                usage_delta

            draft = self.draft_store.get(id(ledger))
            if draft is None or not draft.has_draft:
                await send(message)
                return

            if message["type"] == "http.response.start":
                headers = {
                    k.decode("latin-1").lower(): v.decode("latin-1")
                    for k, v in message.get("headers", [])
                }
                if "text/event-stream" in headers.get("content-type", "").lower():
                    is_sse = True
                await send(message)
                return

            if message["type"] != "http.response.body" or not is_sse:
                await send(message)
                return

            if passthrough:
                await send(message)
                return

            chunk: bytes = message.get("body", b"")
            more_body: bool = message.get("more_body", False)
            if chunk:
                buffered_messages.append(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )
                sse_buffer.extend(chunk)

                while b"\n\n" in sse_buffer:
                    idx = sse_buffer.index(b"\n\n")
                    raw_ev = bytes(sse_buffer[:idx])
                    del sse_buffer[: idx + 2]
                    for line in raw_ev.decode("utf-8", errors="replace").split("\n"):
                        if not line.startswith("data:"):
                            continue
                        d_str = line[5:].strip()
                        if not d_str or d_str == "[DONE]":
                            continue
                        try:
                            d = json.loads(d_str)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(d, dict):
                            continue
                        if provider == "anthropic":
                            et = d.get("type", "")
                            if et == "message_start":
                                message_start_event = d
                            elif et == "content_block_start":
                                cb = d.get("content_block") or {}
                                if cb.get("type") not in ("text", "thinking"):
                                    saw_non_text = True
                            elif et == "content_block_delta":
                                delta = d.get("delta") or {}
                                if delta.get("type") == "text_delta":
                                    accumulated_text += str(delta.get("text", ""))
                                elif delta.get("type") == "input_json_delta":
                                    saw_non_text = True
                            elif et == "message_delta":
                                usage_delta = d.get("usage")
                        else:
                            choices = d.get("choices") or []
                            if choices:
                                delta = (choices[0] or {}).get("delta") or {}
                                if delta.get("tool_calls"):
                                    saw_non_text = True
                                if delta.get("content"):
                                    accumulated_text += str(delta["content"])

                    stripped = accumulated_text.lstrip()
                    if (
                        saw_non_text
                        or (
                            stripped
                            and not accept_token.startswith(stripped)
                            and not stripped.startswith(accept_token)
                        )
                        or len(stripped) > len(accept_token) + 8
                    ):
                        passthrough = True
                        break

            if passthrough:
                self.metrics.record_turn_outcome(draft=draft, config=self.config, accepted=False)
                self.draft_store.pop(id(ledger), None)
                for bmsg in buffered_messages:
                    await send(bmsg)
                buffered_messages.clear()
                if not more_body:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                return

            if not more_body:
                self.draft_store.pop(id(ledger), None)
                if response_accepted_draft(accumulated_text, self.config):
                    saved_tok, _, _ = self.metrics.record_turn_outcome(
                        draft=draft, config=self.config, accepted=True
                    )
                    saved_usd = (
                        saved_tok / 1_000_000.0
                    ) * self.config.estimated_output_usd_per_million
                    try:
                        from headroom.proxy.savings_attribution import record_scope_savings

                        record_scope_savings(
                            scope,
                            "thetarelay_speculative",
                            tokens=saved_tok,
                            usd=saved_usd,
                            details={"accepted_draft": True},
                        )
                    except ImportError:
                        pass

                    from .streaming import (
                        synthesize_anthropic_sse_from_draft,
                        synthesize_openai_sse_from_draft,
                    )

                    synth_events = (
                        synthesize_anthropic_sse_from_draft(
                            draft,
                            message_start_event=message_start_event,
                            usage_delta=usage_delta,
                        )
                        if provider == "anthropic"
                        else synthesize_openai_sse_from_draft(draft)
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b"".join(synth_events),
                            "more_body": False,
                        }
                    )
                else:
                    self.metrics.record_turn_outcome(
                        draft=draft, config=self.config, accepted=False
                    )
                    for bmsg in buffered_messages:
                        await send(bmsg)
                    await send({"type": "http.response.body", "body": b"", "more_body": False})

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            self.draft_store.pop(id(ledger), None)


def install(app: Any, config: Any) -> None:
    """Entry point invoked by `headroom proxy --proxy-extension thetarelay`."""
    spec_config = SpeculativeConfig.from_env()
    metrics = SpeculativeMetrics()
    draft_store: dict[int, DraftResult] = {}

    # 1. Register streaming response interceptor middleware
    app.add_middleware(
        ThetaRelayStreamingResponseMiddleware,
        config=spec_config,
        metrics=metrics,
        draft_store=draft_store,
    )

    # 2. Register TurnHook with Headroom
    try:
        from headroom.proxy.turn_hooks import register_turn_hook

        hook = ThetaRelayTurnHook(spec_config, metrics, draft_store)
        register_turn_hook(hook)
        logger.info(
            "ThetaRelay TurnHook registered with Headroom (local_model=%s, url=%s)",
            spec_config.local_model,
            spec_config.local_base_url,
        )
    except ImportError:
        logger.warning("headroom.proxy.turn_hooks not found; skipping TurnHook registration")

    # 3. Add status endpoint `/thetarelay/stats` to Headroom's FastAPI app
    async def thetarelay_stats() -> dict[str, Any]:
        return {
            "extension": "thetarelay",
            "config": {
                "local_base_url": spec_config.local_base_url,
                "local_model": spec_config.local_model,
                "strategy": spec_config.strategy,
                "accept_token": spec_config.accept_token,
            },
            "metrics": metrics.to_dict(),
        }

    app.get("/thetarelay/stats")(thetarelay_stats)
