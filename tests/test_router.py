"""Routing decisions, the judge summary, and the proxy end to end against mock upstreams."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from thetarelay.config import RouterConfig
from thetarelay.proxy import StreamUsage, create_app
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
        turn_log_path=str(tmp_path / "turns.jsonl"),
        **{"escalate_on_stop": False, "route_mode": "judge", "stay_requests": 6, **overrides},
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
