"""Intensity checks on the histogram table (``docs/contract.md`` §14, follow-up P5).

After the histograms are computed, the normalization brick's
:func:`~morphobricks_normalization.summaries.image_summaries` pools every image's
Z and T planes per channel, and
:func:`~morphobricks_normalization.checks.intensity_checks` compares the images
(normalization contract §7, N38): images far from the others of their condition
and biological repeat, biological repeats shifted from the other repeats of their
condition, saturated images and images without intensity spread. Comparisons
stay within a condition, so a treatment effect is never reported. Everything is
computed from the table's rows; no image is read and nothing is written.

This module also prepares what the notebook plots, channel by channel: the
image medians and high percentiles per condition and biological repeat, with the
flagged images, and the pooled intensity distribution of each biological repeat.
The drawing itself is in :mod:`.notebook_ui`; the command line prints the
warnings only.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
from morphobricks_normalization.checks import IntensityCheckSettings, intensity_checks
from morphobricks_normalization.csv_io import read_plane_histograms
from morphobricks_normalization.issues import (
    IMAGE_INTENSITY_OUTLIER,
    NO_INTENSITY_SPREAD,
    REPEAT_INTENSITY_SHIFT,
    SATURATED_IMAGE,
    Issue,
)
from morphobricks_normalization.pooling import NoValidPixelsError, PooledHistogram
from morphobricks_normalization.records import PlaneRecord
from morphobricks_normalization.summaries import ImageSummary, image_summaries

from .validation import ValidationIssue, plural, warning

INTENSITY_CODES = (
    IMAGE_INTENSITY_OUTLIER,
    REPEAT_INTENSITY_SHIFT,
    SATURATED_IMAGE,
    NO_INTENSITY_SPREAD,
)
# Findings about one image, which the plots mark on that image.
IMAGE_CODES = (IMAGE_INTENSITY_OUTLIER, SATURATED_IMAGE, NO_INTENSITY_SPREAD)


@dataclass(frozen=True)
class IntensityReport:
    """The intensity checks of one histogram table.

    Attributes
    ----------
    summaries
        One per image, sorted by condition, biological repeat, technical repeat
        and path.
    issues
        The normalization brick's warnings, in its order: image outliers, repeat
        shifts, saturated images, images without spread. Each has the channel it
        is about (``Issue.channel``).
    settings
        The limits used.
    """

    summaries: tuple[ImageSummary, ...] = ()
    issues: tuple[Issue, ...] = ()
    settings: IntensityCheckSettings = field(default_factory=IntensityCheckSettings)

    @property
    def channel_count(self) -> int:
        """Number of channels of the images; 0 without images."""
        return self.summaries[0].channel_count if self.summaries else 0

    @property
    def high_percentile(self) -> float:
        """The high percentile of the summaries, in percent (99.8 by default)."""
        return self.summaries[0].percentile[1] if self.summaries else 99.8

    def flagged_images(self, channel: int) -> dict[str, tuple[str, ...]]:
        """Return the codes of the warnings about each image in ``channel``, by path."""
        found: dict[str, list[str]] = {}
        for issue in self.issues:
            if issue.channel == channel and issue.code in IMAGE_CODES:
                for path in issue.items:
                    codes = found.setdefault(path, [])
                    if issue.code not in codes:
                        codes.append(issue.code)
        return {path: tuple(codes) for path, codes in found.items()}

    def shifted_repeats(self, channel: int) -> set[tuple[str, str]]:
        """Return the ``(condition, biological_repeat)`` reported as shifted in ``channel``."""
        shifted = set()
        for issue in self.issues:
            if issue.channel == channel and issue.code == REPEAT_INTENSITY_SHIFT:
                for item in issue.items:
                    condition, _, repeat = item.partition("/")
                    shifted.add((condition, repeat))
        return shifted

    def validation_issues(self) -> list[ValidationIssue]:
        """Return the warnings as this brick's validation issues, for printing and showing."""
        return [warning(i.code, i.message, i.items, i.action) for i in self.issues]


@dataclass(frozen=True)
class RepeatBox:
    """The image values of one condition and biological repeat in one channel, for a box plot.

    Attributes
    ----------
    condition, biological_repeat
        The folder labels.
    paths
        The images with a value in the channel.
    medians, highs
        Their median and high percentile, in the order of ``paths``.
    flagged
        For each image, the codes of the warnings about it in the channel.
    shifted
        True when the biological repeat is reported as shifted in the channel.
    """

    condition: str
    biological_repeat: str
    paths: tuple[str, ...]
    medians: tuple[float, ...]
    highs: tuple[float, ...]
    flagged: tuple[tuple[str, ...], ...]
    shifted: bool = False

    @property
    def label(self) -> str:
        """``condition / biological repeat``, for the plot's axis."""
        return f"{self.condition} / {self.biological_repeat}"


def check_intensities(
    records: Iterable[PlaneRecord], settings: IntensityCheckSettings | None = None
) -> IntensityReport:
    """Summarise every image of the table and run the four intensity checks.

    Parameters
    ----------
    records
        Rows of ``plane_histograms.csv``.
    settings
        The limits; None uses the defaults (``outlier_limit`` 3.0,
        ``saturation_limit`` 0.01, ``min_images`` 5).

    Returns
    -------
    IntensityReport
        The summaries and warnings. Images are never read.
    """
    settings = IntensityCheckSettings() if settings is None else settings
    summaries = tuple(image_summaries(records))
    return IntensityReport(summaries, tuple(intensity_checks(summaries, settings)), settings)


def check_table(
    path: str | os.PathLike, settings: IntensityCheckSettings | None = None
) -> tuple[list[PlaneRecord], IntensityReport]:
    """Read a ``plane_histograms.csv`` and check its intensities.

    Raises
    ------
    morphobricks_normalization.csv_io.CsvFormatError
        If the file is not a histogram table this version can read.
    """
    records = read_plane_histograms(path)
    return records, check_intensities(records, settings)


def repeat_boxes(report: IntensityReport, channel: int) -> list[RepeatBox]:
    """Return the image values of every condition and biological repeat in ``channel``.

    Images without finite pixels in the channel are left out; a repeat with no
    such image is left out too. Sorted by condition, then biological repeat.
    """
    flagged = report.flagged_images(channel)
    shifted = report.shifted_repeats(channel)
    groups: dict[tuple[str, str], list[ImageSummary]] = {}
    for summary in report.summaries:
        if summary.median[channel] is None:
            continue
        groups.setdefault((summary.condition, summary.biological_repeat), []).append(summary)
    return [
        RepeatBox(
            condition,
            repeat,
            tuple(s.source_relative_path for s in members),
            tuple(s.median[channel] for s in members),
            tuple(s.p_high[channel] for s in members),
            tuple(flagged.get(s.source_relative_path, ()) for s in members),
            (condition, repeat) in shifted,
        )
        for (condition, repeat), members in sorted(groups.items())
    ]


def repeat_distributions(
    records: Iterable[PlaneRecord], channel: int
) -> dict[str, dict[str, PooledHistogram]]:
    """Pool ``channel`` of every biological repeat, condition by condition.

    Returns
    -------
    dict
        ``{condition: {biological_repeat: PooledHistogram}}``, sorted; repeats
        without finite pixels in the channel are left out.
    """
    groups: dict[tuple[str, str], list[PlaneRecord]] = {}
    for record in records:
        groups.setdefault((record.condition, record.biological_repeat), []).append(record)
    curves: dict[str, dict[str, PooledHistogram]] = {}
    for (condition, repeat), members in sorted(groups.items()):
        try:
            pooled = PooledHistogram(members, channel=channel)
        except NoValidPixelsError:
            continue
        curves.setdefault(condition, {})[repeat] = pooled
    return curves


def cumulative_share(pooled: PooledHistogram) -> np.ndarray:
    """Share of the pooled pixels at or below each bin's value."""
    return np.cumsum(pooled.counts) / pooled.pixel_count


CHECK_TITLES = {
    IMAGE_INTENSITY_OUTLIER: "Images far from the others of their biological repeat",
    REPEAT_INTENSITY_SHIFT: "Biological repeats shifted from the others of their condition",
    SATURATED_IMAGE: "Saturated images",
    NO_INTENSITY_SPREAD: "Images without intensity spread",
}


def describe_settings(settings: IntensityCheckSettings) -> str:
    """Describe the limits in plain words, for the command line and the notebook."""
    return (
        f"more than {settings.outlier_limit:g} robust deviations from the others, more than "
        f"{100 * settings.saturation_limit:g} % saturated pixels, comparisons with at least "
        f"{settings.min_images} images"
    )


def intensity_text(report: IntensityReport) -> str:
    """Describe the checks as plain text: the summary, the limits, then each kind of warning.

    Each kind lists its findings (image or repeat, channel ``C<i>`` and the
    compared values, as the normalization brick words them) and says once what
    to do.
    """
    lines = [
        f"Intensity checks: {intensity_summary(report)}",
        f"  Limits: {describe_settings(report.settings)}.",
    ]
    for code in INTENSITY_CODES:
        found = [issue for issue in report.issues if issue.code == code]
        if not found:
            continue
        lines += ["", f"{CHECK_TITLES[code]} ({code}, {len(found)}):"]
        lines += [f"  - {issue.message}" for issue in found]
        lines.append(f"  What to do: {found[0].action}")
    return "\n".join(lines)


def intensity_summary(report: IntensityReport) -> str:
    """Describe the checks in one line, e.g. ``"12 images checked: 2 intensity warnings."``."""
    images = plural(len(report.summaries), "image")
    if not report.issues:
        return f"{images} checked: no intensity warnings."
    return f"{images} checked: {plural(len(report.issues), 'intensity warning')}."
