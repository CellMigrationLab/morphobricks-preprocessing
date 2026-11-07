"""Validation phase 5, semantics (experiment-v1 sections 4, 5 and 6).

Cross-references, the RGB rules, display-name uniqueness, the ``used_ids`` registry, one folder
one ID, source repeats and the reference graph. References are resolved exactly, code point by
code point: no case folding, no NFC, no display name ever stands for an ID. It reads the parsed
document only and reports every error of the phase.
"""

from __future__ import annotations

from collections.abc import Mapping

from .description_identity import USED_KINDS, nfc
from .description_report import Issues, key_path
from .description_rules import RGB_TOKENS, Code
from .description_walk import Table, member_path, members, unit_path, units


def _unit_channels(doc: Table, out: Issues) -> None:
    """Check that unit channels are declared, not repeated, and follow the RGB rules (4.4b)."""
    channels = doc["channels"]
    for repeat, condition, unit, table in units(doc):
        path = unit_path(repeat, condition, unit)
        listed = table["channels"]
        seen: set[str] = set()
        for index, channel in enumerate(listed):
            if channel not in channels:
                out.add(Code.E_UNKNOWN_CHANNEL, f"{path}.channels[{index}]", channel)
            if channel in seen:
                out.add(Code.E_DUPLICATE_CHANNEL_IN_UNIT, f"{path}.channels[{index}]", channel)
            seen.add(channel)
        if table.get("image_type") == "rgb":
            if listed != list(RGB_TOKENS):
                out.add(Code.E_RGB_UNIT_CHANNELS, f"{path}.channels", listed)
            continue
        for index, channel in enumerate(listed):
            if channel.casefold() in {token.casefold() for token in RGB_TOKENS}:
                out.add(Code.E_RGB_TOKEN_MISUSE, f"{path}.channels[{index}]", channel)


def _channel_tables(doc: Table, out: Issues) -> None:
    """Header names: no alias for an RGB token, no duplicate (equal after NFC)."""
    rgb = {token.casefold() for token in RGB_TOKENS}
    for identifier, table in doc["channels"].items():
        names = table.get("header_names", [])
        path = f"{key_path('channels', identifier)}.header_names"
        if identifier.casefold() in rgb and names:
            out.add(Code.E_RGB_ALIAS, path, names)
        if len({nfc(name) for name in names}) != len(names):
            out.add(Code.E_DUPLICATE_VALUE, path, names)


def _mask_channels(doc: Table, out: Issues) -> None:
    for identifier, table in doc.get("masks", {}).items():
        listed = table.get("source_channels", [])
        path = key_path(key_path("masks", identifier), "source_channels")
        for index, channel in enumerate(listed):
            if channel not in doc["channels"]:
                out.add(Code.E_UNKNOWN_CHANNEL, f"{path}[{index}]", channel)
        if len(set(listed)) != len(listed):
            out.add(Code.E_DUPLICATE_VALUE, path, listed)


def display_key(name: str) -> str:
    """Return the comparison key of a display name, NFC of the case fold of the NFC form."""
    return nfc(nfc(name).casefold())


def _display_names(doc: Table, out: Issues) -> None:
    """Display names are unique within one kind (the key is never stored or looked up)."""
    kinds: dict[str, list[tuple[str, str, str]]] = {}
    for kind in ("conditions", "channels", "repeats", "masks"):
        for identifier, table in doc.get(kind, {}).items():
            if "display_name" in table:
                path = f"{key_path(kind, identifier)}.display_name"
                kinds.setdefault(kind, []).append((identifier, table["display_name"], path))
    for repeat, condition, unit, table in units(doc):
        if "display_name" in table:
            path = f"{unit_path(repeat, condition, unit)}.display_name"
            kinds.setdefault("technical_repeats", []).append((unit, table["display_name"], path))
    for items in kinds.values():
        owners: dict[str, set[str]] = {}
        for owner, name, _path in items:
            owners.setdefault(display_key(name), set()).add(owner)
        for _owner, name, path in items:
            if len(owners[display_key(name)]) > 1:
                out.add(Code.E_DISPLAY_NAME_COLLISION, path, name)


def _used_ids(doc: Table, out: Issues) -> None:
    """No duplicate entry, and every declared ID is listed."""
    declared = {
        "conditions": set(doc["conditions"]),
        "channels": set(doc["channels"]),
        "repeats": set(doc["repeats"]),
        "technical_repeats": {unit for _, _, unit, _ in units(doc)},
    }
    for kind in USED_KINDS:
        listed = doc["used_ids"][kind]
        if len(set(listed)) != len(listed):
            out.add(Code.E_DUPLICATE_VALUE, f"used_ids.{kind}", listed)
        for identifier in sorted(declared[kind] - set(listed)):
            out.add(Code.E_USED_ID_MISSING, f"used_ids.{kind}", identifier)


def _source_folders(doc: Table, out: Issues) -> None:
    """One folder, one ID (section 4.6a): conditions, and technical repeats over the file."""
    conditions: dict[str, list[tuple[str, str]]] = {}
    for identifier, table in doc["conditions"].items():
        path = f"{key_path('conditions', identifier)}.source_folder"
        conditions.setdefault(table["source_folder"], []).append((identifier, path))
    folders: dict[str, dict[str, str]] = {}
    ids_of: dict[str, dict[str, str]] = {}
    for repeat, condition, unit, table in units(doc):
        path = f"{unit_path(repeat, condition, unit)}.source_folder"
        folders.setdefault(table["source_folder"], {}).setdefault(unit, path)
        ids_of.setdefault(unit, {}).setdefault(table["source_folder"], path)
    for folder, entries in conditions.items():
        if len({identifier for identifier, _ in entries}) > 1:
            for _, path in entries:
                out.add(Code.E_SOURCE_FOLDER_REUSED, path, folder)
    for folder, by_id in folders.items():
        if len(by_id) > 1:
            for path in by_id.values():
                out.add(Code.E_SOURCE_FOLDER_REUSED, path, folder)
    for by_folder in ids_of.values():
        if len(by_folder) > 1:
            for folder, path in by_folder.items():
                out.add(Code.E_SOURCE_FOLDER_CONFLICT, path, folder)


def _repeat_membership(doc: Table, out: Issues) -> None:
    """Check the members: declared conditions, source repeats, and repeat IDs.

    Members name declared conditions, each condition has a member, a source repeat is in one
    repeat, and a repeat ID never shadows another repeat's source folder.
    """
    listed = list(members(doc))
    for repeat, condition, _ in listed:
        if condition not in doc["conditions"]:
            out.add(Code.E_UNKNOWN_CONDITION, member_path(repeat, condition), condition)
    with_members = {condition for _, condition, _ in listed}
    for condition in doc["conditions"]:
        if condition not in with_members:
            out.add(Code.E_CONDITION_WITHOUT_MEMBERS, key_path("conditions", condition), condition)
    owners: dict[tuple[str, str], dict[str, str]] = {}
    for repeat, condition, member in listed:
        path = f"{member_path(repeat, condition)}.source_repeat"
        owners.setdefault((condition, member["source_repeat"]), {})[repeat] = path
    for (_, source), by_repeat in owners.items():
        if len(by_repeat) > 1:
            for path in by_repeat.values():
                out.add(Code.E_SOURCE_REPEAT_REUSED, path, source)
    for repeat in doc["repeats"]:
        for other, condition, member in listed:
            if (
                other != repeat
                and nfc(member["source_repeat"]).casefold() == nfc(repeat).casefold()
            ):
                value = (repeat, member["source_repeat"])
                out.add(Code.E_REPEAT_ID_SHADOWS_SOURCE, member_path(other, condition), value)


def _mask_types(doc: Table, out: Issues) -> None:
    by_type: dict[str, list[tuple[str, str]]] = {}
    for identifier, table in doc.get("masks", {}).items():
        path = f"{key_path('masks', identifier)}.source_type"
        by_type.setdefault(table["source_type"], []).append((identifier, path))
    for source_type, entries in by_type.items():
        if len(entries) > 1:
            for _, path in entries:
                out.add(Code.E_MASK_SOURCE_TYPE_REUSED, path, source_type)


def _references(doc: Table, out: Issues) -> None:
    """Resolve each reference exactly to another declared condition, and refuse a cycle."""
    edges: dict[str, str] = {}
    for identifier, table in doc["conditions"].items():
        if "reference" not in table:
            continue
        reference = table["reference"]
        path = f"{key_path('conditions', identifier)}.reference"
        if reference == identifier:
            out.add(Code.E_REFERENCE_SELF, path, reference)
        elif reference not in doc["conditions"]:
            out.add(Code.E_REFERENCE_UNKNOWN, path, reference)
        else:
            edges[identifier] = reference
    reported: set[frozenset[str]] = set()
    for start in edges:
        trail: list[str] = []
        current = start
        while current in edges and current not in trail:
            trail.append(current)
            current = edges[current]
        cycle = frozenset(trail[trail.index(current) :]) if current in trail else None
        if cycle and cycle not in reported:
            reported.add(cycle)
            for identifier in sorted(cycle):
                path = f"{key_path('conditions', identifier)}.reference"
                out.add(Code.E_REFERENCE_CYCLE, path, sorted(cycle))


def check_semantics(doc: Mapping[str, object]) -> Issues:
    """Return every phase-5 error of a document that passed the identity phase."""
    out = Issues()
    _repeat_membership(doc, out)
    _unit_channels(doc, out)
    _channel_tables(doc, out)
    _mask_channels(doc, out)
    _display_names(doc, out)
    _used_ids(doc, out)
    _source_folders(doc, out)
    _mask_types(doc, out)
    _references(doc, out)
    return out
