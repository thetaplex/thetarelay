"""Configuration of the ThetaRelay routing proxy. Every value has an environment variable."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class RouterConfig:
    """Where requests go and how the judge chooses between the local and frontier tiers."""

    host: str = "127.0.0.1"
    port: int = 8788

    # Easy tier: a server with the Anthropic Messages and OpenAI Chat Completions APIs (Ollama).
    # Base URL without `/v1`.
    local_base_url: str = "http://127.0.0.1:11434"
    local_model: str = "gemma4:e4b"
    local_disable_thinking: bool = True
    # Requests larger than this (characters of system + messages + tools) go to the frontier
    # tier, because the local model cannot read them. About 4 characters per token.
    local_max_request_chars: int = 100_000

    # Hard tier: a server with both APIs (OpenRouter). Base URL without `/v1`.
    frontier_base_url: str = "https://openrouter.ai/api"
    frontier_model: str = "openai/gpt-6.1-terra"
    # Empty: forward the client's own credentials unchanged.
    frontier_api_key: str = ""

    # Judge: an endpoint with the decision API: Ollama `/v1/systemone` (tev1), or OpenRouter
    # `/api/alpha/decisions` (`typesafe/jev-1.14`, needs `judge_api_key`).
    judge_url: str = "http://127.0.0.1:11434/v1/systemone"
    judge_model: str = "tev1"
    judge_api_key: str = ""
    judge_timeout_seconds: float = 10.0
    # Route to the local tier only when the judge gives P(easy) above this value.
    easy_threshold: float = 0.7
    # After a failed tool result, keep this many requests of the conversation on the frontier.
    stay_requests: int = 6

    turn_log_path: str = ""
    debug: bool = False
    enable_headroom_middleware: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.easy_threshold <= 1.0:
            raise ValueError(f"easy_threshold must be between 0 and 1, got {self.easy_threshold}")

    @classmethod
    def from_env(cls) -> RouterConfig:
        return cls(
            host=_env("THETARELAY_HOST", cls.host),
            port=_env_int("THETARELAY_PORT", cls.port),
            local_base_url=_env("THETARELAY_LOCAL_BASE_URL", cls.local_base_url).rstrip("/"),
            local_model=_env("THETARELAY_LOCAL_MODEL", cls.local_model),
            local_disable_thinking=_env_bool(
                "THETARELAY_LOCAL_DISABLE_THINKING", cls.local_disable_thinking
            ),
            local_max_request_chars=_env_int(
                "THETARELAY_LOCAL_MAX_REQUEST_CHARS", cls.local_max_request_chars
            ),
            frontier_base_url=_env("THETARELAY_FRONTIER_BASE_URL", cls.frontier_base_url).rstrip(
                "/"
            ),
            frontier_model=_env("THETARELAY_FRONTIER_MODEL", cls.frontier_model),
            frontier_api_key=_env("THETARELAY_FRONTIER_API_KEY", "")
            or _env("OPENROUTER_API_KEY", ""),
            judge_url=_env("THETARELAY_JUDGE_URL", cls.judge_url),
            judge_model=_env("THETARELAY_JUDGE_MODEL", cls.judge_model),
            judge_api_key=_env("THETARELAY_JUDGE_API_KEY", ""),
            judge_timeout_seconds=_env_float("THETARELAY_JUDGE_TIMEOUT", cls.judge_timeout_seconds),
            easy_threshold=_env_float("THETARELAY_EASY_THRESHOLD", cls.easy_threshold),
            stay_requests=_env_int("THETARELAY_STAY_REQUESTS", cls.stay_requests),
            turn_log_path=_env("THETARELAY_TURN_LOG", ""),
            debug=_env_bool("THETARELAY_DEBUG", False),
            enable_headroom_middleware=_env_bool("THETARELAY_HEADROOM_MIDDLEWARE", True),
        )
