"""The run's image QC table ``image_qc.csv`` and its manifest entry (contract §19).

The table is written with the histogram table, one row per plane and channel, named in the
manifest by its SHA-256, and follows the histogram step's reuse and resume rules. The table
format and the manifest entry are read strictly.
"""

import csv
import json

import pytest
import tifffile
from morphobricks_normalization.csv_io import read_plane_histograms
from morphobricks_normalization.histogram import compute_plane_histogram

from described_tree import run_described
from morphobricks_preprocessing import read_manifest
from morphobricks_preprocessing import run as run_module
from morphobricks_preprocessing.image_qc import METRICS, QC_VERSION, ImageQc, PlaneQc
from morphobricks_preprocessing.image_qc_record import (
    IMAGE_QC_FILENAME,
    image_qc_entry,
    recorded_image_qc,
)
from morphobricks_preprocessing.image_qc_table import (
    COLUMNS,
    ImageQcFormatError,
    QcRow,
    read_image_qc,
    table_text,
    write_image_qc,
)
from morphobricks_preprocessing.log_target import PROTECTED_OUTPUTS
from morphobricks_preprocessing.planes import plane_layout
from morphobricks_preprocessing.run import RunMode
from morphobricks_preprocessing.run.staging import STAGING_FOLDER
from qc_tree import (
    CTYX,
    IMAGES,
    interrupt_at_second_file,
    make_experiment,
    run,
    sha256,
    stack,
)
from tiff_tree import write_data


@pytest.fixture
def experiment(tmp_path):
    return make_experiment(tmp_path)


def test_the_table_and_its_manifest_entry_match_the_histogram_table(tmp_path, experiment):
    out = tmp_path / "out"
    run(experiment, out)
    rows = read_image_qc(out / IMAGE_QC_FILENAME)
    manifest = read_manifest(out / "run_manifest.json")
    assert manifest.image_qc == image_qc_entry(sha256(out / IMAGE_QC_FILENAME))
    raw = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert raw["image_qc"]["settings"]["correlation_lags"] == [1, 4, 16]
    records = read_plane_histograms(out / "plane_histograms.csv")
    expected = [
        QcRow.of(r, channel, False, None).identity for r in records for channel in r.channels
    ]
    assert sorted(row.identity for row in rows) == sorted(expected)
    assert len(rows) == len(IMAGES) * 2 * 2 and not any(row.read_in_blocks for row in rows)
    assert (out / IMAGE_QC_FILENAME).read_text(encoding="utf-8").splitlines()[0] == ",".join(
        COLUMNS
    )


def test_each_row_holds_the_metrics_of_its_channel_plane(tmp_path, experiment):
    out = tmp_path / "out"
    run(experiment, out)
    data = tifffile.imread(experiment / "drug/r1/a.tif")
    plane_id = "drug/r1/a.tif::z=0,t=1"
    found = {row.key: row for row in read_image_qc(out / IMAGE_QC_FILENAME)}
    for c, channel in enumerate(sorted({key[1] for key in found})):
        pixels = data[1, c]
        qc = PlaneQc(pixels.shape, pixels.dtype)
        qc.add(0, 0, pixels)
        assert found[plane_id, channel].qc == qc.finish(compute_plane_histogram(pixels))


def test_planes_read_in_blocks_are_marked_and_keep_their_histograms(tmp_path, experiment):
    whole, blocks = tmp_path / "whole", tmp_path / "blocks"
    run(experiment, whole)
    limit = plane_layout(experiment / IMAGES[0], CTYX, memory_limit=1).block_memory_estimate
    run(experiment, blocks, memory_limit=limit)
    assert (whole / "plane_histograms.csv").read_bytes() == (
        blocks / "plane_histograms.csv"
    ).read_bytes()
    found = {row.key: row for row in read_image_qc(blocks / IMAGE_QC_FILENAME)}
    for row in read_image_qc(whole / IMAGE_QC_FILENAME):
        other = found[row.key]
        assert other.read_in_blocks and not row.read_in_blocks
        assert other.qc.normalized_variance == pytest.approx(row.qc.normalized_variance, rel=1e-9)
        assert other.qc.entropy == row.qc.entropy


def test_a_rerun_reuses_the_rows_and_leaves_the_outputs_as_they_were(
    tmp_path, experiment, monkeypatch
):
    out = tmp_path / "out"
    run(experiment, out)
    before = {name: (out / name).read_bytes() for name in (IMAGE_QC_FILENAME, "run_manifest.json")}
    reads = []
    read = run_module.resume._read_file
    monkeypatch.setattr(run_module.resume, "_read_file", lambda *a: reads.append(a[1]) or read(*a))
    result = run(experiment, out)
    assert reads == [] and result.rebuild_reason is None
    assert {name: (out / name).read_bytes() for name in before} == before


def test_a_changed_image_replaces_only_its_rows(tmp_path, experiment):
    out, reference = tmp_path / "out", tmp_path / "reference"
    run(experiment, out)
    write_data(experiment / "ctrl/r1/b.tif", stack(9), "TCYX")
    result = run(experiment, out)
    assert result.read == ("ctrl/r1/b.tif",)
    run(experiment, reference, mode="full")
    assert (out / IMAGE_QC_FILENAME).read_bytes() == (reference / IMAGE_QC_FILENAME).read_bytes()


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("edit", "the image QC table was changed or replaced after the earlier run"),
        ("delete", "the image QC table was changed or replaced after the earlier run"),
        ("unrecorded", "the earlier run has no image QC table (image_qc.csv)"),
        ("version", "the image QC metrics changed since the earlier run"),
    ],
)
def test_rows_are_recomputed_when_the_earlier_table_cannot_be_reused(
    tmp_path, experiment, damage, reason
):
    out = tmp_path / "out"
    run(experiment, out)
    table, manifest_path = out / IMAGE_QC_FILENAME, out / "run_manifest.json"
    good = table.read_bytes()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if damage == "edit":
        table.write_bytes(good.replace(b"measured", b"measured", 1) + b"\n")
    elif damage == "delete":
        table.unlink()
    elif damage == "unrecorded":
        del data["image_qc"]
    else:
        data["image_qc"]["qc_version"] = "0"
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    result = run(experiment, out)
    assert result.rebuild_reason == reason and sorted(result.read) == sorted(IMAGES)
    assert table.read_bytes() == good


def test_an_interrupted_run_resumes_the_staged_rows(tmp_path, experiment, monkeypatch):
    out, reference = tmp_path / "out", tmp_path / "reference"
    run(experiment, reference)
    read = interrupt_at_second_file(monkeypatch)
    with pytest.raises(RuntimeError, match="stopped"):
        run_described(experiment, CTYX, output_folder=out)
    staged = sorted(p.name for p in (out / STAGING_FOLDER).iterdir())
    assert len(staged) == 3 and sum(name.endswith(".qc.csv") for name in staged) == 1
    monkeypatch.setattr(run_module.resume, "_read_file", read)
    result = run(experiment, out)
    assert result.resumed == (IMAGES[0],)
    assert (out / IMAGE_QC_FILENAME).read_bytes() == (reference / IMAGE_QC_FILENAME).read_bytes()


def test_workers_write_the_same_table(tmp_path, experiment):
    serial, parallel = tmp_path / "serial", tmp_path / "parallel"
    run(experiment, serial)
    run(experiment, parallel, mode=RunMode("restart", workers=2))
    assert (serial / IMAGE_QC_FILENAME).read_bytes() == (parallel / IMAGE_QC_FILENAME).read_bytes()


def test_a_log_may_not_be_written_over_the_table():
    assert IMAGE_QC_FILENAME in PROTECTED_OUTPUTS


# The table format -------------------------------------------------------------------------------


def row(**metrics):
    identity = ("a", "a.tif", "0", "1", "c", "b", "t", "x/a.tif", "x/a.tif::z=0,t=1", "GFP")
    blank = tuple((name, "plane_too_small") for name in METRICS if name not in metrics)
    return QcRow(identity, True, ImageQc("measured", **metrics, reasons=blank))


def test_the_table_reads_back_exactly(tmp_path):
    rows = [row(entropy=1 / 3, correlation_1=-0.1), row(entropy=1.0, tenengrad=1e-300)]
    path = write_image_qc(rows, tmp_path / IMAGE_QC_FILENAME)
    assert read_image_qc(path) == sorted(rows, key=QcRow.order)
    line = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))[1]
    assert line[COLUMNS.index("foreground_fraction")] == ""  # blank, never zero
    assert line[-1] == QC_VERSION and line[COLUMNS.index("read_in_blocks")] == "true"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("qc_status", "status"),
        ("measured", "fine"),
        (",true,", ",yes,"),
        (f",{QC_VERSION}\n", ",9\n"),
    ],
)
def test_the_reader_refuses_a_malformed_table(tmp_path, old, new):
    text = table_text([row(entropy=1.0)]).replace(old, new, 1)
    (tmp_path / "t.csv").write_text(text, encoding="utf-8")
    with pytest.raises(ImageQcFormatError):
        read_image_qc(tmp_path / "t.csv")


@pytest.mark.parametrize("value", ["nan", "inf", "x"])
def test_the_reader_refuses_a_value_that_is_not_a_finite_number(tmp_path, value):
    text = table_text([row(entropy=1.5)]).replace("1.5", value)
    (tmp_path / "t.csv").write_text(text, encoding="utf-8")
    with pytest.raises(ImageQcFormatError, match="entropy"):
        read_image_qc(tmp_path / "t.csv")


def test_the_manifest_entry_is_read_strictly():
    entry = image_qc_entry("0" * 64)
    assert recorded_image_qc(entry) == entry and recorded_image_qc(None) is None
    for broken in (
        {**entry, "extra": 1},
        {**entry, "file": "other.csv"},
        {**entry, "sha256": "abc"},
        {**entry, "qc_version": 1},
        {**entry, "settings": []},
        [],
    ):
        with pytest.raises(ValueError):
            recorded_image_qc(broken)


def test_the_metric_columns_are_the_metrics():
    assert COLUMNS[COLUMNS.index("qc_status") + 1 : -2] == METRICS
