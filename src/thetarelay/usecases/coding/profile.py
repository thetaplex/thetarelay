"""Drafting prompts for coding agents (Claude Code, OpenCode, Pi, Cursor, Aider)."""

from __future__ import annotations

from ..base import UseCaseProfile

CODING = UseCaseProfile(
    name="coding",
    description="Coding agents (Claude Code, OpenCode, Pi, Cursor, Aider)",
    tool_drafting_instructions=(
        "[DRAFTING MODE: If a tool call is appropriate for the next step, "
        "invoke the tool directly with exact arguments. Otherwise output a concise "
        "code block or direct answer without unnecessary preamble.]"
    ),
    text_drafting_instructions=(
        "[DRAFTING MODE: Output ONLY a single ```python code block (or tool call) "
        "containing the minimal implementation. Do NOT write explanations before or after "
        "the code, do NOT write docstrings longer than 1 line, and do NOT include "
        "`if __name__ == '__main__':` or example usage.]"
    ),
    eval_suite="thetarelay.usecases.coding.suite",
)
