#!/usr/bin/env python3
"""Tests for tools/check_stdlib_only.py -- the SPEC section 4 stdlib gate.

SPEC section 4 requires every Python script under tools/ and .github/scripts/
to be stdlib-only (zero external dependencies), and this gate is what enforces
it. These tests pin the two properties that make it a gate rather than a
report: it flags a third-party import -- even one hidden inside try/except --
and it stays quiet on a clean tree. Every assertion runs the real scanner,
never a stub.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import check_stdlib_only as stdlib  # noqa: E402


def _write(tmp_path: Path, name: str, content: str) -> Path:
    """Write a file into the scanned tools/ tree and return its path."""
    directory = tmp_path / "tools"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def _write_bom(tmp_path: Path, name: str, content: str) -> Path:
    """Write a BOM-prefixed file into the scanned tools/ tree.

    The BOM bytes are written explicitly (EF BB BF) rather than relying on
    the platform text encoding to add one.
    """
    directory = tmp_path / "tools"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
    return path


def test_clean_file_using_json_and_pathlib_passes(tmp_path):
    _write(
        tmp_path,
        "clean.py",
        "import json\nimport pathlib\nfrom pathlib import Path\n",
    )
    assert stdlib.find_findings(tmp_path) == []
    assert stdlib.main([str(tmp_path)]) == 0


def test_third_party_import_is_reported(tmp_path):
    _write(tmp_path, "bad.py", "import requests\n")
    assert stdlib.find_findings(tmp_path) == ["tools/bad.py:1: requests"]


def test_import_inside_try_except_is_still_reported(tmp_path):
    _write(
        tmp_path,
        "bad.py",
        "try:\n    import requests\nexcept ImportError:\n    pass\n",
    )
    assert stdlib.find_findings(tmp_path) == ["tools/bad.py:2: requests"]


def test_from_tools_import_passes(tmp_path):
    _write(tmp_path, "ok.py", "from tools import garden\n")
    assert stdlib.find_findings(tmp_path) == []


def test_import_of_sibling_module_in_scanned_dir_passes(tmp_path):
    _write(tmp_path, "garden.py", "x = 1\n")
    _write(tmp_path, "ok.py", "import garden\n")
    assert stdlib.find_findings(tmp_path) == []


def test_pytest_allowed_in_test_file(tmp_path):
    _write(tmp_path, "test_x.py", "import pytest\n")
    assert stdlib.find_findings(tmp_path) == []


def test_pytest_reported_in_non_test_file(tmp_path):
    _write(tmp_path, "helper.py", "import pytest\n")
    assert stdlib.find_findings(tmp_path) == ["tools/helper.py:1: pytest"]


def test_exit_code_is_1_on_finding_and_0_when_clean(tmp_path):
    assert stdlib.main([str(tmp_path)]) == 0
    _write(tmp_path, "bad.py", "import requests\n")
    assert stdlib.main([str(tmp_path)]) == 1


def test_missing_scanned_directory_is_not_an_error(tmp_path):
    assert stdlib.find_findings(tmp_path) == []
    assert stdlib.main([str(tmp_path)]) == 0


def test_bom_prefixed_third_party_import_is_reported(tmp_path):
    _write_bom(tmp_path, "bom.py", "import requests\n")
    assert stdlib.find_findings(tmp_path) == ["tools/bom.py:1: requests"]


def test_bom_prefixed_clean_file_passes(tmp_path):
    _write_bom(tmp_path, "clean.py", "import json\nimport pathlib\n")
    assert stdlib.find_findings(tmp_path) == []
    assert stdlib.main([str(tmp_path)]) == 0


def test_syntactically_invalid_file_is_reported_as_unparseable(tmp_path):
    _write(tmp_path, "broken.py", "def broken(:\n")
    findings = stdlib.find_findings(tmp_path)
    assert len(findings) == 1
    assert findings[0].startswith("tools/broken.py:1: unparseable (")
