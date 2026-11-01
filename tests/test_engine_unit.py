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
    inject_draft_into_body,
    response_accepted_draft,
    should_speculate,
)
from thetarelay.usecases.coding.humaneval import (
    evaluate_humaneval_functional_pass,
    get_builtin_humaneval_problems,
)
from thetarelay.usecases.coding.scenarios import extract_python_code_blocks


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


def test_strategy_validation_and_prediction_fallback() -> None:
    with pytest.raises(ValueError, match="Unknown speculative strategy"):
        SpeculativeConfig(strategy="invalid_mode")

    # `prediction` strategy on Anthropic or with tool calls falls back to `accept_swap`
    cfg = SpeculativeConfig(strategy="prediction")
    body = {"messages": [{"role": "user", "content": "Write hello world"}]}
    draft_text = DraftResult(text="print('hi')")
    updated_anth, mode_anth = inject_draft_into_body(body, "anthropic", draft_text, cfg)
    assert mode_anth == "accept_swap"
    assert "<speculative_draft>" in updated_anth["messages"][-1]["content"]

    draft_tools = DraftResult(
        text="calling tool",
        tool_calls=[{"id": "t1", "name": "read_file", "input": {"path": "a.py"}}],
    )
    updated_oai_tools, mode_oai_tools = inject_draft_into_body(body, "openai", draft_tools, cfg)
    assert mode_oai_tools == "accept_swap"
    assert "prediction" not in updated_oai_tools

    # `prediction` strategy on OpenAI text-only uses `openai_prediction`
    updated_oai_text, mode_oai_text = inject_draft_into_body(body, "openai", draft_text, cfg)
    assert mode_oai_text == "openai_prediction"
    assert updated_oai_text["prediction"] == {"type": "content", "content": "print('hi')"}


def test_extract_python_code_blocks_indentation_and_truncation() -> None:
    # 1. Preserves leading indentation for body-only completions
    body_only_fenced = "```python\n    return number % 1.0\n```"
    extracted_body = extract_python_code_blocks(body_only_fenced)
    assert extracted_body == "    return number % 1.0"

    # Verify HumanEval/2 passes with both indented and unindented body-only completions
    prob_he2 = next(p for p in get_builtin_humaneval_problems() if p.task_id == "HumanEval/2")
    passed_indented, status1 = evaluate_humaneval_functional_pass(
        code_str=body_only_fenced, problem=prob_he2
    )
    assert passed_indented, status1
    passed_unindented, status2 = evaluate_humaneval_functional_pass(
        code_str="```python\nreturn number % 1.0\n```", problem=prob_he2
    )
    assert passed_unindented, status2

    # 2. Unclosed trailing markdown fence (truncated response)
    truncated = (
        "Here is the code:\n```python\n"
        "def truncate_number(number: float) -> float:\n    return number % 1.0"
    )
    extracted_trunc = extract_python_code_blocks(truncated)
    assert "def truncate_number" in extracted_trunc
    assert "Here is the code:" not in extracted_trunc

    # 3. Multiple fences: function in first fence, non-def example in second fence
    multi_fence = (
        "Here is the implementation:\n"
        "```python\ndef truncate_number(number: float) -> float:\n    return number % 1.0\n```\n"
        "Example usage:\n"
        "```python\nassert truncate_number(3.5) == 0.5\n```"
    )
    extracted_multi = extract_python_code_blocks(multi_fence)
    assert "def truncate_number" in extracted_multi
    assert "assert truncate_number" not in extracted_multi
