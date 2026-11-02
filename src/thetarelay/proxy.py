"""FastAPI routing proxy: each request goes to the local or the frontier tier, unchanged."""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from . import __version__
from .config import RouterConfig
from .router import Decision, conversation_key, decide, events

logger = logging.getLogger("thetarelay.proxy")

_DROP_REQUEST_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "accept-encoding"}
)
_DROP_RESPONSE_HEADERS = frozenset(
    {"content-length", "transfer-encoding", "content-encoding", "connection"}
)
_CREDENTIAL_HEADERS = frozenset({"authorization", "x-api-key"})
_PATHS = {"anthropic": "/v1/messages", "openai": "/v1/chat/completions"}


def _outbound(
    cfg: RouterConfig, body: dict[str, Any], headers: dict[str, str], tier: str, provider: str
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Build the upstream URL, body and headers for the chosen tier.

    The body changes only in `model` (and, for the local tier, turning thinking off), so the
    frontier provider sees the same prefix on every request and its prompt cache keeps working.
    """
    out = copy.copy(body)
    hdrs = {k: v for k, v in headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}
    if tier == "local":
        out["model"] = cfg.local_model
        if cfg.local_disable_thinking:
            if provider == "anthropic":
                out["thinking"] = {"type": "disabled"}
            else:
                out["reasoning_effort"] = "none"
        # The client's cloud credentials never go to the local server.
        hdrs = {k: v for k, v in hdrs.items() if k.lower() not in _CREDENTIAL_HEADERS}
        return cfg.local_base_url + _PATHS[provider], out, hdrs
    out["model"] = cfg.frontier_model
    if cfg.frontier_api_key:
        hdrs = {k: v for k, v in hdrs.items() if k.lower() not in _CREDENTIAL_HEADERS}
        hdrs["authorization"] = f"Bearer {cfg.frontier_api_key}"
    return cfg.frontier_base_url + _PATHS[provider], out, hdrs


def _usage(provider: str, data: dict[str, Any]) -> dict[str, Any]:
    u = data.get("usage") or {}
    if provider == "anthropic":
        return {
            "input_tokens": u.get("input_tokens"),
            "cached_tokens": u.get("cache_read_input_tokens"),
            "output_tokens": u.get("output_tokens"),
            "cost_usd": u.get("cost"),
        }
    details = u.get("prompt_tokens_details") or {}
    return {
        "input_tokens": u.get("prompt_tokens"),
        "cached_tokens": details.get("cached_tokens") if isinstance(details, dict) else None,
        "output_tokens": u.get("completion_tokens"),
        "cost_usd": u.get("cost"),
    }


class TurnRecorder:
    """Writes the turn log, the debug line and the per-tier counters."""

    def __init__(self, cfg: RouterConfig) -> None:
        self.cfg = cfg
        self.counts: Counter[str] = Counter()

    def record(self, record: dict[str, Any]) -> None:
        self.counts[f"{record['tier']}_requests"] += 1
        if record.get("status", 200) >= 400:
            self.counts[f"{record['tier']}_errors"] += 1
        if self.cfg.debug:
            p = record["p_easy"]
            judge = f"P(easy)={p:.2f}" if p is not None else "no judge"
            print(
                f"[thetarelay] conv {record['conversation']} msg {record['messages']}  "
                f"{judge}  {record['reason']} → {record['tier']} ({record['model']})  "
                f"status {record['status']}  {record['upstream_ms']:.0f} ms",
                file=sys.stderr,
            )
        if not self.cfg.turn_log_path:
            return
        try:
            # ponytail: blocking append per request; fine for one developer, queue it if hot.
            with Path(self.cfg.turn_log_path).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("Could not write turn log: %s", exc)


def create_app(config: RouterConfig | None = None) -> FastAPI:
    """Create the ThetaRelay routing proxy application."""
    cfg = config or RouterConfig.from_env()
    recorder = TurnRecorder(cfg)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=10.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )
        try:
            yield
        finally:
            await app.state.http_client.aclose()

    app = FastAPI(title="ThetaRelay", version=__version__, lifespan=lifespan)
    app.state.config = cfg
    app.state.recorder = recorder
    app.state.headroom_middleware_active = False
    if cfg.enable_headroom_middleware:
        try:
            from headroom.integrations.asgi import CompressionMiddleware

            app.add_middleware(CompressionMiddleware)
            app.state.headroom_middleware_active = True
        except ImportError:
            logger.info("headroom-ai not installed; running without compression.")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"service": "thetarelay", "status": "healthy"}

    @app.get("/stats")
    @app.get("/thetarelay/stats")
    async def stats() -> dict[str, Any]:
        return {
            "service": "thetarelay",
            "headroom_middleware_active": app.state.headroom_middleware_active,
            "config": {
                "local_model": cfg.local_model,
                "frontier_model": cfg.frontier_model,
                "judge_model": cfg.judge_model,
                "easy_threshold": cfg.easy_threshold,
                "stay_requests": cfg.stay_requests,
            },
            "counts": dict(recorder.counts),
        }

    async def route(request: Request, provider: str) -> Response:
        client: httpx.AsyncClient = request.app.state.http_client
        body = await request.json()
        decision: Decision = await decide(cfg, client, body, provider)
        in_headers = dict(request.headers.items())
        url, out_body, hdrs = _outbound(cfg, body, in_headers, decision.tier, provider)
        evs = events(body, provider)
        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "provider": provider,
            "conversation": conversation_key(evs),
            "messages": len(body.get("messages") or []),
            "stream": bool(body.get("stream")),
            "client_model": body.get("model"),
            "tier": decision.tier,
            "model": out_body["model"],
            "reason": decision.reason,
            "p_easy": decision.p_easy,
            "threshold": cfg.easy_threshold,
            "judge_ms": decision.judge_ms,
            "judge_error": decision.judge_error,
            "judge_state": decision.judge_state,
        }
        route_headers = {
            "x-thetarelay-tier": decision.tier,
            "x-thetarelay-model": out_body["model"],
            "x-thetarelay-reason": decision.reason,
        }
        if decision.p_easy is not None:
            route_headers["x-thetarelay-p-easy"] = f"{decision.p_easy:.3f}"
        query = f"?{request.url.query}" if request.url.query else ""
        started = time.perf_counter()

        async def send(url: str, out_body: dict[str, Any], hdrs: dict[str, str]) -> httpx.Response:
            req = client.build_request(
                "POST", url + query, content=json.dumps(out_body).encode(), headers=hdrs
            )
            return await client.send(req, stream=record["stream"])

        try:
            upstream = await send(url, out_body, hdrs)
            local_failed = decision.tier == "local" and upstream.status_code >= 500
            if local_failed:
                await upstream.aclose()
        except httpx.TransportError:
            if decision.tier != "local":
                raise
            local_failed = True
        if local_failed:
            # The local server is down or broken: the agent should not notice.
            decision.tier = "frontier"
            record["reason"] = decision.reason = decision.reason + "; local_failed"
            url, out_body, hdrs = _outbound(cfg, body, in_headers, "frontier", provider)
            record.update(tier="frontier", model=out_body["model"])
            route_headers.update(
                {
                    "x-thetarelay-tier": "frontier",
                    "x-thetarelay-model": out_body["model"],
                    "x-thetarelay-reason": decision.reason,
                }
            )
            upstream = await send(url, out_body, hdrs)

        if record["stream"]:

            async def relay() -> AsyncIterator[bytes]:
                try:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
                finally:
                    await upstream.aclose()
                    record["status"] = upstream.status_code
                    record["upstream_ms"] = (time.perf_counter() - started) * 1000.0
                    recorder.record(record)

            resp_headers = {
                k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_RESPONSE_HEADERS
            }
            return StreamingResponse(
                relay(),
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type", "text/event-stream"),
                headers={**resp_headers, **route_headers},
            )

        resp = upstream
        await resp.aread()
        record["status"] = resp.status_code
        record["upstream_ms"] = (time.perf_counter() - started) * 1000.0
        if resp.status_code == 200:
            with contextlib.suppress(ValueError):
                record.update(_usage(provider, resp.json()))
        recorder.record(record)
        resp_headers = {
            k: v for k, v in resp.headers.items() if k.lower() not in _DROP_RESPONSE_HEADERS
        }
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
            headers={**resp_headers, **route_headers},
        )

    @app.post("/v1/messages")
    async def anthropic_messages(request: Request) -> Response:
        return await route(request, "anthropic")

    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")
    async def openai_chat(request: Request) -> Response:
        return await route(request, "openai")

    return app
