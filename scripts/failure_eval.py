"""Compare the old keyword failure check with the current one on captured request bodies.

Usage: uv run python scripts/failure_eval.py
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from thetarelay.router import events

# The keyword check as of `main`, before structured signals.
_OLD = re.compile(
    r"\b(FAILED|FAILURES?|Traceback|AssertionError|panicked|Exception|"
    r"[Ee]rror:|exit (code|status) [1-9]\d*|non-zero exit)"
)


def _results(body: dict[str, Any], provider: str) -> list[tuple[str, str, bool]]:
    """(call id, tool name, is_error) of every tool result, in order."""
    names: dict[str, str] = {}
    out: list[tuple[str, str, bool]] = []
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        if provider == "openai":
            for tc in msg.get("tool_calls") or []:
                names[tc.get("id")] = (tc.get("function") or {}).get("name", "")
            if msg.get("role") == "tool":
                cid = msg.get("tool_call_id")
                out.append((cid, names.get(cid, "?"), False))
        elif isinstance(msg.get("content"), list):
            for b in msg["content"]:
                if b.get("type") == "tool_use":
                    names[b.get("id")] = b.get("name", "")
                elif b.get("type") == "tool_result":
                    cid = b.get("tool_use_id")
                    out.append((cid, names.get(cid, "?"), b.get("is_error") is True))
    return out


def main() -> None:
    seen: set[tuple[str, str]] = set()
    old: Counter[tuple[str, bool]] = Counter()
    new: Counter[tuple[str, bool]] = Counter()
    changed: list[str] = []
    skipped = 0
    for path in sorted(Path("scripts/app_eval").glob("results*/**/bodies.jsonl")):
        for line in path.read_text().splitlines():
            rec = json.loads(line)
            body = rec.get("body", rec)
            provider = rec.get("provider", "openai")
            evs = [e for e in events(body, provider) if e.kind == "result"]
            res = _results(body, provider)
            if len(evs) != len(res):
                skipped += 1
                continue
            for e, (cid, tool, is_err) in zip(evs, res, strict=True):
                if (str(path.parent), cid) in seen:
                    continue
                seen.add((str(path.parent), cid))
                was = is_err or bool(_OLD.search(e.text))
                old[e.group or "?", was] += 1
                new[e.group or "?", e.failed] += 1
                if was != e.failed:
                    why = e.failure or next((x for x in e.text.splitlines() if _OLD.search(x)), "")
                    verdict = "failed -> ok" if was else "ok -> failed"
                    changed.append(f"{tool}: {verdict}: {why.strip()[:80]}")
    for title, table in (("old", old), ("new", new)):
        print(f"{title}: group, failed, ok")
        for g in sorted({g for g, _ in table}):
            print(f"  {g:14} {table[g, True]:5} {table[g, False]:5}")
    print(f"\n{len(seen)} unique results, {skipped} bodies skipped")
    print(f"{len(changed)} changed verdicts")
    print("\n".join(changed))


if __name__ == "__main__":
    main()
