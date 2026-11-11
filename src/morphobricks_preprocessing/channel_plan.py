"""Which channels each image of a described run has, by canonical ID (Spec Stage 3, S3.3).

From Stage 3 the imaging units of one experiment may list different channels (experiment-v1
section 4.4: per-unit ordered lists of canonical channel IDs; ADR 0003). An image's channels
are its unit's confirmed list, entry ``i`` naming source channel ``C<i>``; the same ID is the
same stain in every unit, and an ID a unit does not list is **absent** from its images (blank
in ``plane_histograms.csv`` version 5, never zero). Nothing here reads a header or a pixel or
guesses a channel: an image whose folders name no declared unit has no channels here, and the
run refuses it (``R_UNIT_NOT_DECLARED``, :mod:`.physical_check`).

:func:`channel_coverage` records, per biological repeat and channel ID, whether every imaging
unit of the repeat has the channel (``full``), some do (``partial``) or none does
(``unavailable``), with the units that lack it (S3.3: "record per-unit and per-repeat channel
coverage; scope partial repeats explicitly"). It classifies; it refuses nothing: a request that
needs a channel some of its scope lacks is refused by its consumer (``A_SCOPE_INCOMPLETE``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .description import Description, Unit

FULL = "full"
PARTIAL = "partial"
UNAVAILABLE = "unavailable"


def declared_channel_ids(description: Description) -> tuple[str, ...]:
    """Return every channel ID the description declares, sorted (the table's header IDs).

    These are the ``[channels]`` entries, also one no unit lists (``W_UNUSED_CHANNEL``): the
    header of ``plane_histograms.csv`` lists every channel of the description (ADR 0003 HV1).
    """
    return tuple(sorted(description.channels))


def units_by_folders(description: Description) -> dict[tuple[str, str, str], Unit]:
    """Return each declared unit by its source folder labels (condition, repeat, technical)."""
    found: dict[tuple[str, str, str], Unit] = {}
    for repeat in description.repeats.values():
        for condition_id, member in repeat.members.items():
            folder = description.conditions[condition_id].source_folder
            for unit in member.technical_repeats.values():
                found[(folder, member.source_repeat, unit.source_folder)] = unit
    return found


def file_channel_ids(
    description: Description, images: Iterable[tuple[tuple[str, str, str], str]]
) -> dict[str, tuple[str, ...]]:
    """Return the channel IDs of each image, in source order, from its unit's confirmed list.

    Parameters
    ----------
    description
        The published description.
    images
        ``(folder labels, relative path)`` of each image.

    Returns
    -------
    dict
        ``{relative path: channel IDs}``; an image whose folders name no declared unit is
        left out (never guessed).
    """
    units = units_by_folders(description)
    return {rel: units[labels].channels for labels, rel in images if labels in units}


def channel_coverage(description: Description) -> dict[str, dict[str, dict[str, object]]]:
    """Classify every biological repeat for every declared channel ID.

    Returns
    -------
    dict
        ``{repeat ID: {channel ID: {"status", "units_with", "units", "lacking"}}}``, sorted:
        ``status`` is ``full``, ``partial`` or ``unavailable``; ``units_with`` of ``units``
        imaging units list the channel; ``lacking`` names the others as
        ``condition/technical repeat`` IDs, sorted.
    """
    out: dict[str, dict[str, dict[str, object]]] = {}
    for repeat_id, repeat in sorted(description.repeats.items()):
        units = [
            (f"{condition_id}/{unit_id}", unit.channels)
            for condition_id, member in sorted(repeat.members.items())
            for unit_id, unit in sorted(member.technical_repeats.items())
        ]
        out[repeat_id] = {
            channel: _coverage(channel, units) for channel in declared_channel_ids(description)
        }
    return out


def _coverage(channel: str, units: list[tuple[str, tuple[str, ...]]]) -> dict[str, object]:
    """Return one repeat's coverage of one channel."""
    lacking = sorted(name for name, channels in units if channel not in channels)
    have = len(units) - len(lacking)
    status = FULL if not lacking else UNAVAILABLE if have == 0 else PARTIAL
    return {"status": status, "units_with": have, "units": len(units), "lacking": lacking}


def partial_repeats(coverage: Mapping[str, Mapping[str, Mapping[str, object]]]) -> list[str]:
    """Return ``"<repeat>: <channel> in k of n acquisitions"`` for every partial coverage."""
    return [
        f"{repeat}: {channel} in {entry['units_with']} of {entry['units']} acquisitions"
        for repeat, channels in coverage.items()
        for channel, entry in channels.items()
        if entry["status"] == PARTIAL
    ]


__all__ = [
    "FULL",
    "PARTIAL",
    "UNAVAILABLE",
    "channel_coverage",
    "declared_channel_ids",
    "file_channel_ids",
    "partial_repeats",
    "units_by_folders",
]
