"""Routing decisions, the judge summary, and the proxy end to end against mock upstreams."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from thetarelay.config import RouterConfig
from thetarelay.proxy import StreamUsage, create_app
from thetarelay.router import events, requests_since_failure, summarize

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
        local_base_url="http://local.test",
        frontier_base_url="http://frontier.test/api",
        judge_url="http://judge.test/v1/systemone",
        frontier_api_key="sk-frontier",
        enable_headroom_middleware=False,
        turn_log_path=str(tmp_path / "turns.jsonl"),
        **{"escalate_on_stop": False, **overrides},
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
    assert resp.headers["x-thetarelay-tier"] == "local"
    url, body, headers = calls[-1]
    assert url == "http://local.test/v1/messages"
    assert body["model"] == "gemma4:e4b"
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
    assert body["model"] == "openai/gpt-6.1-terra"
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
    app, calls = _app(tmp_path, 0.99, local_max_request_chars=10)
    assert (await _post(app, EASY)).headers["x-thetarelay-reason"] == "too_large_for_local"
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
    assert resp.headers["x-thetarelay-reason"] == "judge; local_failed"


@pytest.mark.asyncio
async def test_turn_log_and_stats_record_each_decision(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, 0.9)
    await _post(app, EASY)
    line = json.loads((tmp_path / "turns.jsonl").read_text().splitlines()[0])
    assert line["tier"] == "local"
    assert line["reason"] == "judge"
    assert line["p_easy"] == 0.9
    assert line["judge_state"]["last_message"] == "[user] ls"
    assert line["cached_tokens"] == 8
    assert line["cost_usd"] == 0.01
    assert "sk-" not in (tmp_path / "turns.jsonl").read_text()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://relay"
    ) as client:
        assert (await client.get("/stats")).json()["counts"] == {"local_requests": 1}


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

    app, calls = _app(tmp_path, 0.01, route_mode="random", random_local_share=1.0)
    resp = await _post(app, EASY)
    assert (resp.headers["x-thetarelay-tier"], resp.headers["x-thetarelay-reason"]) == (
        "local",
        "random",
    )
    assert not any("judge.test" in c[0] for c in calls)
    # Random mode keeps the hard rules: a recent failure still goes to the frontier.
    resp = await _post(app, {"model": "claude-x", "max_tokens": 10, **ANTH_FAIL})
    assert resp.headers["x-thetarelay-tier"] == "frontier"

    app, calls = _app(tmp_path, 0.01, route_mode="local")

    async def down(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), {}, {}))
        return httpx.Response(503)

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(down))
    resp = await _post(app, EASY)
    assert resp.status_code == 503  # forced local: no fallback
    assert not any("frontier.test" in c[0] for c in calls)

    with pytest.raises(ValueError, match="route_mode"):
        RouterConfig(route_mode="cheap")


@pytest.mark.asyncio
async def test_local_reply_without_tool_call_is_escalated(tmp_path: Path) -> None:
    app, calls = _app(tmp_path, 0.9, escalate_on_stop=True)
    tool = b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
    text = b'data: {"choices":[{"delta":{"content":"done?"},"finish_reason":"stop"}]}\n\n'
    replies = {"local": tool}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), {}, {}))
        if "judge.test" in str(request.url):
            return httpx.Response(
                200, json={"answers": {"route": {"probabilities": {"easy": 0.9}}}}
            )
        body = replies["local"] if "local.test" in str(request.url) else b"data: frontier\n\n"
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resp = await _post(app, {**EASY, "stream": True})
    assert (resp.headers["x-thetarelay-tier"], resp.content) == ("local", tool)

    replies["local"] = text
    resp = await _post(app, {**EASY, "stream": True})
    assert resp.headers["x-thetarelay-tier"] == "frontier"
    assert resp.headers["x-thetarelay-reason"] == "judge; local_stopped"
    assert resp.content == b"data: frontier\n\n"
