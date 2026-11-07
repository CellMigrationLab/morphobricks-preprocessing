"""Read the tables of the vendored normative text (``docs/experiment-v1.md``).

The reader's tables of codes, forbidden names, reserved names and fields are compared with
what these functions parse, so the text, not a second copy in a test, is the source.
"""

from __future__ import annotations

import re
from functools import cache

from spec_pin import SpecPinError, spec_root

BACKTICKED = re.compile(r"`([^`]+)`")
HEADING = re.compile(r"^(#+) (.*)")


@cache
def spec_text() -> str:
    """Return the vendored ``experiment-v1.md``."""
    path = spec_root() / "docs" / "experiment-v1.md"
    if not path.is_file():
        raise SpecPinError(f"{path} is missing: the Spec text is not vendored here")
    return path.read_text(encoding="utf-8")


def section(prefix: str) -> list[str]:
    """Return the lines under the heading starting with ``prefix``, up to the next heading
    of the same or a higher level."""
    lines = spec_text().splitlines()
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if HEADING.match(line) and line.lstrip("# ").startswith(prefix)
        ),
        None,
    )
    if start is None:
        raise SpecPinError(f"the vendored text has no section {prefix!r}")
    level = len(HEADING.match(lines[start]).group(1))
    out = []
    for line in lines[start + 1 :]:
        match = HEADING.match(line)
        if match and len(match.group(1)) <= level:
            break
        out.append(line)
    return out


def table_rows(lines: list[str], header_start: str, nth: int = 0) -> list[list[str]]:
    """Return the body rows (cells, stripped) of the nth table whose header starts so."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] | None = None
    for raw in lines:
        line = raw.strip()
        if not line.startswith("|"):
            current = None
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if current is None:
            current = []
            if cells[0].startswith(header_start):
                tables.append(current)
        elif not set(line) <= set("|- :"):
            current.append(cells)
    if nth >= len(tables):
        raise SpecPinError(f"the section has no table {nth} headed {header_start!r}")
    return tables[nth]


def spec_codes() -> frozenset[str]:
    """Return the ``E_`` and ``W_`` codes of the catalogue of section 6."""
    rows = table_rows(section("6."), "Phase")
    return frozenset(BACKTICKED.search(cells[1]).group(1) for cells in rows)


def spec_forbidden() -> frozenset[str]:
    """Return the forbidden field names and ID keys of section 4.7."""
    names: set[str] = set()
    for cells in table_rows(section("4.7"), "Field"):
        names.update(BACKTICKED.findall(cells[0]))
    return frozenset(names)


def spec_reserved() -> frozenset[str]:
    """Return the reserved list of section 3.2 (channel IDs and mask IDs)."""
    names: set[str] = set()
    for cells in table_rows(section("3.2"), "Names"):
        names.update(BACKTICKED.findall(cells[0]))
    return frozenset(names)


def _rule_text(name: str) -> str:
    """Return the bullet of section 3.2 that starts with ``- **name**`` as one line."""
    lines = section("3.2")
    start = next(i for i, line in enumerate(lines) if line.startswith(f"- **{name}"))
    bullet = [lines[start]]
    for line in lines[start + 1 :]:
        if line.startswith(("- ", "  |")) or not line.strip():
            break
        bullet.append(line)
    return " ".join(part.strip() for part in bullet)


def spec_label_sentinels() -> frozenset[str]:
    """Return the names the Label rule refuses in any case (section 3.2), case-folded."""
    match = re.search(r"not (.*?) in any case", _rule_text("Label"))
    names = BACKTICKED.findall(match.group(1))
    return frozenset(("" if name == '""' else name).casefold() for name in names)


def spec_token_pattern() -> str:
    """Return the regular expression of the Token rule (section 3.2) without its anchors."""
    match = re.search(r"`\^(\[A-Za-z\].*?)\$`", _rule_text("Token"))
    return match.group(1)
