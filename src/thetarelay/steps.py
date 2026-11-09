"""What an agent tool call does to the world: read, run, write or irreversible.

`read`, `run` and `write` are allowlists; anything else is `irreversible`, so a missed case is
always treated with the most care. Spec: openspec step-classification.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import PurePosixPath
from typing import Any, Literal

Group = Literal["read", "run", "write", "irreversible"]
SEVERITY: dict[Group, int] = {"read": 0, "run": 1, "write": 2, "irreversible": 3}

# Tool names of Pi, OpenCode and Claude Code, lowercased.
READ_TOOLS = frozenset(
    {"read", "read_file", "view", "grep", "glob", "ls", "list", "find"}
    | {"todowrite", "todoread", "webfetch", "websearch"}
)
WRITE_TOOLS = frozenset({"edit", "multiedit", "write", "patch", "notebookedit"})
SHELL_TOOLS = frozenset({"bash", "shell", "run_command", "execute_command"})

_READ_PROGRAMS = frozenset(
    {"cat", "head", "tail", "ls", "pwd", "cd", "echo", "printf", "true", "grep", "egrep"}
    | {"fgrep", "rg", "tree", "file", "stat", "wc", "which", "diff", "sort", "uniq", "jq"}
    | {"nl", "cut", "tr", "du", "basename", "dirname", "realpath", "date", "sed", "awk", "find"}
)
_GIT_READ = frozenset({"status", "diff", "log", "show", "blame", "grep", "ls-files", "rev-parse"})
_GIT_WRITE = frozenset({"add", "commit", "checkout", "switch", "restore", "stash"})
_INTERPRETERS = frozenset({"python", "python3", "node"})
_RUNNERS = frozenset({"pytest", "just", "make"}) | _INTERPRETERS
_SUBCOMMANDS: dict[str, dict[str, Group]] = {
    "uv": {
        "run": "run",
        "add": "write",
        "remove": "write",
        "sync": "write",
        "lock": "write",
        "--version": "read",
        "-V": "read",
    },
    "npm": {"test": "run", "run": "run", "install": "write", "ci": "write", "i": "write"},
    "pnpm": {"test": "run", "run": "run", "install": "write", "add": "write", "i": "write"},
    "cargo": {"test": "run", "build": "run", "check": "run"},
    "go": {"test": "run", "build": "run", "vet": "run"},
}
_UV_PIP: dict[str, Group] = {
    "list": "read",
    "show": "read",
    "freeze": "read",
    "install": "write",
    "uninstall": "write",
    "sync": "write",
}
_WRITE_PROGRAMS = frozenset({"mv", "cp", "rm", "mkdir", "touch"})
# A runner argument with one of these words leaves the working tree (`make deploy`).
_PUBLISHES = re.compile(r"deploy|release|publish|push|upload", re.IGNORECASE)
_SED_WRITE = re.compile(r"(?:^|[;{}\s/])[we](?:\s|$)")  # sed `w file` and `e cmd`
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")

_SEPARATORS = frozenset({"&&", "||", ";", ";;", "|", "|&", "&", "(", ")"})
_REDIRECTS = ("&>>", "&>", ">>", ">&", ">|", "<<<", "<&", "<>", ">", "<")
_OPERATORS = sorted(_SEPARATORS | set(_REDIRECTS), key=len, reverse=True)


class _Unparsable(Exception):
    pass


def most_severe(groups: list[Group]) -> Group | None:
    if not groups:
        return None
    return max(groups, key=lambda g: SEVERITY[g])


def classify(name: str, arguments: str | dict[str, Any]) -> Group:
    """Group of one tool call, from its name and its arguments (JSON text or a dict)."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except ValueError:
        return "irreversible"
    tool = name.lower()
    if not isinstance(args, dict):
        return "irreversible"
    if tool in READ_TOOLS:
        return "read"
    if tool in WRITE_TOOLS:
        return "write"
    if tool in SHELL_TOOLS and isinstance(args.get("command"), str):
        return shell_group(args["command"])
    return "irreversible"


def shell_group(command: str) -> Group:
    """Group of a shell command: the most severe group of its parts."""
    try:
        tokens, subs = _tokenize(command)
    except _Unparsable:
        return "irreversible"
    groups = [shell_group(s) for s in subs]
    segment: list[str] = []
    floor: Group = "read"
    i = 0
    while i <= len(tokens):
        kind, text = tokens[i] if i < len(tokens) else ("op", ";")
        if kind == "op" and text in _SEPARATORS:
            groups.append(most_severe([_program_group(segment), floor]) or "read")
            segment, floor = [], "read"
        elif kind == "op":  # a redirect; the next word is its target
            target = tokens[i + 1][1] if i + 1 < len(tokens) else ""
            i += 1
            harmless = target == "/dev/null" or (text in (">&", "<&") and target in "-0123456789")
            if ">" in text and not harmless:
                floor = "write"
        else:
            segment.append(text)
        i += 1
    return most_severe(groups) or "read"


def _program_group(words: list[str]) -> Group:
    while words and _ASSIGNMENT.match(words[0]):
        words = words[1:]  # FOO=bar cmd
    if not words:
        return "read"
    prog, args = PurePosixPath(words[0]).name, words[1:]
    if prog == "timeout":  # timeout [-s SIG] [-k T] DURATION cmd …: the wrapped command decides
        while args and args[0].startswith("-"):
            args = args[2:] if args[0] in ("-s", "-k", "--signal", "--kill-after") else args[1:]
        return _program_group(args[1:]) if len(args) > 1 else "irreversible"
    if prog == "uv" and args[:1] == ["pip"]:
        return _UV_PIP.get(args[1] if len(args) > 1 else "", "irreversible")
    if prog == "git":
        return _git_group(args)
    if prog in _INTERPRETERS and args and all(a in ("--version", "-V") for a in args):
        return "read"
    if prog in _INTERPRETERS and any(a in ("-c", "-e", "-") for a in args):
        # Inline code is new code from the model, usually editing files: a local write.
        # ponytail: classify `irreversible` if turn logs show inline code making network calls.
        return "write"
    if prog == "uv" and args[:1] == ["run"]:
        # ponytail: skips uv's flags but not their values (`uv run --with x python`).
        start = next((k for k, a in enumerate(args[1:], 1) if not a.startswith("-")), len(args))
        if start < len(args) and PurePosixPath(args[start]).name in _INTERPRETERS:
            # `uv run` syncs the environment first, so it is at least `run`.
            return most_severe([_program_group(args[start:]), "run"]) or "run"
    if prog in _RUNNERS or prog in _SUBCOMMANDS:
        sub = args[0] if args else ""
        group: Group = "run" if prog in _RUNNERS else _SUBCOMMANDS[prog].get(sub, "irreversible")
        if group == "run" and any(_PUBLISHES.search(a) for a in args):
            return "irreversible"
        return group
    if prog in _WRITE_PROGRAMS:
        return "write"
    if prog not in _READ_PROGRAMS:
        return "irreversible"
    if prog == "sed":
        if any(a.startswith(("-i", "--in-place")) for a in args):
            return "write"
        scripts = [a for a in args if not a.startswith("-")]
        return "irreversible" if any(_SED_WRITE.search(s) for s in scripts) else "read"
    if prog == "awk":
        return "irreversible" if any(c in a for a in args for c in ("system", "|", ">")) else "read"
    if prog == "find":
        if {"-exec", "-execdir", "-ok", "-okdir"} & set(args):
            return "irreversible"
        return "write" if {"-delete", "-fprint", "-fprintf", "-fls"} & set(args) else "read"
    if prog == "rg" and any(a.startswith("--pre") for a in args):
        return "irreversible"
    return "read"


def _git_group(args: list[str]) -> Group:
    while args and args[0] in ("-C", "-c", "--no-pager"):
        args = args[1:] if args[0] == "--no-pager" else args[2:]
    sub = args[0] if args else ""
    if sub in _GIT_READ:
        return "write" if any(a.startswith("--output") for a in args) else "read"
    return "write" if sub in _GIT_WRITE else "irreversible"


def _tokenize(cmd: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Split a command into ("word", text) and ("op", operator) tokens, quote-aware.

    Returns the tokens and the commands inside substitutions. Heredoc bodies and comments are
    dropped; unquoted newlines become `;`. Raises _Unparsable on unclosed quotes or brackets.

    ponytail: hand-written subset of POSIX sh, enough for agent commands; unknown syntax is
    either a word (and so an unknown program) or _Unparsable, never a silent read.
    """
    tokens: list[tuple[str, str]] = []
    subs: list[str] = []
    heredocs: list[tuple[str, bool, bool]] = []  # delimiter, strip tabs, body expands
    buf: list[str] = []
    quoted = False  # the current word had a quote, so "" is a word
    i, n = 0, len(cmd)

    def flush() -> None:
        nonlocal quoted
        if buf or quoted:
            tokens.append(("word", "".join(buf)))
        buf.clear()
        quoted = False

    def substitution(start: int) -> int:
        """Record the command in $( … ) or <( … ) opening at `start`; return the index after it."""
        depth, j, q = 1, start, ""
        while j < n:
            ch = cmd[j]
            if q:
                q = "" if ch == q else q
            elif ch in "'\"":
                q = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    subs.append(cmd[start:j])
                    return j + 1
            j += 1
        raise _Unparsable

    while i < n:
        c = cmd[i]
        if c == "\\":
            if cmd[i + 1 : i + 2] != "\n":
                buf.append(cmd[i + 1 : i + 2])
            i += 2
        elif c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                raise _Unparsable
            buf.append(cmd[i + 1 : j])
            quoted, i = True, j + 1
        elif c == '"':
            quoted, i = True, i + 1
            while True:
                if i >= n:
                    raise _Unparsable
                if cmd[i] == '"':
                    i += 1
                    break
                if cmd[i] == "\\":
                    buf.append(cmd[i + 1 : i + 2])
                    i += 2
                elif cmd.startswith("$((", i):
                    i = _skip_arithmetic(cmd, i)
                elif cmd.startswith("$(", i):
                    i = substitution(i + 2)
                elif cmd[i] == "`":
                    i = _backticks(cmd, i, subs)
                else:
                    buf.append(cmd[i])
                    i += 1
        elif cmd.startswith("$((", i):
            i = _skip_arithmetic(cmd, i)
            buf.append("0")
        elif cmd.startswith(("$(", "<(", ">("), i):
            if c != "$":
                flush()
            i = substitution(i + 2)
            buf.append("_")
        elif c == "`":
            i = _backticks(cmd, i, subs)
            buf.append("_")
        elif c == "#" and not buf and not quoted:
            j = cmd.find("\n", i)
            i = n if j < 0 else j
        elif c in " \t":
            flush()
            i += 1
        elif c == "\n":
            flush()
            tokens.append(("op", ";"))
            i = _skip_heredoc_bodies(cmd, i + 1, heredocs, subs)
            heredocs.clear()
        elif (m := _HEREDOC.match(cmd, i)) is not None:
            flush()
            heredocs.append((m.group(3), m.group(1) == "-", not m.group(2)))
            i = m.end()
        elif op := next((o for o in _OPERATORS if cmd.startswith(o, i)), ""):
            if op in _REDIRECTS and "".join(buf).isdigit() and not quoted:
                buf.clear()  # `2>`: the digit is the file descriptor, not a word
            flush()
            tokens.append(("op", op))
            i += len(op)
        else:
            buf.append(c)
            i += 1
    flush()
    return tokens, subs


def _skip_arithmetic(cmd: str, i: int) -> int:
    j = cmd.find("))", i + 3)
    if j < 0:
        raise _Unparsable
    return j + 2


def _backticks(cmd: str, i: int, subs: list[str]) -> int:
    j = cmd.find("`", i + 1)
    if j < 0:
        raise _Unparsable
    subs.append(cmd[i + 1 : j])
    return j + 1


def _skip_heredoc_bodies(
    cmd: str, i: int, heredocs: list[tuple[str, bool, bool]], subs: list[str]
) -> int:
    """Skip the bodies of heredocs opened on the previous line; return the index after them."""
    for delimiter, strip_tabs, expands in heredocs:
        body: list[str] = []
        while i < len(cmd):
            j = cmd.find("\n", i)
            line = cmd[i : len(cmd) if j < 0 else j]
            i = len(cmd) if j < 0 else j + 1
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                break
            body.append(line)
        text = "\n".join(body)
        if expands and ("$(" in text or "`" in text):
            # ponytail: an unquoted heredoc runs its substitutions; classify the whole body as
            # a command (fails closed). Parse the substitutions alone if this over-escalates.
            subs.append(text)
    return i


def tool_calls(raw: bytes) -> list[tuple[str, str]]:
    """(name, JSON arguments) of each tool call in a buffered OpenAI or Anthropic reply."""
    try:
        objs = [json.loads(raw)]
    except ValueError:
        objs = []
        for line in raw.split(b"\n"):
            if line.startswith(b"data:"):
                with contextlib.suppress(ValueError):
                    objs.append(json.loads(line[5:]))
    calls: dict[int, list[str]] = {}
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for choice in obj.get("choices") or []:  # OpenAI: message, or streamed deltas
            msg = (choice.get("message") or choice.get("delta") or {}) if choice else {}
            for i, tc in enumerate(msg.get("tool_calls") or []):
                fn = tc.get("function") or {}
                call = calls.setdefault(tc.get("index", i), ["", ""])
                call[0] += fn.get("name") or ""
                call[1] += fn.get("arguments") or ""
        content = obj.get("content")
        for i, block in enumerate(content if isinstance(content, list) else []):
            if isinstance(block, dict) and block.get("type") == "tool_use":  # Anthropic message
                calls[i] = [block.get("name", ""), json.dumps(block.get("input", {}))]
        block = obj.get("content_block") or {}
        delta = obj.get("delta") or {}
        if obj.get("type") == "content_block_start" and block.get("type") == "tool_use":
            calls[obj.get("index", 0)] = [block.get("name", ""), ""]
        elif delta.get("type") == "input_json_delta" and obj.get("index", 0) in calls:
            calls[obj.get("index", 0)][1] += delta.get("partial_json", "")
    return [(name, args or "{}") for name, args in calls.values()]


def signals_tool_call(raw: bytes) -> bool:
    """True if a buffered OpenAI or Anthropic reply (JSON or SSE) ends in a tool call."""
    flat = raw.replace(b" ", b"")
    return b'"finish_reason":"tool_calls"' in flat or b'"stop_reason":"tool_use"' in flat


def reply_group(raw: bytes) -> Group | None:
    """Most severe group of a reply's tool calls; None for a reply without tool calls."""
    calls = tool_calls(raw)
    if not calls:
        return "irreversible" if signals_tool_call(raw) else None
    return most_severe([classify(name, args) for name, args in calls])
