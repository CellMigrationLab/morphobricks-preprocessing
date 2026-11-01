"""The manifest entry of every analysed file, and which of them changed (contract §9).

Each entry records the file's fingerprint, labels, axes, roles, where the roles come from
(``axes_from``) and its pixel sizes with their source (manifest version 2). A file is
unchanged when its :class:`.fingerprint.RowKey` is the earlier run's: the same file, read
the same way from the same place.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from morphobricks_normalization.records import PlaneRecord

from ..axes import AxisReview
from ..fingerprint import RowKey
from ..manifest import FileEntry, RunManifest, RunSettings, file_entry_for
from ..recorded_sizes import header_sizes
from .preflight import Preflight

logger = logging.getLogger(__name__)


def file_entries(
    pre: Preflight, review: AxisReview, root: Path, in_blocks: set[str]
) -> dict[str, FileEntry]:
    """Describe every analysed file as it is now, with its roles and pixel sizes.

    Parameters
    ----------
    pre
        The run's preflight.
    review
        The axis review of the confirmed mapping.
    root
        The experiment folder.
    in_blocks
        Files whose planes are read in blocks.

    Returns
    -------
    dict
        ``{relative path: FileEntry}``, sorted by path.
    """
    labels = {image: leaf.labels for leaf in pre.discovery.leaves for image in leaf.images}
    headers = {f.relative_path: f for f in pre.metadata.files}
    entries: dict[str, FileEntry] = {}
    for rel, file_mapping in sorted(review.file_mappings.items()):
        header = headers[rel]
        entry = file_entry_for(
            root,
            rel,
            labels[rel],
            file_mapping,
            channel_names=header.channel_names,
            significant_bits=header.significant_bits,
            read_in_blocks=rel in in_blocks,
            channel_names_trimmed=header.channel_names_trimmed,
        )
        entries[rel] = replace(
            entry,
            axes_from=review.axes_from.get(rel),
            pixel_sizes=header_sizes(header.physical_sizes),
        )
        if header.channel_names_trimmed:
            logger.info(
                "Leading or trailing whitespace was removed from the channel names of %s", rel
            )
    return entries


@dataclass(frozen=True)
class Comparison:
    """The files of a run compared with the earlier run's.

    Attributes
    ----------
    new, changed, unchanged
        Files not in the earlier run, changed since (another fingerprint, labels, roles,
        source of the roles or place), and as they were.
    removed
        Files of the earlier run no longer analysed.
    """

    new: tuple[str, ...]
    changed: tuple[str, ...]
    unchanged: tuple[str, ...]
    removed: tuple[str, ...]


def compare_with_previous(
    entries: Mapping[str, FileEntry],
    previous: RunManifest | None,
    previous_records: Mapping[str, Sequence[PlaneRecord]],
    rebuild: bool,
    settings: RunSettings,
    root: Path,
) -> Comparison:
    """Sort the files into new, changed, unchanged and removed, by their row keys.

    The settings that change every row are compared once, by the earlier run's reason for
    a rebuild; the keys are compared with the current settings on both sides, so they
    differ only in what the file is and how it is read.

    Parameters
    ----------
    entries
        The files as they are now (:func:`file_entries`).
    previous
        The earlier run's manifest, or None.
    previous_records
        Its rows by file (empty during a rebuild).
    rebuild
        Whether every row is recomputed anyway; the earlier rows need not be complete then.
    settings
        The run's settings.
    root
        The experiment folder.

    Returns
    -------
    Comparison
    """
    new, changed, unchanged = [], [], []
    for rel, entry in entries.items():
        before = previous.file(rel) if previous is not None else None
        if before is None:
            new.append(rel)
            continue
        same = RowKey.of(before, settings, previous.experiment_root) == RowKey.of(
            entry, settings, root.as_posix()
        )
        complete = rebuild or len(previous_records.get(rel, ())) == before.plane_count
        (unchanged if same and complete else changed).append(rel)
    removed = sorted(
        f.relative_path
        for f in (previous.files if previous else ())
        if f.relative_path not in entries
    )
    return Comparison(tuple(new), tuple(changed), tuple(unchanged), tuple(removed))
