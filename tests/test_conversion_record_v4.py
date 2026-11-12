"""Conversion record version 4 (owner decision of 12 November 2026, before v0.5.2).

PR #287 added the entry key ``source_relative_path`` (an OME-Zarr copy's source path) but kept
version 3, so v0.5.1, which reads version 3 and ignores the key, read such a record and warned
that every source went missing (``conversion_source_changed``, at the copy's path). The record
is version 4: a version 3 record is refused everywhere it is read, naming the fix, and the key
is required exactly where the writer writes it, so a source is never looked for at a copy's
path.
"""

import json

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing.axes import prefill_mapping
from morphobricks_preprocessing.conversion import convert_experiment
from morphobricks_preprocessing.conversion_record import (
    RECORD_FILENAME,
    RECORD_VERSION,
    ConversionRecord,
    ConversionRecordError,
    read_record,
    write_record,
)
from morphobricks_preprocessing.projection import Projection
from morphobricks_preprocessing.run import preflight

FIX = "convert the originals again into a new folder"
FOLDERS = ("ctrl/rep1", "drug/rep1")


def stacks(root, axes="CYX", shape=(2, 8, 8)):
    """Write one named-axis image per folder."""
    for seed, folder in enumerate(FOLDERS):
        data = np.random.default_rng(seed).integers(0, 4000, shape, dtype=np.uint16)
        path = root / folder / "a.tif"
        path.parent.mkdir(parents=True)
        tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata={"axes": axes})
    return root


def zarr_copies(tmp_path, projection=None):
    """Convert a small tree into OME-Zarr copies; return the copy tree."""
    pytest.importorskip("zarr")
    axes, shape = ("ZCYX", (3, 2, 8, 8)) if projection else ("CYX", (2, 8, 8))
    root, dest = stacks(tmp_path / "exp", axes, shape), tmp_path / "copies"
    result = convert_experiment(
        root, prefill_mapping(axes), dest, projection=projection, store="ome-zarr"
    )
    assert result.written and not result.issues, result.issues
    return dest


def rewrite(dest, change):
    path = dest / RECORD_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def conversion_codes(dest, tmp_path):
    review = preflight(dest, tmp_path / "out").conversion
    return [issue.code for issue in review.issues]


@pytest.mark.parametrize("projection", [None, Projection("max")], ids=["converted", "projected"])
def test_a_version_4_record_with_ome_zarr_copies_round_trips(tmp_path, projection):
    dest = zarr_copies(tmp_path, projection)
    text = (dest / RECORD_FILENAME).read_text(encoding="utf-8")
    data = json.loads(text)
    assert RECORD_VERSION == "4" and data["record_version"] == "4"
    assert [e["source_relative_path"] for e in data["files"]] == [f"{f}/a.tif" for f in FOLDERS]
    record, _ = read_record(dest)
    assert record.to_dict() == data
    assert ConversionRecord.from_dict(data) == record
    (tmp_path / "again").mkdir()
    write_record(record, tmp_path / "again")
    assert (tmp_path / "again" / RECORD_FILENAME).read_text(encoding="utf-8") == text
    assert conversion_codes(dest, tmp_path) == []


def test_a_version_3_record_is_refused_naming_the_fix(tmp_path):
    dest = zarr_copies(tmp_path)
    rewrite(dest, lambda d: d.update(record_version="3"))
    with pytest.raises(ConversionRecordError) as refused:
        read_record(dest)
    message = str(refused.value)
    assert "record of version 3," in message and "reads version 4 only" in message
    assert message.endswith(FIX)
    # The run stops on it and never reads it: no false conversion_source_changed.
    assert conversion_codes(dest, tmp_path) == ["conversion_record_unreadable"]
    # Converting into the same folder again is refused, nothing overwritten.
    again = convert_experiment(tmp_path / "exp", prefill_mapping("CYX"), dest, store="ome-zarr")
    assert not again.written
    assert [i.code for i in again.issues] == ["conversion_record_unreadable"]
    assert "version 3" in again.issues[0].message


def test_an_ome_zarr_copy_without_its_source_path_is_refused_not_warned_missing(tmp_path):
    # What v0.5.1 made of a version 3 record of #287: the key ignored, so the source was looked
    # for at the copy's path and every source was reported missing.
    dest = zarr_copies(tmp_path)

    def drop(data):
        for entry in data["files"]:
            del entry["source_relative_path"]

    rewrite(dest, drop)
    with pytest.raises(ConversionRecordError, match="OME-Zarr copy without its source_relative"):
        read_record(dest)
    assert conversion_codes(dest, tmp_path) == ["conversion_record_unreadable"]


@pytest.mark.parametrize("value", [None, "", "ctrl/rep1/a.ome.zarr", 7])
def test_an_ome_zarr_copy_needs_its_source_path_as_text(tmp_path, value):
    dest = zarr_copies(tmp_path)
    rewrite(dest, lambda d: d["files"][0].update(source_relative_path=value))
    with pytest.raises(ConversionRecordError, match="without its source_relative_path"):
        read_record(dest)


def test_a_source_path_on_another_entry_is_refused(tmp_path):
    root, dest = stacks(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(root, prefill_mapping("CYX"), dest).written
    data = json.loads((dest / RECORD_FILENAME).read_text(encoding="utf-8"))
    assert data["record_version"] == "4"
    assert all("source_relative_path" not in entry for entry in data["files"])
    rewrite(dest, lambda d: d["files"][0].update(source_relative_path="ctrl/rep1/a.tif"))
    with pytest.raises(ConversionRecordError, match="is not an OME-Zarr copy"):
        read_record(dest)


def test_the_suffix_is_the_copy_writers():
    from morphobricks_preprocessing.conversion_record import _ZARR_COPY_SUFFIX
    from morphobricks_preprocessing.ome_zarr_copy import ZARR_COPY_SUFFIX

    assert _ZARR_COPY_SUFFIX == ZARR_COPY_SUFFIX


def test_the_notebook_does_not_offer_version_3_projections(tmp_path):
    from morphobricks_preprocessing.notebook_projection import earlier_projections

    root, output = stacks(tmp_path / "exp", "ZCYX", (3, 2, 8, 8)), tmp_path / "out"
    dest = output / "projected_max"
    assert convert_experiment(root, prefill_mapping("ZCYX"), dest, projection=Projection("max"))
    assert [folder for folder, _ in earlier_projections(root, output)] == [dest]
    rewrite(dest, lambda d: d.update(record_version="3"))
    assert earlier_projections(root, output) == []


@pytest.mark.parametrize("store", ["ome-tiff", "ome-zarr"])
def test_a_projected_entry_names_one_source(tmp_path, store):
    # The source path is written twice on a projected copy (the entry's and its projection's);
    # two different paths would let the run check one source and record another as projected.
    if store == "ome-zarr":
        pytest.importorskip("zarr")
    root, dest = stacks(tmp_path / "exp", "ZCYX", (3, 2, 8, 8)), tmp_path / "copies"
    written = convert_experiment(
        root, prefill_mapping("ZCYX"), dest, projection=Projection("max"), store=store
    )
    assert written.written and not written.issues, written.issues
    assert read_record(dest) is not None
    rewrite(dest, lambda d: d["files"][0]["projection"].update(source_relative_path="drug/x.tif"))
    with pytest.raises(ConversionRecordError, match="projection names the source 'drug/x.tif'"):
        read_record(dest)
    assert conversion_codes(dest, tmp_path) == ["conversion_record_unreadable"]
