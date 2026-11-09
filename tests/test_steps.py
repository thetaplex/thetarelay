"""Step classification: one case per scenario in openspec step-classification."""

from __future__ import annotations

import json

import pytest

from thetarelay.steps import classify, reply_group


def bash(command: str) -> tuple[str, str]:
    return "bash", json.dumps({"command": command})


HEREDOC_WRITE = "cat > app.py <<'EOF'\nimport os\nos.remove('x')\nrm -rf /\nEOF"
HEREDOC_RUN = "uv run pytest - <<'EOF'\nprint('hi')\nEOF\n"

CASES: list[tuple[str, str, str, str]] = [
    # Four step groups
    ("read tool", "Read", '{"file_path": "a.py"}', "read"),
    ("read tool lower", "grep", '{"pattern": "x"}', "read"),
    ("glob tool", "GLOB", "{}", "read"),
    ("edit tool", "Edit", '{"file_path": "a.py"}', "write"),
    ("multiedit tool", "MultiEdit", "{}", "write"),
    ("notebook tool", "NotebookEdit", "{}", "write"),
    # Agent-internal and web tools
    ("todo", "TodoWrite", '{"todos": []}', "read"),
    ("web fetch", "WebFetch", '{"url": "https://x"}', "read"),
    ("sub-agent", "Task", '{"prompt": "x"}', "irreversible"),
    # Unknown and unparsable steps fail closed
    ("unknown tool", "send_email", "{}", "irreversible"),
    ("bad json", "read", "not json", "irreversible"),
    ("no command", "bash", "{}", "irreversible"),
    ("unclosed quote", *bash("cat 'unclosed"), "irreversible"),
    # Shell commands are classified by their most severe part
    ("pipeline of reads", *bash("rg foo src | head -20 && git diff"), "read"),
    ("read then edit", *bash("cat a.py && sed -i s/a/b/ a.py"), "write"),
    ("background job", *bash("ls & git push"), "irreversible"),
    ("newline separated", *bash("ls\ngit push"), "irreversible"),
    ("subshell", *bash("(cd src && ls)"), "read"),
    # Heredoc bodies are data
    ("heredoc write", *bash(HEREDOC_WRITE), "write"),
    ("heredoc run", *bash(HEREDOC_RUN), "run"),
    ("heredoc then push", *bash(HEREDOC_RUN + "git push"), "irreversible"),
    # Read-only shell programs
    ("sed print", *bash("sed -n 40,80p src/app.py"), "read"),
    ("awk print", *bash("awk '{print $1}' a.txt"), "read"),
    ("quoted operator", *bash('grep ">" a.html'), "read"),
    ("comment", *bash("ls  # list; git push"), "read"),
    ("git read", *bash("git -C repo log --oneline -5"), "read"),
    ("find delete", *bash("find . -name '*.pyc' -delete"), "write"),
    ("git output", *bash("git diff --output=x.patch"), "write"),
    ("rg pre", *bash("rg --pre ./run.sh foo"), "irreversible"),
    ("sed in place", *bash("sed -i s/a/b/ x.py"), "write"),
    ("sed write command", *bash("sed -n 's/a/b/w out.txt' x"), "irreversible"),
    ("awk system", *bash("awk '{system(\"rm \" $1)}' list"), "irreversible"),
    ("find exec", *bash("find . -exec rm {} ;"), "irreversible"),
    # Output redirection and substitution
    ("redirect to file", *bash("cat a.py > b.py"), "write"),
    ("redirect lookalike", *bash("cat a.py > /dev/null.bak"), "write"),
    ("append", *bash("echo x >> notes.md"), "write"),
    ("null device", *bash("rg foo 2>/dev/null"), "read"),
    ("fd redirect", *bash("cat a.py 2>&1"), "read"),
    ("all to null", *bash("ls &>/dev/null"), "read"),
    ("substitution", *bash("echo $(git push)"), "irreversible"),
    ("backticks", *bash("echo `git push`"), "irreversible"),
    ("read substitution", *bash('echo "$(git rev-parse HEAD)"'), "read"),
    ("unclosed substitution", *bash("echo $(ls"), "irreversible"),
    ("process substitution", *bash("diff <(ls a) <(ls b)"), "read"),
    ("input redirect", *bash("wc -l < a.py"), "read"),
    # Project code runs
    ("test run", *bash("uv run pytest -q"), "run"),
    ("just test", *bash("just test"), "run"),
    ("python script", *bash("python3 scripts/x.py"), "run"),
    # Inline interpreter code
    ("python -c", *bash('python3 -c \'open("a", "w").write("x")\''), "write"),
    ("python heredoc", *bash("python3 - <<'EOF'\nprint(1)\nEOF"), "write"),
    ("node -e", *bash("node -e 'require(\"fs\")'"), "write"),
    ("uv run python -c", *bash("uv run python -c 'import app'"), "write"),
    ("uv run python script", *bash("uv run python scripts/x.py"), "run"),
    ("python version", *bash("python3 --version"), "read"),
    ("env prefix", *bash("PYTHONPATH=src pytest tests"), "run"),
    ("venv path", *bash(".venv/bin/pytest -x"), "run"),
    ("make deploy", *bash("make deploy"), "irreversible"),
    ("just release", *bash("just release"), "irreversible"),
    ("npm publish script", *bash("npm run publish"), "irreversible"),
    ("run then push", *bash("just test && git push"), "irreversible"),
    # Local writes
    ("commit", *bash('git add -A && git commit -m "fix"'), "write"),
    ("multi-line commit", *bash('git commit -m "a\n\nb"'), "write"),
    ("remove", *bash("rm -rf build"), "write"),
    ("mkdir", *bash("mkdir -p src/app && touch src/app/__init__.py"), "write"),
    ("uv add", *bash("uv add httpx"), "write"),
    ("uv pip install", *bash("uv pip install -r requirements.txt"), "write"),
    # Widened from the step evaluation
    ("uv version", *bash("uv --version && uv pip list"), "read"),
    ("timeout wrapper", *bash("timeout 5 uv run pytest -x"), "run"),
    ("timeout wrapping push", *bash("timeout -s KILL 60 git push"), "irreversible"),
    # Everything else is irreversible
    ("push", *bash("git push origin main"), "irreversible"),
    ("curl", *bash("curl -s https://example.com"), "irreversible"),
    ("gh", *bash("gh pr merge 12"), "irreversible"),
    ("terraform", *bash("terraform apply"), "irreversible"),
    ("sudo", *bash("sudo ls"), "irreversible"),
    ("empty command", *bash("   "), "read"),
]


@pytest.mark.parametrize(("case", "name", "args", "group"), CASES, ids=[c[0] for c in CASES])
def test_classify(case: str, name: str, args: str, group: str) -> None:
    assert classify(name, args) == group


def _sse(events: list[dict[str, object]]) -> bytes:
    return b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)


def test_reply_group_both_formats() -> None:
    args = json.dumps({"command": "git push"})
    anth_stream = _sse(
        [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "name": "bash", "input": {}},
            },
            *[
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": part},
                }
                for part in (args[:7], args[7:])
            ],
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        ]
    )
    assert reply_group(anth_stream) == "irreversible"

    anth_json = json.dumps(
        {
            "content": [
                {"type": "tool_use", "name": "read", "input": {"path": "a"}},
                {"type": "tool_use", "name": "edit", "input": {"path": "a"}},
            ],
            "stop_reason": "tool_use",
        }
    ).encode()
    assert reply_group(anth_json) == "write"

    oai_stream = (
        _sse(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"name": "bash", "arguments": '{"comm'},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": 'and": "ls"}'}}
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            ]
        )
        + b"data: [DONE]\n\n"
    )
    assert reply_group(oai_stream) == "read"

    oai_json = json.dumps(
        {
            "choices": [
                {
                    "message": {"tool_calls": [{"function": {"name": "Read", "arguments": "{}"}}]},
                    "finish_reason": "tool_calls",
                }
            ]
        }
    ).encode()
    assert reply_group(oai_json) == "read"


def test_reply_group_text_only_and_unparsable() -> None:
    text = json.dumps({"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"})
    assert reply_group(text.encode()) is None
    # A tool call is signalled but cannot be read.
    assert reply_group(b'{"stop_reason": "tool_use", "content": "garbled"}') == "irreversible"
    # Streamed arguments cut off mid-way.
    cut = _sse(
        [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "name": "read", "input": {}},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"pa'},
            },
        ]
    )
    assert reply_group(cut) == "irreversible"
    # A tool with no arguments streams no deltas.
    empty = _sse(
        [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "name": "TodoRead", "input": {}},
            }
        ]
    )
    assert reply_group(empty) == "read"
