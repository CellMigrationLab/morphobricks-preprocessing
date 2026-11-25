"""One analysed source file of a run manifest, and its manifest data (contract §9).

:class:`FileEntry` describes a source image as the run read it: fingerprint, labels, axes,
roles and where they come from, pixel sizes, channel names and IDs. :func:`file_entry_for` and
:func:`file_entry_with` describe a file as it is on disk now; :func:`file_dict` and
:func:`file_from_data` write and read an entry as manifest data, refusing what the other would
refuse, so a written manifest always reads back. :mod:`.manifest` holds the manifest around the
entries and re-exports these names.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .axes import AxisMapping
from .image_names import position_id
from .inventory import INVENTORY_KEY, source_signature
from .manifest_fields import (
    CHANNEL_IDS_KEY,
    RAW_NAMES_KEY,
    optional_text,
    recorded_channel_ids,
    recorded_flag,
    recorded_position_id,
    recorded_raw_names,
    recorded_roles,
)
from .manifest_versions import CONTENT_KEY
from .recorded_sizes import RecordedSize, sizes_from_data, sizes_to_data

AXES_FROM = ("header", "confirmed")
"""Where a file's roles come from (:attr:`FileEntry.axes_from`)."""


@dataclass(frozen=True)
class FileEntry:
    """One analysed source file.

    Attributes
    ----------
    relative_path
        Path relative to the experiment root, with ``/`` separators.
    size
        File size in bytes when it was read; for an OME-Zarr folder, the total
        size of its files.
    mtime_ns
        Modification time in nanoseconds when it was read; for an OME-Zarr
        folder, the time of its most recently modified file.
    labels
        ``(condition, biological_repeat, technical_repeat)``.
    axes
        The file's axis labels.
    singleton_roles
        Roles of the experiment mapping the file lacks, read as length 1.
    plane_count
        Number of rows the file has in the table.
    channel_names
        Channel names from metadata; empty when not given.
    significant_bits
        Significant bits from metadata; None when not given.
    channel_names_trimmed
        True when whitespace was removed around the channel names (names are recorded trimmed).
    read_in_blocks
        True when the planes are too large for the memory limit and are read in blocks. The
        histogram rows are the same either way; the neighbour metrics of the image QC rows are
        not (contract §19), so the row key holds the memory limit for such an image.
    position_id
        The image's position identifier, its name without the extension
        (:func:`~morphobricks_preprocessing.image_names.position_id`). Required when written
        and read, and equal to that rule (round-2 audit A-m5); None only before the entry is
        complete.
    roles
        The role of each of the file's axes (``"X"``, ``"Y"``, ``"Z"``, ``"C"``, ``"T"`` or
        None), from its own mapping: for an OME-Zarr image whose declared order differs from
        the experiment mapping's, these are its roles, not the experiment's. Required when
        written and read.
    axes_from
        Where the roles come from: ``"header"`` (the metadata names every axis and
        the roles agree) or ``"confirmed"`` (a plain stack: the user confirmed them).
        Required when written and read.
    pixel_sizes
        The size of one step along X, Y, Z and T, each with its unit and source
        (:class:`.recorded_sizes.RecordedSize`); always written.
    inventory_sha256
        For an OME-Zarr folder, the SHA-256 of its inventory (each file's relative path,
        size and modification time, :func:`.inventory.source_signature`); None for a
        TIFF file, and in manifests written before it was recorded (such an OME-Zarr
        image counts as changed: its fingerprint is not known to be complete).
    content_sha256
        The SHA-256 of the file's content (an OME-Zarr folder: of its files' paths and SHA-256s,
        :func:`.source_content.source_content_sha256`) in a ``verify_content`` run; else None.
    raw_channel_names
        Header channel names, untrimmed; written only when not empty; not in :meth:`same_source`.
    channel_ids
        The image's canonical channel IDs in source order: its imaging unit's confirmed
        ``channels`` list (Stage 3, S3.3; units may list different channels). Required when
        written and read; part of the row key (:class:`.fingerprint.RowKey`), so rows named
        by other IDs are never reused.
    """

    relative_path: str
    size: int
    mtime_ns: int
    labels: tuple[str, str, str]
    axes: str
    singleton_roles: tuple[str, ...] = ()
    plane_count: int = 0
    channel_names: tuple[str | None, ...] = ()
    significant_bits: int | None = None
    read_in_blocks: bool = False
    position_id: str | None = None
    channel_names_trimmed: bool = False
    roles: tuple[str | None, ...] | None = None
    axes_from: str | None = None
    pixel_sizes: tuple[RecordedSize, ...] = ()
    inventory_sha256: str | None = None
    content_sha256: str | None = None
    raw_channel_names: tuple[str | None, ...] = ()
    channel_ids: tuple[str, ...] = ()

    def same_source(self, other: FileEntry) -> bool:
        """Return True if ``other`` describes the same unchanged file, read the same way."""
        return (
            self.relative_path == other.relative_path
            and self.size == other.size
            and self.mtime_ns == other.mtime_ns
            and self.inventory_sha256 == other.inventory_sha256
            and self.content_sha256 == other.content_sha256
            and self.labels == other.labels
            and self.axes == other.axes
            and self.singleton_roles == other.singleton_roles
        )


def file_entry_for(  # noqa: PLR0913
    root: str | os.PathLike,
    relative_path: str,
    labels: tuple[str, str, str],
    mapping: AxisMapping,
    *,
    plane_count: int = 0,
    channel_names: tuple[str | None, ...] = (),
    significant_bits: int | None = None,
    read_in_blocks: bool = False,
    channel_names_trimmed: bool = False,
) -> FileEntry:
    """Describe a source image as it is on disk now, with its position identifier."""
    return file_entry_with(
        source_signature(Path(root) / relative_path),
        relative_path,
        labels,
        mapping,
        plane_count=plane_count,
        channel_names=tuple(channel_names),
        significant_bits=significant_bits,
        read_in_blocks=read_in_blocks,
        channel_names_trimmed=channel_names_trimmed,
    )


def file_entry_with(
    signature: tuple[int, int, str | None],
    relative_path: str,
    labels: tuple[str, str, str],
    mapping: AxisMapping,
    **header: object,
) -> FileEntry:
    """Describe a source image with a fingerprint already taken, as :func:`file_entry_for`.

    ``signature`` is ``(size, mtime_ns, inventory)`` (:func:`.inventory.source_signature`), for
    example the one taken when the header was read; ``relative_path``, ``labels``, ``mapping``
    and the ``header`` keywords (channel names, bits, ...) are those of :func:`file_entry_for`.

    Returns
    -------
    FileEntry
    """
    size, mtime_ns, inventory = signature
    return FileEntry(
        relative_path=relative_path,
        size=size,
        mtime_ns=mtime_ns,
        labels=tuple(labels),
        axes=mapping.axes,
        singleton_roles=mapping.singleton_roles,
        position_id=position_id(relative_path),
        roles=tuple(mapping.roles),
        inventory_sha256=inventory,
        **header,
    )


def file_dict(entry: FileEntry) -> dict:
    """Return a file entry as manifest data; its roles, their source and position are required.

    The writer refuses what the reader would refuse, so a written manifest always reads back.
    """
    if entry.roles is None or entry.axes_from not in AXES_FROM:
        raise ValueError(f"the entry of {entry.relative_path!r} has no roles or axes_from")
    recorded_position_id(entry.position_id, entry.relative_path)
    data = {
        "relative_path": entry.relative_path,
        "size": entry.size,
        "mtime_ns": entry.mtime_ns,
        "condition": entry.labels[0],
        "biological_repeat": entry.labels[1],
        "technical_repeat": entry.labels[2],
        "axes": entry.axes,
        "singleton_roles": list(entry.singleton_roles),
        "plane_count": entry.plane_count,
        "channel_names": list(entry.channel_names),
        "significant_bits": entry.significant_bits,
        "read_in_blocks": recorded_flag(entry.read_in_blocks, "read_in_blocks"),
        "position_id": entry.position_id,
        "channel_names_trimmed": recorded_flag(
            entry.channel_names_trimmed, "channel_names_trimmed"
        ),
        "roles": None if entry.roles is None else list(entry.roles),
        "axes_from": entry.axes_from,
        "pixel_sizes": sizes_to_data(entry.pixel_sizes),
    }
    sizes_from_data(data["pixel_sizes"])  # one positive finite size per role, as read (#389)
    if entry.inventory_sha256 is not None:
        data[INVENTORY_KEY] = entry.inventory_sha256
    if entry.content_sha256 is not None:
        data[CONTENT_KEY] = entry.content_sha256
    if entry.raw_channel_names:
        data[RAW_NAMES_KEY] = list(entry.raw_channel_names)
    data[CHANNEL_IDS_KEY] = list(recorded_channel_ids(entry.channel_ids, entry.relative_path))
    return data


def file_from_data(data: dict) -> FileEntry:
    """Read a file entry; every key the writer always writes is required, none defaulted.

    The roles, their source (``axes_from``), the pixel sizes and the ``position_id`` (the one
    the entry's name gives, round-2 audit A-m5) are required. Only ``inventory_sha256``,
    ``content_sha256`` and ``raw_channel_names`` may be absent: the writer leaves them out when
    they do not apply.
    """
    bits, path = data["significant_bits"], str(data["relative_path"])
    if data.get("roles") is None or data.get("axes_from") not in AXES_FROM:
        raise ValueError(f"the entry of {path!r} has no roles or axes_from")
    return FileEntry(
        relative_path=path,
        size=int(data["size"]),
        mtime_ns=int(data["mtime_ns"]),
        labels=(
            str(data["condition"]),
            str(data["biological_repeat"]),
            str(data["technical_repeat"]),
        ),
        axes=str(data["axes"]),
        singleton_roles=tuple(str(r) for r in data["singleton_roles"]),
        plane_count=int(data["plane_count"]),
        channel_names=tuple(None if n is None else str(n) for n in data["channel_names"]),
        significant_bits=None if bits is None else int(bits),
        read_in_blocks=recorded_flag(data["read_in_blocks"], "read_in_blocks"),
        position_id=recorded_position_id(data.get("position_id"), path),
        channel_names_trimmed=recorded_flag(data["channel_names_trimmed"], "channel_names_trimmed"),
        roles=recorded_roles(data["roles"], str(data["axes"])),
        axes_from=str(data["axes_from"]),
        pixel_sizes=sizes_from_data(data["pixel_sizes"]),
        inventory_sha256=optional_text(data.get(INVENTORY_KEY)),
        content_sha256=optional_text(data.get(CONTENT_KEY)),
        raw_channel_names=recorded_raw_names(data.get(RAW_NAMES_KEY), path),
        channel_ids=recorded_channel_ids(data.get(CHANNEL_IDS_KEY), path),
    )
