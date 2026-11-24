#!/usr/bin/env python3
"""kilo_usage_report.py — Summarize token and cost usage from .solocode/kilo-usage.jsonl."""

import json
import sys
from collections import defaultdict
from pathlib import Path

USAGE_LOG = Path(".solocode/kilo-usage.jsonl")


def load_entries(path: Path) -> list[dict]:
    if not path.exists():
        print(f"No usage log found at {path}")
        sys.exit(0)
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def main() -> None:
    entries = load_entries(USAGE_LOG)
    if not entries:
        print("Usage log is empty.")
        return

    total_input = 0
    total_output = 0
    total_cost = 0.0
    errors = 0
    by_model: dict[str, dict] = defaultdict(
        lambda: {"input": 0, "output": 0, "cost": 0.0, "calls": 0}
    )

    for e in entries:
        model = e.get("model", "unknown")
        by_model[model]["calls"] += 1

        if e.get("error"):
            errors += 1
            continue

        usage = e.get("usage") or {}
        inp = usage.get("input") or 0
        out = usage.get("output") or 0
        cost = e.get("cost") or 0.0

        total_input += inp
        total_output += out
        total_cost += cost
        by_model[model]["input"] += inp
        by_model[model]["output"] += out
        by_model[model]["cost"] += cost

    print(f"Total calls : {len(entries)}  (errors: {errors})")
    print(
        f"Total tokens: input={total_input:,}  output={total_output:,}  total={total_input + total_output:,}"
    )
    print(f"Total cost  : ${total_cost:.6f}")
    print()
    print(f"{'Model':<45} {'Calls':>6} {'Input':>10} {'Output':>10} {'Cost':>12}")
    print("-" * 90)
    for model, stats in sorted(by_model.items()):
        print(
            f"{model:<45} {stats['calls']:>6} {stats['input']:>10,} {stats['output']:>10,} ${stats['cost']:>11.6f}"
        )


if __name__ == "__main__":
    main()
