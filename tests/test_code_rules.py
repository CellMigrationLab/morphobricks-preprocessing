"""The code standards (spec §9): the shared rules, their baseline, and each guard.

``tests/guard_fixtures`` holds deliberately bad modules; Ruff skips them in normal runs
(``extend-exclude``) and these tests lint them explicitly to prove each rule fires.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARDS = Path(__file__).parent / "guard_fixtures"
sys.path.insert(0, str(ROOT / "tools"))

import check  # noqa: E402
from code_rules import (  # noqa: E402
    baseline_key,
    check_source,
    compare_with_baseline,
    layer_problems,
    missing_private_docstrings,
    noqa_counts,
    reserved_name_problems,
    shrunk_baseline,
)

SHARED_SHA256 = "cc04a420a4d25d5ef200bfa370f052d9911786fde0c57385dcaf155716d6e0a2"
"""SHA-256 of the shared ``tools/code_rules.py``, identical in every MorphoBricks repository."""


def test_the_shared_rules_file_is_the_shared_copy():
    # With LF line endings, as committed (a Windows checkout may convert them).
    data = (ROOT / "tools" / "code_rules.py").read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(data).hexdigest()
    assert digest == SHARED_SHA256


def test_the_repository_matches_its_baseline():
    """No new violation, none grown, and the baseline as small as the code allows."""
    assert check.baseline_problems() == []


def test_every_package_module_has_a_layer():
    layers = check.config()["layers"]
    package = ROOT / check.config()["package"]
    modules = {check._module_name(path, package) for path in package.rglob("*.py")}
    assert modules == set(layers)


def ruff_codes(path: Path) -> set[str]:
    result = subprocess.run(
        [
            sys.executable,
            *("-m", "ruff", "check", "--no-cache", "--output-format=concise"),
            # The fixtures live under tests/, whose ignores would hide the rules.
            *("--config", "lint.per-file-ignores = {}", str(path)),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if ": " in line]
    return {line.split(": ", 1)[1].split(" ", 1)[0] for line in lines}


@pytest.mark.parametrize(
    ("fixture", "code"),
    [
        ("missing_annotation.py", "ANN001"),
        ("too_complex.py", "C901"),
        ("many_arguments.py", "PLR0913"),
        # A blanket noqa would hide every rule from the noqa ratchet.
        ("blanket_noqa.py", "PGH004"),
        ("missing_annotation.py", "I002"),
    ],
)
def test_ruff_guard_catches(fixture, code):
    assert code in ruff_codes(GUARDS / fixture)


def guard(name):
    return (GUARDS / name).read_text(encoding="utf-8"), name


@pytest.mark.parametrize(
    ("fixture", "text"),
    [
        ("long_function.py", "executable lines (max 60)"),
        ("many_arguments.py", "9 parameters (max 8)"),
        ("uses_suppress.py", "contextlib.suppress"),
    ],
)
def test_size_and_suppress_guards(fixture, text):
    assert any(text in problem for problem in check_source(*guard(fixture)))


def test_private_functions_need_a_docstring():
    (problem,) = missing_private_docstrings(*guard("private_without_docstring.py"))
    assert "_helper: private function of 13 lines without a docstring" in problem
    assert missing_private_docstrings(*guard("long_function.py")) == []


def test_layers():
    assert layer_problems(*guard("pure_with_io.py"), "pure") == [
        "pure_with_io.py: pure module imports os",
        "pure_with_io.py: pure module calls open()",
    ]
    assert layer_problems(*guard("pure_with_io.py"), "io") == []
    assert layer_problems(*guard("ui_computing.py"), "ui") == [
        "ui_computing.py: UI module imports scipy"
    ]
    assert "no layer declared" in layer_problems(*guard("ui_computing.py"), None)[0]


def test_reserved_names_belong_to_their_owner():
    (problem,) = reserved_name_problems(*guard("reserved_copy.py"), "preprocessing")
    assert "min_max: owned by normalization" in problem
    assert reserved_name_problems(*guard("reserved_copy.py"), "normalization") == []


def test_the_baseline_only_shrinks():
    problems = ["a.py: 700 lines (max 600)", "a.py:12 f: 70 executable lines (max 60)"]
    noqa = {"C901": 1, "PLR0913": 0, "PLR0915": 0, "ANN": 2}
    baseline = shrunk_baseline(problems, noqa, {"problems": {}, "noqa": {}})
    assert baseline["problems"] == {}  # nothing is ever added
    recorded = {
        "problems": {baseline_key(p)[0]: baseline_key(p)[1] for p in problems},
        "noqa": dict(noqa),
    }
    assert compare_with_baseline(problems, noqa, recorded) == []
    # The function moved (another line number): still the same entry.
    moved = ["a.py: 700 lines (max 600)", "a.py:40 f: 70 executable lines (max 60)"]
    assert compare_with_baseline(moved, noqa, recorded) == []
    grown = ["a.py: 710 lines (max 600)", problems[1]]
    assert compare_with_baseline(grown, noqa, recorded) == [
        "grew from 700 to 710: a.py: # lines (max #)"
    ]
    new = [*problems, "b.py:3 g: 9 parameters (max 8)"]
    assert compare_with_baseline(new, noqa, recorded) == [
        "new violation: b.py g: # parameters (max #)"
    ]
    smaller = ["a.py: 650 lines (max 600)"]
    found = compare_with_baseline(smaller, noqa, recorded)
    assert len(found) == 2 and all("baseline can shrink" in f for f in found)
    lowered = shrunk_baseline(smaller, noqa, recorded)
    assert lowered["problems"] == {"a.py: # lines (max #)": 650}
    more_noqa = {**noqa, "ANN": 3}
    assert compare_with_baseline(problems, more_noqa, recorded) == [f"{NOQA}: ANN grew from 2 to 3"]


NOQA = "# " + "noqa"
"""Built, not written out, so this file's test data is not counted by the noqa ratchet."""


def test_noqa_counts():
    sources = [("a.py", f"x = 1  {NOQA}: ANN001, E501\ndef f():  {NOQA}:C901,PLR0915\n")]
    assert noqa_counts(sources) == {"C901": 1, "PLR0913": 0, "PLR0915": 1, "ANN": 1}


def test_file_level_noqa_counts():
    sources = [("a.py", "# ruff: " + "noqa: ANN001, PLR0913\n")]
    assert noqa_counts(sources) == {"C901": 0, "PLR0913": 1, "PLR0915": 0, "ANN": 1}


def test_line_numbers_are_never_a_baseline_size():
    """Moving a duplicated helper or a module with a digit in its name changes no size."""
    duplicate = "identical bodies: tests/a.py:36 helper, tests/b.py:20 helper"
    moved = "identical bodies: tests/a.py:90 helper, tests/b.py:20 helper"
    assert baseline_key(duplicate) == baseline_key(moved)
    assert baseline_key(duplicate)[1] == 0
    assert baseline_key("import cycle: p.v2 -> p.c -> p.v2")[1] == 0
    assert baseline_key("p.py:3 f_v2: 70 executable lines (max 60)")[1] == 70
    assert baseline_key("p.py:3 _h: private function of 13 lines without a docstring")[1] == 13
