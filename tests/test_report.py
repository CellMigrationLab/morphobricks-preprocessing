from dataclasses import replace

import numpy as np
import pytest
from morphobricks_normalization.csv_io import read_plane_histograms
from morphobricks_normalization.histogram import compute_plane_histogram
from morphobricks_normalization.pooling import PooledHistogram
from morphobricks_normalization.records import PlaneRecord, group_records

from morphobricks_preprocessing import (
    discover_experiment,
    group_summaries,
    prefill_mapping,
    preflight,
    run_experiment,
)
from morphobricks_preprocessing.report import (
    ALL_CHANNELS,
    format_issues,
    format_table,
    group_table,
    issue_counts,
    label_tree,
    mapping_summary,
    preflight_summary,
    run_summary,
)
from morphobricks_preprocessing.validation import error, warning
from tiff_tree import make_tree, write_data, write_file, write_tiff


def stack(seed, shape=(2, 12, 12)):
    return np.random.default_rng(seed).integers(0, 200, shape, dtype=np.uint8)


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    for i, rel in enumerate(
        ["ctrl/rep1/t1/a.tif", "ctrl/rep1/t1/b.tif", "ctrl/rep2/a.tif", "drug/rep1/t1/a.tif"]
    ):
        write_data(root / rel, stack(i), axes="TYX")
    write_file(root / "notes.txt")
    write_file(root / "ctrl/readme.md")
    return root


def test_issue_counts():
    assert issue_counts([]) == "no problems found"
    issues = [warning("w", "a"), error("e", "b"), warning("w2", "c")]
    assert issue_counts(issues) == "1 error, 2 warnings"


def test_format_issues_puts_errors_first():
    text = format_issues([warning("w", "Odd.", ["a"]), error("e", "Bad.", ["b"], "Fix.")])
    assert text.index("Error: Bad.") < text.index("Warning: Odd.")
    assert "Suggested action: Fix." in text


def test_label_tree_shows_folders_counts_and_single_levels(experiment):
    tree = label_tree(discover_experiment(experiment))
    assert tree.splitlines() == [
        "exp/",
        "    ctrl/",
        "        rep1/",
        "            t1/  (2 images)",
        "        rep2/  (1 image; technical repeat: single)",
        "    drug/",
        "        rep1/",
        "            t1/  (1 image)",
    ]


def test_label_tree_for_images_in_the_root(tmp_path):
    make_tree(tmp_path / "exp", ["a.tif", "b.tif"])
    tree = label_tree(discover_experiment(tmp_path / "exp"))
    assert "(2 images directly in the experiment folder; all labels: single)" in tree


def test_mapping_summary_shows_lengths_and_roles(tmp_path):
    write_tiff(tmp_path / "a.tif", (3, 6, 7), axes="QYX")
    write_tiff(tmp_path / "b.tif", (5, 6, 7), axes="QYX")
    pre = preflight(tmp_path)
    text = mapping_summary(pre.suggested_mapping, pre.metadata.files)
    assert text.splitlines() == [
        "  Q (other), length 3–5 -> no role",
        "  Y (height), length 6 -> Y",
        "  X (width), length 7 -> X",
    ]


def test_preflight_summary(experiment):
    text = preflight_summary(preflight(experiment, exclude=["ctrl/rep2/a.tif"]))
    assert "Images to analyse: 3 in 3 leaf folders" in text
    assert "Excluded at your request: 1 image" in text
    assert "Skipped: 2 files that are not images" in text
    assert "Pixel type: uint8" in text
    assert "Axes in the files: TYX (3 images)" in text
    assert "T (time), length 2 -> T" in text
    assert "Checks: 2 warnings" in text


def test_preflight_summary_with_conflicting_axes(tmp_path):
    write_tiff(tmp_path / "a.tif", (3, 6, 7), axes="TYX")
    write_tiff(tmp_path / "b.tif", (3, 6, 7), axes="CYX")
    text = preflight_summary(preflight(tmp_path))
    assert "No axis mapping can be suggested" in text
    # The arrangement differs, and so does the number of channels (3 against 1).
    assert "2 errors" in text


def test_run_summaries(experiment):
    first = run_experiment(experiment, prefill_mapping("TYX"))
    text = run_summary(first)
    assert "Wrote 8 planes to" in text and "Read: 4 images" in text
    assert "because there is no earlier run" in text
    (experiment / "ctrl/rep2/a.tif").unlink()
    second = run_summary(run_experiment(experiment, prefill_mapping("TYX")))
    assert "Unchanged (rows kept): 3 images" in second
    assert "Removed from the table: 1 image" in second
    assert "Read:" not in second


def test_run_summary_when_blocked(experiment):
    result = run_experiment(experiment, prefill_mapping("TYX"), bin_count=0)
    assert run_summary(result).startswith("The run stopped (1 error")


def test_group_summaries_pool_the_planes(experiment):
    # One channel: one summary per group, the same numbers as before issue #104.
    result = run_experiment(experiment, prefill_mapping("TYX"))
    records = read_plane_histograms(result.csv_path)
    summaries = group_summaries(records, "biological_repeat", percentiles=(5, 95))
    assert [s.labels for s in summaries] == [("ctrl", "rep1"), ("ctrl", "rep2"), ("drug", "rep1")]
    for summary, members in zip(
        summaries, group_records(records, "biological_repeat").values(), strict=True
    ):
        pooled = PooledHistogram(members)
        assert summary.channel == 0
        assert summary.xyc_plane_count == summary.channel_histogram_count == len(members)
        assert summary.pixel_count == pooled.pixel_count
        assert summary.mean == pooled.mean and summary.median == pooled.median
        assert summary.percentiles == {5: pooled.percentile(5), 95: pooled.percentile(95)}
    # One channel pooled "across channels" is the same summary, labelled as an aggregate.
    pooled = group_summaries(records, "biological_repeat", (5, 95), pool_channels=True)
    assert [replace(s, channel=0) for s in pooled] == summaries


def test_group_table_and_text(experiment):
    result = run_experiment(experiment, prefill_mapping("TYX"))
    records = read_plane_histograms(result.csv_path)
    rows = group_table(group_summaries(records, "condition"), "condition")
    assert rows[0] == [
        "condition",
        "channel",
        "XYC planes",
        "channel histograms",
        "pixels",
        "min",
        "max",
        "mean",
        "std",
        "median",
        "p1",
        "p99.8",
    ]
    assert [row[0] for row in rows[1:]] == ["ctrl", "drug"]
    assert rows[1][1:5] == ["C0", "6", "6", "864"]
    text = format_table(rows)
    assert text.splitlines()[0].startswith("condition  channel  XYC planes")


def channel_records(levels, condition="c", repeat="r", planes=2, size=8, seed=0):
    """Records of ``planes`` XYC planes whose channel ``c`` holds values around ``levels[c]``."""
    rng = np.random.default_rng(seed)
    records = []
    for i in range(planes):
        histograms = tuple(
            compute_plane_histogram(
                (level + rng.integers(0, 3, (size, size))).astype(np.uint16), 256
            )
            for level in levels
        )
        records.append(
            PlaneRecord(f"{condition}/{repeat}/x.tif", condition, repeat, "single", histograms, t=i)
        )
    return records


@pytest.mark.parametrize(
    "levels", [(10, 1000), (10, 1000, 30000), (5, 60000, 200, 4000)], ids=["2", "3", "4"]
)
def test_group_summaries_keep_fluorescence_channels_apart(levels):
    # Issue #104: strongly separated channels are summarised one by one, never mixed.
    records = channel_records(levels) + channel_records(levels, condition="d", seed=1)
    summaries = group_summaries(records, "condition", percentiles=(50,))
    assert [(s.labels, s.channel) for s in summaries] == [
        ((condition,), c) for condition in ("c", "d") for c in range(len(levels))
    ]
    for s in summaries:
        level = levels[s.channel]
        assert level <= s.minimum and s.maximum <= level + 2
        assert level <= s.mean <= level + 2
        # Two XYC planes, one histogram of this channel each: not planes x channels.
        assert s.xyc_plane_count == s.channel_histogram_count == 2
        assert s.pixel_count == 2 * 64
    rows = group_table(summaries, "condition")
    assert [row[1] for row in rows[1:]] == [f"C{c}" for c in range(len(levels))] * 2
    assert {row[2] for row in rows[1:]} == {"2"}


def test_an_all_channel_aggregate_is_explicit_and_labelled():
    records = channel_records((10, 1000))
    (summary,) = group_summaries(records, "condition", pool_channels=True)
    assert summary.channel is ALL_CHANNELS
    assert (summary.xyc_plane_count, summary.channel_histogram_count) == (2, 4)
    assert 10 < summary.mean < 1000  # the mixed value the default never reports
    row = group_table([summary], "condition")[1]
    assert row[1:4] == ["all channels (pooled)", "2", "4"]


def test_a_channel_without_finite_pixels_is_reported_alone():
    finite = compute_plane_histogram(np.full((8, 8), 5.0, np.float32), 16)
    empty = compute_plane_histogram(np.full((8, 8), np.nan, np.float32), 16)
    records = [PlaneRecord("c/r/x.tif", "c", "r", "single", (finite, empty), t=t) for t in range(2)]
    first, second = group_summaries(records, "condition")
    assert first.channel == 0 and first.mean == 5.0 and first.pixel_count == 128
    assert second.channel == 1 and second.pixel_count == 0 and second.mean is None
    assert second.xyc_plane_count == 2
    assert group_table([first, second], "condition")[2][1:] == ["C1", "2", "2", "0"] + ["–"] * 7


def test_rgb_channels_are_summarised_one_by_one(tmp_path):
    root = tmp_path / "exp"
    rgb = np.stack([np.full((8, 8), v, np.uint8) for v in (20, 120, 240)])
    write_data(root / "a/x.tif", rgb, axes="CYX")
    result = run_experiment(root, prefill_mapping("CYX"), image_type="rgb")
    assert result.written
    summaries = group_summaries(read_plane_histograms(result.csv_path), "condition")
    assert [(s.channel, s.mean, s.xyc_plane_count) for s in summaries] == [
        (0, 20.0, 1),
        (1, 120.0, 1),
        (2, 240.0, 1),
    ]


def test_a_group_with_different_channel_counts_is_refused():
    records = channel_records((10, 1000)) + channel_records((10,), seed=1)
    with pytest.raises(ValueError, match="different numbers of channels"):
        group_summaries(records, "condition")


def test_plane_count_is_a_deprecated_alias_of_the_histogram_count():
    (summary,) = group_summaries(channel_records((10, 1000)), "condition", pool_channels=True)
    with pytest.warns(DeprecationWarning, match="xyc_plane_count"):
        assert summary.plane_count == 4


def test_group_without_finite_pixels(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a/x.tif", np.ones((2, 4, 4), np.float32), axes="TYX")
    write_data(root / "b/x.tif", np.full((2, 4, 4), np.nan, np.float32), axes="TYX")
    result = run_experiment(root, prefill_mapping("TYX"))
    summaries = group_summaries(read_plane_histograms(result.csv_path), "condition")
    empty = summaries[1]
    assert empty.labels == ("b",) and empty.pixel_count == 0 and empty.mean is None
    assert group_table(summaries, "condition")[2][5:] == ["–"] * 7
