"""Unit tests for ThetaRelay CLI command-line interface."""

from __future__ import annotations

import pytest

from thetarelay.cli import main


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "ThetaRelay" in captured.out
    assert "proxy" in captured.out
    assert "bench" in captured.out
    assert "eval" in captured.out


def test_cli_eval_help_includes_exec_warning(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["eval", "--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "exec()" in captured.out
    assert "humaneval" in captured.out


def test_cli_eval_missing_target_key_exits(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("THETARELAY_TARGET_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "eval",
                "--target-model",
                "gpt-4o",
                "--target-url",
                "https://api.openai.com/v1",
            ]
        )
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "no target API key is set" in captured.err


def test_cli_bench_runs(capsys: pytest.CaptureFixture[str]) -> None:
    main(["bench", "--json"])
    captured = capsys.readouterr()
    assert '"scenarios_total":' in captured.out
    assert '"speedup_factor":' in captured.out
