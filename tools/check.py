"""Run every local check a pull request must pass: Ruff, the code rules and pytest.

Usage:

- ``python tools/check.py``: everything; exits non-zero when any check fails, after
  running all of them so one run lists every problem;
- ``python tools/check.py --rules``: only the code rules against their baseline (CI);
- ``python tools/check.py --update-baseline``: lower the baseline to the current code. It
  never adds or raises an entry: new violations must be fixed.

The code rules are the shared ``tools/code_rules.py``, configured in ``pyproject.toml``
under ``[tool.morphobricks.code-rules]`` (owner, package, layers, baseline file).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

from code_rules import (
    check_source,
    compare_with_baseline,
    duplicate_bodies,
    import_cycles,
    layer_problems,
    missing_private_docstrings,
    noqa_counts,
    read_baseline,
    reserved_name_problems,
    shrunk_baseline,
)

ROOT = Path(__file__).resolve().parents[1]
LINTED = ("src", "tests", "tools")


def config() -> dict:
    """Return this repository's code-rules settings from ``pyproject.toml``."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["morphobricks"]["code-rules"]


def _python_files() -> list[Path]:
    """Return the repository's checked Python files (guard fixtures are deliberately bad)."""
    files = [path for folder in LINTED for path in sorted((ROOT / folder).rglob("*.py"))]
    return [path for path in files if "guard_fixtures" not in path.parts]


def _module_name(path: Path, package_dir: Path) -> str:
    """Return the dotted module name of a file of the package."""
    parts = path.relative_to(package_dir.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def code_rule_problems() -> tuple[list[str], dict[str, int]]:
    """Return every code-rule violation in the repository and the ``# noqa`` counts."""
    settings = config()
    package_dir = ROOT / settings["package"]
    layers = settings.get("layers", {})
    sources = [
        (str(path.relative_to(ROOT).as_posix()), path.read_text(encoding="utf-8"))
        for path in _python_files()
    ]
    problems = []
    for name, text in sources:
        problems += check_source(text, name)
        problems += missing_private_docstrings(text, name)
        problems += reserved_name_problems(text, name, settings["owner"])
        path = ROOT / name
        if package_dir in path.parents:
            problems += layer_problems(text, name, layers.get(_module_name(path, package_dir)))
    problems += duplicate_bodies(sources)
    problems += import_cycles(package_dir)
    return problems, noqa_counts(sources)


def baseline_problems() -> list[str]:
    """Return what breaks the code-rules baseline (empty when the code matches it)."""
    problems, noqa = code_rule_problems()
    baseline = read_baseline(ROOT / config()["baseline"])
    return compare_with_baseline(problems, noqa, baseline)


def update_baseline() -> Path:
    """Lower the baseline file to the current code, never raising an entry."""
    problems, noqa = code_rule_problems()
    path = ROOT / config()["baseline"]
    lowered = shrunk_baseline(problems, noqa, read_baseline(path))
    path.write_text(json.dumps(lowered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str]) -> int:
    """Run the checks and return the process exit code."""
    if "--update-baseline" in argv:
        print(f"Baseline lowered: {update_baseline()}")
        return 0
    failed = False
    if "--rules" not in argv:
        commands = (
            [sys.executable, "-m", "ruff", "check", "."],
            [sys.executable, "-m", "ruff", "format", "--check", "."],
            [sys.executable, "-m", "pytest", "-q"],
        )
        for command in commands:
            print("$", " ".join(command[2:]), flush=True)
            failed |= subprocess.run(command, cwd=ROOT, check=False).returncode != 0
    print("$ code rules", flush=True)
    problems = baseline_problems()
    for problem in problems:
        print(problem)
    return 1 if failed or problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
