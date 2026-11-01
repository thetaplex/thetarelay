"""Configuration for ThetaRelay speculative proxy and Headroom extension."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .usecases import DEFAULT_USECASE, get_usecase


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
    local_model: str = "gemma4:e4b"
    local_api_key: str = "local"
    local_timeout_seconds: float = 5.0
    local_max_tokens: int = 384
    local_temperature: float = 0.0
    local_disable_thinking: bool = True
    local_concise_draft_instructions: bool = True

    # Context window trimming for the local model so prefill stays sub-second
    local_max_context_chars: int = 64000
    # Size the window per conversation by measured read speed (proxy and Headroom only)
    local_adaptive_context: bool = True
    local_recent_messages_limit: int = 10

    # Speculative strategy:
    # - "auto": `prediction` for OpenAI text, `<ACCEPT/>` verification for Anthropic & tools
    # - "accept_swap": `<ACCEPT/>` verification + instant draft swap on both Anthropic and OpenAI
    # - "prediction": OpenAI `prediction` parameter only
    # - "context_only": Inject `<speculative_draft>` without `<ACCEPT/>` short-circuiting
    strategy: str = "auto"

    # Kind of agent being served. Selects the domain-specific drafting prompts in
    # `thetarelay.usecases`. Only "coding" exists today; add use cases there for other agents.
    usecase: str = DEFAULT_USECASE

    # Selective speculation: only draft on substantive turns (long enough prompts)
    selective_speculation: bool = True
    min_prompt_chars_for_draft: int = 15

    # Enable Headroom in-process CompressionMiddleware when running standalone
    enable_headroom_middleware: bool = True

    # Token marker used for fast verification short-circuiting
    accept_token: str = "<ACCEPT/>"  # noqa: S105

    # Estimated token prices ($ per 1M tokens) and throughput for savings/latency attribution
    estimated_input_usd_per_million: float = 3.0
    estimated_output_usd_per_million: float = 15.0
    estimated_frontier_output_tps: float = 65.0

    # Excluded model substrings (e.g., haiku or small fast models that don't need drafting)
    skip_models: tuple[str, ...] = field(default_factory=lambda: ("haiku", "mini", "nano", "flash"))

    def __post_init__(self) -> None:
        # Fail at startup, not on the first request, if the use case is not known.
        get_usecase(self.usecase)

    @classmethod
    def from_env(cls) -> SpeculativeConfig:
        skip_raw = os.environ.get("THETARELAY_SKIP_MODELS", "haiku,mini,nano,flash")
        skip_models = tuple(s.strip().lower() for s in skip_raw.split(",") if s.strip())
        return cls(
            host=os.environ.get("THETARELAY_HOST", "127.0.0.1"),
            port=_env_int("THETARELAY_PORT", 8788),
            anthropic_upstream_url=os.environ.get(
                "THETARELAY_ANTHROPIC_UPSTREAM", "https://api.anthropic.com"
            ).rstrip("/"),
            openai_upstream_url=os.environ.get(
                "THETARELAY_OPENAI_UPSTREAM", "https://api.openai.com"
            ).rstrip("/"),
            local_base_url=os.environ.get(
                "THETARELAY_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1"
            ).rstrip("/"),
            local_model=os.environ.get("THETARELAY_LOCAL_MODEL", "gemma4:e4b"),
            local_api_key=os.environ.get("THETARELAY_LOCAL_API_KEY", "local"),
            local_timeout_seconds=_env_float("THETARELAY_LOCAL_TIMEOUT", 5.0),
            local_max_tokens=_env_int("THETARELAY_LOCAL_MAX_TOKENS", 384),
            local_temperature=_env_float("THETARELAY_LOCAL_TEMPERATURE", 0.0),
            local_disable_thinking=_env_bool("THETARELAY_LOCAL_DISABLE_THINKING", True),
            local_concise_draft_instructions=_env_bool("THETARELAY_LOCAL_CONCISE_DRAFT", True),
            local_max_context_chars=_env_int("THETARELAY_LOCAL_MAX_CONTEXT_CHARS", 64000),
            local_adaptive_context=_env_bool("THETARELAY_LOCAL_ADAPTIVE_CONTEXT", True),
            local_recent_messages_limit=_env_int("THETARELAY_LOCAL_RECENT_MESSAGES", 10),
            strategy=os.environ.get("THETARELAY_STRATEGY", "auto").strip().lower(),
            usecase=os.environ.get("THETARELAY_USECASE", DEFAULT_USECASE).strip().lower(),
            selective_speculation=_env_bool("THETARELAY_SELECTIVE", True),
            min_prompt_chars_for_draft=_env_int("THETARELAY_MIN_PROMPT_CHARS", 15),
            enable_headroom_middleware=_env_bool("THETARELAY_HEADROOM_MIDDLEWARE", True),
            accept_token=os.environ.get("THETARELAY_ACCEPT_TOKEN", "<ACCEPT/>").strip(),
            estimated_input_usd_per_million=_env_float("THETARELAY_INPUT_USD_PER_M", 3.0),
            estimated_output_usd_per_million=_env_float("THETARELAY_OUTPUT_USD_PER_M", 15.0),
            estimated_frontier_output_tps=_env_float("THETARELAY_FRONTIER_OUTPUT_TPS", 65.0),
            skip_models=skip_models,
        )


_TARGET_API_KEY_ENV_VARS = ("THETARELAY_TARGET_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY")


def resolve_target_api_key(config: SpeculativeConfig, target_base_url: str | None) -> str:
    """Return the bearer token for the `eval` target model.

    The first non-empty variable of `THETARELAY_TARGET_API_KEY`, `OPENROUTER_API_KEY` and
    `OPENAI_API_KEY` wins. The local API key is used only when the target is the local server
    itself, so it is never sent to a different (possibly remote) host.

    Raises:
        ValueError: The target URL differs from the local URL and no target key is set.
    """
    for name in _TARGET_API_KEY_ENV_VARS:
        value = os.environ.get(name)
        if value:
            return value
    local_url = config.local_base_url.rstrip("/")
    if target_base_url is None or target_base_url.rstrip("/") == local_url:
        return config.local_api_key
    raise ValueError(
        "The target URL is not the local URL, and no target API key is set. Set one of "
        + ", ".join(_TARGET_API_KEY_ENV_VARS)
        + "."
    )
