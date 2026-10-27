import numpy as np
import pytest

from morphobricks_preprocessing import (
    PooledHistogram,
    discover_experiment,
    group_records,
    group_summaries,
    prefill_mapping,
    preflight,
    read_plane_histograms,
    run_experiment,
)
from morphobricks_preprocessing.report import (
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
        write_data(root / rel, stack(i), axes="ZYX")
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
    assert "Axes in the files: ZYX (3 images)" in text
    assert "Z (depth), length 2 -> Z" in text
    assert "Checks: 2 warnings" in text


def test_preflight_summary_with_conflicting_axes(tmp_path):
    write_tiff(tmp_path / "a.tif", (3, 6, 7), axes="ZYX")
    write_tiff(tmp_path / "b.tif", (3, 6, 7), axes="CYX")
    text = preflight_summary(preflight(tmp_path))
    assert "No axis mapping can be suggested" in text
    # The arrangement differs, and so does the number of channels (3 against 1).
    assert "2 errors" in text


def test_run_summaries(experiment):
    first = run_experiment(experiment, prefill_mapping("ZYX"))
    text = run_summary(first)
    assert "Wrote 8 planes to" in text and "Read: 4 images" in text
    assert "because there is no earlier run" in text
    (experiment / "ctrl/rep2/a.tif").unlink()
    second = run_summary(run_experiment(experiment, prefill_mapping("ZYX")))
    assert "Unchanged (rows kept): 3 images" in second
    assert "Removed from the table: 1 image" in second
    assert "Read:" not in second


def test_run_summary_when_blocked(experiment):
    result = run_experiment(experiment, prefill_mapping("ZYX"), bin_count=0)
    assert run_summary(result).startswith("Nothing was written (1 error")


def test_group_summaries_pool_the_planes(experiment):
    result = run_experiment(experiment, prefill_mapping("ZYX"))
    records = read_plane_histograms(result.csv_path)
    summaries = group_summaries(records, "biological_repeat", percentiles=(5, 95))
    assert [s.labels for s in summaries] == [("ctrl", "rep1"), ("ctrl", "rep2"), ("drug", "rep1")]
    for summary, members in zip(
        summaries, group_records(records, "biological_repeat").values(), strict=True
    ):
        pooled = PooledHistogram(members)
        assert summary.plane_count == len(members)
        assert summary.pixel_count == pooled.pixel_count
        assert summary.mean == pooled.mean and summary.median == pooled.median
        assert summary.percentiles == {5: pooled.percentile(5), 95: pooled.percentile(95)}


def test_group_table_and_text(experiment):
    result = run_experiment(experiment, prefill_mapping("ZYX"))
    records = read_plane_histograms(result.csv_path)
    rows = group_table(group_summaries(records, "condition"), "condition")
    assert rows[0] == ["condition", "planes", "pixels", "min", "max", "mean", "std", "median"] + [
        "p1",
        "p99.8",
    ]
    assert [row[0] for row in rows[1:]] == ["ctrl", "drug"]
    assert rows[1][1:3] == ["6", "864"]
    text = format_table(rows)
    assert text.splitlines()[0].startswith("condition  planes")


def test_group_without_finite_pixels(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a/x.tif", np.ones((2, 4, 4), np.float32), axes="ZYX")
    write_data(root / "b/x.tif", np.full((2, 4, 4), np.nan, np.float32), axes="ZYX")
    result = run_experiment(root, prefill_mapping("ZYX"))
    summaries = group_summaries(read_plane_histograms(result.csv_path), "condition")
    empty = summaries[1]
    assert empty.labels == ("b",) and empty.pixel_count == 0 and empty.mean is None
    assert group_table(summaries, "condition")[2][3:] == ["–"] * 7
