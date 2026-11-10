"""Routing decisions, the judge summary, and the proxy end to end against mock upstreams."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from thetarelay.config import RouterConfig
from thetarelay.proxy import StreamUsage, _guard, create_app
from thetarelay.router import (
    events,
    requests_since_failure,
    summarize,
)

ANTH_FAIL = {
    "messages": [
        {"role": "user", "content": "<system-reminder>ctx</system-reminder>Fix the date test"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "just test"}}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "FAILED tests/test_dates.py::test_dst",
                }
            ],
        },
    ]
}


def test_summary_and_failure_tracking_for_both_formats() -> None:
    evs = events(ANTH_FAIL, "anthropic")
    state = summarize(evs)
    assert state["goal"] == "Fix the date test"  # harness reminders removed
    assert "result FAILED: FAILED tests/test_dates.py::test_dst" in state["recent_steps"]
    assert state["failed_results"] == 1
    assert requests_since_failure(evs) == 0

    oai = {
        "messages": [
            {"role": "user", "content": "list files"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "function": {"name": "ls", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "a.py b.py"},
        ]
    }
    oai_evs = events(oai, "openai")
    assert [e.kind for e in oai_evs] == ["user", "call", "result"]
    assert requests_since_failure(oai_evs) is None


def _app(
    tmp_path: Path, p_easy: float | None, **overrides: Any
) -> tuple[Any, list[tuple[str, dict[str, Any], dict[str, str]]]]:
    cfg = RouterConfig(
        lite_base_url="http://local.test",
        frontier_base_url="http://frontier.test/api",
        judge_url="http://judge.test/v1/systemone",
        frontier_api_key="sk-frontier",
        enable_headroom_middleware=False,
        **{
            "turn_log_path": str(tmp_path / "turns.jsonl"),
            "escalate_on_stop": False,
            "route_mode": "judge",
            "stay_requests": 6,
            **overrides,
        },
    )
    app = create_app(cfg)
    calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((str(request.url), body, dict(request.headers)))
        if "judge.test" in str(request.url):
            if p_easy is None:
                return httpx.Response(500)
            probs = {"easy": p_easy, "hard": 1 - p_easy}
            return httpx.Response(200, json={"answers": {"route": {"probabilities": probs}}})
        usage = {"input_tokens": 10, "cache_read_input_tokens": 8, "output_tokens": 2, "cost": 0.01}
        return httpx.Response(
            200, json={"content": [{"type": "text", "text": "ok"}], "usage": usage}
        )

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return app, calls


async def _post(app: Any, body: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://relay"
    ) as client:
        return await client.post(
            "/v1/messages", json=body, headers={"x-api-key": "sk-client", "anthropic-version": "1"}
        )


EASY = {"model": "claude-x", "max_tokens": 10, "messages": [{"role": "user", "content": "ls"}]}


@pytest.mark.asyncio
async def test_easy_request_goes_local_without_cloud_credentials(tmp_path: Path) -> None:
    app, calls = _app(tmp_path, 0.9)
    resp = await _post(app, EASY)
    assert resp.headers["x-thetarelay-tier"] == "lite"
    url, body, headers = calls[-1]
    assert url == "http://local.test/v1/messages"
    assert body["model"] == "anthropic/claude-haiku-5.5"
    assert body["thinking"] == {"type": "disabled"}
    assert "x-api-key" not in headers
    assert "authorization" not in headers
    assert body["messages"] == EASY["messages"]  # nothing else changes


@pytest.mark.asyncio
async def test_hard_request_goes_to_frontier_with_its_key(tmp_path: Path) -> None:
    app, calls = _app(tmp_path, 0.2)
    resp = await _post(app, EASY)
    assert resp.headers["x-thetarelay-tier"] == "frontier"
    url, body, headers = calls[-1]
    assert url == "http://frontier.test/api/v1/messages"
    assert body["model"] == "anthropic/claude-opus-5.5"
    assert headers["authorization"] == "Bearer sk-frontier"
    assert "x-api-key" not in headers
    assert {k: v for k, v in body.items() if k != "model"} == {
        k: v for k, v in EASY.items() if k != "model"
    }  # prefix unchanged, so the provider prompt cache keeps working


@pytest.mark.asyncio
async def test_recent_failure_skips_the_judge(tmp_path: Path) -> None:
    app, calls = _app(tmp_path, 0.99)
    resp = await _post(app, {"model": "claude-x", "max_tokens": 10, **ANTH_FAIL})
    assert resp.headers["x-thetarelay-tier"] == "frontier"
    assert resp.headers["x-thetarelay-reason"].startswith("recent_failure")
    assert not any("judge.test" in c[0] for c in calls)


@pytest.mark.asyncio
async def test_judge_error_and_large_requests_go_to_frontier(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, None)
    assert (await _post(app, EASY)).headers["x-thetarelay-reason"] == "judge_error"
    app, calls = _app(tmp_path, 0.99, lite_max_request_chars=10)
    assert (await _post(app, EASY)).headers["x-thetarelay-reason"] == "too_large_for_lite"
    assert not any("judge.test" in c[0] for c in calls)


@pytest.mark.asyncio
async def test_local_server_down_falls_back_to_frontier(tmp_path: Path) -> None:
    app, calls = _app(tmp_path, 0.9)

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), json.loads(request.content), dict(request.headers)))
        if "judge.test" in str(request.url):
            return httpx.Response(
                200, json={"answers": {"route": {"probabilities": {"easy": 0.9}}}}
            )
        if "local.test" in str(request.url):
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"content": []})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resp = await _post(app, EASY)
    assert resp.status_code == 200
    assert resp.headers["x-thetarelay-tier"] == "frontier"
    assert resp.headers["x-thetarelay-reason"] == "judge; lite_failed"


@pytest.mark.asyncio
async def test_turn_log_and_stats_record_each_decision(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, 0.9)
    await _post(app, EASY)
    line = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[0])
    assert line["tier"] == "lite"
    assert line["reason"] == "judge"
    assert line["p_easy"] == 0.9
    assert line["judge_state"]["last_message"] == "[user] ls"
    assert line["cached_tokens"] == 8
    assert line["cost_usd"] == 0.01
    assert "sk-" not in (tmp_path / "turns.jsonl").read_text()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://relay"
    ) as client:
        assert (await client.get("/stats")).json()["counts"] == {"lite_requests": 1}


@pytest.mark.asyncio
async def test_streaming_is_relayed_and_logged(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, 0.9)
    sse = b"event: message_stop\ndata: {}\n\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        if "judge.test" in str(request.url):
            return httpx.Response(
                200, json={"answers": {"route": {"probabilities": {"easy": 0.9}}}}
            )
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resp = await _post(app, {**EASY, "stream": True})
    assert resp.content == sse
    assert json.loads((tmp_path / "turns.jsonl").read_text())["stream"] is True


def test_stream_usage_is_collected_across_chunk_boundaries() -> None:
    oai = (
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":5,'
        b'"prompt_tokens_details":{"cached_tokens":80},"cost":0.002}}\n\ndata: [DONE]\n\n'
    )
    u = StreamUsage()
    for i in range(0, len(oai), 7):  # split lines across chunks
        u.feed(oai[i : i + 7])
    assert u.usage["prompt_tokens"] == 100
    assert u.usage["cost"] == 0.002

    anth = StreamUsage()
    anth.feed(b'data: {"type":"message_start","message":{"usage":{"input_tokens":50,')
    anth.feed(b'"cache_read_input_tokens":40,"output_tokens":1}}}\n\n')
    anth.feed(b'data: {"type":"message_delta","usage":{"output_tokens":9}}\n\n')
    assert anth.usage == {"input_tokens": 50, "cache_read_input_tokens": 40, "output_tokens": 9}


@pytest.mark.asyncio
async def test_streaming_logs_usage_and_cost(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, 0.2)
    sse = (
        b'event: message_start\ndata: {"type":"message_start","message":'
        b'{"usage":{"input_tokens":50,"cache_read_input_tokens":40}}}\n\n'
        b'event: message_delta\ndata: {"type":"message_delta",'
        b'"usage":{"output_tokens":9,"cost":0.003}}\n\n'
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if "judge.test" in str(request.url):
            return httpx.Response(
                200, json={"answers": {"route": {"probabilities": {"easy": 0.2}}}}
            )
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await _post(app, {**EASY, "stream": True})
    line = json.loads((tmp_path / "turns.jsonl").read_text())
    assert (line["input_tokens"], line["cached_tokens"], line["output_tokens"]) == (50, 40, 9)
    assert line["cost_usd"] == 0.003


@pytest.mark.asyncio
async def test_route_modes_for_evaluation_baselines(tmp_path: Path) -> None:
    app, calls = _app(tmp_path, 0.99, route_mode="frontier")
    assert (await _post(app, EASY)).headers["x-thetarelay-tier"] == "frontier"
    assert not any("judge.test" in c[0] for c in calls)

    app, calls = _app(tmp_path, 0.01, route_mode="random", random_lite_share=1.0)
    resp = await _post(app, EASY)
    assert (resp.headers["x-thetarelay-tier"], resp.headers["x-thetarelay-reason"]) == (
        "lite",
        "random",
    )
    assert not any("judge.test" in c[0] for c in calls)
    # Random mode keeps the hard rules: a recent failure still goes to the frontier.
    resp = await _post(app, {"model": "claude-x", "max_tokens": 10, **ANTH_FAIL})
    assert resp.headers["x-thetarelay-tier"] == "frontier"

    app, calls = _app(tmp_path, 0.01, route_mode="lite")

    async def down(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), {}, {}))
        return httpx.Response(503)

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(down))
    resp = await _post(app, EASY)
    assert resp.status_code == 503  # forced lite: no fallback
    assert not any("frontier.test" in c[0] for c in calls)

    with pytest.raises(ValueError, match="route_mode"):
        RouterConfig(route_mode="cheap")


@pytest.mark.asyncio
async def test_local_reply_without_tool_call_is_escalated(tmp_path: Path) -> None:
    app, calls = _app(tmp_path, 0.9, escalate_on_stop=True)
    tool = b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
    text = b'data: {"choices":[{"delta":{"content":"done?"},"finish_reason":"stop"}]}\n\n'
    replies = {"lite": tool}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), {}, {}))
        if "judge.test" in str(request.url):
            return httpx.Response(
                200, json={"answers": {"route": {"probabilities": {"easy": 0.9}}}}
            )
        body = replies["lite"] if "local.test" in str(request.url) else b"data: frontier\n\n"
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resp = await _post(app, {**EASY, "stream": True})
    assert (resp.headers["x-thetarelay-tier"], resp.content) == ("lite", tool)

    replies["lite"] = text
    resp = await _post(app, {**EASY, "stream": True})
    assert resp.headers["x-thetarelay-tier"] == "frontier"
    assert resp.headers["x-thetarelay-reason"] == "judge; lite_stopped"
    assert resp.content == b"data: frontier\n\n"


@pytest.mark.asyncio
async def test_local_finish_after_passing_tests_goes_to_frontier(tmp_path: Path) -> None:
    # The frontier model decides when the work is done, even after green tests: its final
    # review found real bugs (integer overflow) that a lite finish skipped.
    app, _ = _app(tmp_path, 0.9, escalate_on_stop=True)
    text = b'data: {"choices":[{"delta":{"content":"All tests pass."},"finish_reason":"stop"}]}\n\n'

    async def handler(request: httpx.Request) -> httpx.Response:
        if "judge.test" in str(request.url):
            return httpx.Response(
                200, json={"answers": {"route": {"probabilities": {"easy": 0.9}}}}
            )
        body = text if "local.test" in str(request.url) else b"data: frontier\n\n"
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    passed = {
        "messages": [
            {"role": "user", "content": "Fix the date test"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "pytest"}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "17 passed in 0.01s"}
                ],
            },
        ]
    }
    resp = await _post(app, {"model": "claude-x", "max_tokens": 10, "stream": True, **passed})
    assert resp.headers["x-thetarelay-reason"] == "judge; lite_stopped"
    assert resp.content == b"data: frontier\n\n"


@pytest.mark.asyncio
async def test_silent_lite_model_falls_back_with_short_timeout(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, 0.9, lite_timeout_seconds=0.5)
    seen: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if "judge.test" in str(request.url):
            return httpx.Response(
                200, json={"answers": {"route": {"probabilities": {"easy": 0.9}}}}
            )
        if "local.test" in str(request.url):
            seen["lite"] = request.extensions["timeout"]
            raise httpx.ReadTimeout("silent", request=request)
        seen["frontier"] = request.extensions["timeout"]
        return httpx.Response(200, json={"content": []})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resp = await _post(app, EASY)
    assert resp.headers["x-thetarelay-reason"] == "judge; lite_failed"
    assert seen["lite"]["read"] == 0.5
    assert seen["frontier"]["read"] != 0.5  # frontier keeps the client default


@pytest.mark.asyncio
async def test_hosted_lite_model_gets_its_own_key_not_the_clients(tmp_path: Path) -> None:
    app, calls = _app(tmp_path, 0.9, lite_api_key="sk-lite")
    await _post(app, EASY)
    url, _, headers = calls[-1]
    assert url == "http://local.test/v1/messages"
    assert headers["authorization"] == "Bearer sk-lite"
    assert "x-api-key" not in headers  # the client's key never goes to the lite tier


@pytest.mark.asyncio
async def test_openai_requests_to_anthropic_frontier_get_a_cache_marker(tmp_path: Path) -> None:
    oai = {"model": "x", "messages": [{"role": "user", "content": "explain"}]}

    async def post_oai(app: Any, body: dict[str, Any]) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://relay"
        ) as client:
            await client.post("/v1/chat/completions", json=body)

    app, calls = _app(tmp_path, 0.1, frontier_model="anthropic/claude-opus-5.5")
    await post_oai(app, oai)
    body = calls[-1][1]
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["messages"] == oai["messages"]  # the prompt itself is unchanged

    # The client's own markers win; non-Anthropic models need none.
    marked = {
        **oai,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "explain", "cache_control": {"type": "ephemeral"}}
                ],
            }
        ],
    }
    await post_oai(app, marked)
    assert "cache_control" not in calls[-1][1]
    app, calls = _app(tmp_path, 0.1, frontier_model="openai/gpt-6.1-terra")
    await post_oai(app, oai)
    assert "cache_control" not in calls[-1][1]


@pytest.mark.asyncio
async def test_unrecoverable_upstream_transport_error_returns_502(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, 0.1)

    async def down(request: httpx.Request) -> httpx.Response:
        if "judge.test" in str(request.url):
            return httpx.Response(
                200, json={"answers": {"route": {"probabilities": {"easy": 0.1}}}}
            )
        raise httpx.ConnectError("frontier unreachable")

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(down))
    resp = await _post(app, EASY)
    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "upstream_error"
    line = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[-1])
    assert line["status"] == 502


@pytest.mark.asyncio
async def test_turn_log_has_owner_only_permissions_and_non_loopback_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app, _ = _app(tmp_path, 0.9, host="0.0.0.0")  # noqa: S104
    assert "exposes upstream credentials" in caplog.text
    await _post(app, EASY)
    if sys.platform != "win32":  # Windows has no POSIX permission bits
        mode = (tmp_path / "turns.jsonl").stat().st_mode & 0o777
        assert mode == 0o600


# Frontier self-triage: the frontier model either does the step or answers <EASY/>.

TOOL_RUN = {
    "model": "auto",
    "messages": [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Build SPEC.md"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "function": {"name": "bash", "arguments": "ls"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "app tests"},
    ],
}
OAI_EASY = {"choices": [{"message": {"content": "<EASY/>"}}], "usage": {"cost": 0.002}}
OAI_TOOL = {
    "choices": [
        {"message": {"content": None, "tool_calls": [{"id": "c2", "function": {"name": "ls"}}]}}
    ],
    "usage": {"cost": 0.05},
}


async def _post_openai(app: Any, body: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://relay"
    ) as client:
        return await client.post(
            "/v1/chat/completions", json=body, headers={"authorization": "Bearer sk-client"}
        )


def _sse(*chunks: dict[str, Any]) -> bytes:
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + (
        b"data: [DONE]\n\n"
    )


def _triage_app(
    tmp_path: Path,
    frontier_reply: bytes,
    content_type: str = "application/json",
    frontier_model: str = "anthropic/claude-opus-5.5",
) -> tuple[Any, list[tuple[str, dict[str, Any], dict[str, str]]]]:
    app, calls = _app(tmp_path, None, route_mode="triage", frontier_model=frontier_model)

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), json.loads(request.content), dict(request.headers)))
        if "frontier.test" in str(request.url):
            return httpx.Response(
                200, content=frontier_reply, headers={"content-type": content_type}
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "lite"}}]})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return app, calls


@pytest.mark.asyncio
async def test_triage_easy_goes_to_lite_with_the_original_body(tmp_path: Path) -> None:
    app, calls = _triage_app(tmp_path, json.dumps(OAI_EASY).encode())
    resp = await _post_openai(app, TOOL_RUN)
    assert resp.headers["x-thetarelay-tier"] == "lite"
    assert resp.headers["x-thetarelay-reason"] == "triage"
    assert resp.json()["choices"][0]["message"]["content"] == "lite"
    (f_url, f_body, _), (l_url, l_body, l_headers) = calls
    assert "frontier.test" in f_url
    assert "local.test" in l_url
    # The note follows the conversation; the cache marker sits on the last real message.
    assert f_body["messages"][:-2] == TOOL_RUN["messages"][:-1]
    assert f_body["messages"][-2]["content"] == [
        {"type": "text", "text": "app tests", "cache_control": {"type": "ephemeral"}}
    ]
    assert "<EASY/>" in f_body["messages"][-1]["content"]
    assert "cache_control" not in f_body  # no top-level marker: it would land on the note
    assert l_body["messages"] == TOOL_RUN["messages"]  # the lite tier never sees the note
    assert "authorization" not in l_headers
    line = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[0])
    assert line["triage_cost_usd"] == 0.002


@pytest.mark.asyncio
async def test_triage_hard_returns_the_frontier_reply(tmp_path: Path) -> None:
    app, calls = _triage_app(tmp_path, json.dumps(OAI_TOOL).encode())
    resp = await _post_openai(app, TOOL_RUN)
    assert resp.headers["x-thetarelay-tier"] == "frontier"
    assert resp.json() == OAI_TOOL
    assert len(calls) == 1  # one call, no lite request


@pytest.mark.asyncio
async def test_triage_streams_both_ways(tmp_path: Path) -> None:
    easy = _sse(
        {"choices": [{"delta": {"reasoning": "simple"}}]},
        {"choices": [{"delta": {"content": "<EAS"}}]},
        {"choices": [{"delta": {"content": "Y/>"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"cost": 0.003}},
    )
    app, calls = _triage_app(tmp_path, easy, "text/event-stream")
    resp = await _post_openai(app, {**TOOL_RUN, "stream": True})
    assert resp.headers["x-thetarelay-tier"] == "lite"
    assert "<EASY/>" not in resp.text
    hard = _sse(
        {"choices": [{"delta": {"reasoning": "plan"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c2"}]}}]},
    )
    app, calls = _triage_app(tmp_path, hard, "text/event-stream")
    resp = await _post_openai(app, {**TOOL_RUN, "stream": True})
    assert resp.headers["x-thetarelay-tier"] == "frontier"
    assert resp.content == hard  # replayed byte for byte
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_triage_anthropic_format_keeps_client_cache_markers(tmp_path: Path) -> None:
    body: dict[str, Any] = {
        "model": "claude-x",
        "max_tokens": 10,
        "messages": [
            {"role": "user", "content": "Build it"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "ls", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "ok",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
        ],
    }
    reply = {"content": [{"type": "text", "text": "<EASY/>"}], "usage": {"cost": 0.001}}
    app, calls = _triage_app(tmp_path, json.dumps(reply).encode())
    resp = await _post(app, body)
    assert resp.headers["x-thetarelay-tier"] == "lite"
    sent = calls[0][1]["messages"]
    assert sent[:-1] == body["messages"][:-1]
    # The note is one more block in the last user message; the client's marker is untouched.
    assert sent[-1]["content"][:-1] == body["messages"][-1]["content"]
    assert sent[-1]["content"][-1]["type"] == "text"
    assert "<EASY/>" in sent[-1]["content"][-1]["text"]


@pytest.mark.asyncio
async def test_triage_skipped_after_a_failure(tmp_path: Path) -> None:
    app, calls = _triage_app(tmp_path, json.dumps(OAI_TOOL).encode())
    resp = await _post(app, {"model": "claude-x", "max_tokens": 10, **ANTH_FAIL})
    assert resp.headers["x-thetarelay-reason"].startswith("recent_failure")
    assert calls[0][1]["messages"] == ANTH_FAIL["messages"]  # no note


@pytest.mark.asyncio
async def test_hybrid_confident_judge_goes_lite_else_frontier_triages(tmp_path: Path) -> None:
    for p_easy, tier, frontier_calls in ((0.9, "lite", 0), (0.5, "lite", 1)):
        app, calls = _app(tmp_path, p_easy, route_mode="hybrid")

        async def handler(
            request: httpx.Request, calls: list[Any] = calls, p_easy: float = p_easy
        ) -> httpx.Response:
            calls.append((str(request.url), json.loads(request.content), dict(request.headers)))
            if "judge.test" in str(request.url):
                probs = {"easy": p_easy, "hard": 1 - p_easy}
                return httpx.Response(200, json={"answers": {"route": {"probabilities": probs}}})
            if "frontier.test" in str(request.url):
                return httpx.Response(200, json=OAI_EASY)
            return httpx.Response(200, json={"choices": [{"message": {"content": "lite"}}]})

        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        resp = await _post_openai(app, TOOL_RUN)
        assert resp.headers["x-thetarelay-tier"] == tier
        assert sum("frontier.test" in c[0] for c in calls) == frontier_calls
        assert resp.headers["x-thetarelay-reason"] == ("judge" if p_easy > 0.7 else "judge; triage")


def test_defaults_are_the_tested_setup() -> None:
    # The only setup measured to cut cost: Opus 5.5 + Haiku 5.5, hybrid, threshold 0.7, stay 0.
    cfg = RouterConfig()
    assert (cfg.frontier_model, cfg.lite_model) == (
        "anthropic/claude-opus-5.5",
        "anthropic/claude-haiku-5.5",
    )
    assert cfg.lite_base_url == "https://openrouter.ai/api"
    assert (cfg.route_mode, cfg.easy_threshold, cfg.stay_requests) == ("hybrid", 0.7, 0)


def test_hosted_lite_without_a_key_warns(caplog: pytest.LogCaptureFixture) -> None:
    create_app(RouterConfig(enable_headroom_middleware=False))
    assert "THETARELAY_LITE_API_KEY" in caplog.text
    caplog.clear()
    create_app(RouterConfig(enable_headroom_middleware=False, lite_api_key="sk-lite"))
    create_app(
        RouterConfig(enable_headroom_middleware=False, lite_base_url="http://127.0.0.1:11434")
    )
    assert "THETARELAY_LITE_API_KEY" not in caplog.text


@pytest.mark.asyncio
async def test_triage_marks_the_cache_for_openai_models_too(tmp_path: Path) -> None:
    # Measured on GPT-6.1 Terra: without the marker the next request reused only the system
    # prompt; with it, everything before the note.
    app, calls = _triage_app(
        tmp_path, json.dumps(OAI_TOOL).encode(), frontier_model="openai/gpt-6.1-terra"
    )
    await _post_openai(app, TOOL_RUN)
    sent = calls[0][1]
    assert sent["messages"][-2]["content"] == [
        {"type": "text", "text": "app tests", "cache_control": {"type": "ephemeral"}}
    ]
    assert "<EASY/>" in sent["messages"][-1]["content"]


def _anth(calls: list[tuple[str, str, dict[str, Any]]], result_ids: list[str]) -> dict[str, Any]:
    return {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": i, "name": n, "input": a} for i, n, a in calls
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": i, "content": "x"} for i in result_ids
                ],
            },
        ]
    }


def _oai(calls: list[tuple[str, str, dict[str, Any]]], result_ids: list[str]) -> dict[str, Any]:
    return {
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": i,
                        "type": "function",
                        "function": {"name": n, "arguments": json.dumps(a)},
                    }
                    for i, n, a in calls
                ],
            },
            *({"role": "tool", "tool_call_id": i, "content": "x"} for i in result_ids),
        ]
    }


@pytest.mark.parametrize(("build", "provider"), [(_anth, "anthropic"), (_oai, "openai")])
def test_results_linked_to_calls_out_of_order(build: Any, provider: str) -> None:
    calls = [("a", "read", {"path": "f"}), ("b", "bash", {"command": "ls && rm x"})]
    results = [e for e in events(build(calls, ["b", "a"]), provider) if e.kind == "result"]
    assert [(e.group, e.command) for e in results] == [
        ("write", "ls && rm x"),
        ("read", None),
    ]


@pytest.mark.parametrize(("build", "provider"), [(_anth, "anthropic"), (_oai, "openai")])
def test_unlinked_result_is_irreversible(build: Any, provider: str) -> None:
    calls = [("a", "read", {"path": "f"})]
    (res,) = [e for e in events(build(calls, ["zzz"]), provider) if e.kind == "result"]
    assert (res.group, res.command) == ("irreversible", None)
    other = [e for e in events(build(calls, []), provider) if e.kind == "call"]
    assert (other[0].group, other[0].command) == (None, None)


def _result(
    name: str, args: dict[str, Any], content: str, provider: str = "anthropic", **extra: Any
) -> Any:
    """The result event of one tool call and its result."""
    if provider == "openai":
        body = _oai([("c", name, args)], [])
        body["messages"].append({"role": "tool", "tool_call_id": "c", "content": content})
    else:
        body = _anth([("c", name, args)], ["c"])
        body["messages"][1]["content"][0].update(content=content, **extra)
    (res,) = [e for e in events(body, provider) if e.kind == "result"]
    return res


def _sh(command: str, output: str, provider: str = "anthropic") -> Any:
    return _result("bash", {"command": command}, output, provider)


def test_error_flag_is_a_failure_even_for_reads() -> None:
    res = _result("read", {"path": "a.py"}, "no such file", is_error=True)
    assert res.failed
    assert "result FAILED: no such file" in summarize([res])["recent_steps"]


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_pi_exit_status_without_a_keyword_is_a_failure(provider: str) -> None:
    res = _sh("make build", "building\nCommand exited with code 2", provider)
    assert res.failed
    assert "result FAILED: Command exited with code 2" in summarize([res])["recent_steps"]


@pytest.mark.parametrize(
    "output", ["Traceback (most recent call last)\nexit code 0", "Error: x\nexit status 0"]
)
def test_exit_status_zero_is_success(output: str) -> None:
    assert not _sh("make build", output).failed


@pytest.mark.parametrize("line", ["exit code 3", "exit status 1"])
def test_other_exit_status_formats(line: str) -> None:
    res = _sh("make build", f"compiling\n{line}")
    assert res.failed
    assert f"result FAILED: {line}" in summarize([res])["recent_steps"]


def test_passing_test_summary_beats_error_text() -> None:
    out = "test_messages checks Error: bad input\nAssertionError expected\n41 passed in 2.1s"
    assert not _sh("pytest", out).failed


def test_failing_test_summary_is_shown_to_the_judge() -> None:
    res = _sh("pytest", "collected 44 items\n==== 3 failed, 41 passed in 2.1s ====")
    assert res.failed
    assert "result FAILED: 3 failed, 41 passed in 2.1s" in summarize([res])["recent_steps"]


@pytest.mark.parametrize(
    ("output", "failed"),
    [
        ("Ran 4 tests in 0.1s\n\nFAILED (failures=2)", True),
        ("Error: expected\nRan 4 tests in 0.1s\n\nOK", False),
        ("Tests:       1 failed, 4 passed, 5 total", True),
        ("error: expected\nTests:       5 passed, 5 total", False),
        ("test result: FAILED. 3 passed; 1 failed; 0 ignored", True),
        ("panicked at expected\ntest result: ok. 3 passed; 0 failed; 0 ignored", False),
        ("1 error in 0.3s", True),
        ("3 failed, 1 passed\nrerun:\n4 passed in 1.0s", False),  # the last summary wins
    ],
)
def test_test_runner_summaries(output: str, failed: bool) -> None:
    assert _sh("just test", output).failed is failed


def test_run_step_without_structured_signal_falls_back_to_keywords() -> None:
    res = _sh("python app.py", 'Traceback (most recent call last):\n  File "app.py"')
    assert res.failed
    assert "result FAILED: Traceback (most recent call last):" in summarize([res])["recent_steps"]


def test_reading_a_file_that_mentions_errors_is_not_a_failure() -> None:
    src = 'try:\n    run()\nexcept Exception as e:\n    raise ValueError("Error: bad")'
    res = _result("read", {"path": "a.py"}, src)
    assert not res.failed
    assert "result ok" in summarize([res])["recent_steps"]


def test_read_only_shell_command_output_is_not_a_failure() -> None:
    assert not _sh("cat .docs/instructions.md", "# Notes\n## Exception messages\n3 failed").failed


@pytest.mark.parametrize(
    "command",
    [
        "rg foo",
        "grep -r foo src",
        "cat a | egrep foo",
        "fgrep x f",
        "rtk rg foo",
        "rg foo 2>/dev/null",
    ],
)
def test_search_with_no_match_is_not_a_failure(command: str) -> None:
    assert not _sh(command, "Command exited with code 1").failed


@pytest.mark.parametrize(
    ("command", "output"),
    [
        ("cat missing.py", "cat: missing.py: No such file\nCommand exited with code 1"),
        ("rg foo", "Command exited with code 2"),
        ("rg foo && cat x", "Command exited with code 1"),
    ],
)
def test_read_command_that_fails(command: str, output: str) -> None:
    assert _sh(command, output).failed


@pytest.mark.parametrize(("provider", "flag"), [("anthropic", True), ("openai", False)])
def test_search_with_no_match_and_error_flag_is_not_a_failure(provider: str, flag: bool) -> None:
    extra = {"is_error": True} if flag else {}
    assert not _result("bash", {"command": "rg foo"}, "Exit code 1", provider, **extra).failed


def test_search_with_error_flag_and_no_exit_line_is_a_failure() -> None:
    assert _result("bash", {"command": "grep foo a.py"}, "", is_error=True).failed


def test_unlinked_search_exit_one_is_a_failure() -> None:
    body = _anth([], ["zzz"])
    body["messages"][1]["content"][0]["content"] = "Command exited with code 1"
    (res,) = [e for e in events(body, "anthropic") if e.kind == "result"]
    assert res.failed


@pytest.mark.parametrize(("build", "provider"), [(_anth, "anthropic"), (_oai, "openai")])
def test_non_string_ids_are_unlinked(build: Any, provider: str) -> None:
    body = build([(["a"], "bash", {"command": "ls"})], [{"id": "a"}])
    (res,) = [e for e in events(body, provider) if e.kind == "result"]
    assert (res.group, res.command) == ("irreversible", None)


@pytest.mark.parametrize(
    "output",
    [
        "spawned child, exit code 0\nTraceback (most recent call last):\nValueError: x",
        "Traceback (most recent call last):\nRuntimeError: boom\nprocessed 3 passed items",
        "Error: Cannot find module x\n12 passed",
        "Traceback (most recent call last):\nKeyError: 'a'\nOK",
    ],
)
def test_signals_inside_a_line_do_not_mask_a_failure(output: str) -> None:
    assert _sh("python app.py", output).failed


def test_failing_pytest_summary_without_timing_is_a_failure() -> None:
    res = _sh("uv run pytest", "Error: expected\n3 failed, 41 passed")
    assert "result FAILED: 3 failed, 41 passed" in summarize([res])["recent_steps"]


def test_unittest_ok_after_ran_line_passes() -> None:
    assert not _sh(
        "python -m unittest", "Error: expected\nRan 3 tests in 0.0s\n\nOK (skipped=1)"
    ).failed


def test_read_tool_text_about_exit_codes_is_not_a_failure() -> None:
    assert not _result("read", {"path": "README.md"}, "Returns exit code 2 on bad input.").failed
    assert not _result("read", {"path": "a.txt"}, "Command exited with code 2").failed


def _last_line(tmp_path: Path) -> dict[str, Any]:
    line: dict[str, Any] = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[-1])
    return line


@pytest.mark.asyncio
async def test_log_cache_write_tokens_openai(tmp_path: Path) -> None:
    reply = {**OAI_TOOL, "usage": {"prompt_tokens_details": {"cache_write_tokens": 1200}}}
    app, _ = _triage_app(tmp_path, json.dumps(reply).encode())
    await _post_openai(app, TOOL_RUN)
    assert _last_line(tmp_path)["cache_write_tokens"] == 1200


@pytest.mark.asyncio
async def test_log_cache_write_tokens_openai_streamed(tmp_path: Path) -> None:
    hard = _sse(
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c2"}]}}]},
        {"choices": [], "usage": {"prompt_tokens_details": {"cache_write_tokens": 1200}}},
    )
    app, _ = _triage_app(tmp_path, hard, "text/event-stream")
    await _post_openai(app, {**TOOL_RUN, "stream": True})
    assert _last_line(tmp_path)["cache_write_tokens"] == 1200


@pytest.mark.asyncio
async def test_log_cache_write_tokens_null_when_not_reported(tmp_path: Path) -> None:
    app, _ = _triage_app(tmp_path, json.dumps(OAI_TOOL).encode())
    await _post_openai(app, TOOL_RUN)
    assert _last_line(tmp_path)["cache_write_tokens"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_log_cache_write_tokens_anthropic(tmp_path: Path, stream: bool) -> None:
    usage = {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 48000}
    app, _ = _app(tmp_path, 0.0)

    async def handler(request: httpx.Request) -> httpx.Response:
        if "judge.test" in str(request.url):
            return httpx.Response(
                200, json={"answers": {"route": {"probabilities": {"easy": 0.0, "hard": 1.0}}}}
            )
        if stream:
            return httpx.Response(
                200,
                content=_sse({"type": "message_start", "message": {"usage": usage}}),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json={"content": [], "usage": usage})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await _post(app, {**EASY, "stream": stream})
    line = _last_line(tmp_path)
    assert line["cached_tokens"] == 0
    assert line["cache_write_tokens"] == 48000


@pytest.mark.asyncio
async def test_log_triage_verdict_easy(tmp_path: Path) -> None:
    app, _ = _triage_app(tmp_path, json.dumps(OAI_EASY).encode())
    await _post_openai(app, TOOL_RUN)
    assert _last_line(tmp_path)["triage_verdict"] == "easy"


@pytest.mark.asyncio
async def test_log_triage_verdict_did_step(tmp_path: Path) -> None:
    app, _ = _triage_app(tmp_path, json.dumps(OAI_TOOL).encode())
    await _post_openai(app, TOOL_RUN)
    assert _last_line(tmp_path)["triage_verdict"] == "did_step"
    hard = _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c2"}]}}]})
    app, _ = _triage_app(tmp_path, hard, "text/event-stream")
    await _post_openai(app, {**TOOL_RUN, "stream": True})
    assert _last_line(tmp_path)["triage_verdict"] == "did_step"


@pytest.mark.asyncio
async def test_log_no_triage_verdict_when_not_triaged(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, 0.9)
    await _post(app, EASY)
    assert "triage_verdict" not in _last_line(tmp_path)


@pytest.mark.asyncio
async def test_log_triage_cache_usage_on_handed_back_request(tmp_path: Path) -> None:
    usage = {"cost": 0.001, "cache_read_input_tokens": 500, "cache_creation_input_tokens": 700}
    reply = {"content": [{"type": "text", "text": "<EASY/>"}], "usage": usage}
    app, _ = _triage_app(tmp_path, json.dumps(reply).encode())
    await _post(app, {**EASY, "messages": [{"role": "user", "content": "ls"}]})
    line = _last_line(tmp_path)
    assert line["triage_verdict"] == "easy"
    assert line["triage_cached_tokens"] == 500
    assert line["triage_cache_write_tokens"] == 700


@pytest.mark.asyncio
async def test_log_cache_fields_null_on_error_reply(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, 0.0)
    app.state.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: (
                httpx.Response(
                    200, json={"answers": {"route": {"probabilities": {"easy": 0.0, "hard": 1.0}}}}
                )
                if "judge.test" in str(r.url)
                else httpx.Response(429, json={"error": "slow down"})
            )
        )
    )
    await _post(app, EASY)
    line = _last_line(tmp_path)
    assert line["status"] == 429
    assert line["cached_tokens"] is None
    assert line["cache_write_tokens"] is None


@pytest.mark.asyncio
async def test_log_cache_fields_null_on_stream_without_usage(tmp_path: Path) -> None:
    hard = _sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c2"}]}}]})
    app, _ = _triage_app(tmp_path, hard, "text/event-stream")
    await _post_openai(app, {**TOOL_RUN, "stream": True})
    line = _last_line(tmp_path)
    assert line["cached_tokens"] is None
    assert line["cache_write_tokens"] is None


# Step groups of the reply.


def _reply_app(
    tmp_path: Path,
    reply: bytes,
    content_type: str = "application/json",
    status: int = 200,
    p_easy: float | None = 0.9,
    **overrides: Any,
) -> Any:
    app, _ = _app(tmp_path, p_easy, **overrides)

    async def handler(request: httpx.Request) -> httpx.Response:
        if "judge.test" in str(request.url):
            probs = {"easy": p_easy, "hard": 1 - (p_easy or 0)}
            return httpx.Response(200, json={"answers": {"route": {"probabilities": probs}}})
        return httpx.Response(status, content=reply, headers={"content-type": content_type})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return app


def _groups(tmp_path: Path) -> Any:
    return json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[-1])["step_groups"]


def _tool_use(*calls: tuple[str, dict[str, Any]]) -> bytes:
    content = [
        {"type": "tool_use", "id": f"t{i}", "name": n, "input": a} for i, (n, a) in enumerate(calls)
    ]
    return json.dumps({"content": content, "stop_reason": "tool_use"}).encode()


@pytest.mark.asyncio
async def test_step_groups_lite_reply_with_two_calls(tmp_path: Path) -> None:
    reply = _tool_use(("read", {"file_path": "a.py"}), ("edit", {"file_path": "a.py"}))
    resp = await _post(_reply_app(tmp_path, reply), EASY)
    assert resp.content == reply
    assert _groups(tmp_path) == ["read", "write"]


@pytest.mark.asyncio
async def test_step_groups_text_and_error_replies_are_empty(tmp_path: Path) -> None:
    text = json.dumps({"content": [{"type": "text", "text": "hi"}]}).encode()
    await _post(_reply_app(tmp_path, text), EASY)
    assert _groups(tmp_path) == []
    # An error body that mentions a tool call still logs no groups.
    await _post(_reply_app(tmp_path, _tool_use(("bash", {"command": "ls"})), status=429), EASY)
    assert _groups(tmp_path) == []


@pytest.mark.asyncio
async def test_step_groups_unreadable_call_is_irreversible(tmp_path: Path) -> None:
    reply = b'{"stop_reason": "tool_use", "content": []}'
    await _post(_reply_app(tmp_path, reply), EASY)
    assert _groups(tmp_path) == ["irreversible"]


@pytest.mark.asyncio
async def test_step_groups_streamed_frontier_git_push(tmp_path: Path) -> None:
    chunks: list[dict[str, Any]] = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "c", "function": {"name": "bash", "arguments": ""}}
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '{"command": "git push"}'}}
                        ]
                    }
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    sse = _sse(*chunks)
    app = _reply_app(tmp_path, sse, "text/event-stream", p_easy=None)
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://relay"
        ) as client,
        client.stream("POST", "/v1/chat/completions", json={**TOOL_RUN, "stream": True}) as resp,
    ):
        got = [c async for c in resp.aiter_bytes()]
    assert b"".join(got) == sse
    assert _groups(tmp_path) == ["irreversible"]


@pytest.mark.asyncio
async def test_step_groups_follow_the_frontier_reply_after_escalation(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, 0.9, escalate_on_stop=True)
    frontier = _tool_use(("read", {"file_path": "a"}))

    async def handler(request: httpx.Request) -> httpx.Response:
        if "judge.test" in str(request.url):
            return httpx.Response(
                200, json={"answers": {"route": {"probabilities": {"easy": 0.9}}}}
            )
        if "local.test" in str(request.url):
            return httpx.Response(200, json={"content": [{"type": "text", "text": "done"}]})
        return httpx.Response(200, content=frontier)

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resp = await _post(app, EASY)
    assert resp.content == frontier
    assert _groups(tmp_path) == ["read"]


@pytest.mark.asyncio
async def test_turn_log_never_holds_the_client_key(tmp_path: Path) -> None:
    await _post(_reply_app(tmp_path, _tool_use(("bash", {"command": "ls"}))), EASY)
    assert "sk-client" not in (tmp_path / "turns.jsonl").read_text()


def _push_chunks() -> list[bytes]:
    first = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {"index": 0, "id": "c", "function": {"name": "bash", "arguments": ""}}
                    ]
                }
            }
        ]
    }
    second = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {"index": 0, "function": {"arguments": '{"command": "git push"}'}}
                    ]
                }
            }
        ]
    }
    return [_sse(first)[: -len(b"data: [DONE]\n\n")], _sse(second)[: -len(b"data: [DONE]\n\n")]]


@pytest.mark.asyncio
async def test_cut_stream_logs_irreversible_and_stream_cut(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, None)

    async def cut() -> Any:
        for c in _push_chunks():
            yield c
        raise httpx.ReadError("boom")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=cut(), headers={"content-type": "text/event-stream"})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await _post_openai(app, {**TOOL_RUN, "stream": True})
    line = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[-1])
    assert line["status"] == 502
    assert line["step_groups"] == ["irreversible"]
    assert line["stream_cut"] is True


@pytest.mark.asyncio
async def test_cut_stream_with_unreadable_call_is_irreversible(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, None)

    async def cut() -> Any:
        yield _push_chunks()[0]
        raise httpx.ReadError("boom")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=cut(), headers={"content-type": "text/event-stream"})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await _post_openai(app, {**TOOL_RUN, "stream": True})
    line = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[-1])
    assert line["step_groups"] == ["irreversible"]


@pytest.mark.asyncio
async def test_cut_stream_before_any_tool_call_has_no_step_groups(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, None)
    text = {"choices": [{"delta": {"content": "Let me look"}}]}

    async def cut() -> Any:
        yield _sse(text)[: -len(b"data: [DONE]\n\n")]
        raise httpx.ReadError("boom")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=cut(), headers={"content-type": "text/event-stream"})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await _post_openai(app, {**TOOL_RUN, "stream": True})
    line = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[-1])
    assert (line["stream_cut"], line["step_groups"]) == (True, [])


@pytest.mark.asyncio
async def test_normal_stream_has_no_stream_cut(tmp_path: Path) -> None:
    sse = _sse({"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]})
    await _post(_reply_app(tmp_path, sse, "text/event-stream"), {**EASY, "stream": True})
    line = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[-1])
    assert "stream_cut" not in line


NULL_NAME = json.dumps(
    {
        "content": [{"type": "tool_use", "id": "t", "name": None, "input": {}}],
        "stop_reason": "tool_use",
    }
).encode()


@pytest.mark.asyncio
async def test_malformed_tool_call_does_not_break_the_reply(tmp_path: Path) -> None:
    resp = await _post(_reply_app(tmp_path, NULL_NAME), EASY)
    assert resp.status_code == 200
    assert resp.content == NULL_NAME
    assert len((tmp_path / "turns.jsonl").read_text().splitlines()) == 1
    assert _groups(tmp_path) == ["irreversible"]


@pytest.mark.asyncio
async def test_malformed_tool_call_in_a_stream_is_still_logged(tmp_path: Path) -> None:
    sse = b"data: " + NULL_NAME + b"\n\n"
    resp = await _post(_reply_app(tmp_path, sse, "text/event-stream"), {**EASY, "stream": True})
    assert resp.status_code == 200
    assert resp.content == sse
    assert len((tmp_path / "turns.jsonl").read_text().splitlines()) == 1
    assert _groups(tmp_path) == ["irreversible"]


@pytest.mark.asyncio
async def test_triage_error_reply_has_no_verdict(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, None, route_mode="triage")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "slow down"})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await _post_openai(app, TOOL_RUN)
    line = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[-1])
    assert "triage_verdict" not in line


def test_lite_guard_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THETARELAY_LITE_GUARD", raising=False)
    assert RouterConfig().lite_guard == "off"
    assert RouterConfig.from_env().lite_guard == "off"
    monkeypatch.setenv("THETARELAY_LITE_GUARD", "writes")
    assert RouterConfig.from_env().lite_guard == "writes"


def test_lite_guard_invalid_value_names_levels() -> None:
    with pytest.raises(ValueError, match="off, irreversible or writes"):
        RouterConfig(lite_guard="strict")


# Lite action guard (openspec lite-action-guard).
GUARD_TOOLS = {"read": ["path"], "bash": ["command"], "edit": ["path"], "write": ["path"]}
FRONTIER = b"data: frontier\n\n"


def _guard_request(provider: str, stream: bool, tools: dict[str, list[str]] | None = None) -> Any:
    tools = GUARD_TOOLS if tools is None else tools
    if provider == "anthropic":
        specs: list[Any] = [
            {"name": n, "input_schema": {"type": "object", "required": r}} for n, r in tools.items()
        ]
    else:
        specs = [
            {"type": "function", "function": {"name": n, "parameters": {"required": r}}}
            for n, r in tools.items()
        ]
    msgs = [{"role": "user", "content": "go"}]
    return {
        "model": "claude-x",
        "max_tokens": 10,
        "stream": stream,
        "messages": msgs,
        "tools": specs,
    }


def _lite_reply(provider: str, stream: bool, *calls: tuple[str, str]) -> bytes:
    """A lite reply calling each (tool name, JSON arguments), in the provider's format."""
    if provider == "anthropic" and not stream:
        blocks = [
            {"type": "tool_use", "id": f"t{i}", "name": n, "input": json.loads(a)}
            for i, (n, a) in enumerate(calls)
        ]
        return json.dumps({"content": blocks, "stop_reason": "tool_use"}).encode()
    if provider == "anthropic":
        events: list[dict[str, Any]] = []
        for i, (n, a) in enumerate(calls):
            block = {"type": "tool_use", "id": f"t{i}", "name": n, "input": {}}
            delta = {"type": "input_json_delta", "partial_json": a}
            events += [
                {"type": "content_block_start", "index": i, "content_block": block},
                {"type": "content_block_delta", "index": i, "delta": delta},
            ]
        return _sse(*events, {"type": "message_delta", "delta": {"stop_reason": "tool_use"}})
    tcs = [
        {"index": i, "id": f"c{i}", "type": "function", "function": {"name": n, "arguments": a}}
        for i, (n, a) in enumerate(calls)
    ]
    if not stream:
        msg = {"role": "assistant", "tool_calls": tcs}
        return json.dumps({"choices": [{"message": msg, "finish_reason": "tool_calls"}]}).encode()
    return _sse({"choices": [{"delta": {"tool_calls": tcs}, "finish_reason": "tool_calls"}]})


def _guard_app(
    tmp_path: Path, lite: bytes, **overrides: Any
) -> tuple[Any, list[tuple[str, dict[str, Any], dict[str, str]]]]:
    # A non-Anthropic frontier model: the frontier body then differs from the client's only
    # in `model`, in both formats.
    app, calls = _app(tmp_path, 0.9, frontier_model="openai/gpt-6.1-terra", **overrides)

    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append((url, json.loads(request.content), dict(request.headers)))
        if "judge.test" in url:
            return httpx.Response(
                200, json={"answers": {"route": {"probabilities": {"easy": 0.9}}}}
            )
        content = lite if "local.test" in url else FRONTIER
        return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return app, calls


async def _post_as(provider: str, app: Any, body: dict[str, Any]) -> httpx.Response:
    return await (_post if provider == "anthropic" else _post_openai)(app, body)


PUSH = ("bash", '{"command": "git push origin main"}')
EDIT = ("edit", '{"path": "a.py", "old": "a", "new": "b"}')
GUARD_CASES = [
    # level, lite tool calls, reason suffix (None: the agent gets the lite reply), step groups
    ("irreversible", [PUSH], "lite_irreversible", ["irreversible"]),
    ("writes", [PUSH], "lite_irreversible", ["irreversible"]),
    ("irreversible", [("bash", '"git push"')], "lite_malformed", ["irreversible"]),
    ("irreversible", [("run_tests", "{}")], "lite_malformed", ["irreversible"]),
    ("irreversible", [("edit", '{"old": "a", "new": "b"}')], "lite_malformed", ["write"]),
    ("irreversible", [PUSH, ("run_tests", "{}")], "lite_malformed", ["irreversible"]),
    ("writes", [EDIT, PUSH], "lite_irreversible", ["write", "irreversible"]),
    ("writes", [EDIT], "lite_wrote", ["write"]),
    ("irreversible", [EDIT], None, None),
    ("writes", [("bash", '{"command": "uv run pytest -q"}')], None, None),
    ("writes", [("read", '{"path": "a.py"}'), ("bash", '{"command": "rg foo"}')], None, None),
]


def _with_cost(provider: str, stream: bool, reply: bytes, cost: float) -> bytes:
    """The reply with a usage report carrying OpenRouter's `cost`, where the provider puts it."""
    usage = {"cost": cost}
    if stream:
        event = (
            {"type": "message_delta", "usage": usage}
            if provider == "anthropic"
            else {
                "choices": [],
                "usage": usage,
            }
        )
        return reply + _sse(event)
    return json.dumps({**json.loads(reply), "usage": usage}).encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("overrides", "calls", "reason"),
    [
        ({"lite_guard": "irreversible"}, [PUSH], "lite_irreversible"),
        ({"escalate_on_stop": True}, [], "lite_stopped"),
    ],
)
async def test_discarded_lite_reply_cost_is_logged(
    tmp_path: Path,
    provider: str,
    stream: bool,
    overrides: dict[str, Any],
    calls: list[tuple[str, str]],
    reason: str,
) -> None:
    lite = _lite_reply(provider, stream, *calls)
    if not calls:  # a text-only reply that would end the agent's turn
        lite = (
            _sse({"choices": [{"delta": {"content": "done"}}]})
            if stream and provider == "openai"
            else _sse({"type": "content_block_delta", "delta": {"text": "done"}})
            if stream
            else json.dumps(
                {"content": [{"type": "text", "text": "done"}]}
                if provider == "anthropic"
                else {"choices": [{"message": {"content": "done"}}]}
            ).encode()
        )
    app, _ = _guard_app(tmp_path, _with_cost(provider, stream, lite, 0.012), **overrides)
    await _post_as(provider, app, _guard_request(provider, stream))
    line = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[-1])
    assert line["reason"] == f"judge; {reason}"
    assert line["discarded_lite_cost_usd"] == 0.012


@pytest.mark.asyncio
async def test_kept_lite_reply_has_no_discarded_cost(tmp_path: Path) -> None:
    lite = _with_cost("anthropic", False, _lite_reply("anthropic", False, EDIT), 0.012)
    app, _ = _guard_app(tmp_path, lite, lite_guard="irreversible")
    await _post_as("anthropic", app, _guard_request("anthropic", False))
    line = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[-1])
    assert line["tier"] == "lite"
    assert "discarded_lite_cost_usd" not in line


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(("level", "calls", "reason", "groups"), GUARD_CASES)
async def test_guard_discards_risky_or_malformed_lite_replies(
    tmp_path: Path,
    provider: str,
    stream: bool,
    level: str,
    calls: list[tuple[str, str]],
    reason: str | None,
    groups: list[str] | None,
) -> None:
    lite = _lite_reply(provider, stream, *calls)
    app, sent = _guard_app(tmp_path, lite, lite_guard=level)
    body = _guard_request(provider, stream)
    resp = await _post_as(provider, app, body)
    line = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[-1])
    if reason is None:
        assert (resp.headers["x-thetarelay-tier"], resp.content) == ("lite", lite)
        assert not any("frontier.test" in url for url, _, _ in sent)
        assert "guarded_step_groups" not in line
        return
    assert resp.headers["x-thetarelay-tier"] == "frontier"
    assert resp.headers["x-thetarelay-reason"] == f"judge; {reason}"
    assert resp.content == FRONTIER  # no part of the lite reply reaches the agent
    frontier_body = sent[-1][1]
    assert "frontier.test" in sent[-1][0]
    assert {**frontier_body, "model": body["model"]} == body  # nothing of the lite reply
    assert line["reason"] == f"judge; {reason}"
    assert line["guarded_step_groups"] == groups
    assert line["lite_output_chars"] == len(lite)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "openai"])
async def test_guard_malformed_replies_the_classifier_cannot_see(
    tmp_path: Path, provider: str
) -> None:
    # A tool call is signalled but none can be read.
    signal_only = (
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        if provider == "openai"
        else _sse({"type": "message_delta", "delta": {"stop_reason": "tool_use"}})
    )
    # A request without tools makes every call malformed.
    for lite, body in [
        (signal_only, _guard_request(provider, True)),
        (
            _lite_reply(provider, True, ("read", '{"path": "a"}')),
            _guard_request(provider, True, {}),
        ),
        (_lite_reply(provider, True, ("bash", '{"command": ')), _guard_request(provider, True)),
    ]:
        app, _ = _guard_app(tmp_path, lite, lite_guard="irreversible")
        resp = await _post_as(provider, app, body)
        assert resp.headers["x-thetarelay-reason"] == "judge; lite_malformed"
        assert resp.content == FRONTIER


def test_guard_fails_closed_on_odd_requests() -> None:
    raw = _lite_reply("anthropic", False, ("read", '{"path": "a"}'))
    cfg = RouterConfig(lite_guard="irreversible")
    assert _guard(cfg, {"tools": ["read"]}, raw)[0] == "lite_malformed"
    assert _guard(cfg, _guard_request("anthropic", False), raw)[0] is None
    assert _guard(RouterConfig(), {"tools": ["read"]}, raw)[0] is None  # level off


def test_guard_skips_tools_without_a_name() -> None:
    cfg = RouterConfig(lite_guard="irreversible")
    custom = {"type": "custom", "custom": {"name": "apply_patch"}}
    read = {"type": "function", "function": {"name": "read", "parameters": {"required": ["path"]}}}
    body = {"tools": [custom, read]}
    ok = _lite_reply("openai", False, ("read", '{"path": "a.py"}'))
    assert _guard(cfg, body, ok)[0] is None
    bad = _lite_reply("openai", False, ("apply_patch", "{}"))
    assert _guard(cfg, body, bad)[0] == "lite_malformed"  # not among the named tools


@pytest.mark.asyncio
async def test_guard_does_not_run_in_forced_lite_mode(tmp_path: Path) -> None:
    lite = _lite_reply("anthropic", True, EDIT)
    app, sent = _guard_app(tmp_path, lite, lite_guard="writes", route_mode="lite")
    resp = await _post(app, _guard_request("anthropic", True))
    assert (resp.headers["x-thetarelay-tier"], resp.content) == ("lite", lite)
    assert not any("frontier.test" in url for url, _, _ in sent)


@pytest.mark.asyncio
async def test_guard_and_escalate_on_stop_together(tmp_path: Path) -> None:
    text = b'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}\n\n'
    body = _guard_request("openai", True)
    # The guard alone never discards a reply without a tool call.
    app, _ = _guard_app(tmp_path, text, lite_guard="writes")
    resp = await _post_openai(app, body)
    assert (resp.headers["x-thetarelay-tier"], resp.content) == ("lite", text)
    # With both on, a reply without a tool call is still `lite_stopped`.
    app, _ = _guard_app(tmp_path, text, lite_guard="writes", escalate_on_stop=True)
    resp = await _post_openai(app, body)
    assert resp.headers["x-thetarelay-reason"] == "judge; lite_stopped"
    # The guard runs first.
    edit = _lite_reply("openai", True, EDIT)
    app, _ = _guard_app(tmp_path, edit, lite_guard="writes", escalate_on_stop=True)
    resp = await _post_openai(app, body)
    assert resp.headers["x-thetarelay-reason"] == "judge; lite_wrote"
    # A safe tool call passes both checks.
    run = _lite_reply("openai", True, ("bash", '{"command": "pytest"}'))
    app, _ = _guard_app(tmp_path, run, lite_guard="writes", escalate_on_stop=True)
    resp = await _post_openai(app, body)
    assert (resp.headers["x-thetarelay-tier"], resp.content) == ("lite", run)


def _lines(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (tmp_path / "turns.jsonl").read_text().splitlines()]


def test_shadow_share_defaults_off_and_is_validated() -> None:
    assert RouterConfig().shadow_share == 0.0
    with pytest.raises(ValueError, match="shadow_share"):
        RouterConfig(shadow_share=1.5)


@pytest.mark.asyncio
async def test_judge_skipped_without_shadow_share(tmp_path: Path) -> None:
    app, calls = _app(tmp_path, 0.9, route_mode="frontier")
    await _post(app, EASY)
    assert not any("judge.test" in c[0] for c in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "body", "tier", "reason"),
    [
        ({"route_mode": "frontier"}, EASY, "frontier", "forced_frontier"),
        ({"route_mode": "lite"}, EASY, "lite", "forced_lite"),
        ({"route_mode": "random", "random_lite_share": 1.0}, EASY, "lite", "random"),
        ({"route_mode": "triage"}, EASY, "frontier", "triage"),
        ({"lite_max_request_chars": 10}, EASY, "frontier", "too_large_for_lite"),
        (
            {"stay_requests": 6},
            {**EASY, **ANTH_FAIL},
            "frontier",
            "recent_failure (0 requests ago)",
        ),
    ],
)
async def test_judge_asked_in_every_mode_with_shadow_share(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    body: dict[str, Any],
    tier: str,
    reason: str,
) -> None:
    _coin(monkeypatch, 0.99)  # no shadow, so the line is written at once
    app, calls = _app(tmp_path, 0.2, shadow_share=0.1, **overrides)
    resp = await _post(app, body)
    assert (resp.headers["x-thetarelay-tier"], resp.headers["x-thetarelay-reason"]) == (
        tier,
        reason,
    )
    assert any("judge.test" in c[0] for c in calls)
    line = _lines(tmp_path)[0]
    assert line["p_easy"] == pytest.approx(0.2)
    assert line["judge_ms"] is not None


@pytest.mark.asyncio
async def test_judge_error_in_frontier_mode_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.99)
    app, _ = _app(tmp_path, None, route_mode="frontier", shadow_share=0.1)
    resp = await _post(app, EASY)
    assert resp.headers["x-thetarelay-reason"] == "forced_frontier"
    line = _lines(tmp_path)[0]
    assert line["judge_error"]
    assert line["p_easy"] is None


# Shadow requests: a frontier step also sent to the lite tier, logged on the same line.


def _coin(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    monkeypatch.setattr(random, "random", lambda: value)


SRC_READ = _tool_use(("read", {"file_path": "src/app.py"}))
LITE_PATH = "http://local.test"


def _shadow_app(
    tmp_path: Path,
    frontier: bytes | Exception | Callable[[], AsyncIterator[bytes]] = SRC_READ,
    lite: bytes | Exception = SRC_READ,
    gate: asyncio.Event | None = None,
    content_type: str = "application/json",
    lite_status: int = 200,
    p_easy: float = 0.2,
    **overrides: Any,
) -> tuple[Any, list[tuple[str, dict[str, Any], dict[str, str]]]]:
    app, calls = _app(
        tmp_path, p_easy, **{"route_mode": "frontier", "shadow_share": 0.5, **overrides}
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append((url, json.loads(request.content), dict(request.headers)))
        if "judge.test" in url:
            probs = {"easy": p_easy, "hard": 1 - p_easy}
            return httpx.Response(200, json={"answers": {"route": {"probabilities": probs}}})
        if url.startswith(LITE_PATH):
            if gate:
                await gate.wait()
            if isinstance(lite, Exception):
                raise lite
            return httpx.Response(lite_status, content=lite)
        if isinstance(frontier, Exception):
            raise frontier
        content = frontier() if callable(frontier) else frontier
        return httpx.Response(200, content=content, headers={"content-type": content_type})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return app, calls


async def _drain(app: Any) -> None:
    while app.state.shadow_tasks:
        await asyncio.gather(*list(app.state.shadow_tasks), return_exceptions=True)


def _lite_calls(calls: list[tuple[str, dict[str, Any], dict[str, str]]]) -> list[Any]:
    return [c for c in calls if c[0].startswith(LITE_PATH)]


def _sha(args: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(args, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def test_shadow_slow_lite_server_does_not_delay_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.0)
    gate = asyncio.Event()
    app, calls = _shadow_app(tmp_path, gate=gate)
    resp = await asyncio.wait_for(_post(app, EASY), 2.0)  # the lite reply is still held back
    assert resp.status_code == 200
    assert resp.content == SRC_READ
    assert resp.headers["x-thetarelay-tier"] == "frontier"
    assert not (tmp_path / "turns.jsonl").exists()  # the line waits for the shadow
    gate.set()
    await _drain(app)
    assert len(_lite_calls(calls)) == 1
    lines = _lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["shadow"]["match"] is True


async def test_shadow_timeout_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _coin(monkeypatch, 0.0)
    app, _ = _shadow_app(tmp_path, gate=asyncio.Event(), lite_timeout_seconds=0.05)
    resp = await _post(app, EASY)
    assert resp.content == SRC_READ
    await _drain(app)
    assert [line["shadow"] for line in _lines(tmp_path)] == [{"error": "timeout"}]


@pytest.mark.parametrize(
    ("lite", "status", "error"),
    [(httpx.ConnectError("down"), 200, "ConnectError"), (b"oops", 500, "http_500")],
)
async def test_shadow_lite_server_down_is_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lite: bytes | Exception,
    status: int,
    error: str,
) -> None:
    _coin(monkeypatch, 0.0)
    app, _ = _shadow_app(tmp_path, lite=lite, lite_status=status)
    resp = await _post(app, EASY)
    assert resp.status_code == 200
    assert resp.content == SRC_READ
    await _drain(app)
    lines = _lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["shadow"] == {"error": error}
    assert lines[0]["tier"] == "frontier"


async def test_shadow_follows_lite_rules_and_leaves_frontier_body_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.0)
    sse = b"event: message_stop\ndata: {}\n\n"
    app, calls = _shadow_app(
        tmp_path, frontier=sse, content_type="text/event-stream", lite_api_key="sk-lite"
    )
    body = {**EASY, "stream": True, "stream_options": {"include_usage": True}}
    resp = await _post(app, body)
    assert resp.content == sse
    await _drain(app)
    ((_, lite_body, lite_headers),) = _lite_calls(calls)
    # Exactly the lite-tier body: still streamed, as the client asked.
    assert lite_body == {
        **body,
        "model": "anthropic/claude-haiku-5.5",
        "thinking": {"type": "disabled"},
    }
    assert lite_headers["authorization"] == "Bearer sk-lite"
    assert "x-api-key" not in lite_headers
    ((_, frontier_body, _),) = [c for c in calls if "frontier.test" in c[0]]
    assert frontier_body == {**body, "model": "anthropic/claude-opus-5.5"}


async def test_shadow_openai_never_carries_client_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.0)
    app, calls = _shadow_app(tmp_path)
    await _post_openai(app, TOOL_RUN)
    await _drain(app)
    ((_, lite_body, lite_headers),) = _lite_calls(calls)
    assert "authorization" not in lite_headers  # no lite key configured, and not the client's
    assert lite_body["reasoning_effort"] == "none"
    assert lite_body["messages"] == TOOL_RUN["messages"]
    assert "sk-client" not in (tmp_path / "turns.jsonl").read_text()


async def test_shadow_matching_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _coin(monkeypatch, 0.0)
    app, _ = _shadow_app(tmp_path)
    await _post(app, EASY)
    await _drain(app)
    call = {
        "name": "read",
        "group": "read",
        "target": "src/app.py",
        "args_sha256": _sha({"file_path": "src/app.py"}),
    }
    assert _lines(tmp_path)[0]["shadow"] == {"frontier": [call], "lite": [call], "match": True}


async def test_shadow_different_edit_to_the_same_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.0)
    f_args = {"file_path": "src/app.py", "old_string": "a = 1", "new_string": "SECRET_FRONTIER"}
    l_args = {"file_path": "src/app.py", "old_string": "a = 1", "new_string": "SECRET_LITE"}
    app, _ = _shadow_app(
        tmp_path, frontier=_tool_use(("edit", f_args)), lite=_tool_use(("edit", l_args))
    )
    await _post(app, EASY)
    await _drain(app)
    shadow = _lines(tmp_path)[0]["shadow"]
    assert shadow["match"] is True
    assert shadow["frontier"][0]["args_sha256"] == _sha(f_args)
    assert shadow["lite"][0]["args_sha256"] == _sha(l_args)
    assert shadow["frontier"][0]["args_sha256"] != shadow["lite"][0]["args_sha256"]
    text = (tmp_path / "turns.jsonl").read_text()
    assert "SECRET_" not in text  # no argument text beyond target
    assert "a = 1" not in text


@pytest.mark.parametrize(
    ("lite", "match"),
    [
        (_tool_use(("read", {"file_path": "src/other.py"})), False),
        (_tool_use(("bash", {"file_path": "src/app.py"})), False),
        (_tool_use(("read", {"file_path": "src/app.py"}), ("read", {"path": "b"})), False),
        (json.dumps({"content": [{"type": "text", "text": "done"}]}).encode(), False),
        (_tool_use(("read", {"path": "src/app.py"})), True),
    ],
)
async def test_shadow_match_needs_same_names_and_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lite: bytes, match: bool
) -> None:
    _coin(monkeypatch, 0.0)
    app, _ = _shadow_app(tmp_path, lite=lite)
    await _post(app, EASY)
    await _drain(app)
    assert _lines(tmp_path)[0]["shadow"]["match"] is match


async def test_shadow_target_is_the_command_cut_to_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.0)
    cmd = "echo " + "x" * 300
    reply = _tool_use(("bash", {"command": cmd}), ("todo", {"items": ["a"]}))
    app, _ = _shadow_app(tmp_path, frontier=reply, lite=reply)
    await _post(app, EASY)
    await _drain(app)
    calls = _lines(tmp_path)[0]["shadow"]["frontier"]
    assert calls[0]["target"] == cmd[:200]
    assert calls[1]["target"] is None


async def test_shadow_unparsable_arguments_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.0)
    lite = json.dumps(
        {
            "choices": [
                {"message": {"tool_calls": [{"function": {"name": "x", "arguments": "{oops"}}]}}
            ]
        }
    ).encode()
    app, _ = _shadow_app(tmp_path, lite=lite)
    resp = await _post(app, EASY)
    assert resp.content == SRC_READ
    await _drain(app)
    assert _lines(tmp_path)[0]["shadow"] == {"error": "parse"}


ANTH_READ_SSE = (
    b"event: content_block_start\ndata: "
    + json.dumps(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "t", "name": "read", "input": {}},
        }
    ).encode()
    + b"\n\nevent: content_block_delta\ndata: "
    + json.dumps(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"file_path": "src/app.py"}'},
        }
    ).encode()
    + b"\n\nevent: message_stop\ndata: {}\n\n"
)
OAI_READ = {
    "choices": [
        {
            "message": {
                "content": None,
                "tool_calls": [
                    {"id": "c", "function": {"name": "read", "arguments": '{"path": "src/app.py"}'}}
                ],
            }
        }
    ]
}
OAI_READ_SSE = _sse(
    {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {"index": 0, "id": "c", "function": {"name": "read", "arguments": ""}}
                    ]
                }
            }
        ]
    },
    {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {"index": 0, "function": {"arguments": '{"path": "src/app.py"}'}}
                    ]
                }
            }
        ]
    },
    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
)


@pytest.mark.parametrize(
    ("provider", "stream", "frontier"),
    [
        ("anthropic", False, SRC_READ),
        ("anthropic", True, ANTH_READ_SSE),
        ("openai", False, json.dumps(OAI_READ).encode()),
        ("openai", True, OAI_READ_SSE),
    ],
)
async def test_shadow_one_line_per_request_in_both_formats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    stream: bool,
    frontier: bytes,
) -> None:
    _coin(monkeypatch, 0.0)
    ctype = "text/event-stream" if stream else "application/json"
    # The shadow is streamed when the client streams; its SSE reply is read like the agent's.
    lite = frontier
    gate = asyncio.Event()
    app, calls = _shadow_app(tmp_path, frontier=frontier, lite=lite, gate=gate, content_type=ctype)
    post = _post if provider == "anthropic" else _post_openai
    resp = await post(app, {**(EASY if provider == "anthropic" else TOOL_RUN), "stream": stream})
    assert resp.content == frontier
    assert not (tmp_path / "turns.jsonl").exists()
    gate.set()
    await _drain(app)
    lines = _lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["shadow"]["match"] is True
    assert lines[0]["shadow"]["frontier"][0]["target"] == "src/app.py"
    assert lines[0]["step_groups"] == ["read"]
    assert _lite_calls(calls)[0][1]["stream"] is stream


async def test_shadow_with_cut_stream_and_bad_gateway_still_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.0)
    app, _ = _shadow_app(tmp_path, frontier=httpx.ConnectError("frontier down"))
    resp = await _post(app, EASY)
    assert resp.status_code == 502
    await _drain(app)
    (line,) = _lines(tmp_path)
    assert line["status"] == 502
    assert line["shadow"]["frontier"] == []
    assert line["shadow"]["match"] is False

    (tmp_path / "turns.jsonl").unlink()

    async def cut() -> AsyncIterator[bytes]:
        yield _push_chunks()[0]
        raise httpx.ReadError("boom")

    app, _ = _shadow_app(tmp_path, frontier=cut, content_type="text/event-stream")
    await _post_openai(app, {**TOOL_RUN, "stream": True})
    await _drain(app)
    (line,) = _lines(tmp_path)
    assert line["stream_cut"] is True
    assert "shadow" in line


async def test_shadow_cancelled_at_shutdown_still_writes_its_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.0)
    app, _ = _shadow_app(tmp_path, gate=asyncio.Event())
    mock = app.state.http_client
    async with app.router.lifespan_context(app):
        await app.state.http_client.aclose()  # the lifespan's real client, swapped for the mock
        app.state.http_client = mock
        resp = await _post(app, EASY)
        assert resp.status_code == 200
        assert not (tmp_path / "turns.jsonl").exists()
    assert [line["shadow"] for line in _lines(tmp_path)] == [{"error": "shutdown"}]
    assert not app.state.shadow_tasks


@pytest.mark.parametrize("coin", [0.5, 0.99])
async def test_shadow_only_when_the_coin_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, coin: float
) -> None:
    _coin(monkeypatch, coin)
    app, calls = _shadow_app(tmp_path)
    await _post(app, EASY)
    assert not _lite_calls(calls)
    assert "shadow" not in _lines(tmp_path)[0]


async def test_shadow_off_by_default_sends_nothing_to_lite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.0)
    app, calls = _shadow_app(tmp_path, shadow_share=0.0)
    await _post(app, EASY)
    assert not _lite_calls(calls)
    assert "shadow" not in _lines(tmp_path)[0]


async def test_shadow_not_sent_for_triaged_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.0)
    app, calls = _shadow_app(tmp_path, route_mode="triage")
    await _post(app, EASY)
    assert not _lite_calls(calls)
    line = _lines(tmp_path)[0]
    assert line["triage_verdict"] == "did_step"
    assert "shadow" not in line


@pytest.mark.parametrize("lite", [httpx.ConnectError("down"), b'{"content": []}'])
async def test_shadow_not_sent_for_escalated_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lite: bytes | Exception
) -> None:
    _coin(monkeypatch, 0.0)
    app, calls = _shadow_app(
        tmp_path, lite=lite, p_easy=0.9, route_mode="judge", escalate_on_stop=True
    )
    resp = await _post(app, EASY)
    assert resp.headers["x-thetarelay-tier"] == "frontier"
    assert "lite_" in resp.headers["x-thetarelay-reason"]
    assert len(_lite_calls(calls)) == 1  # the lite try itself, no second shadow request
    assert "shadow" not in _lines(tmp_path)[0]


async def test_shadow_skipped_without_a_turn_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.0)
    app, calls = _shadow_app(tmp_path, shadow_share=1.0, turn_log_path="")
    resp = await _post(app, EASY)
    assert resp.content == SRC_READ
    await _drain(app)
    assert not _lite_calls(calls)  # a shadow nobody logs would only cost money


@pytest.mark.parametrize(
    "overrides",
    [{"route_mode": "frontier"}, {"route_mode": "judge"}],
)
async def test_shadow_skipped_for_oversized_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    _coin(monkeypatch, 0.0)
    app, calls = _shadow_app(tmp_path, lite_max_request_chars=10, **overrides)
    resp = await _post(app, EASY)
    assert resp.headers["x-thetarelay-tier"] == "frontier"
    await _drain(app)
    assert not _lite_calls(calls)  # the lite tier cannot take it
    assert "shadow" not in _lines(tmp_path)[0]


async def test_judge_skipped_in_frontier_mode_without_a_turn_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _coin(monkeypatch, 0.0)
    app, calls = _shadow_app(tmp_path, turn_log_path="")
    await _post(app, EASY)
    await _drain(app)
    assert not any("judge.test" in c[0] for c in calls)  # nobody would see its answer
