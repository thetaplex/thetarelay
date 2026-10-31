"""CLI entry point for ThetaRelay speculative proxy."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import uvicorn

from .config import SpeculativeConfig
from .evals import (
    format_benchmark_report_table,
    load_eval_scenarios_from_jsonl,
    run_ab_benchmark,
    run_live_eval,
)
from .humaneval import load_humaneval_from_file, run_humaneval_benchmark
from .proxy import create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="thetarelay",
        description=(
            "ThetaRelay: Speculative Local-to-Frontier Proxy & Headroom Extension for Coding Agents"
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    # `thetarelay proxy`
    proxy_parser = subparsers.add_parser("proxy", help="Start the standalone ThetaRelay proxy")
    proxy_parser.add_argument("--host", default=None, help="Bind host (default: 127.0.0.1)")
    proxy_parser.add_argument("--port", type=int, default=None, help="Bind port (default: 8788)")
    proxy_parser.add_argument(
        "--local-url",
        default=None,
        help="Local OpenAI-compatible API URL (default: http://127.0.0.1:11434/v1)",
    )
    proxy_parser.add_argument(
        "--local-model",
        default=None,
        help="Local draft model name (default: qwen2.5-coder:7b)",
    )
    proxy_parser.add_argument(
        "--strategy",
        choices=["auto", "accept_swap", "prediction", "context_only"],
        default=None,
        help="Speculative verification strategy (default: auto)",
    )
    proxy_parser.add_argument(
        "--anthropic-upstream",
        default=None,
        help="Upstream Anthropic base URL (e.g. https://api.anthropic.com or http://127.0.0.1:8787)",
    )
    proxy_parser.add_argument(
        "--openai-upstream",
        default=None,
        help="Upstream OpenAI base URL (e.g. https://api.openai.com or http://127.0.0.1:8787)",
    )
    proxy_parser.add_argument(
        "--no-headroom-middleware",
        action="store_true",
        help=(
            "Disable embedded Headroom CompressionMiddleware "
            "(use when chaining with `headroom proxy`)"
        ),
    )

    # `thetarelay bench`
    bench_parser = subparsers.add_parser(
        "bench",
        help="Run the deterministic A/B benchmark suite (speed, efficiency, accuracy)",
    )
    bench_parser.add_argument(
        "--json",
        action="store_true",
        help="Output the full benchmark report as JSON",
    )
    bench_parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="Optional path to custom scenarios JSONL file",
    )
    bench_parser.add_argument(
        "--strategy",
        choices=["auto", "accept_swap", "prediction", "context_only"],
        default=None,
        help="Override speculative verification strategy",
    )

    # `thetarelay eval`
    eval_parser = subparsers.add_parser(
        "eval",
        help="Evaluate a live local draft model (Ollama/MLX/vLLM) on coding agent tasks",
    )
    eval_parser.add_argument(
        "--local-url",
        default=None,
        help="Local OpenAI-compatible API URL (default: http://127.0.0.1:11434/v1)",
    )
    eval_parser.add_argument(
        "--local-model",
        default=None,
        help="Local draft model name (default: qwen2.5-coder:7b)",
    )
    eval_parser.add_argument(
        "--target-model",
        default=None,
        help="Optional target model for two-phase verification (e.g. qwen3.8:27b-mlx)",
    )
    eval_parser.add_argument(
        "--target-url",
        default=None,
        help="Optional target OpenAI-compatible base URL (defaults to --local-url)",
    )
    eval_parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="Optional path to custom scenarios or HumanEval JSONL(.gz) file",
    )
    eval_parser.add_argument(
        "--benchmark",
        choices=["scenarios", "humaneval"],
        default="scenarios",
        help="Evaluation benchmark suite to run (default: scenarios)",
    )
    eval_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of benchmark problems to run",
    )
    eval_parser.add_argument(
        "--json",
        action="store_true",
        help="Output evaluation results as JSON",
    )

    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["proxy", *(argv or [])])

    cfg = SpeculativeConfig.from_env()
    if getattr(args, "host", None):
        cfg.host = args.host
    if getattr(args, "port", None):
        cfg.port = args.port
    if getattr(args, "local_url", None):
        cfg.local_base_url = args.local_url.rstrip("/")
    if getattr(args, "local_model", None):
        cfg.local_model = args.local_model
    if getattr(args, "strategy", None):
        cfg.strategy = args.strategy
    if getattr(args, "anthropic_upstream", None):
        cfg.anthropic_upstream_url = args.anthropic_upstream.rstrip("/")
    if getattr(args, "openai_upstream", None):
        cfg.openai_upstream_url = args.openai_upstream.rstrip("/")
    if getattr(args, "no_headroom_middleware", False):
        cfg.enable_headroom_middleware = False

    if args.command == "bench":
        scenarios = (
            load_eval_scenarios_from_jsonl(args.dataset) if getattr(args, "dataset", None) else None
        )
        report = asyncio.run(run_ab_benchmark(cfg, scenarios=scenarios))
        if getattr(args, "json", False):
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(format_benchmark_report_table(report))
        return

    if args.command == "eval":
        if getattr(args, "benchmark", "scenarios") == "humaneval":
            he_problems = (
                load_humaneval_from_file(args.dataset) if getattr(args, "dataset", None) else None
            )
            he_report = asyncio.run(
                run_humaneval_benchmark(
                    cfg,
                    problems=he_problems,
                    limit=getattr(args, "limit", None),
                    target_model=getattr(args, "target_model", None),
                    target_base_url=getattr(args, "target_url", None),
                )
            )
            print(json.dumps(he_report, indent=2))
            return

        scenarios = (
            load_eval_scenarios_from_jsonl(args.dataset) if getattr(args, "dataset", None) else None
        )
        eval_report = asyncio.run(
            run_live_eval(
                cfg,
                scenarios=scenarios,
                target_model=getattr(args, "target_model", None),
                target_base_url=getattr(args, "target_url", None),
            )
        )
        print(json.dumps(eval_report, indent=2))
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = create_app(cfg)
    print(
        f"""
╔══════════════════════════════════════════════════════════════════════╗
║                    THETARELAY SPECULATIVE PROXY                       ║
╠══════════════════════════════════════════════════════════════════════╣
║  Listening:        http://{cfg.host}:{cfg.port:<5}                                ║
║  Local Drafter:    {cfg.local_model:<49} ║
║  Local Endpoint:   {cfg.local_base_url:<49} ║
║  Strategy:         {cfg.strategy:<49} ║
║  Headroom In-Proc: {"ENABLED" if cfg.enable_headroom_middleware else "DISABLED":<49} ║
╠══════════════════════════════════════════════════════════════════════╣
║  UPSTREAM TARGETS:                                                   ║
║    Anthropic:      {cfg.anthropic_upstream_url:<49} ║
║    OpenAI:         {cfg.openai_upstream_url:<49} ║
╚══════════════════════════════════════════════════════════════════════╝
""",
        file=sys.stderr,
    )
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
