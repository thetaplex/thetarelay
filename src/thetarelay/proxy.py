"""Standalone FastAPI Speculative Proxy Server (with optional embedded Headroom middleware)."""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .config import SpeculativeConfig
from .engine import (
    AdaptiveDraftWindow,
    DraftResult,
    SpeculativeMetrics,
    build_anthropic_response_from_draft,
    build_openai_response_from_draft,
    call_local_drafter,
    extract_text_from_content,
    inject_draft_into_body,
    response_accepted_draft,
)
from .streaming import intercept_anthropic_sse_stream, intercept_openai_sse_stream

logger = logging.getLogger("thetarelay.proxy")

_HOP_BY_HOP_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "accept-encoding",
        "content-encoding",
    }
)


def _clean_outbound_headers(headers: Any) -> dict[str, str]:
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in _HOP_BY_HOP_HEADERS and not k.lower().startswith("x-thetarelay-")
    }


def _clean_inbound_response_headers(headers: Any) -> dict[str, str]:
    return {
        k: v
        for k, v in headers.items()
        if k.lower()
        not in {"content-length", "transfer-encoding", "content-encoding", "connection"}
    }


def create_app(config: SpeculativeConfig | None = None) -> FastAPI:
    """Create the ThetaRelay FastAPI proxy application."""
    cfg = config or SpeculativeConfig.from_env()
    metrics = SpeculativeMetrics()
    window = AdaptiveDraftWindow()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )
        try:
            yield
        finally:
            await app.state.http_client.aclose()

    app = FastAPI(
        title="ThetaRelay Speculative Proxy",
        description="Local-to-Frontier Speculative Drafting Proxy for Coding Agents",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.config = cfg
    app.state.metrics = metrics

    # Embed Headroom's CompressionMiddleware if installed and enabled
    if cfg.enable_headroom_middleware:
        try:
            from headroom.integrations.asgi import CompressionMiddleware

            app.add_middleware(CompressionMiddleware)
            app.state.headroom_middleware_active = True
            logger.info("Embedded Headroom CompressionMiddleware enabled.")
        except ImportError:
            app.state.headroom_middleware_active = False
            logger.info(
                "headroom-ai not found in environment; running without in-process compression."
            )
    else:
        app.state.headroom_middleware_active = False

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "service": "thetarelay-proxy",
            "status": "healthy",
            "headroom_middleware_active": getattr(app.state, "headroom_middleware_active", False),
            "local_model": cfg.local_model,
            "local_base_url": cfg.local_base_url,
            "strategy": cfg.strategy,
        }

    @app.get("/stats")
    @app.get("/thetarelay/stats")
    async def stats() -> dict[str, Any]:
        return {
            "service": "thetarelay-proxy",
            "headroom_middleware_active": getattr(app.state, "headroom_middleware_active", False),
            "config": {
                "local_base_url": cfg.local_base_url,
                "local_model": cfg.local_model,
                "strategy": cfg.strategy,
                "accept_token": cfg.accept_token,
                "anthropic_upstream_url": cfg.anthropic_upstream_url,
                "openai_upstream_url": cfg.openai_upstream_url,
            },
            "metrics": metrics.to_dict(),
        }

    @app.post("/v1/messages")
    async def anthropic_messages(request: Request) -> Response:
        """Handle Anthropic Messages API requests with local speculative drafting."""
        client: httpx.AsyncClient = request.app.state.http_client
        body = await request.json()
        stream = bool(body.get("stream", False))
        inbound_headers = dict(request.headers.items())

        metrics.requests_total += 1
        metrics.drafts_attempted += 1

        draft: DraftResult = await call_local_drafter(
            body,
            provider="anthropic",
            config=cfg,
            http_client=client,
            headers=inbound_headers,
            window=window,
        )

        active_mode = "none"
        if draft.skipped:
            metrics.drafts_skipped += 1
            outbound_body = body
        elif draft.error or not draft.has_draft:
            metrics.drafts_failed += 1
            outbound_body = body
        else:
            metrics.drafts_succeeded += 1
            metrics.local_draft_latency_ms_sum += draft.latency_ms
            metrics.local_draft_latencies_ms.append(draft.latency_ms)
            outbound_body, active_mode = inject_draft_into_body(
                body, provider="anthropic", draft=draft, config=cfg
            )

        base_anth = cfg.anthropic_upstream_url.rstrip("/")
        upstream_url = (
            f"{base_anth}/messages" if base_anth.endswith("/v1") else f"{base_anth}/v1/messages"
        )
        if request.url.query:
            upstream_url = f"{upstream_url}?{request.url.query}"
        outbound_headers = _clean_outbound_headers(request.headers)
        outbound_headers["content-type"] = "application/json"

        spec_headers = {
            "x-thetarelay-drafted": "true" if draft.has_draft else "false",
            "x-thetarelay-mode": active_mode,
            "x-thetarelay-draft-ms": f"{draft.latency_ms:.1f}",
        }

        if stream:
            req = client.build_request(
                "POST",
                upstream_url,
                content=json.dumps(outbound_body).encode("utf-8"),
                headers=outbound_headers,
            )
            upstream_resp = await client.send(req, stream=True)
            resp_headers = {
                **_clean_inbound_response_headers(upstream_resp.headers),
                **spec_headers,
            }

            if (
                upstream_resp.status_code != 200
                or not draft.has_draft
                or active_mode != "accept_swap"
            ):

                async def raw_stream() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in upstream_resp.aiter_bytes():
                            yield chunk
                    finally:
                        await upstream_resp.aclose()

                return StreamingResponse(
                    raw_stream(),
                    status_code=upstream_resp.status_code,
                    media_type="text/event-stream",
                    headers=resp_headers,
                )

            async def intercepted_stream() -> AsyncIterator[bytes]:
                try:
                    async for chunk in intercept_anthropic_sse_stream(
                        upstream_resp.aiter_bytes(),
                        draft=draft,
                        config=cfg,
                        metrics=metrics,
                    ):
                        yield chunk
                finally:
                    await upstream_resp.aclose()

            return StreamingResponse(
                intercepted_stream(),
                status_code=200,
                media_type="text/event-stream",
                headers=resp_headers,
            )

        # Non-streaming path
        frontier_started = time.perf_counter()
        upstream_resp = await client.post(
            upstream_url,
            content=json.dumps(outbound_body).encode("utf-8"),
            headers=outbound_headers,
        )
        frontier_ms = (time.perf_counter() - frontier_started) * 1000.0
        resp_headers = {
            **_clean_inbound_response_headers(upstream_resp.headers),
            **spec_headers,
        }

        if upstream_resp.status_code == 200 and draft.has_draft and active_mode == "accept_swap":
            try:
                resp_json = upstream_resp.json()
                resp_text = extract_text_from_content(resp_json.get("content", []))
                if response_accepted_draft(resp_text, cfg):
                    saved_tok, _, _ = metrics.record_turn_outcome(
                        draft=draft,
                        config=cfg,
                        accepted=True,
                        active_mode=active_mode,
                        frontier_latency_ms=frontier_ms,
                    )
                    resp_headers["x-thetarelay-accepted"] = "true"
                    resp_headers["x-thetarelay-tokens-saved"] = str(saved_tok)
                    swapped = build_anthropic_response_from_draft(resp_json, draft)
                    return JSONResponse(status_code=200, content=swapped, headers=resp_headers)
                else:
                    metrics.record_turn_outcome(
                        draft=draft,
                        config=cfg,
                        accepted=False,
                        active_mode=active_mode,
                        frontier_latency_ms=frontier_ms,
                    )
                    resp_headers["x-thetarelay-accepted"] = "false"
            except Exception as exc:
                logger.warning("Failed to inspect non-streaming Anthropic response: %s", exc)

        return Response(
            content=upstream_resp.content,
            status_code=upstream_resp.status_code,
            headers=resp_headers,
            media_type=upstream_resp.headers.get("content-type", "application/json"),
        )

    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")
    async def openai_chat_completions(request: Request) -> Response:
        """Handle OpenAI Chat Completions requests with local speculative drafting
        (supports both OpenAI `prediction` / Predicted Outputs and `<ACCEPT/>` verification).
        """
        client: httpx.AsyncClient = request.app.state.http_client
        body = await request.json()
        stream = bool(body.get("stream", False))
        model = str(body.get("model", "gpt-4o"))
        inbound_headers = dict(request.headers.items())

        metrics.requests_total += 1
        metrics.drafts_attempted += 1

        draft: DraftResult = await call_local_drafter(
            body,
            provider="openai",
            config=cfg,
            http_client=client,
            headers=inbound_headers,
            window=window,
        )

        active_mode = "none"
        if draft.skipped:
            metrics.drafts_skipped += 1
            outbound_body = body
        elif draft.error or not draft.has_draft:
            metrics.drafts_failed += 1
            outbound_body = body
        else:
            metrics.drafts_succeeded += 1
            metrics.local_draft_latency_ms_sum += draft.latency_ms
            metrics.local_draft_latencies_ms.append(draft.latency_ms)
            outbound_body, active_mode = inject_draft_into_body(
                body, provider="openai", draft=draft, config=cfg
            )
            if active_mode == "openai_prediction":
                metrics.openai_prediction_injected_count += 1
                metrics.prefix_cache_preserved_count += 1

        base_oai = cfg.openai_upstream_url.rstrip("/")
        upstream_url = (
            f"{base_oai}/chat/completions"
            if base_oai.endswith("/v1")
            else f"{base_oai}/v1/chat/completions"
        )
        if request.url.query:
            upstream_url = f"{upstream_url}?{request.url.query}"
        outbound_headers = _clean_outbound_headers(request.headers)
        outbound_headers["content-type"] = "application/json"

        spec_headers = {
            "x-thetarelay-drafted": "true" if draft.has_draft else "false",
            "x-thetarelay-mode": active_mode,
            "x-thetarelay-draft-ms": f"{draft.latency_ms:.1f}",
        }

        if stream:
            req = client.build_request(
                "POST",
                upstream_url,
                content=json.dumps(outbound_body).encode("utf-8"),
                headers=outbound_headers,
            )
            upstream_resp = await client.send(req, stream=True)
            resp_headers = {
                **_clean_inbound_response_headers(upstream_resp.headers),
                **spec_headers,
            }

            if (
                upstream_resp.status_code != 200
                or not draft.has_draft
                or active_mode != "accept_swap"
            ):

                async def raw_stream() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in upstream_resp.aiter_bytes():
                            yield chunk
                    finally:
                        await upstream_resp.aclose()

                return StreamingResponse(
                    raw_stream(),
                    status_code=upstream_resp.status_code,
                    media_type="text/event-stream",
                    headers=resp_headers,
                )

            async def intercepted_stream() -> AsyncIterator[bytes]:
                try:
                    async for chunk in intercept_openai_sse_stream(
                        upstream_resp.aiter_bytes(),
                        draft=draft,
                        config=cfg,
                        metrics=metrics,
                        model=model,
                    ):
                        yield chunk
                finally:
                    await upstream_resp.aclose()

            return StreamingResponse(
                intercepted_stream(),
                status_code=200,
                media_type="text/event-stream",
                headers=resp_headers,
            )

        # Non-streaming path
        frontier_started = time.perf_counter()
        upstream_resp = await client.post(
            upstream_url,
            content=json.dumps(outbound_body).encode("utf-8"),
            headers=outbound_headers,
        )
        frontier_ms = (time.perf_counter() - frontier_started) * 1000.0
        resp_headers = {
            **_clean_inbound_response_headers(upstream_resp.headers),
            **spec_headers,
        }

        if upstream_resp.status_code == 200 and draft.has_draft and active_mode == "accept_swap":
            try:
                resp_json = upstream_resp.json()
                choices = resp_json.get("choices") or []
                if choices:
                    msg = (choices[0] or {}).get("message") or {}
                    resp_text = str(msg.get("content") or "")
                    if response_accepted_draft(resp_text, cfg):
                        saved_tok, _, _ = metrics.record_turn_outcome(
                            draft=draft,
                            config=cfg,
                            accepted=True,
                            active_mode=active_mode,
                            frontier_latency_ms=frontier_ms,
                        )
                        resp_headers["x-thetarelay-accepted"] = "true"
                        resp_headers["x-thetarelay-tokens-saved"] = str(saved_tok)
                        swapped = build_openai_response_from_draft(resp_json, draft)
                        return JSONResponse(status_code=200, content=swapped, headers=resp_headers)
                    else:
                        metrics.record_turn_outcome(
                            draft=draft,
                            config=cfg,
                            accepted=False,
                            active_mode=active_mode,
                            frontier_latency_ms=frontier_ms,
                        )
                        resp_headers["x-thetarelay-accepted"] = "false"
            except Exception as exc:
                logger.warning("Failed to inspect non-streaming OpenAI response: %s", exc)

        return Response(
            content=upstream_resp.content,
            status_code=upstream_resp.status_code,
            headers=resp_headers,
            media_type=upstream_resp.headers.get("content-type", "application/json"),
        )

    return app
