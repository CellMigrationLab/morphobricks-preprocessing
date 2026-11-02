"""Decide which rows of an earlier run can be reused (contract §9, plan §7).

Moved from ``run.py`` (PP-3).
"""

from __future__ import annotations

import logging
from pathlib import Path

from morphobricks_normalization.csv_io import (
    OUTPUT_FILENAME,
    SCHEMA_VERSION,
    CsvFormatError,
    read_plane_histograms,
)
from morphobricks_normalization.ranges import HistogramSettings
from morphobricks_normalization.records import PlaneRecord

from ..axes import (
    AxisMapping,
)
from ..manifest import (
    LEGACY_MANIFEST_VERSION,
    MANIFEST_FILENAME,
    ManifestFormatError,
    RunManifest,
    RunSettings,
    file_sha256,
    read_manifest,
)
from ..validation import ValidationIssue, error
from .modes import FULL

logger = logging.getLogger(__name__)


def read_previous(output: Path) -> tuple[RunManifest | None, str | None]:
    """Read the earlier run's manifest in the output folder.

    Parameters
    ----------
    output
        The output folder.

    Returns
    -------
    tuple
        ``(manifest, None)``, or ``(None, why there is none)``.
    """
    manifest_path = output / MANIFEST_FILENAME
    if not manifest_path.exists():
        return None, "there is no earlier run in the output folder"
    try:
        return read_manifest(manifest_path), None
    except ManifestFormatError:
        logger.warning("Cannot read %s", manifest_path, exc_info=True)
        return None, "the earlier run manifest could not be read"


def recorded_experiment_root(
    output: Path | None,
) -> tuple[Path | None, ValidationIssue | None]:
    """Return the experiment folder the run in ``output`` recorded, or why there is none.

    Only a manifest of v0.4.0 or later (version 2) records it usably (design v0.4 §1).

    Parameters
    ----------
    output
        The output folder, resolved, or None when none was given.

    Returns
    -------
    tuple
        ``(experiment folder, None)``, or ``(None, error)``: ``experiment_not_recorded``,
        naming what is missing.
    """
    if output is None:
        why = "neither the experiment folder nor the output folder was given"
        paths: list[str] = []
    else:
        manifest, missing = read_previous(output)
        if manifest is not None and manifest.manifest_version != LEGACY_MANIFEST_VERSION:
            return Path(manifest.experiment_root), None
        why = f"the output folder records none: {missing or 'its run is older than v0.4.0'}"
        paths = [output.as_posix()]
    return None, error(
        "experiment_not_recorded",
        f"No experiment folder was given, and {why}.",
        paths,
        "Give the experiment folder, or the output folder (--output) of a run of v0.4.0 or "
        "later, whose manifest records it.",
    )


def _load_previous(
    output: Path,
    previous: tuple[RunManifest | None, str | None],
    settings: RunSettings,
    mapping: AxisMapping,
    dtype: str,
    mode: str,
) -> tuple[dict[str, list[PlaneRecord]], str | None]:
    """Load the earlier run's rows by file, and say why they cannot be reused.

    ``previous`` is :func:`read_previous`'s answer. In ``full`` mode no row is reused.
    """
    manifest, missing = previous
    csv_path = output / OUTPUT_FILENAME
    if manifest is None:
        return {}, missing
    if mode == FULL:
        return {}, "a full run was asked for (mode full)"
    if manifest.manifest_version == LEGACY_MANIFEST_VERSION:
        return {}, "the earlier run was written before v0.4.0, without the axis check"
    if manifest.schema_version != SCHEMA_VERSION:
        return {}, "the earlier table uses a different format version"
    if manifest.dtype != dtype:
        return {}, f"the pixel type changed from {manifest.dtype} to {dtype}"
    if manifest.settings.bin_count != settings.bin_count:
        return (
            {},
            f"the bin count changed from {manifest.settings.bin_count:,} to {settings.bin_count:,}",
        )
    if manifest.settings.histogram != settings.histogram:
        before = _describe_histogram(manifest.settings.histogram)
        now = _describe_histogram(settings.histogram)
        if before == now:
            return {}, "the limit of the histogram range check changed"
        return {}, f"the histogram range changed from {before} to {now}"
    if (manifest.mapping.axes, manifest.mapping.roles) != (mapping.axes, mapping.roles):
        return {}, "the axis mapping changed"
    if not csv_path.exists() or file_sha256(csv_path) != manifest.csv_sha256:
        return {}, "the histogram table was changed or replaced after the earlier run"
    try:
        rows = read_plane_histograms(csv_path)
    except CsvFormatError:
        logger.warning("Cannot read %s", csv_path, exc_info=True)
        return {}, "the earlier histogram table could not be read"
    by_file: dict[str, list[PlaneRecord]] = {}
    for record in rows:
        by_file.setdefault(record.source_relative_path, []).append(record)
    return by_file, None


def _describe_histogram(settings: HistogramSettings) -> str:
    """Describe the histogram range in words, e.g. ``"robust (0.01-99.99 %)"``."""
    if settings.robust:
        low, high = settings.robust_quantiles
        return f"robust ({low:g}-{high:g} %)"
    return "minimum to maximum"
