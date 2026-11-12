#!/usr/bin/env python3
"""Solo-Code MCP server for DeepSeek Harness.

Exposes the Solo-Code tool layer over stdio using newline-delimited JSON-RPC 2.0.
Standard library only: the DSH runtime spawns this file with the interpreter it
already has.

Safety rules this server enforces itself, independent of the caller:
- every command runs with the workspace root as its working directory;
- path arguments are resolved and must stay inside that root;
- commands are argv lists, never a shell string;
- timeouts are capped, and delegate wrappers never receive write-enabling flags.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SERVER_NAME = 'solo-code'
SERVER_VERSION = '0.1.0'
DEFAULT_PROTOCOL = '2024-11-05'
MAX_TIMEOUT = 900
MAX_STDOUT = 20_000
MAX_STDERR = 4_000

ROOT = Path.cwd()

DELEGATES = {
    'opencode': ('tools/opencode_delegate.py', True),
    'codex': ('tools/codex_delegate.py', True),
    'kilo': ('tools/kilo_cli_delegate.py', True),
    'dsh': ('tools/dsh_delegate.py', False),
}


def log(message: str) -> None:
    """Diagnostics go to stderr; stdout carries protocol messages only."""
    print(f'[solo-code-mcp] {message}', file=sys.stderr, flush=True)


def text_result(payload: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {
        'content': [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}],
        'isError': is_error,
    }


def run(argv: list[str], timeout: int) -> dict[str, Any]:
    """Run one argv list under the workspace root and return its captured result."""
    limit = max(1, min(int(timeout), MAX_TIMEOUT))
    started = time.monotonic()
    environ = dict(os.environ)
    environ['PYTHONIOENCODING'] = 'utf-8'
    try:
        completed = subprocess.run(
            argv,
            cwd=str(ROOT),
            env=environ,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=limit,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return {'ok': False, 'timedOut': True, 'limitSeconds': limit, 'command': argv[1:3]}
    except OSError as error:
        return {'ok': False, 'error': str(error), 'command': argv[1:3]}
    return {
        'ok': completed.returncode == 0,
        'exitCode': completed.returncode,
        'durationMs': int((time.monotonic() - started) * 1000),
        'stdout': completed.stdout[-MAX_STDOUT:],
        'stderr': completed.stderr[-MAX_STDERR:],
    }


def confined_path(raw: str) -> Path:
    """Resolve a caller path and reject anything outside the workspace root."""
    candidate = Path(raw)
    resolved = (candidate if candidate.is_absolute() else ROOT / candidate).resolve()
    root = ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f'path escapes the workspace root: {raw}')
    return resolved


def tool_security_scan(args: dict[str, Any]) -> dict[str, Any]:
    target = confined_path(str(args.get('path', '.')))
    return run([sys.executable, '.github/scripts/security_scan.py', str(target)], args.get('timeout', 120))


def tool_shared_state(args: dict[str, Any]) -> dict[str, Any]:
    view = str(args.get('view', 'show'))
    if view not in {'show', 'features', 'sessions', 'locks', 'memory', 'validate'}:
        raise ValueError(f'unsupported view: {view}')
    return run([sys.executable, 'tools/shared_state.py', view], args.get('timeout', 60))


def tool_model_catalog(args: dict[str, Any]) -> dict[str, Any]:
    argv = [sys.executable, 'tools/check_model_catalog.py', '--json']
    argv += ['--timeout', str(int(args.get('timeout', 60)))]
    probe = args.get('probe') or []
    if isinstance(probe, list) and probe:
        argv += ['--probe', *[str(item) for item in probe]]
    return run(argv, int(args.get('timeout', 60)) + 30)


ROUTING: dict[str, tuple[str, str]] = {
    'simple': ('opencode', 'mechanical change: cheapest capable worker'),
    'complex': ('codex', 'reasoning-heavy task: independent model with evidence'),
    'review': ('codex', 'independent review: a second model catches different defects'),
    'verify': ('dsh', 'verification run through the harness profile'),
}
FALLBACK_ROUTE = ('opencode', 'unclassified task: fallback worker')
LEDGER = Path('.solocode/dsh-routing.jsonl')


def delegate_argv(executor: str, prompt: str, model: str | None, timeout: int) -> list[str]:
    script, accepts_directory = DELEGATES[executor]
    argv = [sys.executable, script, prompt, '--timeout', str(max(1, min(timeout, MAX_TIMEOUT)))]
    if accepts_directory:
        argv += ['--directory', str(ROOT)]
    if model:
        argv += ['--model', model]
    return argv


def route_for(task_type: str) -> tuple[str, str]:
    return ROUTING.get(task_type, FALLBACK_ROUTE)


def append_ledger(entry: dict[str, Any]) -> None:
    """Append one routing decision to the local ledger; never fail the call for it."""
    target = ROOT / LEDGER
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError as error:
        log(f'ledger write failed: {error}')


def evidence_table(executor: str, argv: list[str], result: dict[str, Any]) -> list[dict[str, Any]]:
    command = ' '.join([Path(argv[0]).name, *argv[1:3]])
    tail = (result.get('stdout') or result.get('stderr') or '')[-2000:]
    return [
        {
            'claim': f'{executor} worker finished',
            'command': command,
            'output': f"exit={result.get('exitCode')} durationMs={result.get('durationMs')}",
        },
        {'claim': f'{executor} worker output', 'command': command, 'output': tail},
    ]


def tool_delegate(args: dict[str, Any]) -> dict[str, Any]:
    executor = str(args.get('executor', 'codex'))
    if executor not in DELEGATES:
        raise ValueError(f'unsupported executor: {executor}')
    prompt = str(args.get('prompt', '')).strip()
    if not prompt:
        raise ValueError('prompt is required')
    model = args.get('model') if isinstance(args.get('model'), str) else None
    timeout = int(args.get('timeout', 600))
    return run(delegate_argv(executor, prompt, model, timeout), timeout + 30)


def tool_route(args: dict[str, Any]) -> dict[str, Any]:
    prompt = str(args.get('task', '')).strip()
    if not prompt:
        raise ValueError('task is required')
    task_type = str(args.get('task_type', 'simple'))
    executor, reason = route_for(task_type)
    model = args.get('model') if isinstance(args.get('model'), str) else None
    timeout = int(args.get('timeout', 600))
    argv = delegate_argv(executor, prompt, model, timeout)
    decision = {
        'taskType': task_type,
        'executor': executor,
        'reason': reason,
        'promptChars': len(prompt),
        'promptHash': hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:12],
        'at': int(time.time()),
    }
    if args.get('dry_run') is True:
        append_ledger({**decision, 'spawned': False})
        return {'ok': True, 'dryRun': True, **decision, 'command': [Path(argv[0]).name, argv[1]]}
    result = run(argv, timeout + 30)
    append_ledger({**decision, 'spawned': True, 'ok': result.get('ok', False), 'exitCode': result.get('exitCode')})
    return {
        'ok': result.get('ok', False),
        **decision,
        'exitCode': result.get('exitCode'),
        'durationMs': result.get('durationMs'),
        'evidence': evidence_table(executor, argv, result),
        'stderr': (result.get('stderr') or '')[-1000:],
    }


def ruff_python() -> str:
    """Prefer the project virtualenv for ruff; fall back to this interpreter."""
    for relative in ('.venv/Scripts/python.exe', '.venv/bin/python'):
        candidate = ROOT / relative
        if candidate.is_file():
            return str(candidate)
    return sys.executable


GATES: dict[str, Callable[[], list[str]]] = {
    'lint': lambda: [ruff_python(), '-m', 'ruff', 'check', '.'],
    'lint_budget': lambda: [sys.executable, 'tools/check_lint_budget.py'],
    'schemas': lambda: [sys.executable, 'tools/validate_schemas.py'],
    'drift': lambda: [sys.executable, 'tools/garden.py'],
    'tests': lambda: [sys.executable, '-m', 'pytest', 'tools/', '-q'],
    'secrets': lambda: [sys.executable, '.github/scripts/security_scan.py', '.'],
    'all': lambda: [sys.executable, '.github/scripts/checklist.py', '.', '--fast'],
}


def tool_gate(args: dict[str, Any]) -> dict[str, Any]:
    gate = str(args.get('gate', 'lint'))
    build = GATES.get(gate)
    if build is None:
        raise ValueError(f'unsupported gate: {gate}')
    default_timeout = 600 if gate in {'tests', 'all'} else 180
    return run(build(), args.get('timeout', default_timeout))


TOOLS: dict[str, dict[str, Any]] = {
    'security_scan': {
        'description': 'Run the Solo-Code secret scan over a path inside the workspace.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': 'Path inside the workspace; defaults to the root.'},
                'timeout': {'type': 'integer', 'description': 'Seconds before the scan is abandoned (max 900).'},
            },
            'additionalProperties': False,
        },
        'handler': tool_security_scan,
    },
    'shared_state': {
        'description': 'Read the Solo-Code shared state: sessions, features, locks, memory, or integrity.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'view': {
                    'type': 'string',
                    'enum': ['show', 'features', 'sessions', 'locks', 'memory', 'validate'],
                },
                'timeout': {'type': 'integer', 'description': 'Seconds before the read is abandoned.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_shared_state,
    },
    'model_catalog': {
        'description': 'Check model catalogs against the configured providers; optionally probe models.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'probe': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Models to call once, as provider/model; omitted means compare listings only.',
                },
                'timeout': {'type': 'integer', 'description': 'Per-request timeout in seconds.'},
            },
            'additionalProperties': False,
        },
        'handler': tool_model_catalog,
    },
    'delegate': {
        'description': 'Delegate one self-contained task to a Solo-Code worker engine and return its evidence.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'executor': {'type': 'string', 'enum': ['opencode', 'codex', 'kilo', 'dsh']},
                'prompt': {'type': 'string', 'description': 'Self-contained brief; inline every path and rule the worker needs.'},
                'model': {'type': 'string', 'description': 'Optional provider/model override.'},
                'timeout': {'type': 'integer', 'description': 'Seconds before the worker is abandoned (max 900).'},
            },
            'required': ['executor', 'prompt'],
            'additionalProperties': False,
        },
        'handler': tool_delegate,
    },
    'gate': {
        'description': 'Run one Solo-Code verification gate and return its evidence.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'gate': {
                    'type': 'string',
                    'enum': ['lint', 'lint_budget', 'schemas', 'drift', 'tests', 'secrets', 'all'],
                    'description': 'lint=ruff; lint_budget=ratchet; schemas=frontmatter; drift=garden; tests=pytest tools/; secrets=scan; all=checklist --fast.',
                },
                'timeout': {'type': 'integer', 'description': 'Seconds before the gate is abandoned (max 900).'},
            },
            'required': ['gate'],
            'additionalProperties': False,
        },
        'handler': tool_gate,
    },
    'route': {
        'description': 'Route a task to one worker engine by task type, return an evidence table, and log the decision.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'task': {'type': 'string', 'description': 'Self-contained brief; inline every path and rule the worker needs.'},
                'task_type': {
                    'type': 'string',
                    'enum': ['simple', 'complex', 'review', 'verify'],
                    'description': 'simple=opencode; complex/review=codex; verify=dsh.',
                },
                'model': {'type': 'string', 'description': 'Optional provider/model override.'},
                'timeout': {'type': 'integer', 'description': 'Seconds before the worker is abandoned (max 900).'},
                'dry_run': {'type': 'boolean', 'description': 'Return the routing decision without spawning a worker.'},
            },
            'required': ['task'],
            'additionalProperties': False,
        },
        'handler': tool_route,
    },
}


def list_tools() -> dict[str, Any]:
    return {
        'tools': [
            {'name': name, 'description': spec['description'], 'inputSchema': spec['inputSchema']}
            for name, spec in TOOLS.items()
        ]
    }


def call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = str(params.get('name', ''))
    spec = TOOLS.get(name)
    if spec is None:
        return text_result({'ok': False, 'error': f'unknown tool: {name}'}, is_error=True)
    arguments = params.get('arguments')
    args = arguments if isinstance(arguments, dict) else {}
    handler: Callable[[dict[str, Any]], dict[str, Any]] = spec['handler']
    try:
        payload = handler(args)
    except ValueError as error:
        return text_result({'ok': False, 'error': str(error)}, is_error=True)
    return text_result(payload, is_error=not payload.get('ok', False))


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch one JSON-RPC message; notifications return no response."""
    method = request.get('method')
    request_id = request.get('id')
    params = request.get('params') if isinstance(request.get('params'), dict) else {}
    if request_id is None:
        return None
    if method == 'initialize':
        requested = params.get('protocolVersion')
        version = requested if isinstance(requested, str) and requested else DEFAULT_PROTOCOL
        result = {
            'protocolVersion': version,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
        }
    elif method == 'tools/list':
        result = list_tools()
    elif method == 'tools/call':
        result = call_tool(params)
    elif method == 'ping':
        result = {}
    else:
        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'error': {'code': -32601, 'message': f'method not found: {method}'},
        }
    return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def main() -> int:
    global ROOT
    argv = sys.argv[1:]
    if '--root' in argv:
        index = argv.index('--root')
        if index + 1 < len(argv):
            ROOT = Path(argv[index + 1]).resolve()
    if not ROOT.is_dir():
        log(f'workspace root is not a directory: {ROOT}')
        return 2
    log(f'ready: root={ROOT}')
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}}) + '\n')
            sys.stdout.flush()
            continue
        if not isinstance(message, dict):
            continue
        if message.get('method') == 'exit':
            break
        response = handle(message)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + '\n')
        sys.stdout.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
