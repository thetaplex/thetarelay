"""Command-line interface: `thetarelay proxy`, `judge` and `report`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

import httpx
import uvicorn

from .config import RouterConfig
from .proxy import create_app
from .router import ask_judge


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="thetarelay",
        description="ThetaRelay: route coding-agent requests to a lite or a frontier model",
    )
    sub = parser.add_subparsers(dest="command")

    proxy = sub.add_parser("proxy", help="Start the routing proxy (default command)")
    proxy.add_argument("--host", help="Bind host (default: THETARELAY_HOST or 127.0.0.1)")
    proxy.add_argument("--port", type=int, help="Bind port (default: THETARELAY_PORT or 8788)")
    proxy.add_argument("--debug", action="store_true", help="Print every routing decision")
    proxy.add_argument(
        "--no-headroom-middleware", action="store_true", help="Do not load Headroom compression"
    )

    judge = sub.add_parser("judge", help="Ask the judge about one step and print its answer")
    judge.add_argument("last_message", help="Last message of the conversation")
    judge.add_argument("--goal", default="", help="What the user asked for")

    rep = sub.add_parser("report", help="Summarise a turn log: actual cost vs frontier-only")
    rep.add_argument("path", metavar="PATH", help="Turn log file (THETARELAY_TURN_LOG)")
    rep.add_argument("--frontier-model", help="Model to price against (default: most common)")
    rep.add_argument("--offline", action="store_true", help="Do not fetch prices")
    rep.add_argument("--json", action="store_true", help="Print one JSON object")
    for k in ("input", "output", "cache-read", "cache-write"):
        rep.add_argument(f"--price-{k}", type=float, metavar="USD", help="USD per million tokens")

    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["proxy", *(argv or [])])

    if args.command == "report":
        from . import report

        prices = None
        if (args.price_input is None) != (args.price_output is None):
            parser.error("--price-input and --price-output must be given together")
        if args.price_input is not None and args.price_output is not None:
            prices = {"input": args.price_input, "output": args.price_output}
            for k in ("cache_read", "cache_write"):
                if (v := getattr(args, f"price_{k}")) is not None:
                    prices[k] = v
        try:
            r = report.build(
                args.path, frontier_model=args.frontier_model, offline=args.offline, prices=prices
            )
        except OSError as e:
            parser.error(f"cannot read {args.path}: {e}")
        print(json.dumps(r, indent=2) if args.json else report.format_text(r))
        return

    cfg = RouterConfig.from_env()

    if args.command == "judge":
        state = {"goal": args.goal, "recent_steps": "", "last_message": args.last_message}

        async def run() -> float:
            async with httpx.AsyncClient() as client:
                return await ask_judge(cfg, client, state)

        p_easy = asyncio.run(run())
        tier = "lite" if p_easy > cfg.easy_threshold else "frontier"
        print(
            json.dumps({"p_easy": round(p_easy, 4), "threshold": cfg.easy_threshold, "tier": tier})
        )
        return

    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    if args.debug:
        cfg.debug = True
    if args.no_headroom_middleware:
        cfg.enable_headroom_middleware = False

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    note = (
        " (OpenAI frontier models do not hand steps to the lite tier, so the triage step is off)"
        if cfg.route_mode_reason == "openai frontier model"
        else ""
    )
    print(
        f"thetarelay listening on http://{cfg.host}:{cfg.port}\n"
        f"  judge:    {cfg.judge_model} at {cfg.judge_url} "
        f"(lite if P(easy) > {cfg.easy_threshold})\n"
        f"  lite:     {cfg.lite_model} at {cfg.lite_base_url}\n"
        f"  frontier: {cfg.frontier_model} at {cfg.frontier_base_url}\n"
        f"  route mode: {cfg.route_mode}{note}\n"
        f"  turn log: {cfg.turn_log_path or 'off'}   debug: {'on' if cfg.debug else 'off'}",
        file=sys.stderr,
    )
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
