"""A skipped Spec conformance test is a failure (conformance plan section 1)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent

SKIPPING = """
import pytest

def test_skips():
    pytest.skip("not run")

@pytest.mark.skipif(True, reason="never")
def test_skipif():
    pass

@pytest.mark.xfail(reason="later")
def test_xfail():
    assert False

def test_importorskip():
    pytest.importorskip("a_module_that_is_not_installed")

def test_passes():
    pass
"""


def _pytest(folder: Path) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "spec_gate_plugin", "-q"],
        cwd=folder,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(TESTS)},
        check=False,
    )
    return result.returncode, result.stdout


def test_a_skipped_spec_test_is_reported_as_failed(tmp_path):
    (tmp_path / "test_spec_demo.py").write_text(SKIPPING, encoding="utf-8")
    code, out = _pytest(tmp_path)
    assert code == 1
    # A skip at call time is a failure; a skipif at setup is an error: both fail the run.
    refused = [line for line in out.splitlines() if line.startswith(("FAILED", "ERROR"))]
    assert len(refused) == 4
    assert out.count("a Spec conformance test must run") >= 4
    assert " 1 passed" in out


def test_other_tests_may_still_skip(tmp_path):
    (tmp_path / "test_other.py").write_text(SKIPPING, encoding="utf-8")
    code, out = _pytest(tmp_path)
    assert "FAILED" not in out and "ERROR" not in out
    assert "1 passed" in out and "3 skipped" in out and "1 xfailed" in out
    assert code == 0
