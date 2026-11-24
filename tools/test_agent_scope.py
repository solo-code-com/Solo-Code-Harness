"""Tests for tools/agent_scope.py"""

import threading

import pytest

from tools.agent_scope import AgentScopeRegistry, ToolDefinition


def _fn(label: str):
    return lambda args: f"{label}:{args.get('x')}"


# ── register_global ────────────────────────────────────────────────────────────


def test_register_global_and_resolve():
    r = AgentScopeRegistry()
    r.register_global(ToolDefinition("bash", "run", _fn("g")))
    t = r.resolve("bash")
    assert t is not None
    assert t.execute({"x": "ls"}) == "g:ls"


def test_register_global_duplicate_raises():
    r = AgentScopeRegistry()
    r.register_global(ToolDefinition("bash", "run", _fn("g")))
    with pytest.raises(ValueError, match="already registered"):
        r.register_global(ToolDefinition("bash", "run", _fn("g2")))


def test_register_global_empty_name_raises():
    r = AgentScopeRegistry()
    with pytest.raises(ValueError):
        r.register_global(ToolDefinition("", "bad", _fn("g")))


def test_register_global_undo_removes_tool():
    r = AgentScopeRegistry()
    undo = r.register_global(ToolDefinition("bash", "run", _fn("g")))
    undo()
    assert r.resolve("bash") is None


def test_undo_is_idempotent():
    r = AgentScopeRegistry()
    undo = r.register_global(ToolDefinition("bash", "run", _fn("g")))
    undo()
    undo()  # second call must not raise
    assert r.resolve("bash") is None


def test_stale_undo_does_not_remove_reregistration():
    r = AgentScopeRegistry()
    undo_old = r.register_global(ToolDefinition("bash", "v1", _fn("v1")))
    undo_old()  # removes v1
    r.register_global(ToolDefinition("bash", "v2", _fn("v2")))  # re-register
    undo_old()  # stale undo — must NOT remove v2
    assert r.resolve("bash") is not None
    assert r.resolve("bash").description == "v2"


# ── register_scoped ────────────────────────────────────────────────────────────


def test_scoped_shadows_global():
    r = AgentScopeRegistry()
    r.register_global(ToolDefinition("bash", "global", _fn("global")))
    r.register_scoped("agent-a", ToolDefinition("bash", "scoped", _fn("scoped")))
    assert r.resolve("bash", "agent-a").execute({"x": "ls"}) == "scoped:ls"
    assert r.resolve("bash", "agent-b").execute({"x": "ls"}) == "global:ls"
    assert r.resolve("bash").execute({"x": "ls"}) == "global:ls"


def test_register_scoped_empty_scope_key_raises():
    r = AgentScopeRegistry()
    with pytest.raises(ValueError, match="scope_key"):
        r.register_scoped("", ToolDefinition("bash", "d", _fn("g")))


def test_register_scoped_empty_name_raises():
    r = AgentScopeRegistry()
    with pytest.raises(ValueError):
        r.register_scoped("agent-a", ToolDefinition("", "bad", _fn("g")))


def test_register_scoped_duplicate_raises():
    r = AgentScopeRegistry()
    r.register_scoped("agent-a", ToolDefinition("bash", "d", _fn("g")))
    with pytest.raises(ValueError, match="already registered"):
        r.register_scoped("agent-a", ToolDefinition("bash", "d2", _fn("g2")))


def test_scoped_undo_removes_only_that_scope():
    r = AgentScopeRegistry()
    r.register_global(ToolDefinition("bash", "g", _fn("g")))
    undo = r.register_scoped("agent-a", ToolDefinition("bash", "s", _fn("s")))
    undo()
    # scoped removed — falls back to global
    assert r.resolve("bash", "agent-a").execute({"x": "x"}) == "g:x"


def test_stale_scoped_undo_does_not_remove_reregistration():
    r = AgentScopeRegistry()
    undo_old = r.register_scoped("agent-a", ToolDefinition("bash", "v1", _fn("v1")))
    undo_old()
    r.register_scoped("agent-a", ToolDefinition("bash", "v2", _fn("v2")))
    undo_old()  # stale — must not remove v2
    assert r.resolve("bash", "agent-a").description == "v2"


# ── resolve ────────────────────────────────────────────────────────────────────


def test_resolve_nonexistent_returns_none():
    r = AgentScopeRegistry()
    assert r.resolve("nope") is None
    assert r.resolve("nope", "agent-a") is None


def test_resolve_without_scope_ignores_scoped():
    r = AgentScopeRegistry()
    r.register_scoped("agent-a", ToolDefinition("secret", "s", _fn("s")))
    assert r.resolve("secret") is None


# ── list_tools ─────────────────────────────────────────────────────────────────


def test_list_tools_global_only():
    r = AgentScopeRegistry()
    r.register_global(ToolDefinition("b", "B", _fn("b")))
    r.register_global(ToolDefinition("a", "A", _fn("a")))
    tools = r.list_tools()
    assert [t.name for t in tools] == ["a", "b"]


def test_list_tools_scoped_merges_and_shadows():
    r = AgentScopeRegistry()
    r.register_global(ToolDefinition("bash", "g", _fn("g")))
    r.register_global(ToolDefinition("read", "r", _fn("r")))
    r.register_scoped("agent-a", ToolDefinition("bash", "s", _fn("s")))
    tools = r.list_tools("agent-a")
    assert len(tools) == 2
    bash = next(t for t in tools if t.name == "bash")
    assert bash.description == "s"


def test_list_tools_empty():
    r = AgentScopeRegistry()
    assert r.list_tools() == []


# ── execute ────────────────────────────────────────────────────────────────────


def test_execute_global():
    r = AgentScopeRegistry()
    r.register_global(ToolDefinition("add", "add", lambda a: a["x"] + a["y"]))
    assert r.execute("add", {"x": 1, "y": 2}) == 3


def test_execute_unknown_raises_key_error():
    r = AgentScopeRegistry()
    with pytest.raises(KeyError, match="nope"):
        r.execute("nope", {})


def test_execute_scoped():
    r = AgentScopeRegistry()
    r.register_global(ToolDefinition("echo", "e", lambda a: "global"))
    r.register_scoped("a", ToolDefinition("echo", "e", lambda a: "scoped"))
    assert r.execute("echo", {}, "a") == "scoped"
    assert r.execute("echo", {}) == "global"


# ── dispose_scope ──────────────────────────────────────────────────────────────


def test_dispose_scope_removes_all_scoped():
    r = AgentScopeRegistry()
    r.register_global(ToolDefinition("bash", "g", _fn("g")))
    r.register_scoped("agent-a", ToolDefinition("bash", "s", _fn("s")))
    r.register_scoped("agent-a", ToolDefinition("read", "r", _fn("r")))
    r.dispose_scope("agent-a")
    assert r.resolve("bash", "agent-a").description == "g"
    assert r.resolve("read", "agent-a") is None


def test_dispose_scope_idempotent():
    r = AgentScopeRegistry()
    r.dispose_scope("nonexistent")  # must not raise


def test_active_scopes():
    r = AgentScopeRegistry()
    assert r.active_scopes() == []
    r.register_scoped("agent-a", ToolDefinition("bash", "d", _fn("g")))
    r.register_scoped("agent-b", ToolDefinition("bash", "d", _fn("g")))
    assert sorted(r.active_scopes()) == ["agent-a", "agent-b"]
    r.dispose_scope("agent-a")
    assert r.active_scopes() == ["agent-b"]


# ── thread safety ──────────────────────────────────────────────────────────────


def test_concurrent_register_global():
    r = AgentScopeRegistry()
    errors = []

    def worker(i):
        try:
            r.register_global(ToolDefinition(f"tool-{i}", "d", _fn(str(i))))
        except ValueError as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(r.list_tools()) == 20
