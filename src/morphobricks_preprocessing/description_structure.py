"""Validation phase 3, structure (experiment-v1 sections 4 and 6).

Closed field sets, forbidden names, required fields, exact TOML types (nothing is converted),
non-empty tables and arrays, dates, integer ranges, the enumerations and the N35 confirmation.
It reads the parsed document only, never a folder, and reports every error of the phase.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from datetime import date
from functools import partial

from .description_report import Issues, key_path
from .description_rules import (
    CHANNEL,
    CHANNEL_LAYOUT,
    CHANNELS_FROM,
    CHANNELS_FROM_VALUES,
    CONDITION,
    FORBIDDEN,
    IMAGE_TYPES,
    LAYOUT,
    MASK,
    MEMBER,
    REPEAT,
    REPRESENTATIONS,
    RGB_HEADER_OVERRIDE,
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
    _date(table, "confirmed_on", path, out)


def _date(table: Mapping[str, object], key: str, path: str, out: Issues) -> None:
    """Check a date field: a text value naming a real ``YYYY-MM-DD`` day."""
    if key not in table:
        return
    value = table[key]
    if not _is_text(value):
        out.add(Code.E_TYPE, key_path(path, key), value)
    elif not _real_date(value):
        out.add(Code.E_DATE_FORMAT, key_path(path, key), value)


def _image_type(table: Mapping[str, object], path: str, out: Issues) -> None:
    """Check ``image_type``: text, and only ``rgb`` (section 4.4b)."""
    if "image_type" in table:
        value = table["image_type"]
        if not _is_text(value):
            out.add(Code.E_TYPE, f"{path}.image_type", value)
        elif value not in IMAGE_TYPES:
            out.add(Code.E_ENUM, f"{path}.image_type", value)


def _real_date(value: str) -> bool:
    """Return True for a real calendar day written ``YYYY-MM-DD`` (no other spelling)."""
    if not DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _unit(table: Mapping[str, object], path: str, out: Issues, *, fast: bool) -> None:
    """Check one imaging unit's fields: types, ``channels_from``, ``image_type``, RH1.

    In a fast-mode file (``fast``) every unit states ``channels_from``; there is no default
    inside the file (section 4.4, ADR 0006). Its absence elsewhere is phase 5's rule.
    """
    _fields(table, UNIT, path, out)
    _text(table, "source_folder", path, out, filled=False)
    _text(table, "display_name", path, out, filled=True)
    _text_array(table, "channels", path, out, filled=False, minimum=1)
    if fast and CHANNELS_FROM not in table:
        out.add(Code.E_MISSING_FIELD, f"{path}.{CHANNELS_FROM}")
    if CHANNELS_FROM in table:
        value = table[CHANNELS_FROM]
        if not _is_text(value):
            out.add(Code.E_TYPE, f"{path}.{CHANNELS_FROM}", value)
        elif value not in CHANNELS_FROM_VALUES:
            out.add(Code.E_ENUM, f"{path}.{CHANNELS_FROM}", value)
    _image_type(table, path, out)
    if RGB_HEADER_OVERRIDE in table and type(table[RGB_HEADER_OVERRIDE]) is not bool:
        out.add(Code.E_TYPE, f"{path}.{RGB_HEADER_OVERRIDE}", table[RGB_HEADER_OVERRIDE])


def _member(table: Mapping[str, object], path: str, out: Issues, *, fast: bool) -> None:
    _fields(table, MEMBER, path, out)
    _text(table, "source_repeat", path, out, filled=False)
    if "image_count" in table:
        count = table["image_count"]
        if type(count) is not int:
            out.add(Code.E_TYPE, f"{path}.image_count", count)
        elif count < 1:
            out.add(Code.E_VALUE_RANGE, f"{path}.image_count", count)
    for _, unit, unit_path in _id_tables(table, "technical_repeats", path, out, minimum=1):
        _unit(unit, unit_path, out, fast=fast)


def _repeat(table: Mapping[str, object], path: str, out: Issues, *, fast: bool) -> None:
    _fields(table, REPEAT, path, out)
    _text(table, "display_name", path, out, filled=True)
    _confirmation(table, path, out)
    for _, member, member_path in _id_tables(table, "members", path, out, minimum=1):
        _member(member, member_path, out, fast=fast)


def _channel_layout(doc: Mapping[str, object], out: Issues) -> bool:
    """Check ``channel_layout`` (section 4.3a) and return whether the file is a fast-mode file.

    A file is a fast-mode file exactly when the table is present and is a table; its absence
    means strict per-unit, and nothing here supplies it (ADR 0006).
    """
    if CHANNEL_LAYOUT not in doc:
        return False
    table = doc[CHANNEL_LAYOUT]
    if not _is_table(table):
        out.add(Code.E_TYPE, CHANNEL_LAYOUT, table)
        return False
    _fields(table, LAYOUT, CHANNEL_LAYOUT, out)
    _text_array(table, "channels", CHANNEL_LAYOUT, out, filled=False, minimum=1)
    _image_type(table, CHANNEL_LAYOUT, out)
    _date(table, "confirmed_on", CHANNEL_LAYOUT, out)
    return True


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


Check = Callable[[Mapping[str, object], str, Issues], None]


def _sections(fast: bool) -> tuple[tuple[str, int, Check], ...]:
    """Return the ID-keyed sections in file order, each with its minimum size and check."""
    return (
        ("conditions", 1, _condition),
        ("channels", 1, _channel),
        ("repeats", 1, partial(_repeat, fast=fast)),
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
    fast = _channel_layout(doc, out)
    for key, minimum, check in _sections(fast):
        for _, table, path in _id_tables(doc, key, "", out, minimum=minimum):
            check(table, path, out)
    _used_ids(doc, out)
    return out
