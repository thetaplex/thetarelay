"""Confusion matrix of the step classifier on calls.jsonl, and the over-cautious calls.

Usage: uv run python scripts/step_eval/report.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from thetarelay.steps import SEVERITY, classify

rows = [
    json.loads(line) for line in Path(__file__).with_name("calls.jsonl").read_text().splitlines()
]
pairs = [(r["label"], classify(r["tool"], r["arguments"]), r) for r in rows]
groups = list(SEVERITY)

print(f"{len(rows)} calls; rows: label, columns: classified\n")
print(f"{'':>13}" + "".join(f"{g:>13}" for g in groups))
for label in groups:
    counts = Counter(got for lab, got, _ in pairs if lab == label)
    print(f"{label:>13}" + "".join(f"{counts[g]:>13}" for g in groups))

under = [(lab, got, r) for lab, got, r in pairs if SEVERITY[got] < SEVERITY[lab]]
over = [(lab, got, r) for lab, got, r in pairs if SEVERITY[got] > SEVERITY[lab]]
print(f"\nless severe than label (gate failures): {len(under)}")
for lab, got, r in under:
    print(f"  {lab} -> {got}: {r['tool']} {r['arguments'][:100]}")
print(f"more severe than label (over-cautious): {len(over)}")
for (lab, got), n in Counter((lab, got) for lab, got, _ in over).most_common():
    total = sum(1 for la, _, _ in pairs if la == lab)
    print(f"  {lab} -> {got}: {n} ({n / total:.0%})")
for lab, got, r in over:
    print(f"    {lab} -> {got}: {json.loads(r['arguments']).get('command', r['tool'])[:100]!r}")
