"""The conversion record of a tree of named-axis copies (design v0.4 §5, contract §17).

:func:`.conversion.convert_experiment` writes it last, into the root of the copy tree, as
:data:`RECORD_FILENAME` (a dot-file: discovery skips it, so it is never taken for an
image). It lists every image of the tree with its source's and its copy's fingerprints,
relative paths, roles, axes and pixel sizes, and the package versions. A run on the copy
tree records the record's SHA-256 in its manifest (``conversion_record_sha256``) and
:func:`review_conversion` warns when a source changed after the conversion, or a copy
since it was written.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ._io import write_text_atomic
from .axes import AxisMapping
from .fingerprint import Fingerprint, current_fingerprint
from .manifest import file_sha256
from .recorded_sizes import RecordedSize, sizes_from_data, sizes_to_data
from .validation import ValidationIssue, error, plural, warning

RECORD_FILENAME = ".morphobricks-conversion.json"
"""The conversion record, in the root of the copy tree."""

RECORD_VERSION = "1"

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

    def same_plan(self, other: CopyEntry) -> bool:
        """Return True if ``other`` copies the same source the same way (copies aside)."""
        return (
            self.relative_path,
            self.action,
            self.reason,
            self.source,
            self.roles,
            self.copy_axes,
        ) == (
            other.relative_path,
            other.action,
            other.reason,
            other.source,
            other.roles,
            other.copy_axes,
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
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> CopyEntry:
        """Read an entry written by :meth:`to_dict`."""
        roles = data.get("roles")
        copy = data.get("copy")
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
            For another record version or data that is not a record.
        """
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
    copies = sorted(
        {
            e.relative_path
            for e in record.files
            if current_fingerprint(Path(root) / e.relative_path) != e.copy
        }
        | (set(images) - listed)
    )
    issues = [*_source_issue(changed, record.source_root), *_copy_issue(copies)]
    return ConversionReview(sha256, record.source_root, tuple(issues))


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


def _flag(value: object) -> bool:
    """Return a JSON boolean; raise for anything else."""
    if not isinstance(value, bool):
        raise ValueError(f"{value!r} is not true or false")
    return value
