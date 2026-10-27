import numpy as np
import pytest
from morphobricks_normalization.checks import IntensityCheckSettings, intensity_checks
from morphobricks_normalization.csv_io import write_plane_histograms
from morphobricks_normalization.histogram import compute_plane_histogram
from morphobricks_normalization.records import PlaneRecord
from morphobricks_normalization.summaries import image_summaries

from morphobricks_preprocessing.intensity import (
    INTENSITY_CODES,
    check_intensities,
    check_table,
    cumulative_share,
    intensity_summary,
    repeat_boxes,
    repeat_distributions,
)


def record(path, condition, repeat, channels, z=0):
    """One table row: ``channels`` are 2D arrays, one per channel."""
    return PlaneRecord(
        source_relative_path=path,
        condition=condition,
        biological_repeat=repeat,
        technical_repeat="single",
        histograms=[compute_plane_histogram(c) for c in channels],
        z=z,
    )


def plane(level, rng, spread=10, shape=(16, 16)):
    return np.clip(rng.normal(level, spread, shape), 0, 254).astype(np.uint8)


def rows(levels_by_group, seed=0, saturated=(), blank=()):
    """Two channels per image: channel 0 at the given level, channel 1 the same in every image."""
    rng = np.random.default_rng(seed)
    second = plane(60, np.random.default_rng(99))
    records = []
    for (condition, repeat), levels in levels_by_group.items():
        for i, level in enumerate(levels):
            path = f"{condition}/{repeat}/img_{i}.tif"
            first = plane(level, rng)
            if path in saturated:
                first[:4] = 255
            if path in blank:
                first[:] = 7
            for z in range(2):
                records.append(record(path, condition, repeat, [first, second], z))
    return records


def codes(issues):
    return [issue.code for issue in issues]


@pytest.fixture
def table():
    # ctrl/rep1 has an outlier image in C0; drug is brighter overall (a treatment
    # effect, never reported); ctrl/rep3 is shifted from the other repeats of ctrl.
    return rows(
        {
            ("ctrl", "rep1"): [100, 102, 98, 101, 99, 180],
            ("ctrl", "rep2"): [100, 103, 97, 101, 99],
            ("ctrl", "rep3"): [150, 152, 148, 151, 149],
            ("drug", "rep1"): [200, 202, 198, 201, 199],
            ("drug", "rep2"): [200, 203, 197, 201, 199],
        },
        saturated=("drug/rep1/img_0.tif",),
        blank=("drug/rep2/img_4.tif",),
    )


def test_the_checks_are_the_normalization_bricks(table):
    report = check_intensities(table)
    summaries = image_summaries(table)
    assert report.summaries == tuple(summaries)
    assert report.issues == tuple(intensity_checks(summaries))
    assert set(codes(report.issues)) == set(INTENSITY_CODES)
    assert report.channel_count == 2 and report.high_percentile == 99.8


def test_each_finding_names_the_channel(table):
    report = check_intensities(table)
    outliers = [i for i in report.issues if i.code == "image_intensity_outlier"]
    assert {i.items for i in outliers} >= {("ctrl/rep1/img_5.tif",)}
    assert {i.channel for i in outliers} == {0}
    assert all("channel C0" in i.message for i in outliers)
    shifts = [i for i in report.issues if i.code == "repeat_intensity_shift"]
    assert ("ctrl/rep3",) in {i.items for i in shifts}
    # Conditions are never compared with each other.
    assert not any(i.items[0].startswith("drug/") for i in shifts)


def test_settings_change_the_findings(table):
    loose = check_intensities(table, IntensityCheckSettings(outlier_limit=1000))
    assert not {"image_intensity_outlier", "repeat_intensity_shift"} & set(codes(loose.issues))
    strict = check_intensities(table, IntensityCheckSettings(saturation_limit=0.5))
    assert "saturated_image" not in codes(strict.issues)
    few = check_intensities(table, IntensityCheckSettings(min_images=7))
    assert "image_intensity_outlier" not in codes(few.issues)
    assert few.settings.min_images == 7


def test_flagged_images_and_shifted_repeats_per_channel(table):
    report = check_intensities(table)
    flagged = report.flagged_images(0)
    assert "image_intensity_outlier" in flagged["ctrl/rep1/img_5.tif"]
    assert "saturated_image" in flagged["drug/rep1/img_0.tif"]
    assert "no_intensity_spread" in flagged["drug/rep2/img_4.tif"]
    assert report.flagged_images(1) == {}
    assert ("ctrl", "rep3") in report.shifted_repeats(0)
    assert report.shifted_repeats(1) == set()


def test_validation_issues_are_warnings_with_paths(table):
    report = check_intensities(table)
    converted = report.validation_issues()
    assert len(converted) == len(report.issues)
    assert all(not issue.is_error for issue in converted)
    first = converted[0]
    assert first.code == report.issues[0].code and first.paths == report.issues[0].items
    assert first.action == report.issues[0].action


def test_repeat_boxes(table):
    report = check_intensities(table)
    boxes = repeat_boxes(report, 0)
    assert [b.label for b in boxes] == [
        "ctrl / rep1",
        "ctrl / rep2",
        "ctrl / rep3",
        "drug / rep1",
        "drug / rep2",
    ]
    rep1 = boxes[0]
    assert len(rep1.paths) == len(rep1.medians) == len(rep1.highs) == 6
    assert rep1.flagged[5] and not any(rep1.flagged[:5])
    assert [b.shifted for b in boxes] == [False, False, True, False, False]
    assert not any(any(b.flagged) for b in repeat_boxes(report, 1))


def test_repeat_distributions_pool_one_channel_per_repeat(table):
    curves = repeat_distributions(table, 0)
    assert list(curves) == ["ctrl", "drug"] and list(curves["ctrl"]) == ["rep1", "rep2", "rep3"]
    pooled = curves["ctrl"]["rep2"]
    # Five images, two planes each, 256 pixels per plane.
    assert pooled.pixel_count == 5 * 2 * 256
    share = cumulative_share(pooled)
    assert share[-1] == pytest.approx(1.0) and np.all(np.diff(share) >= 0)


def test_channels_without_finite_pixels_are_left_out():
    rng = np.random.default_rng(1)
    empty = np.full((8, 8), np.nan, np.float32)
    good = rng.normal(5, 1, (8, 8)).astype(np.float32)
    table = [
        record("a/r/x.tif", "a", "r", [empty, good]),
        record("a/r/y.tif", "a", "r", [good, good]),
    ]
    report = check_intensities(table)
    assert [b.paths for b in repeat_boxes(report, 0)] == [("a/r/y.tif",)]
    assert list(repeat_distributions(table, 0)["a"]) == ["r"]


def test_check_table_reads_the_csv(table, tmp_path):
    path = write_plane_histograms(table, tmp_path / "plane_histograms.csv")
    records, report = check_table(path)
    assert len(records) == len(table)
    assert report.issues == check_intensities(table).issues


def test_intensity_summary(table):
    assert intensity_summary(check_intensities(table)).startswith("26 images checked: ")
    quiet = check_intensities(rows({("a", "r"): [100, 100]}))
    assert intensity_summary(quiet) == "2 images checked: no intensity warnings."
