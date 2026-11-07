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


# The oracle's paths for these are the table (``conditions``, ``repeats``, ``masks``), a bare ID
# or a list of IDs; the reader names each offending field, so only the codes are compared.
COARSE = frozenset(
    {
        "E_SOURCE_FOLDER_REUSED",
        "E_SOURCE_FOLDER_CONFLICT",
        "E_SOURCE_REPEAT_REUSED",
        "E_MASK_SOURCE_TYPE_REUSED",
        "E_REFERENCE_CYCLE",
    }
)
# The oracle abbreviates the table in the path of a display-name collision.
ABBREVIATED = {"c.": "conditions.", "ch.": "channels.", "r.": "repeats.", "m.": "masks."}


def normal(path: str, code: str = "") -> str:
    """Return a table path as the reader writes it: no leading dot, no abbreviated table."""
    path = path.lstrip(".")
    if code == "E_DISPLAY_NAME_COLLISION":
        for short, table in ABBREVIATED.items():
            if path.startswith(short):
                return table + path[len(short) :]
    return path


def verdict(errors: list[tuple[str, str]]) -> tuple[frozenset[str], frozenset[tuple[str, str]]]:
    """Return the code set and the (code, path) pairs of the codes whose path is compared."""
    codes = frozenset(code for code, _ in errors)
    pairs = frozenset((code, normal(path, code)) for code, path in errors if code not in COARSE)
    return codes, pairs


def oracle_verdict(data: bytes) -> tuple[int, frozenset[str], frozenset, frozenset[str]]:
    """Return (phase, error codes, compared (code, path) pairs, warning codes) for bytes."""
    report, _ = refval().validate(data)
    codes, pairs = verdict([(code, path) for code, path, _ in report.errors])
    return report.phase, codes, pairs, frozenset(report.warning_codes)


def oracle_document_verdict(doc: dict, phases: tuple[int, ...]) -> tuple[int, set, set]:
    """Run the oracle's document phases (2 to 5) on a parsed document.

    Returns the first failing phase (the last one given when none fails), its error codes and
    their compared (code, path) pairs.
    """
    module = refval()
    functions = {2: module.phase2, 3: module.phase3, 4: module.phase4, 5: module.phase5}
    report = module.Report()
    phase = 0
    for phase in phases:
        functions[phase](doc, report)
        if report.errors:
            break
    codes, pairs = verdict([(code, path) for code, path, _ in report.errors])
    return phase, set(codes), set(pairs)


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


WIDE = frozenset({"source_type", "source_folder", "reference", "source_repeat", "display_name"})
INSERTIONS = (
    ("header_names", ["x"]),
    ("header_names", ["x", "x"]),
    ("display_name", "x"),
    ("image_type", "rgb"),
    ("source_channels", ["x"]),
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


def _texts(node: object) -> list[str]:
    """Return the distinct text values and keys of a document, sorted (donor values)."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, child in node.items():
            found.add(key)
            found.update(_texts(child))
    elif isinstance(node, list):
        for child in node:
            found.update(_texts(child))
    elif isinstance(node, str):
        found.add(node)
    return sorted(found)


def _edit(doc: dict, trail: tuple, change) -> dict:
    """Return a copy of the document with ``change(parent, last)`` applied at the trail."""
    copied = copy.deepcopy(doc)
    change(_parent(copied, trail), trail[-1])
    return copied


def _duplicate_first(parent: object, last: object) -> None:
    parent[last] = [*parent[last], parent[last][0]]


def _replacements(doc: dict, trail: tuple, value: object, borrowed: list) -> Iterator[tuple]:
    for index, replacement in enumerate((*REPLACEMENTS, *borrowed)):
        if replacement == value and type(replacement) is type(value):
            continue

        def put(parent: object, last: object, new: object = replacement) -> None:
            parent[last] = copy.deepcopy(new)

        yield f"set #{index}", _edit(doc, trail, put)


def _renames(doc: dict, trail: tuple, borrowed: list) -> Iterator[tuple]:
    last = trail[-1]
    names = ("files", "colour", "ring", "Cells_in_x", "b", str(last).swapcase(), str(last) + "x")
    for name in (*names, *borrowed[:3]):
        if name == last or name in _parent(doc, trail):
            continue

        def move(parent: dict, key: str, new: str = name) -> None:
            parent[new] = parent.pop(key)

        yield f"rename to {name}", _edit(doc, trail, move)


def _insertions(doc: dict, trail: tuple, value: dict, donors: list) -> Iterator[tuple]:
    extras = [(key, extra) for key, extra in INSERTIONS if key not in value]
    for donor in donors[:8]:
        extras += [(key, donor) for key in ("reference", "source_type", "source_folder")]
    for key, extra in extras:
        if key in value:
            continue

        def add(parent: dict, last: str, key: str = key, extra: object = extra) -> None:
            parent[last][key] = copy.deepcopy(extra)

        yield f"insert {key}={extra!r}", _edit(doc, trail, add)


def mutations(doc: dict) -> Iterator[tuple[str, dict]]:
    """Yield (label, mutated copy) of a document, one change at a time, in a fixed order.

    Every node is deleted, replaced by each of ``REPLACEMENTS`` and, for a table key, renamed
    to a forbidden, a reserved, an unknown and a case-changed name; a table gains fields; a text
    array entry is duplicated. Each node also takes a few values and key names of the same
    document (donors), which is what makes two identities collide or a reference dangle.
    """
    donors = _texts(doc)
    for number, trail in enumerate(_paths(doc)):
        label = ".".join(str(step) for step in trail)
        parent, last = _parent(doc, trail), trail[-1]
        value = parent[last]
        borrowed = [donors[(number * 7 + step * 13) % len(donors)] for step in range(6)]
        if last in WIDE:
            borrowed = donors[:40]
        if isinstance(parent, dict):
            yield f"delete {label}", _edit(doc, trail, lambda p, k: p.pop(k))
            for text, mutated in _renames(doc, trail, borrowed):
                yield f"{text} ({label})", mutated
        for text, mutated in _replacements(doc, trail, value, borrowed):
            yield f"{text} ({label})", mutated
        if isinstance(value, dict):
            for text, mutated in _insertions(doc, trail, value, donors):
                yield f"{text} into {label}", mutated
        if isinstance(value, list) and value:
            yield f"duplicate first of {label}", _edit(doc, trail, _duplicate_first)
