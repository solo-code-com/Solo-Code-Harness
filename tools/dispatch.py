#!/usr/bin/env python3
"""Route a task to the right model-family arm among an ENABLED set. Config-driven fleet.

Fleet (families, per-role model, per-role effort, effort format, command, cost tiers, role
preference) lives in tools/config/dispatch.json and is overridable per project in
.solocode/dispatch.json:

  { "arms": ["deepseek","gemini"],
    "off": ["gemini"],   # an arm that is down now (account expired, provider swap, quota out)
    "models": { "claude": {"discuss": "claude-sonnet-4-6"} },
    "effort": { "chatgpt": {"review": "high"} } }

Swap a model or provider by editing config, never the code. Features:
  --cost auto|cheap|strong   use the family cost tier instead of the role model
  --record                   after planning a CODE task, remember the family so the next
                             review goes to a DIFFERENT family (review-by-different)
  --arms ...                 enabled set (else project file, then auto-detect)
A FreeModel family whose configured model the probe marks non-ok is skipped; a core arm
whose delegate file is missing is skipped; a family in "off" is skipped. All fall back.
Prints the plan JSON. Read-only planner: the orchestrator or the operator runs the command.
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
LAST_WRITE = ROOT / ".solocode" / "last-write.json"
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


def pick_model(family_key, role, fam_cfg, project, cost):
    tier = fam_cfg.get("cost", {})
    if cost in ("cheap", "strong") and tier.get(cost):
        return tier[cost]
    override = project.get("models", {}).get(family_key, {}).get(role)
    if override:
        return override
    return fam_cfg.get("models", {}).get(role)


def pick_effort(family_key, role, fam_cfg, project):
    override = project.get("effort", {}).get(family_key, {}).get(role)
    if override:
        return override
    return fam_cfg.get("effort", {}).get(role)


def fence_is_enforceable(directory: str | None) -> bool:
    """False when the target is the repo root, where no narrower scope exists.

    opencode_delegate refuses to enforce a root fence for the same reason: a check
    against "." can never report a path as outside it, so emitting --enforce-scope
    there would look like a gate while checking nothing.
    """
    if directory in (None, "", "."):
        return False
    try:
        return Path(directory).resolve() != ROOT.resolve()
    except OSError:
        return False


def build_command(fam_cfg, role, model, effort, task, directory="."):
    """Format the family command. {dir} is only present in a write template."""
    q = task.replace('"', "'")
    fmt = fam_cfg.get("effort_fmt", "")
    effort_str = fmt.format(e=effort) if (effort and fmt) else ""
    template = (
        fam_cfg.get("command_write")
        if (role == "code" and fam_cfg.get("command_write"))
        else fam_cfg.get("command")
    )
    command = template.format(
        task=q, model=model, effort=effort_str, dir=directory or "."
    )
    if "{dir}" in template and not fence_is_enforceable(directory):
        # A root-level write has no enforceable fence: drop the flag rather than
        # print a command that claims one.
        command = command.replace(" --enforce-scope", "")
    return command


def order_preference(role, pref, last_write):
    """For a review, prefer a family other than the one that last wrote the code."""
    if role != "review" or not last_write:
        return pref
    return [f for f in pref if f != last_write] + [f for f in pref if f == last_write]


def plan(task, enabled, fleet, project, probe, cost, last_write, directory="."):
    role = classify(task)
    pref = order_preference(
        role, list(fleet.get("preference", {}).get(role, list(enabled))), last_write
    )
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
        model = pick_model(cand, role, fam, project, cost)
        if fam.get("kind") == "freemodel" and probe.get(model) not in (None, "ok"):
            skipped.append(f"{cand}:{model}={probe.get(model)}")
            continue
        chosen = (cand, fam, model)
        break
    if chosen is None:
        fam_key = next(a for a in enabled if a in fleet["families"])
        fam = fleet["families"][fam_key]
        chosen = (fam_key, fam, pick_model(fam_key, role, fam, project, cost))
    fam_key, fam, model = chosen
    effort = pick_effort(fam_key, role, fam, project)
    return {
        "task": task,
        "role": role,
        "family": fam_key,
        "model": model,
        "effort": effort,
        "cost": cost,
        "last_writer": last_write,
        "command": build_command(fam, role, model, effort, task, directory),
        "fence": (
            "repo-root (no enforceable fence)"
            if role == "code" and not fence_is_enforceable(directory)
            else ("enforced" if role == "code" else "read-only")
        ),
        "enabled": enabled,
        "skipped": skipped,
        "reason": f"role={role}; picked {fam_key}; cost={cost}; last_writer={last_write}; enabled={enabled}; skipped={skipped}",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Route a task across the enabled model-family arms"
    )
    ap.add_argument("task")
    ap.add_argument("--arms", help="comma list (default: project/config/auto)")
    ap.add_argument("--cost", choices=["auto", "cheap", "strong"], default="auto")
    ap.add_argument(
        "--record",
        action="store_true",
        help="remember the family of a planned CODE task",
    )
    ap.add_argument(
        "--directory",
        default=".",
        help="target directory for a write task; a fence is only enforceable below the repo root",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    fleet = load_json(FLEET)
    if not fleet.get("families"):
        print("No fleet config at tools/config/dispatch.json", file=sys.stderr)
        return 2
    env = load_env()
    project = load_json(PROJECT)
    last_write = load_json(LAST_WRITE).get("family")
    enabled = enabled_arms(args.arms, project, env, fleet)
    decision = plan(
        args.task,
        enabled,
        fleet,
        project,
        probe_status(),
        args.cost,
        last_write,
        args.directory,
    )
    if args.record and decision["role"] == "code":
        LAST_WRITE.parent.mkdir(parents=True, exist_ok=True)
        LAST_WRITE.write_text(
            json.dumps({"family": decision["family"]}, indent=2), encoding="utf-8"
        )
    print(json.dumps(decision) if args.json else json.dumps(decision, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
