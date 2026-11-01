"""Compute the table's rows, one per XYC plane, and the checks on them (contract §4).

Moved from ``run.py`` (PP-3).
"""

from __future__ import annotations

from pathlib import Path

from morphobricks_normalization.dtypes import default_bin_count, validate_bin_count
from morphobricks_normalization.ranges import range_check_issues
from morphobricks_normalization.records import PlaneRecord

from ..axes import (
    AxisMapping,
)
from ..manifest import (
    NarrowRange,
    RunSettings,
)
from ..planes import (
    iter_xyc_planes,
)
from ..validation import ValidationIssue, error, plural, warning


def _read_file(
    path: Path,
    rel: str,
    labels: tuple[str, str, str],
    mapping: AxisMapping,
    settings: RunSettings,
    position: str | None,
) -> tuple[list[PlaneRecord], list[NarrowRange]]:
    """Histogram every XYC plane of ``path``, recording the rows under the source ``rel``.

    One row per plane holds the histograms of all its channels (schema version 4),
    binned with the run's histogram range. Also returns the channel planes whose
    range check found the central range too narrow.
    """
    records, narrow = [], []
    for plane in iter_xyc_planes(
        path,
        mapping,
        memory_limit=settings.memory_limit,
        bin_count=settings.bin_count,
        histogram_settings=settings.histogram,
    ):
        measured = plane.histograms_with_checks(settings.bin_count, settings.histogram)
        record = PlaneRecord(
            rel,
            *labels,
            histograms=tuple(histogram for histogram, _ in measured),
            z=plane.z,
            t=plane.t,
            position_id=position,
        )
        records.append(record)
        narrow.extend(
            NarrowRange(rel, record.plane_id, channel, check)
            for channel, (_, check) in enumerate(measured)
            if check is not None and check.narrow
        )
    return records, narrow


def _bin_count(dtype, bin_count: int | None) -> tuple[int | None, list[ValidationIssue]]:  # noqa: ANN001
    if bin_count is None:
        return default_bin_count(dtype), []
    try:
        return validate_bin_count(dtype, bin_count), []
    except ValueError as exc:
        return None, [
            error(
                "invalid_bin_count",
                str(exc),
                action="Choose a bin count in the allowed range, or use the default.",
            )
        ]


def _range_issues(narrow: list[NarrowRange]) -> list[ValidationIssue]:
    """Return the brick's ``narrow_histogram_range`` warnings, one per channel (N60)."""
    found = range_check_issues((entry.plane_id, entry.channel, entry.check) for entry in narrow)
    return [warning(issue.code, issue.message, issue.items, issue.action) for issue in found]


def _pixel_issues(records: list[PlaneRecord]) -> list[ValidationIssue]:
    # Every channel of every row is checked.
    channels = [(r.source_relative_path, h) for r in records for h in r.histograms]
    issues = []
    with_non_finite = sorted({rel for rel, h in channels if h.non_finite_pixel_count})
    if with_non_finite:
        total = sum(h.non_finite_pixel_count for _, h in channels)
        issues.append(
            warning(
                "non_finite_pixels",
                f"{plural(len(with_non_finite), 'image')} contain {plural(total, 'pixel')} that "
                "are not numbers or infinite (NaN, ±infinity). They are left out of the "
                "histograms and counted separately.",
                with_non_finite,
                "No action needed if these values mark missing data.",
            )
        )
    empty = [rel for rel, h in channels if h.is_empty]
    if empty and len(empty) == len(channels):
        issues.append(
            error(
                "no_usable_planes",
                "No plane has any finite pixel value, so no statistics can be calculated.",
                sorted(set(empty)),
                "Check that these are the right images and that they were exported correctly.",
            )
        )
    elif empty:
        planes = {r.plane_id for r in records if any(h.is_empty for h in r.histograms)}
        issues.append(
            warning(
                "no_finite_pixels",
                f"{plural(len(planes), 'plane')} have no finite pixel values in at least one "
                "channel. These channels are kept in the table with zero counts and left out "
                "of group statistics.",
                sorted(set(empty)),
                "No action needed if these planes are expected to be empty.",
            )
        )
    return issues


def _row_order(record: PlaneRecord) -> tuple:
    return (record.source_relative_path, record.z, record.t)
