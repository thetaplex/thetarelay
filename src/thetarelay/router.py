"""Decide whether a coding-agent request goes to the lite (easy) or frontier (hard) tier.

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
from .steps import Group, classify, last_program

Tier = Literal["lite", "frontier"]

JUDGE_QUESTION = {
    "route": {
        "type": "choice",
        "instructions": (
            "A coding agent is about to take its next step. Can a small, fast model handle "
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
# Structured signals come first (see _failure); keywords are the fallback.
_FAILURE = re.compile(
    r"\b(FAILED|FAILURES?|Traceback|AssertionError|panicked|Exception|[Ee]rror:|non-zero exit)"
)
# Structured signals match a whole line (stripped of pytest's `=` framing), never part of one.
# Pi: `Command exited with code N` (only when N is not 0); others: `exit code N`, `exit status N`.
_EXIT = re.compile(r"^(?:Command exited with code (\d+)|(?i:exit (?:code|status)):? (\d+))$")
# Test runner summaries: pytest, unittest, Jest, cargo. A pytest line without failures must
# end `in Ns` to count, so a stray `12 passed` cannot mask a keyword failure.
_PYTEST = re.compile(
    r"^(\d+ (passed|failed|errors?|skipped|warnings?|deselected|xfailed|xpassed)[, ]*)+"
    r"( in [\d.]+s\b.*)?$"
)
_SUMMARY = re.compile(r"^FAILED \((failures|errors)=|^Tests:\s+\d|^test result: (ok|FAILED)\.")
_UNITTEST_OK = re.compile(r"^OK( \(.*\))?$")
_UNITTEST_RAN = re.compile(r"^Ran \d+ tests?\b")
_SUMMARY_FAILED = re.compile(r"\b[1-9]\d* (failed|errors?)\b|FAILED")
_SEARCHES = frozenset({"rg", "grep", "egrep", "fgrep"})
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_STEP_LIMIT = 12
_GOAL_CHARS = 300
_LAST_CHARS = 800


@dataclass
class Event:
    """One step of the conversation, in a form common to both API formats."""

    kind: Literal["user", "assistant", "call", "result"]
    text: str
    # Results only: the line that decided the failure (None: not a failure), and the step group
    # and shell command of the linked call.
    failure: str | None = None
    group: Group | None = None
    command: str | None = None

    @property
    def failed(self) -> bool:
        return self.failure is not None


@dataclass
class Decision:
    tier: Tier
    reason: str
    judge_state: dict[str, Any] = field(default_factory=dict)
    p_easy: float | None = None
    judge_ms: float | None = None
    judge_error: str | None = None
    # The frontier request carries the triage note: the frontier model may hand the step back.
    triage: bool = False


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


def _link(calls: dict[str, tuple[str, Any]], call_id: Any) -> tuple[Group, str | None]:
    """Step group and shell command of the call a result answers; unknown id is irreversible."""
    if not isinstance(call_id, str) or call_id not in calls:
        return "irreversible", None
    name, args = calls[call_id]
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = None
    cmd = args.get("command") if isinstance(args, dict) else None
    return classify(name, calls[call_id][1]), cmd if isinstance(cmd, str) else None


def _failure(text: str, is_error: bool, group: Group, command: str | None) -> str | None:
    """The line that decides the step failed, or None. The first signal present decides:
    the error flag, an exit status, a test summary, then keywords (not for read steps)."""
    lines = [s for ln in text.splitlines() if (s := ln.strip(" \t="))]
    # A non-shell read tool reports no exit status: its text is file content.
    shell = not (group == "read" and command is None)
    exits = [(ln, int(m[1] or m[2])) for ln in lines if shell and (m := _EXIT.match(ln))]
    keyword = next((ln for ln in lines if _FAILURE.search(ln)), None)
    if is_error:
        return next((ln for ln, code in exits if code), keyword or (lines[0] if lines else ""))
    if exits:
        line, code = exits[-1]
        search = command is not None and last_program(command) in _SEARCHES
        if code == 0 or (code == 1 and group == "read" and search):
            return None
        return line
    if group == "read":
        return None
    ran = [i for i, ln in enumerate(lines) if _UNITTEST_RAN.match(ln)]
    summaries = [
        ln
        for i, ln in enumerate(lines)
        if _SUMMARY.match(ln)
        or ((m := _PYTEST.match(ln)) and (m[3] or _SUMMARY_FAILED.search(ln)))
        or (_UNITTEST_OK.match(ln) and ran and ran[0] < i)
    ]
    if summaries:
        return summaries[-1] if _SUMMARY_FAILED.search(summaries[-1]) else None
    return keyword


def events(body: dict[str, Any], provider: str) -> list[Event]:
    """Flatten the conversation of an Anthropic or OpenAI request into steps."""
    out: list[Event] = []
    calls: dict[str, tuple[str, Any]] = {}
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role, content = msg.get("role"), msg.get("content")
        if provider == "openai":
            if role == "tool":
                text = _text(content)
                group, cmd = _link(calls, msg.get("tool_call_id"))
                out.append(Event("result", text, _failure(text, False, group, cmd), group, cmd))
                continue
            if text := _text(content).strip():
                out.append(Event("assistant" if role == "assistant" else "user", text))
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                if isinstance(tc.get("id"), str):
                    calls[tc["id"]] = (fn.get("name", ""), fn.get("arguments", ""))
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
                if isinstance(block.get("id"), str):
                    calls[block["id"]] = (block.get("name", ""), args)
                out.append(Event("call", f"{block.get('name', '')} {args}"))
            elif kind == "tool_result":
                text = _text(block.get("content", ""))
                group, cmd = _link(calls, block.get("tool_use_id"))
                failure = _failure(text, block.get("is_error") is True, group, cmd)
                out.append(Event("result", text, failure, group, cmd))
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
        if e.failure is not None:
            return "result FAILED: " + _one_line(e.failure, 120)
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
    if cfg.route_mode in ("lite", "frontier"):
        return Decision(cfg.route_mode, f"forced_{cfg.route_mode}", state)  # type: ignore[arg-type]
    if request_chars(body) > cfg.lite_max_request_chars:
        return Decision("frontier", "too_large_for_lite", state)
    since = requests_since_failure(evs)
    if since is not None and since < cfg.stay_requests:
        return Decision("frontier", f"recent_failure ({since} requests ago)", state)
    if cfg.route_mode == "triage":
        return Decision("frontier", "triage", state, triage=True)
    if cfg.route_mode == "random":
        coin: Tier = "lite" if random.random() < cfg.random_lite_share else "frontier"  # noqa: S311
        return Decision(coin, "random", state)
    started = time.perf_counter()
    try:
        p_easy = await ask_judge(cfg, client, state)
    except Exception as exc:
        ms = (time.perf_counter() - started) * 1000.0
        return Decision("frontier", "judge_error", state, judge_ms=ms, judge_error=repr(exc))
    ms = (time.perf_counter() - started) * 1000.0
    tier: Tier = "lite" if p_easy > cfg.easy_threshold else "frontier"
    # Hybrid: a confident judge sends the step to the lite tier; otherwise the frontier triages.
    triage = tier == "frontier" and cfg.route_mode == "hybrid"
    return Decision(
        tier,
        "judge; triage" if triage else "judge",
        state,
        p_easy=p_easy,
        judge_ms=ms,
        triage=triage,
    )
