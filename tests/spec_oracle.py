"""The Spec's reference validator as a test oracle, and mutations of parsed documents.

``tools/audit/refval.py`` is an audit tool that no brick may import: it is loaded here from the
pin-verified vendored copy, by path, only to compare its verdicts with the reader's. The
reader never calls it (``tests/test_spec_no_fallback.py`` scans the package).
"""

from __future__ import annotations

import copy
import datetime
import importlib.util
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from types import ModuleType

from spec_pin import spec_root


@cache
def refval() -> ModuleType:
    """Return the vendored reference validator module (loaded by path)."""
    path = Path(spec_root()) / "tools" / "audit" / "refval.py"
    spec = importlib.util.spec_from_file_location("spec_refval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normal(path: str) -> str:
    """Return a table path without the leading dot the oracle gives top-level fields."""
    return path.lstrip(".")


def oracle_verdict(data: bytes) -> tuple[int, frozenset[str], frozenset[str], frozenset[str]]:
    """Return (phase, error codes, error paths, warning codes) the oracle gives for bytes."""
    report, _ = refval().validate(data)
    paths = frozenset(normal(path) for _, path, _ in report.errors)
    return report.phase, frozenset(report.codes), paths, frozenset(report.warning_codes)


def oracle_document_verdict(doc: dict, phases: tuple[int, ...]) -> tuple[int, set, set]:
    """Run the oracle's document phases (2 to 5) on a parsed document.

    Returns the first failing phase (the last one given when none fails), its error codes and
    their normalized paths.
    """
    module = refval()
    functions = {2: module.phase2, 3: module.phase3, 4: module.phase4, 5: module.phase5}
    report = module.Report()
    phase = 0
    for phase in phases:
        functions[phase](doc, report)
        if report.errors:
            break
    return phase, set(report.codes), {normal(path) for _, path, _ in report.errors}


REPLACEMENTS = (
    1,
    0,
    -1,
    1.5,
    True,
    False,
    "",
    " ",
    "x",
    "NA",
    " x",
    "Single",
    "single",
    "ctrl",
    "Café",
    ["x"],
    [],
    {},
    {"x": {}},
    datetime.date(2026, 1, 1),
    datetime.datetime(2026, 1, 1, 12, 0),
    datetime.time(12, 0),
)


def _paths(node: object, trail: tuple = ()) -> Iterator[tuple]:
    """Yield the trail (keys and indexes) of every node below the root."""
    if isinstance(node, dict):
        for key, child in node.items():
            yield (*trail, key)
            yield from _paths(child, (*trail, key))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield (*trail, index)
            yield from _paths(child, (*trail, index))


def _parent(doc: object, trail: tuple) -> object:
    for step in trail[:-1]:
        doc = doc[step]
    return doc


def mutations(doc: dict) -> Iterator[tuple[str, dict]]:
    """Yield (label, mutated copy) of a document, one change at a time, in a fixed order.

    Every node is deleted, replaced by each of ``REPLACEMENTS`` and, for a table key, renamed
    to a forbidden, an unknown and a case-changed name; a text array entry is duplicated.
    """
    for trail in _paths(doc):
        label = ".".join(str(step) for step in trail)
        copied = copy.deepcopy(doc)
        parent, last = _parent(copied, trail), trail[-1]
        value = parent[last]
        if isinstance(parent, dict):
            del parent[last]
            yield f"delete {label}", copied
        for index, replacement in enumerate(REPLACEMENTS):
            if replacement == value and type(replacement) is type(value):
                continue
            copied = copy.deepcopy(doc)
            _parent(copied, trail)[last] = copy.deepcopy(replacement)
            yield f"set {label} #{index}", copied
        if isinstance(parent, dict):
            for name in ("files", "colour", str(last).swapcase(), str(last) + "x"):
                if name == last or name in parent:
                    continue
                copied = copy.deepcopy(doc)
                holder = _parent(copied, trail)
                holder[name] = holder.pop(last)
                yield f"rename {label} to {name}", copied
        if isinstance(value, list) and value:
            copied = copy.deepcopy(doc)
            _parent(copied, trail)[last] = [*value, value[0]]
            yield f"duplicate first of {label}", copied
