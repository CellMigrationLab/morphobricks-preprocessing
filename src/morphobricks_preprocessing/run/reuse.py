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
    MANIFEST_FILENAME,
    ManifestFormatError,
    RunManifest,
    RunSettings,
    file_sha256,
    read_manifest,
)

logger = logging.getLogger(__name__)


def _load_previous(
    output: Path, settings: RunSettings, mapping: AxisMapping, dtype: str, force: bool
) -> tuple[RunManifest | None, dict[str, list[PlaneRecord]], str | None]:
    """Load the earlier manifest and its rows by file, and say why they cannot be reused.

    The manifest is returned whenever it can be read, so files can still be
    reported as new, changed or unchanged during a rebuild.
    """
    manifest_path, csv_path = output / MANIFEST_FILENAME, output / OUTPUT_FILENAME
    if not manifest_path.exists():
        return None, {}, "there is no earlier run in the output folder"
    try:
        manifest = read_manifest(manifest_path)
    except ManifestFormatError:
        logger.warning("Cannot read %s", manifest_path, exc_info=True)
        return None, {}, "the earlier run manifest could not be read"
    if force:
        return manifest, {}, "a full rebuild was requested"
    if manifest.schema_version != SCHEMA_VERSION:
        return manifest, {}, "the earlier table uses a different format version"
    if manifest.dtype != dtype:
        return manifest, {}, f"the pixel type changed from {manifest.dtype} to {dtype}"
    if manifest.settings.bin_count != settings.bin_count:
        return (
            manifest,
            {},
            f"the bin count changed from {manifest.settings.bin_count:,} to {settings.bin_count:,}",
        )
    if manifest.settings.histogram != settings.histogram:
        before = _describe_histogram(manifest.settings.histogram)
        now = _describe_histogram(settings.histogram)
        if before == now:
            return manifest, {}, "the limit of the histogram range check changed"
        return manifest, {}, f"the histogram range changed from {before} to {now}"
    if (manifest.mapping.axes, manifest.mapping.roles) != (mapping.axes, mapping.roles):
        return manifest, {}, "the axis mapping changed"
    if not csv_path.exists() or file_sha256(csv_path) != manifest.csv_sha256:
        return manifest, {}, "the histogram table was changed or replaced after the earlier run"
    try:
        rows = read_plane_histograms(csv_path)
    except CsvFormatError:
        logger.warning("Cannot read %s", csv_path, exc_info=True)
        return manifest, {}, "the earlier histogram table could not be read"
    by_file: dict[str, list[PlaneRecord]] = {}
    for record in rows:
        by_file.setdefault(record.source_relative_path, []).append(record)
    return manifest, by_file, None


def _describe_histogram(settings: HistogramSettings) -> str:
    """Describe the histogram range in words, e.g. ``"robust (0.01-99.99 %)"``."""
    if settings.robust:
        low, high = settings.robust_quantiles
        return f"robust ({low:g}-{high:g} %)"
    return "minimum to maximum"
