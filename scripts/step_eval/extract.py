"""Add the unique tool calls of captured agent sessions to the labelled step dataset.

Usage: uv run python scripts/step_eval/extract.py AGENT BODIES.jsonl [BODIES.jsonl ...]

Reads request bodies (one JSON object per line, as written by the app and routing evaluations)
and appends each tool call not yet in calls.jsonl with an empty label, for review. Existing
lines and labels are kept. Arguments of non-shell tools are cut to their first 80 characters per
value: their group depends only on the tool name, and file contents do not belong in the dataset.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from thetarelay.steps import SHELL_TOOLS

DATASET = Path(__file__).with_name("calls.jsonl")


def calls_in(body: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for msg in body.get("messages") or []:
        for tc in msg.get("tool_calls") or []:  # OpenAI
            fn = tc.get("function") or {}
            try:
                out.append((fn.get("name", ""), json.loads(fn.get("arguments") or "{}")))
            except ValueError:
                continue
        content = msg.get("content")
        for block in content if isinstance(content, list) else []:  # Anthropic
            if isinstance(block, dict) and block.get("type") == "tool_use":
                out.append((block.get("name", ""), block.get("input") or {}))
    return out


def shorten(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool.lower() in SHELL_TOOLS:
        return args
    return {k: v[:80] if isinstance(v, str) else v for k, v in args.items()}


def main(agent: str, paths: list[str]) -> None:
    lines = DATASET.read_text().splitlines() if DATASET.exists() else []
    seen = {(r["tool"], r["arguments"]) for r in map(json.loads, lines)}
    added = 0
    with DATASET.open("a") as out:
        for path in paths:
            for line in Path(path).read_text().splitlines():
                try:
                    body = json.loads(line)
                except ValueError:
                    continue
                body = body.get("body", body)
                for tool, args in calls_in(body):
                    key = (tool, json.dumps(shorten(tool, args), ensure_ascii=False))
                    if key in seen:
                        continue
                    seen.add(key)
                    row = {"agent": agent, "tool": key[0], "arguments": key[1], "label": ""}
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    added += 1
    print(f"added {added} calls; {len(seen)} in {DATASET}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
