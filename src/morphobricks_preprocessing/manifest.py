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
limits of the intensity and channel-identity checks, the histogram range the table was binned
with, and the planes whose central intensity range was too narrow (follow-up P6), so a later
run that keeps their rows still reports them. For projected copies it records the projection.
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
from .channel_identity_model import ChannelIdentitySettings, recorded_settings, settings_entry
from .description_record import KEY as DESCRIPTION_KEY
from .description_record import recorded_description
from .image_qc_record import KEY as IMAGE_QC_KEY
from .image_qc_record import recorded_image_qc
from .lab_links import KEY as LAB_LINKS_KEY
from .lab_links import recorded_lab_links
from .manifest_fields import (
    optional_text,
    recorded_flag,
    recorded_masks,
)
from .manifest_files import (
    AXES_FROM as AXES_FROM,  # re-exported (moved)
)
from .manifest_files import (
    FileEntry as FileEntry,  # re-exported (moved)
)
from .manifest_files import (
    file_dict,
    file_from_data,
)
from .manifest_files import (
    file_entry_for as file_entry_for,  # re-exported (moved)
)
from .manifest_files import (
    file_entry_with as file_entry_with,  # re-exported (moved)
)
from .manifest_versions import MANIFEST_VERSION as MANIFEST_VERSION
from .manifest_versions import (
    RERUN,
    UnsupportedVersionError,
    check_version,
    recorded_version,
)
from .narrow_ranges import NarrowRange
from .projection_run import KEY as PROJECTION_KEY
from .projection_run import recorded_projection
from .versions import package_versions as package_versions  # re-exported (moved)

MANIFEST_FILENAME = "run_manifest.json"
_LABEL_KEYS = ("folder", "condition", "biological_repeat", "technical_repeat")


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
        Working-memory limit per plane, in bytes. It changes no histogram row, but it sets the
        blocks of planes read in blocks, which the image QC metrics depend on (contract §19):
        another limit recomputes the rows when planes are read in blocks (``run.reuse``).
    missing_as_singleton
        Whether the user confirmed that files lacking axes have one plane along
        them.
    exclude
        Relative paths of images the user excluded from the run, sorted.
    histogram
        The histogram range of histograms that are not exact, with the limits of
        the range check (the normalization brick's ``HistogramSettings``); always recorded.
    verify_content
        Strict reuse (CLI ``--verify-content``): each source's content SHA-256 is recorded
        (:attr:`FileEntry.content_sha256`) and must match before its staged or earlier rows
        are reused. False: the fingerprint only. Always recorded.
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
        SHA-256 of the ``experiment_design.csv`` written with this run, or None when the run
        had no experiment design (or in manifests written before it was recorded).
    masks
        The masks of the images when masks were given (contract §15): the masks
        folder, the mask types and one entry per mask (image, type, path,
        source); None otherwise. The key is left out of the file when None.
    image_type
        ``"fluorescence"`` or ``"rgb"``, as the user confirmed it (:mod:`.image_type`); None
        when the run was not given one (or in manifests written before it was recorded).
    channels_from_rgb_samples
        True when the channel axis is the RGB samples axis of images stored as
        RGB colour; None in manifests written before it was recorded.
    intensity_checks
        The limits of the intensity checks run on the table (:mod:`.intensity`);
        None in manifests written before they were recorded.
    channel_identity
        The limits of the channel-identity check (:mod:`.channel_identity`); None before it.
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
        What the run records about its published ``morphobricks.toml``
        (:mod:`.description_record`). Every manifest carries it; None is refused when written.
    projection
        The Z projection of the images (:mod:`.projection_run`); None (key left out) if none.
    lab_links
        The lab-tool links the user gave the run (:mod:`.lab_links`), provenance only; None
        (key left out) if none.
    image_qc
        The image QC table written with the run (:mod:`.image_qc_record`: file, SHA-256,
        metric version and settings); None (key left out) in manifests written before it.
    schema_version
        Version of the CSV format.
    manifest_version
        Version of the manifest format (:mod:`.manifest_versions`): ``"4"``, the only one
        written and read; an older manifest is refused (run preprocessing again).
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
    channel_identity: ChannelIdentitySettings | None = None
    narrow_ranges: tuple[NarrowRange, ...] = ()
    conversion_record_sha256: str | None = None
    calibration_sha256: str | None = None
    experiment_description: dict | None = None
    projection: dict | None = None
    lab_links: dict | None = None
    image_qc: dict | None = None
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
                # Always written (#159): a run that opted out of content hashing says so.
                "verify_content": recorded_flag(self.settings.verify_content, "verify_content"),
            },
            "axis_mapping": {"axes": self.mapping.axes, "roles": list(self.mapping.roles)},
            "labels": [
                dict(zip(_LABEL_KEYS, (folder, *labels), strict=True))
                for folder, labels in self.leaves.items()
            ],
            "files": [file_dict(f) for f in self.files],
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
            "channel_identity": settings_entry(self.channel_identity),
            "narrow_histogram_ranges": [entry.to_dict() for entry in self.narrow_ranges],
            "conversion_record_sha256": self.conversion_record_sha256,
            "calibration_sha256": self.calibration_sha256,
            "versions": dict(self.versions),
        }
        optional = {"masks": self.masks, DESCRIPTION_KEY: self.experiment_description}
        optional[PROJECTION_KEY] = self.projection
        optional[LAB_LINKS_KEY] = recorded_lab_links(self.lab_links)
        optional[IMAGE_QC_KEY] = recorded_image_qc(self.image_qc)
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
                    histogram=HistogramSettings.from_dict(settings["histogram"]),
                    verify_content=recorded_flag(settings["verify_content"], "verify_content"),
                ),
                mapping=AxisMapping(str(mapping["axes"]), tuple(mapping["roles"])),
                files=tuple(file_from_data(f) for f in data["files"]),
                csv_sha256=str(data["csv_sha256"]),
                versions={str(k): str(v) for k, v in data.get("versions", {}).items()},
                written_at=str(data.get("written_at", "")),
                experiment_design_sha256=optional_text(data.get("experiment_design_sha256")),
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
                channel_identity=recorded_settings(data.get("channel_identity")),
                narrow_ranges=tuple(
                    NarrowRange.from_dict(entry)
                    for entry in data.get("narrow_histogram_ranges") or ()
                ),
                conversion_record_sha256=optional_text(data.get("conversion_record_sha256")),
                calibration_sha256=optional_text(data.get("calibration_sha256")),
                experiment_description=recorded_description(data.get(DESCRIPTION_KEY)),
                projection=recorded_projection(data.get(PROJECTION_KEY)),
                lab_links=recorded_lab_links(data.get(LAB_LINKS_KEY)),
                image_qc=recorded_image_qc(data.get(IMAGE_QC_KEY)),
                schema_version=str(data["schema_version"]),
                manifest_version=str(version),
            )
        except ManifestFormatError:
            raise
        except UnsupportedVersionError as exc:  # a later version's manifest: plainly refused
            raise ManifestFormatError(str(exc)) from exc
        except KeyError as exc:  # a key every current manifest writes: never defaulted (PP-05)
            raise ManifestFormatError(
                f"The manifest is incomplete: it has no {exc.args[0]!r}, which every version "
                f"{MANIFEST_VERSION} manifest records. {RERUN}"
            ) from exc
        except (TypeError, ValueError, AttributeError) as exc:
            raise ManifestFormatError(f"The manifest is incomplete or malformed: {exc}") from exc


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


def _parent(relative_path: str) -> str:
    return relative_path.rsplit("/", 1)[0] if "/" in relative_path else "."
