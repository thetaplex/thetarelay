"""Built-in coding-agent scenarios and the code/tool accuracy check.

Coding scenario categories: "code_edit", "tool_call", "openai_prediction", plus the generic
"rejection_guard" and "bypass" (see `thetarelay.evals.EvalScenario`).
"""

from __future__ import annotations

import ast
import contextlib
import re
from typing import Any

from ...evals import EvalScenario, verify_text_and_tool_accuracy


def get_builtin_scenarios() -> list[EvalScenario]:
    """Return the standard suite of coding-agent evaluation scenarios."""
    old_port_code = "def parse_port(val: str) -> int:\n    port = int(val)\n    return port"
    new_port_code = (
        "def parse_port(val: str) -> int:\n"
        "    port = int(val)\n"
        "    if not (1 <= port <= 65535):\n"
        '        raise ValueError(f"Invalid port: {port}")\n'
        "    return port"
    )
    pytest_suite_code = (
        "import pytest\nfrom src.net import parse_port\n\n\n"
        '@pytest.mark.parametrize("raw,expected", [("1", 1), ("8080", 8080)])\n'
        "def test_parse_port_valid(raw: str, expected: int) -> None:\n"
        "    assert parse_port(raw) == expected\n\n\n"
        '@pytest.mark.parametrize("invalid", ["0", "-5", "65536", "abc", ""])\n'
        "def test_parse_port_invalid(invalid: str) -> None:\n"
        "    with pytest.raises(ValueError):\n"
        "        parse_port(invalid)\n"
    )
    return [
        EvalScenario(
            id="anth_code_refactor",
            title="Anthropic: Refactor authentication helper with type hints & docstrings",
            category="code_edit",
            provider="anthropic",
            request_body={
                "model": "claude-sonnet-4-5",
                "system": "You are an expert Python coding agent.",
                "messages": [
                    {"role": "user", "content": "Read src/auth.py"},
                    {
                        "role": "assistant",
                        "content": (
                            "Here is src/auth.py:\n"
                            "def verify_token(tok, secret):\n"
                            "    return tok == secret\n"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Rewrite verify_token in Python using hmac.compare_digest "
                            "with full type annotations and handle empty strings safely."
                        ),
                    },
                ],
                "stream": False,
            },
            expected_text=(
                "import hmac\n\n\n"
                "def verify_token(token: str, secret: str) -> bool:\n"
                '    """Constant-time token comparison."""\n'
                "    if not token or not secret:\n"
                "        return False\n"
                "    return hmac.compare_digest(token.encode('utf-8'), secret.encode('utf-8'))\n"
            ),
            simulated_draft_text=(
                "import hmac\n\n\n"
                "def verify_token(token: str, secret: str) -> bool:\n"
                '    """Constant-time token comparison."""\n'
                "    if not token or not secret:\n"
                "        return False\n"
                "    return hmac.compare_digest(token.encode('utf-8'), secret.encode('utf-8'))\n"
            ),
            draft_is_accurate=True,
            prompt_cached_tokens=12000,
            prompt_uncached_tokens=350,
            expected_output_tokens=185,
            simulated_local_draft_ms=240.0,
            simulated_frontier_ttft_ms=400.0,
        ),
        EvalScenario(
            id="anth_tool_edit_file",
            title="Anthropic: Multi-line edit_file tool call on buggy parser",
            category="tool_call",
            provider="anthropic",
            request_body={
                "model": "claude-sonnet-4-5",
                "system": "You are a coding agent with file editing tools.",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_read_1",
                                "content": old_port_code + "\n",
                            },
                            {
                                "type": "text",
                                "text": (
                                    "Use edit_file on src/net.py to validate that port is "
                                    "between 1 and 65535, raising ValueError otherwise."
                                ),
                            },
                        ],
                    }
                ],
                "tools": [
                    {
                        "name": "edit_file",
                        "description": "Replace target text in a file",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "old_str": {"type": "string"},
                                "new_str": {"type": "string"},
                            },
                            "required": ["path", "old_str", "new_str"],
                        },
                    }
                ],
                "stream": False,
            },
            expected_text="I will update `parse_port` in `src/net.py` to validate the port range.",
            expected_tool_calls=[
                {
                    "id": "toolu_edit_1",
                    "name": "edit_file",
                    "input": {
                        "path": "src/net.py",
                        "old_str": old_port_code,
                        "new_str": new_port_code,
                    },
                }
            ],
            simulated_draft_text=(
                "I will update `parse_port` in `src/net.py` to validate the port range."
            ),
            simulated_draft_tool_calls=[
                {
                    "id": "toolu_edit_1",
                    "name": "edit_file",
                    "input": {
                        "path": "src/net.py",
                        "old_str": old_port_code,
                        "new_str": new_port_code,
                    },
                }
            ],
            draft_is_accurate=True,
            prompt_cached_tokens=16000,
            prompt_uncached_tokens=420,
            expected_output_tokens=260,
            simulated_local_draft_ms=290.0,
            simulated_frontier_ttft_ms=430.0,
        ),
        EvalScenario(
            id="anth_stream_pytest_gen",
            title="Anthropic SSE Stream: Generate parametrized pytest unit suite",
            category="code_edit",
            provider="anthropic",
            request_body={
                "model": "claude-sonnet-4-5",
                "system": "You are a senior Python test engineer.",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Write a complete pytest suite using @pytest.mark.parametrize "
                            "for parse_port(val: str) covering valid and invalid ports."
                        ),
                    }
                ],
                "stream": True,
            },
            expected_text=pytest_suite_code,
            simulated_draft_text=pytest_suite_code,
            draft_is_accurate=True,
            prompt_cached_tokens=10000,
            prompt_uncached_tokens=300,
            expected_output_tokens=360,
            simulated_local_draft_ms=320.0,
            simulated_frontier_ttft_ms=410.0,
            stream=True,
        ),
        EvalScenario(
            id="openai_predicted_outputs",
            title="OpenAI GPT-4o: Predicted Outputs (`prediction`) fast file rewrite",
            category="openai_prediction",
            provider="openai",
            request_body={
                "model": "gpt-4o",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Add a retry_backoff(attempt: int, base: float = 0.5) -> float "
                            "helper function with exponential backoff capped at 30.0 seconds."
                        ),
                    }
                ],
                "stream": False,
            },
            expected_text=(
                "def retry_backoff(attempt: int, base: float = 0.5) -> float:\n"
                '    """Calculate exponential backoff in seconds, capped at 30.0s."""\n'
                "    return min(30.0, base * (2 ** max(0, attempt)))\n"
            ),
            simulated_draft_text=(
                "def retry_backoff(attempt: int, base: float = 0.5) -> float:\n"
                '    """Calculate exponential backoff in seconds, capped at 30.0s."""\n'
                "    return min(30.0, base * (2 ** max(0, attempt)))\n"
            ),
            draft_is_accurate=True,
            prompt_cached_tokens=6000,
            prompt_uncached_tokens=250,
            expected_output_tokens=160,
            simulated_local_draft_ms=210.0,
            simulated_frontier_ttft_ms=380.0,
        ),
        EvalScenario(
            id="anth_reject_flawed_draft",
            title="Anthropic Accuracy Guardrail: Reject flawed local draft with SQL injection",
            category="rejection_guard",
            provider="anthropic",
            request_body={
                "model": "claude-sonnet-4-5",
                "system": "You are a security-focused backend engineer.",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Write a sqlite3 query helper get_user_by_email(conn, email) "
                            "that safely fetches a user row."
                        ),
                    }
                ],
                "stream": False,
            },
            expected_text=(
                "def get_user_by_email(conn, email: str):\n"
                '    sql = "SELECT id, email FROM users WHERE email = ?"\n'
                "    cursor = conn.execute(sql, (email,))\n"
                "    return cursor.fetchone()\n"
            ),
            # Local model drafts an unsafe f-string query -> Frontier MUST reject and rewrite!
            simulated_draft_text=(
                "def get_user_by_email(conn, email: str):\n"
                "    sql = f\"SELECT id, email FROM users WHERE email = '{email}'\"\n"
                "    cursor = conn.execute(sql)\n"
                "    return cursor.fetchone()\n"
            ),
            draft_is_accurate=False,
            prompt_cached_tokens=9000,
            prompt_uncached_tokens=280,
            expected_output_tokens=140,
            simulated_local_draft_ms=230.0,
            simulated_frontier_ttft_ms=420.0,
        ),
        EvalScenario(
            id="anth_skip_small_model",
            title="Bypass Filter: Skip local drafting on fast Haiku subagent call",
            category="bypass",
            provider="anthropic",
            request_body={
                "model": "claude-3-5-haiku-latest",
                "messages": [
                    {
                        "role": "user",
                        "content": "Summarize the git commit message in one line.",
                    }
                ],
                "stream": False,
            },
            expected_text="fix(net): validate port bounds in parse_port",
            simulated_draft_text="",
            draft_is_accurate=False,
            prompt_cached_tokens=2000,
            prompt_uncached_tokens=120,
            expected_output_tokens=25,
            simulated_local_draft_ms=0.0,
            simulated_frontier_ttft_ms=180.0,
        ),
    ]


def extract_python_code_blocks(text: str) -> str:
    """Extract Python code from markdown ```python ... ``` fences if present."""
    stripped = text.strip()
    fence_matches: list[str] = re.findall(
        r"```(?:python|py)?\s*\n(.*?)```", stripped, flags=re.DOTALL
    )
    if fence_matches:
        joined = "\n\n".join(m.strip() for m in fence_matches)
        with contextlib.suppress(SyntaxError):
            ast.parse(joined)
            return joined
        # When a model critiques a snippet in one fence and provides the complete function
        # in a later fence, prefer the last syntactically valid block containing `def `.
        for block in reversed(fence_matches):
            candidate = block.strip()
            with contextlib.suppress(SyntaxError):
                ast.parse(candidate)
                if "def " in candidate:
                    return candidate
        for block in reversed(fence_matches):
            candidate = block.strip()
            with contextlib.suppress(SyntaxError):
                ast.parse(candidate)
                return candidate
        return fence_matches[-1].strip()
    return stripped


def verify_code_and_tool_accuracy(
    *,
    actual_text: str,
    actual_tool_calls: list[dict[str, Any]],
    expected_text: str,
    expected_tool_calls: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    """Verify whether the proxy's returned response matches the expected frontier quality.

    Checks:
    1. Exact or normalized text match.
    2. Python AST syntax validity if Python code blocks/defs are present.
    3. Tool call name and JSON argument equivalence.
    """
    matched, details = verify_text_and_tool_accuracy(
        actual_text=actual_text,
        actual_tool_calls=actual_tool_calls,
        expected_text=expected_text,
        expected_tool_calls=expected_tool_calls,
    )

    # Check if Python code in actual_text (or inside ```python fences) parses cleanly as valid AST
    ast_valid = True
    candidate_code = extract_python_code_blocks(actual_text)
    if candidate_code.startswith(("def ", "import ", "from ", "@pytest", "class ")):
        try:
            ast.parse(candidate_code)
        except SyntaxError:
            ast_valid = False

    return matched and ast_valid, {**details, "ast_valid": ast_valid}
