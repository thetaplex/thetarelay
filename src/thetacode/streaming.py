"""SSE Streaming interceptors for real-time `<ACCEPT/>` detection and draft expansion."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from .config import SpeculativeConfig
from .engine import DraftResult, SpeculativeMetrics, response_accepted_draft


def synthesize_anthropic_sse_from_draft(
    draft: DraftResult,
    message_start_event: dict[str, Any] | None = None,
    usage_delta: dict[str, Any] | None = None,
) -> list[bytes]:
    """Synthesize complete Anthropic SSE stream events from a local DraftResult."""
    events: list[bytes] = []

    if message_start_event:
        events.append(f"event: message_start\ndata: {json.dumps(message_start_event)}\n\n".encode())
    else:
        default_start = {
            "type": "message_start",
            "message": {
                "id": f"msg_spec_{uuid.uuid4().hex[:12]}",
                "type": "message",
                "role": "assistant",
                "model": "speculative-draft",
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": 0, "output_tokens": 1},
            },
        }
        events.append(f"event: message_start\ndata: {json.dumps(default_start)}\n\n".encode())

    block_idx = 0
    if draft.text.strip():
        block_start = {
            "type": "content_block_start",
            "index": block_idx,
            "content_block": {"type": "text", "text": ""},
        }
        events.append(f"event: content_block_start\ndata: {json.dumps(block_start)}\n\n".encode())
        block_delta = {
            "type": "content_block_delta",
            "index": block_idx,
            "delta": {"type": "text_delta", "text": draft.text},
        }
        events.append(f"event: content_block_delta\ndata: {json.dumps(block_delta)}\n\n".encode())
        block_stop = {"type": "content_block_stop", "index": block_idx}
        events.append(f"event: content_block_stop\ndata: {json.dumps(block_stop)}\n\n".encode())
        block_idx += 1

    for tc in draft.tool_calls:
        block_start = {
            "type": "content_block_start",
            "index": block_idx,
            "content_block": {
                "type": "tool_use",
                "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "name": tc["name"],
                "input": {},
            },
        }
        events.append(f"event: content_block_start\ndata: {json.dumps(block_start)}\n\n".encode())
        block_delta = {
            "type": "content_block_delta",
            "index": block_idx,
            "delta": {
                "type": "input_json_delta",
                "partial_json": json.dumps(tc.get("input", {})),
            },
        }
        events.append(f"event: content_block_delta\ndata: {json.dumps(block_delta)}\n\n".encode())
        block_stop = {"type": "content_block_stop", "index": block_idx}
        events.append(f"event: content_block_stop\ndata: {json.dumps(block_stop)}\n\n".encode())
        block_idx += 1

    stop_reason = "tool_use" if draft.tool_calls else "end_turn"
    msg_delta = {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": usage_delta or {"output_tokens": 1},
    }
    events.append(f"event: message_delta\ndata: {json.dumps(msg_delta)}\n\n".encode())
    events.append(b'event: message_stop\ndata: {"type": "message_stop"}\n\n')
    return events


async def intercept_anthropic_sse_stream(
    upstream_byte_iter: AsyncIterator[bytes],
    draft: DraftResult,
    config: SpeculativeConfig,
    metrics: SpeculativeMetrics,
    on_accepted_callback: Any = None,
) -> AsyncIterator[bytes]:
    """Intercept an Anthropic SSE stream to check if the first generated tokens are `<ACCEPT/>`.

    - Buffers ONLY the initial ~20 characters of text_delta.
    - As soon as the text diverges from `<ACCEPT/>` (or a tool_use block starts directly),
      flushes all buffered SSE bytes immediately and streams the rest with zero overhead.
    - If the stream completes with `<ACCEPT/>`, replaces the buffered output with the full
      synthesized SSE events from the local model's draft!
    """
    buffered_raw_chunks: list[bytes] = []
    sse_line_buffer = bytearray()
    accumulated_text = ""
    message_start_event: dict[str, Any] | None = None
    usage_delta: dict[str, Any] | None = None
    passthrough_mode = False
    saw_non_text_content_block = False

    accept_token = config.accept_token

    async for chunk in upstream_byte_iter:
        if passthrough_mode:
            yield chunk
            continue

        buffered_raw_chunks.append(chunk)
        sse_line_buffer.extend(chunk)

        # Parse complete SSE events in buffer
        while b"\n\n" in sse_line_buffer:
            idx = sse_line_buffer.index(b"\n\n")
            raw_event = bytes(sse_line_buffer[:idx])
            del sse_line_buffer[: idx + 2]

            event_text = raw_event.decode("utf-8", errors="replace")
            data_str = ""
            for line in event_text.split("\n"):
                if line.startswith("data:"):
                    data_str = line[5:].strip()

            if not data_str or data_str == "[DONE]":
                continue

            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            if not isinstance(data, dict):
                continue

            etype = data.get("type", "")
            if etype == "message_start":
                message_start_event = data
            elif etype == "content_block_start":
                cblock = data.get("content_block") or {}
                if cblock.get("type") not in ("text", "thinking"):
                    # Frontier model started its own tool_use directly -> not <ACCEPT/>
                    saw_non_text_content_block = True
            elif etype == "content_block_delta":
                delta = data.get("delta") or {}
                if delta.get("type") == "text_delta":
                    accumulated_text += str(delta.get("text", ""))
                elif delta.get("type") == "input_json_delta":
                    saw_non_text_content_block = True
            elif etype == "message_delta":
                usage_delta = data.get("usage")

            # Check if we can rule out <ACCEPT/> early and switch to zero-latency passthrough
            stripped = accumulated_text.lstrip()
            if saw_non_text_content_block:
                passthrough_mode = True
                break
            if (
                stripped
                and not accept_token.startswith(stripped)
                and not stripped.startswith(accept_token)
            ):
                passthrough_mode = True
                break
            if len(stripped) > len(accept_token) + 8:
                passthrough_mode = True
                break

        if passthrough_mode:
            metrics.frontier_modified_count += 1
            for bchunk in buffered_raw_chunks:
                yield bchunk
            buffered_raw_chunks.clear()

    # Stream finished while still in buffering window! Check if it accepted the draft.
    if not passthrough_mode:
        if response_accepted_draft(accumulated_text, config):
            metrics.frontier_accepted_count += 1
            saved_tokens = max(0, draft.draft_tokens_est - 2)
            saved_usd = (saved_tokens / 1_000_000.0) * config.estimated_output_usd_per_million
            metrics.estimated_output_tokens_saved += saved_tokens
            metrics.estimated_usd_saved += saved_usd
            if callable(on_accepted_callback):
                on_accepted_callback(saved_tokens, saved_usd)

            for sse_bytes in synthesize_anthropic_sse_from_draft(
                draft,
                message_start_event=message_start_event,
                usage_delta=usage_delta,
            ):
                yield sse_bytes
        else:
            metrics.frontier_modified_count += 1
            for bchunk in buffered_raw_chunks:
                yield bchunk


def synthesize_openai_sse_from_draft(
    draft: DraftResult,
    model: str = "speculative-draft",
) -> list[bytes]:
    """Synthesize OpenAI Chat Completions SSE chunks from a local DraftResult."""
    chat_id = f"chatcmpl-spec-{uuid.uuid4().hex[:12]}"
    events: list[bytes] = []

    if draft.text.strip():
        chunk = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": draft.text},
                    "finish_reason": None,
                }
            ],
        }
        events.append(f"data: {json.dumps(chunk)}\n\n".encode())

    for idx, tc in enumerate(draft.tool_calls):
        chunk = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": idx,
                                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": json.dumps(tc.get("input", {})),
                                },
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        }
        events.append(f"data: {json.dumps(chunk)}\n\n".encode())

    finish_reason = "tool_calls" if draft.tool_calls else "stop"
    stop_chunk = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
    }
    events.append(f"data: {json.dumps(stop_chunk)}\n\n".encode())
    events.append(b"data: [DONE]\n\n")
    return events


async def intercept_openai_sse_stream(
    upstream_byte_iter: AsyncIterator[bytes],
    draft: DraftResult,
    config: SpeculativeConfig,
    metrics: SpeculativeMetrics,
    model: str = "gpt-4o",
    on_accepted_callback: Any = None,
) -> AsyncIterator[bytes]:
    """Intercept an OpenAI Chat Completions SSE stream for `<ACCEPT/>` swap."""
    buffered_raw_chunks: list[bytes] = []
    sse_line_buffer = bytearray()
    accumulated_text = ""
    passthrough_mode = False
    saw_tool_calls = False
    accept_token = config.accept_token

    async for chunk in upstream_byte_iter:
        if passthrough_mode:
            yield chunk
            continue

        buffered_raw_chunks.append(chunk)
        sse_line_buffer.extend(chunk)

        while b"\n\n" in sse_line_buffer:
            idx = sse_line_buffer.index(b"\n\n")
            raw_event = bytes(sse_line_buffer[:idx])
            del sse_line_buffer[: idx + 2]

            event_text = raw_event.decode("utf-8", errors="replace")
            for line in event_text.split("\n"):
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0] or {}).get("delta") or {}
                if delta.get("tool_calls"):
                    saw_tool_calls = True
                if delta.get("content"):
                    accumulated_text += str(delta["content"])

            stripped = accumulated_text.lstrip()
            if saw_tool_calls:
                passthrough_mode = True
                break
            if (
                stripped
                and not accept_token.startswith(stripped)
                and not stripped.startswith(accept_token)
            ):
                passthrough_mode = True
                break
            if len(stripped) > len(accept_token) + 8:
                passthrough_mode = True
                break

        if passthrough_mode:
            metrics.frontier_modified_count += 1
            for bchunk in buffered_raw_chunks:
                yield bchunk
            buffered_raw_chunks.clear()

    if not passthrough_mode:
        if response_accepted_draft(accumulated_text, config):
            metrics.frontier_accepted_count += 1
            saved_tokens = max(0, draft.draft_tokens_est - 2)
            saved_usd = (saved_tokens / 1_000_000.0) * config.estimated_output_usd_per_million
            metrics.estimated_output_tokens_saved += saved_tokens
            metrics.estimated_usd_saved += saved_usd
            if callable(on_accepted_callback):
                on_accepted_callback(saved_tokens, saved_usd)

            for sse_bytes in synthesize_openai_sse_from_draft(draft, model=model):
                yield sse_bytes
        else:
            metrics.frontier_modified_count += 1
            for bchunk in buffered_raw_chunks:
                yield bchunk
