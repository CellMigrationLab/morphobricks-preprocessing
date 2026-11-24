"""Recorded pixel sizes are positive finite numbers, one per role (#389)."""

import json

import pytest

from morphobricks_preprocessing.conversion_record import (
    ConversionRecord,
    ConversionRecordError,
)
from morphobricks_preprocessing.manifest import ManifestFormatError, read_manifest
from morphobricks_preprocessing.recorded_sizes import RecordedSize, sizes_from_data
from morphobricks_preprocessing.testing import make_clean_run
from test_projection_record import edited

GOOD = [
    {"role": "X", "value": 0.65, "unit": "micrometer", "source": "stated"},
    {"role": "Y", "value": 0.65, "unit": "micrometer", "source": "header"},
    {"role": "Z", "value": None, "unit": None, "source": "pixels"},
    {"role": "T", "value": None, "unit": None, "source": None},
]

BAD_X = [
    {"value": float("nan")},
    {"value": float("inf")},
    {"value": -1.0},
    {"value": 0.0},
    {"value": 0},
    {"value": "1.0"},
    {"value": True},
    {"unit": None},
    {"unit": ""},
    {"unit": 7},
]

BAD_LISTS = [
    GOOD + [GOOD[0]],  # a duplicate X
    GOOD[:3],  # no T
    [GOOD[0], GOOD[0], GOOD[2], GOOD[3]],  # X twice and no Y
]


def with_x(change):
    return [{**GOOD[0], **change}, *GOOD[1:]]


def test_the_good_sizes_read():
    sizes = sizes_from_data(GOOD)
    assert sizes[0] == RecordedSize("X", 0.65, "micrometer", "stated")
    assert sizes_from_data(json.loads(json.dumps(GOOD, allow_nan=True))) == sizes


@pytest.mark.parametrize("change", BAD_X)
def test_a_size_that_is_not_a_positive_finite_number_with_a_unit_is_refused(change):
    with pytest.raises(ValueError, match="pixel size"):
        sizes_from_data(with_x(change))


@pytest.mark.parametrize("sizes", BAD_LISTS)
def test_a_list_without_exactly_one_size_per_role_is_refused(sizes):
    with pytest.raises(ValueError, match="one size per role"):
        sizes_from_data(sizes)


@pytest.fixture(scope="module")
def manifest_data(tmp_path_factory):
    run = make_clean_run(tmp_path_factory.mktemp("run"), axes="YX", shape=(8, 8))
    path = run.output / "run_manifest.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("sizes", [with_x(c) for c in BAD_X[:4]] + BAD_LISTS)
def test_read_manifest_refuses_them(manifest_data, tmp_path, sizes):
    path, data = manifest_data
    data = json.loads(json.dumps(data))
    data["files"][0]["pixel_sizes"] = sizes
    edited_path = tmp_path / "run_manifest.json"
    edited_path.write_text(json.dumps(data, allow_nan=True), encoding="utf-8")
    with pytest.raises(ManifestFormatError):
        read_manifest(edited_path)


@pytest.mark.parametrize("sizes", [with_x(c) for c in BAD_X[:4]] + BAD_LISTS)
def test_the_conversion_record_refuses_them(sizes):
    def change(data):
        data["files"][0]["pixel_sizes"] = sizes

    with pytest.raises(ConversionRecordError):
        ConversionRecord.from_dict(edited(change))


def test_a_plain_copy_records_no_sizes():
    def copied(data):
        entry = data["files"][0]
        entry.update(action="copied", reason="excluded", projection=None, pixel_sizes=[])
        entry.update(roles=None, copy_axes=None)

    data = edited(copied)
    data["projection"] = None
    assert ConversionRecord.from_dict(data).files[0].pixel_sizes == ()


def test_the_manifest_writer_refuses_what_the_reader_refuses():
    from dataclasses import replace

    from morphobricks_preprocessing.manifest_files import file_dict
    from test_manifest import entry

    good = entry()
    assert file_dict(good)["pixel_sizes"][0]["role"] == "X"
    for sizes in (good.pixel_sizes[:3], (*good.pixel_sizes, good.pixel_sizes[0])):
        with pytest.raises(ValueError, match="one size per role"):
            file_dict(replace(good, pixel_sizes=sizes))
    nan = (RecordedSize("X", float("nan"), "micrometer", "stated"), *good.pixel_sizes[1:])
    with pytest.raises(ValueError, match="positive, finite"):
        file_dict(replace(good, pixel_sizes=nan))
