# Defensive Programming Patterns

Hard-won bug-class rules: each pattern below is a class of defect that actually shipped or nearly shipped in production systems, stated as the rule that prevents its recurrence. Read this before writing lifecycle, concurrency, subprocess, or teardown code.

These patterns are drawn from real incidents in the DeepSeek harness and Solo-Code-CLI development. Each rule exists because the violation caused an actual bug.

---

## Report Orthogonal Outcomes Independently

**Problem**: A process can exhibit multiple independent states simultaneously — it can time out AND exit 0 because it trapped the signal. If you report only one fact, or nest one flag's report inside another's branch, a caller may read a cut-short run as clean success.

**Rule**: Surface each independent fact (`timedOut`, `signal`, `exitCode`) on its own field. Never gate one outcome's report behind another outcome's branch.

**Example (Wrong)**:
```python
def run_subprocess(cmd, timeout):
    try:
        result = subprocess.run(cmd, timeout=timeout)
        return {"success": True, "exit_code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"success": False}  # Lost exit code if process exited before timeout handler ran
```

**Example (Right)**:
```python
def run_subprocess(cmd, timeout):
    timed_out = False
    exit_code = None
    signal = None

    try:
        result = subprocess.run(cmd, timeout=timeout)
        exit_code = result.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        # Process may have exited before we killed it
        if e.returncode is not None:
            exit_code = e.returncode

    return {
        "timed_out": timed_out,
        "exit_code": exit_code,
        "signal": signal,
    }
```

---

## Honor Public Contracts on BOTH Sides

**Problem**: When an implementation receives several representations of one outcome (exception thrown, error object returned, error flag set), returning them inconsistently forces callers to handle every form.

**Rule**: Normalize all error representations to the public contract before returning. Document the normalized contract where the type is defined. Test every source form through the real consumer.

**Example**: `LlmAdapter.stream()` implementations may throw or emit `finish {kind:'error'|'aborted'}`, but `LlmRuntime.stream()` exposes model-request failures only as terminal finish chunks. Middleware and consumer defects remain thrown. This keeps consumers from guessing whether a caught exception came from the provider, a wrapper, chunk logging, or their own assembly.

**Application to Solo-Code-CLI**:
- Hook scripts may fail in different ways (exit 2, exit 1, exception, timeout)
- Normalize to a consistent `{allowed: bool, reason?: str}` contract
- Never let one failure mode bypass the contract

---

## Async State Is Not Synchronous State

**Problem**: `agent.followup()` has no per-message completion or result. A background job's completion races turn boundaries. `reader.close()` fires for both EOF and disposal. Treating `agent/status` or `whenIdle()` as the result of one follow-up is wrong: several queued follow-ups, steering, and injected work may share one `running` interval, while cancellation or disposal can discard unstarted items.

**Rule**: An automation caller that truly owns a run must define its interval explicitly — for example, from its message's durable inbox receipt through the next whole-agent `idle` — and describe any selected output as interval-wide rather than causally attributed to that message.

**Guard the other way**: If the awaited transition can never occur, the wait hangs. Handle the "nothing to wait for" branch explicitly.

**Example (Wrong)**:
```python
async def run_agent_task(agent, task):
    agent.followup(task)
    await agent.when_idle()  # Which idle? Could be for a different task
    return agent.last_output  # Which output? Could be from queued work
```

**Example (Right)**:
```python
async def run_agent_task(agent, task):
    task_id = agent.followup(task)

    # Define explicit interval: from task submission to task completion
    async for event in agent.events():
        if event.task_id == task_id and event.kind == "completed":
            return event.output
        elif event.task_id == task_id and event.kind == "cancelled":
            raise TaskCancelled()
```

---

## Dispose Must Reach Quiescence, Not Just Request It

**Problem**: A teardown that issues kills/aborts but returns before the work stops leaves orphans. Late completions may fire handlers after the parent context is torn down.

**Rule**: Make cleanup async and await the children's exit (`kill` → `await done`). Close listener/notification registries BEFORE killing so late completions stay silent.

**Example (Wrong)**:
```python
def cleanup(self):
    for proc in self.processes:
        proc.kill()  # Sent signal, but didn't wait
    # Returns immediately - processes still running
```

**Example (Right)**:
```python
async def cleanup(self):
    # 1. Stop accepting new notifications
    self.event_registry.close()

    # 2. Request termination
    for proc in self.processes:
        proc.kill()

    # 3. Wait for quiescence
    await asyncio.gather(
        *[proc.wait() for proc in self.processes],
        return_exceptions=True
    )

    # 4. Now safe to return - no orphans
```

---

## Contain Callback Exceptions in the Dispatcher

**Problem**: A user-supplied listener that throws must not reject the promise it runs inside or starve the listeners after it.

**Rule**: Wrap the dispatch loop in try/catch and log. One bad subscriber never breaks core lifecycle.

**Example (Wrong)**:
```python
async def notify_listeners(self, event):
    for listener in self.listeners:
        await listener(event)  # If listener throws, later listeners never run
```

**Example (Right)**:
```python
async def notify_listeners(self, event):
    for listener in self.listeners:
        try:
            await listener(event)
        except Exception as e:
            # Log but continue to next listener
            logger.error(f"Listener {listener} failed: {e}")
            # Later listeners still run
```


## Never Hand Untrusted Output the Ambient Environment or Predictable Paths

**Problem**: Spawned commands that inherit the full environment may leak harness credentials into output, `env` dumps, or spill files. Predictable world-readable temp paths invite symlink races and disclosure.

**Rule**:
1. Scrub spawned command environments: drop `*KEY*`, `*SECRET*`, `*TOKEN*`, `*PASSWORD*`
2. Use private temp directories (mode 0700)
3. Use random filenames
4. Open files exclusively with owner-only permissions (`'wx'`, `0o600`)

**Example (Wrong)**:
```python
import subprocess
import os

def run_worker(cmd):
    # Inherits ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, etc.
    subprocess.run(cmd, env=os.environ)

    # Predictable path, world-readable
    output_file = "/tmp/worker-output.txt"
    with open(output_file, "w") as f:
        f.write(result)
```

**Example (Right)**:
```python
import subprocess
import tempfile
import secrets
from pathlib import Path

def run_worker(cmd):
    # Scrub environment
    clean_env = {
        k: v for k, v in os.environ.items()
        if not any(pattern in k.upper() for pattern in
                   ["KEY", "SECRET", "TOKEN", "PASSWORD"])
    }
    subprocess.run(cmd, env=clean_env)

    # Private directory (0700) with random filename
    private_dir = Path(tempfile.mkdtemp(prefix="worker-"))
    private_dir.chmod(0o700)

    output_file = private_dir / f"{secrets.token_hex(8)}.txt"

    # Exclusive open, owner-only (0600)
    fd = os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(result)
```

---

## Unlink Link-Shaped Paths

**Problem**: A path that may be a symlink or Windows junction can cause `rm` to follow the link into its target and delete real files.

**Rule**: Check if path is a symlink with `lstatSync().isSymbolicLink()` (not `statSync()` which follows links), then use `unlinkSync()` to delete only the link. Never use recursive delete on a link — it may descend through the link into its target.

**Example (Wrong)**:
```python
import shutil
from pathlib import Path

def cleanup(path):
    # DANGER: if path is a symlink to /important/data, this deletes /important/data
    if path.exists():
        shutil.rmtree(path)
```

**Example (Right)**:
```python
import os
from pathlib import Path

def cleanup(path):
    path = Path(path)

    # Use lstat (not stat) to check link itself, not target
    if path.exists(follow_symlinks=False):
        if path.is_symlink():
            # Unlink only the link, never follow
            path.unlink()
        elif path.is_dir():
            # Only use recursive delete on real directories
            shutil.rmtree(path)
        else:
            path.unlink()
```

---

## Application to Solo-Code-CLI

These patterns are particularly relevant to:

1. **Hook Lifecycle** (`.claude/hooks/*.py`, `.kilo/hooks/*.js`)
   - Hooks must report orthogonal outcomes (blocked, allowed, timeout, error)
   - Dispose must reach quiescence before returning

2. **Subprocess Execution** (`tools/opencode_delegate.py`, `tools/kilo_cli_delegate.py`)
   - Environment scrubbing for worker processes
   - Timeout + exit code + signal reporting

3. **Async Agent Communication** (future: session persistence, query system)
   - Async state is not synchronous state
   - Event-driven completion tracking

4. **Cleanup Paths** (`tools/snapshot_testing.py`, test fixtures)
   - Symlink-aware deletion
   - Private temp directories

---

## Testing Defensive Patterns

Each pattern should have a corresponding test that exercises the failure mode:

- **Orthogonal outcomes**: Test timeout + exit 0
- **Public contracts**: Test all error source forms
- **Async state**: Test interleaved operations
- **Dispose quiescence**: Test orphan detection
- **Callback exceptions**: Test listener that throws
- **Environment scrubbing**: Test for leaked credentials
- **Symlink safety**: Test deletion with symlink present

See `tools/test_guard.py` and `tools/test_e2e.py` for examples.

---

## References

- Original source: `deepseek-harness-master/docs/defensive-patterns.md`
- Testing patterns: `deepseek-harness-master/docs/testing.md`
- Hook implementations: `.claude/hooks/guard.py`, `.claude/hooks/security_post.py`
