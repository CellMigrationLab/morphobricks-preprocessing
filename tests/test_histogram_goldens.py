"""Science goldens: every histogram value equals an independently computed reference.

The reference in ``tests/data`` was computed by an earlier, independent implementation (commit
``55f1074`` with morphobricks-normalization v0.1.0, ``make_histogram_goldens.py``): simulated
pixels and the ``plane_histograms.csv`` it wrote for them, one row per plane and channel. Here
the same images go through the current code, read whole and in blocks; every value of every
channel must be the same text as in the reference row of that channel (owner ruling 6 of
10 November 2026: kept as science goldens, not as a version-parity check). The current table
also has underflow and overflow columns (follow-up P6), which the default minimum-to-maximum
range leaves at zero. The reference's uint16 Z-stacks are written as time-lapses of the same pixels
since Z-stacks are refused (owner decision of 5 November 2026): its ``z`` is read as ``t``.
"""

import csv
import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from morphobricks_normalization.csv_io import read_plane_histograms
from morphobricks_normalization.histogram import PlaneHistogram
from morphobricks_normalization.records import PlaneRecord

from described_tree import run_described
from histogram_goldens import DATA, EXPERIMENTS, PIXELS, TIME_FOR_DEPTH, write_experiment
from morphobricks_preprocessing import (
    plane_layout,
    prefill_mapping,
    preflight,
    review_axes,
)
from morphobricks_preprocessing.manifest import file_sha256
from morphobricks_preprocessing.report import group_summaries, group_table

# Reference column -> current column of the channel with ID c (schema version 5).
CHANNEL_COLUMNS = {
    "pixel_min": "Intensity_MinIntensity_{c}",
    "pixel_max": "Intensity_MaxIntensity_{c}",
    "valid_pixel_count": "Histogram_ValidPixelCount_{c}",
    "binning_min": "Histogram_BinningMin_{c}",
    "binning_max": "Histogram_BinningMax_{c}",
}
# Columns the reference and the current table share, with the same meaning.
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
def test_histograms_equal_the_reference_values(tmp_path, pixels, dtype, blocks):
    needs_zarr(dtype)
    root = tmp_path / "exp"
    bins, axes = write_experiment(root, dtype, pixels)
    mapping = prefill_mapping(axes)
    limit = smallest_blocks_limit(root, mapping, bins) if blocks else 2**30
    result = run_described(root, mapping, bin_count=bins, memory_limit=limit)
    assert result.written
    in_blocks = [i for i in result.issues if i.code == "planes_read_in_blocks"]
    assert bool(in_blocks) == blocks
    if blocks:
        assert len(in_blocks[0].paths) == len(EXPERIMENTS[dtype][2])

    old = {}
    as_time = EXPERIMENTS[dtype][1] in TIME_FOR_DEPTH  # the reference's z is now t
    for row in read_rows(DATA / f"golden_{dtype}.csv"):
        assert row["schema_version"] == "2"  # the reference table's own format
        z, t = ("0", row["z"]) if as_time else (row["z"], row["t"])
        assert not as_time or row["t"] == "0"
        plane_id = f"{row['source_relative_path']}::z={z},t={t}"
        old[(plane_id, int(row["c"]))] = {**row, "z": z, "t": t, "plane_id": plane_id}
    new = read_rows(result.csv_path)
    matched = 0
    for row in new:
        assert row["schema_version"] == "5"
        ids = row["channels"].split(";")  # the plane's channel IDs, in source order
        for c in range(int(row["channel_count"])):
            channel = ids[c]
            for tail in ("Underflow", "Overflow"):
                assert float(row[f"Histogram_{tail}Count_{channel}"]) == 0
            before = old[(row["plane_id"], c)]
            for column in SHARED_COLUMNS:
                assert row[column] == before[column], (row["plane_id"], column)
            for old_column, column in CHANNEL_COLUMNS.items():
                assert row[column.format(c=channel)] == before[old_column], (row["plane_id"], c)
            counts = [row[f"Histogram_Count_{channel}_{i:05d}"] for i in range(bins)]
            assert counts == [before[f"histogram_bin_{i:05d}"] for i in range(bins)]
            matched += 1
    # Every reference row is a channel of exactly one current row.
    assert matched == len(old)
    assert len(new) == len(old) // int(new[0]["channel_count"])


def reference_records(dtype, ids):
    """The reference rows as one-channel records, named by the run's IDs of each image's ``c``.

    ``ids`` maps each image to its channel IDs in source order (the described run's).
    """
    records = []
    for row in read_rows(DATA / f"golden_{dtype}.csv"):
        number = int if np.dtype(row["dtype"]).kind in "iu" else float
        bins = int(row["bin_count"])

        def value(text, number=number):
            return None if text == "" else number(text)

        histogram = PlaneHistogram(
            dtype=np.dtype(row["dtype"]),
            bin_count=bins,
            binning_min=value(row["binning_min"]),
            binning_max=value(row["binning_max"]),
            # Normalization's next release refuses a histogram with valid pixels and no
            # extrema (normalization#65, issue #47): passed explicitly from the reference columns.
            pixel_min=value(row["pixel_min"]),
            pixel_max=value(row["pixel_max"]),
            counts=np.array([int(row[f"histogram_bin_{i:05d}"]) for i in range(bins)]),
            total_pixel_count=int(row["total_pixel_count"]),
            valid_pixel_count=int(row["valid_pixel_count"]),
        )
        labels = (row["condition"], row["biological_repeat"], row["technical_repeat"])
        path = row["source_relative_path"]
        channel = ids[path][int(row["c"])]
        records.append(
            PlaneRecord(path, *labels, histogram, position_id=Path(path).stem, channels=(channel,))
        )
    return records


@pytest.mark.parametrize("level", ["condition", "biological_repeat", "image"])
@pytest.mark.parametrize("dtype", list(EXPERIMENTS))
def test_step_4_statistics_equal_the_reference_values(tmp_path, pixels, dtype, level):
    needs_zarr(dtype)
    root = tmp_path / "exp"
    bins, axes = write_experiment(root, dtype, pixels)
    result = run_described(root, prefill_mapping(axes), bin_count=bins)
    records = read_plane_histograms(result.csv_path)
    ids = {record.source_relative_path: record.channels for record in records}
    # Issue #104: each channel on its own, from the reference rows of that channel.
    now = group_summaries(records, level, (1.0, 99.8))
    before = group_summaries(reference_planes(dtype, ids), level, (1.0, 99.8))
    assert now == before
    assert group_table(now, level) == group_table(before, level)
    # The all-channel aggregate, asked for, equals the reference's pooled numbers.
    pooled = group_summaries(records, level, (1.0, 99.8), pool_channels=True)
    legacy = group_summaries(reference_records(dtype, ids), level, (1.0, 99.8), pool_channels=True)
    statistics = ("pixel_count", "minimum", "maximum", "mean", "std", "median", "percentiles")
    for p, old in zip(pooled, legacy, strict=True):
        assert p.labels == old.labels
        assert p.channel_histogram_count == old.xyc_plane_count  # a reference row is one channel
        assert [getattr(p, f) for f in statistics] == [getattr(old, f) for f in statistics]


def reference_planes(dtype, ids):
    """The reference rows gathered into XYC planes, channels in order (``c``)."""
    planes = {}
    for record, row in zip(
        reference_records(dtype, ids), read_rows(DATA / f"golden_{dtype}.csv"), strict=True
    ):
        key = (record.source_relative_path, row["z"], row["t"])
        planes.setdefault(key, {})[int(row["c"])] = record
    return [
        replace(
            first,
            histograms=tuple(channels[c].histograms[0] for c in sorted(channels)),
            channels=tuple(channels[c].channels[0] for c in sorted(channels)),
        )
        for channels in planes.values()
        for first in [channels[min(channels)]]
    ]


def test_a_table_of_an_older_format_is_recomputed(tmp_path, pixels):
    # A table replaced by hand with an earlier one cannot be read, so it is recomputed.
    root = tmp_path / "exp"
    bins, axes = write_experiment(root, "float32", pixels)
    mapping = prefill_mapping(axes)
    first = run_described(root, mapping, bin_count=bins)
    table = first.csv_path.read_bytes()
    shutil.copyfile(DATA / "golden_float32.csv", first.csv_path)
    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    manifest["csv_sha256"] = file_sha256(first.csv_path)
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    second = run_described(root, mapping, bin_count=bins)
    assert second.rebuild_reason == "the earlier histogram table could not be read"
    assert second.csv_path.read_bytes() == table
