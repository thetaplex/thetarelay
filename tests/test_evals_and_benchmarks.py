"""Tests for ThetaRelay A/B benchmarks, live/mock evals, and speed/efficiency/accuracy telemetry."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from thetarelay.config import SpeculativeConfig
from thetarelay.evals import (
    format_benchmark_report_table,
    get_builtin_scenarios,
    load_eval_scenarios_from_jsonl,
    run_ab_benchmark,
    run_live_eval,
    verify_code_and_tool_accuracy,
)
from thetarelay.humaneval import (
    compute_paired_mcnemar_and_confusion,
    evaluate_humaneval_functional_pass,
    get_builtin_humaneval_problems,
    mutate_python_code_ast,
    run_humaneval_benchmark,
)


@pytest.mark.asyncio
async def test_ab_benchmark_proves_speed_efficiency_and_accuracy() -> None:
    """Verify that the deterministic A/B benchmark suite runs all coding scenarios and proves:
    1. Speedup (>1.5x overall wall-clock speedup on coding turns)
    2. Efficiency (>50% reduction in frontier output tokens, net USD savings, 100% cache preserved)
    3. Accuracy (100% scenario pass rate, 0 false accepts on flawed SQL-injection draft)
    """
    cfg = SpeculativeConfig()
    report = await run_ab_benchmark(cfg)
    summary = report.summary()

    # 1. Speed assertions
    speed = summary["speed"]
    assert speed["speculative_total_latency_ms"] < speed["baseline_total_latency_ms"]
    assert speed["net_latency_saved_ms"] > 6000.0
    assert speed["overall_speedup_factor"] >= 1.8

    # 2. Efficiency assertions
    eff = summary["efficiency"]
    assert eff["output_tokens_saved"] > 600
    assert eff["output_token_reduction_pct"] >= 65.0
    assert eff["net_tokens_saved"] > 0
    assert eff["net_usd_saved"] > 0.0
    assert eff["cost_reduction_pct"] >= 20.0
    assert eff["prefix_cache_preserved_rate_pct"] == 100.0

    # 3. Accuracy & Guardrail assertions
    acc = summary["accuracy"]
    assert acc["scenarios_passed"] == acc["scenarios_total"]
    assert acc["accuracy_rate_pct"] == 100.0
    assert acc["false_accepts"] == 0

    # Check formatted CLI table output
    table_str = format_benchmark_report_table(report)
    assert "THETARELAY SPECULATIVE A/B BENCHMARK" in table_str
    assert "anth_reject_flawed_draft" in table_str
    assert "rejected(guard)" in table_str


def test_verify_code_and_tool_accuracy_detects_syntax_and_tool_mismatches() -> None:
    # Valid Python code & matching tool call passes
    ok, details = verify_code_and_tool_accuracy(
        actual_text="def f(x: int) -> int:\n    return x + 1\n",
        actual_tool_calls=[{"id": "1", "name": "edit_file", "input": {"path": "a.py"}}],
        expected_text="def f(x: int) -> int:\n    return x + 1\n",
        expected_tool_calls=[{"id": "99", "name": "edit_file", "input": {"path": "a.py"}}],
    )
    assert ok
    assert details["ast_valid"]
    assert details["tools_match"]

    # Invalid Python syntax fails ast_valid
    ok_bad, details_bad = verify_code_and_tool_accuracy(
        actual_text="def f(x: int) ->\n    return x\n",
        actual_tool_calls=[],
        expected_text="def f(x: int) ->\n    return x\n",
        expected_tool_calls=[],
    )
    assert not ok_bad
    assert not details_bad["ast_valid"]


@pytest.mark.asyncio
async def test_run_live_eval_with_mock_transport_and_custom_jsonl(tmp_path: Path) -> None:
    """Verify `run_live_eval` and `load_eval_scenarios_from_jsonl` with mocked local model."""
    custom_jsonl = tmp_path / "scenarios.jsonl"
    fib_code = "def fib(n: int) -> int:\n    return n if n <= 1 else fib(n - 1) + fib(n - 2)\n"
    scenario_item = {
        "id": "custom_fib",
        "title": "Write fib(n)",
        "category": "code_edit",
        "provider": "anthropic",
        "request_body": {
            "model": "claude-sonnet-4-5",
            "messages": [
                {"role": "user", "content": "Write a python function fib(n: int) -> int."}
            ],
        },
        "expected_text": fib_code,
    }
    custom_jsonl.write_text(json.dumps(scenario_item) + "\n", encoding="utf-8")

    loaded = load_eval_scenarios_from_jsonl(custom_jsonl)
    assert len(loaded) == 1
    assert loaded[0].id == "custom_fib"

    async def mock_local_drafter(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-eval",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": fib_code,
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    cfg = SpeculativeConfig(local_base_url="http://local-eval.test/v1")
    eval_res = await run_live_eval(
        cfg,
        scenarios=loaded,
        transport=httpx.MockTransport(mock_local_drafter),
    )
    assert eval_res["scenarios_total"] == 1
    assert eval_res["scenarios_succeeded"] == 1
    assert eval_res["valid_draft_rate_pct"] == 100.0
    assert eval_res["results"][0]["exact_match"] is True
    assert eval_res["results"][0]["ast_valid"] is True


def test_builtin_scenarios_cover_all_categories() -> None:
    scenarios = get_builtin_scenarios()
    categories = {s.category for s in scenarios}
    assert {
        "code_edit",
        "tool_call",
        "openai_prediction",
        "rejection_guard",
        "bypass",
    }.issubset(categories)


@pytest.mark.asyncio
async def test_humaneval_functional_pass_and_guardrail_recovery() -> None:
    """Verify canonical HumanEval `check(candidate)` execution and buggy-draft recovery."""
    problems = get_builtin_humaneval_problems()[:2]

    # 1. Verify canonical solutions pass 100% and buggy_draft fails
    p0 = problems[0]
    ok_canon, _ = evaluate_humaneval_functional_pass(code_str=p0.canonical_solution, problem=p0)
    assert ok_canon
    ok_bug, _ = evaluate_humaneval_functional_pass(code_str=p0.buggy_draft, problem=p0)
    assert not ok_bug

    # 2. Run `run_humaneval_benchmark` with MockTransport
    by_entry = {p.entry_point: p.canonical_solution for p in problems}

    async def mock_humaneval_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        last_content = body["messages"][-1]["content"]
        # If this is a verification turn containing a buggy draft, reject and return canonical
        if "<speculative_draft>" in last_content:
            if "BUG:" in last_content:
                for ep, sol in by_entry.items():
                    if ep in last_content:
                        return httpx.Response(
                            200,
                            json={
                                "choices": [{"message": {"role": "assistant", "content": sol}}],
                                "usage": {"completion_tokens": 45},
                            },
                        )
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "<ACCEPT/>"}}],
                    "usage": {"completion_tokens": 2},
                },
            )

        # Otherwise return the canonical solution for the requested entry_point
        for ep, sol in by_entry.items():
            if ep in last_content:
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"role": "assistant", "content": sol}}],
                        "usage": {"completion_tokens": 45},
                    },
                )
        return httpx.Response(404)

    cfg = SpeculativeConfig(local_base_url="http://he-local.test/v1")
    report = await run_humaneval_benchmark(
        cfg,
        problems=problems,
        target_model="he-target-model",
        transport=httpx.MockTransport(mock_humaneval_handler),
    )
    assert report["local_pass_at_1_pct"] == 100.0
    tc = report["target_comparison"]
    assert tc["baseline_pass_at_1_pct"] == 100.0
    assert tc["speculative_pass_at_1_pct"] == 100.0
    assert tc["acceptance_rate_pct"] == 100.0
    guard = tc["buggy_draft_guardrail_tests"][0]
    assert guard["buggy_draft_pass_at_1"] is False
    assert guard["verifier_rejected_bug"] is True
    assert guard["recovered_pass_at_1"] is True
    assert tc["mcnemar_paired_test"]["delta_accuracy_pct"] == 0.0
    assert tc["verifier_confusion_matrix"]["false_accepts_sycophancy"] == 0


def test_ast_mutation_and_mcnemar_confusion_matrix() -> None:
    """Verify AST mutation produces syntactically valid failing mutants and McNemar stats."""
    p0 = get_builtin_humaneval_problems()[0]
    mutants = mutate_python_code_ast(p0.canonical_solution)
    assert len(mutants) >= 2
    # At least one single-operator mutant must fail the unit test suite while parsing cleanly
    failing_mutants = [
        (label, src)
        for label, src in mutants
        if not evaluate_humaneval_functional_pass(code_str=src, problem=p0)[0]
    ]
    assert len(failing_mutants) >= 1

    # Verify McNemar & Verifier Confusion Matrix with positive, sycophantic, and overstrict rows
    sample_rows = [
        # 1. True accept (good draft, accepted)
        {
            "local_pass_at_1": True,
            "target_accepted": True,
            "baseline_pass_at_1": True,
            "speculative_pass_at_1": True,
        },
        # 2. False accept / sycophancy (bad draft, accepted -> regression!)
        {
            "local_pass_at_1": False,
            "target_accepted": True,
            "baseline_pass_at_1": True,
            "speculative_pass_at_1": False,
        },
        # 3. True reject with recovery (bad draft, rejected -> rewritten to pass)
        {
            "local_pass_at_1": False,
            "target_accepted": False,
            "baseline_pass_at_1": True,
            "speculative_pass_at_1": True,
        },
        # 4. False reject / overstrict (good draft, rejected -> still passes)
        {
            "local_pass_at_1": True,
            "target_accepted": False,
            "baseline_pass_at_1": True,
            "speculative_pass_at_1": True,
        },
    ]
    stats = compute_paired_mcnemar_and_confusion(sample_rows)
    mc = stats["mcnemar_paired_test"]
    assert mc["n_pairs"] == 4
    assert mc["both_pass_a"] == 3
    assert mc["baseline_only_pass_b"] == 1
    assert mc["speculative_only_pass_c"] == 0
    assert mc["delta_accuracy_pct"] == -25.0

    cm = stats["verifier_confusion_matrix"]
    assert cm["true_accepts"] == 1
    assert cm["false_accepts_sycophancy"] == 1
    assert cm["true_rejects"] == 1
    assert cm["false_rejects_overstrict"] == 1
    assert cm["true_accept_rate_pct"] == 50.0
    assert cm["false_accept_rate_pct"] == 50.0
    assert cm["post_rejection_recovery_rate_pct"] == 100.0
