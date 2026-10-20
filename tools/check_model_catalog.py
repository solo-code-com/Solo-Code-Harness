#!/usr/bin/env python3
"""Verify the declared model catalogs against the providers' live `/models` endpoints.

The harness's rule is that model ids are verified against the provider's own catalog, never
from memory. This tool turns that rule into a command:

    python tools/check_model_catalog.py            # table + exit 1 on drift
    python tools/check_model_catalog.py --json     # machine-readable

Why the User-Agent matters: CommandCode answers 403 to a default Python client and 200 to a
client-shaped one (measured 2026-10-16), so every request carries `opencode/1.0`.

A provider that serves a model the harness does not declare is drift, because that model is
invisible to the pipelines. A declared model the provider does not serve is drift too, because
a routing entry that 404s is worse than a missing one. OpenRouter is the exception: the harness
declares a *curated* set there on purpose, so its extras are reported, not failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env_file  # noqa: E402
import opencode_engine as engine  # noqa: E402

UA = "opencode/1.0"

#: The decision model behind `POST /systemone`. It is absent from `GET /models` -- that listing
#: only carries the chat, responses and messages models -- so a listing check cannot see it.
SYSTEMONE_MODEL = "typesafe/jev"
SYSTEMONE_QUESTION = {
    "urgent": {"type": "noul", "instructions": "Is this request urgent?"}
}

#: The raw `.env` values, unnormalised: the paths below already carry their `/v1`.
RAW_ENV: dict[str, str] = env_file.parse_env_file(
    Path(__file__).resolve().parent.parent / ".env"
)

#: provider -> (base url env or literal, models path, key env, harness catalog, curated?)
#: Paths assume the *raw* `.env` value (with its `/v1`), so the checker reads `.env` directly
#: instead of exporting through the launcher's normaliser.
PROVIDERS: dict[str, tuple[str | None, str, str, dict, bool]] = {
    "commandcode": (
        "COMMANDCODE_BASE_URL",
        "/models",
        "COMMANDCODE_API_KEY",
        engine._PROVIDER_MODELS,
        False,
    ),
    "freemodel": (
        "OPENAI_BASE_URL",
        "/v1/models",
        "OPENAI_API_KEY",
        engine._FREEMODEL_MODELS,
        False,
    ),
    "deepseek": ("DEEPSEEK_BASE_URL", "/models", "DEEPSEEK_API_KEY", {}, False),
    "openrouter": (
        "https://openrouter.ai/api/v1",
        "/models",
        "OPENROUTER_API_KEY",
        engine._OPENROUTER_MODELS,
        True,
    ),
}

#: Advertised by a provider but known dead; counted as informational, not drift.
IGNORED_ADVERTISED: dict[str, set[str]] = {
    "freemodel": {"gpt-6-luna"},  # listed, HTTP 404 when used
}

#: Providers whose `/models` listing is not authoritative. FreeModel's is stale in both
#: directions, measured 2026-10-16: it omits `gpt-6.1-sol`, which a real call answers
#: (`FREEMODEL_OK`/`FM61_OK` through the OpenCode delegate), and it lists `gpt-6-luna`, which
#: 404s. Differences for these providers are reported, never failed -- a call is the evidence.
LISTING_UNRELIABLE: set[str] = {"freemodel"}


def declared_ids(catalog: dict) -> set[str]:
    return {body.get("modelID") for body in catalog.values() if body.get("modelID")}


def fetch(base: str, path: str, key: str, timeout: int) -> list[dict] | None:
    url = base.rstrip("/") + path
    request = urllib.request.Request(  # noqa: S310 - operator-configured https endpoint
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"  HTTP {exc.code} from {url}", file=sys.stderr)
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"  {type(exc).__name__} from {url}", file=sys.stderr)
        return None
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    return items if isinstance(items, list) else None


def resolve(provider: str) -> tuple[str, str]:
    """(base url, api key) for a provider prefix, from the raw `.env` with the env as backup."""
    import os

    base_ref, _path, key_env, _catalog, _curated = PROVIDERS[provider]
    base = base_ref if base_ref.startswith("http") else RAW_ENV.get(base_ref, "")
    key = RAW_ENV.get(key_env, "") or os.environ.get(key_env, "")
    return base, key


def probe(model_ref: str, timeout: int) -> tuple[bool, str]:
    """Call a model once and report whether it answers.

    The point of a probe is that a listing can lie: FreeModel's `/v1/models` omits
    `gpt-6.1-sol` while a real call answers (measured 2026-10-16), so `--probe` exists to
    replace "the listing says" with "the service answered".
    """
    provider, _, model_id = model_ref.partition("/")
    if provider not in PROVIDERS or not model_id:
        return False, f"unknown provider {provider!r} (use <provider>/<model-id>)"
    base, key = resolve(provider)
    if not base or not key:
        return False, f"missing base url or key for {provider}"
    url = (
        base.rstrip("/")
        + PROVIDERS[provider][1].removesuffix("/models")
        + "/chat/completions"
    )
    payload = json.dumps(
        {
            "model": model_id,
            "messages": [
                {"role": "user", "content": "Reply with the single word: pong"}
            ],
            "max_tokens": 8,
        }
    ).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - operator-configured https endpoint
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:120].replace("\n", " ")
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return False, f"unexpected body: {json.dumps(body)[:100]}"
    if not str(content or "").strip():
        # The DeepSeek family answers with an empty `content`; the text lives in
        # `reasoning_content`, so an empty cell would misreport a working model.
        content = body["choices"][0]["message"].get("reasoning_content") or content
    if not str(content or "").strip():
        return True, "(empty reply)"
    return True, str(content).strip()[:40]


def probe_systemone(base: str, key: str, timeout: int) -> tuple[str, str]:
    """POST the System One endpoint once and report whether the decision model answers.

    `typesafe/jev` is absent from the `/models` listing, so "the catalog is ok" says nothing
    about it: only an answer from `/systemone` proves the model is still served and named the
    same (measured 2026-10-20). A drift here means the endpoint answers with a body the
    wrapper cannot read; an unreachable endpoint is reported, not failed, like any provider.
    """
    url = base.rstrip("/") + "/systemone"
    payload = json.dumps(
        {
            "model": SYSTEMONE_MODEL,
            "state": "ping",
            "questions": SYSTEMONE_QUESTION,
        }
    ).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - operator-configured https endpoint
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:120].replace("\n", " ")
        return "unreachable", f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001
        return "unreachable", type(exc).__name__
    try:
        value = body["answers"]["urgent"]["noul"]
    except (KeyError, IndexError, TypeError):
        return "drift", f"unexpected body: {json.dumps(body)[:100]}"
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "drift", f"noul is not a number: {value!r}"
    if not 0.0 <= float(value) <= 1.0:
        return "drift", f"noul out of range: {float(value)}"
    return "ok", f"noul={float(value):.2f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check model catalogs against the providers"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--probe",
        nargs="+",
        metavar="PROVIDER/MODEL",
        help="call each model once instead of comparing listings, e.g. "
        "commandcode/deepseek/deepseek-v4.1-flash freemodel/gpt-6.1-sol",
    )
    args = parser.parse_args(argv)

    if args.probe:
        rows = []
        failed = 0
        print(f"{'model':52} {'ok':>5}  {'latency':>8}  reply")
        for model_ref in args.probe:
            started = time.monotonic()
            ok, detail = probe(model_ref, args.timeout)
            elapsed = time.monotonic() - started
            failed += 0 if ok else 1
            rows.append(
                {
                    "model": model_ref,
                    "ok": ok,
                    "seconds": round(elapsed, 1),
                    "detail": detail,
                }
            )
            print(f"{model_ref:52} {str(ok):>5}  {elapsed:7.1f}s  {detail}")
        if args.json:
            print(json.dumps(rows, indent=1))
        return 1 if failed else 0

    env_file.load_project_env()
    import os

    raw = RAW_ENV

    report, drift = [], 0
    for provider, (base_ref, path, key_env, catalog, curated) in PROVIDERS.items():
        if base_ref and base_ref.startswith("http"):
            base = base_ref
        else:
            # raw, not normalised: the launcher strips `/v1` because the config appends it,
            # while these paths already carry it.
            base = raw.get(base_ref or "", "")
        key = raw.get(key_env, "") or os.environ.get(key_env, "")
        row = {
            "provider": provider,
            "base_env": base_ref,
            "key_env": key_env,
            "declared": len(catalog),
        }
        if not base or not key:
            row["status"] = f"skipped ({'base' if not base else 'key'} missing)"
            report.append(row)
            continue
        items = fetch(base, path, key, args.timeout)
        if items is None:
            row["status"] = "unreachable"
            report.append(row)
            continue
        live = {str(i.get("id")) for i in items if isinstance(i, dict) and i.get("id")}
        declared = (
            declared_ids(catalog) if catalog else live
        )  # deepseek declares none: report only
        missing = sorted(live - declared - IGNORED_ADVERTISED.get(provider, set()))
        stale = sorted(declared - live)
        if not catalog:
            status = "n/a (nothing declared)"
        elif curated:
            status = "curated"
        elif provider in LISTING_UNRELIABLE:
            status = "listing stale (verify by call)"
        else:
            status = "ok" if not missing and not stale else "drift"
        row.update(
            {
                "live": len(live),
                "missing": missing[:12],
                "missing_count": len(missing),
                "stale": stale[:12],
                "stale_count": len(stale),
                "status": status,
            }
        )
        if status == "drift":
            drift += 1
        report.append(row)

    # `/systemone` is its own route with its own model, so it gets its own row: the listing
    # above cannot report on a model the listing never carries.
    base_ref, _path, key_env, _catalog, _curated = PROVIDERS["commandcode"]
    base = raw.get(base_ref or "", "")
    key = raw.get(key_env, "") or os.environ.get(key_env, "")
    row = {
        "provider": "systemone",
        "model": SYSTEMONE_MODEL,
        "base_env": base_ref,
        "key_env": key_env,
        "declared": None,
    }
    if not base or not key:
        row.update(
            {
                "status": f"skipped ({'base' if not base else 'key'} missing)",
                "detail": "",
            }
        )
    else:
        status, detail = probe_systemone(base, key, args.timeout)
        row.update({"status": status, "detail": detail})
        if status == "drift":
            drift += 1
    report.append(row)

    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"{'provider':14} {'models':>13}  {'key env':22} status")
        for row in report:
            counts = row.get("model") or (
                f"{row.get('declared', '-')}/{row.get('live', '-')}"
            )
            line = f"{row['provider']:14} {counts:>13}  {row['key_env']:22} {row['status']}"
            print(f"{line}  {row['detail']}" if row.get("detail") else line)
            if row.get("missing"):
                print(f"      served but undeclared: {', '.join(row['missing'][:6])}")
            if row.get("stale"):
                print(f"      declared but unserved: {', '.join(row['stale'][:6])}")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
