"""Validation phase 4, identity (experiment-v1 sections 3 and 6).

The Label rules (the reader's own ``label_refused``, decision D-e), the Token rule, the reserved
list, NFC, ``single`` and the case-collision scopes. Each broken rule has its own code and every
rule an identity breaks is reported (one code per broken rule). Nothing is trimmed, normalized or
repaired: the NFC form of a value is a comparison key only and never leaves this module.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping

from .description_report import Issues, key_path
from .description_rules import OBJECT_SET_PART, RESERVED, SINGLE, TOKEN, Code, label_refused
from .description_walk import Table, member_path, members, unit_path

USED_KINDS = ("conditions", "channels", "repeats", "technical_repeats")


def nfc(value: str) -> str:
    """Return the NFC form of ``value`` (a comparison key, never stored)."""
    return unicodedata.normalize("NFC", value)


class Identities:
    """The identity checks of one document, and the values of each case-collision scope."""

    def __init__(self, out: Issues) -> None:
        self.out = out
        self.scopes: dict[str, list[tuple[str, str]]] = {}

    def label(self, value: str, path: str) -> None:
        """Apply the Label rules (``E_ID_TEXT``)."""
        if label_refused(value):
            self.out.add(Code.E_ID_TEXT, path, value)

    def token(self, value: str, path: str) -> None:
        """Apply the Label rules and the Token regular expression, as two separate rules."""
        self.label(value, path)
        if not TOKEN.fullmatch(value):
            self.out.add(Code.E_ID_SYNTAX, path, value)

    def nfc(self, value: str, path: str) -> None:
        """Refuse a value that is not NFC (``E_ID_NOT_NFC``); it is never normalized."""
        if nfc(value) != value:
            self.out.add(Code.E_ID_NOT_NFC, path, value)

    def single(self, value: str, path: str) -> None:
        """Refuse ``single`` in another case (``E_SINGLE_MISUSE``)."""
        if value.casefold() == SINGLE and value != SINGLE:
            self.out.add(Code.E_SINGLE_MISUSE, path, value)

    def reserved(self, value: str, path: str, *, object_set: bool = False) -> None:
        """Refuse a name on the reserved list (and, for a mask, an ``in`` part)."""
        folded = value.casefold()
        if folded in RESERVED or (object_set and OBJECT_SET_PART in folded.split("_")):
            self.out.add(Code.E_ID_RESERVED, path, value)

    def label_id(self, value: str, path: str, scope: str) -> None:
        """Check an identity of the Label kind and enter it in its collision scope."""
        self.label(value, path)
        self.nfc(value, path)
        self.single(value, path)
        self.scopes.setdefault(scope, []).append((value, path))

    def collisions(self) -> None:
        """Report identities of one scope that differ after NFC but not by case folding."""
        for items in self.scopes.values():
            spellings: dict[str, set[str]] = {}
            for value, _ in items:
                spellings.setdefault(nfc(value).casefold(), set()).add(nfc(value))
            for value, path in items:
                if len(spellings[nfc(value).casefold()]) > 1:
                    self.out.add(Code.E_ID_CASE_COLLISION, path, value)


def _conditions(doc: Table, ids: Identities) -> None:
    """Check condition IDs and folders, and the ``single`` rule of conditions."""
    count = len(doc["conditions"])
    for identifier, table in doc["conditions"].items():
        path = key_path("conditions", identifier)
        ids.label_id(identifier, path, "conditions")
        ids.label_id(table["source_folder"], f"{path}.source_folder", "condition_folders")
        folder = table["source_folder"]
        if (identifier == SINGLE) != (folder == SINGLE):  # `single` iff no condition folder level
            ids.out.add(Code.E_SINGLE_MISUSE, f"{path}.source_folder", folder)
        elif folder == SINGLE and count > 1:
            message = "single is not the only condition"
            ids.out.add(Code.E_SINGLE_MISUSE, f"{path}.source_folder", message)
        if "reference" in table:  # a reference is checked for NFC only, then resolved (phase 5)
            ids.nfc(table["reference"], f"{path}.reference")


def _channels(doc: Table, ids: Identities) -> None:
    for identifier in doc["channels"]:
        path = key_path("channels", identifier)
        ids.token(identifier, path)
        ids.nfc(identifier, path)
        ids.single(identifier, path)
        ids.reserved(identifier, path)
        ids.scopes.setdefault("channels", []).append((identifier, path))


def _unit(
    ids: Identities, repeat: str, condition: str, unit: str, table: Table, count: int
) -> None:
    path = unit_path(repeat, condition, unit)
    ids.label_id(unit, path, "technical_repeats")
    ids.label_id(table["source_folder"], f"{path}.source_folder", "technical_folders")
    if unit == SINGLE and count > 1:
        ids.out.add(Code.E_SINGLE_MISUSE, path, "single is not the only technical repeat")
    if (unit == SINGLE) != (table["source_folder"] == SINGLE):
        ids.out.add(Code.E_SINGLE_MISUSE, f"{path}.source_folder", table["source_folder"])
    for index, channel in enumerate(table["channels"]):
        ids.nfc(channel, f"{path}.channels[{index}]")


def _repeats(doc: Table, ids: Identities) -> None:
    """Check repeat IDs, source repeats, technical repeats and the ``single`` rules."""
    for identifier in doc["repeats"]:
        ids.label_id(identifier, key_path("repeats", identifier), "repeats")
    by_condition: dict[str, list[tuple[str, str]]] = {}
    for repeat, condition, member in members(doc):
        path = member_path(repeat, condition)
        ids.nfc(condition, path)  # a member key is a reference to a condition ID
        ids.label_id(member["source_repeat"], f"{path}.source_repeat", "source_repeats")
        by_condition.setdefault(condition, []).append((member["source_repeat"], path))
        for unit, table in member["technical_repeats"].items():
            _unit(ids, repeat, condition, unit, table, len(member["technical_repeats"]))
    for entries in by_condition.values():
        if len(entries) > 1:  # "single" must be the only member of its condition
            for source, path in entries:
                if source == SINGLE:
                    message = "single is not the only member"
                    ids.out.add(Code.E_SINGLE_MISUSE, f"{path}.source_repeat", message)


def _masks(doc: Table, ids: Identities) -> None:
    for identifier, table in doc.get("masks", {}).items():
        path = key_path("masks", identifier)
        ids.token(identifier, path)
        ids.nfc(identifier, path)
        ids.single(identifier, path)
        ids.reserved(identifier, path, object_set=True)
        ids.scopes.setdefault("masks", []).append((identifier, path))
        ids.label_id(table["source_type"], f"{path}.source_type", "mask_types")
        for index, channel in enumerate(table.get("source_channels", [])):
            ids.nfc(channel, f"{path}.source_channels[{index}]")


def _used_ids(doc: Table, ids: Identities) -> None:
    """Entries follow the identity rules of their kind and share its collision scope.

    The reserved list of section 3.2 applies to ``used_ids.channels`` entries like a declared
    channel ID (owner decision UR1, Spec 4.9): a listed ID reserves it. The registry has no list
    for mask IDs, so the other three kinds are unchanged.
    """
    for kind in USED_KINDS:
        for index, value in enumerate(doc["used_ids"][kind]):
            path = f"used_ids.{kind}[{index}]"
            if kind == "channels":
                ids.token(value, path)
                ids.reserved(value, path)
            else:
                ids.label(value, path)
            ids.nfc(value, path)
            ids.single(value, path)
            ids.scopes.setdefault(kind, []).append((value, path))


def check_identity(doc: Mapping[str, object]) -> Issues:
    """Return every phase-4 error of a document that passed the structure phase."""
    out = Issues()
    ids = Identities(out)
    _conditions(doc, ids)
    _channels(doc, ids)
    _repeats(doc, ids)
    _masks(doc, ids)
    _used_ids(doc, ids)
    ids.collisions()
    return out
