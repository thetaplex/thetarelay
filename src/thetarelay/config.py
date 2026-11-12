"""Configuration of the ThetaRelay routing proxy. Every value has an environment variable."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


def _env(name: str, default: str) -> str:
    return _raw(name, default).strip()


def _raw(name: str, default: str | int | float) -> str:
    return str(os.environ.get(name, default))


def _env_bool(name: str, default: bool) -> bool:
    val = _raw(name, "")
    if not val:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(_raw(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_raw(name, default))
    except ValueError:
        return default


@dataclass
class RouterConfig:
    """Where requests go and how the judge chooses between the lite and frontier tiers."""

    host: str = "127.0.0.1"
    port: int = 8788

    # Easy tier: a server with the Anthropic Messages and OpenAI Chat Completions APIs (Ollama).
    # Base URL without `/v1`.
    # Defaults: the only setup measured to cut cost (Opus 5.5 + Haiku 5.5 on OpenRouter, hybrid,
    # threshold 0.7, stay 0). Other models are untested; see docs/reference/tested-models.md.
    lite_base_url: str = "https://openrouter.ai/api"
    lite_model: str = "anthropic/claude-haiku-5.5"
    # Empty: send no credentials (a local server). A hosted lite model needs it.
    lite_api_key: str = ""
    lite_disable_thinking: bool = True
    # Requests larger than this (characters of system + messages + tools) go to the frontier
    # tier, because the lite model cannot read them. About 4 characters per token.
    lite_max_request_chars: int = 100_000
    # Seconds the lite server may stay silent (before the first byte, or between chunks)
    # before the request falls back to the frontier tier. A hung lite model costs this much.
    lite_timeout_seconds: float = 30.0

    # Hard tier: a server with both APIs (OpenRouter). Base URL without `/v1`.
    frontier_base_url: str = "https://openrouter.ai/api"
    frontier_model: str = "anthropic/claude-opus-5.5"
    # Empty: forward the client's own credentials unchanged.
    frontier_api_key: str = ""

    # Judge: an endpoint with the decision API: Ollama `/v1/systemone` (tev1), or OpenRouter
    # `/api/alpha/decisions` (`typesafe/jev-1.14`, needs `judge_api_key`).
    judge_url: str = "http://127.0.0.1:11434/v1/systemone"
    judge_model: str = "tev1"
    judge_api_key: str = ""
    judge_timeout_seconds: float = 10.0
    # Route to the lite tier only when the judge gives P(easy) above this value.
    easy_threshold: float = 0.7
    # After a failed tool result, keep this many requests of the conversation on the frontier.
    stay_requests: int = 0
    # `hybrid` (default) or `judge` (the judge alone); `random`: the same rules, but a coin with
    # P(lite) = random_lite_share replaces the judge; `lite` / `frontier`: every request to one
    # tier, no fallback.
    # `random`, `lite` and `frontier` are evaluation baselines. `triage`: no judge; the frontier
    # model gets each request with a short note and either does the step or answers `<EASY/>`,
    # and then the request goes to the lite tier. `hybrid`: the judge first; if P(easy) is not
    # above the threshold, the frontier model triages instead of a plain frontier request.
    route_mode: str = "hybrid"
    # Why route_mode has its value: "set" (by the user), "default", "openai frontier model".
    route_mode_reason: Literal["set", "default", "openai frontier model"] = "default"
    random_lite_share: float = 0.5
    # Share of frontier requests also sent to the lite tier as a shadow; above 0 the judge is
    # asked in every mode.
    shadow_share: float = 0.0
    # On: a lite reply without a tool call (it would end the agent's turn) is discarded and
    # the request goes to the frontier tier. Lite responses are buffered to check this.
    escalate_on_stop: bool = False
    # `irreversible`: discard lite replies with an irreversible or malformed tool call and ask
    # the frontier tier instead. `writes`: also discard file-editing steps.
    lite_guard: str = "off"

    turn_log_path: str = ""
    debug: bool = False
    enable_headroom_middleware: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.easy_threshold <= 1.0:
            raise ValueError(f"easy_threshold must be between 0 and 1, got {self.easy_threshold}")
        if self.route_mode not in ("judge", "triage", "hybrid", "random", "lite", "frontier"):
            raise ValueError(
                "route_mode must be judge, triage, hybrid, random, lite or frontier, "
                f"got {self.route_mode!r}"
            )
        if self.lite_guard not in ("off", "irreversible", "writes"):
            raise ValueError(
                f"lite_guard must be off, irreversible or writes, got {self.lite_guard!r}"
            )
        if not 0.0 <= self.random_lite_share <= 1.0:
            raise ValueError(
                f"random_lite_share must be between 0 and 1, got {self.random_lite_share}"
            )
        if not 0.0 <= self.shadow_share <= 1.0:
            raise ValueError(f"shadow_share must be between 0 and 1, got {self.shadow_share}")

    @classmethod
    def from_env(cls) -> RouterConfig:
        frontier_model = _env("THETARELAY_FRONTIER_MODEL", cls.frontier_model)
        mode = _env("THETARELAY_ROUTE_MODE", "")
        reason: Literal["set", "default", "openai frontier model"]
        if mode:
            reason = "set"
        elif frontier_model.startswith("openai/"):
            mode, reason = "judge", "openai frontier model"
        else:
            mode, reason = cls.route_mode, "default"
        return cls(
            host=_env("THETARELAY_HOST", cls.host),
            port=_env_int("THETARELAY_PORT", cls.port),
            lite_base_url=_env("THETARELAY_LITE_BASE_URL", cls.lite_base_url).rstrip("/"),
            lite_model=_env("THETARELAY_LITE_MODEL", cls.lite_model),
            lite_api_key=_env("THETARELAY_LITE_API_KEY", ""),
            lite_disable_thinking=_env_bool(
                "THETARELAY_LITE_DISABLE_THINKING", cls.lite_disable_thinking
            ),
            lite_max_request_chars=_env_int(
                "THETARELAY_LITE_MAX_REQUEST_CHARS", cls.lite_max_request_chars
            ),
            lite_timeout_seconds=_env_float("THETARELAY_LITE_TIMEOUT", cls.lite_timeout_seconds),
            frontier_base_url=_env("THETARELAY_FRONTIER_BASE_URL", cls.frontier_base_url).rstrip(
                "/"
            ),
            frontier_model=frontier_model,
            frontier_api_key=_env("THETARELAY_FRONTIER_API_KEY", "")
            or _env("OPENROUTER_API_KEY", ""),
            judge_url=_env("THETARELAY_JUDGE_URL", cls.judge_url),
            judge_model=_env("THETARELAY_JUDGE_MODEL", cls.judge_model),
            judge_api_key=_env("THETARELAY_JUDGE_API_KEY", ""),
            judge_timeout_seconds=_env_float("THETARELAY_JUDGE_TIMEOUT", cls.judge_timeout_seconds),
            easy_threshold=_env_float("THETARELAY_EASY_THRESHOLD", cls.easy_threshold),
            stay_requests=_env_int("THETARELAY_STAY_REQUESTS", cls.stay_requests),
            route_mode=mode,
            route_mode_reason=reason,
            random_lite_share=_env_float("THETARELAY_RANDOM_LITE_SHARE", cls.random_lite_share),
            shadow_share=_env_float("THETARELAY_SHADOW_SHARE", cls.shadow_share),
            escalate_on_stop=_env_bool("THETARELAY_ESCALATE_ON_STOP", cls.escalate_on_stop),
            lite_guard=_env("THETARELAY_LITE_GUARD", cls.lite_guard),
            turn_log_path=_env("THETARELAY_TURN_LOG", ""),
            debug=_env_bool("THETARELAY_DEBUG", False),
            enable_headroom_middleware=_env_bool("THETARELAY_HEADROOM_MIDDLEWARE", True),
        )
