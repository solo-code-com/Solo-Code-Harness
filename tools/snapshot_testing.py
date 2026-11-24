#!/usr/bin/env python3
"""
snapshot_testing.py — Snapshot testing framework for CLI output validation.

Inspired by DeepSeek harness ACP snapshot testing pattern.
Records CLI execution output and compares against stored fixtures.

Usage:
    python tools/snapshot_testing.py --record test_name
    python tools/snapshot_testing.py --replay test_name
    python tools/snapshot_testing.py --self-test
"""

import argparse
import json
import sys
from datetime import datetime
from difflib import unified_diff
from pathlib import Path
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(".solocode/testing/snapshots/fixtures")
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)


# ── Core Functions ───────────────────────────────────────────────────────────


def normalize_output(data: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize output data by stripping timestamps, absolute paths, and
    other non-deterministic fields.

    Args:
        data: Raw output dictionary

    Returns:
        Normalized dictionary safe for snapshot comparison
    """
    normalized = data.copy()

    # Remove timestamp fields
    for key in ["timestamp", "created_at", "updated_at", "time"]:
        normalized.pop(key, None)

    # Normalize paths to relative
    if "path" in normalized and isinstance(normalized["path"], str):
        normalized["path"] = Path(normalized["path"]).name

    # Recursively normalize nested dicts
    for key, value in normalized.items():
        if isinstance(value, dict):
            normalized[key] = normalize_output(value)
        elif isinstance(value, list):
            normalized[key] = [
                normalize_output(item) if isinstance(item, dict) else item
                for item in value
            ]

    return normalized


def record_snapshot(test_name: str, output_data: dict[str, Any]) -> Path:
    """
    Record a snapshot fixture to disk.

    Args:
        test_name: Unique name for this test case
        output_data: CLI output data to save

    Returns:
        Path to the saved fixture file
    """
    normalized = normalize_output(output_data)
    fixture_path = FIXTURES_DIR / f"{test_name}.json"

    with open(fixture_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, sort_keys=True)

    print(f"[OK] Recorded snapshot: {fixture_path}")
    return fixture_path


def replay_snapshot(test_name: str) -> dict[str, Any] | None:
    """
    Load a snapshot fixture from disk.

    Args:
        test_name: Name of the test case

    Returns:
        Loaded fixture data, or None if not found
    """
    fixture_path = FIXTURES_DIR / f"{test_name}.json"

    if not fixture_path.exists():
        print(f"[FAIL] Snapshot not found: {fixture_path}", file=sys.stderr)
        return None

    with open(fixture_path, encoding="utf-8") as f:
        data = json.load(f)

    print(f"[OK] Loaded snapshot: {fixture_path}")
    return data


def compare_snapshots(
    expected: dict[str, Any], actual: dict[str, Any], test_name: str
) -> bool:
    """
    Compare two snapshots and report differences.

    Args:
        expected: Expected snapshot data
        actual: Actual output data
        test_name: Name of the test being compared

    Returns:
        True if snapshots match, False otherwise
    """
    # Normalize actual before comparison
    actual_normalized = normalize_output(actual)

    # Convert to sorted JSON strings for comparison
    expected_str = json.dumps(expected, indent=2, sort_keys=True)
    actual_str = json.dumps(actual_normalized, indent=2, sort_keys=True)

    if expected_str == actual_str:
        print(f"[OK] Snapshot match: {test_name}")
        return True

    # Show diff
    print(f"[FAIL] Snapshot mismatch: {test_name}", file=sys.stderr)
    print("\nDiff:", file=sys.stderr)

    diff = unified_diff(
        expected_str.splitlines(keepends=True),
        actual_str.splitlines(keepends=True),
        fromfile="expected",
        tofile="actual",
        lineterm="",
    )

    for line in diff:
        print(line.rstrip(), file=sys.stderr)

    return False


# ── Self-Test ────────────────────────────────────────────────────────────────


def run_self_test() -> bool:
    """
    Run self-test demonstrating record/replay/compare cycle.

    Returns:
        True if all tests pass
    """
    print("Running self-test...")

    test_name = "self_test_example"

    # Sample data mimicking CLI output
    sample_output = {
        "status": "success",
        "timestamp": datetime.now().isoformat(),  # Will be stripped
        "path": "/absolute/path/to/file.py",  # Will be normalized
        "tokens": {"input": 100, "output": 50, "cached": 20},
        "model": "deepseek-v4-pro",
    }

    # Step 1: Record
    print("\n1. Recording snapshot...")
    record_snapshot(test_name, sample_output)

    # Step 2: Replay
    print("\n2. Replaying snapshot...")
    loaded = replay_snapshot(test_name)

    if loaded is None:
        print("[FAIL] Self-test failed: could not load snapshot", file=sys.stderr)
        return False

    # Step 3: Compare (should match)
    print("\n3. Comparing snapshots...")

    # Create new output with different timestamp but same structure
    new_output = sample_output.copy()
    new_output["timestamp"] = datetime.now().isoformat()

    if not compare_snapshots(loaded, new_output, test_name):
        print(
            "[FAIL] Self-test failed: snapshots should match after normalization",
            file=sys.stderr,
        )
        return False

    # Step 4: Test mismatch detection
    print("\n4. Testing mismatch detection...")

    mismatched_output = sample_output.copy()
    mismatched_output["model"] = "different-model"

    if compare_snapshots(loaded, mismatched_output, test_name):
        print("[FAIL] Self-test failed: should detect model change", file=sys.stderr)
        return False

    print("\n[OK] All self-tests passed!")

    # Cleanup
    fixture_path = FIXTURES_DIR / f"{test_name}.json"
    fixture_path.unlink()
    print(f"[OK] Cleaned up: {fixture_path}")

    return True


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Snapshot testing framework for CLI output validation"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--record", metavar="NAME", help="Record a new snapshot with the given name"
    )
    group.add_argument("--replay", metavar="NAME", help="Replay an existing snapshot")
    group.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-test demonstrating record/replay/compare",
    )

    parser.add_argument(
        "--data", type=str, help="JSON string of data to record (for --record mode)"
    )

    args = parser.parse_args()

    if args.self_test:
        success = run_self_test()
        sys.exit(0 if success else 1)

    if args.record:
        if not args.data:
            print("Error: --data required for --record mode", file=sys.stderr)
            sys.exit(1)

        try:
            data = json.loads(args.data)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON: {e}", file=sys.stderr)
            sys.exit(1)

        record_snapshot(args.record, data)
        sys.exit(0)

    if args.replay:
        loaded = replay_snapshot(args.replay)
        if loaded is None:
            sys.exit(1)

        print(json.dumps(loaded, indent=2))
        sys.exit(0)


if __name__ == "__main__":
    main()
