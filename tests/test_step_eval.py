"""Step classification on real tool calls (scripts/step_eval/calls.jsonl).

Spec: openspec step-classification-eval. The gate: no call is classified less severe than its
hand-assigned label. Run scripts/step_eval/report.py for the full confusion matrix.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from thetarelay.steps import SEVERITY, classify

RANK: dict[str, int] = {str(g): n for g, n in SEVERITY.items()}

DATASET = Path(__file__).parents[1] / "scripts" / "step_eval" / "calls.jsonl"
SECRET = re.compile(r"sk-[A-Za-z0-9]|Bearer |ghp_|AKIA")


def rows() -> list[dict[str, str]]:
    return [json.loads(line) for line in DATASET.read_text().splitlines()]


def test_dataset_is_complete_and_clean() -> None:
    data = rows()
    assert data
    for n, row in enumerate(data, 1):
        assert set(row) >= {"agent", "tool", "arguments", "label"}, n
        assert row["label"] in RANK, f"line {n}: label {row['label']!r} not reviewed"
    assert not SECRET.search(DATASET.read_text())


def test_no_call_is_classified_less_severe_than_its_label() -> None:
    under = [
        f"{row['label']} -> {got}: {row['tool']} {row['arguments'][:120]}"
        for row in rows()
        if RANK[got := classify(row["tool"], row["arguments"])] < RANK[row["label"]]
    ]
    assert not under, "\n".join(under)
