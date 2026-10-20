#!/usr/bin/env python3
"""Count prose findings (long sentences, semicolons, em-dashes) in Markdown docs.

`.claude`, `.copilot`, `.gemini` dropped: they mirror `.kilo` (prose counted 4x)."""

import argparse
import json
import pathlib
import re
import sys

BUDGET_PATH = pathlib.Path("tools/config/prose-budget.json")
SCOPE_DIRS = ("docs", ".kilo")
ROOT_FILES = ("README.md", "AGENTS.md", "SPEC.md", "CLAUDE.md")
EM_DASH = "\u2014"
_SENTENCE_END = re.compile(r"[.!?]+(?=\s)")


def strip_markers(text):
    """Strip a leading bullet, heading, list-number or blockquote marker."""
    while True:
        before = text
        text = re.sub(r"^(?:[-*+]|#{1,6}|\d+[.)])\s+", "", text)
        text = re.sub(r"^>\s?", "", text)
        text = text.lstrip()
        if text == before:
            return text


def word_count(text):
    return len(text.split())


def analyze(text):
    long_count = 0
    semi_count = 0
    dash_count = 0
    in_fence = False
    for raw in text.splitlines():
        if raw.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if raw.startswith("|"):
            continue
        if raw.startswith("`"):
            continue
        if raw.startswith("    "):
            continue
        prose = strip_markers(raw)
        if not prose:
            continue
        dash_count += prose.count(EM_DASH)
        for sentence in _SENTENCE_END.split(prose):
            sentence = sentence.strip()
            if word_count(sentence) < 5:
                continue
            if word_count(sentence) > 30:
                long_count += 1
            if ";" in sentence:
                semi_count += 1
    return long_count, semi_count, dash_count


def iter_files():
    seen = set()
    for directory in SCOPE_DIRS:
        base = pathlib.Path(directory)
        if base.is_dir():
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix != ".md":
                    continue
                if "archive" in path.name.lower():
                    continue
                if str(path) in seen:
                    continue
                seen.add(str(path))
                yield path
    for name in ROOT_FILES:
        path = pathlib.Path(name)
        if not path.is_file():
            continue
        if str(path) in seen:
            continue
        seen.add(str(path))
        yield path


def read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def compute():
    totals = {"long_sentence": 0, "semicolon": 0, "em_dash": 0}
    per_file = {}
    for path in iter_files():
        text = read_text(path)
        if not text:
            continue
        long_count, semi_count, dash_count = analyze(text)
        total = long_count + semi_count + dash_count
        if total:
            per_file[str(path)] = total
        totals["long_sentence"] += long_count
        totals["semicolon"] += semi_count
        totals["em_dash"] += dash_count
    return totals, per_file


def write_budget(totals):
    pathlib.Path("tools/config").mkdir(parents=True, exist_ok=True)
    data = {
        "long_sentence": totals["long_sentence"],
        "semicolon": totals["semicolon"],
        "em_dash": totals["em_dash"],
    }
    with open(str(BUDGET_PATH), "wb") as handle:
        handle.write((json.dumps(data, indent=2) + "\n").encode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args(argv)

    totals, per_file = compute()

    if args.update:
        write_budget(totals)
        return 0

    if args.report:
        print("long_sentence", totals["long_sentence"])
        print("semicolon", totals["semicolon"])
        print("em_dash", totals["em_dash"])
        print()
        top = sorted(per_file.items(), key=lambda item: item[1], reverse=True)[:10]
        for name, count in top:
            print(count, name)
        return 0

    try:
        budget = json.loads(BUDGET_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("error: cannot read budget file", file=sys.stderr)
        return 1

    failed = False
    for rule in ("long_sentence", "semicolon", "em_dash"):
        current = totals[rule]
        limit = budget.get(rule, 0)
        if current > limit:
            failed = True
            print(rule, current, limit, current - limit)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
