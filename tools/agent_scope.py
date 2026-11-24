#!/usr/bin/env python3
"""
agent_scope.py — Scoped tool/skill registration for per-agent isolation.

Pattern ported from DeepSeek Harness packages/core/scope/ and packages/core/tools/.

Two registration layers:
  - Global: visible to all agents
  - Scoped: visible only to the agent that owns the scope, shadows global

Scope lifetime is tied to the agent: when the agent is disposed, its
scoped registrations are automatically removed. This enforces the invariant
that registration context determines both visibility AND lifetime.

Usage:
    registry = AgentScopeRegistry()
    registry.register_global(ToolDefinition(name="bash", execute=run_bash))
    registry.register_scoped("agent-a", ToolDefinition(name="bash", execute=sandbox_bash))

    registry.resolve("bash")           # -> global bash
    registry.resolve("bash", "agent-a")  # -> sandbox_bash (scoped shadow)
    registry.resolve("bash", "agent-b")  # -> global bash

    registry.dispose_scope("agent-a")  # removes all agent-a scoped tools
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDefinition:
    name: str
    description: str
    execute: Callable[[dict[str, Any]], Any]


@dataclass
class _ScopeEntry:
    tools: dict[str, ToolDefinition] = field(default_factory=dict)


class AgentScopeRegistry:
    """
    Thread-safe scoped tool registry.

    Global tools are the baseline. A scoped registration for (scope, name)
    shadows the global for that scope only. Disposing a scope reclaims all
    its registrations atomically.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._global: dict[str, ToolDefinition] = {}
        self._scopes: dict[str, _ScopeEntry] = {}

    # ------------------------------------------------------------------ #
    # Registration                                                         #
    # ------------------------------------------------------------------ #

    def register_global(self, tool: ToolDefinition) -> Callable[[], None]:
        """Register a tool globally. Returns an undo callable."""
        if not tool.name:
            raise ValueError("Tool name must not be empty")
        with self._lock:
            if tool.name in self._global:
                raise ValueError(f"Global tool '{tool.name}' already registered")
            self._global[tool.name] = tool

        def _undo() -> None:
            with self._lock:
                if self._global.get(tool.name) is tool:
                    del self._global[tool.name]

        return _undo

    def register_scoped(
        self, scope_key: str, tool: ToolDefinition
    ) -> Callable[[], None]:
        """
        Register a tool for a specific agent scope, shadowing the global entry
        of the same name for that scope only. Returns an undo callable.
        """
        if not scope_key:
            raise ValueError("scope_key must not be empty")
        if not tool.name:
            raise ValueError("Tool name must not be empty")
        with self._lock:
            entry = self._scopes.setdefault(scope_key, _ScopeEntry())
            if tool.name in entry.tools:
                raise ValueError(
                    f"Scoped tool '{tool.name}' already registered for scope '{scope_key}'"
                )
            entry.tools[tool.name] = tool

        def _undo() -> None:
            with self._lock:
                if (
                    scope_key in self._scopes
                    and self._scopes[scope_key].tools.get(tool.name) is tool
                ):
                    del self._scopes[scope_key].tools[tool.name]

        return _undo

    # ------------------------------------------------------------------ #
    # Resolution                                                           #
    # ------------------------------------------------------------------ #

    def resolve(self, name: str, scope_key: str | None = None) -> ToolDefinition | None:
        """
        Resolve a tool name. Scoped entry shadows global when scope is given.
        Returns None when no tool is found.
        """
        with self._lock:
            if scope_key is not None:
                entry = self._scopes.get(scope_key)
                if entry and name in entry.tools:
                    return entry.tools[name]
            return self._global.get(name)

    def list_tools(self, scope_key: str | None = None) -> list[ToolDefinition]:
        """
        Return all tools visible from the given scope (global merged with
        scoped, nearest wins), sorted by name.
        """
        with self._lock:
            merged: dict[str, ToolDefinition] = dict(self._global)
            if scope_key is not None:
                entry = self._scopes.get(scope_key)
                if entry:
                    merged.update(entry.tools)
        return sorted(merged.values(), key=lambda t: t.name)

    # ------------------------------------------------------------------ #
    # Execution                                                            #
    # ------------------------------------------------------------------ #

    def execute(
        self, name: str, args: dict[str, Any], scope_key: str | None = None
    ) -> Any:
        """Resolve and execute a tool. Raises KeyError when not found."""
        tool = self.resolve(name, scope_key)
        if tool is None:
            scope_label = f" (scope '{scope_key}')" if scope_key else ""
            raise KeyError(f"Unknown tool '{name}'{scope_label}")
        return tool.execute(args)

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def dispose_scope(self, scope_key: str) -> None:
        """Remove all scoped registrations for this agent. Idempotent."""
        with self._lock:
            self._scopes.pop(scope_key, None)

    def active_scopes(self) -> list[str]:
        """Return scope keys that have at least one registration."""
        with self._lock:
            return [k for k, v in self._scopes.items() if v.tools]


# --------------------------------------------------------------------------- #
# CLI / self-test                                                              #
# --------------------------------------------------------------------------- #


def _self_test() -> None:
    print("Running agent_scope self-test...")

    registry = AgentScopeRegistry()

    def _global_bash(args: dict) -> str:
        return f"global-bash: {args.get('cmd')}"

    def _sandbox_bash(args: dict) -> str:
        return f"sandbox-bash: {args.get('cmd')}"

    def _read_file(args: dict) -> str:
        return f"read: {args.get('path')}"

    # Register globals
    undo_bash = registry.register_global(
        ToolDefinition("bash", "run shell", _global_bash)
    )
    registry.register_global(ToolDefinition("read_file", "read a file", _read_file))

    # Register scoped override for agent-a
    registry.register_scoped(
        "agent-a", ToolDefinition("bash", "sandbox bash", _sandbox_bash)
    )

    # Resolution tests
    assert registry.resolve("bash") is not None
    assert registry.resolve("bash").execute({"cmd": "ls"}) == "global-bash: ls"
    assert (
        registry.resolve("bash", "agent-a").execute({"cmd": "ls"}) == "sandbox-bash: ls"
    )
    assert (
        registry.resolve("bash", "agent-b").execute({"cmd": "ls"}) == "global-bash: ls"
    )
    assert (
        registry.resolve("read_file", "agent-a").execute({"path": "/var"})
        == "read: /var"
    )
    assert registry.resolve("nonexistent") is None

    # List tools
    global_tools = registry.list_tools()
    assert len(global_tools) == 2  # bash + read_file

    scoped_tools = registry.list_tools("agent-a")
    assert len(scoped_tools) == 2  # sandbox bash (shadows) + read_file
    assert (
        scoped_tools[0].execute({"cmd": "x"}) == "sandbox-bash: x"
    )  # bash is first alphabetically

    # Undo global registration
    undo_bash()
    assert registry.resolve("bash") is None
    assert (
        registry.resolve("bash", "agent-a").execute({"cmd": "x"}) == "sandbox-bash: x"
    )

    # Dispose scope
    registry.dispose_scope("agent-a")
    assert registry.resolve("bash", "agent-a") is None
    assert registry.active_scopes() == []

    # Duplicate detection
    registry.register_global(ToolDefinition("bash", "bash", _global_bash))
    try:
        registry.register_global(ToolDefinition("bash", "bash", _global_bash))
        raise AssertionError("Expected ValueError for duplicate global")
    except ValueError:
        pass

    # Stale-undo identity check: undo must not remove a re-registration
    undo_bash2 = registry.register_global(
        ToolDefinition("ls", "list files", _global_bash)
    )
    undo_bash3 = registry.register_global(
        ToolDefinition("cat", "cat file", _global_bash)
    )
    undo_bash2()  # remove "ls"
    registry.register_global(
        ToolDefinition("ls", "list files v2", _sandbox_bash)
    )  # re-register
    undo_bash2()  # stale undo — must NOT remove the new "ls"
    assert registry.resolve("ls") is not None, (
        "Stale undo must not remove re-registration"
    )
    undo_bash3()

    # Validation: empty name/scope_key rejected
    try:
        registry.register_global(ToolDefinition("", "bad", _global_bash))
        raise AssertionError("Expected ValueError for empty tool name")
    except ValueError:
        pass
    try:
        registry.register_scoped("", ToolDefinition("x", "bad", _global_bash))
        raise AssertionError("Expected ValueError for empty scope_key")
    except ValueError:
        pass

    print("All tests passed.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Agent scope registry")
    parser.add_argument("--self-test", action="store_true", help="Run self-test")
    args = parser.parse_args()

    if args.self_test:
        _self_test()
    else:
        parser.print_help()
