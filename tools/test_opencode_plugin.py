"""Static guards for the OpenCode harness plugin.

The plugin cannot be unit-tested without an OpenCode runtime, but the two ways
it broke in design are static: importing a package that may be absent, and
exporting a shape the V2 loader rejects. Both are pinned here.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / ".opencode" / "plugins" / "harness" / "index.js"


def test_plugin_exists():
    assert PLUGIN.is_file(), PLUGIN


def test_plugin_exports_the_v2_shape():
    text = PLUGIN.read_text(encoding="utf-8")
    assert 'id: "harness"' in text
    assert "setup(ctx)" in text


def test_plugin_has_no_package_dependency():
    """A missing SDK must not be able to break a session.

    The name may appear in a comment explaining why it is not imported; only an
    actual import statement would reintroduce the dependency.
    """
    for line in PLUGIN.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")) and "@opencode/plugin" in stripped:
            raise AssertionError(f"plugin imports the SDK: {stripped}")


def test_every_hook_body_is_guarded():
    """Each hook registration sits in its own try/catch and logs the failure."""
    text = PLUGIN.read_text(encoding="utf-8")
    assert text.count("catch (error)") >= 2
    assert "hook unavailable" in text
