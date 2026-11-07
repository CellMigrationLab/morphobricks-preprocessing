"""The canonical writer of ``morphobricks.toml`` and its section digests (experiment-v1 section 9).

Writers produce deterministic bytes: UTF-8, no byte-order mark, LF, a final newline, no
comments. Order: ``schema``, ``schema_version``, ``conditions``, ``channels``, ``repeats``,
``masks`` (omitted when empty), ``used_ids``; entries of each table sorted by key in code point
order; fields in the order of the Spec's tables (``description_rules``); every table a
standard ``[header]``; arrays inline; keys bare when they match ``[A-Za-z0-9_-]+``, else
basic quoted strings; strings basic strings with the minimal escapes. Nothing is normalized,
defaulted or added: ``confirmed_on`` is written as the model holds it (section 4.4a).

A section digest is the SHA-256 of that section's tables written as above, with every
``display_name``, ``target`` and ``treatment_key`` omitted (section 9.3). ``used_ids`` is not a
section. Section 9.3 does not define the byte form of a section alone: here it is exactly the
text the file holds for the section (its table blocks, each followed by a blank line), nothing
else; an empty section is the empty byte string.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping

from .description import USED_KINDS, Description
from .description_rules import CHANNEL, CONDITION, MASK, MEMBER, REPEAT, UNIT

BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
DESCRIPTIVE = frozenset({"display_name", "target", "treatment_key"})
"""The fields no digest covers (experiment-v1 sections 4.2a and 9.3)."""
UNORDERED = frozenset({"header_names", "source_channels"})
"""Arrays whose order is not semantic: always written sorted (section 9.1)."""
SECTIONS = ("conditions", "channels", "repeats", "masks")
_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


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
    for name in order:
        value = getattr(item, name, None)
        if value is None or isinstance(value, Mapping):  # a table is its own block
            continue
        if not descriptive and name in DESCRIPTIVE:
            continue
        if name in UNORDERED:
            value = sorted(value)
        lines.append(f"{name} = {toml_value(value)}")
    lines.append("")
    return lines


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
    else, so a model that the reader would refuse is written as it is (the caller reads the
    bytes back with the strict reader before publishing).
    """
    lines = [
        f"schema = {toml_string(description.schema)}",
        f"schema_version = {description.schema_version}",
        "",
    ]
    for name in SECTIONS:
        lines += _section(name, description, True)
    lines.append("[used_ids]")
    for kind in USED_KINDS:
        lines.append(f"{kind} = {toml_value(sorted(description.used_ids[kind]))}")
    return _text(lines)


def section_digests(description: Description) -> dict[str, str]:
    """Return the SHA-256 (hex) of each of ``conditions``, ``channels``, ``repeats``, ``masks``.

    A change to a display name, a target, a treatment key or ``used_ids`` changes none of
    them; ``source_folder`` is part of ``conditions`` and ``repeats``.
    """
    return {
        name: hashlib.sha256(_text(_section(name, description, False))).hexdigest()
        for name in SECTIONS
    }
