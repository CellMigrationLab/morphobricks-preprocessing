"""Tests are exempt from the annotation and docstring rules (owner decision, 1 Nov 2026)."""

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check  # noqa: E402

LONG_PRIVATE = (
    "def _helper(a, b):\n"
    + "".join(f"    a = a + {i}\n" for i in range(15))
    + ("    return a + b\n")
)


def test_ruff_ignores_annotations_and_docstrings_in_tests():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ignored = data["tool"]["ruff"]["lint"]["per-file-ignores"]["tests/**"]
    assert {"ANN", "D"} <= set(ignored)


def test_the_rules_tool_leaves_tests_out_of_the_docstring_rule(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    package = tmp_path / check.config()["package"]
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('"""Package."""\n', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(LONG_PRIVATE, encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "helper.py").write_text(LONG_PRIVATE, encoding="utf-8")
    monkeypatch.setattr(check, "ROOT", tmp_path)
    problems, _ = check.code_rule_problems()
    flagged = [p for p in problems if "without a docstring" in p]
    assert [p.split(":")[0] for p in flagged] == ["tools/helper.py"]
