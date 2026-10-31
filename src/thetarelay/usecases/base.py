"""The `UseCaseProfile` type. Kept free of heavy imports so `config` can import the registry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UseCaseProfile:
    """Prompts and settings for one kind of agent."""

    name: str
    description: str
    # System message for the local drafter when the request offers tools.
    tool_drafting_instructions: str
    # System message for the local drafter when the request has no tools.
    text_drafting_instructions: str
    # Dotted path of the module whose `SUITE` (a `thetarelay.evals.EvalSuite`) holds the
    # `bench` / `eval` scenarios, accuracy check and extra benchmarks. Imported lazily.
    eval_suite: str

    def drafting_instructions(self, has_tools: bool) -> str:
        """Return the drafting system message for a request with or without tools."""
        return self.tool_drafting_instructions if has_tools else self.text_drafting_instructions
