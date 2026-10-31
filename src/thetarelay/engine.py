"""Core speculative drafting, context conversion, and verification engine."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import SpeculativeConfig

logger = logging.getLogger("thetarelay")


@dataclass
class DraftResult:
    """Result from the local draft model."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    draft_tokens_est: int = 0
    skipped: bool = False
    skip_reason: str = ""
    error: str | None = None

    @property
    def has_draft(self) -> bool:
        return bool(self.text.strip() or self.tool_calls) and not self.skipped and not self.error

    def format_for_verification(self) -> str:
        """Render the draft (text and/or tool calls) into a structured verification block."""
        parts: list[str] = []
        if self.text.strip():
            parts.append(self.text.strip())
        if self.tool_calls:
            parts.append("<draft_tool_calls>")
            parts.append(json.dumps(self.tool_calls, indent=2))
            parts.append("</draft_tool_calls>")
        return "\n".join(parts)


def _percentile(values: list[float], pct: float) -> float:
    """Compute a percentile (0..100) from a list of floats."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = (pct / 100.0) * (len(sorted_vals) - 1)
    lower = int(idx)
    upper = min(lower + 1, len(sorted_vals) - 1)
    weight = idx - lower
    return round(sorted_vals[lower] * (1.0 - weight) + sorted_vals[upper] * weight, 2)


def estimate_injection_input_tokens(draft: DraftResult, config: SpeculativeConfig) -> int:
    """Estimate input token overhead added when injecting a speculative draft into the prompt."""
    if not draft.has_draft:
        return 0
    injection = build_verification_injection(draft, config)
    return max(1, len(injection) // 4)


@dataclass
class SpeculativeMetrics:
    """Runtime statistics for speculative drafting and frontier verification."""

    requests_total: int = 0
    drafts_attempted: int = 0
    drafts_succeeded: int = 0
    drafts_skipped: int = 0
    drafts_failed: int = 0
    frontier_accepted_count: int = 0
    frontier_modified_count: int = 0
    openai_prediction_injected_count: int = 0
    estimated_output_tokens_saved: int = 0
    estimated_input_tokens_added: int = 0
    estimated_usd_saved: float = 0.0
    estimated_input_usd_cost: float = 0.0
    local_draft_latency_ms_sum: float = 0.0
    frontier_latency_ms_sum: float = 0.0
    estimated_net_latency_saved_ms_sum: float = 0.0
    prefix_cache_preserved_count: int = 0
    local_draft_latencies_ms: list[float] = field(default_factory=list)
    frontier_latencies_ms: list[float] = field(default_factory=list)
    end_to_end_latencies_ms: list[float] = field(default_factory=list)

    def record_turn_outcome(
        self,
        *,
        draft: DraftResult,
        config: SpeculativeConfig,
        accepted: bool,
        active_mode: str = "accept_swap",
        frontier_latency_ms: float = 0.0,
    ) -> tuple[int, float, float]:
        """Record token, cost, and latency effects of a speculative turn.

        Returns (saved_output_tokens, net_usd_saved, est_net_latency_saved_ms).
        """
        if frontier_latency_ms > 0:
            self.frontier_latency_ms_sum += frontier_latency_ms
            self.frontier_latencies_ms.append(frontier_latency_ms)
            self.end_to_end_latencies_ms.append(draft.latency_ms + frontier_latency_ms)

        input_overhead_tok = (
            0
            if active_mode == "openai_prediction"
            else estimate_injection_input_tokens(draft, config)
        )
        self.estimated_input_tokens_added += input_overhead_tok
        input_cost_usd = (input_overhead_tok / 1_000_000.0) * config.estimated_input_usd_per_million
        self.estimated_input_usd_cost += input_cost_usd
        self.prefix_cache_preserved_count += 1

        if accepted:
            self.frontier_accepted_count += 1
            saved_tok = max(0, draft.draft_tokens_est - 2)
            gross_saved_usd = (saved_tok / 1_000_000.0) * config.estimated_output_usd_per_million
            self.estimated_output_tokens_saved += saved_tok
            self.estimated_usd_saved += gross_saved_usd
            tps = max(1.0, config.estimated_frontier_output_tps)
            avoided_decode_ms = (saved_tok / tps) * 1000.0
            net_latency_saved_ms = avoided_decode_ms - draft.latency_ms
            self.estimated_net_latency_saved_ms_sum += net_latency_saved_ms
            return saved_tok, gross_saved_usd - input_cost_usd, net_latency_saved_ms

        self.frontier_modified_count += 1
        net_latency_saved_ms = -draft.latency_ms
        self.estimated_net_latency_saved_ms_sum += net_latency_saved_ms
        return 0, -input_cost_usd, net_latency_saved_ms

    def to_dict(self) -> dict[str, Any]:
        avg_draft_ms = (
            round(self.local_draft_latency_ms_sum / self.drafts_succeeded, 2)
            if self.drafts_succeeded > 0
            else 0.0
        )
        avg_frontier_ms = (
            round(self.frontier_latency_ms_sum / len(self.frontier_latencies_ms), 2)
            if self.frontier_latencies_ms
            else 0.0
        )
        accept_rate = (
            round((self.frontier_accepted_count / self.drafts_succeeded) * 100.0, 1)
            if self.drafts_succeeded > 0
            else 0.0
        )
        net_usd = round(self.estimated_usd_saved - self.estimated_input_usd_cost, 4)
        return {
            "requests_total": self.requests_total,
            "drafts_attempted": self.drafts_attempted,
            "drafts_succeeded": self.drafts_succeeded,
            "drafts_skipped": self.drafts_skipped,
            "drafts_failed": self.drafts_failed,
            "frontier_accepted_count": self.frontier_accepted_count,
            "frontier_modified_count": self.frontier_modified_count,
            "acceptance_rate_percent": accept_rate,
            "openai_prediction_injected_count": self.openai_prediction_injected_count,
            "estimated_output_tokens_saved": self.estimated_output_tokens_saved,
            "estimated_input_tokens_added": self.estimated_input_tokens_added,
            "net_tokens_saved": self.estimated_output_tokens_saved
            - self.estimated_input_tokens_added,
            "estimated_usd_saved": round(self.estimated_usd_saved, 4),
            "estimated_input_usd_cost": round(self.estimated_input_usd_cost, 4),
            "estimated_net_usd_saved": net_usd,
            "avg_local_draft_latency_ms": avg_draft_ms,
            "p50_local_draft_latency_ms": _percentile(self.local_draft_latencies_ms, 50),
            "p95_local_draft_latency_ms": _percentile(self.local_draft_latencies_ms, 95),
            "avg_frontier_latency_ms": avg_frontier_ms,
            "p50_end_to_end_latency_ms": _percentile(self.end_to_end_latencies_ms, 50),
            "p95_end_to_end_latency_ms": _percentile(self.end_to_end_latencies_ms, 95),
            "estimated_net_latency_saved_ms": round(self.estimated_net_latency_saved_ms_sum, 1),
            "prefix_cache_preserved_count": self.prefix_cache_preserved_count,
        }


def extract_text_from_content(content: Any) -> str:
    """Extract plain text from Anthropic or OpenAI message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype in ("text", "input_text", "output_text"):
                parts.append(str(block.get("text", "")))
            elif btype == "tool_use":
                parts.append(
                    f"[Tool Call: {block.get('name')}({json.dumps(block.get('input', {}))})]"
                )
            elif btype == "tool_result":
                inner = extract_text_from_content(block.get("content", ""))
                parts.append(f"[Tool Result ({block.get('tool_use_id', '')})]:\n{inner}")
        return "\n".join(p for p in parts if p)
    return ""


def should_speculate(
    body: dict[str, Any],
    provider: str,
    config: SpeculativeConfig,
    headers: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """Determine if this request should trigger local speculative drafting."""
    if headers:
        lower_headers = {k.lower(): v for k, v in headers.items()}
        if lower_headers.get("x-thetarelay-bypass", "").lower() in ("1", "true", "yes"):
            return False, "bypass_header"
        if lower_headers.get("x-headroom-bypass", "").lower() in ("1", "true", "yes"):
            return False, "headroom_bypass_header"

    model = str(body.get("model", "")).lower()
    for skip_sub in config.skip_models:
        if skip_sub and skip_sub in model:
            return False, f"skipped_small_model:{skip_sub}"

    messages = body.get("messages") or []
    if not messages or not isinstance(messages, list):
        return False, "empty_messages"

    last_msg = messages[-1] if isinstance(messages[-1], dict) else {}
    last_text = extract_text_from_content(last_msg.get("content", ""))

    # Avoid infinite recursion if request already contains a speculative draft
    if "<speculative_draft>" in last_text:
        return False, "already_has_draft"

    if not config.selective_speculation:
        return True, "always_on"

    if len(last_text.strip()) < config.min_prompt_chars_for_draft:
        return False, "prompt_too_short"

    return True, "eligible"


def _newest_messages(converted: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Return the newest messages that fit in `budget` characters, trimming one if needed."""
    used = 0
    for i in range(len(converted) - 1, -1, -1):
        used += len(converted[i]["content"])
        if used > budget:
            if i == len(converted) - 1:
                # The newest message alone is over the budget: keep its end.
                return [{**converted[i], "content": converted[i]["content"][-budget:]}]
            return converted[i + 1 :]
    return converted


def _stable_window(
    converted: list[dict[str, Any]], config: SpeculativeConfig
) -> list[dict[str, Any]]:
    """Fixed-size draft window for callers that keep no state between drafts.

    Move the window start in jumps of half the message limit and half the character
    budget, never one message per turn. Consecutive drafts then share a prefix, so the
    local server's prompt cache can skip prefill for everything but the new messages.
    """
    budget = config.local_max_context_chars
    limit = config.local_recent_messages_limit
    msg_stride = max(1, limit // 2)
    start = -(-max(0, len(converted) - limit) // msg_stride) * msg_stride
    total_chars = sum(len(m["content"]) for m in converted)
    if total_chars > budget:
        char_stride = max(1, budget // 2)
        threshold = -(-(total_chars - budget) // char_stride) * char_stride
        offset = 0
        for i, m in enumerate(converted):
            if offset >= threshold:
                start = max(start, i)
                break
            offset += len(m["content"])
        else:
            start = len(converted)
    return converted[start:] or _newest_messages(converted, budget)


class AdaptiveDraftWindow:
    """Draft window sized per conversation by what the local server can read in time.

    The local server's prompt cache makes the part of the window it saw on the previous
    draft almost free; only new text costs prefill time. So the window keeps its start and
    grows at the end while the new text can be read within the timeout, up to
    `local_max_context_chars`. When the new text is too large, or the ceiling is reached,
    the window restarts from the newest messages that can be read from scratch in time.
    The read speed is learned from completed drafts.
    """

    _MAX_CONVERSATIONS = 64
    # Part of the timeout spent reading new text; the rest is left for writing the draft.
    _READ_SHARE = 0.8

    def __init__(self, chars_per_second: float = 1300.0) -> None:
        # Default measured on an Apple silicon Mac with gemma4:e4b-mlx (Ollama).
        self.chars_per_second = chars_per_second
        # Conversation key -> (window start, messages sent last time, trimmed first message).
        self._state: OrderedDict[str, tuple[int, int, dict[str, Any] | None]] = OrderedDict()

    def affordable_chars(self, config: SpeculativeConfig) -> int:
        """Characters of new text the local server can read within the timeout."""
        return max(
            500, int(self.chars_per_second * config.local_timeout_seconds * self._READ_SHARE)
        )

    def observe(self, new_chars: int, elapsed_ms: float) -> None:
        """Update the read speed from one draft. Small reads are dominated by decode time."""
        if new_chars < 1000 or elapsed_ms <= 0:
            return
        measured = new_chars / (elapsed_ms / 1000.0)
        self.chars_per_second = 0.7 * self.chars_per_second + 0.3 * measured

    def build_payload(
        self, body: dict[str, Any], provider: str, config: SpeculativeConfig
    ) -> tuple[dict[str, Any], int]:
        """Return the local draft payload and the characters the server has not seen yet."""
        new_chars = 0

        def select(converted: list[dict[str, Any]], cfg: SpeculativeConfig) -> list[dict[str, Any]]:
            nonlocal new_chars
            window, new_chars = self._select(converted, cfg)
            return window

        payload = convert_to_openai_draft_payload(body, provider, config, select_window=select)
        return payload, new_chars

    def _select(
        self, converted: list[dict[str, Any]], config: SpeculativeConfig
    ) -> tuple[list[dict[str, Any]], int]:
        if not converted:
            return [], 0
        key = hashlib.sha256(converted[0]["content"].encode()).hexdigest()
        affordable = self.affordable_chars(config)
        state = self._state.get(key)
        if state is not None and state[0] < len(converted) and state[1] <= len(converted):
            start, sent, trimmed = state
            window = ([trimmed] if trimmed else [converted[start]]) + converted[start + 1 :]
            new_chars = sum(len(m["content"]) for m in converted[max(start + 1, sent) :])
            if (
                new_chars <= affordable
                and sum(len(m["content"]) for m in window) <= config.local_max_context_chars
            ):
                self._remember(key, (start, len(converted), trimmed))
                return window, new_chars
        # Restart from what can be read from scratch, leaving room to grow before the ceiling.
        window = _newest_messages(converted, min(affordable, config.local_max_context_chars // 2))
        start = len(converted) - len(window)
        trimmed = window[0] if window[0] is not converted[start] else None
        self._remember(key, (start, len(converted), trimmed))
        return window, sum(len(m["content"]) for m in window)

    def _remember(self, key: str, state: tuple[int, int, dict[str, Any] | None]) -> None:
        self._state[key] = state
        self._state.move_to_end(key)
        while len(self._state) > self._MAX_CONVERSATIONS:
            self._state.popitem(last=False)


def convert_to_openai_draft_payload(
    body: dict[str, Any],
    provider: str,
    config: SpeculativeConfig,
    select_window: Callable[[list[dict[str, Any]], SpeculativeConfig], list[dict[str, Any]]]
    | None = None,
) -> dict[str, Any]:
    """Convert an Anthropic or OpenAI request body (already compressed by Headroom)
    into a fast, bounded OpenAI Chat Completions payload for the local drafter.

    `select_window` picks which converted messages to send; by default a fixed-size
    window that moves in jumps (see `_stable_window`).
    """
    messages = body.get("messages") or []

    openai_messages: list[dict[str, Any]] = []

    # 1. Include system instructions (trimmed). The drafting instructions go in their own
    # leading system message, never on the last user turn: a message that changes on the
    # next turn makes the local server's prompt cache miss from that message on.
    has_tools = bool(body.get("tools"))
    if config.local_concise_draft_instructions:
        if has_tools:
            drafting_instructions = (
                "[DRAFTING MODE: If a tool call is appropriate for the next step, "
                "invoke the tool directly with exact arguments. Otherwise output a concise "
                "code block or direct answer without unnecessary preamble.]"
            )
        else:
            drafting_instructions = (
                "[DRAFTING MODE: Output ONLY a single ```python code block (or tool call) "
                "containing the minimal implementation. Do NOT write explanations before or after "
                "the code, do NOT write docstrings longer than 1 line, and do NOT include "
                "`if __name__ == '__main__':` or example usage.]"
            )
        openai_messages.append({"role": "system", "content": drafting_instructions})
    if provider == "anthropic":
        sys_raw = body.get("system")
        sys_text = extract_text_from_content(sys_raw) if sys_raw else ""
        if sys_text:
            openai_messages.append(
                {
                    "role": "system",
                    "content": sys_text[: config.local_max_context_chars // 3],
                }
            )

    # 2. Convert conversation & tool history
    converted: list[dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        if role not in ("user", "assistant", "system", "tool"):
            role = "user"

        text = extract_text_from_content(msg.get("content", ""))
        if not text and role == "assistant" and msg.get("tool_calls"):
            text = f"[Assistant Tool Calls]: {json.dumps(msg.get('tool_calls'))}"
        elif role == "tool" and text:
            tool_name = msg.get("name") or msg.get("tool_call_id") or "tool"
            text = f"[Tool Result ({tool_name})]:\n{text}"

        if not text:
            continue

        converted.append(
            {
                "role": "user" if role == "tool" else role,
                "content": text,
            }
        )

    openai_messages.extend((select_window or _stable_window)(converted, config))

    # 3. Convert tools if present so the local model can draft tool calls too
    openai_tools: list[dict[str, Any]] = []
    raw_tools = body.get("tools") or []
    if isinstance(raw_tools, list):
        for t in raw_tools:
            if not isinstance(t, dict):
                continue
            # Skip server-side/internal tools like tool_search or headroom_retrieve
            t_name = t.get("name") or (t.get("function") or {}).get("name") or ""
            if t_name in ("headroom_retrieve", "tool_search") or t.get("defer_loading"):
                continue
            if "function" in t:
                openai_tools.append(t)
            elif "name" in t and "input_schema" in t:
                openai_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": str(t.get("description", ""))[:300],
                            "parameters": t["input_schema"],
                        },
                    }
                )

    payload: dict[str, Any] = {
        "model": config.local_model,
        "messages": openai_messages,
        "max_tokens": config.local_max_tokens,
        "temperature": config.local_temperature,
        "stream": False,
    }
    if config.local_disable_thinking:
        payload["reasoning_effort"] = "none"
    if openai_tools:
        payload["tools"] = openai_tools[:16]  # Keep local tool schema prompt compact

    return payload


async def call_local_drafter(
    body: dict[str, Any],
    provider: str,
    config: SpeculativeConfig,
    http_client: httpx.AsyncClient,
    headers: dict[str, str] | None = None,
    window: AdaptiveDraftWindow | None = None,
) -> DraftResult:
    """Query the local draft model to produce a speculative completion or tool call."""
    eligible, reason = should_speculate(body, provider, config, headers)
    if not eligible:
        return DraftResult(skipped=True, skip_reason=reason)

    if window is not None and config.local_adaptive_context:
        payload, new_chars = window.build_payload(body, provider, config)
        result = await _post_local_draft(payload, config, http_client)
        window.observe(new_chars, result.latency_ms)
        return result
    payload = convert_to_openai_draft_payload(body, provider, config)
    return await _post_local_draft(payload, config, http_client)


async def _post_local_draft(
    payload: dict[str, Any], config: SpeculativeConfig, http_client: httpx.AsyncClient
) -> DraftResult:
    url = f"{config.local_base_url}/chat/completions"
    req_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.local_api_key}",
    }

    started = time.perf_counter()
    try:
        resp = await http_client.post(
            url,
            json=payload,
            headers=req_headers,
            timeout=config.local_timeout_seconds,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if resp.status_code != 200:
            logger.warning(
                "Local drafter returned HTTP %d: %s",
                resp.status_code,
                resp.text[:200],
            )
            return DraftResult(
                latency_ms=elapsed_ms,
                error=f"http_{resp.status_code}",
            )

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return DraftResult(latency_ms=elapsed_ms, error="empty_choices")

        msg = (choices[0] or {}).get("message") or {}
        draft_text = str(msg.get("content") or "")
        raw_tool_calls = msg.get("tool_calls") or []

        normalized_tools: list[dict[str, Any]] = []
        for tc in raw_tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments", "{}")
            try:
                args_parsed = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args_parsed = {"raw": args_raw}
            normalized_tools.append(
                {
                    "id": tc.get("id") or f"toolu_draft_{uuid.uuid4().hex[:8]}",
                    "name": fn.get("name", ""),
                    "input": args_parsed if isinstance(args_parsed, dict) else {},
                }
            )

        usage = data.get("usage") or {}
        completion_tokens = int(
            usage.get("completion_tokens")
            or max(1, (len(draft_text) + len(json.dumps(normalized_tools))) // 4)
        )

        return DraftResult(
            text=draft_text,
            tool_calls=normalized_tools,
            latency_ms=elapsed_ms,
            draft_tokens_est=completion_tokens,
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.debug("Local drafter call failed (%s): %s", type(exc).__name__, exc)
        return DraftResult(latency_ms=elapsed_ms, error=str(exc))


def build_verification_injection(draft: DraftResult, config: SpeculativeConfig) -> str:
    """Build the speculative draft block injected into the latest user turn (prefix-cache safe)."""
    formatted = draft.format_for_verification()
    if config.strategy == "context_only":
        return (
            f"\n\n<speculative_draft>\n{formatted}\n</speculative_draft>\n"
            "You may use or refine the above local draft response if it is accurate."
        )

    return (
        f"\n\n<speculative_draft>\n{formatted}\n</speculative_draft>\n"
        f"INSTRUCTION: A local draft model proposed the response inside <speculative_draft>. "
        f"If the draft is complete, accurate, and directly satisfies the request (including any "
        f"tool call names and arguments), output ONLY `{config.accept_token}` and nothing else. "
        f"If it needs any changes, ignore `{config.accept_token}` and output your full response "
        f"or tool calls normally."
    )


def inject_draft_into_body(
    body: dict[str, Any],
    provider: str,
    draft: DraftResult,
    config: SpeculativeConfig,
) -> tuple[dict[str, Any], str]:
    """Mutate a copy of the request body to include the speculative draft.

    Preserves KV prefix cache by ONLY appending to the final user message (never mutating
    the system prompt or historical turns) and setting OpenAI `prediction` where applicable.

    Returns (updated_body, active_mode) where active_mode is:
    - "openai_prediction"
    - "accept_swap"
    - "context_only"
    """
    updated = copy.deepcopy(body)

    # Determine effective mode
    effective_mode = config.strategy
    if effective_mode == "auto":
        if provider == "openai" and draft.text.strip() and not draft.tool_calls:
            effective_mode = "prediction"
        else:
            effective_mode = "accept_swap"

    # Mode 1: Native OpenAI Predicted Outputs (`prediction` parameter)
    if effective_mode == "prediction" and provider == "openai":
        updated["prediction"] = {
            "type": "content",
            "content": draft.text,
        }
        # Predicted Outputs on OpenAI apply when drafting text content without tool calls.
        return updated, "openai_prediction"

    # Mode 2: `<ACCEPT/>` verification or advisory context appended to final user turn
    injection_text = build_verification_injection(draft, config)
    messages = updated.get("messages") or []
    if not messages:
        return updated, "none"

    # Append only to the final turn (live zone tail) to preserve 100% of historical prefix cache
    last_msg = messages[-1]
    if isinstance(last_msg, dict) and last_msg.get("role") == "user":
        content = last_msg.get("content")
        if isinstance(content, str):
            last_msg["content"] = content + injection_text
        elif isinstance(content, list):
            content.append({"type": "text", "text": injection_text})
    else:
        # When the final turn is a tool result (`role == "tool"` in OpenAI format),
        # append a new user message at the tail so historical messages are never mutated.
        messages.append({"role": "user", "content": injection_text.lstrip()})

    return updated, effective_mode


def response_accepted_draft(
    resp_text: str,
    config: SpeculativeConfig,
) -> bool:
    """Check if the target model output indicates acceptance of the speculative draft."""
    import re

    # Strip any residual `<think>...</think>` or leading `</think>` tags from reasoning models
    cleaned = re.sub(r"<think>.*?</think>", "", resp_text, flags=re.DOTALL)
    cleaned = cleaned.replace("</think>", "").strip()
    if not cleaned:
        return False
    if cleaned == config.accept_token:
        return True
    # Also accept if the model echoed `<ACCEPT/>` with minor surrounding whitespace/punctuation
    # or repeated `<ACCEPT/>` after a thinking tag
    max_len = len(config.accept_token) * 2 + 8
    return cleaned.startswith(config.accept_token) and len(cleaned) <= max_len


def build_anthropic_response_from_draft(
    original_response: dict[str, Any],
    draft: DraftResult,
) -> dict[str, Any]:
    """Replace an `<ACCEPT/>` Anthropic response JSON with the full drafted content/tool_use."""
    replaced = copy.deepcopy(original_response)
    content_blocks: list[dict[str, Any]] = []

    if draft.text.strip():
        content_blocks.append({"type": "text", "text": draft.text})

    for tc in draft.tool_calls:
        content_blocks.append(
            {
                "type": "tool_use",
                "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "name": tc["name"],
                "input": tc.get("input", {}),
            }
        )

    replaced["content"] = content_blocks
    if draft.tool_calls:
        replaced["stop_reason"] = "tool_use"
    else:
        replaced["stop_reason"] = replaced.get("stop_reason") or "end_turn"

    return replaced


def build_openai_response_from_draft(
    original_response: dict[str, Any],
    draft: DraftResult,
) -> dict[str, Any]:
    """Replace an `<ACCEPT/>` OpenAI Chat Completion JSON with the drafted content/tool_calls."""
    replaced = copy.deepcopy(original_response)
    choices = replaced.get("choices") or [{}]
    first_choice = choices[0] if choices else {}
    message: dict[str, Any] = {
        "role": "assistant",
        "content": draft.text if draft.text.strip() else None,
    }
    if draft.tool_calls:
        message["tool_calls"] = [
            {
                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc.get("input", {})),
                },
            }
            for tc in draft.tool_calls
        ]
        first_choice["finish_reason"] = "tool_calls"
    else:
        first_choice["finish_reason"] = first_choice.get("finish_reason") or "stop"

    first_choice["message"] = message
    replaced["choices"] = [first_choice]
    return replaced
