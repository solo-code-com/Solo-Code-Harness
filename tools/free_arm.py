#!/usr/bin/env python3
"""FreeModel off-core arm: model catalog, health probe, one-shot ask.

Read-only helper for less-sensitive projects. NOT wired into routing
(model-roles.json / opencode.json). FreeModel is intermittent and its catalog
changes, so probe before trusting a model.

Commands:
  models [--refresh] [--json]    list models on both endpoints; --refresh probes each
  pick --role discuss|exec|fast  best usable model from the last probe cache
  ask --file PATH [--model M] [--platform auto|claude|openai]   one-shot, retry, strip
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".solocode" / "free_arm" / "models.json"
ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def strip_zw(s):
    return (s or "").translate(ZW)


def load_env():
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _get(url, headers, timeout=30):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _post(url, headers, body, timeout=45):
    data = json.dumps(body).encode()
    hdr = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=hdr, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _classify(e):
    if e.code in (502, 503, 504, 524):
        return "overloaded"
    if e.code in (401, 403):
        return "denied"
    return f"http{e.code}"


def probe_claude(an, key, model):
    body = {
        "model": model,
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "Reply with the single word OK"}],
    }
    t = time.time()
    try:
        _, b = _post(
            f"{an}/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            body,
        )
        j = json.loads(b)
        txt = strip_zw(
            "".join(
                p.get("text", "") for p in j.get("content", []) if isinstance(p, dict)
            )
        ).strip()
        return ("ok" if txt else "empty"), round(time.time() - t, 1), txt[:20]
    except urllib.error.HTTPError as e:
        return (
            _classify(e),
            round(time.time() - t, 1),
            e.read().decode("utf-8", "replace")[:80],
        )
    except OSError as e:
        return "error", round(time.time() - t, 1), str(e)[:80]


def probe_openai(oa, key, model):
    body = {
        "model": model,
        "input": [{"role": "user", "content": "Reply with the single word OK"}],
        "max_output_tokens": 16,
        "stream": True,
    }
    t = time.time()
    text = ""
    kind = "ok"
    try:
        req = urllib.request.Request(
            f"{oa}/v1/responses",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "User-Agent": UA,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            for raw in r:
                ln = raw.decode("utf-8", "replace").strip()
                if not ln.startswith("data:"):
                    continue
                p = ln[5:].strip()
                if p == "[DONE]":
                    break
                try:
                    j = json.loads(p)
                except ValueError:
                    continue
                tp = j.get("type", "")
                if tp.endswith("output_text.delta"):
                    text += j.get("delta", "")
                elif tp in ("response.completed", "response.done"):
                    break
                elif j.get("error"):
                    kind = (
                        "overloaded" if "overload" in json.dumps(j).lower() else "error"
                    )
                    break
        text = strip_zw(text).strip()
        return ("ok" if text else kind), round(time.time() - t, 1), text[:20]
    except urllib.error.HTTPError as e:
        return (
            _classify(e),
            round(time.time() - t, 1),
            e.read().decode("utf-8", "replace")[:80],
        )
    except OSError as e:
        return "error", round(time.time() - t, 1), str(e)[:80]


def fetch(an, oa, key):
    claude, gpt = [], []
    try:
        _, b = _get(
            f"{an}/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}
        )
        claude = [m.get("id") for m in json.loads(b).get("data", []) if m.get("id")]
    except Exception as e:  # noqa: BLE001 - a failed list is not a crash
        print("claude list failed:", e, file=sys.stderr)
    try:
        _, b = _get(f"{oa}/v1/models", {"Authorization": f"Bearer {key}"})
        gpt = [m.get("id") for m in json.loads(b).get("data", []) if m.get("id")]
    except Exception as e:  # noqa: BLE001
        print("openai list failed:", e, file=sys.stderr)
    return claude, gpt


def write_cache(data):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_cache():
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {"models": {}}


def cmd_models(args):
    env = load_env()
    key = env.get("FREEMODEL_API")
    an = env.get("FREEMODE_BASE_URL_Anthropic", "https://cc.freemodel.dev")
    oa = env.get("FREEMODE_BASE_URL_OPENAI", "https://api.freemodel.dev")
    if not key:
        print("FREEMODEL_API missing in .env", file=sys.stderr)
        return 2
    claude, gpt = fetch(an, oa, key)
    if not args.refresh:
        out = {"claude": claude, "gpt": gpt}
        if args.json:
            print(json.dumps(out))
        else:
            print("claude (" + str(len(claude)) + "):", ", ".join(claude))
            print("gpt (" + str(len(gpt)) + "):", ", ".join(gpt))
        return 0
    cache = {"probed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "models": {}}
    print(f"{'model':28} {'kind':12} {'secs':>5}  note")
    for m in claude:
        kind, secs, note = probe_claude(an, key, m)
        cache["models"][m] = {"kind": kind, "secs": secs, "platform": "claude"}
        write_cache(cache)
        print(f"{m:28} {kind:12} {secs:>5}  {note}")
    for m in gpt:
        kind, secs, note = probe_openai(oa, key, m)
        cache["models"][m] = {"kind": kind, "secs": secs, "platform": "openai"}
        write_cache(cache)
        print(f"{m:28} {kind:12} {secs:>5}  {note}")
    ok = sum(1 for v in cache["models"].values() if v["kind"] == "ok")
    print(f"\nusable: {ok}/{len(cache['models'])}   cache: {CACHE}")
    return 0


ROLE_PREF = {
    "discuss": ["claude-opus-5-5", "claude-sonnet-4-6", "claude-sonnet-5-5"],
    "exec": ["gpt-6.1-sol", "claude-sonnet-4-6", "gpt-6-sol"],
    "fast": ["claude-haiku-5-5", "gpt-6-luna", "claude-haiku-4-5-20251001"],
}


def cmd_pick(args):
    cache = read_cache()
    models = cache.get("models", {})
    prefs = ROLE_PREF.get(args.role, [])
    usable = [m for m, v in models.items() if v.get("kind") == "ok"]
    for p in prefs:
        if p in usable:
            print(p)
            return 0
    if usable:
        print(usable[0])
        return 0
    print("none", file=sys.stderr)
    return 1


def cmd_ask(args):
    env = load_env()
    key = env.get("FREEMODEL_API")
    an = env.get("FREEMODE_BASE_URL_Anthropic", "https://cc.freemodel.dev")
    oa = env.get("FREEMODE_BASE_URL_OPENAI", "https://api.freemodel.dev")
    prompt = Path(args.file).read_text(encoding="utf-8")
    model = args.model
    platform = args.platform
    if platform == "auto":
        platform = "openai" if model and model.startswith("gpt-") else "claude"
    if not model:
        cache = read_cache()
        usable = [
            m for m, v in cache.get("models", {}).items() if v.get("kind") == "ok"
        ]
        model = (
            usable[0]
            if usable
            else ("claude-sonnet-4-6" if platform == "claude" else "gpt-6.1-sol")
        )
    last = None
    for attempt in range(1, 4):
        if platform == "claude":
            body = {
                "model": model,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            }
            try:
                t = time.time()
                _, b = _post(
                    f"{an}/v1/messages",
                    {"x-api-key": key, "anthropic-version": "2023-06-01"},
                    body,
                )
                j = json.loads(b)
                txt = strip_zw(
                    "".join(
                        p.get("text", "")
                        for p in j.get("content", [])
                        if isinstance(p, dict)
                    )
                ).strip()
                if txt:
                    print(f"[{model} {round(time.time() - t, 1)}s]\n{txt}")
                    return 0
                last = "empty"
            except Exception as e:  # noqa: BLE001
                last = str(e)[:120]
        else:
            kind, secs, note = probe_openai(oa, key, model)
            if kind == "ok":
                print(f"[{model} {secs}s]\n{note}")
                return 0
            last = f"{kind} {note}"
        print(f"attempt {attempt}: {last}", file=sys.stderr)
        time.sleep(1.5 * attempt)
    print("ask failed:", last, file=sys.stderr)
    return 1


def cmd_setup(args):
    """Read .env and write the FreeModel client config for Claude Code and Codex."""
    env = load_env()
    key = env.get("FREEMODEL_API", "").strip()
    an = env.get("FREEMODE_BASE_URL_Anthropic", "https://cc.freemodel.dev")
    oa = env.get("FREEMODE_BASE_URL_OPENAI", "https://api.freemodel.dev")
    if not key or key.startswith("fe_oa-your"):
        print(
            "Fill FREEMODEL_API in .env first (cp .env.template .env).", file=sys.stderr
        )
        return 2
    home = Path.home()
    # Claude Code settings.json (merge)
    sp = home / ".claude" / "settings.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    data.setdefault("theme", "light")
    e = data.get("env", {})
    e.update(
        {
            "ANTHROPIC_API_KEY": key,
            "ANTHROPIC_BASE_URL": an,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
    )
    data["env"] = e
    data.setdefault("permissions", {"allow": [], "deny": []})
    data["customApiKeyResponses"] = {"approved": [key]}
    sp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # Codex config.toml
    cp = home / ".codex" / "config.toml"
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(
        'model = "gpt-6.1-sol"\nmodel_provider = "freemodel"\n\n'
        '[model_providers.freemodel]\nname = "FreeModel"\n'
        f'base_url = "{oa}/v1"\nenv_key = "FREEMODEL_API"\nwire_api = "responses"\n',
        encoding="utf-8",
    )
    # FREEMODEL_API in the OS env (Codex env_key reads it)
    if sys.platform == "win32":
        subprocess.run(["setx", "FREEMODEL_API", key], capture_output=True, check=False)
    else:
        prof = home / ".profile"
        line = f'export FREEMODEL_API="{key}"'
        existing = prof.read_text(encoding="utf-8") if prof.exists() else ""
        if line not in existing:
            with prof.open("a", encoding="utf-8") as f:
                f.write(
                    ("\n" if existing and not existing.endswith("\n") else "")
                    + line
                    + "\n"
                )
    print("configured Claude Code:", sp)
    print("configured Codex      :", cp)
    print("set FREEMODEL_API in env (new terminals).")
    print("install CLIs if missing: npm i -g @anthropic-ai/claude-code @openai/codex")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="FreeModel off-core arm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("models")
    m.add_argument("--refresh", action="store_true")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_models)
    p = sub.add_parser("pick")
    p.add_argument("--role", choices=list(ROLE_PREF), default="discuss")
    p.set_defaults(func=cmd_pick)
    a = sub.add_parser("ask")
    a.add_argument("--file", required=True)
    a.add_argument("--model")
    a.add_argument("--platform", choices=["auto", "claude", "openai"], default="auto")
    a.set_defaults(func=cmd_ask)
    s = sub.add_parser("setup")
    s.set_defaults(func=cmd_setup)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
