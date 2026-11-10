"""An image channel without finite pixels is named, not left silent (normalization #57).

Its summaries are blank, so the four intensity checks cannot see it; the normalization
brick's ``unchecked_channels`` names it as ``channel_without_finite_pixels``, which this
brick reports with the other intensity warnings: in ``check_intensities`` and
``check_table``, the report of ``run``, the command line and the notebook.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from morphobricks_normalization.checks import intensity_checks, unchecked_channels
from morphobricks_normalization.csv_io import write_plane_histograms
from morphobricks_normalization.histogram import compute_plane_histogram
from morphobricks_normalization.records import PlaneRecord
from morphobricks_normalization.summaries import image_summaries

from described_tree import describe_tree, main_described, run_described
from morphobricks_preprocessing import default_output_folder, prefill_mapping
from morphobricks_preprocessing.intensity import (
    CHECK_TITLES,
    IMAGE_CODES,
    INTENSITY_CODES,
    check_intensities,
    check_table,
    intensity_summary,
    intensity_text,
)
from tiff_tree import write_data

CODE = "channel_without_finite_pixels"


def nan_tree(root):
    """Three 2-channel float images in ctrl/rep1; channel C1 of img_1 is all NaN."""
    rng = np.random.default_rng(7)
    for i in range(3):
        first = rng.normal(5, 1, (16, 16)).astype(np.float32)
        second = rng.normal(9, 1, (16, 16)).astype(np.float32)
        if i == 1:
            second[:] = np.nan
        write_data(root / f"ctrl/rep1/img_{i}.tif", np.stack([first, second]), axes="CYX")
    return root


def table_rows():
    rng = np.random.default_rng(1)
    empty = np.full((8, 8), np.nan, np.float32)
    good = rng.normal(5, 1, (8, 8)).astype(np.float32)
    channels = {"a/r/x.tif": [good, empty], "a/r/y.tif": [good, good], "a/r/z.tif": [empty, good]}
    return [
        PlaneRecord(
            path,
            "a",
            "r",
            "single",
            [compute_plane_histogram(c) for c in planes],
            position_id=Path(path).stem,
        )
        for path, planes in channels.items()
    ]


def test_the_code_is_an_intensity_code_with_a_title():
    assert CODE in INTENSITY_CODES and CODE in CHECK_TITLES
    # The channel has no value to plot, so no image is marked for it.
    assert CODE not in IMAGE_CODES


def test_check_intensities_adds_the_unchecked_channels_after_the_checks():
    records = table_rows()
    report = check_intensities(records)
    summaries = image_summaries(records)
    expected = (*intensity_checks(summaries), *unchecked_channels(summaries))
    assert report.issues == expected
    unchecked = [i for i in report.issues if i.code == CODE]
    assert [(i.items, i.channel) for i in unchecked] == [(("a/r/x.tif",), 1), (("a/r/z.tif",), 0)]
    assert all(not issue.is_error for issue in report.validation_issues())
    assert intensity_summary(report) == "3 images checked: 2 intensity warnings."
    text = intensity_text(report)
    assert f"{CHECK_TITLES[CODE]} ({CODE}, 2):" in text
    assert "  - Image a/r/x.tif, channel C1: it has no finite pixels" in text
    assert text.count("What to do: Check the image") == 1


def test_check_table_reports_it_from_the_csv(tmp_path):
    path = write_plane_histograms(table_rows(), tmp_path / "plane_histograms.csv")
    _, report = check_table(path)
    assert [i.code for i in report.issues].count(CODE) == 2


def test_a_run_reports_it_with_the_intensity_warnings(tmp_path):
    root = nan_tree(tmp_path / "exp")
    result = run_described(root, prefill_mapping("CYX"))
    assert result.written
    (found,) = [i for i in result.intensity.issues if i.code == CODE]
    assert found.items == ("ctrl/rep1/img_1.tif",) and found.channel == 1
    # The run's own issues keep their warning about the empty plane.
    assert CODE not in [i.code for i in result.issues]
    report = json.loads((result.output_folder / "discovery_report.json").read_text("utf-8"))
    assert CODE in json.dumps(report)


def test_the_command_line_run_prints_it(tmp_path, capsys):
    root = nan_tree(tmp_path / "exp")
    output = default_output_folder(root)
    assert main_described(["check", str(root)]) == 0
    assert main_described(["run", str(root), "--axes", "CYX"]) == 0
    out = capsys.readouterr().out.split("Intensity checks:")[1]
    assert f"Image channels without finite pixels, not checked ({CODE}, 1):" in out
    assert "  - Image ctrl/rep1/img_1.tif, channel C1: it has no finite pixels" in out
    assert (output / "plane_histograms.csv").is_file()


def test_the_notebook_shows_it_with_the_other_intensity_warnings(tmp_path):
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    root = nan_tree(tmp_path / "exp")
    describe_tree(root)  # the experiment described and saved, as Check and save does
    nb = PreprocessingNotebook(str(root))
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        step()
    nb.check_button.click()
    for button in (nb.preview_button, nb.preview_checked.button, nb.confirm_button):
        button.click()
    nb.run()
    panel = nb.run_checks
    assert "1 intensity warning" in panel.status.value
    assert "Not checked" in panel.findings.value and "ctrl/rep1/img_1.tif" in panel.findings.value
    assert "channels not checked" in panel.tiles.value
    assert CHECK_TITLES[CODE] in panel.findings.value
