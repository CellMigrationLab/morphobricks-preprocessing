"""The canonical writer of ``morphobricks.toml`` and its section digests (experiment-v1 section 9).

Writers produce deterministic bytes: UTF-8, no byte-order mark, LF, a final newline, no
comments. Order: ``schema``, ``schema_version``, ``conditions``, ``channels``,
``channel_layout`` (fast mode only, ADR 0006), ``repeats``, ``masks`` (omitted when empty),
``used_ids``; entries of each table sorted by key in code point order; fields in the order of
the Spec's tables (``description_rules``); every table a standard ``[header]``; arrays inline;
keys bare when they match ``[A-Za-z0-9_-]+``, else basic quoted strings; strings basic strings
with the minimal escapes. Nothing is normalized,
defaulted or added: ``confirmed_on`` is written as the model holds it (section 4.4a).

A section digest is the SHA-256 of that section's tables written as above, with every
``display_name``, ``target`` and ``treatment_key`` omitted (section 9.3), and with
``channels_from`` omitted too (it records how a unit's list was confirmed, not what it is; ADR
0006). ``used_ids`` and ``channel_layout`` are not sections. The bytes of a section are
exactly the text the file holds for it (its table blocks, each followed by a blank line),
nothing else; an empty section is the empty byte string.

Both refuse a model holding a value of a type the strict reader refuses (issue #405): the
values they would write are checked by the reader's own phases 2 and 3 first, and every
``E_TYPE`` is a :class:`DescriptionTypeError` naming the field and the type found. Nothing is
dropped, converted or sorted into another type: a mapping in a field is never taken for a child
table, and a text is never written as the array of its characters.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping

from .description import USED_KINDS, Description, DescriptionError
from .description_read import STRUCTURE_PHASES
from .description_report import DescriptionIssue
from .description_rules import (
    CHANNEL,
    CHANNEL_LAYOUT,
    CHANNELS_FROM,
    CONDITION,
    LAYOUT,
    MASK,
    MEMBER,
    REPEAT,
    UNIT,
    Code,
)
from .type_names import type_name

BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
DESCRIPTIVE = frozenset({"display_name", "target", "treatment_key"})
"""The fields no digest covers (experiment-v1 sections 4.2a and 9.3)."""
UNDIGESTED = DESCRIPTIVE | {CHANNELS_FROM}
"""Also omitted from every digest: confirmation provenance, never what a list is (ADR 0006)."""
UNORDERED = frozenset({"header_names", "source_channels"})
"""Arrays whose order is not semantic: always written sorted (section 9.1)."""
SECTIONS = ("conditions", "channels", "repeats", "masks")
CHILD_TABLES = frozenset({"members", "technical_repeats"})
"""Fields that are tables of their own: written as their own blocks, never as a value."""
_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


class DescriptionTypeError(DescriptionError, TypeError):
    """Raised when a model holds a value the strict reader would refuse as ``E_TYPE``.

    ``issues`` are the reader's ``E_TYPE`` issues of the values the writer would write (code,
    table path, value), all of them; nothing was written.
    """

    def __init__(self, issues: tuple[DescriptionIssue, ...], phase: int) -> None:
        super().__init__(issues, phase)
        found = "; ".join(f"{i.path} holds a value of type {type_name(i.value)}" for i in issues)
        self.args = (
            f"the description cannot be written: {found}, a type the strict reader refuses "
            "(E_TYPE). Nothing was written.",
        )


def toml_key(key: str) -> str:
    """Return a table or dotted key as the canonical writer spells it."""
    return key if BARE_KEY.fullmatch(key) else toml_string(key)


def toml_string(text: str) -> str:
    """Return a TOML basic string, escaping only what must be (control characters by number)."""
    out = []
    for char in text:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def toml_value(value: object) -> str:
    """Return a bool, int, str or list of them as inline TOML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise TypeError(f"cannot write a {type(value).__name__} to the description")


def _block(header: str, item: object, order: Iterable[str], *, descriptive: bool) -> list[str]:
    """Return one ``[header]`` block: the fields of ``item`` set (not None), in ``order``."""
    lines = [f"[{header}]"]
    for name, value in _table(item, order).items():
        if not descriptive and name in UNDIGESTED:
            continue
        if name in UNORDERED:
            value = sorted(value)
        lines.append(f"{name} = {toml_value(value)}")
    lines.append("")
    return lines


def _table(item: object, order: Iterable[str]) -> dict[str, object]:
    """Return the fields of ``item`` that are set (not None), in ``order``, as the file holds them.

    A child table (:data:`CHILD_TABLES`) is left out: it is its own block. An array the model
    holds as a tuple is the file's list; nothing else is changed, so a value of a wrong type
    stays one for :func:`check_types`.
    """
    out: dict[str, object] = {}
    for name in order:
        value = getattr(item, name, None)
        if value is not None and name not in CHILD_TABLES:
            out[name] = _array(value)
    return out


def _array(value: object) -> object:
    """Return a tuple as the file's list (TOML has one array type); anything else as it is."""
    return list(value) if type(value) is tuple else value


def _tables(items: object, build: Callable[[object], dict[str, object]]) -> object:
    """Return an ID-keyed table of models as the document's dict; anything else as it is."""
    if isinstance(items, Mapping):
        return {key: build(value) for key, value in items.items()}
    return items


def _member_table(member: object) -> dict[str, object]:
    units = _tables(
        getattr(member, "technical_repeats", None), lambda unit: _table(unit, UNIT.fields)
    )
    return {**_table(member, MEMBER.fields), "technical_repeats": units}


def _repeat_table(repeat: object) -> dict[str, object]:
    members = _tables(getattr(repeat, "members", None), _member_table)
    return {**_table(repeat, REPEAT.fields), "members": members}


def _document(description: Description) -> dict[str, object]:
    """Return the document the writer would write for ``description``, before any text."""
    doc: dict[str, object] = {
        "schema": description.schema,
        "schema_version": description.schema_version,
        "conditions": _tables(description.conditions, lambda c: _table(c, CONDITION.fields)),
        "channels": _tables(description.channels, lambda c: _table(c, CHANNEL.fields)),
        "repeats": _tables(description.repeats, _repeat_table),
        "masks": _tables(description.masks, lambda m: _table(m, MASK.fields)),
        "used_ids": {kind: _array(description.used_ids[kind]) for kind in USED_KINDS},
    }
    if description.channel_layout is not None:
        doc[CHANNEL_LAYOUT] = _table(description.channel_layout, LAYOUT.fields)
    return doc


def check_types(description: Description) -> None:
    """Refuse a model holding a value of a type the strict reader refuses (issue #405).

    The values the writer would write go through the reader's own phases 2 and 3
    (:data:`.description_read.STRUCTURE_PHASES`); only their ``E_TYPE`` issues refuse here,
    since a wrong type is what the writer would drop or convert. Every other refusal is the
    strict reader's, on the written bytes.

    Raises
    ------
    DescriptionTypeError
        With every ``E_TYPE`` issue of the first phase that has one.
    """
    doc = _document(description)
    for phase, check in STRUCTURE_PHASES:
        wrong = tuple(i for i in check(doc).items if i.code is Code.E_TYPE)
        if wrong:
            raise DescriptionTypeError(wrong, phase)


def _keyed(base: str, key: str) -> str:
    return f"{base}.{toml_key(key)}"


def _flat(
    base: str, table: Mapping[str, object], order: Iterable[str], descriptive: bool
) -> list[str]:
    lines: list[str] = []
    for key in sorted(table):
        lines += _block(_keyed(base, key), table[key], order, descriptive=descriptive)
    return lines


def _repeats(description: Description, descriptive: bool) -> list[str]:
    """Return the repeat, member and unit blocks, each level sorted by key."""
    lines: list[str] = []
    for key in sorted(description.repeats):
        repeat = description.repeats[key]
        head = _keyed("repeats", key)
        lines += _block(head, repeat, REPEAT.fields, descriptive=descriptive)
        for condition in sorted(repeat.members):
            member = repeat.members[condition]
            mhead = _keyed(f"{head}.members", condition)
            lines += _block(mhead, member, MEMBER.fields, descriptive=descriptive)
            lines += _flat(
                f"{mhead}.technical_repeats", member.technical_repeats, UNIT.fields, descriptive
            )
    return lines


def _section(name: str, description: Description, descriptive: bool) -> list[str]:
    builders: dict[str, Callable[[], list[str]]] = {
        "conditions": lambda: _flat(
            "conditions", description.conditions, CONDITION.fields, descriptive
        ),
        "channels": lambda: _flat("channels", description.channels, CHANNEL.fields, descriptive),
        "repeats": lambda: _repeats(description, descriptive),
        "masks": lambda: _flat("masks", description.masks, MASK.fields, descriptive),
    }
    return builders[name]()


def _text(lines: list[str]) -> bytes:
    return "".join(f"{line}\n" for line in lines).encode("utf-8")


def canonical_bytes(description: Description) -> bytes:
    """Return the canonical bytes of ``description`` (experiment-v1 section 9.1).

    The writer is given a model, never a document: it writes what the model holds and nothing
    else. A value of a type the reader refuses is refused here (:func:`check_types`), since
    writing it would drop or convert it; any other model the reader would refuse is written as
    it is (the caller reads the bytes back with the strict reader before publishing).

    Raises
    ------
    DescriptionTypeError
        When a value has a type the strict reader refuses (``E_TYPE``); nothing is written.
    """
    check_types(description)
    lines = [
        f"schema = {toml_string(description.schema)}",
        f"schema_version = {description.schema_version}",
        "",
    ]
    for name in SECTIONS:
        lines += _section(name, description, True)
        if name == "channels" and description.channel_layout is not None:
            lines += _block(
                CHANNEL_LAYOUT, description.channel_layout, LAYOUT.fields, descriptive=True
            )
    lines.append("[used_ids]")
    for kind in USED_KINDS:
        lines.append(f"{kind} = {toml_value(sorted(description.used_ids[kind]))}")
    return _text(lines)


def section_digests(description: Description) -> dict[str, str]:
    """Return the SHA-256 (hex) of each of ``conditions``, ``channels``, ``repeats``, ``masks``.

    A change to a display name, a target, a treatment key or ``used_ids`` changes none of
    them; ``source_folder`` is part of ``conditions`` and ``repeats``.

    Raises
    ------
    DescriptionTypeError
        As :func:`canonical_bytes` does.
    """
    check_types(description)
    return {
        name: hashlib.sha256(_text(_section(name, description, False))).hexdigest()
        for name in SECTIONS
    }
