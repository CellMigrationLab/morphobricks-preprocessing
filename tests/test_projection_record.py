"""Conversion record version 3: the projection (issue #176, contract §17).

Reading is strict: a projected entry that contradicts itself, the record or the dtype
table is refused, and the reuse key holds the projection.
"""

import json
from dataclasses import replace

import pytest

from morphobricks_preprocessing import (
    AxisMapping,
    ConversionRecord,
    ConversionRecordError,
    Projection,
    convert_experiment,
    read_conversion_record,
)
from morphobricks_preprocessing.conversion_record import (
    PROJECTED,
    RECORD_FILENAME,
    RECORD_VERSION,
    CopyEntry,
    write_record,
)
from morphobricks_preprocessing.fingerprint import Fingerprint
from morphobricks_preprocessing.projection_record import ProjectionEntry
from morphobricks_preprocessing.recorded_sizes import RecordedSize
from test_convert_run import SWAPPED, swapped_experiment

SHA = "a" * 64
Z_SIZE = RecordedSize("Z", 2.5, "micrometer", "header")
MAX_2_10 = Projection("max", (2, 10))


def projected_entry(**changes) -> CopyEntry:
    """Return a projected entry: a 12-slice uint16 stack, max over slices 2:10."""
    details = ProjectionEntry(
        source_relative_path="c/a.tif",
        source_content_sha256=SHA,
        source_z_axis=("Z", 0),
        source_z_size=12,
        z_range=(2, 10),
        method="max",
        source_dtype="uint16",
        output_dtype="uint16",
        dtype_rule="max uint16->uint16",
        rounding="none",
        dropped_sizes={"Z": Z_SIZE},
    )
    entry = CopyEntry(
        relative_path="c/a.tif",
        action=PROJECTED,
        reason="axes_not_named",
        source=Fingerprint(10, 20),
        copy=Fingerprint(5, 6),
        source_axes="QYX",
        roles=("Z", "Y", "X"),
        copy_axes="YX",
        copy_sha256=SHA,
        projection=details,
    )
    return replace(entry, **changes)


def record(entry=None, projection=MAX_2_10, **changes) -> ConversionRecord:
    return ConversionRecord(
        source_root="/exp",
        mapping=AxisMapping("ZYX", ("Z", "Y", "X")),
        missing_as_singleton=False,
        files=(entry or projected_entry(),),
        versions={"numpy": "2"},
        written_at="2026-11-07T00:00:00Z",
        projection=projection,
        **changes,
    )


def reread(rec: ConversionRecord) -> ConversionRecord:
    return ConversionRecord.from_dict(json.loads(json.dumps(rec.to_dict())))


def edited(change, rec=None) -> dict:
    data = json.loads(json.dumps((rec or record()).to_dict()))
    change(data)
    return data


def test_a_projected_record_round_trips():
    rec = record()
    assert RECORD_VERSION == "3"
    again = reread(rec)
    assert again == rec
    data = rec.to_dict()
    assert data["projection"] == {"method": "max", "z_range": [2, 10]}
    done = data["files"][0]["projection"]
    assert done["source_z_axis"] == {"letter": "Z", "index": 0}
    assert done["dropped_sizes"] == {"Z": {"value": 2.5, "unit": "micrometer", "source": "header"}}
    assert done["nan_policy"] == "propagate" and done["significant_bits_dropped"] is None


def test_a_record_without_a_projection_is_version_3_with_null(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    data = json.loads((copies / RECORD_FILENAME).read_text())
    assert data["record_version"] == "3" and data["projection"] is None
    assert all(entry["projection"] is None for entry in data["files"])
    assert read_conversion_record(copies)[0].projection is None


def test_a_version_2_record_is_refused_naming_the_fix(tmp_path):
    exp, copies = swapped_experiment(tmp_path / "exp"), tmp_path / "copies"
    assert convert_experiment(exp, SWAPPED, copies).written
    path = copies / RECORD_FILENAME
    data = json.loads(path.read_text())
    data["record_version"] = "2"
    del data["projection"]
    for entry in data["files"]:
        del entry["projection"]
    path.write_text(json.dumps(data))
    with pytest.raises(ConversionRecordError, match="version 2,.*convert the originals again"):
        read_conversion_record(copies)


@pytest.mark.parametrize("version", ["1", "2", "4", None])
def test_other_versions_are_refused(version):
    with pytest.raises(ConversionRecordError, match="reads version 3 only"):
        ConversionRecord.from_dict(edited(lambda d: d.update(record_version=version)))


def test_a_record_without_its_projection_key_is_refused():
    with pytest.raises(ConversionRecordError, match="projection"):
        ConversionRecord.from_dict(edited(lambda d: d.pop("projection")))


def test_the_record_is_written_and_read_from_the_copy_tree(tmp_path):
    rec = record()
    sha = write_record(rec, tmp_path)
    found, read_sha = read_conversion_record(tmp_path)
    assert found == rec and read_sha == sha


def test_a_changed_projection_field_changes_the_reuse_key():
    base = projected_entry()
    assert base.same_plan(projected_entry())
    done = base.projection
    changes = [
        {"method": "sum", "output_dtype": "uint32", "dtype_rule": "sum uint16->uint32 exact"},
        {"z_range": (2, 9)},
        {"source_z_size": 13},
        {"source_z_axis": ("Z", 1)},
        {"source_dtype": "uint8", "output_dtype": "uint8"},
        {"dropped_sizes": {"Z": replace(Z_SIZE, value=2.6)}},
        {"dropped_sizes": {"Z": None}},
        {"significant_bits_dropped": "a sum can exceed the range"},
        {"dtype_rule": "max uint16->uint16 (edited)"},
    ]
    for change in changes:
        other = replace(base, projection=replace(done, **change))
        assert not base.same_plan(other), change
    # The source content SHA-256 is a result of the write, not part of the plan.
    assert base.same_plan(replace(base, projection=done.with_content("b" * 64)))
    # A projected entry is never the plan of an unprojected one.
    assert not base.same_plan(replace(base, action="converted", projection=None))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(projection=None), "no projection"),
        (lambda d: d["files"][0].update(projection=None), "needs its projection"),
        (lambda d: d["files"][0].update(action="converted"), "needs its projection"),
        (lambda d: d["files"][0]["projection"].update(method="mean"), "mean"),
        (lambda d: d["files"][0]["projection"].update(z_range=[0, 12]), "not the record's"),
        (lambda d: d["projection"].update(method="mean"), "not the record's"),
        (lambda d: d["projection"].update(method="median"), "needs a method"),
        (lambda d: d["projection"].update(z_range=[5, 2]), "START:STOP"),
        (lambda d: d["projection"].pop("z_range"), "method, z_range"),
        (lambda d: d["files"][0]["projection"].update(output_dtype="float64"), "uint16"),
        (lambda d: d["files"][0]["projection"].update(rounding="float64, one division"), "round"),
        (lambda d: d["files"][0]["projection"].update(rounding="sloppy"), "rounding"),
        (lambda d: d["files"][0]["projection"].update(nan_policy="zero"), "NaN policy"),
        (lambda d: d["files"][0]["projection"].update(source_z_size=5), "does not fit"),
        (lambda d: d["files"][0]["projection"].update(source_content_sha256=None), "SHA-256"),
        (lambda d: d["files"][0]["projection"].update(extra=1), "keys"),
        (lambda d: d["files"][0]["projection"].update(source_dtype="uint64"), "cannot"),
        (lambda d: d["files"][0]["projection"].update(source_z_axis={"letter": "Z"}), "letter"),
        (lambda d: d["files"][0]["projection"]["dropped_sizes"].update(X=None), "only the Z"),
        (
            lambda d: d["files"][0]["projection"]["dropped_sizes"].update(
                Z={"value": None, "unit": None, "source": None}
            ),
            "dropped size",
        ),
    ],
)
def test_a_projected_record_that_contradicts_itself_is_refused(change, message):
    with pytest.raises(ConversionRecordError, match=message):
        ConversionRecord.from_dict(edited(change))


def test_a_stack_without_a_z_axis_must_have_one_slice():
    done = projected_entry().projection
    one = replace(done, source_z_axis=None, source_z_size=1, z_range=(0, 1))
    assert one.source_z_axis is None
    with pytest.raises(ValueError, match="no Z axis"):
        replace(done, source_z_axis=None)


def test_all_slices_means_the_whole_stack_is_recorded():
    with pytest.raises(ValueError, match="not the record's"):
        record(projection=Projection("max"))  # the entry used 2:10, the record says all slices
    whole = replace(projected_entry().projection, z_range=(0, 12))
    assert reread(record(replace(projected_entry(), projection=whole), Projection("max")))


def test_a_record_of_a_sum_that_could_not_be_exact_is_refused():
    done = projected_entry().projection
    kwargs = {"source_dtype": "uint32", "method": "sum", "output_dtype": "float64"}
    fits = replace(done, source_z_size=2**21, z_range=(0, 2**21), **kwargs)
    assert fits.output_dtype == "float64"
    with pytest.raises(ValueError, match="exact"):
        replace(fits, source_z_size=2**21 + 1, z_range=(0, 2**21 + 1))


def test_an_excluded_image_stays_a_plain_copy_in_a_projected_tree():
    excluded = CopyEntry(
        "c/x.tif", "copied", "excluded", Fingerprint(1, 2), copy=Fingerprint(1, 2), copy_sha256=SHA
    )
    rec = ConversionRecord(
        "/exp",
        AxisMapping("ZYX", tuple("ZYX")),
        False,
        (projected_entry(), excluded),
        projection=Projection("max", (2, 10)),
    )
    assert reread(rec) == rec
    plain = replace(excluded, reason=None)
    with pytest.raises(ValueError, match="not projected"):
        ConversionRecord(
            "/exp", AxisMapping("ZYX", tuple("ZYX")), False, (plain,), projection=Projection("max")
        )
