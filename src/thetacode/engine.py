"""Core speculative drafting, context conversion, and verification engine."""

from __future__ import annotations

import copy
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import SpeculativeConfig

logger = logging.getLogger("thetacode")


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
    estimated_usd_saved: float = 0.0
    local_draft_latency_ms_sum: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        avg_draft_ms = (
            round(self.local_draft_latency_ms_sum / self.drafts_succeeded, 2)
            if self.drafts_succeeded > 0
            else 0.0
        )
        accept_rate = (
            round((self.frontier_accepted_count / self.drafts_succeeded) * 100.0, 1)
            if self.drafts_succeeded > 0
            else 0.0
        )
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
            "estimated_usd_saved": round(self.estimated_usd_saved, 4),
            "avg_local_draft_latency_ms": avg_draft_ms,
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
        if lower_headers.get("x-thetacode-bypass", "").lower() in ("1", "true", "yes"):
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


def convert_to_openai_draft_payload(
    body: dict[str, Any],
    provider: str,
    config: SpeculativeConfig,
) -> dict[str, Any]:
    """Convert an Anthropic or OpenAI request body (already compressed by Headroom)
    into a fast, bounded OpenAI Chat Completions payload for the local drafter.
    """
    messages = body.get("messages") or []
    recent_messages = messages[-config.local_recent_messages_limit :]

    openai_messages: list[dict[str, Any]] = []

    # 1. Include system instructions (trimmed)
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

    # 2. Convert recent conversation & tool history
    budget_remaining = config.local_max_context_chars
    converted_tail: list[dict[str, Any]] = []

    for msg in reversed(recent_messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        if role not in ("user", "assistant", "system", "tool"):
            role = "user"

        text = extract_text_from_content(msg.get("content", ""))
        if not text and role == "assistant" and msg.get("tool_calls"):
            text = json.dumps(msg.get("tool_calls"))

        if not text:
            continue

        if len(text) > budget_remaining:
            text = text[-budget_remaining:]

        budget_remaining -= len(text)
        converted_tail.append(
            {
                "role": "user" if role == "tool" else role,
                "content": text,
            }
        )
        if budget_remaining <= 0:
            break

    converted_tail.reverse()
    openai_messages.extend(converted_tail)

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
    if openai_tools:
        payload["tools"] = openai_tools[:16]  # Keep local tool schema prompt compact

    return payload


async def call_local_drafter(
    body: dict[str, Any],
    provider: str,
    config: SpeculativeConfig,
    http_client: httpx.AsyncClient,
    headers: dict[str, str] | None = None,
) -> DraftResult:
    """Query the local draft model to produce a speculative completion or tool call."""
    eligible, reason = should_speculate(body, provider, config, headers)
    if not eligible:
        return DraftResult(skipped=True, skip_reason=reason)

    payload = convert_to_openai_draft_payload(body, provider, config)
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

    # Find last user message (live zone tail) to preserve prefix caching on earlier messages
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = content + injection_text
            break
        elif isinstance(content, list):
            # Append as a new text block at the end of the final user turn
            content.append({"type": "text", "text": injection_text})
            break

    return updated, effective_mode


def response_accepted_draft(
    resp_text: str,
    config: SpeculativeConfig,
) -> bool:
    """Check if the target model output indicates acceptance of the speculative draft."""
    cleaned = resp_text.strip()
    if not cleaned:
        return False
    if cleaned == config.accept_token:
        return True
    # Also accept if the model echoed `<ACCEPT/>` with minor surrounding whitespace/punctuation
    return cleaned.startswith(config.accept_token) and len(cleaned) <= len(config.accept_token) + 8


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
