#!/usr/bin/env python3
"""
Benchmark executor models - compare performance, cost, and quality.

Tests multiple models on standard coding tasks and reports:
- Success rate
- Token usage (input, output, reasoning, cache)
- Cost per task
- Execution time
- Code quality (passes tests/lint)

Usage:
    python tools/benchmark_executors.py
    python tools/benchmark_executors.py --models glm-5.2 qwen3.8-27b deepseek-v4-flash
    python tools/benchmark_executors.py --tasks simple medium complex
"""

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Test Tasks ───────────────────────────────────────────────────────────────

TASKS = {
    "simple_function": {
        "prompt": """Write a Python function `fibonacci(n: int) -> int` that returns the nth Fibonacci number.
Use iterative approach, not recursive. Add docstring and type hints.
Save to temp_test_fib.py""",
        "verify": "python temp_test_fib.py",
        "expected_file": "temp_test_fib.py",
        "complexity": "simple",
    },
    "refactor_code": {
        "prompt": """Refactor this code in temp_messy.py to be cleaner:

```python
# temp_messy.py
def calc(a,b,op):
    if op=='add':return a+b
    elif op=='sub':return a-b
    elif op=='mul':return a*b
    elif op=='div':return a/b if b!=0 else None
```

Make it: use dict dispatch, add type hints, handle errors properly, docstring.""",
        "setup": {
            "temp_messy.py": """def calc(a,b,op):
    if op=='add':return a+b
    elif op=='sub':return a-b
    elif op=='mul':return a*b
    elif op=='div':return a/b if b!=0 else None
""",
        },
        "verify": "python -c 'import temp_messy'",
        "expected_file": "temp_messy.py",
        "complexity": "medium",
    },
    "add_test": {
        "prompt": """Add pytest tests for this function in temp_util.py:

```python
# temp_util.py
def parse_version(ver: str) -> tuple[int, int, int]:
    \"\"\"Parse semantic version string like '1.2.3' into (1, 2, 3).\"\"\"
    parts = ver.split('.')
    return tuple(int(p) for p in parts)
```

Create temp_test_util.py with 4 test cases: valid, invalid format, missing parts, extra parts.""",
        "setup": {
            "temp_util.py": """def parse_version(ver: str) -> tuple[int, int, int]:
    \"\"\"Parse semantic version string like '1.2.3' into (1, 2, 3).\"\"\"
    parts = ver.split('.')
    return tuple(int(p) for p in parts)
""",
        },
        "verify": "python -m pytest temp_test_util.py -v",
        "expected_file": "temp_test_util.py",
        "complexity": "medium",
    },
}

# ── Models to Benchmark ──────────────────────────────────────────────────────

MODELS = {
    "glm-5": {
        "id": "opencode-go/glm-5.3",
        "provider": "opencode-go",
        "display": "GLM-5 (OpenCode Go)",
    },
    "glm-5.1": {
        "id": "opencode-go/glm-5.2",
        "provider": "opencode-go",
        "display": "GLM-5.1 (OpenCode Go)",
    },
    "qwen3.6-plus": {
        "id": "opencode-go/qwen3.8-flash",
        "provider": "opencode-go",
        "display": "Qwen 3.6 Plus",
    },
    "qwen3.7-max": {
        "id": "opencode-go/qwen3.7-plus",
        "provider": "opencode-go",
        "display": "Qwen 3.7 Max",
    },
    "qwen3.8-27b": {
        "id": "opencode-go/qwen3.8-flash",  # closest match to 3.8 27B on opencode-go
        "provider": "opencode-go",
        "display": "Qwen 3.8 27B (Alibaba)",
    },
    "deepseek-v4-flash": {
        "id": "opencode-go/deepseek-v4-pro",
        "provider": "opencode-go",
        "display": "DeepSeek V4 Flash",
    },
    "mimo-v2.6-flash-free": {
        "id": "opencode/mimo-v2.6-flash-free",
        "provider": "opencode",
        "display": "Mimo v2.6 Flash Free",
    },
    "deepseek-v4-pro": {
        "id": "opencode-go/deepseek-v4-pro",
        "provider": "opencode-go",
        "display": "DeepSeek V4 Pro (baseline)",
    },
    # Added 2026-10-06 (Step 5 Preview). `id` verified against the provider /models
    # endpoints; `deepseek-v4.1-flash` is unavailable on the official DeepSeek API,
    # so it is reachable here only through OpenCode or OpenRouter.
    "deepseek-v4.1-flash": {
        "id": "opencode-go/deepseek-v4.1-flash",
        "provider": "opencode-go",
        "display": "DeepSeek V4.1 Flash",
    },
    "deepseek-v4.1-flash-openrouter": {
        "id": "openrouter/deepseek-v4.1-flash",
        "provider": "openrouter",
        "display": "DeepSeek V4.1 Flash (OpenRouter)",
    },
    "claude-sonnet-5.5": {
        "id": "opencode-go/claude-haiku-5-5",
        "provider": "opencode-go",
        "display": "Claude Sonnet 5.5",
    },
    "grok-4.7": {
        "id": "opencode-go/grok-4.7",
        "provider": "opencode-go",
        "display": "Grok 4.7",
    },
    "gpt-6-sol": {
        "id": "opencode-go/gpt-6-luna",
        "provider": "opencode-go",
        "display": "GPT-6 Sol",
    },
    "mimo-v2.6-pro": {
        "id": "opencode-go/mimo-v2.6-pro",
        "provider": "opencode-go",
        "display": "MiMo V2.6 Pro",
    },
    "step-5-preview": {
        "id": "opencode-go/step-5-preview",
        "provider": "opencode-go",
        "display": "Step 5 Preview",
    },
}

# ── Data Structures ──────────────────────────────────────────────────────────


@dataclass
class BenchmarkResult:
    model: str
    task: str
    success: bool
    elapsed_s: float
    tokens_input: int | None
    tokens_output: int | None
    tokens_reasoning: int | None
    tokens_cache_read: int | None
    tokens_cache_write: int | None
    cost_usd: float | None
    error: str | None
    file_created: bool
    verification_passed: bool
    timestamp: str


# ── Runner ───────────────────────────────────────────────────────────────────


def setup_task(task: dict[str, Any]) -> None:
    """Prepare filesystem fixtures before running a benchmark task."""
    if "setup" not in task:
        return
    setup_val = task["setup"]
    if isinstance(setup_val, dict):
        for file_path, content in setup_val.items():
            Path(file_path).write_text(content, encoding="utf-8")
    elif isinstance(setup_val, str):
        try:
            subprocess.run(
                shlex.split(setup_val),
                capture_output=True,
                check=False,
                shell=False,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            print(f"[benchmark] setup timed out after 600s: {setup_val!r}")


def run_task_with_model(
    task_name: str,
    task: dict[str, Any],
    model_id: str,
    model_display: str,
) -> BenchmarkResult:
    """Run one task with one model, return result."""
    print(f"  [{model_display}] {task_name}...", end="", flush=True)

    # Setup if needed
    setup_task(task)

    # Run OpenCode delegation
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [
                sys.executable,  # S607: Use sys.executable instead of "python"
                "tools/opencode_delegate.py",
                task["prompt"],
                "--model",
                model_id,
                "--timeout",
                "180",
            ],
            capture_output=True,
            text=True,
            timeout=200,
            check=False,
        )
        elapsed = time.monotonic() - start

        if proc.returncode != 0:
            print(" FAIL")
            return BenchmarkResult(
                model=model_display,
                task=task_name,
                success=False,
                elapsed_s=elapsed,
                tokens_input=None,
                tokens_output=None,
                tokens_reasoning=None,
                tokens_cache_read=None,
                tokens_cache_write=None,
                cost_usd=None,
                error=f"Exit code {proc.returncode}",
                file_created=False,
                verification_passed=False,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        # Parse stderr for token stats
        stderr = proc.stderr
        tokens_in, tokens_out, tokens_reason = None, None, None
        tokens_cache_r, tokens_cache_w = None, None
        cost = None

        for line in stderr.split("\n"):
            if "tokens:" in line:
                # tokens: in=123 out=456 reasoning=78 total=657 cache_read=0 cache_write=0
                parts = line.split("tokens:")[1].strip()
                for kv in parts.split():
                    if "=" in kv:
                        k, v = kv.split("=")
                        if k == "in":
                            tokens_in = int(v)
                        elif k == "out":
                            tokens_out = int(v)
                        elif k == "reasoning":
                            tokens_reason = int(v)
                        elif k == "cache_read":
                            tokens_cache_r = int(v)
                        elif k == "cache_write":
                            tokens_cache_w = int(v)
            if "cost:" in line:
                # cost: $0.001234
                cost_str = line.split("$")[1].strip()
                cost = float(cost_str)

        # Check if file was created
        file_created = (
            Path(task["expected_file"]).exists() if "expected_file" in task else False
        )

        # Run verification if provided
        verification_passed = False
        if "verify" in task and file_created:
            # S602: Use list args instead of shell=True
            verify_proc = subprocess.run(
                shlex.split(task["verify"]),
                capture_output=True,
                timeout=30,
                check=False,
                shell=False,
            )
            verification_passed = verify_proc.returncode == 0

        cost_str = f"${cost:.6f}" if cost else "$0.000000"
        print(f" OK ({elapsed:.1f}s, {cost_str})")

        return BenchmarkResult(
            model=model_display,
            task=task_name,
            success=True,
            elapsed_s=elapsed,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            tokens_reasoning=tokens_reason,
            tokens_cache_read=tokens_cache_r,
            tokens_cache_write=tokens_cache_w,
            cost_usd=cost,
            error=None,
            file_created=file_created,
            verification_passed=verification_passed,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        print(" TIMEOUT")
        return BenchmarkResult(
            model=model_display,
            task=task_name,
            success=False,
            elapsed_s=elapsed,
            tokens_input=None,
            tokens_output=None,
            tokens_reasoning=None,
            tokens_cache_read=None,
            tokens_cache_write=None,
            cost_usd=None,
            error="Timeout after 200s",
            file_created=False,
            verification_passed=False,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        # Catch specific exceptions: subprocess errors, file I/O, parsing errors
        elapsed = time.monotonic() - start
        print(f" ERROR: {exc}")
        return BenchmarkResult(
            model=model_display,
            task=task_name,
            success=False,
            elapsed_s=elapsed,
            tokens_input=None,
            tokens_output=None,
            tokens_reasoning=None,
            tokens_cache_read=None,
            tokens_cache_write=None,
            cost_usd=None,
            error=str(exc),
            file_created=False,
            verification_passed=False,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        # Cleanup temp files
        for f in [
            "temp_test_fib.py",
            "temp_messy.py",
            "temp_util.py",
            "temp_test_util.py",
        ]:
            Path(f).unlink(missing_ok=True)


def print_summary(results: list[BenchmarkResult]) -> None:
    """Print benchmark summary table."""
    print("\n" + "=" * 80)
    print("BENCHMARK SUMMARY")
    print("=" * 80)

    # Group by model
    by_model: dict[str, list[BenchmarkResult]] = {}
    for r in results:
        by_model.setdefault(r.model, []).append(r)

    for model, model_results in by_model.items():
        print(f"\n{model}")
        print("-" * 80)

        total_tasks = len(model_results)
        successful = sum(1 for r in model_results if r.success)
        verified = sum(1 for r in model_results if r.verification_passed)
        total_cost = sum(r.cost_usd for r in model_results if r.cost_usd)
        avg_time = (
            sum(r.elapsed_s for r in model_results) / total_tasks if total_tasks else 0
        )

        print(
            f"  Success rate: {successful}/{total_tasks} ({successful / total_tasks * 100:.0f}%)"
        )
        print(
            f"  Verification pass: {verified}/{total_tasks} ({verified / total_tasks * 100:.0f}%)"
        )
        print(f"  Total cost: ${total_cost:.6f}")
        print(f"  Avg time: {avg_time:.1f}s")

        # Token stats
        total_in = sum(r.tokens_input or 0 for r in model_results)
        total_out = sum(r.tokens_output or 0 for r in model_results)
        total_reason = sum(r.tokens_reasoning or 0 for r in model_results)
        print(f"  Tokens: in={total_in} out={total_out} reasoning={total_reason}")

    print("\n" + "=" * 80)
    print("WINNER ANALYSIS")
    print("=" * 80)

    # Find best by cost
    model_costs = {m: sum(r.cost_usd or 0 for r in rs) for m, rs in by_model.items()}
    cheapest = min(model_costs, key=model_costs.get)
    print(f"  [COST] Cheapest: {cheapest} (${model_costs[cheapest]:.6f})")

    # Find best by speed
    model_times = {
        m: sum(r.elapsed_s for r in rs) / len(rs) for m, rs in by_model.items()
    }
    fastest = min(model_times, key=model_times.get)
    print(f"  [SPEED] Fastest: {fastest} ({model_times[fastest]:.1f}s avg)")

    # Find best by quality
    model_quality = {
        m: sum(1 for r in rs if r.verification_passed) / len(rs)
        for m, rs in by_model.items()
    }
    best_quality = max(model_quality, key=model_quality.get)
    print(
        f"  [QUALITY] Best quality: {best_quality} ({model_quality[best_quality] * 100:.0f}% pass)"
    )

    # Value score: quality / cost (higher is better)
    model_value = {m: model_quality[m] / (model_costs[m] + 0.00001) for m in by_model}
    best_value = max(model_value, key=model_value.get)
    print(f"  [VALUE] Best value (quality/cost): {best_value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark executor models")
    parser.add_argument(
        "--models",
        nargs="+",
        choices=list(MODELS.keys()),
        default=["glm-5", "qwen3.7-max", "deepseek-v4-pro"],
        help="Models to benchmark",
    )
    parser.add_argument(
        "--tasks",
        nargs="+",
        choices=list(TASKS.keys()) + ["simple", "medium", "complex"],
        default=["simple_function", "refactor_code"],
        help="Tasks to run (or complexity: simple/medium/complex)",
    )
    parser.add_argument(
        "--output",
        default=".solocode/benchmark-results.jsonl",
        help="Output file for raw results",
    )

    args = parser.parse_args(argv)

    # Resolve complexity filters
    task_names = []
    for t in args.tasks:
        if t in ["simple", "medium", "complex"]:
            task_names.extend([k for k, v in TASKS.items() if v["complexity"] == t])
        else:
            task_names.append(t)
    task_names = list(dict.fromkeys(task_names))  # dedupe, preserve order

    print("=" * 80)
    print(f"BENCHMARKING {len(args.models)} models × {len(task_names)} tasks")
    print("=" * 80)

    results: list[BenchmarkResult] = []

    for task_name in task_names:
        task = TASKS[task_name]
        print(f"\n{task_name} ({task['complexity']}):")

        for model_key in args.models:
            model = MODELS[model_key]
            result = run_task_with_model(task_name, task, model["id"], model["display"])
            results.append(result)

    # Save raw results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r)) + "\n")
    print(f"\nRaw results saved to {output_path}")

    # Print summary
    print_summary(results)

    return 0


if __name__ == "__main__":
    sys.exit(main())
