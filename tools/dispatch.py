#!/usr/bin/env python3
"""Route a task to the right model-family arm among an ENABLED set. Config-driven fleet.

The fleet (model/provider/effort/command per family, and the role preference) lives in
tools/config/dispatch.json and can be overridden per project in .solocode/dispatch.json:

  { "arms": ["deepseek","gemini"],
    "off": ["gemini"],   # an arm that is down now (account expired, provider swap, quota out)
    "models": { "claude": {"discuss": "claude-sonnet-4-6"} },
    "effort": { "chatgpt": {"review": "high"} } }

Swap a model or a provider by editing config, never the code. The enabled set comes from
--arms, then the project file, then .solocode/dispatch-arms.json, then auto-detect.
A FreeModel family whose configured model the probe marks non-ok is skipped (fallback).
Prints the plan JSON: role, family, model, effort, command, enabled, skipped, reason.
Read-only planner: the orchestrator or the operator runs the printed command.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "tools" / "config" / "dispatch.json"
PROJECT = ROOT / ".solocode" / "dispatch.json"
LEGACY_ARMS = ROOT / ".solocode" / "dispatch-arms.json"
PROBE = ROOT / ".solocode" / "free_arm" / "models.json"
ALL = ("deepseek", "gemini", "claude", "chatgpt")
#: A core arm is "up" when its delegate file exists; a missing one means the arm is gone.
TOOL_FOR = {
    "deepseek": "tools/opencode_delegate.py",
    "gemini": "tools/antigravity_delegate.py",
}


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_env() -> dict:
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def probe_status() -> dict:
    data = load_json(PROBE).get("models", {})
    return {k: v.get("kind") for k, v in data.items() if isinstance(v, dict)}


def enabled_arms(spec, project, env, fleet) -> list:
    if spec:
        return [
            a for a in (x.strip() for x in spec.split(",")) if a in fleet["families"]
        ]
    if project.get("arms"):
        return [a for a in project["arms"] if a in fleet["families"]]
    legacy = load_json(LEGACY_ARMS).get("arms")
    if legacy:
        return [a for a in legacy if a in fleet["families"]]
    arms = []
    if (ROOT / "tools" / "opencode_delegate.py").exists():
        arms.append("deepseek")
    if (ROOT / "tools" / "antigravity_delegate.py").exists():
        arms.append("gemini")
    if env.get("FREEMODEL_API"):
        arms += [
            f
            for f in ("claude", "chatgpt")
            if fleet["families"][f].get("kind") == "freemodel"
        ]
    return arms or list(fleet["families"])


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


def pick_model(family_key, role, fam_cfg, project):
    override = project.get("models", {}).get(family_key, {}).get(role)
    if override:
        return override
    return fam_cfg.get("models", {}).get(role)


def pick_effort(family_key, role, fam_cfg, project):
    override = project.get("effort", {}).get(family_key, {}).get(role)
    if override:
        return override
    return fam_cfg.get("effort", {}).get(role)


def build_command(fam_cfg, role, model, effort, task):
    q = task.replace('"', "'")
    fmt = fam_cfg.get("effort_fmt", "")
    effort_str = fmt.format(e=effort) if (effort and fmt) else ""
    template = (
        fam_cfg.get("command_write")
        if (role == "code" and fam_cfg.get("command_write"))
        else fam_cfg.get("command")
    )
    return template.format(task=q, model=model, effort=effort_str)


def plan(task, enabled, fleet, project, probe):
    role = classify(task)
    pref = fleet.get("preference", {}).get(role, list(enabled))
    skipped = []
    chosen = None
    off = set(project.get("off", []))
    for cand in pref:
        if cand not in enabled or cand not in fleet["families"]:
            skipped.append(
                cand + (":not-enabled" if cand not in enabled else ":unknown")
            )
            continue
        fam = fleet["families"][cand]
        if cand in off or fam.get("enabled", True) is False:
            skipped.append(cand + ":off")
            continue
        tool = TOOL_FOR.get(cand)
        if tool and not (ROOT / tool).exists():
            skipped.append(cand + ":tool-missing")
            continue
        model = pick_model(cand, role, fam, project)
        if fam.get("kind") == "freemodel" and probe.get(model) not in (None, "ok"):
            skipped.append(f"{cand}:{model}={probe.get(model)}")
            continue
        chosen = (cand, fam, model)
        break
    if chosen is None:
        fam_key = next(a for a in enabled if a in fleet["families"])
        fam = fleet["families"][fam_key]
        chosen = (fam_key, fam, pick_model(fam_key, role, fam, project))
    fam_key, fam, model = chosen
    effort = pick_effort(fam_key, role, fam, project)
    return {
        "task": task,
        "role": role,
        "family": fam_key,
        "model": model,
        "effort": effort,
        "command": build_command(fam, role, model, effort, task),
        "enabled": enabled,
        "skipped": skipped,
        "reason": f"role={role}; picked {fam_key}; enabled={enabled}; skipped={skipped}",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Route a task across the enabled model-family arms"
    )
    ap.add_argument("task")
    ap.add_argument("--arms", help="comma list (default: project/config/auto)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    fleet = load_json(FLEET)
    if not fleet.get("families"):
        print("No fleet config at tools/config/dispatch.json", file=sys.stderr)
        return 2
    env = load_env()
    project = load_json(PROJECT)
    enabled = enabled_arms(args.arms, project, env, fleet)
    decision = plan(args.task, enabled, fleet, project, probe_status())
    print(json.dumps(decision) if args.json else json.dumps(decision, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
