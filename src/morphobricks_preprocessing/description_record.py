"""The ``experiment_description`` entry of the run manifest (experiment-v1 sections 4.5a, 4.6, 9.3).

A run with a published description records, in its manifest, the file's identity (the SHA-256
of the exact bytes the run read: provenance, never a cache key), the section digests consumers
key their products on, which units' image types were checked against a header, the status of
every declared mask file, and the records of publication (re-confirmations) copied unchanged
from ``morphobricks.records.jsonl``. Every run has a published description (Spec ADR 0002 AR10).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from .channel_plan import channel_coverage
from .description import Description
from .description_unnamed import FIELD, checked_items, unnamed_items
from .description_write import section_digests

KEY = "experiment_description"
"""The manifest key of the entry."""
SHA256 = re.compile(r"[0-9a-f]{64}")
_FIELDS = (
    "sha256",
    "schema_version",
    "section_digests",
    "units",
    "masks",
    "records",
    "channel_coverage",
)


def unit_records(
    description: Description,
    verified: Mapping[tuple[str, str, str], bool],
    unnamed: Mapping[tuple[str, str, str], Mapping[str, tuple[int, ...]]] | None = None,
) -> list[dict[str, object]]:
    """Return every declared unit with whether its image type was checked against a header.

    ``verified`` maps ``(condition folder, repeat folder, technical repeat folder)`` to the
    check's outcome; a unit it does not name is not verified. It is False for a unit whose
    format states no colour interpretation (OME-Zarr: the declaration stands on the user's
    confirmation) and for a unit with no readable image.

    ``unnamed`` maps the same keys to the images whose header names not every channel and the
    positions without a name (:mod:`.description_unnamed`); a unit with such images gets the
    list under ``images_without_header_channel_names``, any other unit has no such key.
    """
    out = []
    for repeat_id, repeat in sorted(description.repeats.items()):
        for condition_id, member in sorted(repeat.members.items()):
            for unit_id, unit in sorted(member.technical_repeats.items()):
                key = (
                    description.conditions[condition_id].source_folder,
                    member.source_repeat,
                    unit.source_folder,
                )
                record: dict[str, object] = {
                    "repeat": repeat_id,
                    "condition": condition_id,
                    "technical_repeat": unit_id,
                    "channels": list(unit.channels),
                    "image_type_header_verified": verified.get(key, False),
                }
                if (unnamed or {}).get(key):
                    record[FIELD] = unnamed_items(unnamed[key], len(unit.channels))
                out.append(record)
    return out


def description_record(
    description: Description,
    sha256: str,
    units: list[dict[str, object]],
    masks: dict[str, dict],
    records: tuple[dict, ...],
) -> dict[str, object]:
    """Return the manifest entry for a description read from the bytes with this SHA-256.

    ``channel_coverage`` classifies every biological repeat for every declared channel ID as
    ``full``, ``partial`` or ``unavailable``, with the units that lack it
    (:func:`.channel_plan.channel_coverage`; Stage 3, S3.3); each unit record lists its
    channels in source order.
    """
    return {
        "sha256": sha256,
        "schema_version": description.schema_version,
        "section_digests": section_digests(description),
        "units": units,
        "masks": masks,
        "records": list(records),
        "channel_coverage": channel_coverage(description),
    }


def recorded_description(value: object) -> dict | None:
    """Return the manifest's entry as read (None when absent), refusing a malformed one.

    Raises
    ------
    ValueError
        When the entry is not an object with the recorded fields and types.
    """
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(_FIELDS):
        raise ValueError(f"{KEY} must hold exactly {', '.join(_FIELDS)}")
    sha = value["sha256"]
    if not isinstance(sha, str) or not SHA256.fullmatch(sha):
        raise ValueError(f"{KEY}.sha256 is not a SHA-256")
    if not isinstance(value["schema_version"], int) or isinstance(value["schema_version"], bool):
        raise ValueError(f"{KEY}.schema_version is not a whole number")
    for name, kind in (
        ("section_digests", dict),
        ("masks", dict),
        ("units", list),
        ("records", list),
        ("channel_coverage", dict),
    ):
        if not isinstance(value[name], kind):
            raise ValueError(f"{KEY}.{name} is not a {kind.__name__}")
    for index, unit in enumerate(value["units"]):
        if isinstance(unit, dict) and FIELD in unit:
            checked_items(unit[FIELD], f"{KEY}.units[{index}]")
    return value
