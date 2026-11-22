"""Compute the table's rows, one per XYC plane, and the checks on them (contract §4).

Moved from ``run.py`` (PP-3).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from morphobricks_normalization.dtypes import default_bin_count, validate_bin_count
from morphobricks_normalization.histogram import PlaneHistogram, histogram_with_range_check
from morphobricks_normalization.ranges import RangeCheck, range_check_issues
from morphobricks_normalization.records import PlaneRecord

from ..axes import (
    AxisMapping,
)
from ..image_qc import ImageQc, PlaneQc
from ..image_qc_table import QcRow
from ..manifest import (
    NarrowRange,
    RunSettings,
)
from ..plane_errors import PlaneReadError
from ..planes import (
    Plane,
    iter_xyc_planes,
)
from ..validation import ValidationIssue, error, plural, warning


@dataclass(frozen=True)
class ChannelFailure:
    """One channel of one plane whose histogram could not be measured (Spec ADR 0003 HV3).

    Attributes
    ----------
    t, z
        The plane's T and Z index; None when the image has no such axis.
    c
        The channel's C index (source channel ``C<c>``).
    channel
        Its canonical channel ID, from the description.
    cause
        Why it could not be measured, with the image named by its relative path.
    """

    t: int | None
    z: int | None
    c: int
    channel: str
    cause: str

    def text(self, rel: str) -> str:
        """Return ``"<rel>, plane t=1, z=0, c=1 (channel GFP): <cause>"`` (absent axes left out)."""
        found = [f"{axis}={index}" for axis, index in (("t", self.t), ("z", self.z), ("c", self.c))]
        plane = ", ".join(entry for entry in found if not entry.endswith("=None"))
        return f"{rel}, plane {plane} (channel {self.channel}): {self.cause}"


class ChannelReadError(PlaneReadError):
    """Raised when channels of an image's planes could not be measured; names every one."""

    def __init__(self, failures: tuple[ChannelFailure, ...]) -> None:
        super().__init__("; ".join(f"t={f.t}, z={f.z}, c={f.c}: {f.cause}" for f in failures))
        self.failures = failures


def _read_file(
    path: Path,
    rel: str,
    labels: tuple[str, str, str],
    mapping: AxisMapping,
    settings: RunSettings,
    position: str,
    channels: tuple[str, ...],
) -> tuple[list[PlaneRecord], list[NarrowRange], list[QcRow]]:
    """Histogram every XYC plane of ``path``, recording the rows under the source ``rel``.

    One row per plane holds the histograms of all its channels (schema version 5), named by
    ``channels``, the canonical IDs of its unit's confirmed list (entry ``i`` is source
    channel ``C<i>``), binned with the run's histogram range. Also returns the channel planes
    whose range check found the central range too narrow, by channel ID, and the image QC
    row of every channel plane, measured in the same pass (contract §19).

    A channel that cannot be measured fails the image (Spec ADR 0003 HV3); the other planes
    are measured still, so that :class:`ChannelReadError` names every failed one.
    """
    records, narrow, failed, qc = [], [], [], []
    for plane in iter_xyc_planes(
        path,
        mapping,
        memory_limit=settings.memory_limit,
        bin_count=settings.bin_count,
        histogram_settings=settings.histogram,
    ):
        measured, in_blocks = [], []
        for c in range(plane.channel_count):
            try:
                channel_plane = plane.channel(c)
                in_blocks.append(channel_plane.in_blocks)
                measured.append(_measure(channel_plane, settings))
            except PlaneReadError as exc:
                # Measure on, so that the error names every failed plane (HV3); none of
                # this image's rows is used.
                cause = str(exc).replace(str(path), rel)
                failed.append(ChannelFailure(plane.t, plane.z, c, channels[c], cause))
        if failed:
            continue
        record = PlaneRecord(
            rel,
            *labels,
            histograms=tuple(histogram for histogram, _, _ in measured),
            z=plane.z,
            t=plane.t,
            position_id=position,
            channels=channels,
        )
        records.append(record)
        narrow.extend(
            NarrowRange(rel, record.plane_id, channel, check)
            for channel, (_, check, _) in zip(channels, measured, strict=True)
            if check is not None and check.narrow
        )
        qc.extend(
            QcRow.of(record, channel, blocks, found)
            for channel, blocks, (_, _, found) in zip(channels, in_blocks, measured, strict=True)
        )
    if failed:
        raise ChannelReadError(tuple(failed))
    return records, narrow, qc


def _measure(
    plane: Plane, settings: RunSettings
) -> tuple[PlaneHistogram, RangeCheck | None, ImageQc]:
    """Return a channel plane's histogram, range check and QC metrics, from the same reads.

    The QC sums are taken from the blocks of the histogram's first pass over the plane, so
    no pixel is read again for them (contract §19).
    """
    qc = PlaneQc(plane.shape, plane.dtype)
    passes = []

    def blocks() -> Iterator[np.ndarray]:
        """Yield the plane's blocks; the first pass also adds them to the QC sums."""
        tap = not passes
        passes.append(tap)
        for block in plane.blocks():
            if tap:
                qc.add(block.y, block.x, block.pixels)
            yield block.pixels

    histogram, check = histogram_with_range_check(
        blocks, plane.dtype, settings.bin_count, settings=settings.histogram
    )
    return histogram, check, qc.finish(histogram)


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
