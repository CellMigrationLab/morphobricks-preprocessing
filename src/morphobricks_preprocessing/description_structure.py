"""Validation phase 3, structure (experiment-v1 sections 4 and 6).

Closed field sets, forbidden names, required fields, exact TOML types (nothing is converted),
non-empty tables and arrays, dates, integer ranges, the enumerations and the N35 confirmation.
It reads the parsed document only, never a folder, and reports every error of the phase.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from datetime import date

from .description_report import Issues, key_path
from .description_rules import (
    CHANNEL,
    CONDITION,
    FORBIDDEN,
    IMAGE_TYPES,
    MASK,
    MEMBER,
    REPEAT,
    REPRESENTATIONS,
    TOP,
    UNIT,
    USED_IDS,
    Code,
    TableSpec,
)

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _is_text(value: object) -> bool:
    return type(value) is str


def _is_table(value: object) -> bool:
    return type(value) is dict


def _fields(table: Mapping[str, object], spec: TableSpec, path: str, out: Issues) -> None:
    """Report forbidden, unknown and missing fields of one table."""
    for key in table:
        if key in FORBIDDEN:
            out.add(Code.E_FORBIDDEN_FIELD, key_path(path, key), key)
        elif key not in spec.fields:
            out.add(Code.E_UNKNOWN_FIELD, key_path(path, key), key)
    for key in spec.fields:
        if key in spec.required and key not in table:
            out.add(Code.E_MISSING_FIELD, key_path(path, key))


def _text(table: Mapping[str, object], key: str, path: str, out: Issues, *, filled: bool) -> None:
    """Check a text field; with ``filled`` it may not be empty or whitespace only."""
    if key not in table:
        return
    value = table[key]
    if not _is_text(value):
        out.add(Code.E_TYPE, key_path(path, key), value)
    elif filled and value.strip() == "":
        out.add(Code.E_TEXT_EMPTY, key_path(path, key), value)


def _text_array(
    table: Mapping[str, object], key: str, path: str, out: Issues, *, filled: bool, minimum: int
) -> None:
    """Check an array of text; ``filled`` refuses empty items, ``minimum`` entries needed."""
    if key not in table:
        return
    value = table[key]
    here = key_path(path, key)
    if type(value) is not list:
        out.add(Code.E_TYPE, here, value)
        return
    if minimum and len(value) < minimum:
        out.add(Code.E_EMPTY, here, value)
    for index, item in enumerate(value):
        if not _is_text(item):
            out.add(Code.E_TYPE, f"{here}[{index}]", item)
        elif filled and item.strip() == "":
            out.add(Code.E_TEXT_EMPTY, f"{here}[{index}]", item)


def _id_tables(
    parent: Mapping[str, object], key: str, path: str, out: Issues, *, minimum: int
) -> Iterator[tuple[str, Mapping[str, object], str]]:
    """Yield (ID, table, path) of an ID-keyed table; a forbidden ID key is reported, skipped.

    A missing table is reported by the parent's field check, not here.
    """
    if key not in parent:
        return
    here = key_path(path, key)
    tables = parent[key]
    if not _is_table(tables):
        out.add(Code.E_TYPE, here, tables)
        return
    if minimum and not tables:
        out.add(Code.E_EMPTY, here, tables)
    for identifier, table in tables.items():
        if identifier in FORBIDDEN:
            out.add(Code.E_FORBIDDEN_FIELD, key_path(here, identifier), identifier)
        elif not _is_table(table):
            out.add(Code.E_TYPE, key_path(here, identifier), table)
        else:
            yield identifier, table, key_path(here, identifier)


def _condition(table: Mapping[str, object], path: str, out: Issues) -> None:
    _fields(table, CONDITION, path, out)
    _text(table, "source_folder", path, out, filled=False)
    _text(table, "display_name", path, out, filled=True)
    _text(table, "reference", path, out, filled=False)
    _text(table, "target", path, out, filled=True)
    _text(table, "treatment_key", path, out, filled=True)


def _channel(table: Mapping[str, object], path: str, out: Issues) -> None:
    _fields(table, CHANNEL, path, out)
    _text(table, "display_name", path, out, filled=True)
    _text_array(table, "header_names", path, out, filled=True, minimum=0)


def _confirmation(table: Mapping[str, object], path: str, out: Issues) -> None:
    """Check ``confirmed`` (a boolean, true in a published file) and ``confirmed_on``."""
    if "confirmed" in table:
        value = table["confirmed"]
        if type(value) is not bool:
            out.add(Code.E_TYPE, f"{path}.confirmed", value)
        elif not value:
            out.add(Code.E_REPEAT_UNCONFIRMED, f"{path}.confirmed", value)
    if "confirmed_on" in table:
        value = table["confirmed_on"]
        if not _is_text(value):
            out.add(Code.E_TYPE, f"{path}.confirmed_on", value)
        elif not _real_date(value):
            out.add(Code.E_DATE_FORMAT, f"{path}.confirmed_on", value)


def _real_date(value: str) -> bool:
    """Return True for a real calendar day written ``YYYY-MM-DD`` (no other spelling)."""
    if not DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _unit(table: Mapping[str, object], path: str, out: Issues) -> None:
    _fields(table, UNIT, path, out)
    _text(table, "source_folder", path, out, filled=False)
    _text(table, "display_name", path, out, filled=True)
    _text_array(table, "channels", path, out, filled=False, minimum=1)
    if "image_type" in table:
        value = table["image_type"]
        if not _is_text(value):
            out.add(Code.E_TYPE, f"{path}.image_type", value)
        elif value not in IMAGE_TYPES:
            out.add(Code.E_ENUM, f"{path}.image_type", value)


def _member(table: Mapping[str, object], path: str, out: Issues) -> None:
    _fields(table, MEMBER, path, out)
    _text(table, "source_repeat", path, out, filled=False)
    if "image_count" in table:
        count = table["image_count"]
        if type(count) is not int:
            out.add(Code.E_TYPE, f"{path}.image_count", count)
        elif count < 1:
            out.add(Code.E_VALUE_RANGE, f"{path}.image_count", count)
    for _, unit, unit_path in _id_tables(table, "technical_repeats", path, out, minimum=1):
        _unit(unit, unit_path, out)


def _repeat(table: Mapping[str, object], path: str, out: Issues) -> None:
    _fields(table, REPEAT, path, out)
    _text(table, "display_name", path, out, filled=True)
    _confirmation(table, path, out)
    for _, member, member_path in _id_tables(table, "members", path, out, minimum=1):
        _member(member, member_path, out)


def _mask(table: Mapping[str, object], path: str, out: Issues) -> None:
    _fields(table, MASK, path, out)
    _text(table, "source_type", path, out, filled=False)
    _text(table, "display_name", path, out, filled=True)
    _text_array(table, "source_channels", path, out, filled=False, minimum=0)
    if "representation" in table:
        value = table["representation"]
        if not _is_text(value):
            out.add(Code.E_TYPE, f"{path}.representation", value)
        elif value not in REPRESENTATIONS:
            out.add(Code.E_ENUM, f"{path}.representation", value)


def _used_ids(doc: Mapping[str, object], out: Issues) -> None:
    if "used_ids" not in doc:
        return
    table = doc["used_ids"]
    if not _is_table(table):
        out.add(Code.E_TYPE, "used_ids", table)
        return
    _fields(table, USED_IDS, "used_ids", out)
    for key in USED_IDS.fields:
        _text_array(table, key, "used_ids", out, filled=False, minimum=1)


SECTIONS: tuple[tuple[str, int, Callable[[Mapping[str, object], str, Issues], None]], ...] = (
    ("conditions", 1, _condition),
    ("channels", 1, _channel),
    ("repeats", 1, _repeat),
    ("masks", 0, _mask),
)


def check_structure(doc: Mapping[str, object]) -> Issues:
    """Return every phase-3 error of a parsed document.

    Parameters
    ----------
    doc
        The parsed TOML, with the header already checked.
    """
    out = Issues()
    _fields(doc, TOP, "", out)
    for key, minimum, check in SECTIONS:
        for _, table, path in _id_tables(doc, key, "", out, minimum=minimum):
            check(table, path, out)
    _used_ids(doc, out)
    return out
