#!/usr/bin/env python3
"""win_sandbox.py — Windows ACL restricted-token worker confinement.

Closes gap G5/P2.1 in ``plan.md``: the harness guard is only a regex gate plus
an ``executor-mode`` prompt/hook, so it can *detect* a destructive command but
cannot stop a worker process from writing outside the workspace. This module
adds the missing OS-level layer.

It is a stdlib-only (``ctypes``) port of the mechanism in DeepSeek Harness's
``dsh-sandbox-windows-acl`` (``packages/sandbox/sandbox-windows-acl``, a port of
github.com/huoyaoyuan/windows-acl-restrict-poc). The confinement shape is the
reference's:

  * a **``WRITE_RESTRICTED`` token** is minted from the current process token
    with a restricting-SID list ``[logon SID, Everyone, workspace SID, temp SID]``
    (``read-only`` keeps only the keep-alive group ``[logon SID, Everyone]``);
  * Windows then checks every write twice — once against the normal SIDs, once
    against the restricting SIDs — and grants write-class access only where
    both pass;
  * the **workspace SID** is derived deterministically from the canonical
    workspace path, and a Write ACE for it is materialized on the workspace
    tree; the **temp SID** is derived from a per-run private temp directory and
    its ACE is revoked on exit. The workspace ACE is left standing (the
    cross-run reuse cache), so a repeat provision is cheap via the exact-ACE
    skip.

Scope, stated honestly (the reference's documented boundaries apply):

  * **Writes are restricted; reads, network, and process visibility are not.**
    Egress is deliberately NOT restricted (decision 2026-11-24). The workers
    now use named providers, and every way to add network control on Windows
    costs more than it buys: a userland CONNECT proxy only binds tools that
    honour HTTP(S)_PROXY (a raw socket, PowerShell 5.1 Invoke-RestMethod and
    most native binaries ignore it), DLP cannot read the TLS tunnel it proxies,
    and a per-program firewall rule would have to block powershell.exe,
    node.exe and python.exe -- the workers themselves. Revisit only if a client
    requires network isolation in writing, or if WSL becomes the primary
    sandbox, where unshare -n is a kernel boundary rather than advice.
    ``WRITE_RESTRICTED`` intersects write accesses only.
  * **Everyone stays ambient.** It is required in the restricting list or early
    DLL initialization dies (``0xC0000142``) and CNG crashes PowerShell; an
    external object whose DACL grants Everyone write therefore stays writable
    (the documented ``partial`` boundary).
  * **Hard links alias the file object**, so an inheritable workspace ACE can be
    reached through another path to the same object.
  * The workspace and temp directories must be **caller-owned** (owner-implicit
    ``WRITE_DAC``) and grant the caller an explicit or inherited write ACE; the
    grant edits their DACLs. A protected DACL naming only the Owner Rights SID
    (``S-1-3-4``) is not sufficient — the restricted token's write pass-1 check
    does not honor Owner Rights alone (verified), so such a directory rejects
    every confined write.
  * Fail-closed by construction: any Win32 failure raises before a child spawns;
    a runner-side failure prints ``harness-acl-run: <detail>`` and exits 127.

Usage (argv-prefix runner — the seam the harness delegates can wrap):

    python tools/win_sandbox.py run \\
        --workspace <dir> --temp <dir> --mode workspace-write -- <program> [args...]

    python tools/win_sandbox.py probe          # availability, no host mutation
    python tools/win_sandbox.py run --self-test
    python tools/win_sandbox.py revert --workspace <dir>   # drop the standing ACE

The workspace ACE outlives the run on purpose (the reuse cache), so ``revert`` is
how a tree gets its DACL back once the sandbox is no longer wanted on it.

A worker usually needs its own state directory too (``~/.codex``, a cache dir,
…). Those sit outside the workspace and are denied, so grant each one with a
repeatable ``--also-write <dir>``. Such a grant is revoked on exit, unlike the
workspace ACE, because it is the caller's own directory rather than a target the
caller named as a workspace.

The delegate wrappers (``tools/*_delegate.py``) can be confined by prefixing
their own argv with ``run ... --``; the restricted token is inherited by every
descendant the worker spawns, so the whole worker subtree is write-confined.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Mode = Literal["read-only", "workspace-write"]

_WIN32 = "win32"
_RUNNER_SIGNATURE = "harness-acl-run"
_REVERT_SIGNATURE = "win_sandbox revert"
_RUNNER_FAILURE_EXIT = 127
# 2**30 - 1: the 30-bit subauthority ceiling the workspace capability shape uses.
_SID_CEILING = 2**30 - 1
# The SID header is Revision(1) + SubAuthorityCount(1) + IdentifierAuthority(6).
_SID_HEADER_SIZE = 8
# SECURITY_MAX_SID_SIZE allows at most 15 subauthorities after the header.
SID_MAX_SUB_AUTHORITIES = 15


class Win32Error(RuntimeError):
    """A checked Win32 call failed: it carries the API name, code, and context."""

    def __init__(self, api: str, code: int, detail: str) -> None:
        self.api = api
        self.code = code
        self.detail = detail
        super().__init__(f"{api} failed (Win32 {code}): {detail}")


class RunnerFailure(RuntimeError):  # noqa: N818 — mirrors the dsh runner's error name
    """A runner-side failure: the wrapped command must never run."""


# ─── Pure: path identity, boundaries, and the argv contract ─────────────────


def canonical_path(path: str) -> str:
    """Return the canonical spelling of a path (case and alias converged)."""
    return os.path.realpath(path)


def _norm(path: str) -> str:
    return os.path.normcase(canonical_path(path))


def contains_directory(root: str, candidate: str) -> bool:
    """Whether ``root`` is the same directory as ``candidate`` or contains it."""
    root_norm = _norm(root)
    candidate_norm = _norm(candidate)
    if root_norm == candidate_norm:
        return True
    try:
        rel = os.path.relpath(candidate_norm, root_norm)
    except ValueError:
        # Different drives on Windows: there is no relative path, so neither
        # directory can contain the other. Without this the F: repo + C: %TEMP%
        # layout — the common one — failed every provision with exit 127.
        return False
    return (
        rel != os.pardir
        and not rel.startswith(os.pardir + os.sep)
        and not os.path.isabs(rel)
    )


def _derive_sid(digest: bytes, domain: bool) -> str:
    first = (int.from_bytes(digest[0:4], "little") % _SID_CEILING) + 1
    second = (int.from_bytes(digest[4:8], "little") % _SID_CEILING) + 1
    tail = "-1" if domain else ""
    return f"S-1-4-{first}-{second}{tail}"


def workspace_write_sid(workspace_root: str) -> str:
    """The per-workspace capability SID (``S-1-4-x-y``), deterministic from the path."""
    digest = hashlib.sha256(canonical_path(workspace_root).encode("utf-8")).digest()
    return _derive_sid(digest, domain=False)


def temp_write_sid(temp_dir: str) -> str:
    """The per-temp-directory capability SID, domain-separated from workspace SIDs."""
    digest = hashlib.sha256(
        b"temp\x00" + canonical_path(temp_dir).encode("utf-8")
    ).digest()
    return _derive_sid(digest, domain=True)


def assert_temp_root_outside_workspace(workspace_root: str, temp_root: str) -> None:
    """Reject a temp directory inside the workspace (its children would inherit it)."""
    if contains_directory(workspace_root, temp_root):
        raise ValueError(
            f"temp directory must be outside the workspace: workspace={workspace_root}; temp={temp_root}"
        )


def assert_private_temp_disjoint(writable_dirs: list[str], temp_dir: str) -> None:
    """Reject overlap between the private temp directory and any writable root."""
    for writable in writable_dirs:
        if contains_directory(writable, temp_dir) or contains_directory(
            temp_dir, writable
        ):
            raise ValueError(
                f"private temp directory must be disjoint from writable dirs: "
                f"writable={writable}; temp={temp_dir}"
            )


def windows_command_line(argv: list[str]) -> str:
    """Join argv into a CommandLineToArgvW-compatible command line string."""
    return " ".join(_quote_arg(arg) for arg in argv)


def confined_argv(
    argv: list[str],
    workspace: str,
    temp: str,
    *,
    mode: Mode = "workspace-write",
    also_write: list[str] | None = None,
) -> list[str]:
    """Prefix ``argv`` with this module's runner, so the whole subtree is confined.

    This is the seam a delegate wrapper executes *instead of* ``argv``: the
    restricted token is inherited by every descendant the worker spawns.
    """
    prefix = [
        sys.executable,
        str(Path(__file__).resolve()),
        "run",
        "--workspace",
        workspace,
        "--temp",
        temp,
        "--mode",
        mode,
    ]
    for extra in also_write or []:
        prefix += ["--also-write", extra]
    return [*prefix, "--", *argv]


def confinement_available() -> bool:
    """Whether this host can confine at all — ``probe`` with no host mutation."""
    return bool(probe().get("available"))


def _quote_arg(arg: str) -> str:
    if arg == "":
        return '""'
    if not any(ch in arg for ch in ' \t\n\v"'):
        return arg
    out: list[str] = ['"']
    backslashes = 0
    for ch in arg:
        if ch == "\\":
            backslashes += 1
        elif ch == '"':
            out.append("\\" * (backslashes * 2 + 1))
            out.append('"')
            backslashes = 0
        else:
            out.append("\\" * backslashes)
            backslashes = 0
            out.append(ch)
    out.append("\\" * (backslashes * 2))
    out.append('"')
    return "".join(out)


@dataclass(frozen=True)
class RunnerArgs:
    """Parsed runner invocation: the plan plus the wrapped argv."""

    workspace: str
    temp: str
    mode: Mode
    argv: list[str]
    also_write: list[str] = field(default_factory=list)

    @property
    def seam_managed(self) -> bool:
        # This port always owns its DACLs; the reference's caller-owned-grant
        # flow (--write-sid/--temp-write-sid) is intentionally not exposed.
        return False


def parse_runner_argv(raw: list[str]) -> RunnerArgs:
    """Parse ``--workspace/--temp/--mode/--also-write -- <argv>``. Pure."""
    workspace: str | None = None
    temp: str | None = None
    mode: str | None = None
    also_write: list[str] = []
    index = 0
    while index < len(raw):
        argument = raw[index]
        if argument == "--":
            index += 1
            break
        if argument not in ("--workspace", "--temp", "--mode", "--also-write"):
            raise ValueError(f"unknown argument: {argument}")
        if index + 1 >= len(raw):
            raise ValueError(f"missing value after {argument}")
        value = raw[index + 1]
        if argument == "--workspace":
            workspace = value
        elif argument == "--temp":
            temp = value
        elif argument == "--also-write":
            also_write.append(value)
        else:
            mode = value
        index += 2
    if workspace is None:
        raise ValueError("missing --workspace")
    if temp is None:
        raise ValueError("missing --temp")
    if mode not in ("read-only", "workspace-write"):
        raise ValueError(f"unknown mode: {mode!r}")
    argv = raw[index:]
    if not argv:
        raise ValueError("missing command after --")
    return RunnerArgs(
        workspace=workspace,
        temp=temp,
        mode=mode,
        argv=list(argv),
        also_write=also_write,
    )


# ─── Win32 layer (ctypes) ────────────────────────────────────────────────────

DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
BOOL = ctypes.c_int
HANDLE = ctypes.c_void_p
LPCWSTR = ctypes.c_wchar_p
LPWSTR = ctypes.c_wchar_p

PROCESS_QUERY_INFORMATION = 0x0400
TOKEN_ASSIGN_PRIMARY = 0x0001
TOKEN_DUPLICATE = 0x0002
TOKEN_QUERY = 0x0008
TOKEN_ADJUST_DEFAULT = 0x0080
SE_GROUP_LOGON_ID = 0xC0000000
STANDARD_RIGHTS_WRITE = 0x00020000
FILE_GENERIC_WRITE = 0x00120116
DELETE = 0x00010000
FILE_DELETE_CHILD = 0x0040
GRANT_MASK = (FILE_GENERIC_WRITE | DELETE | FILE_DELETE_CHILD) & ~STANDARD_RIGHTS_WRITE
FILE_ALL_ACCESS = 0x1F01FF
DISABLE_MAX_PRIVILEGE = 0x1
LUA_TOKEN = 0x4
WRITE_RESTRICTED = 0x8
WIN_WORLD_SID = 1
TOKEN_GROUPS = 2
TOKEN_DEFAULT_DACL = 6
DACL_SECURITY_INFORMATION = 0x00000004
SE_FILE_OBJECT = 1
TRUSTEE_IS_UNKNOWN = 0
TRUSTEE_IS_SID = 0
NO_MULTIPLE_TRUSTEE = 0
GRANT_ACCESS = 1
REVOKE_ACCESS = 4
SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x3
SECURITY_MAX_SID_SIZE = 68
ERROR_SUCCESS = 0
ACCESS_ALLOWED_ACE_TYPE = 0
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF
CREATE_SUSPENDED = 0x4
STARTF_USESTDHANDLES = 0x00000100
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0
HANDLE_FLAG_INHERIT = 0x00000001
STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
SID_AND_ATTRIBUTES_SIZE = 16
TOKEN_GROUPS_OFFSET = 8
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class SID_AND_ATTRIBUTES(ctypes.Structure):  # noqa: N801 — mirrors the Win32 ABI name
    _fields_ = [("Sid", HANDLE), ("Attributes", DWORD)]


class TRUSTEE_W(ctypes.Structure):  # noqa: N801 — mirrors the Win32 ABI name
    _fields_ = [
        ("pMultipleTrustee", HANDLE),
        ("MultipleTrusteeOperation", DWORD),
        ("TrusteeForm", DWORD),
        ("TrusteeType", DWORD),
        ("ptstrName", HANDLE),
    ]


class EXPLICIT_ACCESS_W(ctypes.Structure):  # noqa: N801 — mirrors the Win32 ABI name
    _fields_ = [
        ("grfAccessPermissions", DWORD),
        ("grfAccessMode", DWORD),
        ("grfInheritance", DWORD),
        ("Trustee", TRUSTEE_W),
    ]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", DWORD),
        ("lpReserved", LPWSTR),
        ("lpDesktop", LPWSTR),
        ("lpTitle", LPWSTR),
        ("dwX", DWORD),
        ("dwY", DWORD),
        ("dwXSize", DWORD),
        ("dwYSize", DWORD),
        ("dwXCountChars", DWORD),
        ("dwYCountChars", DWORD),
        ("dwFillAttribute", DWORD),
        ("dwFlags", DWORD),
        ("wShowWindow", WORD),
        ("cbReserved2", WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", HANDLE),
        ("hStdOutput", HANDLE),
        ("hStdError", HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):  # noqa: N801 — mirrors the Win32 ABI name
    _fields_ = [
        ("hProcess", HANDLE),
        ("hThread", HANDLE),
        ("dwProcessId", DWORD),
        ("dwThreadId", DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):  # noqa: N801 — mirrors the Win32 ABI name
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801 — mirrors the Win32 ABI name
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", DWORD),
        ("SchedulingClass", DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801 — mirrors the Win32 ABI name
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class TOKEN_DEFAULT_DACL_INFO(ctypes.Structure):  # noqa: N801 — mirrors the Win32 ABI name
    _fields_ = [("DefaultDacl", HANDLE)]


def _bindings() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    """Resolve kernel32/advapi32 with full prototypes. Windows-only."""
    if sys.platform != _WIN32:
        raise RunnerFailure("Win32 ACL sandbox is only available on Windows")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)

    k32.GetCurrentProcess.restype = HANDLE
    k32.CloseHandle.argtypes = [HANDLE]
    k32.CloseHandle.restype = BOOL
    k32.LocalFree.argtypes = [HANDLE]
    k32.LocalFree.restype = HANDLE
    k32.LocalAlloc.argtypes = [DWORD, ctypes.c_size_t]
    k32.LocalAlloc.restype = HANDLE
    k32.GetStdHandle.argtypes = [DWORD]
    k32.GetStdHandle.restype = HANDLE
    k32.SetHandleInformation.argtypes = [HANDLE, DWORD, DWORD]
    k32.SetHandleInformation.restype = BOOL
    k32.SetConsoleCtrlHandler.argtypes = [HANDLE, BOOL]
    k32.SetConsoleCtrlHandler.restype = BOOL
    k32.CreateJobObjectW.argtypes = [HANDLE, LPCWSTR]
    k32.CreateJobObjectW.restype = HANDLE
    k32.SetInformationJobObject.argtypes = [HANDLE, ctypes.c_int, HANDLE, DWORD]
    k32.SetInformationJobObject.restype = BOOL
    k32.AssignProcessToJobObject.argtypes = [HANDLE, HANDLE]
    k32.AssignProcessToJobObject.restype = BOOL
    k32.ResumeThread.argtypes = [HANDLE]
    k32.ResumeThread.restype = DWORD
    k32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
    k32.WaitForSingleObject.restype = DWORD
    k32.GetExitCodeProcess.argtypes = [HANDLE, ctypes.POINTER(DWORD)]
    k32.GetExitCodeProcess.restype = BOOL
    k32.TerminateProcess.argtypes = [HANDLE, ctypes.c_uint32]
    k32.TerminateProcess.restype = BOOL

    adv.OpenProcessToken.argtypes = [HANDLE, DWORD, ctypes.POINTER(HANDLE)]
    adv.OpenProcessToken.restype = BOOL
    adv.CreateRestrictedToken.argtypes = [
        HANDLE,
        DWORD,
        DWORD,
        ctypes.POINTER(SID_AND_ATTRIBUTES),
        DWORD,
        HANDLE,
        DWORD,
        ctypes.POINTER(SID_AND_ATTRIBUTES),
        ctypes.POINTER(HANDLE),
    ]
    adv.CreateRestrictedToken.restype = BOOL
    adv.CreateProcessAsUserW.argtypes = [
        HANDLE,
        LPCWSTR,
        LPWSTR,
        HANDLE,
        HANDLE,
        BOOL,
        DWORD,
        HANDLE,
        LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    adv.CreateProcessAsUserW.restype = BOOL
    adv.GetTokenInformation.argtypes = [
        HANDLE,
        ctypes.c_int,
        HANDLE,
        DWORD,
        ctypes.POINTER(DWORD),
    ]
    adv.GetTokenInformation.restype = BOOL
    adv.SetTokenInformation.argtypes = [HANDLE, ctypes.c_int, HANDLE, DWORD]
    adv.SetTokenInformation.restype = BOOL
    adv.CreateWellKnownSid.argtypes = [
        ctypes.c_int,
        HANDLE,
        HANDLE,
        ctypes.POINTER(DWORD),
    ]
    adv.CreateWellKnownSid.restype = BOOL
    adv.CopySid.argtypes = [DWORD, HANDLE, HANDLE]
    adv.CopySid.restype = BOOL
    adv.GetLengthSid.argtypes = [HANDLE]
    adv.GetLengthSid.restype = DWORD
    adv.IsValidSid.argtypes = [HANDLE]
    adv.IsValidSid.restype = BOOL
    adv.EqualSid.argtypes = [HANDLE, HANDLE]
    adv.EqualSid.restype = BOOL
    adv.ConvertStringSidToSidW.argtypes = [LPCWSTR, ctypes.POINTER(HANDLE)]
    adv.ConvertStringSidToSidW.restype = BOOL
    adv.SetEntriesInAclW.argtypes = [
        ctypes.c_ulong,
        ctypes.POINTER(EXPLICIT_ACCESS_W),
        HANDLE,
        ctypes.POINTER(HANDLE),
    ]
    adv.SetEntriesInAclW.restype = DWORD
    adv.GetNamedSecurityInfoW.argtypes = [
        LPWSTR,
        ctypes.c_int,
        DWORD,
        ctypes.POINTER(HANDLE),
        ctypes.POINTER(HANDLE),
        ctypes.POINTER(HANDLE),
        ctypes.POINTER(HANDLE),
        ctypes.POINTER(HANDLE),
    ]
    adv.GetNamedSecurityInfoW.restype = DWORD
    adv.SetNamedSecurityInfoW.argtypes = [
        LPWSTR,
        ctypes.c_int,
        DWORD,
        HANDLE,
        HANDLE,
        HANDLE,
        HANDLE,
    ]
    adv.SetNamedSecurityInfoW.restype = DWORD
    return k32, adv


_K32: ctypes.WinDLL | None = None
_ADV: ctypes.WinDLL | None = None


def _api() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    global _K32, _ADV
    if _K32 is None or _ADV is None:
        _K32, _ADV = _bindings()
    return _K32, _ADV


def _last_error_k32(api: str, detail: str) -> Win32Error:
    return Win32Error(api, ctypes.get_last_error(), detail)


# ─── ACL grant / revoke ─────────────────────────────────────────────────────


def _read_current_dacl(
    k32: ctypes.WinDLL, adv: ctypes.WinDLL, path: str
) -> tuple[int | None, int | None]:
    owner = HANDLE()
    group = HANDLE()
    dacl = HANDLE()
    sacl = HANDLE()
    descriptor = HANDLE()
    rc = adv.GetNamedSecurityInfoW(
        path,
        SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        ctypes.byref(group),
        ctypes.byref(dacl),
        ctypes.byref(sacl),
        ctypes.byref(descriptor),
    )
    if rc != ERROR_SUCCESS:
        raise Win32Error("GetNamedSecurityInfoW", rc, path)
    return (dacl.value, descriptor.value)


def _read_pointer_at(buf: ctypes.Array[ctypes.c_char], offset: int) -> int | None:
    return ctypes.c_void_p.from_buffer(buf, offset).value


def _read_u16_at(buf: ctypes.Array[ctypes.c_char], offset: int) -> int:
    return ctypes.c_uint16.from_buffer(buf, offset).value


def _read_u32_at(buf: ctypes.Array[ctypes.c_char], offset: int) -> int:
    return ctypes.c_uint32.from_buffer(buf, offset).value


def _read_u8_at(buf: ctypes.Array[ctypes.c_char], offset: int) -> int:
    return ctypes.c_uint8.from_buffer(buf, offset).value


def _sid_equals(left: int, right: int) -> bool:
    """Compare two in-memory SIDs field-by-field without allocating strings.

    The ACE's SID is INLINE (embedded in the ACE after the 4-byte access mask),
    so an ACE-embedded SID is not a pointer and cannot be passed to EqualSid —
    a bogus address there crashes the API. Reading the bounded SID layout
    directly is the same comparison the reference performs (``sameSidAt``).
    """
    left_head = ctypes.string_at(left, _SID_HEADER_SIZE)
    right_head = ctypes.string_at(right, _SID_HEADER_SIZE)
    if left_head[0] != right_head[0]:
        return False
    count = left_head[1]
    if count != right_head[1] or count > SID_MAX_SUB_AUTHORITIES:
        return False
    if left_head[2:] != right_head[2:]:
        return False
    if count == 0:
        return True
    return ctypes.string_at(left + _SID_HEADER_SIZE, count * 4) == ctypes.string_at(
        right + _SID_HEADER_SIZE, count * 4
    )


def _has_exact_grant(old_acl: int, sid_ptr: int) -> bool:
    """Whether the explicit DACL already carries the exact grant ACE (skip re-propagate)."""
    header = (ctypes.c_char * 8).from_address(old_acl)
    acl_size = _read_u16_at(header, 2)
    ace_count = _read_u16_at(header, 4)
    if acl_size < 8 or acl_size > 1_048_576:
        return False
    block = (ctypes.c_char * acl_size).from_address(old_acl)
    offset = 8
    for _ in range(ace_count):
        ace_size = _read_u16_at(block, offset + 2)
        if ace_size < 8 or offset + ace_size > acl_size:
            return False
        if (
            _read_u8_at(block, offset) == ACCESS_ALLOWED_ACE_TYPE
            and _read_u8_at(block, offset + 1) == SUB_CONTAINERS_AND_OBJECTS_INHERIT
            and _read_u32_at(block, offset + 4) == GRANT_MASK
            # ACCESS_ALLOWED_ACE: header(4) + Mask(4); the inline SID follows at +8.
            and _sid_equals(old_acl + offset + 8, sid_ptr)
        ):
            return True
        offset += ace_size
    return False


def _build_explicit_access(
    sid_ptr: int, mode: int, permissions: int
) -> EXPLICIT_ACCESS_W:
    entry = EXPLICIT_ACCESS_W()
    entry.grfAccessPermissions = permissions
    entry.grfAccessMode = mode
    entry.grfInheritance = SUB_CONTAINERS_AND_OBJECTS_INHERIT
    entry.Trustee.pMultipleTrustee = None
    entry.Trustee.MultipleTrusteeOperation = NO_MULTIPLE_TRUSTEE
    entry.Trustee.TrusteeForm = TRUSTEE_IS_SID
    entry.Trustee.TrusteeType = TRUSTEE_IS_UNKNOWN
    entry.Trustee.ptstrName = sid_ptr
    return entry


def _merge_and_apply(
    k32: ctypes.WinDLL,
    adv: ctypes.WinDLL,
    path: str,
    entry: EXPLICIT_ACCESS_W,
    old_acl: int | None,
    descriptor: int | None,
    label: str,
) -> None:
    new_acl = HANDLE()
    rc = adv.SetEntriesInAclW(
        1,
        ctypes.byref(entry),
        ctypes.c_void_p(old_acl) if old_acl else None,
        ctypes.byref(new_acl),
    )
    if rc != ERROR_SUCCESS:
        if descriptor:
            k32.LocalFree(ctypes.c_void_p(descriptor))
        raise Win32Error("SetEntriesInAclW", rc, f"{label}({path})")
    if not new_acl.value:
        if descriptor:
            k32.LocalFree(ctypes.c_void_p(descriptor))
        raise Win32Error(
            "SetEntriesInAclW",
            ctypes.get_last_error(),
            f"{label}({path}): null new ACL",
        )
    # The descriptor block (old_acl included) is dead after the merge.
    if descriptor:
        k32.LocalFree(ctypes.c_void_p(descriptor))
    rc2 = adv.SetNamedSecurityInfoW(
        path, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, None, None, new_acl, None
    )
    k32.LocalFree(new_acl)
    if rc2 != ERROR_SUCCESS:
        raise Win32Error("SetNamedSecurityInfoW", rc2, f"{label}({path})")


def _parse_sid(adv: ctypes.WinDLL, sid_string: str) -> int:
    sid = HANDLE()
    if adv.ConvertStringSidToSidW(sid_string, ctypes.byref(sid)) == 0:
        raise _last_error_k32("ConvertStringSidToSidW", sid_string)
    if not sid.value:
        raise Win32Error(
            "ConvertStringSidToSidW",
            ctypes.get_last_error(),
            f"null SID for {sid_string}",
        )
    return sid.value


def _last_error_k32(api: str, detail: str) -> Win32Error:
    return Win32Error(api, ctypes.get_last_error(), detail)


def _local_alloc(k32: ctypes.WinDLL, size: int) -> int:
    """Allocate ``size`` bytes with LocalAlloc (LMEM_FIXED) so LocalFree owns it.

    SIDs are freed with LocalFree everywhere in this module, so any buffer that
    outlives its creating function must come from the same allocator — a Python
    ``create_string_buffer`` freed with LocalFree corrupts the heap.
    """
    memory = k32.LocalAlloc(0, size)
    if not memory:
        raise _last_error_k32("LocalAlloc", f"{size} bytes")
    return memory


@contextmanager
def _path_lock(path: str):
    """Serialise the get-merge-set sequence per path across processes."""
    import msvcrt
    import tempfile

    digest = hashlib.sha256(
        os.path.normcase(canonical_path(path)).encode("utf-8")
    ).hexdigest()[:16]
    lock_dir = os.path.join(tempfile.gettempdir(), "harness-acl-locks")
    os.makedirs(lock_dir, exist_ok=True)
    lock_path = os.path.join(lock_dir, f"{digest}.lock")
    handle = open(lock_path, "a+b")  # noqa: SIM115 — held across the yielded block
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


def grant_write(path: str, sid_ptr: int) -> None:
    """Grant ``GRANT_MASK`` (Write+Delete, inheriting) to ``sid_ptr`` on ``path``."""
    k32, adv = _api()
    with _path_lock(path):
        old_acl, descriptor = _read_current_dacl(k32, adv, path)
        if old_acl and _has_exact_grant(old_acl, sid_ptr):
            if descriptor:
                k32.LocalFree(ctypes.c_void_p(descriptor))
            return
        entry = _build_explicit_access(sid_ptr, GRANT_ACCESS, GRANT_MASK)
        _merge_and_apply(k32, adv, path, entry, old_acl, descriptor, "grantWrite")


def revoke_write(path: str, sid_ptr: int) -> bool:
    """Remove every ACE for ``sid_ptr`` from ``path``. False when it has no DACL."""
    k32, adv = _api()
    with _path_lock(path):
        old_acl, descriptor = _read_current_dacl(k32, adv, path)
        if not old_acl:
            if descriptor:
                k32.LocalFree(ctypes.c_void_p(descriptor))
            return False
        entry = _build_explicit_access(sid_ptr, REVOKE_ACCESS, 0)
        _merge_and_apply(k32, adv, path, entry, old_acl, descriptor, "revokeWrite")
        return True


def revoke_grant(path: str, sid_string: str) -> bool:
    """Drop the capability ACE for ``sid_string``; False when the tree has none.

    A bare ``revoke_write`` cannot answer that question — SetEntriesInAclW
    accepts a REVOKE entry that matches nothing and still rewrites the DACL — so
    the check reads the DACL first. Callers need the distinction to tell
    "already clean" from "just cleaned".
    """
    k32, adv = _api()
    sid_ptr = _parse_sid(adv, sid_string)
    try:
        old_acl, descriptor = _read_current_dacl(k32, adv, path)
        try:
            if not old_acl or not _has_exact_grant(old_acl, sid_ptr):
                return False
        finally:
            if descriptor:
                k32.LocalFree(ctypes.c_void_p(descriptor))
        return revoke_write(path, sid_ptr)
    finally:
        k32.LocalFree(ctypes.c_void_p(sid_ptr))


# ─── Restricted token ───────────────────────────────────────────────────────


def _open_current_token(k32: ctypes.WinDLL, adv: ctypes.WinDLL) -> int:
    process = k32.GetCurrentProcess()
    token = HANDLE()
    rights = TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ADJUST_DEFAULT | TOKEN_ASSIGN_PRIMARY
    if adv.OpenProcessToken(process, rights, ctypes.byref(token)) == 0:
        raise _last_error_k32("OpenProcessToken", f"pid {os.getpid()}")
    if not token.value:
        raise Win32Error(
            "OpenProcessToken", ctypes.get_last_error(), "null token handle"
        )
    return token.value


def _find_logon_sid(k32: ctypes.WinDLL, adv: ctypes.WinDLL, token: int) -> int:
    needed = DWORD()
    adv.GetTokenInformation(token, TOKEN_GROUPS, None, 0, ctypes.byref(needed))
    size = needed.value
    if size < TOKEN_GROUPS_OFFSET:
        raise _last_error_k32("GetTokenInformation", "TokenGroups size")
    buffer = ctypes.create_string_buffer(size)
    if (
        adv.GetTokenInformation(
            token, TOKEN_GROUPS, ctypes.cast(buffer, HANDLE), size, ctypes.byref(needed)
        )
        == 0
    ):
        raise _last_error_k32("GetTokenInformation", "TokenGroups")
    count = _read_u32_at(buffer, 0)
    for index in range(count):
        base = TOKEN_GROUPS_OFFSET + index * SID_AND_ATTRIBUTES_SIZE
        sid_addr = _read_pointer_at(buffer, base)
        attributes = _read_u32_at(buffer, base + 8)
        if not sid_addr or (attributes & SE_GROUP_LOGON_ID) != SE_GROUP_LOGON_ID:
            continue
        length = adv.GetLengthSid(ctypes.c_void_p(sid_addr))
        if length == 0:
            raise _last_error_k32("GetLengthSid", f"logon SID group {index}")
        copy = _local_alloc(k32, length)
        if adv.CopySid(length, ctypes.c_void_p(copy), ctypes.c_void_p(sid_addr)) == 0:
            k32.LocalFree(ctypes.c_void_p(copy))
            raise _last_error_k32("CopySid", f"logon SID group {index}")
        return copy
    raise RunnerFailure(f"no logon SID found among {count} token groups")


def _make_well_known_sid(k32: ctypes.WinDLL, adv: ctypes.WinDLL, sid_type: int) -> int:
    sid = _local_alloc(k32, SECURITY_MAX_SID_SIZE)
    size = DWORD(SECURITY_MAX_SID_SIZE)
    if (
        adv.CreateWellKnownSid(sid_type, None, ctypes.c_void_p(sid), ctypes.byref(size))
        == 0
    ):
        k32.LocalFree(ctypes.c_void_p(sid))
        raise _last_error_k32("CreateWellKnownSid", f"type {sid_type}")
    if adv.IsValidSid(ctypes.c_void_p(sid)) == 0:
        k32.LocalFree(ctypes.c_void_p(sid))
        raise _last_error_k32("IsValidSid", f"CreateWellKnownSid type {sid_type}")
    return sid


def _sid_array(sid_ptrs: list[int]) -> ctypes.Array[SID_AND_ATTRIBUTES]:
    array = (SID_AND_ATTRIBUTES * len(sid_ptrs))()
    for index, pointer in enumerate(sid_ptrs):
        array[index].Sid = pointer
        array[index].Attributes = 0
    return array


def _create_restricted_token(
    k32: ctypes.WinDLL,
    adv: ctypes.WinDLL,
    current_token: int,
    logon_sid: int,
    world_sid: int,
    write_sids: list[int],
    mode: Mode,
) -> int:
    if mode == "read-only":
        restricting = [logon_sid, world_sid]
    else:
        if not write_sids:
            raise RunnerFailure(
                "workspace-write restricting list requires at least one write SID"
            )
        restricting = [logon_sid, world_sid, *write_sids]
    array = _sid_array(restricting)
    new_token = HANDLE()
    flags = DISABLE_MAX_PRIVILEGE | LUA_TOKEN | WRITE_RESTRICTED
    created = adv.CreateRestrictedToken(
        current_token,
        flags,
        0,
        None,
        0,
        None,
        len(restricting),
        array,
        ctypes.byref(new_token),
    )
    if created == 0:
        raise _last_error_k32(
            "CreateRestrictedToken", f"restricting SIDs: {len(restricting)}"
        )
    if not new_token.value:
        raise Win32Error(
            "CreateRestrictedToken", ctypes.get_last_error(), "null token handle"
        )
    return new_token.value


def _set_default_dacl_grant(
    k32: ctypes.WinDLL, adv: ctypes.WinDLL, token: int, sid_ptr: int
) -> None:
    needed = DWORD()
    adv.GetTokenInformation(token, TOKEN_DEFAULT_DACL, None, 0, ctypes.byref(needed))
    size = needed.value
    if size == 0:
        raise _last_error_k32("GetTokenInformation", "TokenDefaultDacl size")
    buffer = ctypes.create_string_buffer(size)
    if (
        adv.GetTokenInformation(
            token,
            TOKEN_DEFAULT_DACL,
            ctypes.cast(buffer, HANDLE),
            size,
            ctypes.byref(needed),
        )
        == 0
    ):
        raise _last_error_k32("GetTokenInformation", "TokenDefaultDacl")
    current_dacl = _read_pointer_at(buffer, 0)
    if not current_dacl:
        raise RunnerFailure("token carries no default DACL to extend")
    entry = _build_explicit_access(sid_ptr, GRANT_ACCESS, FILE_ALL_ACCESS)
    new_dacl = HANDLE()
    rc = adv.SetEntriesInAclW(
        1, ctypes.byref(entry), ctypes.c_void_p(current_dacl), ctypes.byref(new_dacl)
    )
    if rc != ERROR_SUCCESS:
        raise Win32Error("SetEntriesInAclW", rc, "default DACL merge")
    if not new_dacl.value:
        raise Win32Error(
            "SetEntriesInAclW", ctypes.get_last_error(), "null merged default DACL"
        )
    info = TOKEN_DEFAULT_DACL_INFO()
    info.DefaultDacl = new_dacl
    if (
        adv.SetTokenInformation(
            token, TOKEN_DEFAULT_DACL, ctypes.byref(info), ctypes.sizeof(info)
        )
        == 0
    ):
        code = ctypes.get_last_error()
        k32.LocalFree(new_dacl)
        raise Win32Error("SetTokenInformation", code, "TokenDefaultDacl")
    k32.LocalFree(new_dacl)


# ─── Sandbox instance ───────────────────────────────────────────────────────


@dataclass
class SpawnResult:
    """A settled confined child: its pid and exit code (stdio is inherited)."""

    pid: int
    exit_code: int


@dataclass
class WinSandbox:
    """One write-restricted sandbox: token + capability-SID grants + spawn.

    ``init()`` is fail-closed — any Win32 failure revokes the revocable grants and
    raises, so a child is never spawned unrestricted. ``dispose()`` revokes the
    temp and ``also_write`` grants, frees the SIDs, and closes the token; the
    standing workspace ACE stays as the cross-run reuse cache.
    """

    mode: Mode
    workspace_root: str
    temp_dir: str | None = None
    also_write: list[str] = field(default_factory=list)
    _k32: ctypes.WinDLL | None = field(default=None, repr=False)
    _adv: ctypes.WinDLL | None = field(default=None, repr=False)
    _token: int | None = field(default=None, repr=False)
    _workspace_sid: int | None = field(default=None, repr=False)
    _temp_sid: int | None = field(default=None, repr=False)
    _extra_grants: list[tuple[str, int]] = field(default_factory=list, repr=False)
    _allocations: list[int] = field(default_factory=list, repr=False)
    _temp_granted: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        root = canonical_path(self.workspace_root)
        if not os.path.isdir(root):
            raise ValueError(f"workspace does not exist or is not a directory: {root}")
        self.workspace_root = root
        if self.mode == "workspace-write":
            if not self.temp_dir:
                raise ValueError("workspace-write requires a private temp directory")
            temp = canonical_path(self.temp_dir)
            if not os.path.isdir(temp):
                raise ValueError(f"temp does not exist or is not a directory: {temp}")
            assert_temp_root_outside_workspace(root, temp)
            assert_private_temp_disjoint([root], temp)
            self.temp_dir = temp
        elif self.temp_dir:
            raise ValueError("read-only does not accept a temp directory")
        if self.also_write and self.mode != "workspace-write":
            raise ValueError(
                "--also-write needs workspace-write: read-only denies writes"
            )
        roots: list[str] = []
        for extra in self.also_write:
            path = canonical_path(extra)
            if not os.path.isdir(path):
                raise ValueError(f"--also-write is not a directory: {path}")
            roots.append(path)
        self.also_write = roots
        if roots and self.temp_dir:
            assert_private_temp_disjoint(roots, self.temp_dir)

    def init(self) -> None:
        """Create the restricted token and apply the capability-SID grants."""
        if self._token is not None:
            raise RuntimeError("WinSandbox is already initialized")
        k32, adv = _api()
        self._k32, self._adv = k32, adv
        current_token = _open_current_token(k32, adv)
        current_open = True
        restricted: int | None = None
        try:
            if self.mode == "workspace-write":
                self._workspace_sid = _parse_sid(
                    adv, workspace_write_sid(self.workspace_root)
                )
                assert self.temp_dir is not None
                self._temp_sid = _parse_sid(adv, temp_write_sid(self.temp_dir))
                grant_write(self.workspace_root, self._workspace_sid)
                grant_write(self.temp_dir, self._temp_sid)
                self._temp_granted = True
                # Extra roots are granted and then revoked on dispose, unlike the
                # workspace cache: they are usually the caller's own state dirs,
                # and a confinement layer should not leave a DACL edit behind on a
                # tree the caller never named as a workspace.
                for extra in self.also_write:
                    extra_sid = _parse_sid(adv, workspace_write_sid(extra))
                    self._extra_grants.append((extra, extra_sid))
                    grant_write(extra, extra_sid)
            logon_sid = _find_logon_sid(k32, adv, current_token)
            self._allocations.append(logon_sid)
            world_sid = _make_well_known_sid(k32, adv, WIN_WORLD_SID)
            self._allocations.append(world_sid)
            write_sids = [
                sid
                for sid in (
                    self._workspace_sid,
                    self._temp_sid,
                    *[extra_sid for _, extra_sid in self._extra_grants],
                )
                if sid
            ]
            restricted = _create_restricted_token(
                k32, adv, current_token, logon_sid, world_sid, write_sids, self.mode
            )
            default_sid = self._temp_sid or self._workspace_sid or world_sid
            _set_default_dacl_grant(k32, adv, restricted, default_sid)
            k32.CloseHandle(ctypes.c_void_p(current_token))
            current_open = False
            self._token = restricted
        except Exception:
            if current_open:
                k32.CloseHandle(ctypes.c_void_p(current_token))
            if restricted is not None:
                k32.CloseHandle(ctypes.c_void_p(restricted))
            self._cleanup_grants()
            self._token = None
            raise

    def _cleanup_grants(self) -> None:
        k32 = self._k32
        if self._temp_granted and self.temp_dir and self._temp_sid and k32 is not None:
            with suppress(OSError):
                revoke_write(self.temp_dir, self._temp_sid)
            self._temp_granted = False
        for path, sid in self._extra_grants:
            with suppress(OSError):
                revoke_write(path, sid)
            if k32 is not None:
                k32.LocalFree(ctypes.c_void_p(sid))
        self._extra_grants = []
        if k32 is not None:
            for sid in (self._workspace_sid, self._temp_sid):
                if sid:
                    k32.LocalFree(ctypes.c_void_p(sid))
        self._workspace_sid = None
        self._temp_sid = None

    def spawn(self, argv: list[str], cwd: str | None = None) -> SpawnResult:
        """Run ``argv`` under the restricted token with inherited stdio."""
        if self._token is None or self._k32 is None or self._adv is None:
            raise RuntimeError("WinSandbox is not initialized: call init() first")
        k32, adv = self._k32, self._adv
        cwd = canonical_path(cwd or os.getcwd())
        command_line = ctypes.create_unicode_buffer(windows_command_line(argv))
        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(STARTUPINFOW)
        startup.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW
        startup.wShowWindow = SW_HIDE
        startup.hStdInput = k32.GetStdHandle(STD_INPUT_HANDLE)
        startup.hStdOutput = k32.GetStdHandle(STD_OUTPUT_HANDLE)
        startup.hStdError = k32.GetStdHandle(STD_ERROR_HANDLE)
        for handle in (startup.hStdInput, startup.hStdOutput, startup.hStdError):
            if handle:
                k32.SetHandleInformation(
                    handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT
                )
        info = PROCESS_INFORMATION()
        created = adv.CreateProcessAsUserW(
            ctypes.c_void_p(self._token),
            None,
            command_line,
            None,
            None,
            True,
            CREATE_SUSPENDED,
            None,
            cwd,
            ctypes.byref(startup),
            ctypes.byref(info),
        )
        if created == 0:
            raise _last_error_k32(
                "CreateProcessAsUserW", f"command: {argv[0]}, cwd: {cwd}"
            )
        job = 0
        try:
            job, error = self._create_job(k32, info)
            if error is not None:
                job = 0
                raise error
            if k32.AssignProcessToJobObject(job, info.hProcess) == 0:
                raise _last_error_k32(
                    "AssignProcessToJobObject", f"pid {info.dwProcessId}"
                )
            if k32.ResumeThread(info.hThread) == INFINITE:
                raise _last_error_k32("ResumeThread", f"pid {info.dwProcessId}")
            k32.CloseHandle(info.hThread)
            code = self._wait(k32, info.hProcess)
            k32.CloseHandle(info.hProcess)
            k32.CloseHandle(job)
            return SpawnResult(pid=info.dwProcessId, exit_code=code)
        except Exception:
            if job:
                k32.CloseHandle(job)
            k32.TerminateProcess(info.hProcess, 1)
            k32.CloseHandle(info.hThread)
            k32.CloseHandle(info.hProcess)
            raise

    @staticmethod
    def _create_job(
        k32: ctypes.WinDLL, info: PROCESS_INFORMATION
    ) -> tuple[int, Win32Error | None]:
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return 0, _last_error_k32("CreateJobObjectW", f"pid {info.dwProcessId}")
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(
            job,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if ok == 0:
            k32.CloseHandle(job)
            return 0, _last_error_k32(
                "SetInformationJobObject", f"pid {info.dwProcessId}"
            )
        return job, None

    @staticmethod
    def _wait(k32: ctypes.WinDLL, process: int) -> int:
        if k32.WaitForSingleObject(process, INFINITE) == WAIT_FAILED:
            raise _last_error_k32("WaitForSingleObject", "child")
        code = DWORD()
        if k32.GetExitCodeProcess(process, ctypes.byref(code)) == 0:
            raise _last_error_k32("GetExitCodeProcess", "child")
        return int(code.value)

    def dispose(self) -> None:
        """Revoke the temp grant, free SIDs, close the token. Standing ACEs stay."""
        k32 = self._k32
        self._cleanup_grants()
        if k32 is not None and self._token is not None:
            k32.CloseHandle(ctypes.c_void_p(self._token))
        if k32 is not None:
            for sid in self._allocations:
                k32.LocalFree(ctypes.c_void_p(sid))
        self._allocations = []
        self._token = None


# ─── Availability probe ─────────────────────────────────────────────────────


def probe() -> dict[str, object]:
    """Report whether the sandbox can start here. No workspace/DACL mutation."""
    if sys.platform != _WIN32:
        return {"available": False, "reason": "not Windows"}
    try:
        k32, adv = _api()
        # Creating a read-only token exercises the token machinery without any
        # ACL grant: read-only has no capability SIDs.
        current = _open_current_token(k32, adv)
        try:
            logon = _find_logon_sid(k32, adv, current)
            try:
                world = _make_well_known_sid(k32, adv, WIN_WORLD_SID)
                try:
                    token = _create_restricted_token(
                        k32, adv, current, logon, world, [], "read-only"
                    )
                    k32.CloseHandle(ctypes.c_void_p(token))
                finally:
                    k32.LocalFree(ctypes.c_void_p(world))
            finally:
                k32.LocalFree(ctypes.c_void_p(logon))
        finally:
            k32.CloseHandle(ctypes.c_void_p(current))
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise _last_error_k32("CreateJobObjectW", "probe")
        k32.CloseHandle(job)
    except (Win32Error, RunnerFailure, OSError) as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, "mode": "restricted-token", "enforcement": "partial"}


# ─── Runner ─────────────────────────────────────────────────────────────────


def _fail(detail: str) -> None:
    sys.stderr.write(f"{_RUNNER_SIGNATURE}: {detail}\n")
    raise RunnerFailure(detail)


def run_confined(raw_argv: list[str]) -> int:
    """Runner entry: confine and execute the wrapped argv, mirroring its exit code."""
    try:
        parsed = parse_runner_argv(raw_argv)
    except ValueError as exc:
        _fail(str(exc))
    assert parsed is not None
    if not os.path.isdir(parsed.workspace):
        _fail(f"--workspace is not an existing directory: {parsed.workspace}")
    if not os.path.isdir(parsed.temp):
        _fail(f"--temp is not an existing directory: {parsed.temp}")

    sandbox = WinSandbox(
        mode=parsed.mode,
        workspace_root=parsed.workspace,
        temp_dir=parsed.temp if parsed.mode == "workspace-write" else None,
        also_write=list(parsed.also_write),
    )
    initialized = False
    try:
        sandbox.init()
        initialized = True
        previous: dict[str, str | None] = {}
        if parsed.mode == "workspace-write" and sandbox.temp_dir:
            for key in ("TMP", "TEMP"):
                previous[key] = os.environ.get(key)
                os.environ[key] = sandbox.temp_dir
        try:
            result = sandbox.spawn(parsed.argv, cwd=parsed.workspace)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return result.exit_code
    finally:
        if initialized:
            try:
                sandbox.dispose()
            except OSError as exc:
                sys.stderr.write(f"{_RUNNER_SIGNATURE}: cleanup: {exc}\n")
        else:
            sandbox.dispose()


def _revert(
    workspace: str | None, temp: str | None, also_write: list[str] | None = None
) -> int:
    """Drop the capability ACEs a previous run left on these trees.

    Prints JSON and exits 0; any failure prints to stderr and exits 1. The temp
    and ``also_write`` ACEs are normally revoked by ``dispose()``, so a leftover
    grant means a run died without cleanup — this cleans that up too.
    """
    scopes: list[tuple[str, str, str]] = []
    if workspace:
        scopes.append(("workspace", workspace, workspace_write_sid(workspace)))
    if temp:
        scopes.append(("temp", temp, temp_write_sid(temp)))
    for extra in also_write or []:
        scopes.append((extra, extra, workspace_write_sid(extra)))
    if not scopes:
        sys.stderr.write(
            f"{_REVERT_SIGNATURE}: pass --workspace, --temp and/or --also-write\n"
        )
        return 1

    report: dict[str, dict[str, object]] = {}
    try:
        for label, path, sid_string in scopes:
            if not os.path.isdir(path):
                raise RunnerFailure(f"--{label} is not an existing directory: {path}")
            report[label] = {
                "path": canonical_path(path),
                "sid": sid_string,
                "removed": revoke_grant(path, sid_string),
            }
    except (Win32Error, RunnerFailure, OSError, ValueError) as exc:
        sys.stderr.write(f"{_REVERT_SIGNATURE}: {exc}\n")
        return 1
    print(json.dumps(report, indent=2))
    return 0


def _self_test() -> int:
    """Pure self-test that runs on any platform (no Win32 calls)."""
    assert (
        parse_runner_argv(
            [
                "--workspace",
                "w",
                "--temp",
                "t",
                "--mode",
                "read-only",
                "--",
                "cmd",
                "/c",
                "x",
            ]
        ).mode
        == "read-only"
    )
    assert workspace_write_sid("C:/a") == workspace_write_sid("C:/a")
    assert workspace_write_sid("C:/a") != workspace_write_sid("C:/b")
    assert (
        temp_write_sid("C:/t").count("-") == workspace_write_sid("C:/a").count("-") + 1
    )
    assert windows_command_line(["a b", 'c"d']) == '"a b" "c\\"d"'
    print("win_sandbox self-test passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Windows ACL worker confinement")
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="confine and execute a command")
    run.add_argument("--workspace", required=True)
    run.add_argument("--temp", required=True)
    run.add_argument(
        "--mode", choices=["read-only", "workspace-write"], default="workspace-write"
    )
    run.add_argument("argv", nargs=argparse.REMAINDER)
    run.add_argument(
        "--also-write",
        action="append",
        default=[],
        help="extra writable root (repeatable); its ACE is revoked on exit",
    )
    sub.add_parser("probe", help="report availability without touching the host")
    revert = sub.add_parser("revert", help="drop a capability ACE left on a tree")
    revert.add_argument(
        "--workspace", help="workspace root to take the standing ACE off"
    )
    revert.add_argument("--temp", help="private temp directory to take the ACE off")
    revert.add_argument(
        "--also-write",
        action="append",
        default=[],
        help="extra writable root to take the ACE off (repeatable)",
    )

    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.command == "probe":
        print(json.dumps(probe(), indent=2))
        return 0
    if args.command == "revert":
        return _revert(args.workspace, args.temp, args.also_write)
    if args.command == "run":
        tail = list(args.argv)
        if tail and tail[0] == "--":
            tail = tail[1:]
        raw = [
            "--workspace",
            args.workspace,
            "--temp",
            args.temp,
            "--mode",
            args.mode,
        ]
        for extra in args.also_write:
            raw += ["--also-write", extra]
        raw += ["--", *tail]
        try:
            return run_confined(raw)
        except RunnerFailure:
            return _RUNNER_FAILURE_EXIT
        except Exception as exc:  # noqa: BLE001 — runner contract: any failure is exit 127
            sys.stderr.write(f"{_RUNNER_SIGNATURE}: {exc}\n")
            return _RUNNER_FAILURE_EXIT
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
