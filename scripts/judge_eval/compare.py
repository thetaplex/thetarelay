"""Compare tev1:4b, tev1:0.8b (local Ollama) and Jev (OpenRouter) as easy/hard routers.

Usage: uv run python scripts/judge_eval/compare.py scripts/judge_eval/turns.jsonl 0.7
Needs `ollama pull tev1 tev1:0.8b` and OPENROUTER_API_KEY (Jev costs ~$0.001 per run).
"""

import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

QUESTION = {
    "route": {
        "type": "choice",
        "instructions": (
            "A coding agent is about to take its next step. Can a small local model handle "
            "this step correctly, or does it need a frontier model?"
        ),
        "criteria": {
            "easy": "Routine: read or search files, list, run a command, trivial one-line "
            "edit, or a simple factual answer",
            "hard": "Needs reasoning: debugging a failure, planning, design, multi-file or "
            "subtle edits, reviews, performance or concurrency problems",
        },
    }
}
JUDGES = {
    "tev1:4b": ("http://localhost:11434/v1/systemone", "tev1", {}),
    "tev1:0.8b": ("http://localhost:11434/v1/systemone", "tev1:0.8b", {}),
    "jev": (
        "https://openrouter.ai/api/alpha/decisions",
        "typesafe/jev-1.14",
        {"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY', '')}"},
    ),
}
THRESHOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 0.8  # P(easy) needed to go local

turns = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines() if line]
results: dict[str, list[tuple[float, float]]] = {}
with httpx.Client(timeout=120) as client:
    for name, (url, model, headers) in JUDGES.items():
        body = {"model": model, "questions": QUESTION}
        if "localhost" in url:
            body["keep_alive"] = "30m"
            client.post(url, json={**body, "state": {"task": "warm", "last": "up"}})
        rows = []
        for t in turns:
            t0 = time.perf_counter()
            r = client.post(
                url,
                headers=headers,
                json={**body, "state": {"task": t["task"], "last_message": t["last"]}},
            )
            r.raise_for_status()
            rows.append(
                (
                    r.json()["answers"]["route"]["probabilities"]["easy"],
                    (time.perf_counter() - t0) * 1000,
                )
            )
        results[name] = rows

labels = [t["label"] for t in turns]
n_easy, n_hard = labels.count("easy"), labels.count("hard")
print(f"{len(turns)} turns ({n_easy} easy, {n_hard} hard), route local if P(easy) > {THRESHOLD}\n")
print(f"{'judge':<10} {'accuracy':>8} {'hard->local':>12} {'easy caught':>12} {'median ms':>10}")
routes = {}
for name, rows in results.items():
    local = [p > THRESHOLD for p, _ in rows]
    routes[name] = local
    acc = sum((lab == "easy") == loc for lab, loc in zip(labels, local, strict=True)) / len(turns)
    miss = sum(loc for lab, loc in zip(labels, local, strict=True) if lab == "hard")
    caught = sum(loc for lab, loc in zip(labels, local, strict=True) if lab == "easy")
    ms = statistics.median(m for _, m in rows)
    print(f"{name:<10} {acc:>8.0%} {miss:>5}/{n_hard:<6} {caught:>5}/{n_easy:<6} {ms:>10.0f}")

agree = sum(a == b for a, b in zip(routes["tev1:4b"], routes["tev1:0.8b"], strict=True)) / len(
    turns
)
print(f"\ntev1:0.8b agrees with tev1:4b on {agree:.0%} of routing decisions")
for name in ("tev1:4b", "tev1:0.8b"):
    a = sum(x == y for x, y in zip(routes[name], routes["jev"], strict=True)) / len(turns)
    print(f"{name} agrees with jev on {a:.0%}")
print("\nhard turns routed local:")
for name, local in routes.items():
    for t, loc, (p, _) in zip(turns, local, results[name], strict=True):
        if loc and t["label"] == "hard":
            print(f"  {name:<10} P(easy)={p:.2f}  {t['last'][:80]}")

print("\nthreshold sweep (hard->local / easy caught):")
for th in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
    cells = []
    for name, rows in results.items():
        miss = sum(p > th for (p, _), lab in zip(rows, labels, strict=True) if lab == "hard")
        caught = sum(p > th for (p, _), lab in zip(rows, labels, strict=True) if lab == "easy")
        cells.append(f"{name} {miss:>2}/{caught:<2}")
    print(f"  {th:.1f}: " + "   ".join(cells))
# AUC: chance a random easy turn gets higher P(easy) than a random hard turn
for name, rows in results.items():
    e = [p for (p, _), lab in zip(rows, labels, strict=True) if lab == "easy"]
    h = [p for (p, _), lab in zip(rows, labels, strict=True) if lab == "hard"]
    auc = sum((a > b) + 0.5 * (a == b) for a in e for b in h) / (len(e) * len(h))
    print(f"AUC {name}: {auc:.3f}")
