"""The run manifest: what a run read, with which settings, and what it wrote.

``run_manifest.json`` sits next to ``plane_histograms.csv``. It records the
confirmed folder labels and axis mapping, the settings, the package versions,
and each source file's size and modification time. The next run compares these
with the experiment folder to find new, changed and deleted files, so unchanged
images are never read again (``docs/plan.md`` §7). The format is described in
``docs/contract.md`` §9.

The manifest also records, for information only, each file's channel names and
significant bits when its metadata provides them, the SHA-256 of the
experiment design used, so later bricks can check that it was not changed, and
the image type the user confirmed, which later bricks normalize by, and the
limits of the intensity checks run on the table. It also records the histogram
range the table was binned with, and the planes whose central intensity range
was too narrow (follow-up P6), so a later run that keeps their rows still
reports them.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import morphobricks_normalization
import numpy as np
import tifffile
from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.csv_io import SCHEMA_VERSION
from morphobricks_normalization.ranges import HistogramSettings, RangeCheck

from ._io import write_text_atomic
from .axes import AxisMapping
from .discovery import position_id

MANIFEST_FILENAME = "run_manifest.json"
MANIFEST_VERSION = "1"


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
    """

    bin_count: int
    memory_limit: int
    missing_as_singleton: bool = False
    exclude: tuple[str, ...] = ()
    histogram: HistogramSettings = field(default_factory=HistogramSettings)

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
        True when leading or trailing whitespace was removed from the image's
        channel names as they were read (names are recorded trimmed).
    read_in_blocks
        True when the image's planes are too large for the memory limit and are
        read in blocks. For information only: the rows are the same either way.
    position_id
        The image's position identifier, its name without the extension
        (:func:`~morphobricks_preprocessing.discovery.position_id`); None in
        manifests written before it was recorded.
    roles
        The role of each of the file's axes (``"X"``, ``"Y"``, ``"Z"``, ``"C"``,
        ``"T"`` or None), from its own mapping: for an OME-Zarr image whose
        declared order differs from the experiment mapping's, these are its
        roles, not the experiment's. None in manifests written before they were
        recorded.
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

    def same_source(self, other: FileEntry) -> bool:
        """Return True if ``other`` describes the same unchanged file, read the same way."""
        return (
            self.relative_path == other.relative_path
            and self.size == other.size
            and self.mtime_ns == other.mtime_ns
            and self.labels == other.labels
            and self.axes == other.axes
            and self.singleton_roles == other.singleton_roles
        )


@dataclass(frozen=True)
class NarrowRange:
    """A channel plane whose central intensity range covers too few histogram bins.

    Attributes
    ----------
    relative_path
        The image the plane belongs to.
    plane_id
        The plane's ``plane_id`` in the table.
    channel
        The zero-based channel.
    check
        The normalization brick's ``RangeCheck`` of the plane's channel.
    """

    relative_path: str
    plane_id: str
    channel: int
    check: RangeCheck

    def to_dict(self) -> dict:
        """Return the entry as JSON-compatible data."""
        check = {name: _plain(value) for name, value in asdict(self.check).items()}
        return {
            "relative_path": self.relative_path,
            "plane_id": self.plane_id,
            "channel": self.channel,
            **check,
        }

    @classmethod
    def from_dict(cls, data: dict) -> NarrowRange:
        """Read an entry written by :meth:`to_dict`."""
        return cls(
            relative_path=str(data["relative_path"]),
            plane_id=str(data["plane_id"]),
            channel=int(data["channel"]),
            check=RangeCheck(
                histogram_range=str(data["histogram_range"]),
                central_percentiles=tuple(float(v) for v in data["central_percentiles"]),
                central_bins=int(data["central_bins"]),
                robust_quantiles=tuple(float(v) for v in data["robust_quantiles"]),
                robust_limits=tuple(data["robust_limits"]),
                outside_robust_count=int(data["outside_robust_count"]),
                robust_central_bins=int(data["robust_central_bins"]),
                min_central_bins=int(data["min_central_bins"]),
            ),
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
    schema_version
        Version of the CSV format.
    manifest_version
        Version of the manifest format.
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
        """Return the manifest as JSON-compatible data."""
        data = {
            "manifest_version": self.manifest_version,
            "schema_version": self.schema_version,
            "written_at": self.written_at,
            "experiment_root": self.experiment_root,
            "dtype": self.dtype,
            "settings": {
                "bin_count": self.settings.bin_count,
                "memory_limit": self.settings.memory_limit,
                "missing_as_singleton": _flag(
                    self.settings.missing_as_singleton, "missing_as_singleton"
                ),
                "exclude": list(self.settings.exclude),
                "histogram": self.settings.histogram.to_dict(),
            },
            "axis_mapping": {"axes": self.mapping.axes, "roles": list(self.mapping.roles)},
            "labels": [
                {
                    "folder": folder,
                    "condition": labels[0],
                    "biological_repeat": labels[1],
                    "technical_repeat": labels[2],
                }
                for folder, labels in self.leaves.items()
            ],
            "files": [_file_dict(f) for f in self.files],
            "csv_sha256": self.csv_sha256,
            "experiment_design_sha256": self.experiment_design_sha256,
            "image_type": self.image_type,
            "channels_from_rgb_samples": (
                None
                if self.channels_from_rgb_samples is None
                else _flag(self.channels_from_rgb_samples, "channels_from_rgb_samples")
            ),
            "intensity_checks": (
                None if self.intensity_checks is None else self.intensity_checks.to_dict()
            ),
            "narrow_histogram_ranges": [entry.to_dict() for entry in self.narrow_ranges],
            "versions": dict(self.versions),
        }
        if self.masks is not None:
            data["masks"] = self.masks
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
            if data["manifest_version"] != MANIFEST_VERSION:
                raise ManifestFormatError(
                    f"Manifest version {data['manifest_version']!r} is not supported "
                    f"(this version reads {MANIFEST_VERSION!r})."
                )
            settings = data["settings"]
            mapping = data["axis_mapping"]
            return cls(
                experiment_root=str(data["experiment_root"]),
                dtype=str(data["dtype"]),
                settings=RunSettings(
                    bin_count=int(settings["bin_count"]),
                    memory_limit=int(settings["memory_limit"]),
                    missing_as_singleton=_flag(settings["missing_as_singleton"]),
                    exclude=tuple(str(p) for p in settings["exclude"]),
                    histogram=(
                        HistogramSettings()
                        if settings.get("histogram") is None
                        else HistogramSettings.from_dict(settings["histogram"])
                    ),
                ),
                mapping=AxisMapping(str(mapping["axes"]), tuple(mapping["roles"])),
                files=tuple(_file_entry(f) for f in data["files"]),
                csv_sha256=str(data["csv_sha256"]),
                versions={str(k): str(v) for k, v in data.get("versions", {}).items()},
                written_at=str(data.get("written_at", "")),
                experiment_design_sha256=(
                    None
                    if data.get("experiment_design_sha256") is None
                    else str(data["experiment_design_sha256"])
                ),
                masks=_masks(data.get("masks")),
                image_type=None if data.get("image_type") is None else str(data["image_type"]),
                channels_from_rgb_samples=(
                    None
                    if data.get("channels_from_rgb_samples") is None
                    else _flag(data["channels_from_rgb_samples"])
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
                schema_version=str(data["schema_version"]),
            )
        except ManifestFormatError:
            raise
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
    size, mtime_ns = source_fingerprint(Path(root) / relative_path)
    return FileEntry(
        relative_path=relative_path,
        size=size,
        mtime_ns=mtime_ns,
        labels=tuple(labels),
        axes=mapping.axes,
        singleton_roles=mapping.singleton_roles,
        plane_count=plane_count,
        channel_names=tuple(channel_names),
        significant_bits=significant_bits,
        read_in_blocks=read_in_blocks,
        position_id=position_id(relative_path),
        channel_names_trimmed=channel_names_trimmed,
        roles=tuple(mapping.roles),
    )


def source_fingerprint(path: str | os.PathLike) -> tuple[int, int]:
    """Size and modification time used to tell whether an image changed.

    A TIFF file gives its own size and time. An OME-Zarr folder gives the total
    size of its files and the time of the newest one, so rewriting any chunk or
    metadata file counts as a change.
    """
    path = Path(path)
    if not path.is_dir():
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    size, newest = 0, path.stat().st_mtime_ns
    for folder, _, names in os.walk(path):
        for name in names:
            stat = (Path(folder) / name).stat()
            size += stat.st_size
            newest = max(newest, stat.st_mtime_ns)
    return size, newest


def package_versions() -> dict[str, str]:
    """Versions of the packages that determine the output."""
    try:
        own = version("morphobricks-preprocessing")
    except PackageNotFoundError:  # running from a source checkout without installing
        own = "0.0.0+unknown"
    return {
        "morphobricks-preprocessing": own,
        # Computes the histograms and writes the CSV.
        "morphobricks-normalization": morphobricks_normalization.__version__,
        "numpy": np.__version__,
        "tifffile": tifffile.__version__,
        "python": platform.python_version(),
    }


def file_sha256(path: str | os.PathLike) -> str:
    """SHA-256 of a file, read in blocks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _masks(data: object) -> dict | None:
    """Check the optional ``masks`` entry of a manifest (absent in older manifests)."""
    if data is None:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise ValueError("its masks entry is not a mask list")
    return data


def _file_dict(entry: FileEntry) -> dict:
    return {
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
        "read_in_blocks": _flag(entry.read_in_blocks, "read_in_blocks"),
        "position_id": entry.position_id,
        "channel_names_trimmed": _flag(entry.channel_names_trimmed, "channel_names_trimmed"),
        "roles": None if entry.roles is None else list(entry.roles),
    }


def _file_entry(data: dict) -> FileEntry:
    bits = data.get("significant_bits")
    return FileEntry(
        relative_path=str(data["relative_path"]),
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
        read_in_blocks=_flag(data.get("read_in_blocks", False)),
        position_id=None if data.get("position_id") is None else str(data["position_id"]),
        channel_names_trimmed=_flag(data.get("channel_names_trimmed", False)),
        roles=_roles(data.get("roles"), str(data["axes"])),
    )


def _roles(value: object, axes: str) -> tuple[str | None, ...] | None:
    """Check a file's recorded axis roles: one role or null per axis (absent in older manifests)."""
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != len(axes):
        raise ValueError(f"the roles {value!r} do not fit the axes {axes!r}")
    roles = tuple(None if r is None else str(r) for r in value)
    AxisMapping(axes, roles)  # raises ValueError for an unknown role
    return roles


def _flag(value: object, key: str | None = None) -> bool:
    """Return a boolean; anything else (``"false"``, 0, null) is malformed, not guessed.

    Used both when reading and when writing a manifest, so a manifest is never written
    with a flag its reader would refuse (or that ``bool()`` would turn around).
    """
    if not isinstance(value, bool):
        where = f" for {key}" if key else ""
        raise ValueError(f"expected true or false{where}, found {value!r}")
    return value


def _plain(value):  # noqa: ANN001, ANN202
    """Return a range check value as JSON data: Python numbers, lists for tuples."""
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _parent(relative_path: str) -> str:
    return relative_path.rsplit("/", 1)[0] if "/" in relative_path else "."
