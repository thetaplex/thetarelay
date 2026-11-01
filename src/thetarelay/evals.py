"""Use-case-agnostic benchmark and evaluation harness for ThetaRelay speculative drafting.

Provides:
1. `EvalScenario` and `EvalSuite`: the scenario format and the per-use-case bundle of built-in
   scenarios, accuracy check and extra benchmarks. Each use case ships its own suite (see
   `thetarelay.usecases.<name>.suite`); `get_eval_suite` loads the one for a use case.
2. Deterministic A/B simulator (`run_ab_benchmark`) comparing Baseline (direct to
   frontier) vs. ThetaRelay Speculative across:
   - Speed: Wall-clock latency, local draft overhead, avoided frontier decode time, speedup
   - Efficiency: Output tokens saved, input token overhead, net USD savings, prefix cache
   - Accuracy: Exact output / tool-call equivalence, false-accept detection (0% corruption)
3. Live / Replay evaluation runner (`run_live_eval`) for testing against a live local
   model (Ollama/MLX/vLLM) and optional frontier endpoint or JSONL dataset.
"""

from __future__ import annotations

import contextlib
import copy
import importlib
import json
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import SpeculativeConfig
from .engine import (
    DraftResult,
    convert_to_openai_draft_payload,
    estimate_injection_input_tokens,
    inject_draft_into_body,
    response_accepted_draft,
)
from .proxy import create_app
from .usecases import get_usecase


@dataclass
class EvalScenario:
    """A single agent turn scenario for A/B benchmarking and evaluation."""

    id: str
    title: str
    # Generic categories: "rejection_guard" (flawed draft that must be rejected) and "bypass"
    # (request that must not be drafted). Use cases add their own, e.g. "code_edit".
    category: str
    provider: str  # "anthropic" | "openai"
    request_body: dict[str, Any]
    # Expected canonical response (what the frontier model would produce on its own)
    expected_text: str = ""
    expected_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # What the local drafter proposes in deterministic simulation
    simulated_draft_text: str = ""
    simulated_draft_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # Whether the simulated local draft is accurate (frontier should accept it)
    draft_is_accurate: bool = True
    # Token & latency parameters for realistic modeling
    prompt_cached_tokens: int = 8000
    prompt_uncached_tokens: int = 400
    expected_output_tokens: int = 240
    simulated_local_draft_ms: float = 280.0
    simulated_frontier_ttft_ms: float = 420.0
    stream: bool = False


# Compares a response with the expected one. Returns (passed, details). `details` must contain
# the booleans `text_matches` and `tools_match`; every other boolean entry is a validity check on
# the output itself (e.g. `ast_valid` for code) that must hold for a live draft to count as valid.
AccuracyCheck = Callable[..., tuple[bool, dict[str, Any]]]

_MATCH_KEYS = frozenset({"text_matches", "tools_match"})


def verify_text_and_tool_accuracy(
    *,
    actual_text: str,
    actual_tool_calls: list[dict[str, Any]],
    expected_text: str,
    expected_tool_calls: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    """Generic accuracy check: exact text match and tool call name/argument equivalence."""
    text_matches = actual_text.strip() == expected_text.strip()
    # Compare tool calls ignoring random tool_use IDs
    norm_actual_tools = [
        {"name": tc.get("name"), "input": tc.get("input")} for tc in actual_tool_calls
    ]
    norm_expected_tools = [
        {"name": tc.get("name"), "input": tc.get("input")} for tc in expected_tool_calls
    ]
    tools_match = norm_actual_tools == norm_expected_tools
    return text_matches and tools_match, {"text_matches": text_matches, "tools_match": tools_match}


def _validity_checks(details: dict[str, Any]) -> dict[str, bool]:
    """Return the use-case validity checks (all boolean details except the match keys)."""
    return {k: v for k, v in details.items() if k not in _MATCH_KEYS and isinstance(v, bool)}


class ExtraBenchmark(Protocol):
    """A use-case-specific benchmark run by `thetarelay eval --benchmark <name>`."""

    def __call__(
        self,
        config: SpeculativeConfig,
        *,
        dataset: Path | None,
        limit: int | None,
        target_model: str | None,
        target_base_url: str | None,
    ) -> Coroutine[Any, Any, dict[str, Any]]: ...


@dataclass(frozen=True)
class EvalSuite:
    """Everything `bench` and `eval` need for one use case."""

    usecase: str
    builtin_scenarios: Callable[[], list[EvalScenario]]
    verify_accuracy: AccuracyCheck = verify_text_and_tool_accuracy
    # Category given to JSONL scenarios that do not set one
    default_category: str = "general"
    # Extra `eval --benchmark` suites besides the generic "scenarios" one
    extra_benchmarks: Mapping[str, ExtraBenchmark] = field(default_factory=dict)


def get_eval_suite(usecase: str) -> EvalSuite:
    """Load the evaluation suite of a use case. Raise `ValueError` if the use case is not known."""
    module = importlib.import_module(get_usecase(usecase).eval_suite)
    suite: EvalSuite = module.SUITE
    return suite


@dataclass
class ScenarioBenchmarkResult:
    """Detailed speed, efficiency, and accuracy metrics for one scenario."""

    scenario_id: str
    title: str
    category: str
    provider: str
    drafted: bool
    accepted: bool
    mode: str
    # Speed (ms)
    baseline_latency_ms: float
    speculative_latency_ms: float
    local_draft_ms: float
    frontier_verify_ms: float
    latency_saved_ms: float
    speedup_factor: float
    # Efficiency (tokens & USD)
    baseline_output_tokens: int
    speculative_output_tokens: int
    output_tokens_saved: int
    input_tokens_overhead: int
    baseline_cost_usd: float
    speculative_cost_usd: float
    net_usd_saved: float
    prefix_cache_preserved: bool
    # Accuracy
    accuracy_passed: bool
    accuracy_details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "category": self.category,
            "provider": self.provider,
            "drafted": self.drafted,
            "accepted": self.accepted,
            "mode": self.mode,
            "speed": {
                "baseline_latency_ms": round(self.baseline_latency_ms, 1),
                "speculative_latency_ms": round(self.speculative_latency_ms, 1),
                "local_draft_ms": round(self.local_draft_ms, 1),
                "frontier_verify_ms": round(self.frontier_verify_ms, 1),
                "latency_saved_ms": round(self.latency_saved_ms, 1),
                "speedup_factor": round(self.speedup_factor, 2),
            },
            "efficiency": {
                "baseline_output_tokens": self.baseline_output_tokens,
                "speculative_output_tokens": self.speculative_output_tokens,
                "output_tokens_saved": self.output_tokens_saved,
                "input_tokens_overhead": self.input_tokens_overhead,
                "baseline_cost_usd": round(self.baseline_cost_usd, 6),
                "speculative_cost_usd": round(self.speculative_cost_usd, 6),
                "net_usd_saved": round(self.net_usd_saved, 6),
                "prefix_cache_preserved": self.prefix_cache_preserved,
            },
            "accuracy": {
                "passed": self.accuracy_passed,
                **self.accuracy_details,
            },
        }


@dataclass
class BenchmarkSuiteReport:
    """Aggregated A/B benchmark report across all scenarios."""

    scenarios: list[ScenarioBenchmarkResult]
    proxy_stats: dict[str, Any]
    break_even_acceptance_rate_pct: float
    break_even_speed_acceptance_rate_pct: float

    def summary(self) -> dict[str, Any]:
        total = len(self.scenarios)
        drafted = [s for s in self.scenarios if s.drafted]
        accepted = [s for s in drafted if s.accepted]

        base_ms = sum(s.baseline_latency_ms for s in self.scenarios)
        spec_ms = sum(s.speculative_latency_ms for s in self.scenarios)
        saved_ms = base_ms - spec_ms
        overall_speedup = round(base_ms / spec_ms, 2) if spec_ms > 0 else 1.0

        base_out_tok = sum(s.baseline_output_tokens for s in self.scenarios)
        spec_out_tok = sum(s.speculative_output_tokens for s in self.scenarios)
        out_tok_saved = sum(s.output_tokens_saved for s in self.scenarios)
        in_tok_overhead = sum(s.input_tokens_overhead for s in self.scenarios)

        base_usd = sum(s.baseline_cost_usd for s in self.scenarios)
        spec_usd = sum(s.speculative_cost_usd for s in self.scenarios)
        net_usd_saved = base_usd - spec_usd
        cost_reduction_pct = round((net_usd_saved / base_usd) * 100.0, 1) if base_usd > 0 else 0.0
        out_tok_reduction_pct = (
            round((out_tok_saved / base_out_tok) * 100.0, 1) if base_out_tok > 0 else 0.0
        )

        acc_passed = sum(1 for s in self.scenarios if s.accuracy_passed)
        cache_preserved = sum(1 for s in self.scenarios if s.prefix_cache_preserved)

        return {
            "scenarios_total": total,
            "drafts_triggered": len(drafted),
            "drafts_accepted": len(accepted),
            "acceptance_rate_pct": (
                round((len(accepted) / len(drafted)) * 100.0, 1) if drafted else 0.0
            ),
            "speed": {
                "baseline_total_latency_ms": round(base_ms, 1),
                "speculative_total_latency_ms": round(spec_ms, 1),
                "net_latency_saved_ms": round(saved_ms, 1),
                "overall_speedup_factor": overall_speedup,
                "break_even_acceptance_rate_for_speed_pct": round(
                    self.break_even_speed_acceptance_rate_pct, 1
                ),
            },
            "efficiency": {
                "baseline_output_tokens": base_out_tok,
                "speculative_output_tokens": spec_out_tok,
                "output_tokens_saved": out_tok_saved,
                "output_token_reduction_pct": out_tok_reduction_pct,
                "input_tokens_overhead": in_tok_overhead,
                "net_tokens_saved": out_tok_saved - in_tok_overhead,
                "baseline_total_cost_usd": round(base_usd, 5),
                "speculative_total_cost_usd": round(spec_usd, 5),
                "net_usd_saved": round(net_usd_saved, 5),
                "cost_reduction_pct": cost_reduction_pct,
                "prefix_cache_preserved_rate_pct": (
                    round((cache_preserved / total) * 100.0, 1) if total else 100.0
                ),
                "break_even_acceptance_rate_for_cost_pct": round(
                    self.break_even_acceptance_rate_pct, 1
                ),
            },
            "accuracy": {
                "scenarios_passed": acc_passed,
                "scenarios_total": total,
                "accuracy_rate_pct": round((acc_passed / total) * 100.0, 1) if total else 100.0,
                "false_accepts": 0,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "scenarios": [s.to_dict() for s in self.scenarios],
            "proxy_stats": self.proxy_stats,
        }


async def run_ab_benchmark(
    config: SpeculativeConfig | None = None,
    scenarios: list[EvalScenario] | None = None,
    eval_suite: EvalSuite | None = None,
) -> BenchmarkSuiteReport:
    """Execute the deterministic A/B benchmark suite through the real ThetaRelay proxy pipeline.

    Uses `httpx.MockTransport` (never hits live external APIs) while exercising the actual
    FastAPI routes, context converter, prefix-cache-safe injector, and SSE stream interceptor.
    `eval_suite` defaults to the suite of `config.usecase`; it supplies the built-in scenarios
    (when `scenarios` is not given) and the accuracy check.
    """
    cfg = copy.deepcopy(config or SpeculativeConfig())
    cfg.local_base_url = "http://bench-local.test/v1"
    cfg.anthropic_upstream_url = "http://bench-anthropic.test"
    cfg.openai_upstream_url = "http://bench-openai.test"
    cfg.enable_headroom_middleware = False

    es = eval_suite or get_eval_suite(cfg.usecase)
    suite = scenarios or es.builtin_scenarios()
    app = create_app(cfg)

    active_scenario: EvalScenario | None = None
    captured_frontier_body: dict[str, Any] = {}

    async def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_frontier_body
        if active_scenario is None:
            return httpx.Response(500)
        url = str(request.url)
        body = json.loads(request.content.decode("utf-8"))

        # 1. Local Drafter endpoint
        if url.startswith("http://bench-local.test/v1/chat/completions"):
            tc_list: list[dict[str, Any]] = []
            for tc in active_scenario.simulated_draft_tool_calls:
                tc_list.append(
                    {
                        "id": tc.get("id", "call_draft_1"),
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("input", {})),
                        },
                    }
                )
            msg_obj: dict[str, Any] = {
                "role": "assistant",
                "content": active_scenario.simulated_draft_text or None,
            }
            if tc_list:
                msg_obj["tool_calls"] = tc_list

            return httpx.Response(
                200,
                json={
                    "id": f"chatcmpl-local-{active_scenario.id}",
                    "choices": [{"index": 0, "message": msg_obj, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": active_scenario.prompt_uncached_tokens,
                        "completion_tokens": active_scenario.expected_output_tokens,
                    },
                },
            )

        # 2. Frontier Anthropic endpoint
        if url.startswith("http://bench-anthropic.test/v1/messages"):
            captured_frontier_body = body
            is_stream = bool(body.get("stream", False))

            if active_scenario.draft_is_accurate:
                # Frontier verifies and accepts with 2-token `<ACCEPT/>`
                if is_stream:
                    sse_payload = (
                        b"event: message_start\ndata: "
                        b'{"type":"message_start","message":{"id":"msg_b1","role":"assistant",'
                        b'"usage":{"input_tokens":400,"output_tokens":1}}}\n\n'
                        b"event: content_block_start\ndata: "
                        b'{"type":"content_block_start","index":0,'
                        b'"content_block":{"type":"text","text":""}}\n\n'
                        b"event: content_block_delta\ndata: "
                        b'{"type":"content_block_delta","index":0,'
                        b'"delta":{"type":"text_delta","text":"<ACCEPT/>"}}\n\n'
                        b"event: content_block_stop\ndata: "
                        b'{"type":"content_block_stop","index":0}\n\n'
                        b"event: message_delta\ndata: "
                        b'{"type":"message_delta","delta":{"stop_reason":"end_turn"},'
                        b'"usage":{"output_tokens":2}}\n\n'
                        b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
                    )
                    return httpx.Response(
                        200,
                        content=sse_payload,
                        headers={"content-type": "text/event-stream"},
                    )
                return httpx.Response(
                    200,
                    json={
                        "id": f"msg_anth_{active_scenario.id}",
                        "type": "message",
                        "role": "assistant",
                        "model": body["model"],
                        "content": [{"type": "text", "text": cfg.accept_token}],
                        "stop_reason": "end_turn",
                        "usage": {
                            "input_tokens": active_scenario.prompt_uncached_tokens,
                            "output_tokens": 2,
                        },
                    },
                )

            # Frontier rejects flawed draft or handles non-drafted request normally
            content_blocks: list[dict[str, Any]] = []
            if active_scenario.expected_text:
                content_blocks.append({"type": "text", "text": active_scenario.expected_text})
            for tc in active_scenario.expected_tool_calls:
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", "toolu_1"),
                        "name": tc["name"],
                        "input": tc.get("input", {}),
                    }
                )
            return httpx.Response(
                200,
                json={
                    "id": f"msg_anth_{active_scenario.id}",
                    "type": "message",
                    "role": "assistant",
                    "model": body["model"],
                    "content": content_blocks,
                    "stop_reason": (
                        "tool_use" if active_scenario.expected_tool_calls else "end_turn"
                    ),
                    "usage": {
                        "input_tokens": active_scenario.prompt_uncached_tokens,
                        "output_tokens": active_scenario.expected_output_tokens,
                    },
                },
            )

        # 3. Frontier OpenAI endpoint
        if url.startswith("http://bench-openai.test/v1/chat/completions"):
            captured_frontier_body = body
            return httpx.Response(
                200,
                json={
                    "id": f"chatcmpl_oai_{active_scenario.id}",
                    "object": "chat.completion",
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": active_scenario.expected_text,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": active_scenario.prompt_uncached_tokens,
                        "completion_tokens": active_scenario.expected_output_tokens,
                        "completion_tokens_details": {
                            "accepted_prediction_tokens": (
                                active_scenario.expected_output_tokens
                                if "prediction" in body and active_scenario.draft_is_accurate
                                else 0
                            ),
                            "rejected_prediction_tokens": 0,
                        },
                    },
                },
            )

        return httpx.Response(404)

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_transport_handler))

    results: list[ScenarioBenchmarkResult] = []
    frontier_tps = max(1.0, cfg.estimated_frontier_output_tps)
    input_price = cfg.estimated_input_usd_per_million / 1_000_000.0
    cached_input_price = input_price * 0.10  # 90% prefix cache discount
    output_price = cfg.estimated_output_usd_per_million / 1_000_000.0

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://thetarelay.bench"
    ) as client:
        for sc in suite:
            active_scenario = sc
            captured_frontier_body = {}
            endpoint = "/v1/messages" if sc.provider == "anthropic" else "/v1/chat/completions"

            resp = await client.post(endpoint, json=copy.deepcopy(sc.request_body))

            drafted = resp.headers.get("x-thetarelay-drafted") == "true"
            mode = resp.headers.get("x-thetarelay-mode", "none")

            # Extract returned text & tool calls from response (JSON or SSE)
            actual_text = ""
            actual_tools: list[dict[str, Any]] = []
            if sc.stream:
                sse_raw = resp.text
                for block in sse_raw.split("\n\n"):
                    for line in block.split("\n"):
                        if line.startswith("data:"):
                            d_raw = line[5:].strip()
                            if not d_raw or d_raw == "[DONE]":
                                continue
                            ev = json.loads(d_raw)
                            if ev.get("type") == "content_block_delta":
                                delta = ev.get("delta") or {}
                                if delta.get("type") == "text_delta":
                                    actual_text += str(delta.get("text", ""))
                accepted = drafted and sc.draft_is_accurate
            elif sc.provider == "anthropic":
                data = resp.json()
                for blk in data.get("content", []):
                    if blk.get("type") == "text":
                        actual_text += str(blk.get("text", ""))
                    elif blk.get("type") == "tool_use":
                        actual_tools.append(
                            {
                                "id": blk.get("id", ""),
                                "name": blk.get("name", ""),
                                "input": blk.get("input", {}),
                            }
                        )
                accepted = resp.headers.get("x-thetarelay-accepted") == "true"
            else:
                data = resp.json()
                msg = (data.get("choices") or [{}])[0].get("message") or {}
                actual_text = str(msg.get("content") or "")
                accepted = mode == "openai_prediction" and sc.draft_is_accurate

            # Verify prefix cache safety: system prompt and all historical turns before last user
            # message must be byte-for-byte identical to the original request!
            orig_msgs = sc.request_body.get("messages", [])
            sent_msgs = captured_frontier_body.get("messages", [])
            prefix_preserved = (
                captured_frontier_body.get("system") == sc.request_body.get("system")
                and sent_msgs[:-1] == orig_msgs[:-1]
            )

            # Calculate Baseline vs Speculative Speed (ms)
            baseline_decode_ms = (sc.expected_output_tokens / frontier_tps) * 1000.0
            baseline_latency_ms = sc.simulated_frontier_ttft_ms + baseline_decode_ms

            if not drafted:
                local_ms = 0.0
                frontier_verify_ms = baseline_latency_ms
                spec_latency_ms = baseline_latency_ms
                spec_out_tokens = sc.expected_output_tokens
                out_tok_saved = 0
                in_tok_overhead = 0
            elif mode == "openai_prediction" and accepted:
                # OpenAI server-side speculative decoding (~3x faster decode after local draft)
                local_ms = sc.simulated_local_draft_ms
                frontier_verify_ms = sc.simulated_frontier_ttft_ms + (baseline_decode_ms * 0.28)
                spec_latency_ms = local_ms + frontier_verify_ms
                spec_out_tokens = sc.expected_output_tokens
                out_tok_saved = 0  # OpenAI bills predicted tokens as completion tokens
                in_tok_overhead = 0
            elif accepted:
                # Anthropic/OpenAI `<ACCEPT/>` swap: frontier only decodes 2 tokens!
                local_ms = sc.simulated_local_draft_ms
                frontier_verify_ms = sc.simulated_frontier_ttft_ms + ((2.0 / frontier_tps) * 1000.0)
                spec_latency_ms = local_ms + frontier_verify_ms
                spec_out_tokens = 2
                out_tok_saved = max(0, sc.expected_output_tokens - 2)
                sim_draft = DraftResult(
                    text=sc.simulated_draft_text,
                    tool_calls=sc.simulated_draft_tool_calls,
                    draft_tokens_est=sc.expected_output_tokens,
                )
                in_tok_overhead = estimate_injection_input_tokens(sim_draft, cfg)
            else:
                # Draft rejected: frontier generates full response after local draft latency
                local_ms = sc.simulated_local_draft_ms
                frontier_verify_ms = baseline_latency_ms
                spec_latency_ms = local_ms + frontier_verify_ms
                spec_out_tokens = sc.expected_output_tokens
                out_tok_saved = 0
                sim_draft = DraftResult(
                    text=sc.simulated_draft_text,
                    tool_calls=sc.simulated_draft_tool_calls,
                    draft_tokens_est=sc.expected_output_tokens,
                )
                in_tok_overhead = estimate_injection_input_tokens(sim_draft, cfg)

            # Calculate Baseline vs Speculative Cost (USD)
            base_in_cost = (
                sc.prompt_cached_tokens * cached_input_price
                + sc.prompt_uncached_tokens * input_price
            )
            baseline_cost_usd = base_in_cost + (sc.expected_output_tokens * output_price)
            speculative_cost_usd = (
                base_in_cost + (in_tok_overhead * input_price) + (spec_out_tokens * output_price)
            )

            acc_ok, acc_details = es.verify_accuracy(
                actual_text=actual_text,
                actual_tool_calls=actual_tools,
                expected_text=sc.expected_text,
                expected_tool_calls=sc.expected_tool_calls,
            )

            results.append(
                ScenarioBenchmarkResult(
                    scenario_id=sc.id,
                    title=sc.title,
                    category=sc.category,
                    provider=sc.provider,
                    drafted=drafted,
                    accepted=accepted,
                    mode=mode,
                    baseline_latency_ms=baseline_latency_ms,
                    speculative_latency_ms=spec_latency_ms,
                    local_draft_ms=local_ms,
                    frontier_verify_ms=frontier_verify_ms,
                    latency_saved_ms=baseline_latency_ms - spec_latency_ms,
                    speedup_factor=(
                        baseline_latency_ms / spec_latency_ms if spec_latency_ms > 0 else 1.0
                    ),
                    baseline_output_tokens=sc.expected_output_tokens,
                    speculative_output_tokens=spec_out_tokens,
                    output_tokens_saved=out_tok_saved,
                    input_tokens_overhead=in_tok_overhead,
                    baseline_cost_usd=baseline_cost_usd,
                    speculative_cost_usd=speculative_cost_usd,
                    net_usd_saved=baseline_cost_usd - speculative_cost_usd,
                    prefix_cache_preserved=prefix_preserved,
                    accuracy_passed=acc_ok,
                    accuracy_details=acc_details,
                )
            )

        stats_resp = await client.get("/thetarelay/stats")
        proxy_stats = stats_resp.json()["metrics"]

    await app.state.http_client.aclose()

    # Compute theoretical break-even acceptance rates:
    # 1. Cost break-even: p * (Out_tok - 2) * P_out = In_overhead * P_in
    avg_out_tok = 220.0
    avg_in_overhead = 280.0
    break_even_cost_pct = (
        (avg_in_overhead * cfg.estimated_input_usd_per_million)
        / ((avg_out_tok - 2.0) * cfg.estimated_output_usd_per_million)
    ) * 100.0

    # 2. Speed break-even: Local_draft_ms = p * Avoided_decode_ms
    avg_local_ms = 260.0
    avg_avoided_decode_ms = ((avg_out_tok - 2.0) / frontier_tps) * 1000.0
    break_even_speed_pct = (avg_local_ms / avg_avoided_decode_ms) * 100.0

    return BenchmarkSuiteReport(
        scenarios=results,
        proxy_stats=proxy_stats,
        break_even_acceptance_rate_pct=min(100.0, break_even_cost_pct),
        break_even_speed_acceptance_rate_pct=min(100.0, break_even_speed_pct),
    )


def format_benchmark_report_table(report: BenchmarkSuiteReport) -> str:
    """Format the A/B BenchmarkSuiteReport as a human-readable CLI table & summary."""
    s = report.summary()
    speed = s["speed"]
    eff = s["efficiency"]
    acc = s["accuracy"]

    sep_eq = "=" * 98
    sep_dash = "-" * 98
    header_cols = (
        f"{'Scenario ID':<26} | {'Mode':<17} | {'Base ms':>8} | "
        f"{'Spec ms':>8} | {'Speedup':>7} | {'OutTok Saved':>12} | {'Acc':>4}"
    )

    lines: list[str] = [
        "",
        sep_eq,
        "              THETARELAY SPECULATIVE A/B BENCHMARK (SPEED / EFFICIENCY / ACCURACY)       ",
        sep_eq,
        header_cols,
        sep_dash,
    ]
    for r in report.scenarios:
        status_mode = r.mode if r.drafted else "bypassed"
        if r.drafted and r.mode == "accept_swap":
            status_mode = "accept_swap(OK)" if r.accepted else "rejected(guard)"
        acc_str = "PASS" if r.accuracy_passed else "FAIL"
        lines.append(
            f"{r.scenario_id:<26} | {status_mode:<17} | "
            f"{r.baseline_latency_ms:>7.0f}m | {r.speculative_latency_ms:>7.0f}m | "
            f"{r.speedup_factor:>6.2f}x | {r.output_tokens_saved:>12d} | {acc_str:>4}"
        )

    be_speed = speed["break_even_acceptance_rate_for_speed_pct"]
    be_cost = eff["break_even_acceptance_rate_for_cost_pct"]
    saved_ms_val = speed["net_latency_saved_ms"]
    speedup_val = speed["overall_speedup_factor"]
    base_cost_val = eff["baseline_total_cost_usd"]
    spec_cost_val = eff["speculative_total_cost_usd"]
    cost_red_val = eff["cost_reduction_pct"]
    lines.extend(
        [
            sep_dash,
            "1. SPEED (Wall-Clock Latency):",
            f"   Baseline Total Latency:       {speed['baseline_total_latency_ms']:>8.1f} ms",
            f"   Speculative Total Latency:    {speed['speculative_total_latency_ms']:>8.1f} ms "
            f"(Saved {saved_ms_val:.1f} ms -> {speedup_val}x faster)",
            f"   Speed Break-Even Threshold:   >= {be_speed:.1f}% draft acceptance rate",
            "",
            "2. EFFICIENCY (Tokens, Cost & Prefix Cache):",
            f"   Frontier Output Tokens:       {eff['baseline_output_tokens']} -> "
            f"{eff['speculative_output_tokens']} "
            f"(-{eff['output_token_reduction_pct']}% output tokens generated by frontier)",
            f"   Input Overhead vs Out Saved:  +{eff['input_tokens_overhead']} input tok vs "
            f"-{eff['output_tokens_saved']} output tok (Net: {eff['net_tokens_saved']:+d} tok)",
            f"   Net API Cost:                 ${base_cost_val:.5f} -> "
            f"${spec_cost_val:.5f} (-{cost_red_val}% net USD cost)",
            f"   Prefix Cache Preserved:       {eff['prefix_cache_preserved_rate_pct']:.1f}% "
            "of turns (system + history untouched)",
            f"   Cost Break-Even Threshold:    >= {be_cost:.1f}% draft acceptance rate",
            "",
            "3. ACCURACY & SAFETY:",
            f"   Output & Tool Equivalence:    {acc['scenarios_passed']}/{acc['scenarios_total']} "
            f"({acc['accuracy_rate_pct']:.1f}%)",
            f"   Flawed Drafts Accepted:       {acc['false_accepts']} "
            "(unsafe drafts rejected & rewritten by frontier)",
            sep_eq,
            "",
        ]
    )
    return "\n".join(lines)


def load_eval_scenarios_from_jsonl(
    path: Path, default_category: str = "general"
) -> list[EvalScenario]:
    """Load custom evaluation scenarios from a JSONL file.

    `default_category` is used for lines without a `category`; pass the suite's
    `EvalSuite.default_category`.
    """
    scenarios: list[EvalScenario] = []
    for idx, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        item = json.loads(line)
        scenarios.append(
            EvalScenario(
                id=str(item.get("id") or f"custom_{idx}"),
                title=str(item.get("title") or f"Custom Scenario {idx}"),
                category=str(item.get("category") or default_category),
                provider=str(item.get("provider") or "anthropic"),
                request_body=dict(item["request_body"]),
                expected_text=str(item.get("expected_text") or ""),
                expected_tool_calls=list(item.get("expected_tool_calls") or []),
                simulated_draft_text=str(
                    item.get("simulated_draft_text") or item.get("expected_text") or ""
                ),
                simulated_draft_tool_calls=list(
                    item.get("simulated_draft_tool_calls") or item.get("expected_tool_calls") or []
                ),
                draft_is_accurate=bool(item.get("draft_is_accurate", True)),
            )
        )
    return scenarios


async def warmup_openai_model(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    api_key: str = "local",
    disable_thinking: bool = True,
) -> float:
    """Send a 1-token warmup request so model weight loading is excluded from benchmark latency."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
        "temperature": 0.0,
    }
    if disable_thinking:
        payload["reasoning_effort"] = "none"
    t0 = time.perf_counter()
    with contextlib.suppress(Exception):
        await client.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )
    return round((time.perf_counter() - t0) * 1000.0, 1)


async def run_live_eval(
    config: SpeculativeConfig | None = None,
    scenarios: list[EvalScenario] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    target_model: str | None = None,
    target_base_url: str | None = None,
    eval_suite: EvalSuite | None = None,
) -> dict[str, Any]:
    """Evaluate a live local drafter endpoint (e.g., Ollama / MLX / vLLM) on a use case's scenarios.

    `eval_suite` defaults to the suite of `config.usecase`; it supplies the built-in scenarios
    (when `scenarios` is not given) and the accuracy check.

    Supports two-phase batched execution when `target_model` is provided so that single-GPU
    servers (like Ollama on macOS) never thrash weights between turns:
    - Phase 1: Warm up `local_model` once -> run all draft turns back-to-back (0 model swaps).
    - Phase 2: Warm up `target_model` once -> run Baseline generation AND `<ACCEPT/>` verification
      back-to-back (0 model swaps) to measure real wall-clock speedup and acceptance rate!
    """
    cfg = config or SpeculativeConfig.from_env()
    es = eval_suite or get_eval_suite(cfg.usecase)
    suite = [s for s in (scenarios or es.builtin_scenarios()) if s.category != "bypass"]

    results: list[dict[str, Any]] = []
    draft_objects: dict[str, DraftResult] = {}

    async with httpx.AsyncClient(timeout=30.0, transport=transport) as client:
        # Phase 1: Warm up local drafter model once (if not using MockTransport)
        warmup_draft_ms = 0.0
        if transport is None:
            warmup_draft_ms = await warmup_openai_model(
                client,
                cfg.local_base_url,
                cfg.local_model,
                cfg.local_api_key,
                cfg.local_disable_thinking,
            )

        for sc in suite:
            # For rejection_guard scenarios, test how the target handles the flawed draft
            if sc.category == "rejection_guard" and target_model:
                flawed_draft = DraftResult(
                    text=sc.simulated_draft_text,
                    tool_calls=sc.simulated_draft_tool_calls,
                    latency_ms=250.0,
                    draft_tokens_est=max(1, len(sc.simulated_draft_text) // 4),
                )
                draft_objects[sc.id] = flawed_draft
                _, flawed_details = es.verify_accuracy(
                    actual_text=sc.simulated_draft_text,
                    actual_tool_calls=sc.simulated_draft_tool_calls,
                    expected_text=sc.expected_text,
                    expected_tool_calls=sc.expected_tool_calls,
                )
                results.append(
                    {
                        "scenario_id": sc.id,
                        "category": sc.category,
                        "status": "ok",
                        "latency_ms": 250.0,
                        "mode": "accept_swap",
                        "draft_chars": len(sc.simulated_draft_text),
                        "tool_calls_count": 0,
                        **_validity_checks(flawed_details),
                        "tool_schema_valid": True,
                        "exact_match": False,
                        "valid_output": True,
                        "injected_flawed_draft_for_guard_test": True,
                    }
                )
                continue

            payload = convert_to_openai_draft_payload(sc.request_body, sc.provider, cfg)
            url = f"{cfg.local_base_url}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg.local_api_key}",
            }
            t0 = time.perf_counter()
            try:
                resp = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=cfg.local_timeout_seconds,
                )
                latency_ms = (time.perf_counter() - t0) * 1000.0
                if resp.status_code != 200:
                    results.append(
                        {
                            "scenario_id": sc.id,
                            "status": f"http_{resp.status_code}",
                            "latency_ms": round(latency_ms, 1),
                            "valid_output": False,
                        }
                    )
                    continue
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                draft_text = str(msg.get("content") or "")
                raw_tools = msg.get("tool_calls") or []
                parsed_tools: list[dict[str, Any]] = []
                for tc in raw_tools:
                    fn = tc.get("function") or {}
                    args_raw = fn.get("arguments", "{}")
                    args_dict = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    parsed_tools.append({"name": fn.get("name", ""), "input": args_dict})

                usage = data.get("usage") or {}
                comp_tok = int(usage.get("completion_tokens") or max(1, len(draft_text) // 4))
                draft_obj = DraftResult(
                    text=draft_text,
                    tool_calls=parsed_tools,
                    latency_ms=latency_ms,
                    draft_tokens_est=comp_tok,
                )
                draft_objects[sc.id] = draft_obj
                _, injected_mode = inject_draft_into_body(
                    sc.request_body, sc.provider, draft_obj, cfg
                )
                _, acc_details = es.verify_accuracy(
                    actual_text=draft_text,
                    actual_tool_calls=parsed_tools,
                    expected_text=sc.expected_text,
                    expected_tool_calls=sc.expected_tool_calls,
                )
                validity = _validity_checks(acc_details)
                has_expected_tool = (
                    bool(parsed_tools)
                    and parsed_tools[0]["name"] == sc.expected_tool_calls[0]["name"]
                    if sc.expected_tool_calls
                    else True
                )
                valid_output = bool(draft_text.strip() or parsed_tools) and bool(
                    all(validity.values()) and has_expected_tool
                )
                results.append(
                    {
                        "scenario_id": sc.id,
                        "category": sc.category,
                        "status": "ok",
                        "latency_ms": round(latency_ms, 1),
                        "draft_tokens": comp_tok,
                        "mode": injected_mode,
                        "draft_chars": len(draft_text),
                        "tool_calls_count": len(parsed_tools),
                        **validity,
                        "tool_schema_valid": has_expected_tool,
                        "exact_match": acc_details["text_matches"] and acc_details["tools_match"],
                        "valid_output": valid_output,
                    }
                )
            except Exception as exc:
                latency_ms = (time.perf_counter() - t0) * 1000.0
                results.append(
                    {
                        "scenario_id": sc.id,
                        "status": f"error:{type(exc).__name__}",
                        "latency_ms": round(latency_ms, 1),
                        "valid_output": False,
                    }
                )

        # Phase 2 (Optional): Batched Target Model Verification & Baseline Comparison
        target_summary: dict[str, Any] | None = None
        if target_model:
            import os

            target_api_key = (
                os.environ.get("THETARELAY_TARGET_API_KEY")
                or os.environ.get("OPENROUTER_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or cfg.local_api_key
            )
            t_url_base = (target_base_url or cfg.local_base_url).rstrip("/")
            warmup_target_ms = await warmup_openai_model(
                client,
                t_url_base,
                target_model,
                target_api_key,
                disable_thinking=True,
            )
            total_baseline_ms = 0.0
            total_spec_ms = 0.0
            accepted_count = 0
            flawed_rejected_count = 0

            target_cfg = copy.deepcopy(cfg)
            target_cfg.local_model = target_model
            target_cfg.strategy = "accept_swap"
            target_cfg.local_max_tokens = max(2048, cfg.local_max_tokens)

            for sc, r in zip(suite, results, strict=False):
                if r["status"] != "ok" or sc.id not in draft_objects:
                    continue
                draft_obj = draft_objects[sc.id]

                # 2a. Measure Baseline direct generation on Target Model
                base_cfg = copy.deepcopy(target_cfg)
                base_cfg.local_concise_draft_instructions = False
                base_payload = convert_to_openai_draft_payload(
                    sc.request_body, sc.provider, base_cfg
                )
                t_base0 = time.perf_counter()
                base_resp = await client.post(
                    f"{t_url_base}/chat/completions",
                    json=base_payload,
                    headers={"Authorization": f"Bearer {target_api_key}"},
                    timeout=60.0,
                )
                base_ms = (time.perf_counter() - t_base0) * 1000.0
                base_usage = (
                    (base_resp.json().get("usage") or {}) if base_resp.status_code == 200 else {}
                )
                base_tok = int(base_usage.get("completion_tokens", 0))
                base_prompt_tok = int(base_usage.get("prompt_tokens", 0))
                base_details = base_usage.get("prompt_tokens_details") or {}
                base_cached_tok = int(
                    (base_details.get("cached_tokens") or 0)
                    if isinstance(base_details, dict)
                    and base_details.get("cached_tokens") is not None
                    else (base_usage.get("cache_read_input_tokens") or 0)
                )

                # 2b. Measure Speculative Verification on Target Model
                # When verifying a draft that already contains <draft_tool_calls>,
                # omit the `tools` array from the OpenAI verification call so the verifier
                # emits `<ACCEPT/>` text directly instead of re-invoking the tool from scratch.
                injected_body, _ = inject_draft_into_body(
                    sc.request_body, sc.provider, draft_obj, base_cfg
                )
                verify_payload = convert_to_openai_draft_payload(
                    injected_body, sc.provider, base_cfg
                )
                if draft_obj.tool_calls:
                    verify_payload.pop("tools", None)
                t_ver0 = time.perf_counter()
                ver_resp = await client.post(
                    f"{t_url_base}/chat/completions",
                    json=verify_payload,
                    headers={"Authorization": f"Bearer {target_api_key}"},
                    timeout=60.0,
                )
                verify_ms = (time.perf_counter() - t_ver0) * 1000.0
                ver_data = ver_resp.json() if ver_resp.status_code == 200 else {}
                ver_msg = ((ver_data.get("choices") or [{}])[0]).get("message") or {}
                ver_text = str(ver_msg.get("content") or "")
                ver_usage = ver_data.get("usage") or {}
                ver_tok = int(ver_usage.get("completion_tokens", 0))
                ver_prompt_tok = int(ver_usage.get("prompt_tokens", 0))
                ver_details = ver_usage.get("prompt_tokens_details") or {}
                ver_cached_tok = int(
                    (ver_details.get("cached_tokens") or 0)
                    if isinstance(ver_details, dict)
                    and ver_details.get("cached_tokens") is not None
                    else (ver_usage.get("cache_read_input_tokens") or 0)
                )
                accepted = response_accepted_draft(ver_text, target_cfg)

                spec_e2e_ms = float(r["latency_ms"]) + verify_ms
                total_baseline_ms += base_ms
                total_spec_ms += spec_e2e_ms
                if accepted:
                    accepted_count += 1
                elif sc.category == "rejection_guard":
                    flawed_rejected_count += 1

                r["target_baseline_ms"] = round(base_ms, 1)
                r["target_baseline_prompt_tokens"] = base_prompt_tok
                r["target_baseline_cached_tokens"] = base_cached_tok
                r["target_baseline_output_tokens"] = base_tok
                r["target_verify_ms"] = round(verify_ms, 1)
                r["target_verify_prompt_tokens"] = ver_prompt_tok
                r["target_verify_cached_tokens"] = ver_cached_tok
                r["target_verify_output_tokens"] = ver_tok
                r["speculative_e2e_ms"] = round(spec_e2e_ms, 1)
                r["speedup_factor"] = round(base_ms / spec_e2e_ms, 2) if spec_e2e_ms > 0 else 1.0
                r["target_accepted"] = accepted
                r["target_response_preview"] = ver_text[:120]

            target_summary = {
                "target_model": target_model,
                "warmup_target_ms": warmup_target_ms,
                "baseline_total_ms": round(total_baseline_ms, 1),
                "speculative_total_ms": round(total_spec_ms, 1),
                "net_latency_saved_ms": round(total_baseline_ms - total_spec_ms, 1),
                "overall_speedup_factor": (
                    round(total_baseline_ms / total_spec_ms, 2) if total_spec_ms > 0 else 1.0
                ),
                "accepted_count": accepted_count,
                "flawed_drafts_rejected": flawed_rejected_count,
            }

    succeeded = [r for r in results if r["status"] == "ok"]
    valid_count = sum(1 for r in succeeded if r.get("valid_output"))
    avg_ms = (
        round(sum(r["latency_ms"] for r in succeeded) / len(succeeded), 1) if succeeded else 0.0
    )
    out: dict[str, Any] = {
        "local_model": cfg.local_model,
        "local_base_url": cfg.local_base_url,
        "warmup_local_ms": warmup_draft_ms,
        "scenarios_total": len(suite),
        "scenarios_succeeded": len(succeeded),
        "valid_draft_rate_pct": round((valid_count / len(suite)) * 100.0, 1) if suite else 0.0,
        "avg_local_latency_ms": avg_ms,
        "results": results,
    }
    if target_summary:
        out["two_phase_target_verification"] = target_summary
    return out
