"""No second copy of the rules (conformance plan sections 1 and 2, "No fallback rules").

The reader has one table of the rules (``description_rules.py``), compared with the vendored
text by its own tests. This scan fails on any other table of codes, enumerations or names in
the package, and on the markers of a lenient, older or default-version path. It runs on
source code as written, so a copy cannot hide behind a test that never imports it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from spec_text import spec_forbidden, spec_reserved

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "morphobricks_preprocessing"
RULES_MODULE = "description_rules.py"
CODE = re.compile(r"[EWRA]_[A-Z0-9_]+")
MARKERS = ("legacy", "lenient", "fallback", "best effort", "best_effort", "older_version")
VERSION_KEYS = {"schema", "schema_version"}


def reader_modules() -> list[Path]:
    """Return the modules of the experiment-description reader."""
    return sorted(PACKAGE.glob("description*.py"))


def other_modules() -> list[Path]:
    """Return the reader's modules except the one table of the rules."""
    return [path for path in reader_modules() if path.name != RULES_MODULE]


def _docstrings(tree: ast.AST) -> set[int]:
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, owners) and node.body and isinstance(node.body[0], ast.Expr):
            value = node.body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.add(id(value))
    return found


def _strings(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Dict):
        elements = [key for key in node.keys if key is not None]
    else:
        elements = list(node.elts)
    return [e.value for e in elements if isinstance(e, ast.Constant) and isinstance(e.value, str)]


def code_tables(source: str) -> list[int]:
    """Return the lines of collections holding three or more distinct codes."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Set, ast.List, ast.Tuple, ast.Dict)):
            codes = {s for s in _strings(node) if CODE.fullmatch(s)}
            if len(codes) >= 3:
                lines.append(node.lineno)
    return lines


def name_tables(source: str) -> list[int]:
    """Return the lines of collections copying the forbidden or reserved names of the text."""
    forbidden, reserved = spec_forbidden(), spec_reserved()
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Set, ast.List, ast.Tuple, ast.Dict)):
            names = set(_strings(node))
            if len(names & forbidden) >= 4 or len(names & reserved) >= 4:
                lines.append(node.lineno)
    return lines


def code_literals(source: str) -> list[int]:
    """Return the lines of string literals that are a code (outside docstrings)."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and CODE.fullmatch(node.value)
    ]


def fallback_markers(source: str) -> list[str]:
    """Return what marks a lenient, older or default-version path (outside docstrings)."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    found = []
    for node in ast.walk(tree):
        words = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            words = [] if id(node) in skip else [node.value]
        elif isinstance(node, (ast.Name, ast.arg, ast.FunctionDef, ast.ClassDef)):
            words = [getattr(node, "id", None) or getattr(node, "arg", None) or node.name]
        elif isinstance(node, ast.Attribute):
            words = [node.attr]
        found += [
            f"{marker} (line {node.lineno})"
            for w in words
            for marker in MARKERS
            if marker in w.lower()
        ]
        if _default_version(node):
            found.append("a default for the schema or its version")
    return found


def _default_version(node: ast.AST) -> bool:
    """Return True for ``x.get("schema_version", default)`` and a ``schema_version=`` default."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        first = node.args[0] if node.args else None
        named = isinstance(first, ast.Constant) and first.value in VERSION_KEYS
        return node.func.attr in {"get", "setdefault", "pop"} and named and len(node.args) > 1
    if isinstance(node, ast.arguments):
        positional = node.args[len(node.args) - len(node.defaults) :]
        keyword = [
            a for a, default in zip(node.kwonlyargs, node.kw_defaults, strict=True) if default
        ]
        return any(a.arg in VERSION_KEYS for a in positional + keyword)
    return False


def test_the_scan_sees_the_reader_modules():
    # Not vacuous: the modules it exists to scan are there (and the rules module is the one
    # table the other scans leave out).
    names = {path.name for path in reader_modules()}
    assert {
        "description_rules.py",
        "description_read.py",
        "description_structure.py",
        "description_identity.py",
        "description_semantics.py",
        "description_warnings.py",
    } <= names
    assert RULES_MODULE in names and RULES_MODULE not in {p.name for p in other_modules()}


def test_no_table_of_codes_outside_the_rules_module():
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name != RULES_MODULE:
            lines = code_tables(path.read_text(encoding="utf-8"))
            if lines:
                found[str(path.relative_to(PACKAGE))] = lines
    assert found == {}


def test_the_reader_has_no_copy_of_the_names_or_codes():
    found = {}
    for path in other_modules():
        source = path.read_text(encoding="utf-8")
        lines = name_tables(source) + code_literals(source)
        if lines:
            found[path.name] = lines
    assert found == {}


def test_the_reader_has_no_lenient_older_or_default_path():
    found = {}
    for path in reader_modules():
        markers = fallback_markers(path.read_text(encoding="utf-8"))
        if markers:
            found[path.name] = markers
    assert found == {}


# The scan itself: each kind of copy it exists to catch is caught, a clean module is not.
COPIES = {
    "a table of codes": 'CODES = {"E_TYPE", "E_ENUM", "E_EMPTY", "W_UNUSED_CHANNEL"}\n',
    "a table of codes as dict keys": 'X = {"E_TYPE": 1, "E_ENUM": 2, "E_EMPTY": 3}\n',
}
NAMES = {
    "the forbidden names": 'F = ("axes", "shared", "plane_scope", "pixel_size", "files")\n',
    "the reserved list": 'R = ["whole", "cell", "nucleus", "ring", "patch_id"]\n',
}
LITERAL = 'def f(r):\n    r.e("E_TYPE")\n'
MARKED = {
    "a lenient mode": "def read(data, lenient=False):\n    return data\n",
    "a legacy table": 'LEGACY_FIELDS = ("a",)\n',
    "a fallback name": "def fallback_rules():\n    return 1\n",
    "a version default": 'def f(doc):\n    return doc.get("schema_version", 1)\n',
    "a version default argument": "def f(data, schema_version=1):\n    return data\n",
}


@pytest.mark.parametrize("source", COPIES.values(), ids=COPIES)
def test_scan_catches_a_table_of_codes(source):
    assert code_tables(source)


@pytest.mark.parametrize("source", NAMES.values(), ids=NAMES)
def test_scan_catches_a_copy_of_the_forbidden_or_reserved_names(source):
    assert name_tables(source)


def test_scan_catches_a_code_literal_but_not_a_docstring():
    assert code_literals(LITERAL)
    assert not code_literals('"""Gives E_TYPE."""\n\ndef f():\n    """Gives E_ENUM."""\n')


@pytest.mark.parametrize("source", MARKED.values(), ids=MARKED)
def test_scan_catches_a_lenient_or_default_path(source):
    assert fallback_markers(source)


def test_scan_passes_a_clean_module():
    clean = '"""Never a fallback."""\n\ndef read(doc):\n    return doc["schema_version"]\n'
    assert fallback_markers(clean) == []
    assert code_tables(clean) == [] and name_tables(clean) == [] and code_literals(clean) == []
