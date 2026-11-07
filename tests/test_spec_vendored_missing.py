"""Removing the vendored tree fails the suite; it never passes on defaults (plan section 2).

The pin and catalogue tests run again in a child pytest whose Spec root is an empty folder, a
folder that does not exist, and a copy without ``fixtures/``. Each run must fail (or stop at
collection) and never report a pass or a skip for a test that needs the files.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from spec_pin import ROOT_ENV, spec_root

TESTS = Path(__file__).resolve().parent
CHILD = ["test_spec_pin.py", "test_spec_catalogue.py"]


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *CHILD],
        cwd=TESTS,
        capture_output=True,
        text=True,
        env={**os.environ, ROOT_ENV: str(root)},
        check=False,
    )


def _without_fixtures(tmp_path: Path) -> Path:
    root = tmp_path / "copy"
    shutil.copytree(spec_root(), root)
    shutil.rmtree(root / "fixtures")
    return root


@pytest.mark.parametrize("variant", ["empty", "absent", "no_fixtures"])
def test_a_missing_vendored_tree_fails_the_suite(tmp_path, variant):
    if variant == "empty":
        root = tmp_path / "empty"
        root.mkdir()
    elif variant == "absent":
        root = tmp_path / "does_not_exist"
    else:
        root = _without_fixtures(tmp_path)
    result = _run(root)
    output = result.stdout.lower()
    assert result.returncode != 0, result.stdout
    assert "error" in output or "failed" in output, result.stdout
    assert "skipped" not in output
