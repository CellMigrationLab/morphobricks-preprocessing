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


def _gated(folder: Path, expected: str = "") -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "spec_gate_plugin", "-q"],
        cwd=folder,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(TESTS), "SPEC_GATE_EXPECTED": expected},
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


PASSING = "def test_passes():\n    pass\n"


def test_a_module_level_skip_in_a_spec_module_fails_the_run(tmp_path):
    (tmp_path / "test_spec_demo.py").write_text(
        'import pytest\npytest.skip("no weights", allow_module_level=True)\n' + PASSING,
        encoding="utf-8",
    )
    code, out = _gated(tmp_path)
    assert code != 0
    assert "a Spec conformance module must be collected, it was skipped: Skipped: no weights" in out


def test_a_module_level_importorskip_in_a_spec_module_fails_the_run(tmp_path):
    (tmp_path / "test_spec_demo.py").write_text(
        'import pytest\npytest.importorskip("a_module_that_is_not_installed")\n' + PASSING,
        encoding="utf-8",
    )
    code, out = _gated(tmp_path)
    assert code != 0 and "a Spec conformance module must be collected" in out


def test_a_module_level_skip_in_another_module_may_still_skip(tmp_path):
    (tmp_path / "test_other.py").write_text(
        'import pytest\npytest.importorskip("a_module_that_is_not_installed")\n' + PASSING,
        encoding="utf-8",
    )
    (tmp_path / "test_spec_demo.py").write_text(PASSING, encoding="utf-8")
    code, out = _gated(tmp_path)
    assert code == 0 and "1 passed" in out and "1 skipped" in out


def test_a_missing_spec_module_fails_a_whole_folder_run(tmp_path):
    (tmp_path / "test_spec_here.py").write_text(PASSING, encoding="utf-8")
    code, out = _gated(tmp_path, "test_spec_here.py,test_spec_gone.py")
    assert code != 0
    assert "test_spec_gone.py" in out and "test_spec_here.py:" not in out
    (tmp_path / "test_spec_gone.py").write_text(PASSING, encoding="utf-8")
    assert _gated(tmp_path, "test_spec_here.py,test_spec_gone.py")[0] == 0


def test_a_spec_module_without_a_collected_test_fails_a_whole_folder_run(tmp_path):
    (tmp_path / "test_spec_empty.py").write_text("X = 1\n", encoding="utf-8")
    code, out = _gated(tmp_path, "test_spec_empty.py")
    assert code != 0 and "test_spec_empty.py" in out


def test_a_run_of_one_chosen_file_does_not_need_the_other_spec_modules(tmp_path):
    (tmp_path / "test_spec_here.py").write_text(PASSING, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(TESTS), "SPEC_GATE_EXPECTED": "test_spec_gone.py"}
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "spec_gate_plugin"]
    result = subprocess.run(
        [*command, "-q", "test_spec_here.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0 and "1 passed" in result.stdout


def test_every_spec_module_of_the_repository_is_listed():
    from spec_gate import EXPECTED_MODULES, MODULE_PREFIX

    found = sorted(p.name for p in TESTS.glob(f"{MODULE_PREFIX}*.py"))
    assert found == sorted(EXPECTED_MODULES)
