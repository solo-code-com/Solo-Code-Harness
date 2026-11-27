#!/usr/bin/env python3
"""Route a task to the right model-family arm among an ENABLED set, then print the plan.

The enabled set is situational (quota balance + project nature):
  --arms gemini,deepseek              # only the two main arms
  --arms deepseek,claude,chatgpt      # no gemini
  --arms gemini,claude,chatgpt        # no deepseek
  (omit --arms)                       # auto-detect / all available
Or write .solocode/dispatch-arms.json : {"arms": ["deepseek","gemini"]}

Families: deepseek (opencode/kilo/model_api) | gemini (antigravity agy) |
          claude (FreeModel cc.freemodel.dev) | chatgpt (FreeModel api.freemodel.dev)
Outputs JSON: role, family, model, effort, command, enabled, skipped, reason.
Read-only planner: the orchestrator or the operator runs the printed command.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARMS_CFG = ROOT / ".solocode" / "dispatch-arms.json"
PROBE = ROOT / ".solocode" / "free_arm" / "models.json"
ALL = ("deepseek", "gemini", "claude", "chatgpt")


def load_env() -> dict:
    d = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                d[k.strip()] = v.strip()
    return d


def enabled_arms(spec: str | None, env: dict) -> list[str]:
    if spec:
        return [a for a in (x.strip() for x in spec.split(",")) if a in ALL]
    if ARMS_CFG.exists():
        try:
            return [
                a
                for a in json.loads(ARMS_CFG.read_text(encoding="utf-8")).get(
                    "arms", []
                )
                if a in ALL
            ]
        except (OSError, ValueError):
            pass
    arms = []
    if (ROOT / "tools" / "opencode_delegate.py").exists():
        arms.append("deepseek")
    if (ROOT / "tools" / "antigravity_delegate.py").exists():
        arms.append("gemini")
    if env.get("FREEMODEL_API"):
        arms += ["claude", "chatgpt"]
    return arms or list(ALL)


def usable_models() -> dict | None:
    if PROBE.exists():
        try:
            data = json.loads(PROBE.read_text(encoding="utf-8")).get("models", {})
            return {k: v for k, v in data.items() if v.get("kind") == "ok"}
        except (OSError, ValueError):
            return None
    return None


def classify(task: str) -> str:
    t = task.lower()
    if re.search(
        r"review|audit|survey|read .*files|where is|kiem|kiểm|rà soát|soát", t
    ):
        return "review"
    if re.search(
        r"design|discuss|plan|architect|explain|why |thiet ke|thiết kế|bàn|kiến trúc|kế hoạch",
        t,
    ):
        return "discuss"
    if re.search(
        r"implement|fix|refactor|add |write|build|code|sua|sửa|viết|thêm|tạo|test", t
    ):
        return "code"
    if re.search(r"list|count|what file|quick|who |liet ke|liệt kê|đếm|nhanh", t):
        return "fast"
    return "code"


FAM_PREF = {
    "review": ["gemini", "claude", "chatgpt", "deepseek"],
    "discuss": ["claude", "chatgpt", "gemini", "deepseek"],
    "code": ["deepseek", "chatgpt", "claude", "gemini"],
    "fast": ["deepseek", "claude", "chatgpt", "gemini"],
}


def _first(prefs, usable, group=None):
    if usable is not None:
        for p in prefs:
            if p in usable and (group is None or p.startswith(group)):
                return p
    for p in prefs:
        if group is None or p.startswith(group):
            return p
    return prefs[0]


def model_for(fam: str, role: str, usable) -> str:
    if fam == "deepseek":
        return (
            "opencode-go/deepseek-v4-pro"
            if role in ("review", "discuss")
            else "opencode-go/deepseek-v4.1-flash"
        )
    if fam == "gemini":
        return (
            "gemini-4-hydrogen-high"
            if role in ("review", "discuss")
            else "gemini-4-hydrogen-medium"
        )
    if fam == "claude":
        return _first(
            {
                "discuss": [
                    "claude-opus-5-5",
                    "claude-sonnet-4-6",
                    "claude-sonnet-5-5",
                ],
                "review": ["claude-opus-5-5", "claude-sonnet-4-6"],
                "code": ["claude-sonnet-4-6", "claude-opus-5-5"],
                "fast": ["claude-haiku-5-5", "claude-sonnet-4-6"],
            }[role],
            usable,
            "claude-",
        )
    return _first(
        {
            "code": ["gpt-6.2-sol", "gpt-6.1-sol", "gpt-6-sol"],
            "discuss": ["gpt-6.2-sol", "gpt-6.1-sol"],
            "review": ["gpt-6.2-sol", "gpt-6.1-sol"],
            "fast": ["gpt-6-luna", "gpt-6.1-sol"],
        }[role],
        usable,
        "gpt-",
    )


def effort_for(fam: str, role: str) -> str | None:
    if fam == "claude":
        return "high" if role in ("discuss", "review") else "medium"
    if fam == "chatgpt":
        return "high" if role == "review" else "medium"
    return None


def command_for(fam: str, model: str, effort: str | None, role: str, task: str) -> str:
    q = task.replace('"', "'")
    if fam == "deepseek":
        if role == "code":
            return f'python tools/opencode_delegate.py "{q}" --agent solo-code-engineer --model {model}'
        return f'python tools/opencode_delegate.py "{q}" --model {model}'
    if fam == "gemini":
        return (
            f'python tools/antigravity_delegate.py "{q}" --allow-tools --model {model}'
        )
    if fam == "claude":
        eff = f" --effort {effort}" if effort else ""
        return f'.\\tools\\claude-code-env.ps1 --model {model}{eff} -p "{q}"'
    eff = f' -c model_reasoning_effort="{effort}"' if effort else ""
    return f'.\\tools\\codex-env.ps1 -m {model}{eff} exec "{q}"'


def plan(task: str, enabled: list[str], usable) -> dict:
    role = classify(task)
    skipped = []
    fam = None
    for cand in FAM_PREF[role]:
        if cand in enabled:
            fam = cand
            break
        skipped.append(cand)
    if fam is None:
        fam = enabled[0]  # nothing preferred is enabled: use the first enabled arm
    model = model_for(fam, role, usable)
    effort = effort_for(fam, role)
    return {
        "task": task,
        "role": role,
        "family": fam,
        "model": model,
        "effort": effort,
        "command": command_for(fam, model, effort, role, task),
        "enabled": enabled,
        "skipped": skipped,
        "reason": f"role={role}; picked {fam}; enabled={enabled}; skipped={skipped}",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Route a task across the enabled model-family arms"
    )
    ap.add_argument("task", help="the task text")
    ap.add_argument(
        "--arms",
        help="comma list: deepseek,gemini,claude,chatgpt (default: config/auto)",
    )
    ap.add_argument(
        "--json", action="store_true", help="print the decision as one JSON line"
    )
    args = ap.parse_args(argv)
    env = load_env()
    enabled = enabled_arms(args.arms, env)
    usable = usable_models()
    decision = plan(args.task, enabled, usable)
    if args.json:
        print(json.dumps(decision))
    else:
        print(json.dumps(decision, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
