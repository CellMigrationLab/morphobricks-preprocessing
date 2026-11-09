"""The names a run's plots use: channel and replicate IDs from the published description.

A histogram table names its channels by position (``C0``, ``C1``, ...) and its images by the
folder labels (condition, biological repeat). When the run was described, the manifest records
the SHA-256 of the ``morphobricks.toml`` it read; the file in the output folder is used here
only when it still has exactly those bytes, so the IDs shown are the ones the run was made
with. Nothing is guessed: a channel position whose units do not all declare the same ID, a
repeat folder the file does not name, a changed or unreadable file, or a run without a
description all keep the table's own names (``C0``, the folder label).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .description import Description, DescriptionError, description_from_bytes
from .description_file import DESCRIPTION_FILENAME, read_file_bytes, sha256_hex
from .manifest import MANIFEST_FILENAME, ManifestFormatError, read_manifest


@dataclass(frozen=True)
class RunLabels:
    """The IDs a described run gives its channels and biological repeats.

    Attributes
    ----------
    channels
        The channel ID at each table position, or None where the description does not give
        one single ID for every unit.
    repeats
        ``(condition folder, biological-repeat folder)`` -> the replicate ID.
    """

    channels: tuple[str | None, ...] = ()
    repeats: Mapping[tuple[str, str], str] = field(default_factory=dict)

    def channel(self, index: int) -> str:
        """Return the channel's ID, or ``C<index>`` when the run does not give one."""
        known = self.channels[index] if index < len(self.channels) else None
        return known or f"C{index}"

    def repeat(self, condition: str, repeat: str) -> str:
        """Return the replicate ID of a biological-repeat folder, or the folder label."""
        return self.repeats.get((condition, repeat), repeat)

    def order(self, condition: str, repeat: str) -> tuple[str, str, str]:
        """Return the sort key of a biological-repeat folder: condition, replicate ID, folder.

        Within a condition the plots list the replicates by the ID they are shown with
        (``rep1`` before ``rep2``), not by folder name; the folder breaks a tie.
        """
        return (condition, self.repeat(condition, repeat), repeat)


NO_LABELS = RunLabels()
"""The labels of a run without a description: the table's own names."""


def labels_from_description(description: Description) -> RunLabels:
    """Return the channel and replicate IDs ``description`` declares."""
    units = [
        unit
        for repeat in description.repeats.values()
        for member in repeat.members.values()
        for unit in member.technical_repeats.values()
    ]
    width = min((len(unit.channels) for unit in units), default=0)
    channels = tuple(
        ids.pop() if len(ids := {unit.channels[i] for unit in units}) == 1 else None
        for i in range(width)
    )
    repeats = {
        (description.conditions[condition].source_folder, member.source_repeat): repeat_id
        for repeat_id, repeat in description.repeats.items()
        for condition, member in repeat.members.items()
    }
    return RunLabels(channels, repeats)


def recorded_labels(output_folder: str | os.PathLike) -> RunLabels:
    """Return the IDs of the run recorded in ``output_folder``, or :data:`NO_LABELS`.

    The run's manifest must record a description and the published file must still have the
    recorded SHA-256; anything else (no description, a file edited since, an unreadable
    manifest or file) gives :data:`NO_LABELS`.
    """
    folder = Path(output_folder)
    try:
        recorded = read_manifest(folder / MANIFEST_FILENAME).experiment_description
        data = read_file_bytes(folder / DESCRIPTION_FILENAME)
        if recorded is None or data is None or sha256_hex(data) != recorded["sha256"]:
            return NO_LABELS
        return labels_from_description(description_from_bytes(data))
    except (OSError, ManifestFormatError, DescriptionError):
        return NO_LABELS


__all__ = ["NO_LABELS", "RunLabels", "labels_from_description", "recorded_labels"]
