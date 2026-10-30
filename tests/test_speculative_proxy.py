"""Unit and integration tests for ThetaCode speculative proxy & Headroom extension."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from thetacode.config import SpeculativeConfig
from thetacode.engine import (
    DraftResult,
    SpeculativeMetrics,
    convert_to_openai_draft_payload,
    inject_draft_into_body,
    should_speculate,
)
from thetacode.headroom_ext import ThetaCodeStreamingResponseMiddleware, ThetaCodeTurnHook
from thetacode.proxy import create_app
from thetacode.streaming import intercept_anthropic_sse_stream


def test_should_speculate_filters_small_models_and_short_prompts() -> None:
    cfg = SpeculativeConfig(min_prompt_chars_for_draft=20)

    # Small model skipped
    ok, reason = should_speculate(
        {
            "model": "claude-3-5-haiku-latest",
            "messages": [{"role": "user", "content": "Write a full parser"}],
        },
        provider="anthropic",
        config=cfg,
    )
    assert not ok
    assert "skipped_small_model" in reason

    # Short prompt skipped
    ok, reason = should_speculate(
        {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "hi"}]},
        provider="anthropic",
        config=cfg,
    )
    assert not ok
    assert reason == "prompt_too_short"

    # Eligible prompt passes
    ok, reason = should_speculate(
        {
            "model": "claude-sonnet-4-5",
            "messages": [
                {"role": "user", "content": "Refactor the authentication middleware in auth.py"}
            ],
        },
        provider="anthropic",
        config=cfg,
    )
    assert ok
    assert reason == "eligible"


def test_convert_to_openai_draft_payload_preserves_tools_and_trims_context() -> None:
    cfg = SpeculativeConfig(local_model="qwen2.5-coder:7b", local_max_context_chars=500)
    anthropic_body = {
        "model": "claude-sonnet-4-5",
        "system": [{"type": "text", "text": "You are an expert coding assistant."}],
        "messages": [
            {"role": "user", "content": "Read utils.py"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {"path": "utils.py"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "def add(a, b):\n    return a - b\n",
                    },
                    {"type": "text", "text": "Fix the bug in add() using edit_file"},
                ],
            },
        ],
        "tools": [
            {
                "name": "edit_file",
                "description": "Edit a file",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
            {
                "name": "headroom_retrieve",
                "description": "Internal CCR tool",
                "input_schema": {"type": "object"},
            },
        ],
    }

    draft_payload = convert_to_openai_draft_payload(anthropic_body, "anthropic", cfg)
    assert draft_payload["model"] == "qwen2.5-coder:7b"
    assert draft_payload["messages"][0]["role"] == "system"
    # headroom_retrieve is excluded from local draft tools, edit_file is kept
    assert len(draft_payload["tools"]) == 1
    assert draft_payload["tools"][0]["function"]["name"] == "edit_file"


def test_inject_draft_into_body_preserves_prefix_cache_and_supports_prediction() -> None:
    cfg = SpeculativeConfig(strategy="auto")
    draft = DraftResult(text="def add(a, b):\n    return a + b\n", draft_tokens_est=25)

    # 1. Anthropic uses accept_swap on the final user message without touching earlier messages
    anth_body = {
        "model": "claude-sonnet-4-5",
        "system": "System prompt",
        "messages": [
            {"role": "user", "content": "First user turn"},
            {"role": "assistant", "content": "First assistant turn"},
            {"role": "user", "content": "Fix the add function in utils.py"},
        ],
    }
    updated_anth, mode_anth = inject_draft_into_body(anth_body, "anthropic", draft, cfg)
    assert mode_anth == "accept_swap"
    assert updated_anth["system"] == "System prompt"
    assert updated_anth["messages"][0] == anth_body["messages"][0]
    assert "<speculative_draft>" in updated_anth["messages"][2]["content"]
    assert "<ACCEPT/>" in updated_anth["messages"][2]["content"]

    # 2. OpenAI uses native `prediction` parameter when drafting text
    oai_body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Fix the add function in utils.py"}],
    }
    updated_oai, mode_oai = inject_draft_into_body(oai_body, "openai", draft, cfg)
    assert mode_oai == "openai_prediction"
    assert updated_oai["prediction"] == {"type": "content", "content": draft.text}


@pytest.mark.asyncio
async def test_streaming_anthropic_accept_swap_and_passthrough() -> None:
    cfg = SpeculativeConfig()
    metrics = SpeculativeMetrics()
    draft = DraftResult(
        text="Here is the fixed code.",
        tool_calls=[{"id": "toolu_draft_1", "name": "edit_file", "input": {"path": "utils.py"}}],
        draft_tokens_est=120,
    )

    # Case 1: Frontier model outputs `<ACCEPT/>` -> stream is replaced with draft text + tool_use
    async def mock_accept_stream() -> AsyncIterator[bytes]:
        yield (
            b"event: message_start\ndata: "
            b'{"type": "message_start", "message": {"id": "msg_1", "role": "assistant", '
            b'"usage": {"input_tokens": 500, "output_tokens": 1}}}\n\n'
        )
        yield (
            b"event: content_block_start\ndata: "
            b'{"type": "content_block_start", "index": 0, '
            b'"content_block": {"type": "text", "text": ""}}\n\n'
        )
        yield (
            b"event: content_block_delta\ndata: "
            b'{"type": "content_block_delta", "index": 0, '
            b'"delta": {"type": "text_delta", "text": "<ACC"}}\n\n'
        )
        yield (
            b"event: content_block_delta\ndata: "
            b'{"type": "content_block_delta", "index": 0, '
            b'"delta": {"type": "text_delta", "text": "EPT/>"}}\n\n'
        )
        yield b'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n'
        yield (
            b"event: message_delta\ndata: "
            b'{"type": "message_delta", "delta": {"stop_reason": "end_turn"}, '
            b'"usage": {"output_tokens": 3}}\n\n'
        )
        yield b'event: message_stop\ndata: {"type": "message_stop"}\n\n'

    chunks = [
        c
        async for c in intercept_anthropic_sse_stream(
            mock_accept_stream(), draft=draft, config=cfg, metrics=metrics
        )
    ]
    combined = b"".join(chunks).decode("utf-8")
    assert "Here is the fixed code." in combined
    assert '"name": "edit_file"' in combined
    assert '"stop_reason": "tool_use"' in combined
    assert "<ACCEPT/>" not in combined
    assert metrics.frontier_accepted_count == 1
    assert metrics.estimated_output_tokens_saved == 118

    # Case 2: Frontier model rejects draft and writes its own response -> immediate passthrough
    async def mock_reject_stream() -> AsyncIterator[bytes]:
        yield (
            b"event: message_start\ndata: "
            b'{"type": "message_start", "message": {"id": "msg_2", "role": "assistant"}}\n\n'
        )
        yield (
            b"event: content_block_start\ndata: "
            b'{"type": "content_block_start", "index": 0, '
            b'"content_block": {"type": "text", "text": ""}}\n\n'
        )
        yield (
            b"event: content_block_delta\ndata: "
            b'{"type": "content_block_delta", "index": 0, '
            b'"delta": {"type": "text_delta", "text": "Actually, let us check tests first."}}\n\n'
        )
        yield b'event: message_stop\ndata: {"type": "message_stop"}\n\n'

    reject_chunks = [
        c
        async for c in intercept_anthropic_sse_stream(
            mock_reject_stream(), draft=draft, config=cfg, metrics=metrics
        )
    ]
    reject_combined = b"".join(reject_chunks).decode("utf-8")
    assert "Actually, let us check tests first." in reject_combined
    assert metrics.frontier_modified_count == 1


@pytest.mark.asyncio
async def test_end_to_end_proxy_with_mock_local_and_frontier() -> None:
    """Test the full FastAPI speculative proxy against mocked local drafter + frontier upstream."""
    cfg = SpeculativeConfig(
        local_base_url="http://local-drafter.test/v1",
        local_model="qwen2.5-coder:7b",
        anthropic_upstream_url="http://frontier-anthropic.test",
        openai_upstream_url="http://frontier-openai.test",
        enable_headroom_middleware=False,
    )
    app = create_app(cfg)

    captured_frontier_requests: list[dict[str, Any]] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = json.loads(request.content.decode("utf-8"))

        # 1. Mock Local Drafter
        if url.startswith("http://local-drafter.test/v1/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-local",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "def multiply(x, y):\n    return x * y\n",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 42},
                },
            )

        # 2. Mock Frontier Anthropic API (returns `<ACCEPT/>` when it sees `<speculative_draft>`)
        if url.startswith("http://frontier-anthropic.test/v1/messages"):
            captured_frontier_requests.append(body)
            return httpx.Response(
                200,
                json={
                    "id": "msg_frontier_1",
                    "type": "message",
                    "role": "assistant",
                    "model": body["model"],
                    "content": [{"type": "text", "text": "<ACCEPT/>"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 120, "output_tokens": 3},
                },
            )

        # 3. Mock Frontier OpenAI API (verifies `prediction` parameter was attached)
        if url.startswith("http://frontier-openai.test/v1/chat/completions"):
            captured_frontier_requests.append(body)
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-frontier-1",
                    "object": "chat.completion",
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": body.get("prediction", {}).get("content", ""),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 42},
                },
            )

        return httpx.Response(404)

    mock_transport = httpx.MockTransport(mock_handler)
    app.state.http_client = httpx.AsyncClient(transport=mock_transport)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://thetacode.local"
    ) as client:
        # Test 1: Anthropic `/v1/messages` -> Local drafts `multiply`, Frontier replies `<ACCEPT/>`,
        # Proxy swaps `<ACCEPT/>` with the full local draft!
        anth_resp = await client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-5",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Write a python function multiply(x, y) that multiplies two numbers."
                        ),
                    }
                ],
                "stream": False,
            },
        )
        assert anth_resp.status_code == 200
        assert anth_resp.headers["x-thetacode-drafted"] == "true"
        assert anth_resp.headers["x-thetacode-accepted"] == "true"
        anth_data = anth_resp.json()
        assert anth_data["content"][0]["text"] == "def multiply(x, y):\n    return x * y\n"
        assert "<speculative_draft>" in captured_frontier_requests[0]["messages"][0]["content"]

        # Test 2: OpenAI `/v1/chat/completions` -> Local drafts `multiply`, injects `prediction`
        oai_resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Write a python function multiply(x, y) that multiplies two numbers."
                        ),
                    }
                ],
                "stream": False,
            },
        )
        assert oai_resp.status_code == 200
        assert oai_resp.headers["x-thetacode-mode"] == "openai_prediction"
        assert captured_frontier_requests[1]["prediction"] == {
            "type": "content",
            "content": "def multiply(x, y):\n    return x * y\n",
        }

        # Check `/thetacode/stats`
        stats_resp = await client.get("/thetacode/stats")
        stats_data = stats_resp.json()["metrics"]
        assert stats_data["requests_total"] == 2
        assert stats_data["drafts_succeeded"] == 2
        assert stats_data["frontier_accepted_count"] == 1
        assert stats_data["openai_prediction_injected_count"] == 1
        assert stats_data["estimated_output_tokens_saved"] == 40

    await app.state.http_client.aclose()


@pytest.mark.asyncio
async def test_headroom_turn_hook_and_streaming_middleware() -> None:
    """Verify native Headroom TurnHook + StreamingResponseMiddleware integration."""
    from dataclasses import dataclass, field

    from fastapi.responses import StreamingResponse

    @dataclass
    class MockTurnContext:
        provider: str
        model: str
        messages: list[dict[str, Any]]
        tools: list[dict[str, Any]] | None = None
        tags: dict[str, Any] = field(default_factory=dict)

    cfg = SpeculativeConfig(
        local_base_url="http://local-drafter.test/v1",
        local_model="qwen2.5-coder:7b",
    )
    metrics = SpeculativeMetrics()
    draft_store: dict[int, DraftResult] = {}

    def mock_local_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-local",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "def divide(a, b):\n    return a / b\n",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 30, "completion_tokens": 35},
            },
        )

    hook = ThetaCodeTurnHook(
        cfg,
        metrics,
        draft_store,
        sync_transport=httpx.MockTransport(mock_local_handler),
    )

    # 1. Test non-streaming turn (`on_request` + `on_response`)
    ctx = MockTurnContext(
        provider="anthropic",
        model="claude-sonnet-4-5",
        messages=[
            {
                "role": "user",
                "content": "Write a python function divide(a, b) that divides two numbers.",
            }
        ],
    )
    hook.on_request(ctx)
    assert "<speculative_draft>" in ctx.messages[0]["content"]

    frontier_non_stream_resp = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5",
        "content": [{"type": "text", "text": "<ACCEPT/>"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 90, "output_tokens": 3},
    }
    swapped_resp = await hook.on_response(ctx, frontier_non_stream_resp, call_model=None)
    assert swapped_resp is not None
    assert swapped_resp["content"][0]["text"] == "def divide(a, b):\n    return a / b\n"
    assert metrics.frontier_accepted_count == 1

    # 2. Test streaming turn (`on_request` stores draft, ASGI middleware intercepts `<ACCEPT/>`)
    app = FastAPI()
    app.add_middleware(
        ThetaCodeStreamingResponseMiddleware,
        config=cfg,
        metrics=metrics,
        draft_store=draft_store,
    )

    async def mock_headroom_streaming_route(request: Request) -> StreamingResponse:
        ledger = request.scope.get("state", {}).get("headroom_savings_attribution", [])
        stream_ctx = MockTurnContext(
            provider="anthropic",
            model="claude-sonnet-4-5",
            messages=[
                {
                    "role": "user",
                    "content": "Write a python function divide(a, b) that divides two numbers.",
                }
            ],
            tags={"_headroom_savings_attribution": ledger},
        )
        hook.on_request(stream_ctx)

        async def _sse_gen() -> AsyncIterator[bytes]:
            for chunk in [
                (
                    b"event: message_start\ndata: "
                    b'{"type":"message_start","message":{"id":"msg_s1","model":"claude-sonnet-4-5"}}\n\n'
                ),
                (
                    b"event: content_block_start\ndata: "
                    b'{"type":"content_block_start","index":0,'
                    b'"content_block":{"type":"text","text":""}}\n\n'
                ),
                (
                    b"event: content_block_delta\ndata: "
                    b'{"type":"content_block_delta","index":0,'
                    b'"delta":{"type":"text_delta","text":"<ACCEPT/>"}}\n\n'
                ),
                b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
                (
                    b"event: message_delta\ndata: "
                    b'{"type":"message_delta","delta":{"stop_reason":"end_turn"},'
                    b'"usage":{"output_tokens":3}}\n\n'
                ),
                b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
            ]:
                yield chunk

        return StreamingResponse(_sse_gen(), media_type="text/event-stream")

    app.post("/v1/messages")(mock_headroom_streaming_route)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://headroom.local"
    ) as client:
        stream_resp = await client.post("/v1/messages")
        assert stream_resp.status_code == 200
        text_body = stream_resp.text
        assert "def divide(a, b):" in text_body
        assert "<ACCEPT/>" not in text_body
        assert metrics.frontier_accepted_count == 2
