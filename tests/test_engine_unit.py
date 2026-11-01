"""Unit tests for thetarelay.engine functions and edge cases."""

from __future__ import annotations

import httpx
import pytest

from thetarelay.config import SpeculativeConfig
from thetarelay.engine import (
    DraftResult,
    _percentile,
    build_verification_injection,
    call_local_drafter,
    estimate_injection_input_tokens,
    response_accepted_draft,
    should_speculate,
)


def test_percentile_calculations() -> None:
    assert _percentile([], 50.0) == 0.0
    assert _percentile([42.0], 90.0) == 42.0
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile(vals, 0.0) == 10.0
    assert _percentile(vals, 50.0) == 30.0
    assert _percentile(vals, 100.0) == 50.0


def test_draft_result_and_injection_formatting() -> None:
    d_empty = DraftResult()
    assert not d_empty.has_draft
    assert estimate_injection_input_tokens(d_empty, SpeculativeConfig()) == 0

    d_full = DraftResult(
        text="some code",
        tool_calls=[{"name": "test_tool", "input": {"arg": 1}}],
    )
    assert d_full.has_draft
    formatted = d_full.format_for_verification()
    assert "some code" in formatted
    assert "<draft_tool_calls>" in formatted

    cfg_context = SpeculativeConfig(strategy="context_only")
    inj_context = build_verification_injection(d_full, cfg_context)
    assert "You may use or refine the above local draft" in inj_context

    cfg_swap = SpeculativeConfig(strategy="accept_swap", accept_token="<YES/>")
    inj_swap = build_verification_injection(d_full, cfg_swap)
    assert "output ONLY `<YES/>`" in inj_swap


def test_response_accepted_draft_variations() -> None:
    cfg = SpeculativeConfig(accept_token="<ACCEPT/>")
    assert response_accepted_draft("<ACCEPT/>", cfg) is True
    assert response_accepted_draft("  <ACCEPT/>  \n", cfg) is True
    assert response_accepted_draft("<think>checking</think><ACCEPT/>", cfg) is True
    assert response_accepted_draft("I accept this draft.", cfg) is False
    assert response_accepted_draft("<ACCEPT/> but here are changes", cfg) is False
    assert response_accepted_draft("", cfg) is False


def test_should_speculate_edge_cases() -> None:
    cfg = SpeculativeConfig()
    # Empty messages list
    ok, reason = should_speculate({"messages": []}, provider="anthropic", config=cfg)
    assert not ok
    assert "empty_messages" in reason

    # Already has speculative draft
    ok, reason = should_speculate(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "prompt with <speculative_draft>existing</speculative_draft>",
                }
            ]
        },
        provider="anthropic",
        config=cfg,
    )
    assert not ok
    assert "already_has_draft" in reason


@pytest.mark.asyncio
async def test_call_local_drafter_error_and_empty_choices() -> None:
    cfg = SpeculativeConfig(local_base_url="http://mock-local.test/v1")

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "empty" in url:
            return httpx.Response(200, json={"choices": []})
        if "error" in url:
            return httpx.Response(502, text="Bad Gateway")
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)) as client:
        # Empty choices
        cfg.local_base_url = "http://mock-local.test/v1/empty"
        d1 = await call_local_drafter(
            body={"messages": [{"role": "user", "content": "test prompt long enough"}]},
            provider="anthropic",
            config=cfg,
            http_client=client,
        )
        assert d1.error == "empty_choices"

        # HTTP error
        cfg.local_base_url = "http://mock-local.test/v1/error"
        d2 = await call_local_drafter(
            body={"messages": [{"role": "user", "content": "test prompt long enough"}]},
            provider="anthropic",
            config=cfg,
            http_client=client,
        )
        assert d2.error == "http_502"
