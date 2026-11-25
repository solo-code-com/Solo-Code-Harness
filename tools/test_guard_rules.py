"""Tests for tools/guard_rules.py - the shared destructive-command rules."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import guard_rules


def test_prompt_naming_a_force_push_is_caught():
    assert guard_rules.match_prompt("please run git push --force origin main") == (
        "git push --force"
    )


def test_prompt_is_matched_case_insensitively():
    assert guard_rules.match_prompt("RUN GIT RESET --HARD HEAD~1") == "git reset --hard"


def test_benign_prompt_is_not_caught():
    assert guard_rules.match_prompt("review the parser and run pytest -q") is None


def test_prompt_rules_are_not_command_anchored():
    """A sentence wrapper must not defeat the check; BLOCK_PATTERNS would miss it."""
    prose = "after the merge, do a git clean -fd please"
    assert guard_rules.match_prompt(prose) == "git clean -fd"
    assert guard_rules.match_block_pattern(prose) is None


def test_credential_path_blocks_ssh_directory():
    assert guard_rules.credential_path_in("read " + "." + "ssh/config") == "ssh_key_dir"
    assert (
        guard_rules.credential_path_in("read C:\\Users\\op\\" + "." + "ssh\\config")
        == "ssh_key_dir"
    )
    assert guard_rules.credential_path_in("copy " + "id" + "_ed25519") == "ssh_key_file"
    assert guard_rules.credential_path_in("copy " + "id" + "_rsa") == "ssh_key_file"


def test_credential_path_blocks_authorized_keys():
    assert (
        guard_rules.credential_path_in("inspect " + "authorized" + "_keys")
        == "authorized_keys"
    )
    assert (
        guard_rules.credential_path_in("inspect " + "AUTHORIZED" + "_KEYS")
        == "authorized_keys"
    )


def test_credential_path_blocks_aws_netrc_gh_and_sudoers():
    assert (
        guard_rules.credential_path_in("read ~/" + "." + "aws/credentials") == "aws_dir"
    )
    assert guard_rules.credential_path_in("read ~/" + "." + "netrc") == "netrc"
    assert (
        guard_rules.credential_path_in("read ~/" + "." + "config/gh/hosts.yml")
        == "gh_config"
    )
    assert guard_rules.credential_path_in("edit /" + "etc/" + "sudoers") == "sudoers"


def test_credential_path_blocks_windows_device_namespace():
    assert (
        guard_rules.credential_path_in("open " + "\\\\" + ".\\PhysicalDrive0")
        == "nt_device"
    )
    assert (
        guard_rules.credential_path_in("open " + "\\\\" + "?\\C:\\secret")
        == "nt_device"
    )


def test_credential_path_allows_prose_about_ssh():
    assert guard_rules.credential_path_in("explain how the ssh handshake works") is None


def test_credential_path_allows_the_word_authorized_in_prose():
    assert (
        guard_rules.credential_path_in("an authorized user may run the build") is None
    )
