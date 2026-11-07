"""Warnings of a valid description (experiment-v1 sections 4.3, 4.4 and 5).

Reported only for a file without errors, and never blocking.
"""

from __future__ import annotations

from collections.abc import Mapping

from .description_report import Issues, key_path
from .description_rules import Code
from .description_walk import Table, units


def check_warnings(doc: Mapping[str, object]) -> Issues:
    """Return the warnings of a document that passed all five phases."""
    out = Issues()
    used = {channel for _, _, _, unit in units(doc) for channel in unit["channels"]}
    for identifier in doc["channels"]:
        if identifier not in used:
            out.add(Code.W_UNUSED_CHANNEL, key_path("channels", identifier), identifier)
    conditions = set(doc["conditions"])
    members_of: dict[str, set[str]] = {
        repeat: set(table["members"]) for repeat, table in doc["repeats"].items()
    }
    for repeat, present in members_of.items():
        if present != conditions:
            missing = sorted(conditions - present)
            out.add(Code.W_REPEAT_MISSING_CONDITIONS, key_path("repeats", repeat), missing)
    _unpaired(doc["conditions"], members_of, out)
    return out


def _unpaired(conditions: Table, members_of: Mapping[str, set[str]], out: Issues) -> None:
    """Warn for a condition and its reference that share no repeat (they cannot be paired)."""
    for identifier, table in conditions.items():
        reference = table.get("reference")
        if reference is None:
            continue
        if not any(
            identifier in present and reference in present for present in members_of.values()
        ):
            out.add(Code.W_REFERENCE_UNPAIRED, key_path("conditions", identifier), reference)
