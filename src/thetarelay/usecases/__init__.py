"""Use cases: everything that assumes a particular kind of agent.

The speculative engine and the `bench` / `eval` harness are domain-agnostic. Each use case is a
sub-package with:

- `profile.py`: a `UseCaseProfile` with the drafting prompts for the local model.
- `suite.py`: a `SUITE` (`thetarelay.evals.EvalSuite`) with the built-in scenarios, the accuracy
  check and any extra benchmarks (for coding: HumanEval).

`SpeculativeConfig.usecase` selects the use case. To support another kind of agent, add a
sub-package and register its profile in `USECASES`.
"""

from __future__ import annotations

from .base import UseCaseProfile
from .coding.profile import CODING

DEFAULT_USECASE = CODING.name

USECASES: dict[str, UseCaseProfile] = {CODING.name: CODING}


def get_usecase(name: str) -> UseCaseProfile:
    """Return the profile for `name`. Raise `ValueError` if it is not known."""
    try:
        return USECASES[name]
    except KeyError:
        known = ", ".join(sorted(USECASES))
        raise ValueError(f"Unknown use case {name!r}. Known use cases: {known}") from None


__all__ = ["DEFAULT_USECASE", "USECASES", "UseCaseProfile", "get_usecase"]
