"""CLI entry point for ThetaCode speculative proxy."""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from .config import SpeculativeConfig
from .proxy import create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="thetacode",
        description="ThetaCode: Speculative Local-to-Frontier Proxy & Headroom Extension for Coding Agents",
    )
    subparsers = parser.add_subparsers(dest="command")

    # `thetacode proxy`
    proxy_parser = subparsers.add_parser("proxy", help="Start the standalone ThetaCode proxy")
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
        help="Disable embedded Headroom CompressionMiddleware (use when chaining with `headroom proxy`)",
    )

    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["proxy", *(argv or [])])

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

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

    app = create_app(cfg)
    print(
        f"""
╔══════════════════════════════════════════════════════════════════════╗
║                    THETACODE SPECULATIVE PROXY                       ║
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
