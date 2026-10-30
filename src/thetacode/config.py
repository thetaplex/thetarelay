"""Configuration for ThetaCode speculative proxy and Headroom extension."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


@dataclass
class SpeculativeConfig:
    """Configuration for local draft generation and target model verification."""

    # Server settings (when running as standalone proxy)
    host: str = "127.0.0.1"
    port: int = 8788

    # Upstream target API URLs
    anthropic_upstream_url: str = "https://api.anthropic.com"
    openai_upstream_url: str = "https://api.openai.com"

    # Local Draft Model settings (OpenAI-compatible endpoint: Ollama, MLX, llama.cpp, vLLM)
    local_base_url: str = "http://127.0.0.1:11434/v1"
    local_model: str = "qwen2.5-coder:7b"
    local_api_key: str = "local"
    local_timeout_seconds: float = 8.0
    local_max_tokens: int = 1024
    local_temperature: float = 0.0

    # Context window trimming for the local model so prefill stays sub-second
    local_max_context_chars: int = 24000
    local_recent_messages_limit: int = 10

    # Speculative strategy:
    # - "auto": `prediction` for OpenAI text, `<ACCEPT/>` verification for Anthropic & tools
    # - "accept_swap": `<ACCEPT/>` verification + instant draft swap on both Anthropic and OpenAI
    # - "prediction": OpenAI `prediction` parameter only
    # - "context_only": Inject `<speculative_draft>` without `<ACCEPT/>` short-circuiting
    strategy: str = "auto"

    # Selective speculation: only draft on substantive code generation/editing turns
    selective_speculation: bool = True
    min_prompt_chars_for_draft: int = 15

    # Enable Headroom in-process CompressionMiddleware when running standalone
    enable_headroom_middleware: bool = True

    # Token marker used for fast verification short-circuiting
    accept_token: str = "<ACCEPT/>"  # noqa: S105

    # Estimated output token price ($ per 1M tokens) for savings attribution
    estimated_output_usd_per_million: float = 15.0

    # Excluded model substrings (e.g., haiku or small fast models that don't need drafting)
    skip_models: tuple[str, ...] = field(default_factory=lambda: ("haiku", "mini", "nano", "flash"))

    @classmethod
    def from_env(cls) -> SpeculativeConfig:
        skip_raw = os.environ.get("THETACODE_SKIP_MODELS", "haiku,mini,nano,flash")
        skip_models = tuple(s.strip().lower() for s in skip_raw.split(",") if s.strip())
        return cls(
            host=os.environ.get("THETACODE_HOST", "127.0.0.1"),
            port=_env_int("THETACODE_PORT", 8788),
            anthropic_upstream_url=os.environ.get(
                "THETACODE_ANTHROPIC_UPSTREAM", "https://api.anthropic.com"
            ).rstrip("/"),
            openai_upstream_url=os.environ.get(
                "THETACODE_OPENAI_UPSTREAM", "https://api.openai.com"
            ).rstrip("/"),
            local_base_url=os.environ.get(
                "THETACODE_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1"
            ).rstrip("/"),
            local_model=os.environ.get("THETACODE_LOCAL_MODEL", "qwen2.5-coder:7b"),
            local_api_key=os.environ.get("THETACODE_LOCAL_API_KEY", "local"),
            local_timeout_seconds=_env_float("THETACODE_LOCAL_TIMEOUT", 8.0),
            local_max_tokens=_env_int("THETACODE_LOCAL_MAX_TOKENS", 1024),
            local_temperature=_env_float("THETACODE_LOCAL_TEMPERATURE", 0.0),
            local_max_context_chars=_env_int("THETACODE_LOCAL_MAX_CONTEXT_CHARS", 24000),
            local_recent_messages_limit=_env_int("THETACODE_LOCAL_RECENT_MESSAGES", 10),
            strategy=os.environ.get("THETACODE_STRATEGY", "auto").strip().lower(),
            selective_speculation=_env_bool("THETACODE_SELECTIVE", True),
            min_prompt_chars_for_draft=_env_int("THETACODE_MIN_PROMPT_CHARS", 15),
            enable_headroom_middleware=_env_bool("THETACODE_HEADROOM_MIDDLEWARE", True),
            accept_token=os.environ.get("THETACODE_ACCEPT_TOKEN", "<ACCEPT/>").strip(),
            estimated_output_usd_per_million=_env_float("THETACODE_OUTPUT_USD_PER_M", 15.0),
            skip_models=skip_models,
        )
