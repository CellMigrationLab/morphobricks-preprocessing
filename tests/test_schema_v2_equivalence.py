"""The table holds exactly what version 2 held, channel by channel (follow-ups P3b, P6).

The fixture in ``tests/data`` was written by the code before P3b (commit
``55f1074`` with morphobricks-normalization v0.1.0, ``make_v2_fixture.py``):
simulated pixels and the version-2 ``plane_histograms.csv`` its ``run_experiment``
wrote for them, one row per plane and channel. Here the same images go through
the current code, read whole and in blocks; every value of every channel must be
the same text as in the version-2 row of that channel. Version 4 (follow-up P6) adds
underflow and overflow columns, which the default minimum-to-maximum range leaves
at zero.
"""

import csv
import json
import shutil

import numpy as np
import pytest
from morphobricks_normalization.csv_io import CsvFormatError, read_plane_histograms
from morphobricks_normalization.histogram import PlaneHistogram
from morphobricks_normalization.records import PlaneRecord

from morphobricks_preprocessing import (
    plane_layout,
    prefill_mapping,
    preflight,
    read_manifest,
    review_axes,
    run_experiment,
)
from morphobricks_preprocessing.manifest import file_sha256
from morphobricks_preprocessing.report import group_summaries, group_table
from v2_fixture import DATA, EXPERIMENTS, PIXELS, write_experiment

# Version-2 column -> version-3 column of channel c.
CHANNEL_COLUMNS = {
    "pixel_min": "Intensity_MinIntensity_C{c}",
    "pixel_max": "Intensity_MaxIntensity_C{c}",
    "valid_pixel_count": "Histogram_ValidPixelCount_C{c}",
    "binning_min": "Histogram_BinningMin_C{c}",
    "binning_max": "Histogram_BinningMax_C{c}",
}
# Columns both versions share, with the same meaning.
SHARED_COLUMNS = (
    "position_id",
    "image_file",
    "z",
    "t",
    "condition",
    "biological_repeat",
    "technical_repeat",
    "source_relative_path",
    "dtype",
    "bit_depth",
    "total_pixel_count",
    "bin_count",
)


@pytest.fixture(scope="module")
def pixels():
    with np.load(PIXELS) as data:
        return {key: data[key] for key in data.files}


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def needs_zarr(dtype):
    if any(rel.endswith(".ome.zarr") for rel, _, _ in EXPERIMENTS[dtype][2]):
        pytest.importorskip("zarr")


def smallest_blocks_limit(root, mapping, bins):
    """A memory limit at which every image of the experiment is read in blocks."""
    review = review_axes(preflight(root).metadata, mapping)
    return max(
        plane_layout(root / rel, m, memory_limit=1, bin_count=bins).block_memory_estimate
        for rel, m in review.file_mappings.items()
    )


@pytest.mark.parametrize("blocks", [False, True], ids=["whole", "in-blocks"])
@pytest.mark.parametrize("dtype", list(EXPERIMENTS))
def test_histograms_equal_those_of_version_2(tmp_path, pixels, dtype, blocks):
    needs_zarr(dtype)
    root = tmp_path / "exp"
    bins, axes = write_experiment(root, dtype, pixels)
    mapping = prefill_mapping(axes)
    limit = smallest_blocks_limit(root, mapping, bins) if blocks else 2**30
    result = run_experiment(root, mapping, bin_count=bins, memory_limit=limit)
    assert result.written
    in_blocks = [i for i in result.issues if i.code == "planes_read_in_blocks"]
    assert bool(in_blocks) == blocks
    if blocks:
        assert len(in_blocks[0].paths) == len(EXPERIMENTS[dtype][2])

    old = {}
    for row in read_rows(DATA / f"v2_{dtype}.csv"):
        assert row["schema_version"] == "2"
        old[(f"{row['source_relative_path']}::z={row['z']},t={row['t']}", int(row["c"]))] = row
    new = read_rows(result.csv_path)
    matched = 0
    for row in new:
        assert row["schema_version"] == "4"
        for c in range(int(row["channel_count"])):
            for tail in ("Underflow", "Overflow"):
                assert float(row[f"Histogram_{tail}Count_C{c}"]) == 0
            before = old[(row["plane_id"], c)]
            for column in SHARED_COLUMNS:
                assert row[column] == before[column], (row["plane_id"], column)
            for v2_column, v3_column in CHANNEL_COLUMNS.items():
                assert row[v3_column.format(c=c)] == before[v2_column], (row["plane_id"], c)
            counts = [row[f"Histogram_Count_C{c}_{i:05d}"] for i in range(bins)]
            assert counts == [before[f"histogram_bin_{i:05d}"] for i in range(bins)]
            matched += 1
    # Every version-2 row is a channel of exactly one version-3 row.
    assert matched == len(old)
    assert len(new) == len(old) // int(new[0]["channel_count"])


def v2_records(dtype):
    """The version-2 rows as one-channel records, as the code before P3b read them."""
    records = []
    for row in read_rows(DATA / f"v2_{dtype}.csv"):
        number = int if np.dtype(row["dtype"]).kind in "iu" else float
        bins = int(row["bin_count"])

        def value(text, number=number):
            return None if text == "" else number(text)

        histogram = PlaneHistogram(
            dtype=np.dtype(row["dtype"]),
            bin_count=bins,
            binning_min=value(row["binning_min"]),
            binning_max=value(row["binning_max"]),
            counts=np.array([int(row[f"histogram_bin_{i:05d}"]) for i in range(bins)]),
            total_pixel_count=int(row["total_pixel_count"]),
            valid_pixel_count=int(row["valid_pixel_count"]),
        )
        labels = (row["condition"], row["biological_repeat"], row["technical_repeat"])
        records.append(PlaneRecord(row["source_relative_path"], *labels, histogram))
    return records


@pytest.mark.parametrize("level", ["condition", "biological_repeat", "image"])
@pytest.mark.parametrize("dtype", list(EXPERIMENTS))
def test_step_4_statistics_are_those_of_version_2(tmp_path, pixels, dtype, level):
    needs_zarr(dtype)
    root = tmp_path / "exp"
    bins, axes = write_experiment(root, dtype, pixels)
    result = run_experiment(root, prefill_mapping(axes), bin_count=bins)
    now = group_summaries(read_plane_histograms(result.csv_path), level, (1.0, 99.8))
    before = group_summaries(v2_records(dtype), level, (1.0, 99.8))
    assert now == before
    assert group_table(now, level) == group_table(before, level)


def test_a_version_2_table_is_recomputed_on_the_next_run(tmp_path, pixels):
    root = tmp_path / "exp"
    bins, axes = write_experiment(root, "float32", pixels)
    mapping = prefill_mapping(axes)
    first = run_experiment(root, mapping, bin_count=bins)
    table = first.csv_path.read_bytes()
    # Leave the output as a run of version 2 did: its table, and a manifest naming it.
    shutil.copyfile(DATA / "v2_float32.csv", first.csv_path)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "2"
    manifest["csv_sha256"] = file_sha256(first.csv_path)
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CsvFormatError, match="schema version 2"):
        read_plane_histograms(first.csv_path)

    second = run_experiment(root, mapping, bin_count=bins)
    assert second.written
    assert second.rebuild_reason == "the earlier table uses a different format version"
    assert second.read == tuple(sorted(rel for rel, _, _ in EXPERIMENTS["float32"][2]))
    assert second.csv_path.read_bytes() == table
    data = json.loads(second.manifest_path.read_text(encoding="utf-8"))
    assert (data["manifest_version"], data["schema_version"]) == ("1", "4")
    assert read_manifest(second.manifest_path).csv_sha256 == file_sha256(second.csv_path)


def test_a_version_2_table_under_a_current_manifest_is_recomputed(tmp_path, pixels):
    # A table replaced by hand with an earlier one cannot be read, so it is recomputed.
    root = tmp_path / "exp"
    bins, axes = write_experiment(root, "float32", pixels)
    mapping = prefill_mapping(axes)
    first = run_experiment(root, mapping, bin_count=bins)
    table = first.csv_path.read_bytes()
    shutil.copyfile(DATA / "v2_float32.csv", first.csv_path)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    manifest["csv_sha256"] = file_sha256(first.csv_path)
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    second = run_experiment(root, mapping, bin_count=bins)
    assert second.rebuild_reason == "the earlier histogram table could not be read"
    assert second.csv_path.read_bytes() == table
