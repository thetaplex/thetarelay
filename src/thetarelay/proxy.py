"""FastAPI routing proxy: each request goes to the lite or the frontier tier, unchanged."""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from . import __version__
from .config import RouterConfig
from .router import Decision, conversation_key, decide, events
from .steps import SEVERITY, Group, classify, signals_tool_call, tool_calls

logger = logging.getLogger("thetarelay.proxy")

_DROP_REQUEST_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "accept-encoding"}
)
_DROP_RESPONSE_HEADERS = frozenset(
    {"content-length", "transfer-encoding", "content-encoding", "connection"}
)
_CREDENTIAL_HEADERS = frozenset({"authorization", "x-api-key"})
_PATHS = {"anthropic": "/v1/messages", "openai": "/v1/chat/completions"}


TRIAGE_NOTE = (
    "[Router note] A smaller, cheaper model ({model}) can take your next "
    "step instead of you. If it can do the next step correctly, reply with exactly <EASY/> and "
    "nothing else. Otherwise ignore this note and continue as you normally would."
)
_EASY = "<EASY/>"


def _with_triage_note(cfg: RouterConfig, body: dict[str, Any], provider: str) -> list[Any]:
    """The messages plus the triage note at the end.

    The note changes on every request, so the cache entry must end before it: a top-level
    marker would land on the note and the next request could not reuse it. Without client
    markers, the last real message gets one (a plain string becomes one text block). OpenAI
    models need it too: without it, GPT-6.1 Terra reused only the system prompt.
    """
    msgs = list(body.get("messages") or [])
    note = TRIAGE_NOTE.format(model=cfg.lite_model)
    if not msgs:
        return [{"role": "user", "content": note}]
    last = msgs[-1]
    blocks = (
        [{"type": "text", "text": last["content"]}]
        if isinstance(last.get("content"), str)
        else list(last.get("content") or [])
    )
    if blocks and b"cache_control" not in json.dumps(body).encode():
        blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        msgs[-1] = last = {**last, "content": blocks}
    if provider == "anthropic" and last.get("role") == "user":
        # Anthropic wants alternating roles: the note is one more block of the last user turn.
        msgs[-1] = {**last, "content": [*blocks, {"type": "text", "text": note}]}
    else:
        msgs.append({"role": "user", "content": note})
    return msgs


def _outbound(
    cfg: RouterConfig,
    body: dict[str, Any],
    headers: dict[str, str],
    tier: str,
    provider: str,
    triage: bool = False,
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Build the upstream URL, body and headers for the chosen tier.

    Frontier requests change only in `model`, so the frontier provider sees the same prefix on
    every request and its prompt cache keeps working. Lite requests also turn thinking off, so
    the lite model answers quickly; that change never reaches the frontier provider.
    """
    out = copy.copy(body)
    hdrs = {k: v for k, v in headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}
    if tier == "lite":
        out["model"] = cfg.lite_model
        if cfg.lite_disable_thinking:
            if provider == "anthropic":
                out["thinking"] = {"type": "disabled"}
            else:
                out["reasoning_effort"] = "none"
        # The client's credentials never go to the lite tier; it gets its own key, if any.
        hdrs = {k: v for k, v in hdrs.items() if k.lower() not in _CREDENTIAL_HEADERS}
        if cfg.lite_api_key:
            hdrs["authorization"] = f"Bearer {cfg.lite_api_key}"
        return cfg.lite_base_url + _PATHS[provider], out, hdrs
    out["model"] = cfg.frontier_model
    if triage:
        out["messages"] = _with_triage_note(cfg, body, provider)
    elif (
        provider == "openai"
        and cfg.frontier_model.startswith("anthropic/")
        and b"cache_control" not in json.dumps(body).encode()
    ):
        # Anthropic models cache only with a cache_control marker. The client cannot add one:
        # it does not know which model the proxy picks. OpenRouter's top-level marker moves the
        # breakpoint to the end of the prompt and leaves messages and system prompt untouched.
        out["cache_control"] = {"type": "ephemeral"}
    if cfg.frontier_api_key:
        hdrs = {k: v for k, v in hdrs.items() if k.lower() not in _CREDENTIAL_HEADERS}
        hdrs["authorization"] = f"Bearer {cfg.frontier_api_key}"
    return cfg.frontier_base_url + _PATHS[provider], out, hdrs


def _guard(cfg: RouterConfig, body: dict[str, Any], raw: bytes) -> tuple[str | None, list[Group]]:
    """Guard reason for a buffered lite reply (None: keep it) and the groups of its tool calls."""
    if cfg.lite_guard == "off":
        return None, []
    try:
        calls = tool_calls(raw)
        groups = [classify(name, args) for name, args in calls]
        if not calls and signals_tool_call(raw):
            return "lite_malformed", ["irreversible"]
        required: dict[str, list[str]] = {}
        for tool in body.get("tools") or []:
            # OpenAI nests the tool under `function`; Anthropic does not.
            spec = tool.get("function", tool)
            schema = spec.get("parameters" if "function" in tool else "input_schema") or {}
            if "name" in spec:  # e.g. OpenAI custom tools have no top-level name
                required[spec["name"]] = schema.get("required") or []
        for name, args in calls:
            parsed = json.loads(args) if name in required else None
            if not isinstance(parsed, dict) or any(k not in parsed for k in required[name]):
                return "lite_malformed", groups
        if "irreversible" in groups:
            return "lite_irreversible", groups
        if cfg.lite_guard == "writes" and "write" in groups:
            return "lite_wrote", groups
        return None, groups
    except Exception:  # an odd reply or request: fail closed
        return "lite_malformed", ["irreversible"]


def _usage(provider: str, data: dict[str, Any]) -> dict[str, Any]:
    u = data.get("usage")
    u = u if isinstance(u, dict) else {}
    if provider == "anthropic":
        return {
            "input_tokens": u.get("input_tokens"),
            "cached_tokens": u.get("cache_read_input_tokens"),
            "cache_write_tokens": u.get("cache_creation_input_tokens"),
            "output_tokens": u.get("output_tokens"),
            "cost_usd": u.get("cost"),
        }
    details = u.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    return {
        "input_tokens": u.get("prompt_tokens"),
        "cached_tokens": details.get("cached_tokens"),
        "cache_write_tokens": details.get("cache_write_tokens"),
        "output_tokens": u.get("completion_tokens"),
        "cost_usd": u.get("cost"),
    }


def _step_groups(status: int, raw: bytes) -> list[str]:
    """Step groups of the tool calls in a reply; `irreversible` if a call cannot be read."""
    if status != 200:
        return []
    try:
        calls = tool_calls(raw)
        if calls:
            return [classify(name, args) for name, args in calls]
        return ["irreversible"] if signals_tool_call(raw) else []
    except Exception:  # a reply we cannot read must not break the proxy
        return ["irreversible"]


def _cut_step_groups(seen: bytes) -> list[str]:
    """Step groups of a stream cut short; a started but unreadable tool call is irreversible."""
    groups = _step_groups(200, seen)
    started = b'"tool_use"' in seen or b'"tool_calls"' in seen
    return groups or (["irreversible"] if started else [])


def _reply_usage(provider: str, raw: bytes) -> dict[str, Any]:
    """Usage of a whole buffered reply, streamed (SSE) or not."""
    try:
        data = json.loads(raw)
    except ValueError:
        stream = StreamUsage()
        stream.feed(raw + b"\n")
        data = {"usage": stream.usage}
    return _usage(provider, data if isinstance(data, dict) else {})


def _text_verdict(text: str) -> bool | None:
    """True: the reply is <EASY/>. False: it is not. None: too short to tell."""
    t = text.lstrip()
    if len(t) >= len(_EASY):
        return t.startswith(_EASY)
    return None if _EASY.startswith(t) else False


def _reply_is_easy(provider: str, data: dict[str, Any]) -> bool:
    if provider == "openai":
        msg = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
        return not msg.get("tool_calls") and bool(_text_verdict(msg.get("content") or ""))
    blocks = [b for b in data.get("content") or [] if b.get("type") in ("text", "tool_use")]
    return bool(blocks) and blocks[0]["type"] == "text" and bool(_text_verdict(blocks[0]["text"]))


class TriageSniffer:
    """Reads the start of a frontier SSE stream until it shows whether the reply is <EASY/>.

    Reasoning and thinking deltas do not count; the first text or tool call decides.
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.text = ""
        self.verdict: bool | None = None
        self._partial = b""

    def feed(self, chunk: bytes) -> None:
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()
        for line in lines:
            if self.verdict is not None:
                return
            if not line.startswith(b"data:"):
                continue
            try:
                obj = json.loads(line[5:])
            except ValueError:
                continue
            if isinstance(obj, dict):
                self._event(obj)

    def _event(self, obj: dict[str, Any]) -> None:
        if self.provider == "openai":
            choice = (obj.get("choices") or [{}])[0] or {}
            delta = choice.get("delta") or {}
            if delta.get("tool_calls"):
                self.verdict = False
                return
            self.text += delta.get("content") or ""
            done = bool(choice.get("finish_reason"))
        else:
            kind = obj.get("type")
            if kind == "content_block_start" and obj["content_block"].get("type") == "tool_use":
                self.verdict = False
                return
            delta = obj.get("delta") or {}
            if kind == "content_block_delta" and delta.get("type") == "text_delta":
                self.text += delta.get("text", "")
            done = kind in ("message_delta", "message_stop")
        verdict = _text_verdict(self.text) if self.text else None
        self.verdict = verdict if verdict is not None or not done else False

    def finish(self) -> None:
        if self.verdict is None:
            self.verdict = bool(_text_verdict(self.text))


class _Replay(httpx.AsyncByteStream):
    """The bytes already read, then the rest of the upstream stream."""

    def __init__(self, head: list[bytes], rest: AsyncIterator[bytes], upstream: httpx.Response):
        self.head, self.rest, self.upstream = head, rest, upstream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.head:
            yield chunk
        async for chunk in self.rest:
            yield chunk

    async def aclose(self) -> None:
        await self.upstream.aclose()


async def _triage(
    upstream: httpx.Response, provider: str, stream: bool
) -> tuple[bool, httpx.Response, dict[str, Any]]:
    """Whether the frontier reply is <EASY/>. If not, a response that replays it whole.

    An <EASY/> reply is read to the end, for its usage (it is only a few tokens).
    """
    if upstream.status_code != 200:
        return False, upstream, {}
    if not stream:
        raw = await upstream.aread()
        await upstream.aclose()
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        if isinstance(data, dict) and _reply_is_easy(provider, data):
            return True, upstream, _usage(provider, data)
        replay = httpx.Response(200, headers=upstream.headers, stream=httpx.ByteStream(raw))
        return False, replay, {}
    sniffer, usage, head = TriageSniffer(provider), StreamUsage(), []
    chunks = upstream.aiter_bytes()
    async for chunk in chunks:
        head.append(chunk)
        sniffer.feed(chunk)
        usage.feed(chunk)
        if sniffer.verdict is False:
            stream_ = _Replay(head, chunks, upstream)
            return False, httpx.Response(200, headers=upstream.headers, stream=stream_), {}
    await upstream.aclose()
    sniffer.finish()
    if sniffer.verdict:
        return True, upstream, _usage(provider, {"usage": usage.usage})
    done = httpx.Response(200, headers=upstream.headers, stream=httpx.ByteStream(b"".join(head)))
    return False, done, {}


class StreamUsage:
    """Collect the `usage` objects of an SSE stream as the bytes pass through.

    OpenAI sends usage in the last chunk; Anthropic sends it in `message_start` (input) and
    `message_delta` (output). Later non-null values win.
    """

    def __init__(self) -> None:
        self.usage: dict[str, Any] = {}
        self._partial = b""

    def feed(self, chunk: bytes) -> None:
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()
        for line in lines:
            if not line.startswith(b"data:") or b'"usage"' not in line:
                continue
            try:
                obj = json.loads(line[5:])
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            message = obj.get("message")
            u = obj.get("usage") or (message.get("usage") if isinstance(message, dict) else None)
            if isinstance(u, dict):
                self.usage.update({k: v for k, v in u.items() if v is not None})


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
            fd = os.open(self.cfg.turn_log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
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
    if cfg.host not in ("127.0.0.1", "localhost", "::1") and (
        cfg.frontier_api_key or cfg.lite_api_key
    ):
        logger.warning(
            "Binding to %s with a configured API key exposes upstream credentials to the network.",
            cfg.host,
        )
    if not cfg.lite_api_key and not cfg.lite_base_url.startswith(
        ("http://127.0.0.1", "http://localhost", "http://[::1]")
    ):
        # The client's key never goes to the lite tier, so a hosted lite model needs its own.
        logger.warning(
            "The lite tier %s is hosted, but THETARELAY_LITE_API_KEY is empty: lite requests "
            "will fail and fall back to the frontier tier.",
            cfg.lite_base_url,
        )
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
                "lite_model": cfg.lite_model,
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
            "cached_tokens": None,
            "cache_write_tokens": None,
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
            lite = url.startswith(cfg.lite_base_url)
            req = client.build_request(
                "POST",
                url + query,
                content=json.dumps(out_body).encode(),
                headers=hdrs,
                # A silent lite model falls back quickly; the frontier keeps the client default.
                timeout=httpx.Timeout(cfg.lite_timeout_seconds, connect=5.0) if lite else None,
            )
            return await client.send(req, stream=record["stream"])

        def _bad_gateway(exc: httpx.TransportError) -> Response:
            record["status"] = 502
            record["step_groups"] = []
            record["upstream_ms"] = (time.perf_counter() - started) * 1000.0
            recorder.record(record)
            return Response(
                content=json.dumps({"error": {"type": "upstream_error", "message": str(exc)}}),
                status_code=502,
                media_type="application/json",
                headers=route_headers,
            )

        triaged: httpx.Response | None = None
        if decision.triage:
            url, out_body, hdrs = _outbound(cfg, body, in_headers, "frontier", provider, True)
            try:
                easy, triaged, triage_usage = await _triage(
                    await send(url, out_body, hdrs), provider, record["stream"]
                )
            except httpx.TransportError as exc:
                return _bad_gateway(exc)
            if triaged.status_code == 200:
                record["triage_verdict"] = "easy" if easy else "did_step"
            if easy:
                # The frontier model hands the step over; the lite tier gets the original body.
                triaged = None
                record["triage_cost_usd"] = triage_usage.get("cost_usd")
                record["triage_cached_tokens"] = triage_usage.get("cached_tokens")
                record["triage_cache_write_tokens"] = triage_usage.get("cache_write_tokens")
                decision.tier = "lite"
                url, out_body, hdrs = _outbound(cfg, body, in_headers, "lite", provider)
                record.update(tier="lite", model=out_body["model"])
                route_headers.update(
                    {"x-thetarelay-tier": "lite", "x-thetarelay-model": out_body["model"]}
                )

        # Forced-lite mode (evaluation baseline) must not fall back to the frontier.
        can_fall_back = decision.tier == "lite" and cfg.route_mode != "lite"
        escalation = ""
        try:
            upstream = triaged or await send(url, out_body, hdrs)
            if can_fall_back and upstream.status_code >= 500:
                escalation = "lite_failed"
                await upstream.aclose()
            elif (
                can_fall_back
                and (cfg.escalate_on_stop or cfg.lite_guard != "off")
                and upstream.status_code == 200
            ):
                # Buffer the lite reply; replay it only if its tool calls pass the guard and it
                # continues the agent loop.
                raw = await upstream.aread()
                await upstream.aclose()
                reason, groups = _guard(cfg, body, raw)
                if reason:
                    escalation = reason
                    record["guarded_step_groups"] = sorted(set(groups), key=SEVERITY.__getitem__)
                elif cfg.escalate_on_stop and not signals_tool_call(raw):
                    escalation = "lite_stopped"
                if escalation:
                    record["lite_output_chars"] = len(raw)
                    record["discarded_lite_cost_usd"] = _reply_usage(provider, raw)["cost_usd"]
                else:
                    upstream = httpx.Response(
                        200, headers=upstream.headers, stream=httpx.ByteStream(raw)
                    )
        except httpx.TransportError as exc:
            if not can_fall_back:
                return _bad_gateway(exc)
            escalation = "lite_failed"
        if escalation:
            # The lite server is down, its reply would end the agent's turn, or the guard
            # discarded it. The frontier request is built from the client's body.
            decision.tier = "frontier"
            record["reason"] = decision.reason = f"{decision.reason}; {escalation}"
            url, out_body, hdrs = _outbound(cfg, body, in_headers, "frontier", provider)
            record.update(tier="frontier", model=out_body["model"])
            route_headers.update(
                {
                    "x-thetarelay-tier": "frontier",
                    "x-thetarelay-model": out_body["model"],
                    "x-thetarelay-reason": decision.reason,
                }
            )
            try:
                upstream = await send(url, out_body, hdrs)
            except httpx.TransportError as exc:
                return _bad_gateway(exc)

        if record["stream"]:

            async def relay() -> AsyncIterator[bytes]:
                usage = StreamUsage()
                seen = bytearray()
                finished = False
                try:
                    async for chunk in upstream.aiter_bytes():
                        usage.feed(chunk)
                        seen += chunk
                        yield chunk
                    finished = True
                except httpx.TransportError as exc:
                    logger.warning("Upstream stream interrupted: %s", exc)
                    record["status"] = 502
                finally:
                    if usage.usage:
                        record.update(_usage(provider, {"usage": usage.usage}))
                    record.setdefault("status", upstream.status_code)
                    if finished:
                        record["step_groups"] = _step_groups(record["status"], bytes(seen))
                    else:
                        # Cut short (upstream error or client gone): the agent may already
                        # hold a tool call, so count what was relayed, whatever the status.
                        record["stream_cut"] = True
                        record["step_groups"] = _cut_step_groups(bytes(seen))
                    record["upstream_ms"] = (time.perf_counter() - started) * 1000.0
                    recorder.record(record)
                    await upstream.aclose()

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
        try:
            await resp.aread()
        except httpx.TransportError as exc:
            await resp.aclose()
            return _bad_gateway(exc)
        record["status"] = resp.status_code
        record["upstream_ms"] = (time.perf_counter() - started) * 1000.0
        record["step_groups"] = _step_groups(resp.status_code, resp.content)
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
