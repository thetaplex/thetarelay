"""`bench` / `eval` suite for the coding use case."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...config import SpeculativeConfig
from ...evals import EvalSuite
from .humaneval import load_humaneval_from_file, run_humaneval_benchmark
from .scenarios import get_builtin_scenarios, verify_code_and_tool_accuracy


async def _run_humaneval(
    config: SpeculativeConfig,
    *,
    dataset: Path | None,
    limit: int | None,
    target_model: str | None,
    target_base_url: str | None,
    concise_baseline: bool,
    mutants: int,
) -> dict[str, Any]:
    return await run_humaneval_benchmark(
        config,
        problems=load_humaneval_from_file(dataset) if dataset else None,
        limit=limit,
        target_model=target_model,
        target_base_url=target_base_url,
        concise_baseline=concise_baseline,
        mutants=mutants,
    )


SUITE = EvalSuite(
    usecase="coding",
    builtin_scenarios=get_builtin_scenarios,
    verify_accuracy=verify_code_and_tool_accuracy,
    default_category="code_edit",
    extra_benchmarks={"humaneval": _run_humaneval},
)
