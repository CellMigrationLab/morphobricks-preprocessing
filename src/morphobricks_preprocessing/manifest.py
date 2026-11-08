"""The run manifest: what a run read, with which settings, and what it wrote.

``run_manifest.json`` sits next to ``plane_histograms.csv``. It records the confirmed folder
labels and axis mapping, the settings, the package versions, and each source file's
fingerprint (size and modification time, and for an OME-Zarr folder its inventory). The next
run compares these with the experiment folder to find new, changed and deleted files, and
reads again only those (``docs/plan.md`` §7). The fingerprint is metadata: a file rewritten
with the same size and its modification time set back is not seen as changed, unless the run
verifies content (``verify_content``: each source's SHA-256 is recorded and must match). The
format is described in ``docs/contract.md`` §9.

The manifest also records, for information only, each file's channel names and
significant bits, the SHA-256 of the experiment design used (so later bricks can check it
was not changed), the image type the user confirmed (which later bricks normalize by), the
limits of the intensity checks, the histogram range the table was binned with, and the
planes whose central intensity range was too narrow (follow-up P6), so a later run that
keeps their rows still reports them. For projected copies it records the projection.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.csv_io import SCHEMA_VERSION
from morphobricks_normalization.ranges import HistogramSettings

from ._io import file_sha256 as file_sha256  # re-exported (moved)
from ._io import write_text_atomic
from .axes import AxisMapping
from .description_record import KEY as DESCRIPTION_KEY
from .description_record import recorded_description
from .image_names import position_id
from .inventory import INVENTORY_KEY, source_signature
from .inventory import source_fingerprint as source_fingerprint  # re-exported (moved, PP-12)
from .manifest_fields import (
    RAW_NAMES_KEY,
    optional_text,
    recorded_flag,
    recorded_masks,
    recorded_position_id,
    recorded_raw_names,
    recorded_roles,
)
from .manifest_versions import (
    CONTENT_KEY,
    UnsupportedVersionError,
    check_version,
    recorded_version,
)
from .manifest_versions import LEGACY_MANIFEST_VERSION as LEGACY_MANIFEST_VERSION
from .manifest_versions import MANIFEST_VERSION as MANIFEST_VERSION
from .manifest_versions import STRICT_MANIFEST_VERSION as STRICT_MANIFEST_VERSION
from .narrow_ranges import NarrowRange
from .projection_run import KEY as PROJECTION_KEY
from .projection_run import recorded_projection
from .recorded_sizes import RecordedSize, sizes_from_data, sizes_to_data
from .versions import package_versions as package_versions  # re-exported (moved)

MANIFEST_FILENAME = "run_manifest.json"
AXES_FROM = ("header", "confirmed")
_LABEL_KEYS = ("folder", "condition", "biological_repeat", "technical_repeat")
"""Where a file's roles come from (:attr:`FileEntry.axes_from`)."""


class ManifestFormatError(ValueError):
    """Raised when a file is not a run manifest this version can read."""


@dataclass(frozen=True)
class RunSettings:
    """Settings that decide the content of the histogram table.

    Attributes
    ----------
    bin_count
        Number of bins of every plane.
    memory_limit
        Working-memory limit per plane, in bytes. It does not change any row.
    missing_as_singleton
        Whether the user confirmed that files lacking axes have one plane along
        them.
    exclude
        Relative paths of images the user excluded from the run, sorted.
    histogram
        The histogram range of histograms that are not exact, with the limits of
        the range check (the normalization brick's ``HistogramSettings``).
        Manifests written before it was recorded read as the default, the
        minimum to maximum.
    verify_content
        Strict reuse (CLI ``--verify-content``): each source's content SHA-256 is recorded
        (:attr:`FileEntry.content_sha256`) and must match before its staged or earlier rows
        are reused. False (the default, and in manifests without it): the fingerprint only.
    """

    bin_count: int
    memory_limit: int
    missing_as_singleton: bool = False
    exclude: tuple[str, ...] = ()
    histogram: HistogramSettings = field(default_factory=HistogramSettings)
    verify_content: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "exclude", tuple(sorted(set(self.exclude))))


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
        True when the planes are too large for the memory limit and are read in blocks.
        For information only: the rows are the same either way.
    position_id
        The image's position identifier, its name without the extension
        (:func:`~morphobricks_preprocessing.image_names.position_id`). Required in manifest
        version 2, and equal to that rule (round-2 audit A-m5); None in older manifests.
    roles
        The role of each of the file's axes (``"X"``, ``"Y"``, ``"Z"``, ``"C"``, ``"T"`` or
        None), from its own mapping: for an OME-Zarr image whose declared order differs from
        the experiment mapping's, these are its roles, not the experiment's. Required in
        manifest version 2; None in older manifests.
    axes_from
        Where the roles come from: ``"header"`` (the metadata names every axis and
        the roles agree) or ``"confirmed"`` (a plain stack: the user confirmed them).
        Required in manifest version 2; None in older manifests.
    pixel_sizes
        The size of one step along X, Y, Z and T, each with its unit and source
        (:class:`.recorded_sizes.RecordedSize`); empty in older manifests.
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


@dataclass(frozen=True)
class RunManifest:
    """Everything a run records about its inputs and output.

    Attributes
    ----------
    experiment_root
        Absolute path of the experiment folder, for information.
    dtype
        Pixel type of the experiment.
    settings
        The run settings.
    mapping
        The confirmed experiment axis mapping.
    files
        One entry per analysed file, sorted by path.
    csv_sha256
        SHA-256 of the histogram table written with this manifest, so a table
        changed or replaced afterwards is detected.
    versions
        Versions of this package, NumPy, tifffile and Python.
    written_at
        When the manifest was written (UTC, ISO 8601).
    experiment_design_sha256
        SHA-256 of the ``experiment_design.csv`` written with this run, or None
        when the run had no experiment design (or in manifests written before it
        was recorded).
    masks
        The masks of the images when masks were given (contract §15): the masks
        folder, the mask types and one entry per mask (image, type, path,
        source); None otherwise. The key is left out of the file when None.
    image_type
        ``"fluorescence"`` or ``"rgb"``, as the user confirmed it
        (:mod:`.image_type`); None when the run was not given one (or in
        manifests written before it was recorded).
    channels_from_rgb_samples
        True when the channel axis is the RGB samples axis of images stored as
        RGB colour; None in manifests written before it was recorded.
    intensity_checks
        The limits of the intensity checks run on the table (:mod:`.intensity`);
        None in manifests written before they were recorded.
    narrow_ranges
        The channel planes whose central intensity range covered fewer bins
        than the range check's limit (``narrow_histogram_range``), sorted by
        plane and channel; empty when there were none (and in manifests
        written before they were recorded).
    conversion_record_sha256
        SHA-256 of the conversion record in the experiment root, when the experiment is
        a copy tree of named-axis copies (design v0.4 §5); None otherwise.
    calibration_sha256
        SHA-256 of the output folder's ``calibration.csv`` as the run read it (the pixel
        sizes the user stated, design v0.4 §3); None when there was none.
    experiment_description
        What a run with a published ``morphobricks.toml`` records about it
        (:mod:`.description_record`); None, and the key left out, for a run without one.
    projection
        The Z projection of the images (:mod:`.projection_run`); None (key left out) if none.
    schema_version
        Version of the CSV format.
    manifest_version
        Version of the manifest format (:mod:`.manifest_versions`): ``"2"`` since v0.4.0,
        ``"3"`` for a ``verify_content`` run (:func:`.manifest_versions.version_for`), which
        readers of v0.4.0rc4 and earlier refuse. A ``"1"`` manifest (older runs) is read
        for reporting only; no row of such a run is reused.
    """

    experiment_root: str
    dtype: str
    settings: RunSettings
    mapping: AxisMapping
    files: tuple[FileEntry, ...]
    csv_sha256: str
    versions: dict[str, str] = field(default_factory=dict)
    written_at: str = ""
    experiment_design_sha256: str | None = None
    masks: dict | None = None
    image_type: str | None = None
    channels_from_rgb_samples: bool | None = None
    intensity_checks: IntensityCheckSettings | None = None
    narrow_ranges: tuple[NarrowRange, ...] = ()
    conversion_record_sha256: str | None = None
    calibration_sha256: str | None = None
    experiment_description: dict | None = None
    projection: dict | None = None
    schema_version: str = SCHEMA_VERSION
    manifest_version: str = MANIFEST_VERSION

    @property
    def leaves(self) -> dict[str, tuple[str, str, str]]:
        """Labels of each leaf folder, keyed by its relative path."""
        return {
            _parent(entry.relative_path): entry.labels
            for entry in sorted(self.files, key=lambda e: e.relative_path)
        }

    def file(self, relative_path: str) -> FileEntry | None:
        """Return the entry of one file, or None if the run did not analyse it."""
        return next((f for f in self.files if f.relative_path == relative_path), None)

    def to_dict(self) -> dict:
        """Return the manifest as JSON-compatible data.

        Raises
        ------
        ValueError
            If its version does not match its content verification
            (:func:`.manifest_versions.check_version`): the reader would refuse it.
        """
        check_version(
            self.manifest_version,
            recorded_flag(self.settings.verify_content, "verify_content"),
            [(f.relative_path, f.content_sha256) for f in self.files],
            self.experiment_description is not None,
        )
        data = {
            "manifest_version": self.manifest_version,
            "schema_version": self.schema_version,
            "written_at": self.written_at,
            "experiment_root": self.experiment_root,
            "dtype": self.dtype,
            "settings": {
                "bin_count": self.settings.bin_count,
                "memory_limit": self.settings.memory_limit,
                "missing_as_singleton": recorded_flag(
                    self.settings.missing_as_singleton, "missing_as_singleton"
                ),
                "exclude": list(self.settings.exclude),
                "histogram": self.settings.histogram.to_dict(),
                # Written by every version 2 and 3 manifest (#159): a run that opted out of
                # content hashing says so. Manifests written before it have no key and read
                # as not verified; version 1 is unchanged.
                **(
                    {
                        "verify_content": recorded_flag(
                            self.settings.verify_content, "verify_content"
                        )
                    }
                    if self.manifest_version != "1"
                    else {}
                ),
            },
            "axis_mapping": {"axes": self.mapping.axes, "roles": list(self.mapping.roles)},
            "labels": [
                dict(zip(_LABEL_KEYS, (folder, *labels), strict=True))
                for folder, labels in self.leaves.items()
            ],
            "files": [_file_dict(f, strict=self.manifest_version != "1") for f in self.files],
            "csv_sha256": self.csv_sha256,
            "experiment_design_sha256": self.experiment_design_sha256,
            "image_type": self.image_type,
            "channels_from_rgb_samples": (
                None
                if self.channels_from_rgb_samples is None
                else recorded_flag(self.channels_from_rgb_samples, "channels_from_rgb_samples")
            ),
            "intensity_checks": (
                None if self.intensity_checks is None else self.intensity_checks.to_dict()
            ),
            "narrow_histogram_ranges": [entry.to_dict() for entry in self.narrow_ranges],
            "conversion_record_sha256": self.conversion_record_sha256,
            "calibration_sha256": self.calibration_sha256,
            "versions": dict(self.versions),
        }
        optional = {"masks": self.masks, DESCRIPTION_KEY: self.experiment_description}
        optional[PROJECTION_KEY] = self.projection
        data.update({key: value for key, value in optional.items() if value is not None})
        return data

    @classmethod
    def from_dict(cls, data: dict) -> RunManifest:
        """Build a manifest from data written by :meth:`to_dict`.

        Raises
        ------
        ManifestFormatError
            If the data is not a manifest of this version.
        """
        try:
            version = recorded_version(data)
            settings = data["settings"]
            mapping = data["axis_mapping"]
            return cls(
                experiment_root=str(data["experiment_root"]),
                dtype=str(data["dtype"]),
                settings=RunSettings(
                    bin_count=int(settings["bin_count"]),
                    memory_limit=int(settings["memory_limit"]),
                    missing_as_singleton=recorded_flag(settings["missing_as_singleton"]),
                    exclude=tuple(str(p) for p in settings["exclude"]),
                    histogram=(
                        HistogramSettings()
                        if settings.get("histogram") is None
                        else HistogramSettings.from_dict(settings["histogram"])
                    ),
                    verify_content=recorded_flag(settings.get("verify_content", False)),
                ),
                mapping=AxisMapping(str(mapping["axes"]), tuple(mapping["roles"])),
                files=tuple(_file_entry(f, strict=version != "1") for f in data["files"]),
                csv_sha256=str(data["csv_sha256"]),
                versions={str(k): str(v) for k, v in data.get("versions", {}).items()},
                written_at=str(data.get("written_at", "")),
                experiment_design_sha256=(
                    None
                    if data.get("experiment_design_sha256") is None
                    else str(data["experiment_design_sha256"])
                ),
                masks=recorded_masks(data.get("masks")),
                image_type=None if data.get("image_type") is None else str(data["image_type"]),
                channels_from_rgb_samples=(
                    None
                    if data.get("channels_from_rgb_samples") is None
                    else recorded_flag(data["channels_from_rgb_samples"])
                ),
                intensity_checks=(
                    None
                    if data.get("intensity_checks") is None
                    else IntensityCheckSettings.from_dict(data["intensity_checks"])
                ),
                narrow_ranges=tuple(
                    NarrowRange.from_dict(entry)
                    for entry in data.get("narrow_histogram_ranges") or ()
                ),
                conversion_record_sha256=optional_text(data.get("conversion_record_sha256")),
                calibration_sha256=optional_text(data.get("calibration_sha256")),
                experiment_description=recorded_description(data.get(DESCRIPTION_KEY)),
                projection=recorded_projection(data.get(PROJECTION_KEY)),
                schema_version=str(data["schema_version"]),
                manifest_version=str(version),
            )
        except ManifestFormatError:
            raise
        except UnsupportedVersionError as exc:  # a later version's manifest: plainly refused
            raise ManifestFormatError(str(exc)) from exc
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ManifestFormatError(f"The manifest is incomplete or malformed: {exc}") from exc


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


def write_manifest(manifest: RunManifest, path: str | os.PathLike) -> Path:
    """Write a manifest as JSON, replacing the file only once writing succeeded."""
    text = json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n"
    return write_text_atomic(path, text)


def read_manifest(path: str | os.PathLike) -> RunManifest:
    """Read a manifest written by :func:`write_manifest`.

    Raises
    ------
    ManifestFormatError
        If the file is not valid JSON or not a manifest of this version.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestFormatError(f"{path} is not a valid manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestFormatError(f"{path} is not a valid manifest.")
    return RunManifest.from_dict(data)


def _file_dict(entry: FileEntry, *, strict: bool = True) -> dict:
    """Return a file entry as manifest data; ``strict`` (version 2) requires roles and source.

    The writer refuses what the reader would refuse, so a written manifest always reads back.
    """
    if strict and (entry.roles is None or entry.axes_from not in AXES_FROM):
        raise ValueError(f"the entry of {entry.relative_path!r} has no roles or axes_from")
    recorded_position_id(entry.position_id, entry.relative_path, strict=strict)
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
    if entry.inventory_sha256 is not None:
        data[INVENTORY_KEY] = entry.inventory_sha256
    if entry.content_sha256 is not None:
        data[CONTENT_KEY] = entry.content_sha256
    if entry.raw_channel_names:
        data[RAW_NAMES_KEY] = list(entry.raw_channel_names)
    return data


def _file_entry(data: dict, *, strict: bool = True) -> FileEntry:
    """Read a file entry; ``strict`` (manifest version 2) requires roles, source and position.

    The ``position_id`` must be the one the entry's name gives (round-2 audit A-m5).
    """
    bits, path = data.get("significant_bits"), str(data["relative_path"])
    if strict and (data.get("roles") is None or data.get("axes_from") not in AXES_FROM):
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
        singleton_roles=tuple(str(r) for r in data.get("singleton_roles", [])),
        plane_count=int(data["plane_count"]),
        channel_names=tuple(None if n is None else str(n) for n in data.get("channel_names", [])),
        significant_bits=None if bits is None else int(bits),
        read_in_blocks=recorded_flag(data.get("read_in_blocks", False)),
        position_id=recorded_position_id(data.get("position_id"), path, strict=strict),
        channel_names_trimmed=recorded_flag(data.get("channel_names_trimmed", False)),
        roles=recorded_roles(data.get("roles"), str(data["axes"])),
        axes_from=optional_text(data.get("axes_from")),
        pixel_sizes=sizes_from_data(data.get("pixel_sizes")),
        inventory_sha256=optional_text(data.get(INVENTORY_KEY)),
        content_sha256=optional_text(data.get(CONTENT_KEY)),
        raw_channel_names=recorded_raw_names(data.get(RAW_NAMES_KEY), path),
    )


def _parent(relative_path: str) -> str:
    return relative_path.rsplit("/", 1)[0] if "/" in relative_path else "."
