"""Reference implementation of SPEC.md, used only to validate the hidden tests."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

RANK = {"high": 0, "normal": 1, "low": 2}


class Fail(Exception):
    def __init__(self, msg: str, code: int) -> None:
        super().__init__(msg)
        self.code = code


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise Fail(f"error: {message}", 2)


def parser() -> Parser:
    p = Parser(prog="todo")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)
    a = sub.add_parser("add")
    a.add_argument("text")
    a.add_argument("--priority", choices=list(RANK), default="normal")
    ls = sub.add_parser("list").add_mutually_exclusive_group()
    ls.add_argument("--done", action="store_true")
    ls.add_argument("--all", action="store_true")
    sub.add_parser("done").add_argument("ids", nargs="+")
    sub.add_parser("remove").add_argument("id")
    sub.add_parser("clear-done")
    return p


def ident(v: str) -> int:
    if not v.isdigit() or int(v) < 1:
        raise Fail(f"error: invalid id {v}", 2)
    return int(v)


def run(argv: list[str], path: Path) -> list[str]:
    args = parser().parse_args(argv)
    data: dict[str, Any] = {"next_id": 1, "tasks": []}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except ValueError:
            raise Fail(f"error: cannot read {path}", 1) from None
    tasks: list[dict[str, Any]] = data["tasks"]
    by_id = {t["id"]: t for t in tasks}

    def find(i: int) -> dict[str, Any]:
        if i not in by_id:
            raise Fail(f"error: no task {i}", 1)
        return by_id[i]

    out: list[str]
    if args.cmd == "list":
        key = lambda t: (t["done"], RANK[t["priority"]], t["id"])  # noqa: E731
        shown = [t for t in tasks if args.all or t["done"] == args.done]
        out = [
            f"{t['id']} [{'x' if t['done'] else ' '}] ({t['priority']}) {t['text']}"
            for t in sorted(shown, key=key)
        ]
        return out or ["no tasks"]
    if args.cmd == "add":
        text = args.text.strip()
        if not text:
            raise Fail("error: empty text", 2)
        i = data["next_id"]
        tasks.append({"id": i, "text": text, "priority": args.priority, "done": False})
        data["next_id"] = i + 1
        out = [f"added {i}"]
    elif args.cmd == "done":
        found = [find(ident(v)) for v in args.ids]
        for t in found:
            t["done"] = True
        out = [f"done {t['id']}" for t in found]
    elif args.cmd == "remove":
        t = find(ident(args.id))
        tasks.remove(t)
        out = [f"removed {t['id']}"]
    else:
        data["tasks"] = [t for t in tasks if not t["done"]]
        out = [f"cleared {len(tasks) - len(data['tasks'])}"]
    path.write_text(json.dumps(data))
    return out


def main(argv: list[str]) -> int:
    path = Path(os.environ.get("TODO_FILE", "todo.json"))
    try:
        lines = run(argv, path)
    except Fail as e:
        print(e, file=sys.stderr)
        return e.code
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
