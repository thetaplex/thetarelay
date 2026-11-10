"""Route mode default by frontier model, and the start-up line."""

from __future__ import annotations

import pytest
import uvicorn

from thetarelay import cli
from thetarelay.config import RouterConfig

OPENAI = "openai/gpt-6.1-terra"
REASON = "OpenAI frontier models do not hand steps to the lite tier, so the triage step is off"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THETARELAY_ROUTE_MODE", raising=False)
    monkeypatch.delenv("THETARELAY_FRONTIER_MODEL", raising=False)


@pytest.mark.parametrize(
    ("model", "mode_env", "mode", "reason"),
    [
        (OPENAI, None, "judge", "openai frontier model"),
        ("anthropic/claude-opus-5.5", None, "hybrid", "default"),
        (OPENAI, "", "judge", "openai frontier model"),
        (OPENAI, "hybrid", "hybrid", "set"),
    ],
)
def test_default_mode_by_frontier_model(
    monkeypatch: pytest.MonkeyPatch, model: str, mode_env: str | None, mode: str, reason: str
) -> None:
    monkeypatch.setenv("THETARELAY_FRONTIER_MODEL", model)
    if mode_env is not None:
        monkeypatch.setenv("THETARELAY_ROUTE_MODE", mode_env)
    cfg = RouterConfig.from_env()
    assert (cfg.route_mode, cfg.route_mode_reason) == (mode, reason)


def _startup(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(cli, "create_app", lambda cfg: None)
    cli.main(["proxy"])
    return capsys.readouterr().err


def test_startup_mode_set_by_user(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("THETARELAY_ROUTE_MODE", "triage")
    err = _startup(monkeypatch, capsys)
    assert "route mode: triage" in err
    assert "do not hand steps" not in err


def test_startup_mode_chosen_by_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("THETARELAY_FRONTIER_MODEL", OPENAI)
    err = _startup(monkeypatch, capsys)
    assert f"route mode: judge ({REASON})" in err
