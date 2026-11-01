"""Tests for OpenAI SSE streaming, proxy edge cases, bypass headers, and failure recovery."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from thetarelay.config import SpeculativeConfig
from thetarelay.engine import DraftResult, SpeculativeMetrics
from thetarelay.proxy import create_app
from thetarelay.streaming import (
    intercept_openai_sse_stream,
    synthesize_openai_sse_from_draft,
)


def test_synthesize_openai_sse_from_draft() -> None:
    draft = DraftResult(
        text="def add(a, b): return a + b",
        tool_calls=[
            {"id": "call_1", "name": "run_test", "input": {"cmd": "pytest"}},
            {"id": "call_2", "name": "notify", "input": {"status": "ok"}},
        ],
    )
    events = synthesize_openai_sse_from_draft(draft, model="gpt-4o")
    assert len(events) == 5  # text chunk, tool 1 chunk, tool 2 chunk, stop chunk + [DONE]
    assert events[-1] == b"data: [DONE]\n\n"

    # Verify text chunk
    c1 = json.loads(events[0].decode("utf-8")[len("data: ") :].strip())
    assert c1["choices"][0]["delta"]["content"] == "def add(a, b): return a + b"
    assert c1["choices"][0]["delta"]["role"] == "assistant"

    # Verify tool call chunks
    c2 = json.loads(events[1].decode("utf-8")[len("data: ") :].strip())
    tc1 = c2["choices"][0]["delta"]["tool_calls"][0]
    assert tc1["function"]["name"] == "run_test"
    assert json.loads(tc1["function"]["arguments"]) == {"cmd": "pytest"}

    # Verify stop chunk
    c_stop = json.loads(events[3].decode("utf-8")[len("data: ") :].strip())
    assert c_stop["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
async def test_intercept_openai_sse_stream_accept_swap() -> None:
    cfg = SpeculativeConfig()
    metrics = SpeculativeMetrics()
    draft = DraftResult(
        text="print('hello')",
        tool_calls=[{"id": "call_9", "name": "log", "input": {"msg": "done"}}],
        draft_tokens_est=50,
    )

    async def mock_accept_stream() -> AsyncIterator[bytes]:
        yield b'data: {"choices":[{"delta":{"role":"assistant","content":"<ACC"}}]}\n\n'
        yield b'data: {"choices":[{"delta":{"content":"EPT/>"}}]}\n\n'
        yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        yield b"data: [DONE]\n\n"

    chunks = [
        c
        async for c in intercept_openai_sse_stream(
            mock_accept_stream(), draft=draft, config=cfg, metrics=metrics, model="gpt-4o"
        )
    ]
    combined = b"".join(chunks).decode("utf-8")
    assert "print('hello')" in combined
    assert '"name": "log"' in combined
    assert "<ACCEPT/>" not in combined
    assert metrics.frontier_accepted_count == 1
    assert metrics.estimated_output_tokens_saved == 48


@pytest.mark.asyncio
async def test_intercept_openai_sse_stream_divergence_passthrough() -> None:
    cfg = SpeculativeConfig()
    metrics = SpeculativeMetrics()
    draft = DraftResult(text="print('hello')", draft_tokens_est=50)

    async def mock_diverge_stream() -> AsyncIterator[bytes]:
        yield b'data: {"choices":[{"delta":{"role":"assistant","content":"Let me "}}]}\n\n'
        yield b'data: {"choices":[{"delta":{"content":"help you with that."}}]}\n\n'
        yield b"data: [DONE]\n\n"

    chunks = [
        c
        async for c in intercept_openai_sse_stream(
            mock_diverge_stream(), draft=draft, config=cfg, metrics=metrics, model="gpt-4o"
        )
    ]
    combined = b"".join(chunks).decode("utf-8")
    assert "Let me " in combined
    assert "help you with that." in combined
    assert metrics.frontier_modified_count == 1


@pytest.mark.asyncio
async def test_intercept_openai_sse_stream_tool_calls_divergence() -> None:
    cfg = SpeculativeConfig()
    metrics = SpeculativeMetrics()
    draft = DraftResult(text="print('hello')", draft_tokens_est=50)

    async def mock_tool_stream() -> AsyncIterator[bytes]:
        yield (
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
            b'"function":{"name":"search","arguments":"{}"}}]}}]}\n\n'
        )
        yield b"data: [DONE]\n\n"

    chunks = [
        c
        async for c in intercept_openai_sse_stream(
            mock_tool_stream(), draft=draft, config=cfg, metrics=metrics, model="gpt-4o"
        )
    ]
    combined = b"".join(chunks).decode("utf-8")
    assert "search" in combined
    assert metrics.frontier_modified_count == 1


@pytest.mark.asyncio
async def test_proxy_anthropic_and_openai_streaming_e2e() -> None:
    """Test full proxy streaming endpoints for both Anthropic and OpenAI accept_swap paths."""
    cfg = SpeculativeConfig(
        local_base_url="http://local-drafter.test/v1",
        anthropic_upstream_url="http://frontier-anthropic.test",
        openai_upstream_url="http://frontier-openai.test",
        strategy="accept_swap",
        enable_headroom_middleware=False,
    )
    app = create_app(cfg)

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("http://local-drafter.test/v1/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "drafted streaming response",
                            },
                        }
                    ],
                    "usage": {"completion_tokens": 30},
                },
            )

        if url.startswith("http://frontier-anthropic.test/v1/messages"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b"event: message_start\ndata: "
                    b'{"type": "message_start", "message": {"id": "m1", "role": "assistant"}}\n\n'
                    b"event: content_block_start\ndata: "
                    b'{"type": "content_block_start", "index": 0, '
                    b'"content_block": {"type": "text", "text": ""}}\n\n'
                    b"event: content_block_delta\ndata: "
                    b'{"type": "content_block_delta", "index": 0, '
                    b'"delta": {"type": "text_delta", "text": "<ACCEPT/>"}}\n\n'
                    b"event: content_block_stop\ndata: "
                    b'{"type": "content_block_stop", "index": 0}\n\n'
                    b'event: message_stop\ndata: {"type": "message_stop"}\n\n'
                ),
            )

        if url.startswith("http://frontier-openai.test/v1/chat/completions"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b'data: {"choices":[{"delta":{"role":"assistant","content":"<ACCEPT/>"}}]}\n\n'
                    b"data: [DONE]\n\n"
                ),
            )

        return httpx.Response(404)

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://thetarelay.local"
    ) as client:
        # 1. Anthropic streaming with <ACCEPT/> swap
        resp_anth = await client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-5",
                "messages": [{"role": "user", "content": "Write something long enough to draft."}],
                "stream": True,
            },
        )
        assert resp_anth.status_code == 200
        assert resp_anth.headers["x-thetarelay-drafted"] == "true"
        assert "drafted streaming response" in resp_anth.text
        assert "<ACCEPT/>" not in resp_anth.text

        # 2. OpenAI streaming with accept_swap <ACCEPT/> swap
        resp_oai = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Write something long enough to draft."}],
                "stream": True,
            },
        )
        assert resp_oai.status_code == 200
        assert resp_oai.headers["x-thetarelay-drafted"] == "true"
        assert "drafted streaming response" in resp_oai.text
        assert "<ACCEPT/>" not in resp_oai.text

    await app.state.http_client.aclose()


@pytest.mark.asyncio
async def test_proxy_bypass_headers_and_error_handling() -> None:
    """Verify bypass headers skip drafting, and local drafter errors fall back gracefully."""
    cfg = SpeculativeConfig(
        local_base_url="http://local-drafter.test/v1",
        anthropic_upstream_url="http://frontier-anthropic.test",
        enable_headroom_middleware=False,
    )
    app = create_app(cfg)

    drafter_should_fail = False

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("http://local-drafter.test/v1/chat/completions"):
            if drafter_should_fail:
                return httpx.Response(500, text="Internal Drafter Crash")
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "drafted code"},
                        }
                    ]
                },
            )

        if url.startswith("http://frontier-anthropic.test/v1/messages"):
            return httpx.Response(
                200,
                json={
                    "id": "m_frontier",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "frontier direct answer"}],
                },
            )
        return httpx.Response(404)

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://thetarelay.local"
    ) as client:
        # Case 1: Bypass header `x-thetarelay-bypass: 1`
        resp_bypass = await client.post(
            "/v1/messages",
            headers={"x-thetarelay-bypass": "1"},
            json={
                "model": "claude-sonnet-4-5",
                "messages": [{"role": "user", "content": "Please write a function."}],
            },
        )
        assert resp_bypass.status_code == 200
        assert resp_bypass.headers["x-thetarelay-drafted"] == "false"
        assert resp_bypass.json()["content"][0]["text"] == "frontier direct answer"

        # Case 2: Drafter fails (500 error) -> falls back to frontier smoothly
        drafter_should_fail = True
        resp_fail = await client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-5",
                "messages": [{"role": "user", "content": "Please write a function."}],
            },
        )
        assert resp_fail.status_code == 200
        assert resp_fail.headers["x-thetarelay-drafted"] == "false"
        assert resp_fail.json()["content"][0]["text"] == "frontier direct answer"

    await app.state.http_client.aclose()
