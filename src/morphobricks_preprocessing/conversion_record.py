"""The conversion record of a tree of named-axis copies (design v0.4 §5, contract §17).

:func:`.conversion.convert_experiment` writes it last, into the root of the copy tree, as
:data:`RECORD_FILENAME` (a dot-file: discovery skips it, so it is never taken for an
image). It lists every image of the tree with its source's and its copy's fingerprints,
the copy's SHA-256 (:mod:`.content_hash`), relative paths, roles, axes, pixel sizes,
channel names and significant bits, and the package versions. A run on the copy tree
records the record's SHA-256 in its manifest (``conversion_record_sha256``) and
:func:`review_conversion` warns when a source changed after the conversion, or a copy
since it was written (its fingerprint or its SHA-256).

Record version 2 (PP-11c) added the copies' SHA-256, channel names and significant bits;
a version 1 record is refused with a message to convert again
(:class:`ConversionRecordError`), since its copies cannot be checked.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ._io import write_text_atomic
from .axes import AxisMapping
from .content_hash import unchanged_content
from .fingerprint import Fingerprint, current_fingerprint
from .manifest import file_sha256
from .recorded_sizes import RecordedSize, sizes_from_data, sizes_to_data
from .validation import ValidationIssue, error, plural, warning

RECORD_FILENAME = ".morphobricks-conversion.json"
"""The conversion record, in the root of the copy tree."""

RECORD_VERSION = "2"
"""Version 2 (PP-11c): each copy's SHA-256, channel names and significant bits."""

_OLD_VERSIONS = ("1",)
"""Record versions written before the copies' SHA-256: refused, convert again."""

CONVERTED = "converted"
"""An image written as a named-axis OME-TIFF with the confirmed roles."""

COPIED = "copied"
"""An image copied byte for byte: its header already names its axes as confirmed, it is
an OME-Zarr image, or it is excluded (never read)."""


class ConversionRecordError(ValueError):
    """Raised when a conversion record cannot be read."""


@dataclass(frozen=True)
class CopyEntry:
    """One image of a copy tree, as the conversion record lists it.

    Attributes
    ----------
    relative_path
        The image, relative to the experiment root and to the copy tree (the same path).
    action
        :data:`CONVERTED` or :data:`COPIED`.
    reason
        Why: the axis-check code of a converted image (``"axes_yx_swapped"``,
        ``"axes_contradict_header"``, ``"axes_x_before_y"``, ``"axes_not_named"``),
        ``"excluded"`` for an excluded image, or None for an image copied as it was.
    source
        The source's fingerprint when it was read.
    copy
        The copy's fingerprint when it was written (None before it is written).
    source_axes
        The axes the source header gives (tifffile letters).
    roles
        The roles the source was read with (converted images), else None.
    copy_axes
        The copy's axes (converted images), else None.
    pixel_sizes
        The copy's pixel sizes by role (converted images).
    unknown_sizes
        ``{role: reason}`` for each size the source header gives that the copy does not.
    copy_sha256
        The copy's SHA-256 when it was written (:func:`.content_hash.content_sha256`;
        None before it is written).
    channel_names
        The channel names the copy holds (a converted image's as written, a copied
        image's from its header); empty for none.
    significant_bits
        The significant bits the copy holds, or None.
    """

    relative_path: str
    action: str
    reason: str | None
    source: Fingerprint
    copy: Fingerprint | None = None
    source_axes: str = ""
    roles: tuple[str | None, ...] | None = None
    copy_axes: str | None = None
    pixel_sizes: tuple[RecordedSize, ...] = ()
    unknown_sizes: Mapping[str, str] = field(default_factory=dict)
    copy_sha256: str | None = None
    channel_names: tuple[str | None, ...] = ()
    significant_bits: int | None = None

    def same_plan(self, other: CopyEntry) -> bool:
        """Return True if ``other`` copies the same source the same way (copies aside).

        The source's fingerprint and axes, the action and reason, the roles, the copy's
        axes, its pixel sizes, which sizes are unknown and why, its channel names and
        significant bits must all be the same, so a copy written under another rule is
        never reused beside new ones.
        """
        return self._plan() == other._plan()

    def _plan(self) -> tuple:
        """Return what decides how the copy is written."""
        return (
            self.relative_path,
            self.action,
            self.reason,
            self.source,
            self.source_axes,
            self.roles,
            self.copy_axes,
            self.pixel_sizes,
            tuple(sorted(self.unknown_sizes.items())),
            tuple(self.channel_names),
            self.significant_bits,
        )

    def to_dict(self) -> dict:
        """Return the entry as JSON-compatible data."""
        return {
            "relative_path": self.relative_path,
            "action": self.action,
            "reason": self.reason,
            "source": _fingerprint_data(self.source),
            "copy": None if self.copy is None else _fingerprint_data(self.copy),
            "source_axes": self.source_axes,
            "roles": None if self.roles is None else list(self.roles),
            "copy_axes": self.copy_axes,
            "pixel_sizes": sizes_to_data(self.pixel_sizes),
            "unknown_sizes": dict(sorted(self.unknown_sizes.items())),
            "copy_sha256": self.copy_sha256,
            "channel_names": list(self.channel_names),
            "significant_bits": self.significant_bits,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> CopyEntry:
        """Read an entry written by :meth:`to_dict`; it must give its copy and SHA-256."""
        roles = data.get("roles")
        copy = data.get("copy")
        if copy is None or not isinstance(data.get("copy_sha256"), str):
            raise ValueError(f"{data.get('relative_path')!r} has no copy fingerprint or SHA-256")
        return cls(
            relative_path=str(data["relative_path"]),
            action=_choice(data["action"], (CONVERTED, COPIED), "action"),
            reason=None if data.get("reason") is None else str(data["reason"]),
            source=Fingerprint.recorded(data["source"]),
            copy=None if copy is None else Fingerprint.recorded(copy),
            source_axes=str(data.get("source_axes", "")),
            roles=None if roles is None else tuple(None if r is None else str(r) for r in roles),
            copy_axes=None if data.get("copy_axes") is None else str(data["copy_axes"]),
            pixel_sizes=sizes_from_data(data.get("pixel_sizes")),
            unknown_sizes={str(k): str(v) for k, v in (data.get("unknown_sizes") or {}).items()},
            copy_sha256=str(data["copy_sha256"]),
            channel_names=tuple(None if n is None else str(n) for n in data["channel_names"]),
            significant_bits=_bits(data["significant_bits"]),
        )


@dataclass(frozen=True)
class ConversionRecord:
    """What a conversion wrote into a copy tree.

    Attributes
    ----------
    source_root
        The experiment folder the copies were made from (absolute, ``/`` separators).
    mapping
        The experiment mapping the user confirmed for the conversion.
    missing_as_singleton
        Whether the user confirmed missing axes as single planes.
    files
        One entry per image of the copy tree, sorted by path.
    versions
        The package versions that wrote the copies.
    written_at
        When the record was written (UTC, ISO 8601).
    """

    source_root: str
    mapping: AxisMapping
    missing_as_singleton: bool
    files: tuple[CopyEntry, ...]
    versions: Mapping[str, str] = field(default_factory=dict)
    written_at: str = ""

    def to_dict(self) -> dict:
        """Return the record as JSON-compatible data."""
        return {
            "record_version": RECORD_VERSION,
            "source_root": self.source_root,
            "mapping": {"axes": self.mapping.axes, "roles": list(self.mapping.roles)},
            "missing_as_singleton": self.missing_as_singleton,
            "written_at": self.written_at,
            "versions": dict(self.versions),
            "files": [entry.to_dict() for entry in self.files],
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> ConversionRecord:
        """Read a record written by :meth:`to_dict`.

        Raises
        ------
        ConversionRecordError
            For another record version (a version 1 record: convert again) or data that is
            not a record.
        """
        if isinstance(data, Mapping) and data.get("record_version") in _OLD_VERSIONS:
            raise ConversionRecordError(
                f"it is a record of version {data['record_version']}, written before the "
                "copies' SHA-256 was recorded, so the copies cannot be checked; convert the "
                "originals again into a new folder"
            )
        if not isinstance(data, Mapping) or data.get("record_version") != RECORD_VERSION:
            raise ConversionRecordError(f"not a conversion record of version {RECORD_VERSION}")
        try:
            mapping = data["mapping"]
            return cls(
                source_root=str(data["source_root"]),
                mapping=AxisMapping(str(mapping["axes"]), tuple(mapping["roles"])),
                missing_as_singleton=_flag(data["missing_as_singleton"]),
                files=tuple(CopyEntry.from_dict(item) for item in data["files"]),
                versions={str(k): str(v) for k, v in (data.get("versions") or {}).items()},
                written_at=str(data.get("written_at", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConversionRecordError(f"not a valid conversion record: {exc}") from exc

    def entry(self, relative_path: str) -> CopyEntry | None:
        """Return the entry of an image, or None when the record does not list it."""
        return next((e for e in self.files if e.relative_path == relative_path), None)


def write_record(record: ConversionRecord, folder: str | os.PathLike) -> str:
    """Write the record into the copy tree atomically; return its SHA-256.

    Parameters
    ----------
    record
        The record.
    folder
        The root of the copy tree.

    Returns
    -------
    str
        The SHA-256 of the file written.
    """
    text = json.dumps(record.to_dict(), indent=2, ensure_ascii=False) + "\n"
    return file_sha256(write_text_atomic(Path(folder) / RECORD_FILENAME, text))


def read_record(folder: str | os.PathLike) -> tuple[ConversionRecord, str] | None:
    """Read the conversion record of a copy tree, with its SHA-256.

    Parameters
    ----------
    folder
        The root of the copy tree (or any experiment folder).

    Returns
    -------
    tuple or None
        ``(record, sha256)``, or None when the folder holds no record.

    Raises
    ------
    ConversionRecordError
        If the record cannot be read.
    """
    path = Path(folder) / RECORD_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConversionRecordError(f"{path} cannot be read: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConversionRecordError(f"{path} is not valid JSON: {exc}") from exc
    return ConversionRecord.from_dict(data), file_sha256(path)


@dataclass(frozen=True)
class ConversionReview:
    """What a run finds about the conversion record of its experiment folder.

    Attributes
    ----------
    sha256
        The record's SHA-256 (recorded in the manifest), or None when it cannot be read.
    source_root
        The experiment folder the copies were made from, or None.
    issues
        ``conversion_source_changed`` and ``copy_changed_after_conversion`` (warnings), or
        ``conversion_record_unreadable`` (an error).
    record
        The record's file name, in the experiment folder.
    """

    sha256: str | None
    source_root: str | None
    issues: tuple[ValidationIssue, ...] = ()
    record: str = RECORD_FILENAME


def review_conversion(root: str | os.PathLike, images: Iterable[str]) -> ConversionReview | None:
    """Check a copy tree against its conversion record; None for an ordinary experiment.

    Parameters
    ----------
    root
        The experiment folder of the run.
    images
        The images discovery found in it, relative to it.

    Returns
    -------
    ConversionReview or None
        None when the folder holds no conversion record.
    """
    try:
        found = read_record(root)
    except ConversionRecordError as exc:
        return ConversionReview(None, None, (_unreadable(exc),))
    if found is None:
        return None
    record, sha256 = found
    sources = Path(record.source_root)
    changed = [
        e.relative_path
        for e in record.files
        if current_fingerprint(sources / e.relative_path) != e.source
    ]
    listed = {e.relative_path for e in record.files}
    copies = sorted(set(changed_copies(root, record)) | (set(images) - listed))
    issues = [*_source_issue(changed, record.source_root), *_copy_issue(copies)]
    return ConversionReview(sha256, record.source_root, tuple(issues))


def changed_copies(
    root: str | os.PathLike, record: ConversionRecord, paths: Iterable[str] | None = None
) -> list[str]:
    """Return the copies changed or gone since they were written: fingerprint or SHA-256.

    Each copy listed is read whole to compute its SHA-256 (unless its fingerprint already
    differs), so the check costs one read of the copies.

    Parameters
    ----------
    root
        The copy tree.
    record
        Its conversion record.
    paths
        The images to check, relative; by default every image the record lists. An image
        the record does not list counts as changed.

    Returns
    -------
    list of str
        The changed, gone or unlisted copies, sorted.
    """
    wanted = [e.relative_path for e in record.files] if paths is None else list(paths)
    changed = []
    for rel in sorted(set(wanted)):
        entry, copy = record.entry(rel), Path(root) / rel
        if (
            entry is None
            or current_fingerprint(copy) != entry.copy
            or not unchanged_content(copy, entry.copy_sha256)
        ):
            changed.append(rel)
    return changed


def _source_issue(paths: list[str], source_root: str) -> list[ValidationIssue]:
    """Warn about sources changed or gone since the conversion."""
    if not paths:
        return []
    return [
        warning(
            "conversion_source_changed",
            f"{plural(len(paths), 'source image')} changed or went missing in {source_root} "
            "after these named-axis copies were made. The copies hold the images as they were "
            "converted.",
            paths,
            "If the originals were corrected or replaced, convert them again into a new folder "
            "and run preprocessing on that one.",
        )
    ]


def _copy_issue(paths: list[str]) -> list[ValidationIssue]:
    """Warn about copies changed, gone or added since the conversion."""
    if not paths:
        return []
    return [
        warning(
            "copy_changed_after_conversion",
            f"{plural(len(paths), 'image')} of this folder of named-axis copies changed, went "
            "missing or were added after the conversion, so the conversion record does not "
            "describe them.",
            paths,
            "Convert the originals again into a new folder, or check that these images are "
            "the ones you want analysed.",
        )
    ]


def _unreadable(exc: ConversionRecordError) -> ValidationIssue:
    """Refuse a copy tree whose conversion record cannot be read."""
    return error(
        "conversion_record_unreadable",
        f"The conversion record of this folder of named-axis copies cannot be read ({exc}), "
        "so it is not known what was converted and how.",
        [RECORD_FILENAME],
        "Convert the originals again into a new folder, and run preprocessing on that one.",
    )


def _fingerprint_data(fingerprint: Fingerprint) -> dict:
    """Return a fingerprint as JSON-compatible data."""
    return {"size": fingerprint.size, "mtime_ns": fingerprint.mtime_ns}


def _choice(value: object, allowed: tuple[str, ...], name: str) -> str:
    """Return ``value`` when it is one of ``allowed``; raise otherwise."""
    if value not in allowed:
        raise ValueError(f"{name} {value!r} is not one of {', '.join(allowed)}")
    return str(value)


def _bits(value: object) -> int | None:
    """Return recorded significant bits: a whole number, or None."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"significant_bits {value!r} is not a whole number")
    return value


def _flag(value: object) -> bool:
    """Return a JSON boolean; raise for anything else."""
    if not isinstance(value, bool):
        raise ValueError(f"{value!r} is not true or false")
    return value
