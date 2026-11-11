"""Hidden acceptance tests for SPEC.md. The agents never see this file."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from app.cli import main

Todo = Callable[..., tuple[int, list[str], str]]


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tasks.json"
    monkeypatch.setenv("TODO_FILE", str(path))
    return path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, list[str], str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out.splitlines(), err


@pytest.fixture
def todo(db: Path, capsys: pytest.CaptureFixture[str]) -> Todo:
    def call(*argv: str) -> tuple[int, list[str], str]:
        return run(capsys, *argv)

    return call


# --- add --------------------------------------------------------------------------------


def test_add_prints_id_and_ids_increase(todo: Todo) -> None:
    assert todo("add", "Buy milk") == (0, ["added 1"], "")
    assert todo("add", "Walk dog")[1] == ["added 2"]


def test_add_trims_and_defaults_to_normal(todo: Todo) -> None:
    todo("add", "  Buy milk  ")
    assert todo("list") == (0, ["1 [ ] (normal) Buy milk"], "")


def test_add_with_priority(todo: Todo) -> None:
    todo("add", "Pay rent", "--priority", "high")
    assert todo("list")[1] == ["1 [ ] (high) Pay rent"]


@pytest.mark.parametrize("text", ["   "])
def test_add_empty_text(todo: Todo, db: Path, text: str) -> None:
    code, out, err = todo("add", text)
    assert code == 2
    assert out == []
    assert "error: empty text" in err
    assert not db.exists()


def test_add_bad_priority(todo: Todo, db: Path) -> None:
    code, out, err = todo("add", "x", "--priority", "urgent")
    assert code == 2
    assert out == []
    assert err
    assert not db.exists()


# --- list -------------------------------------------------------------------------------


def test_list_empty_does_not_create_file(todo: Todo, db: Path) -> None:
    assert todo("list") == (0, ["no tasks"], "")
    assert not db.exists()


def test_list_sorted_by_priority_then_id(todo: Todo) -> None:
    todo("add", "a", "--priority", "low")
    todo("add", "b")
    todo("add", "c", "--priority", "high")
    todo("add", "d", "--priority", "low")
    todo("add", "e", "--priority", "high")
    assert todo("list")[1] == [
        "3 [ ] (high) c",
        "5 [ ] (high) e",
        "2 [ ] (normal) b",
        "1 [ ] (low) a",
        "4 [ ] (low) d",
    ]


def test_list_filters(todo: Todo) -> None:
    todo("add", "a", "--priority", "low")
    todo("add", "b", "--priority", "high")
    todo("add", "c")
    todo("add", "d", "--priority", "high")
    todo("done", "1", "4")
    assert todo("list")[1] == ["2 [ ] (high) b", "3 [ ] (normal) c"]
    assert todo("list", "--done")[1] == ["4 [x] (high) d", "1 [x] (low) a"]
    assert todo("list", "--all")[1] == [
        "2 [ ] (high) b",
        "3 [ ] (normal) c",
        "4 [x] (high) d",
        "1 [x] (low) a",
    ]


def test_list_done_when_none_done(todo: Todo) -> None:
    todo("add", "a")
    assert todo("list", "--done") == (0, ["no tasks"], "")


# --- done -------------------------------------------------------------------------------


def test_done_several_in_given_order(todo: Todo) -> None:
    for t in "abc":
        todo("add", t)
    assert todo("done", "3", "1") == (0, ["done 3", "done 1"], "")
    assert todo("list")[1] == ["2 [ ] (normal) b"]


def test_done_twice_is_allowed(todo: Todo) -> None:
    todo("add", "a")
    todo("done", "1")
    assert todo("done", "1") == (0, ["done 1"], "")


def test_done_unknown_id_changes_nothing(todo: Todo, db: Path) -> None:
    todo("add", "a")
    before = db.read_bytes()
    code, out, err = todo("done", "1", "99")
    assert (code, out) == (1, [])
    assert "error: no task 99" in err
    assert db.read_bytes() == before
    assert todo("list")[1] == ["1 [ ] (normal) a"]


@pytest.mark.parametrize("value", ["abc", "0"])
def test_invalid_id(todo: Todo, value: str) -> None:
    todo("add", "a")
    for cmd in ("done", "remove"):
        code, out, err = todo(cmd, value)
        assert (code, out) == (2, [])
        assert f"error: invalid id {value}" in err


# --- remove and clear-done --------------------------------------------------------------


def test_remove(todo: Todo) -> None:
    todo("add", "a")
    todo("add", "b")
    assert todo("remove", "1") == (0, ["removed 1"], "")
    assert todo("list")[1] == ["2 [ ] (normal) b"]
    code, out, err = todo("remove", "1")
    assert (code, out) == (1, [])
    assert "error: no task 1" in err


def test_clear_done(todo: Todo) -> None:
    for t in "abc":
        todo("add", t)
    todo("done", "1", "3")
    assert todo("clear-done") == (0, ["cleared 2"], "")
    assert todo("list", "--all")[1] == ["2 [ ] (normal) b"]
    assert todo("clear-done")[1] == ["cleared 0"]


def test_ids_never_reused(todo: Todo) -> None:
    todo("add", "a")
    todo("add", "b")
    todo("remove", "2")
    todo("done", "1")
    todo("clear-done")
    assert todo("add", "c")[1] == ["added 3"]


# --- usage errors -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [[], ["frobnicate"], ["add"], ["list", "--bogus"]],
)
def test_usage_errors_exit_2(todo: Todo, argv: list[str]) -> None:
    code, out, err = todo(*argv)
    assert code == 2
    assert out == []
    assert err


# --- data file --------------------------------------------------------------------------


def test_data_file_is_json_and_persists(todo: Todo, db: Path) -> None:
    todo("add", "a", "--priority", "high")
    json.loads(db.read_text())
    assert todo("list")[1] == ["1 [ ] (high) a"]


def test_corrupt_file(todo: Todo, db: Path) -> None:
    db.write_text("{not json")
    for argv in (["list"], ["add", "a"]):
        code, out, err = todo(*argv)
        assert (code, out) == (1, [])
        assert f"error: cannot read {db}" in err
    assert db.read_text() == "{not json"


def test_default_file_in_current_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TODO_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert run(capsys, "add", "a")[0] == 0
    assert (tmp_path / "todo.json").is_file()


def test_files_are_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "one.json"))
    run(capsys, "add", "a")
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "two.json"))
    assert run(capsys, "list")[1] == ["no tasks"]
