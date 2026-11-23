#!/usr/bin/env python3
"""
codex_usage.py — measure Codex CLI token usage, cache hit rate, and cost.

Why: Codex CLI prints only a single "tokens used" total, and the
gateway returns no cost field (its /v1/responses usage block carries tokens
only, and /api/billing needs a browser session, not an API key). The one place
the real numbers exist is Codex's own rollout log, which records a
`token_usage_record` per API call with cached_input_tokens broken out.

This reads ~/.codex/sessions/**/rollout-*.jsonl and reports, per session:
  - input / cached input / output / reasoning tokens
  - cache hit rate (cached / input)
  - estimated cost, from the PRICING table below

Pricing caveat: no gateway publishes a price list for these model ids (their
its API exposes models without cost metadata). PRICING therefore uses the
reference prices other providers publish for the same model ids — treat the
cost column as an estimate, and override the table if your invoice disagrees.

Usage:
    python tools/codex_usage.py                 # last 10 sessions
    python tools/codex_usage.py --last 30
    python tools/codex_usage.py --all
    python tools/codex_usage.py --json
    python tools/codex_usage.py --pricing gpt-5.6-luna=0.2/1.2/0.02
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

SESSIONS_DIR = Path.home() / ".codex" / "sessions"

# USD per 1,000,000 tokens: (input, output, cached_input_read).
# Reference prices for the gpt-5.6 and gpt-6 families, as published by other
# providers in the models.dev catalog. The gpt-6.2-sol figures were read from the
# OpenAI, Azure and AWS Mantle entries on 2026-11-19: 2 / 10 / 0.1. FreeModel
# itself does not publish a price list.
PRICING: dict[str, tuple[float, float, float]] = {
    "gpt-5.6-terra": (2.0, 12.0, 0.2),
    "gpt-5.6-sol": (5.0, 30.0, 0.5),
    "gpt-5.6-luna": (0.2, 1.2, 0.02),
    "gpt-6.2-sol": (2.0, 10.0, 0.1),
    "gpt-6.1-sol": (2.0, 10.0, 0.1),
}

PER_MILLION = 1_000_000

# FreeModel prepay plan (confirmed 2026-09-14, RETIRED 2026-11-23 -- kept so
#   - $20/month flat fee
#   - each rolling 5-hour window allows $20 of list-price usage
#   - a week is capped at $132 of list-price usage
# "List price" is the PRICING table above — the same per-token rates the
# official providers publish. The plan converts a fixed fee into that much
# on-demand value, so the useful comparison is value-consumed vs list price.
PLAN_MONTHLY_FEE_USD = 20.0
PLAN_WINDOW_HOURS = 5
PLAN_WINDOW_VALUE_USD = 20.0
PLAN_WEEK_CAP_USD = 132.0
WEEKS_PER_MONTH = 52 / 12
PLAN_MONTH_CAP_USD = PLAN_WEEK_CAP_USD * WEEKS_PER_MONTH


@dataclass
class Usage:
    """Accumulated token counts for one session."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    calls: int = 0

    def add(self, usage: dict[str, int]) -> None:
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.cached_input_tokens += int(usage.get("cached_input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)
        self.reasoning_output_tokens += int(usage.get("reasoning_output_tokens") or 0)
        self.calls += 1

    @property
    def uncached_input_tokens(self) -> int:
        return max(self.input_tokens - self.cached_input_tokens, 0)

    @property
    def cache_hit_rate(self) -> float:
        if not self.input_tokens:
            return 0.0
        return self.cached_input_tokens / self.input_tokens


@dataclass
class SessionReport:
    """One rollout file's aggregated usage."""

    path: Path
    session_id: str = ""
    model: str = ""
    started: str = ""
    usage: Usage = field(default_factory=Usage)

    def cost(self, pricing: dict[str, tuple[float, float, float]]) -> float | None:
        """Estimated USD cost, or None when the model has no price entry."""
        if self.model not in pricing:
            return None
        input_price, output_price, cached_price = pricing[self.model]
        return (
            self.usage.uncached_input_tokens * input_price
            + self.usage.cached_input_tokens * cached_price
            + self.usage.output_tokens * output_price
        ) / PER_MILLION


def parse_pricing_override(spec: str) -> tuple[str, tuple[float, float, float]]:
    """Parse `model=input/output/cached` from --pricing."""
    if "=" not in spec:
        raise ValueError(f"expected model=input/output/cached, got {spec!r}")
    model, values = spec.split("=", 1)
    parts = values.split("/")
    if len(parts) != 3:
        raise ValueError(f"expected three slash-separated prices, got {values!r}")
    return model.strip(), (float(parts[0]), float(parts[1]), float(parts[2]))


def _iter_jsonl(path: Path):
    """Yield parsed JSON objects, skipping unparseable lines."""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def read_session(path: Path) -> SessionReport:
    """Aggregate one rollout file into a SessionReport."""
    report = SessionReport(path=path)
    for obj in _iter_jsonl(path):
        obj_type = obj.get("type")
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}

        if obj_type == "session_meta":
            report.session_id = str(obj.get("session_id") or obj.get("id") or "")
            report.started = str(obj.get("timestamp") or "")
            report.model = str(obj.get("model") or "")
        elif obj_type == "turn_context":
            report.model = report.model or str(payload.get("model") or "")
        elif obj_type == "token_usage_record":
            usage = payload.get("usage")
            if isinstance(usage, dict):
                report.usage.add(usage)
    return report


def collect(limit: int | None) -> list[SessionReport]:
    """Load the newest `limit` rollout files (None = all)."""
    if not SESSIONS_DIR.is_dir():
        return []
    files = sorted(SESSIONS_DIR.rglob("rollout-*.jsonl"))
    if limit is not None:
        files = files[-limit:]
    return [read_session(f) for f in files]


def _fmt_cost(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def print_report(
    reports: list[SessionReport], pricing: dict[str, tuple[float, float, float]]
) -> None:
    """Print a per-session table plus a grand total."""
    if not reports:
        print(f"No Codex sessions found under {SESSIONS_DIR}")
        return

    total = Usage()
    total_cost = 0.0
    priced = True

    print(
        f"{'started':<20} {'model':<15} {'input':>9} {'cached':>9} "
        f"{'cache%':>7} {'output':>8} {'cost':>10}"
    )
    print("-" * 84)

    for report in reports:
        cost = report.cost(pricing)
        if cost is None:
            priced = False
        else:
            total_cost += cost
        started = report.started.replace("T", " ")[:19] or "-"
        model = report.model or "-"
        usage = report.usage
        print(
            f"{started:<20} {model:<15} {usage.input_tokens:>9,} "
            f"{usage.cached_input_tokens:>9,} {usage.cache_hit_rate:>6.0%} "
            f"{usage.output_tokens:>8,} {_fmt_cost(cost):>10}"
        )
        total.input_tokens += usage.input_tokens
        total.cached_input_tokens += usage.cached_input_tokens
        total.output_tokens += usage.output_tokens
        total.reasoning_output_tokens += usage.reasoning_output_tokens
        total.calls += usage.calls

    print("-" * 84)
    print(
        f"{'TOTAL':<20} {'':<15} {total.input_tokens:>9,} "
        f"{total.cached_input_tokens:>9,} {total.cache_hit_rate:>6.0%} "
        f"{total.output_tokens:>8,} {_fmt_cost(total_cost if priced else None):>10}"
    )
    print()
    print(f"sessions: {len(reports)} | api calls: {total.calls}")
    print(f"uncached input: {total.uncached_input_tokens:,} tokens")
    print(f"reasoning output: {total.reasoning_output_tokens:,} tokens")
    if not priced:
        print("NOTE: at least one model has no price entry — its cost is excluded.")
    print("Cost is an ESTIMATE from reference prices (no gateway publishes them).")


def _parse_timestamp(value: str) -> datetime | None:
    """Parse an ISO-8601 rollout timestamp into an aware datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def print_plan(
    reports: list[SessionReport],
    pricing: dict[str, tuple[float, float, float]],
    *,
    window_hours: int = PLAN_WINDOW_HOURS,
) -> None:
    """Report consumption against the retired FreeModel prepay caps.

    The plan sells a fixed fee worth of list-price usage inside a rolling
    5-hour window, capped per week. Both caps are only reachable when the
    session timestamps cover the window, so this prints how much of the log
    actually falls inside each one rather than assuming coverage.
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)
    week_start = now - timedelta(days=7)

    window_cost = 0.0
    week_cost = 0.0
    window_sessions = 0
    week_sessions = 0
    unpriced = False

    for report in reports:
        cost = report.cost(pricing)
        if cost is None:
            unpriced = True
            continue
        started = _parse_timestamp(report.started)
        if started is None:
            continue
        if started >= week_start:
            week_cost += cost
            week_sessions += 1
            if started >= window_start:
                window_cost += cost
                window_sessions += 1

    print(
        f"FreeModel prepay plan (retired): ${PLAN_MONTHLY_FEE_USD:.0f}/month, "
        f"${PLAN_WINDOW_VALUE_USD:.0f} per {window_hours}h window, "
        f"${PLAN_WEEK_CAP_USD:.0f}/week cap"
    )
    print()
    print(
        f"last {window_hours}h:  ${window_cost:>8.4f} of ${PLAN_WINDOW_VALUE_USD:<7.0f} "
        f"({window_cost / PLAN_WINDOW_VALUE_USD:>6.1%})  over {window_sessions} session(s)"
    )
    print(
        f"last 7d:   ${week_cost:>8.4f} of ${PLAN_WEEK_CAP_USD:<7.0f} "
        f"({week_cost / PLAN_WEEK_CAP_USD:>6.1%})  over {week_sessions} session(s)"
    )
    if unpriced:
        print("NOTE: sessions from unpriced models are excluded.")
    print()

    if not week_cost:
        print("No priced sessions in the last 7 days — nothing to project.")
        return

    monthly_value = week_cost * WEEKS_PER_MONTH
    print(f"Projected at this rate: ${monthly_value:,.2f}/month of list-price value")
    print(f"Subscription fee:       ${PLAN_MONTHLY_FEE_USD:,.2f}/month")
    net = monthly_value - PLAN_MONTHLY_FEE_USD
    if net >= 0:
        print(
            f"Net saving vs list price: ${net:,.2f}/month "
            f"({monthly_value / PLAN_MONTHLY_FEE_USD:.1f}x value)"
        )
    else:
        print(f"Cheaper to buy list price this month by ${abs(net):,.2f}")

    sessions_per_week = week_sessions
    break_even_week = PLAN_MONTHLY_FEE_USD / WEEKS_PER_MONTH
    if week_cost > 0 and sessions_per_week:
        per_session = week_cost / sessions_per_week
        print()
        print(
            f"Break-even: ${break_even_week:,.2f}/week of list-price value "
            f"(~{break_even_week / per_session:.0f} sessions/week at "
            f"${per_session:.4f} each)"
        )
        print(
            f"Monthly cap is ${PLAN_MONTH_CAP_USD:,.2f} of value for "
            f"${PLAN_MONTHLY_FEE_USD:.2f}."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--last", type=int, default=10, help="newest N sessions (default: 10)"
    )
    parser.add_argument("--all", action="store_true", help="include every session")
    parser.add_argument(
        "--json", action="store_true", help="emit JSON instead of a table"
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="report consumption against the retired FreeModel prepay caps",
    )
    parser.add_argument(
        "--pricing",
        action="append",
        default=[],
        metavar="MODEL=IN/OUT/CACHED",
        help="override a price entry (USD per 1M tokens); repeatable",
    )
    args = parser.parse_args()

    pricing = dict(PRICING)
    for spec in args.pricing:
        try:
            model, prices = parse_pricing_override(spec)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        pricing[model] = prices

    reports = collect(None if args.all else args.last)

    if args.plan:
        print_plan(reports, pricing)
        return 0

    if args.json:
        payload = []
        for report in reports:
            payload.append(
                {
                    "session_id": report.session_id,
                    "started": report.started,
                    "model": report.model,
                    "input_tokens": report.usage.input_tokens,
                    "cached_input_tokens": report.usage.cached_input_tokens,
                    "uncached_input_tokens": report.usage.uncached_input_tokens,
                    "output_tokens": report.usage.output_tokens,
                    "reasoning_output_tokens": report.usage.reasoning_output_tokens,
                    "api_calls": report.usage.calls,
                    "cache_hit_rate": round(report.usage.cache_hit_rate, 4),
                    "cost_usd": report.cost(pricing),
                }
            )
        print(json.dumps(payload, indent=2))
        return 0

    print_report(reports, pricing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
