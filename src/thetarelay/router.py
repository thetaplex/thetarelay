"""Decide whether a coding-agent request goes to the local (easy) or frontier (hard) tier.

Everything here is derived from the request itself: agents resend the whole conversation on
every request, so the proxy keeps no per-conversation state and survives restarts.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from .config import RouterConfig

Tier = Literal["local", "frontier"]

JUDGE_QUESTION = {
    "route": {
        "type": "choice",
        "instructions": (
            "A coding agent is about to take its next step. Can a small local model handle "
            "this step correctly, or does it need a frontier model?"
        ),
        "criteria": {
            "easy": "Routine: read or search files, list, run a command, trivial one-line "
            "edit, or a simple factual answer",
            "hard": "Needs reasoning: debugging a failure, planning, design, multi-file or "
            "subtle edits, reviews, performance or concurrency problems",
        },
    }
}

# Signs that a tool result is a failure. A failure keeps the conversation on the frontier tier.
_FAILURE = re.compile(
    r"\b(FAILED|FAILURES?|Traceback|AssertionError|panicked|Exception|"
    r"[Ee]rror:|exit (code|status) [1-9]\d*|non-zero exit)"
)
# Signs that a tool result is a passing test run (pytest, unittest, jest, cargo, go).
_PASSED = re.compile(
    r"\b\d+ passed\b|^OK\b|Tests?:\s+\d+ passed|test result: ok|^ok\s+\S+\s+[\d.]+s", re.M
)
_NOT_PASSED = re.compile(r"\b(failed|errors?)\b", re.I)
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_STEP_LIMIT = 12
_GOAL_CHARS = 300
_LAST_CHARS = 800


@dataclass
class Event:
    """One step of the conversation, in a form common to both API formats."""

    kind: Literal["user", "assistant", "call", "result"]
    text: str
    failed: bool = False


@dataclass
class Decision:
    tier: Tier
    reason: str
    judge_state: dict[str, Any] = field(default_factory=dict)
    p_easy: float | None = None
    judge_ms: float | None = None
    judge_error: str | None = None
    # The tool results just before this request include a passing test run, so a local reply
    # that ends the agent's turn is a legitimate finish, not giving up.
    tests_passed: bool = False


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
            if not isinstance(b, dict) or b.get("type") in (None, "text")
        )
    return ""


def events(body: dict[str, Any], provider: str) -> list[Event]:
    """Flatten the conversation of an Anthropic or OpenAI request into steps."""
    out: list[Event] = []
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role, content = msg.get("role"), msg.get("content")
        if provider == "openai":
            if role == "tool":
                text = _text(content)
                out.append(Event("result", text, bool(_FAILURE.search(text))))
                continue
            if text := _text(content).strip():
                out.append(Event("assistant" if role == "assistant" else "user", text))
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                out.append(Event("call", f"{fn.get('name', '')} {fn.get('arguments', '')}"))
            continue
        if isinstance(content, str):
            out.append(Event("assistant" if role == "assistant" else "user", content))
            continue
        for block in content or []:
            kind = block.get("type") if isinstance(block, dict) else None
            if kind == "text":
                out.append(Event("assistant" if role == "assistant" else "user", block["text"]))
            elif kind == "tool_use":
                args = json.dumps(block.get("input", {}), ensure_ascii=False)
                out.append(Event("call", f"{block.get('name', '')} {args}"))
            elif kind == "tool_result":
                text = _text(block.get("content", ""))
                failed = bool(block.get("is_error")) or bool(_FAILURE.search(text))
                out.append(Event("result", text, failed))
    # Agent harness reminders are not part of the task.
    for e in out:
        if e.kind == "user":
            e.text = _REMINDER.sub("", e.text).strip()
    return [e for e in out if e.text.strip() or e.kind == "result"]


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _step_line(e: Event) -> str:
    if e.kind == "result":
        lines = e.text.strip().splitlines() or [""]
        if e.failed:
            hit = next((ln for ln in lines if _FAILURE.search(ln)), lines[0])
            return "result FAILED: " + _one_line(hit, 120)
        return "result ok: " + _one_line(lines[-1], 80)
    return f"{e.kind}: " + _one_line(e.text, 120)


def summarize(evs: list[Event]) -> dict[str, Any]:
    """The judge's view of the conversation: goal, recent steps, last message (<2k tokens)."""
    goal = next((e.text for e in reversed(evs) if e.kind == "user"), "")
    last = evs[-1] if evs else Event("user", "")
    return {
        "goal": _one_line(goal, _GOAL_CHARS),
        "recent_steps": "\n".join(_step_line(e) for e in evs[-_STEP_LIMIT:]),
        "last_message": f"[{last.kind}] " + last.text.strip()[-_LAST_CHARS:],
        "failed_results": sum(e.failed for e in evs),
    }


def requests_since_failure(evs: list[Event]) -> int | None:
    """Number of agent requests made since the last failed tool result (None: no failure).

    Each tool result that follows an assistant step is one agent request.
    """
    count = 0
    for e in reversed(evs):
        if e.kind == "result":
            if e.failed:
                return count
            count += 1
    return None


def passed_tests_just_ran(evs: list[Event]) -> bool:
    """True if the trailing tool results include a passing test run and no failure."""
    trailing: list[Event] = []
    for e in reversed(evs):
        if e.kind != "result":
            break
        trailing.append(e)
    if not trailing or any(e.failed or _NOT_PASSED.search(e.text) for e in trailing):
        return False
    return any(_PASSED.search(e.text) for e in trailing)


def conversation_key(evs: list[Event]) -> str:
    first = next((e.text for e in evs if e.kind == "user"), "")
    return hashlib.sha256(first.encode()).hexdigest()[:12]


def request_chars(body: dict[str, Any]) -> int:
    return len(json.dumps([body.get("system"), body.get("messages"), body.get("tools")]))


async def ask_judge(cfg: RouterConfig, client: httpx.AsyncClient, state: dict[str, Any]) -> float:
    """Return the judge's P(easy). Raise on any transport or format error."""
    headers = {"Authorization": f"Bearer {cfg.judge_api_key}"} if cfg.judge_api_key else {}
    resp = await client.post(
        cfg.judge_url,
        json={"model": cfg.judge_model, "state": state, "questions": JUDGE_QUESTION},
        headers=headers,
        timeout=cfg.judge_timeout_seconds,
    )
    resp.raise_for_status()
    return float(resp.json()["answers"]["route"]["probabilities"]["easy"])


async def decide(
    cfg: RouterConfig, client: httpx.AsyncClient, body: dict[str, Any], provider: str
) -> Decision:
    evs = events(body, provider)
    state = summarize(evs)
    if cfg.route_mode in ("local", "frontier"):
        return Decision(cfg.route_mode, f"forced_{cfg.route_mode}", state)  # type: ignore[arg-type]
    if request_chars(body) > cfg.local_max_request_chars:
        return Decision("frontier", "too_large_for_local", state)
    since = requests_since_failure(evs)
    if since is not None and since < cfg.stay_requests:
        return Decision("frontier", f"recent_failure ({since} requests ago)", state)
    if cfg.route_mode == "random":
        coin: Tier = "local" if random.random() < cfg.random_local_share else "frontier"  # noqa: S311
        return Decision(coin, "random", state, tests_passed=passed_tests_just_ran(evs))
    started = time.perf_counter()
    try:
        p_easy = await ask_judge(cfg, client, state)
    except Exception as exc:
        ms = (time.perf_counter() - started) * 1000.0
        return Decision("frontier", "judge_error", state, judge_ms=ms, judge_error=repr(exc))
    ms = (time.perf_counter() - started) * 1000.0
    tier: Tier = "local" if p_easy > cfg.easy_threshold else "frontier"
    return Decision(
        tier, "judge", state, p_easy=p_easy, judge_ms=ms, tests_passed=passed_tests_just_ran(evs)
    )
