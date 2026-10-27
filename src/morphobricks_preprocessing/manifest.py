"""The run manifest: what a run read, with which settings, and what it wrote.

``run_manifest.json`` sits next to ``plane_histograms.csv``. It records the
confirmed folder labels and axis mapping, the settings, the package versions,
and each source file's size and modification time. The next run compares these
with the experiment folder to find new, changed and deleted files, so unchanged
images are never read again (``docs/plan.md`` §7). The format is described in
``docs/contract.md`` §9.

The manifest also records, for information only, each file's channel names and
significant bits when its metadata provides them, and the SHA-256 of the
experiment design used, so later bricks can check that it was not changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import morphobricks_normalization
import numpy as np
import tifffile
from morphobricks_normalization.csv_io import SCHEMA_VERSION

from .axes import AxisMapping
from .discovery import position_id
from .zarr_export import ConversionSettings

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
    convert_tiffs
        Whether TIFF images are converted to OME-Zarr in the output folder and
        their histograms computed from the converted copies.
    conversion
        How the copies are written (chunk size, compression, smaller levels);
        only used when ``convert_tiffs`` is on. It does not change any row.
    """

    bin_count: int
    memory_limit: int
    missing_as_singleton: bool = False
    exclude: tuple[str, ...] = ()
    convert_tiffs: bool = False
    conversion: ConversionSettings = field(default_factory=ConversionSettings)

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
    ome_zarr_path
        For a TIFF converted to OME-Zarr, the converted copy relative to the
        output folder (its rows are computed from it); otherwise None.
    read_in_blocks
        True when the image's planes are too large for the memory limit and are
        read in blocks. For information only: the rows are the same either way.
    position_id
        The image's position identifier, its name without the extension
        (:func:`~morphobricks_preprocessing.discovery.position_id`); None in
        manifests written before it was recorded.
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
    ome_zarr_path: str | None = None
    read_in_blocks: bool = False
    position_id: str | None = None

    def same_source(self, other: FileEntry) -> bool:
        """Return True if ``other`` describes the same unchanged file, read the same way."""
        return (
            self.relative_path == other.relative_path
            and self.size == other.size
            and self.mtime_ns == other.mtime_ns
            and self.labels == other.labels
            and self.axes == other.axes
            and self.singleton_roles == other.singleton_roles
            and self.ome_zarr_path == other.ome_zarr_path
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
        return {
            "manifest_version": self.manifest_version,
            "schema_version": self.schema_version,
            "written_at": self.written_at,
            "experiment_root": self.experiment_root,
            "dtype": self.dtype,
            "settings": {
                "bin_count": self.settings.bin_count,
                "memory_limit": self.settings.memory_limit,
                "missing_as_singleton": self.settings.missing_as_singleton,
                "exclude": list(self.settings.exclude),
                "convert_tiffs": self.settings.convert_tiffs,
                "conversion": self.settings.conversion.to_dict(),
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
            "versions": dict(self.versions),
        }

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
                    missing_as_singleton=bool(settings["missing_as_singleton"]),
                    exclude=tuple(str(p) for p in settings["exclude"]),
                    convert_tiffs=bool(settings.get("convert_tiffs", False)),
                    conversion=ConversionSettings.from_dict(settings.get("conversion")),
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
                schema_version=str(data["schema_version"]),
            )
        except ManifestFormatError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ManifestFormatError(f"The manifest is incomplete or malformed: {exc}") from exc


def file_entry_for(
    root: str | os.PathLike,
    relative_path: str,
    labels: tuple[str, str, str],
    mapping: AxisMapping,
    *,
    plane_count: int = 0,
    channel_names: tuple[str | None, ...] = (),
    significant_bits: int | None = None,
    ome_zarr_path: str | None = None,
    read_in_blocks: bool = False,
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
        ome_zarr_path=ome_zarr_path,
        read_in_blocks=read_in_blocks,
        position_id=position_id(relative_path),
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
    path = Path(path)
    text = json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", newline="\n", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


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
        "ome_zarr_path": entry.ome_zarr_path,
        "read_in_blocks": entry.read_in_blocks,
        "position_id": entry.position_id,
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
        ome_zarr_path=None if data.get("ome_zarr_path") is None else str(data["ome_zarr_path"]),
        read_in_blocks=bool(data.get("read_in_blocks", False)),
        position_id=None if data.get("position_id") is None else str(data["position_id"]),
    )


def _parent(relative_path: str) -> str:
    return relative_path.rsplit("/", 1)[0] if "/" in relative_path else "."
