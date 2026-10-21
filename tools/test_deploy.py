#!/usr/bin/env python3
"""
Deploy Safety Tests
===================
Regression guards for tools/deploy.py's stale-cleanup logic.

Background: _cleanup_stale_files() used to decide what to delete at a target
project's ROOT by matching file EXTENSIONS (.md/.json/.yml/.ps1/...). Every
config file of a normal JS/TS/Docker project matches that filter, so deploying
the harness into a real project silently deleted package.json, package-lock.json,
tsconfig.json, docker-compose.yml and README.md from disk — breaking the build
with "Module not found: Can't resolve '@/lib/...'" once the tsconfig path
aliases disappeared.

The fix: cleanup may only remove root files the harness itself deployed
(ROOT_FILES + RETIRED_ROOT_FILES + .harness.lock). These tests pin that.

Usage:
    python -m pytest tools/test_deploy.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Unconditional: under `pytest tools/` the root is already on sys.path, so a guard
# left this file with one line the suite could never reach, and a duplicate entry
# costs nothing.
sys.path.insert(0, str(ROOT))

from tools import deploy  # noqa: E402
from tools.harness_fingerprint import lock_value  # noqa: E402


def _write(path: Path, text: str = "{}") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# Files a typical target project owns at its root. None of these belong to
# the harness, so none may ever be deleted by a deploy.
PROJECT_ROOT_FILES = [
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "docker-compose.yml",
    "next.config.mjs",
    "vite.config.ts",
    "README.md",
    "SPEC.md",
    "plan.md",
    "Dockerfile",
    "server.py",
    "pnpm-lock.yaml",
    "biome.json",
    "turbo.json",
    "renovate.json",
    "CHANGELOG.md",
]


def _make_project(tmp_path: Path) -> Path:
    proj = tmp_path / "target"
    for name in PROJECT_ROOT_FILES:
        _write(proj / name)
    _write(proj / "src" / "index.ts", "export {}\n")
    return proj


def test_project_root_files_survive_deploy(tmp_path):
    """The regression: deploy must not delete project-owned root files."""
    proj = _make_project(tmp_path)

    removed = deploy._cleanup_stale_files(proj, deploy.DIRS_ALL, dry_run=False)

    survivors = {p.name for p in proj.iterdir() if p.is_file()}
    missing = [n for n in PROJECT_ROOT_FILES if n not in survivors]
    assert not missing, f"deploy deleted project-owned files: {missing}"
    assert removed == 0, f"nothing was stale, but {removed} file(s) removed"


def test_dry_run_does_not_touch_disk(tmp_path):
    proj = _make_project(tmp_path)
    before = {p.name for p in proj.iterdir() if p.is_file()}

    deploy._cleanup_stale_files(proj, deploy.DIRS_ALL, dry_run=True)

    after = {p.name for p in proj.iterdir() if p.is_file()}
    assert before == after, "dry_run must not modify the filesystem"


def test_retired_harness_file_is_removed(tmp_path):
    """Cleanup must still do its job for files the harness once deployed."""
    proj = _make_project(tmp_path)
    _write(proj / "opencode.json")  # now ACTIVE (reintroduced v4.2.0) — must survive
    _write(proj / "opencode.ps1")  # retired launcher — must be removed

    removed = deploy._cleanup_stale_files(proj, deploy.DIRS_ALL, dry_run=False)

    assert (proj / "opencode.json").exists(), "active harness file must not be cleaned"
    assert not (proj / "opencode.ps1").exists(), "retired harness file not cleaned"
    assert removed == 1, f"expected 1 removal, got {removed}"
    # ...and the project's own files are still untouched.
    assert (proj / "package.json").exists()
    assert (proj / "tsconfig.json").exists()


def test_current_harness_files_are_kept(tmp_path):
    """Files the harness actively ships must not be flagged stale."""
    proj = _make_project(tmp_path)
    shipped = [f for f in deploy.ROOT_FILES if (deploy.ROOT / f).is_file()]
    assert shipped, "expected some ROOT_FILES to exist in the source repo"
    for name in shipped:
        _write(proj / name)

    deploy._cleanup_stale_files(proj, deploy.DIRS_ALL, dry_run=False)

    for name in shipped:
        assert (proj / name).exists(), f"actively-shipped harness file deleted: {name}"


def test_cleanup_scope_is_a_closed_set(tmp_path):
    """No project-owned name may leak into the harness-owned delete list."""
    owned = deploy.HARNESS_OWNED_ROOT_FILES
    for name in PROJECT_ROOT_FILES:
        assert name not in owned, (
            f"{name!r} is a project-owned file but appears in "
            f"HARNESS_OWNED_ROOT_FILES — deploy would delete it"
        )


def test_retired_files_are_not_also_shipped(tmp_path):
    """A name cannot be both actively shipped and retired."""
    overlap = set(deploy.ROOT_FILES) & deploy.RETIRED_ROOT_FILES
    assert not overlap, f"names are both shipped and retired: {overlap}"


# ─── shared directories (.github / .vscode / tools) ─────────────────────────
#
# Second instance of the same bug class: the per-directory loop deleted
# everything not in the current manifest, but .github/.vscode/tools are
# SHARED with the target project. A deploy would have wiped the project's
# CI workflows, CODEOWNERS, dependabot config, editor settings and dev
# scripts. Only explicitly retired harness files may be removed there.

PROJECT_SHARED_FILES = {
    ".github/workflows/deploy-prod.yml": "name: deploy\n",
    ".github/workflows/ci.yml": "name: project CI\n",
    ".github/CODEOWNERS": "* @team\n",
    ".github/dependabot.yml": "version: 2\n",
    ".github/ISSUE_TEMPLATE/bug.md": "bug\n",
    ".vscode/launch.json": "{}\n",
    ".vscode/settings.json": "{}\n",
    "tools/seed_db.py": "print(1)\n",
    "tools/migrate.sh": "echo hi\n",
}


def _make_shared(tmp_path: Path) -> Path:
    proj = tmp_path / "shared"
    for rel, body in PROJECT_SHARED_FILES.items():
        _write(proj / rel, body)
    return proj


def test_project_files_in_shared_dirs_survive(tmp_path):
    proj = _make_shared(tmp_path)

    removed = deploy._cleanup_stale_files(proj, deploy.DIRS_ALL, dry_run=False)

    for rel, body in PROJECT_SHARED_FILES.items():
        p = proj / rel
        assert p.exists(), f"deploy deleted project file in shared dir: {rel}"
        assert p.read_text(encoding="utf-8") == body, f"content changed: {rel}"
    assert removed == 0, f"nothing was stale, but {removed} file(s) removed"


def test_retired_shared_file_is_removed(tmp_path):
    """Cleanup must still remove harness files that shared dirs once held."""
    proj = _make_shared(tmp_path)
    for rel in deploy.RETIRED_SHARED_FILES:
        _write(proj / rel, "stale harness file\n")

    removed = deploy._cleanup_stale_files(proj, deploy.DIRS_ALL, dry_run=False)

    for rel in deploy.RETIRED_SHARED_FILES:
        assert not (proj / rel).exists(), f"retired harness file not cleaned: {rel}"
    assert removed == len(deploy.RETIRED_SHARED_FILES)
    # Project files in the same directories are untouched.
    for rel in PROJECT_SHARED_FILES:
        assert (proj / rel).exists(), f"collateral damage: {rel}"


def test_retired_shared_files_are_cleanable_despite_exclusion(tmp_path):
    """Regression: should_copy() must not shadow retired-file cleanup.

    Files are usually retired *by being added to EXCLUDE_FILES*, which makes
    should_copy() return False. When the should_copy() skip ran first, those
    files became permanently uncleanable — cleanup silently removed nothing.
    """
    assert deploy.RETIRED_SHARED_FILES, "expected at least one retired shared file"
    for rel in deploy.RETIRED_SHARED_FILES:
        proj = tmp_path / f"case_{Path(rel).name}"
        _write(proj / rel, "x\n")
        removed = deploy._cleanup_stale_files(proj, deploy.DIRS_ALL, dry_run=False)
        assert removed == 1, f"{rel} was not cleaned (removed={removed})"


def test_shared_dirs_are_disjoint_from_exclusive(tmp_path):
    overlap = deploy.SHARED_DIRS & deploy.EXCLUSIVE_HARNESS_DIRS
    assert not overlap, f"a dir cannot be both shared and exclusive: {overlap}"


def test_every_deployed_dir_is_classified(tmp_path):
    """A new DIRS_ALL entry must be declared shared or exclusive.

    Otherwise it silently defaults to full-wipe cleanup — the exact failure
    mode that destroyed project files in .github/ and tools/.
    """
    known = deploy.SHARED_DIRS | deploy.EXCLUSIVE_HARNESS_DIRS
    unclassified = [d for d in deploy.DIRS_ALL if d not in known]
    assert not unclassified, (
        f"unclassified deploy dirs {unclassified} — add each to SHARED_DIRS "
        f"(project also owns files there) or EXCLUSIVE_HARNESS_DIRS"
    )


def test_retired_shared_files_live_in_shared_dirs(tmp_path):
    for rel in deploy.RETIRED_SHARED_FILES:
        top = rel.split("/", 1)[0]
        assert top in deploy.SHARED_DIRS, (
            f"{rel} is listed as a retired SHARED file but {top}/ is not a shared dir"
        )


# ─── boundary declaration (.harness.lock) ───────────────────────────────────
#
# The whole point of the harness is that a model can tell control-plane files
# from the project's own code. .harness.lock is what it reads to do that, so
# a wrong boundary is a correctness bug, not a docs nit: it either invites a
# model to "fix" the project's CI as if it were harness config, or makes it
# treat harness files as project code to be refactored.


def _parse_lock_paths(lock_text: str) -> set[str]:
    """Extract [shared_files].paths — NOT the shared_dirs list above it."""
    import re

    block = lock_text.split("paths = [")[1].split("]")[0]
    return set(re.findall(r'"([^"]+)"', block))


def test_lock_shared_files_match_deployed_files(tmp_path):
    """Declared harness paths must equal what deploy actually ships there.

    A target lock declares only the files the deploy shipped into that target.
    """
    # .github/agents/*.agent.md are generated at deploy time from .copilot/agents/,
    # so they exist only AFTER a deploy generates them and cannot be copied from
    # ROOT here; test_lock_never_claims_a_project_file still covers their
    # declaration.
    copied = [
        p
        for p in deploy._shared_harness_paths(deploy.DIRS_ALL)
        if (deploy.ROOT / p).is_file()
    ]
    for rel in copied:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((deploy.ROOT / rel).read_bytes())
    deploy._generate_harness_lock(tmp_path, dry_run=False, dirs=deploy.DIRS_ALL)
    lock_text = (tmp_path / ".harness.lock").read_text(encoding="utf-8")
    declared = _parse_lock_paths(lock_text)
    expected = set(copied)
    assert declared == expected, (
        f"lock drifted from reality: missing={expected - declared}, "
        f"phantom={declared - expected}"
    )
    assert declared, "no shared harness paths declared at all"
    for rel in sorted(declared):
        assert (tmp_path / rel).is_file(), (
            f"declared path is not a file in the target: {rel}"
        )
    assert lock_value(lock_text, "harness", "content_fingerprint") == (
        deploy.harness_content_fingerprint(sorted(declared), tmp_path)
    ), "recorded content_fingerprint does not match the files in the target"


def test_lock_declares_shared_dirs_separately_from_owned_dirs(tmp_path):
    """Shared dirs must NOT be listed as wholly-harness directories.

    Listing .github/.vscode/tools under `dirs` told models that a project's
    own CI workflows and dev scripts were harness infrastructure.
    """
    deploy._generate_harness_lock(tmp_path, dry_run=False, dirs=deploy.DIRS_ALL)
    text = (tmp_path / ".harness.lock").read_text(encoding="utf-8")
    owned_block = text.split("dirs = [")[1].split("]")[0]
    for d in deploy.SHARED_DIRS:
        assert f'"{d}"' not in owned_block, (
            f"{d} is a shared dir but .harness.lock lists it as wholly harness"
        )
    shared_block = text.split("shared_dirs = [")[1].split("]")[0]
    for d in deploy.SHARED_DIRS:
        assert f'"{d}"' in shared_block, f"{d} missing from lock's shared_dirs"


def test_lock_never_claims_a_project_file(tmp_path):
    """A file the harness does not ship must never appear as harness."""
    declared = set(deploy._shared_harness_paths(deploy.DIRS_ALL))
    intruders = {
        ".github/workflows/deploy-prod.yml",
        ".github/CODEOWNERS",
        ".github/dependabot.yml",
        ".vscode/launch.json",
        "tools/seed_db.py",
    }
    overlap = declared & intruders
    assert not overlap, f"lock would claim project-owned files as harness: {overlap}"


# ─── the lock TEMPLATE must not drift from the deploy data model ────────────
#
# The template is a static string, so nothing tied it to ROOT_FILES /
# EXCLUSIVE_HARNESS_DIRS / SHARED_DIRS. It silently fell behind: .opencode
# (a whole first-class engine dir) was missing from `dirs`, and opencode.json,
# eslint.config.js and .env.template were missing from `files` -- so every
# deployed lock told the model those were project code it could edit.


def _parse_template_list(template: str, key: str) -> list[str]:
    import re

    m = re.search(rf"^{key} = \[(.*?)\]$", template, re.MULTILINE | re.DOTALL)
    assert m, f"lock template has no top-level {key} = [...] block"
    return re.findall(r'"([^"]+)"', m.group(1))


def test_lock_template_owned_dirs_match_exclusive_dirs():
    declared = set(_parse_template_list(deploy.HARNESS_LOCK_TEMPLATE, "dirs"))
    assert declared == deploy.EXCLUSIVE_HARNESS_DIRS, (
        f"lock template dirs drifted: "
        f"missing={deploy.EXCLUSIVE_HARNESS_DIRS - declared}, "
        f"phantom={declared - deploy.EXCLUSIVE_HARNESS_DIRS}"
    )


def test_lock_template_shared_dirs_match_shared_dirs():
    declared = set(_parse_template_list(deploy.HARNESS_LOCK_TEMPLATE, "shared_dirs"))
    assert declared == deploy.SHARED_DIRS


def test_lock_template_root_files_match_root_files():
    declared = set(_parse_template_list(deploy.HARNESS_LOCK_TEMPLATE, "files"))
    assert declared == set(deploy.ROOT_FILES), (
        f"lock template files drifted from ROOT_FILES: "
        f"missing={set(deploy.ROOT_FILES) - declared}, "
        f"phantom={declared - set(deploy.ROOT_FILES)}"
    )


def test_root_lock_version_matches_pyproject_fallback():
    """The committed lock must carry the version deploy will stamp.

    pyproject fallback-version was left at 4.1.0 while agent.yaml moved to
    4.2.0, so every deploy shipped a lock that garden.py immediately flagged
    as drift against agent.yaml.
    """
    lock = (deploy.ROOT / ".harness.lock").read_text(encoding="utf-8")
    lock_version = ""
    for line in lock.splitlines():
        s = line.strip()
        if s.startswith("version"):
            lock_version = s.split("=", 1)[1].strip().strip('"')
            break

    pyproject = (deploy.ROOT / "pyproject.toml").read_text(encoding="utf-8")
    fallback = ""
    for line in pyproject.splitlines():
        if line.strip().startswith("fallback-version"):
            fallback = line.split("=", 1)[1].strip().strip('"')
            break

    assert lock_version and fallback, "missing version in lock or pyproject"
    assert lock_version == fallback, (
        f"root .harness.lock version {lock_version!r} != pyproject "
        f"fallback-version {fallback!r}"
    )


def test_root_lock_dirs_cover_exclusive_dirs_plus_declared_local_only():
    """The committed lock's dirs must equal the exclusive set plus the
    explicitly-declared local-only dirs -- no more, no less.

    The root lock is a SUPERSET of what deploy ships (this repo IS the
    harness; a target receives a subset), so it is NOT required to match the
    template's dirs exactly. But the difference must be exactly the declared
    local-only set: an engine dir silently dropped when the lock is
    regenerated is the failure this catches, and an undeclared extra dir
    means someone added a boundary without saying so.
    """
    lock = (deploy.ROOT / ".harness.lock").read_text(encoding="utf-8")
    declared = set(_parse_template_list(lock, "dirs"))

    expected = deploy.EXCLUSIVE_HARNESS_DIRS | deploy.LOCAL_ONLY_HARNESS_DIRS

    assert declared == expected, (
        f"root .harness.lock dirs drifted from "
        f"EXCLUSIVE_HARNESS_DIRS | LOCAL_ONLY_HARNESS_DIRS: "
        f"missing={expected - declared}, phantom={declared - expected}"
    )


def test_root_lock_shared_files_cover_every_tracked_shared_file():
    """The root lock's shared list must cover every tracked file in shared dirs.

    Found the hard way on 2026-10-15: 26 tracked harness files (session_registry,
    session_control, win_sandbox, gui_server, subagent_cli, codex_delegate and
    their suites) were missing from the manifest, so anything reading it would
    treat real harness files as project code.
    """
    lock = (deploy.ROOT / ".harness.lock").read_text(encoding="utf-8")
    declared = _parse_lock_paths(lock)
    tracked = set(deploy._root_shared_paths())

    assert tracked - declared == set(), (
        f"root .harness.lock misses tracked harness files: {sorted(tracked - declared)}"
    )
    assert declared - tracked == set(), (
        f"root .harness.lock declares paths git does not track: "
        f"{sorted(declared - tracked)}"
    )


def test_root_lock_is_reproducible_from_the_generator(tmp_path):
    """Regenerating must reproduce the committed lock apart from `generated_at`.

    Regenerating through the deploy path instead of `root_mode` drops 39 boundaries
    (tools/deploy.py, garden.py, test_e2e.py, the test suites, .github/workflows/*),
    so this asserts the root path specifically. A file added under a shared dir and
    a lock left unrefreshed is the drift this fails on.
    """
    deploy._generate_harness_lock(tmp_path, root_mode=True)

    def body(path: Path) -> list[str]:
        return [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("generated_at")
        ]

    assert body(tmp_path / ".harness.lock") == body(deploy.ROOT / ".harness.lock")


def test_root_lock_flag_regenerates_this_repos_lock(monkeypatch):
    """`--root-lock` must go through root_mode, never the deploy path."""
    calls: dict = {}
    monkeypatch.setattr(
        deploy,
        "_generate_harness_lock",
        lambda target, dry_run=False, **kwargs: calls.update(
            target=target, dry_run=dry_run, **kwargs
        ),
    )
    monkeypatch.setattr(sys, "argv", ["deploy.py", "--root-lock"])

    assert deploy.main() == 0
    assert calls == {"target": deploy.ROOT, "dry_run": False, "root_mode": True}


def test_local_only_dirs_are_not_deployed():
    """A local-only harness dir must stay out of the cleanup-owns-everything
    set — otherwise a target's locally-created copy (e.g. the .agents/skills
    junction Antigravity makes) becomes eligible for full stale-cleanup."""
    overlap = deploy.LOCAL_ONLY_HARNESS_DIRS & deploy.EXCLUSIVE_HARNESS_DIRS
    assert not overlap, (
        f"local-only dirs must not be exclusive (cleanup would wipe them): {overlap}"
    )
    for d in deploy.LOCAL_ONLY_HARNESS_DIRS:
        assert d not in deploy.DIRS_ALL, (
            f"{d} is declared local-only but DIRS_ALL would deploy it"
        )


def test_harness_test_files_are_never_deployed(tmp_path):
    """tools/test_*.py test THIS repo, not the target project.

    These were excluded by a hand-maintained list, so every new test file
    leaked into deployed projects until someone remembered to add it --
    test_deploy.py and test_garden.py both did. Now excluded by rule.
    """
    test_files = sorted((deploy.ROOT / "tools").glob("test_*.py"))
    assert test_files, "expected harness test files to exist"
    for f in test_files:
        assert not deploy.should_copy(f), f"harness test file would deploy: {f.name}"


def test_generated_agent_files_are_declared(tmp_path):
    """.github/agents/*.agent.md are generated at deploy time.

    They exist in no source dir, so a manifest-derived boundary misses them
    and a model would read them as project code.
    """
    declared = set(deploy._shared_harness_paths(deploy.DIRS_ALL))
    agents = list((deploy.ROOT / ".copilot" / "agents").glob("*.md"))
    assert agents, "expected .copilot/agents/*.md to exist"
    for a in agents:
        rel = f".github/agents/{a.stem}.agent.md"
        assert rel in declared, f"generated agent file not declared as harness: {rel}"


# ─── boundary docs must not contradict the shared-dir reality ───────────────

BOUNDARY_DOCS = [
    "AGENTS.md",
    "CLAUDE.md",
    ".kilo/instruction/harness-boundaries.md",
    ".copilot/instruction/harness-boundaries.md",
    ".gemini/antigravity/instruction/harness-boundaries.md",
    ".gemini/antigravity/AGENTS.md",
    ".github/copilot-instructions.md",
]


def test_boundary_docs_flag_shared_dirs(tmp_path):
    """Every boundary doc that mentions a shared dir must call it shared.

    These docs are what a model reads to decide "is this file mine to
    change?". Claiming .github/ or tools/ is wholly harness invites it to
    ignore the project's own CI and dev scripts -- or to "clean up" harness
    files as if they were project code.
    """
    offenders = []
    for rel in BOUNDARY_DOCS:
        p = deploy.ROOT / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace").lower()
        if not any(d in text for d in ("`.github/", "`tools/", "`.vscode/")):
            continue
        if "shared" not in text and "dùng chung" not in text:
            offenders.append(rel)
    assert not offenders, (
        f"boundary docs mention shared dirs without marking them shared: {offenders}"
    )


# ─── live-deploy safety guard ───────────────────────────────────────────────
#
# deploy() writes ~485 files and removes retired harness files, with no
# backup. If the target has uncommitted work there is no clean `git checkout`
# to undo it, so a LIVE deploy there must be confirmed explicitly.

import subprocess  # noqa: E402


def _git_repo(tmp_path: Path, *, dirty: bool) -> Path:
    repo = tmp_path / ("dirty" if dirty else "clean")
    repo.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    import os

    env = {**os.environ, **env}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / "package.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "init"], check=True, env=env
    )
    if dirty:
        (repo / "package.json").write_text("{modified}\n", encoding="utf-8")
    return repo


def test_clean_repo_needs_no_confirmation(tmp_path):
    repo = _git_repo(tmp_path, dirty=False)
    assert deploy._uncommitted_changes(repo) == []
    assert deploy._confirm_unsafe_deploy(repo, assume_yes=False) is True


def test_dirty_repo_is_blocked_without_yes(tmp_path):
    repo = _git_repo(tmp_path, dirty=True)
    assert deploy._uncommitted_changes(repo), "expected a dirty tree"
    # Non-interactive (pytest has no TTY) must refuse rather than hang.
    assert deploy._confirm_unsafe_deploy(repo, assume_yes=False) is False


def test_dirty_repo_proceeds_with_yes(tmp_path):
    repo = _git_repo(tmp_path, dirty=True)
    assert deploy._confirm_unsafe_deploy(repo, assume_yes=True) is True


def test_non_git_target_is_blocked_without_yes(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert deploy._uncommitted_changes(plain) is None
    assert deploy._confirm_unsafe_deploy(plain, assume_yes=False) is False
    assert deploy._confirm_unsafe_deploy(plain, assume_yes=True) is True


def test_blocked_deploy_writes_nothing(tmp_path):
    """An aborted deploy must leave the target untouched."""
    repo = _git_repo(tmp_path, dirty=True)
    before = {p.name for p in repo.iterdir()}

    rc = deploy.deploy(str(repo), dry_run=False, assume_yes=False)

    assert rc == 1, "aborted deploy should exit non-zero"
    assert {p.name for p in repo.iterdir()} == before, "aborted deploy wrote files"
    assert not (repo / ".harness.lock").exists()


def test_dry_run_is_never_blocked(tmp_path):
    """--dry-run writes nothing, so it must not require confirmation."""
    repo = _git_repo(tmp_path, dirty=True)
    rc = deploy.deploy(str(repo), dry_run=True, assume_yes=False)
    assert rc == 0, "dry-run was blocked by the live-deploy guard"
    assert not (repo / ".harness.lock").exists()


# ??? upgrade note for a target that carries an earlier harness ???????????????


def test_upgrade_note_names_legacy_docs_and_deletes_nothing(tmp_path: Path) -> None:
    """Upgrading an old harness warns about its stale root docs and removes none of them.

    A target's SPEC.md and docs/ are classified as project-owned - a project may keep its own spec
    there - so cleanup may never delete them. An earlier harness that shipped them therefore leaves
    them behind, and the deploy owes the operator a sentence about it.
    """
    proj = tmp_path / "old-harness"
    (proj / "docs").mkdir(parents=True)
    _write(proj / "SPEC.md", "# Solo-Code-Harness - Specification (SPEC) v4.1.0\n")
    _write(proj / "docs" / "deepseek-harness-upgrade-review-plan.md", "old doc\n")

    result = subprocess.run(
        [sys.executable, str(deploy.__file__), "deploy", str(proj), "--yes"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert "from an earlier harness" in result.stdout, result.stdout[-800:]
    tail = result.stdout.split("from an earlier harness", 1)[1]
    assert "SPEC.md" in tail, "the note must name the file the operator has to look at"
    assert "docs/deepseek-harness-upgrade-review-plan.md" in tail
    assert (proj / "SPEC.md").is_file(), (
        "a project-owned root doc must survive an upgrade"
    )
    assert (proj / "docs" / "deepseek-harness-upgrade-review-plan.md").is_file()


def test_upgrade_note_ignores_a_projects_own_spec_md(tmp_path: Path) -> None:
    """A SPEC.md the project wrote is not evidence of an earlier harness.

    This is the line the first version got wrong: it triggered on the file's existence, so any
    target that happened to keep its own SPEC.md was told it carried harness debris.
    """
    proj = tmp_path / "own-spec"
    (proj / "docs").mkdir(parents=True)
    _write(
        proj / "SPEC.md",
        "# Acme Platform - Specification v1\n\nOwned by the project.\n",
    )
    _write(proj / "docs" / "architecture.md", "# Architecture\n")

    result = subprocess.run(
        [sys.executable, str(deploy.__file__), "deploy", str(proj), "--yes"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert "from an earlier harness" not in result.stdout, result.stdout[-800:]
    assert (proj / "SPEC.md").is_file(), "the project's own spec must survive untouched"


def test_upgrade_note_is_silent_for_a_fresh_target(tmp_path: Path) -> None:
    proj = tmp_path / "fresh"
    proj.mkdir()
    _write(proj / "package.json", "{}\n")

    result = subprocess.run(
        [sys.executable, str(deploy.__file__), "deploy", str(proj), "--yes"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert "from an earlier harness" not in result.stdout


# --- the deploy must not replace a project's .gitignore (measured on a real upgrade) ------------


def test_deploy_keeps_a_projects_own_gitignore_rules(tmp_path: Path) -> None:
    """A target's own .gitignore rules survive, and the harness rules are guaranteed present.

    The first live upgrade dropped five rules a project had written (*.log, logs/, one of its own
    source paths, *.exe, .kilo/worktrees/) because .gitignore is in ROOT_FILES and was copied over.
    A deploy cannot know which of a project's rules matter, so it now keeps the file and appends
    only what is missing.
    """
    proj = tmp_path / "has-gitignore"
    proj.mkdir()
    _write(
        proj / ".gitignore", "# project rules\nsource_code/only/here/\n*.log\n.venv/\n"
    )

    rc = deploy.deploy(str(proj), dry_run=False, assume_yes=True)
    assert rc == 0

    merged = (proj / ".gitignore").read_text(encoding="utf-8")
    assert "source_code/only/here/" in merged, "the project's own rule was dropped"
    assert "*.log" in merged
    assert "*.venv" not in merged
    # rules the harness depends on are now present even though the project never wrote them
    assert ".env" in merged
    assert ".solocode/" in merged
    assert "__pycache__/" in merged
    # and nothing was duplicated
    lines = [ln for ln in merged.splitlines() if ln.strip()]
    assert len(lines) == len(set(lines)), "the merge duplicated a rule"


def test_gitignore_merge_is_idempotent(tmp_path: Path) -> None:
    """A second deploy has nothing to add, so the file does not grow."""
    proj = tmp_path / "idempotent"
    proj.mkdir()
    _write(proj / ".gitignore", "my-own-rule/\n")

    assert deploy.deploy(str(proj), dry_run=False, assume_yes=True) == 0
    first = (proj / ".gitignore").read_bytes()
    assert deploy.deploy(str(proj), dry_run=False, assume_yes=True) == 0
    second = (proj / ".gitignore").read_bytes()

    assert first == second, "the second deploy changed .gitignore again"
    assert first.decode("utf-8").count("added by the Solo-Code Harness deploy") == 1


def test_fresh_target_gets_the_harness_gitignore_unchanged(tmp_path: Path) -> None:
    """No existing file means the harness version is written verbatim."""
    proj = tmp_path / "fresh"
    proj.mkdir()

    assert deploy.deploy(str(proj), dry_run=False, assume_yes=True) == 0

    assert (proj / ".gitignore").read_bytes() == (ROOT / ".gitignore").read_bytes()


def test_gitignore_merge_matches_the_files_line_endings(tmp_path: Path) -> None:
    """A CRLF file keeps CRLF in the appended block, so git does not see the whole file change."""
    proj = tmp_path / "crlf"
    proj.mkdir()
    (proj / ".gitignore").write_bytes(b"project-rule/\r\n")

    status = deploy._merge_list_file(
        ROOT / ".gitignore", proj / ".gitignore", dry_run=False, base=proj
    )

    assert "[MRG]" in status
    data = (proj / ".gitignore").read_bytes()
    appended = data[len(b"project-rule/\r\n") :]
    assert b"\r\n" in appended, "appended lines did not use the file's CRLF ending"
    assert b"\n" in appended


def test_gitignore_merge_ignores_comment_only_differences(tmp_path: Path) -> None:
    """Comments are documentation, not rules: a differently-dashed comment must not be re-appended.

    The project's copy of a harness comment used an em dash where the harness file uses a plain
    hyphen. Treating that as a missing rule made the merge append two comment lines - visible in a
    dry-run against a real target as "would append 2 missing harness rule(s)" - for no behavioural
    change at all.
    """
    proj = tmp_path / "comments"
    proj.mkdir()
    source = ROOT / ".gitignore"
    rules = [
        ln
        for ln in source.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    # every real rule is present, but the comments are the project's own wording
    _write(
        proj / ".gitignore",
        "# Reference repos — upstream projects, my own dash\n"
        + "\n".join(rules)
        + "\n",
    )

    status = deploy._merge_list_file(
        source, proj / ".gitignore", dry_run=False, base=proj
    )

    assert "[SKIP]" in status, status
    assert "added by the Solo-Code Harness deploy" not in (
        proj / ".gitignore"
    ).read_text(encoding="utf-8")


def test_gitignore_merge_still_appends_missing_rules(tmp_path: Path) -> None:
    """The comment rule must not swallow real rules: a missing rule is still appended."""
    proj = tmp_path / "missing-rule"
    proj.mkdir()
    _write(proj / ".gitignore", "only-this-rule/\n")

    status = deploy._merge_list_file(
        ROOT / ".gitignore", proj / ".gitignore", dry_run=False, base=proj
    )

    assert "[MRG]" in status
    merged = (proj / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in merged
    assert "only-this-rule/" in merged
    # no comment lines were appended, only the marker plus rules
    added = merged.split("added by the Solo-Code Harness deploy", 1)[1]
    assert not [ln for ln in added.splitlines() if ln.strip().startswith("#")], added


# --- structured root files a project owns are never replaced ---------------------------------


def test_project_owned_root_files_are_kept(tmp_path: Path) -> None:
    """A target's own pyproject.toml, Makefile, .ruff.toml, pre-commit config and ESLint config stay.

    Measured on a real upgrade: the deploy replaced them and the target lost its pytest testpaths and
    pythonpath (its own tests stopped being collected), a ruff exclusion and a pre-commit exclude.
    TOML, YAML, JS and Make cannot be merged line-wise the way .gitignore can, so they are kept and
    reported instead of overwritten.
    """
    proj = tmp_path / "owns-config"
    proj.mkdir()
    own = {
        "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["my_tests"]\n',
        "Makefile": "mine:\n\techo mine\n",
        ".ruff.toml": 'extend-exclude = ["my_dir"]\n',
        ".pre-commit-config.yaml": "repos: []\n",
        "eslint.config.js": "export default [];\n",
    }
    for name, body in own.items():
        _write(proj / name, body)

    rc = deploy.deploy(str(proj), dry_run=False, assume_yes=True)
    assert rc == 0

    for name, body in own.items():
        assert (proj / name).read_text(encoding="utf-8") == body, f"{name} was replaced"


def test_project_owned_root_files_are_written_when_absent(tmp_path: Path) -> None:
    """A fresh target still receives them, so a new project is not left half-configured."""
    proj = tmp_path / "fresh-config"
    proj.mkdir()

    assert deploy.deploy(str(proj), dry_run=False, assume_yes=True) == 0

    for name in (
        "pyproject.toml",
        ".ruff.toml",
        ".pre-commit-config.yaml",
        "Makefile",
        "eslint.config.js",
    ):
        assert (proj / name).is_file(), f"{name} missing on a fresh target"


def test_write_if_absent_reports_new_skip_and_keep(tmp_path: Path) -> None:
    src = ROOT / "Makefile"
    target = tmp_path / "statuses"
    target.mkdir()

    assert "[NEW]" in deploy._write_if_absent(
        src, target / "Makefile", dry_run=False, base=target
    )
    assert "[SKIP]" in deploy._write_if_absent(
        src, target / "Makefile", dry_run=False, base=target
    )

    _write(target / "Makefile", "different:\n\ttrue\n")
    status = deploy._write_if_absent(
        src, target / "Makefile", dry_run=False, base=target
    )
    assert "[KEEP]" in status, status
    assert (target / "Makefile").read_text(encoding="utf-8") == "different:\n\ttrue\n"


def test_write_if_absent_does_nothing_on_dry_run(tmp_path: Path) -> None:
    target = tmp_path / "dry"
    target.mkdir()
    status = deploy._write_if_absent(
        ROOT / "Makefile", target / "Makefile", dry_run=True, base=target
    )
    assert "[DRY]" in status
    assert not (target / "Makefile").exists()


def test_root_file_sets_are_disjoint_and_inside_the_manifest() -> None:
    """Guard the two policies: .gitignore is merged, structured files are kept, and both are shipped."""
    assert not (deploy.MERGE_ROOT_FILES & deploy.PROJECT_OWNED_ROOT_FILES)
    assert deploy.PROJECT_OWNED_ROOT_FILES.issubset(set(deploy.ROOT_FILES))
    assert deploy.MERGE_ROOT_FILES.issubset(set(deploy.ROOT_FILES))


# --- root docs are replaced, but the operator is told ------------------------------------------


def test_deploy_notices_root_docs_it_replaced(tmp_path: Path, capsys) -> None:
    """A rulebook must stay current, so it is replaced - but a project's additions are reported.

    AGENTS.md, CLAUDE.md and .env.template are not merged like .gitignore: a stale rulebook teaches
    the wrong rules. Replacing them silently, though, is how a project's own section disappears
    without anyone noticing until much later.
    """
    proj = tmp_path / "own-docs"
    proj.mkdir()
    _write(proj / "AGENTS.md", "# my rules\n\n## A section only this project has\n")
    _write(proj / ".env.template", "MY_OWN_KEY=\n")

    assert deploy.deploy(str(proj), dry_run=False, assume_yes=True) == 0

    out = capsys.readouterr().out
    assert "replaced root files" in out
    assert "AGENTS.md" in out
    assert ".env.template" in out
    assert (proj / "AGENTS.md").read_text(encoding="utf-8") == (
        ROOT / "AGENTS.md"
    ).read_text(encoding="utf-8"), (
        "the rulebook must be current, not the project's old copy"
    )


def test_deploy_stays_quiet_when_root_docs_were_absent(tmp_path: Path, capsys) -> None:
    """A fresh target had nothing to lose, so no notice is printed."""
    proj = tmp_path / "fresh-docs"
    proj.mkdir()

    assert deploy.deploy(str(proj), dry_run=False, assume_yes=True) == 0

    out = capsys.readouterr().out
    assert "replaced root files" not in out


# ─── harness content fingerprint ────────────────────────────────────────────


def test_harness_content_fingerprint_ignores_crlf_and_bom(tmp_path):
    """The same logical content must hash alike whether LF or CRLF plus a BOM."""
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    (lf / "tools").mkdir(parents=True)
    (crlf / "tools").mkdir(parents=True)
    (lf / "tools" / "a.py").write_bytes(b"line one\nline two\n")
    (crlf / "tools" / "a.py").write_bytes(b"\xef\xbb\xbfline one\r\nline two\r\n")

    assert deploy.harness_content_fingerprint(
        ["tools/a.py"], lf
    ) == deploy.harness_content_fingerprint(["tools/a.py"], crlf)


def test_harness_content_fingerprint_changes_with_one_byte(tmp_path):
    """A single changed byte must change the digest."""
    (tmp_path / "tools").mkdir(parents=True)
    target = tmp_path / "tools" / "a.py"
    target.write_bytes(b"x = 1\n")
    before = deploy.harness_content_fingerprint(["tools/a.py"], tmp_path)

    target.write_bytes(b"x = 2\n")
    after = deploy.harness_content_fingerprint(["tools/a.py"], tmp_path)

    assert before != after


def test_harness_content_fingerprint_rejects_a_missing_path(tmp_path):
    """A declared path that does not exist must raise, never be skipped."""
    try:
        deploy.harness_content_fingerprint(["tools/missing.py"], tmp_path)
    except RuntimeError as exc:
        assert "tools/missing.py" in str(exc)
    else:
        raise AssertionError("a missing declared path must raise RuntimeError")


def test_root_lock_records_a_content_fingerprint(tmp_path):
    """Every generated lock carries the digest of the files it declares."""
    deploy._generate_harness_lock(tmp_path, root_mode=True)

    text = (tmp_path / ".harness.lock").read_text(encoding="utf-8")
    assert 'content_fingerprint = "sha256:' in text


# ─── shared .harness.lock parser ────────────────────────────────────────────


def test_lock_shared_paths_keeps_a_comma_inside_a_quoted_path():
    """A comma inside a quoted path is literal, not a separator."""
    from tools.harness_fingerprint import lock_shared_paths

    text = '[shared_files]\npaths = [\n    "tools/a,b.py",\n]\n'

    assert lock_shared_paths(text) == ["tools/a,b.py"]


def test_lock_shared_paths_keeps_a_bracket_inside_a_quoted_path():
    """The list ends at the matching bracket, not the first one inside quotes."""
    from tools.harness_fingerprint import lock_shared_paths

    text = '[shared_files]\npaths = [\n    "tools/a]b.py",\n    "tools/c.py",\n]\n'

    assert lock_shared_paths(text) == ["tools/a]b.py", "tools/c.py"]


def test_lock_shared_paths_ignores_a_commented_out_block():
    """A commented-out ``paths = [`` ahead of the real one must not win."""
    from tools.harness_fingerprint import lock_shared_paths

    text = (
        "[shared_files]\n"
        "# paths = [\n"
        '#     "old.py",\n'
        "# ]\n"
        "paths = [\n"
        '    "real.py",\n'
        "]\n"
    )

    assert lock_shared_paths(text) == ["real.py"]


def test_lock_shared_paths_accepts_the_generator_trailing_comma():
    """deploy writes a trailing comma; the parser must accept its own writer."""
    from tools.harness_fingerprint import lock_shared_paths

    text = '[shared_files]\npaths = [\n    "tools/a.py",\n    "tools/b.py",\n]\n'

    assert lock_shared_paths(text) == ["tools/a.py", "tools/b.py"]


def test_lock_shared_paths_reports_a_duplicated_section_as_ambiguous():
    """Two [shared_files] sections cannot both be authoritative."""
    from tools.harness_fingerprint import lock_shared_paths

    text = '[shared_files]\npaths = ["a.py"]\n[shared_files]\npaths = ["b.py"]\n'

    assert lock_shared_paths(text) is None


def test_the_three_tools_share_one_fingerprint_implementation():
    """The digest and the lock parser exist once, not once per tool."""
    from tools import failure_ledger, garden, harness_fingerprint

    assert deploy.harness_content_fingerprint is harness_fingerprint.content_fingerprint
    assert garden.content_fingerprint is harness_fingerprint.content_fingerprint
    assert (
        failure_ledger.optional_fingerprint is harness_fingerprint.optional_fingerprint
    )
    assert failure_ledger.lock_value is harness_fingerprint.lock_value
    assert failure_ledger.lock_shared_paths is harness_fingerprint.lock_shared_paths


# ─── a target declares exactly what it shipped ──────────────────────────────


def test_non_copilot_target_deploy_declares_only_shipped_files(tmp_path, capsys):
    """A kilo deploy must not declare files only the copilot engine generates.

    ``_shared_harness_paths`` declares .github/agents/*.agent.md whenever
    .github is deployed, but those files are produced only for copilot/all.
    The fingerprint enforces existence, so an unfiltered declaration aborted a
    kilo deploy after the copy had already happened, leaving a half-deployed
    target with no lock.
    """
    from tools.harness_fingerprint import lock_shared_paths, lock_value

    target = tmp_path / "target"
    target.mkdir()

    assert deploy.deploy(str(target), engine="kilo", assume_yes=True) == 0
    capsys.readouterr()

    lock_path = target / ".harness.lock"
    assert lock_path.is_file(), "a successful deploy must write .harness.lock"
    text = lock_path.read_text(encoding="utf-8")

    declared = lock_shared_paths(text)
    assert declared is not None, "deploy wrote a lock its own reader rejects"
    assert declared, "a kilo deploy ships shared files, so none must be declared"
    assert lock_value(text, "harness", "mode") == "target"
    for path in declared:
        assert (target / path).is_file(), f"declared path was not shipped: {path}"
    assert not any(p.startswith(".github/agents/") for p in declared), (
        "kilo deploy declared copilot-generated agent files"
    )
    assert declared == sorted(declared)
    assert lock_value(text, "harness", "content_fingerprint") == (
        deploy.harness_content_fingerprint(declared, target)
    )


def test_empty_shared_set_round_trips_and_hashes_the_empty_input(tmp_path):
    """A lock with an empty shared set must parse as [] and verify as empty."""
    from tools.harness_fingerprint import lock_shared_paths, lock_value

    deploy._generate_harness_lock(tmp_path, dry_run=False, dirs=[])
    text = (tmp_path / ".harness.lock").read_text(encoding="utf-8")

    assert lock_shared_paths(text) == []
    assert lock_value(text, "harness", "content_fingerprint") == (
        deploy.harness_content_fingerprint([], tmp_path)
    )


def test_root_lock_generation_raises_when_a_declared_path_is_missing(
    tmp_path, monkeypatch
):
    """Root mode stays strict: a declared-but-absent path must raise."""
    monkeypatch.setattr(
        deploy, "_root_shared_paths", lambda root=None: ["tools/missing.py"]
    )
    try:
        deploy._generate_harness_lock(tmp_path, root_mode=True)
    except RuntimeError as exc:
        assert "tools/missing.py" in str(exc)
    else:
        raise AssertionError("root mode must raise on a missing declared path")
    assert not (tmp_path / ".harness.lock").exists()
