"""The names a run's plots use: replicate IDs from the published description.

A histogram table (schema version 5) names its channels by canonical ID and its images by the
folder labels (condition, biological repeat). When the run was described, the manifest records
the SHA-256 of the ``morphobricks.toml`` it read; the file in the output folder is used here
only when it still has exactly those bytes, so the IDs shown are the ones the run was made
with. Nothing is guessed: a repeat folder the file does not name, a changed or unreadable file,
or a run without a description keep the folder label.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .description import Description, DescriptionError, description_from_bytes
from .description_file import DESCRIPTION_FILENAME, read_file_bytes, sha256_hex
from .manifest import MANIFEST_FILENAME, ManifestFormatError, read_manifest
from .notebook_values import natural_key


@dataclass(frozen=True)
class RunLabels:
    """The IDs a described run gives its biological repeats.

    Attributes
    ----------
    repeats
        ``(condition folder, biological-repeat folder)`` -> the replicate ID.
    """

    repeats: Mapping[tuple[str, str], str] = field(default_factory=dict)

    def channel(self, channel: str) -> str:
        """Return how a channel is shown: its canonical ID, as the table names it."""
        return channel

    def repeat(self, condition: str, repeat: str) -> str:
        """Return the replicate ID of a biological-repeat folder, or the folder label."""
        return self.repeats.get((condition, repeat), repeat)

    def order(self, condition: str, repeat: str) -> tuple:
        """Return the sort key of a biological-repeat folder: condition, replicate ID, folder.

        Within a condition the plots list the replicates by the ID they are shown with, in
        natural order (``rep2`` before ``rep10``, :func:`.notebook_values.natural_key`), not by
        folder name; the folder breaks a tie. Display only.
        """
        return (condition, natural_key(self.repeat(condition, repeat)), repeat)


NO_LABELS = RunLabels()
"""The labels when the run's description cannot be used (edited since): the table's names."""


def labels_from_description(description: Description) -> RunLabels:
    """Return the replicate IDs ``description`` declares."""
    repeats = {
        (description.conditions[condition].source_folder, member.source_repeat): repeat_id
        for repeat_id, repeat in description.repeats.items()
        for condition, member in repeat.members.items()
    }
    return RunLabels(repeats)


def recorded_labels(output_folder: str | os.PathLike) -> RunLabels:
    """Return the IDs of the run recorded in ``output_folder``, or :data:`NO_LABELS`.

    The run's manifest must record a description and the published file must still have the
    recorded SHA-256; anything else (a file edited or removed since, an unreadable manifest or
    file) gives :data:`NO_LABELS`.
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
