"""The histogram range and the narrow-range warning of step 3 (follow-up P6).

A float image with one hot pixel squeezes every other pixel into a few bins of
its minimum-to-maximum range; the normalization brick's range check finds it, and
the robust range (bins between the 0.01 % and 99.99 % quantiles) avoids it.
"""

import csv

import numpy as np
import pytest
from morphobricks_normalization.csv_io import read_plane_histograms
from morphobricks_normalization.ranges import HistogramSettings

from morphobricks_preprocessing import (
    plane_layout,
    prefill_mapping,
    read_manifest,
    run_experiment,
)
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.run import rows as rows_module
from tiff_tree import write_data

YX = prefill_mapping("YX")
ROBUST = HistogramSettings(histogram_range="robust")


def hot_pixel_image(seed=0, shape=(128, 128)):
    """A float32 plane of normal values with one hot pixel.

    128 x 128 pixels put more than one pixel above the 99.99 % quantile, so the
    robust range leaves the hot pixel out.
    """
    rng = np.random.default_rng(seed)
    plane = rng.normal(100.0, 10.0, shape).astype(np.float32)
    plane[3, 5] = 1e9
    return plane


@pytest.fixture
def hot_experiment(tmp_path):
    root = tmp_path / "hot"
    write_data(root / "ctrl/rep1/hot.tif", hot_pixel_image(0), axes="YX")
    write_data(
        root / "ctrl/rep1/clean.tif",
        np.random.default_rng(1).normal(100.0, 10.0, (128, 128)).astype(np.float32),
        axes="YX",
    )
    return root


@pytest.fixture
def reads(monkeypatch):
    paths = []
    original = rows_module.iter_xyc_planes

    def spy(path, *args, **kwargs):
        paths.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(rows_module, "iter_xyc_planes", spy)
    return paths


def narrow_warnings(result):
    return [issue for issue in result.issues if issue.code == "narrow_histogram_range"]


def test_a_hot_pixel_gives_the_narrow_range_warning_with_the_default_range(hot_experiment):
    result = run_experiment(hot_experiment, YX)

    assert result.written
    (found,) = narrow_warnings(result)
    assert found.paths == ("ctrl/rep1/hot.tif::z=0,t=0",)
    assert "C0" in found.message and "robust histogram range" in found.action
    manifest = read_manifest(result.manifest_path)
    assert manifest.settings.histogram == HistogramSettings()
    (entry,) = manifest.narrow_ranges
    assert (entry.relative_path, entry.channel, entry.check.histogram_range) == (
        "ctrl/rep1/hot.tif",
        0,
        "minmax",
    )
    assert entry.check.robust_central_bins >= entry.check.min_central_bins


def test_the_robust_range_avoids_the_warning(hot_experiment, tmp_path):
    result = run_experiment(
        hot_experiment, YX, output_folder=tmp_path / "out", histogram_settings=ROBUST
    )

    assert result.written and narrow_warnings(result) == []
    manifest = read_manifest(result.manifest_path)
    assert manifest.settings.histogram == ROBUST and manifest.narrow_ranges == ()
    hot = next(
        r
        for r in read_plane_histograms(result.csv_path)
        if r.source_relative_path.endswith("hot.tif")
    )
    assert hot.channel_histogram(0).overflow.count >= 1  # the hot pixel, counted outside the bins


def test_the_table_is_schema_version_4(hot_experiment):
    result = run_experiment(hot_experiment, YX)
    with result.csv_path.open(newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert "Histogram_OverflowCount_C0" in header
    assert read_manifest(result.manifest_path).schema_version == "4"


def test_the_robust_range_changes_no_8_or_16_bit_table(tmp_path):
    root = tmp_path / "ints"
    rng = np.random.default_rng(2)
    write_data(root / "a/r/u8.tif", rng.integers(0, 256, (2, 32, 32), dtype=np.uint8), axes="ZYX")
    zyx = prefill_mapping("ZYX")
    minmax = run_experiment(root, zyx, output_folder=tmp_path / "minmax")
    robust = run_experiment(root, zyx, output_folder=tmp_path / "robust", histogram_settings=ROBUST)
    assert minmax.csv_path.read_bytes() == robust.csv_path.read_bytes()

    root16 = tmp_path / "ints16"
    write_data(
        root16 / "a/r/u16.tif", rng.integers(0, 65536, (2, 32, 32), dtype=np.uint16), axes="ZYX"
    )
    minmax = run_experiment(root16, zyx, output_folder=tmp_path / "minmax16")
    robust = run_experiment(
        root16, zyx, output_folder=tmp_path / "robust16", histogram_settings=ROBUST
    )
    assert minmax.csv_path.read_bytes() == robust.csv_path.read_bytes()


@pytest.mark.parametrize("settings", [HistogramSettings(), ROBUST])
def test_planes_read_in_blocks_give_the_same_table(hot_experiment, tmp_path, settings):
    whole = run_experiment(
        hot_experiment, YX, output_folder=tmp_path / "whole", histogram_settings=settings
    )
    limit = plane_layout(
        hot_experiment / "ctrl/rep1/hot.tif", YX, memory_limit=1, histogram_settings=settings
    ).block_memory_estimate
    blocks = run_experiment(
        hot_experiment,
        YX,
        output_folder=tmp_path / "blocks",
        memory_limit=limit,
        histogram_settings=settings,
    )
    assert blocks.written and any(i.code == "planes_read_in_blocks" for i in blocks.issues)
    assert blocks.csv_path.read_bytes() == whole.csv_path.read_bytes()
    assert [i.paths for i in narrow_warnings(blocks)] == [i.paths for i in narrow_warnings(whole)]


def test_changing_the_range_recomputes_the_table(hot_experiment, reads):
    run_experiment(hot_experiment, YX)
    reads.clear()
    result = run_experiment(hot_experiment, YX, histogram_settings=ROBUST)
    assert result.rebuild_reason == (
        "the histogram range changed from minimum to maximum to robust (0.01-99.99 %)"
    )
    assert len(reads) == 2 and narrow_warnings(result) == []
    reads.clear()
    again = run_experiment(
        hot_experiment, YX, histogram_settings=HistogramSettings(min_central_bins=50)
    )
    assert (
        again.rebuild_reason
        == "the histogram range changed from robust (0.01-99.99 %) to minimum to maximum"
    )
    assert len(reads) == 2


def test_a_rerun_keeps_the_warning_without_reading(hot_experiment, reads):
    first = run_experiment(hot_experiment, YX)
    reads.clear()
    again = run_experiment(hot_experiment, YX)
    assert again.read == () and reads == []
    assert [i.message for i in narrow_warnings(again)] == [
        i.message for i in narrow_warnings(first)
    ]
    assert (
        read_manifest(again.manifest_path).narrow_ranges
        == read_manifest(first.manifest_path).narrow_ranges
    )


def test_a_table_of_schema_version_3_is_recomputed(hot_experiment, reads):
    import json

    first = run_experiment(hot_experiment, YX)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "3"
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    reads.clear()
    result = run_experiment(hot_experiment, YX)
    assert result.rebuild_reason == "the earlier table uses a different format version"
    assert len(reads) == 2


def test_the_command_line_takes_the_range(hot_experiment, tmp_path, capsys):
    from morphobricks_preprocessing import default_design, preflight, write_experiment_design

    output = tmp_path / "cli"
    design = default_design(preflight(hot_experiment).dataset.repeats)
    for group in design.groups:
        design = design.with_confirmed(group, True, "2026-10-28")
    output.mkdir()
    write_experiment_design(design, output / "experiment_design.csv")
    args = ["run", str(hot_experiment), "--axes", "YX", "--output", str(output)]
    assert main([*args, "--histogram-range", "robust", "--robust-quantiles", "0.1", "99.9"]) == 0
    settings = read_manifest(output / "run_manifest.json").settings.histogram
    assert (settings.histogram_range, settings.robust_quantiles) == ("robust", (0.1, 99.9))
    assert main([*args, "--histogram-range", "robust", "--robust-quantiles", "99", "1"]) == 1
    assert "0 <= low < high <= 100" in capsys.readouterr().out
