"""Walk the members and imaging units of a document that passed the structure phase."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from .description_report import key_path

Table = Mapping[str, object]


def member_path(repeat: str, condition: str) -> str:
    """Return the table path of ``repeats.<repeat>.members.<condition>``."""
    return key_path(key_path(key_path("repeats", repeat), "members"), condition)


def unit_path(repeat: str, condition: str, unit: str) -> str:
    """Return the table path of one imaging unit (a technical repeat of a member)."""
    return key_path(key_path(member_path(repeat, condition), "technical_repeats"), unit)


def members(doc: Table) -> Iterator[tuple[str, str, Table]]:
    """Yield (repeat ID, condition ID, member table) for every member of every repeat."""
    for repeat, table in doc["repeats"].items():
        for condition, member in table["members"].items():
            yield repeat, condition, member


def units(doc: Table) -> Iterator[tuple[str, str, str, Table]]:
    """Yield (repeat ID, condition ID, technical repeat ID, unit table) for every unit."""
    for repeat, condition, member in members(doc):
        for unit, table in member["technical_repeats"].items():
            yield repeat, condition, unit, table
