from dataclasses import replace

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    Severity,
    TiffMetadata,
    check_metadata,
    discover_experiment,
    inspect_experiment,
    inspect_images,
    inspect_tiff,
)
from tiff_tree import make_tree, write_file, write_tiff


def codes(issues):
    return [issue.code for issue in issues]


def issue(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


def meta(path, shape=(8, 8), dtype="uint16", axes="YX", readable=True):
    return TiffMetadata(path, readable, shape, np.dtype(dtype), axes, 1, 1)


@pytest.fixture
def no_pixel_reads(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("pixel data was read")

    for cls in (tifffile.TiffFile, tifffile.TiffPage, tifffile.TiffFrame, tifffile.TiffPageSeries):
        monkeypatch.setattr(cls, "asarray", fail)


# Single files -------------------------------------------------------------------------


def test_inspect_2d(tmp_path, no_pixel_reads):
    path = write_tiff(tmp_path / "a.tif", shape=(6, 7), dtype=np.uint16)
    info = inspect_tiff(path, "cond/a.tif")
    assert info == TiffMetadata(
        relative_path="cond/a.tif",
        readable=True,
        shape=(6, 7),
        dtype=np.dtype("uint16"),
        axes="YX",
        series_count=1,
        page_count=1,
    )
    assert info.ndim == 2 and info.dtype_supported


def test_relative_path_defaults_to_file_name(tmp_path):
    assert inspect_tiff(write_tiff(tmp_path / "a.tif")).relative_path == "a.tif"


@pytest.mark.parametrize(
    ("shape", "axes", "kwargs"),
    [
        ((3, 6, 7), "ZYX", {}),
        ((2, 3, 6, 7), "TCYX", {}),
        ((2, 3, 6, 7), "ZCYX", {"imagej": True}),
        ((2, 6, 7), "CYX", {"ome": True}),
    ],
)
def test_inspect_axes_from_metadata(tmp_path, no_pixel_reads, shape, axes, kwargs):
    dtype = np.float32 if kwargs.get("imagej") else np.uint16
    info = inspect_tiff(write_tiff(tmp_path / "a.tif", shape, dtype, axes=axes, **kwargs))
    assert info.readable
    assert info.shape == shape
    assert info.axes == axes


def test_page_and_series_counts(tmp_path):
    path = tmp_path / "multi.tif"
    with tifffile.TiffWriter(path) as writer:
        writer.write(np.zeros((4, 5), np.uint8))
        writer.write(np.zeros((6, 7), np.uint8))
    info = inspect_tiff(path)
    assert info.series_count == 2
    assert info.page_count == 2
    assert info.shape == (4, 5)  # the first series


def test_multiple_series_are_an_error(tmp_path):
    with tifffile.TiffWriter(tmp_path / "multi.tif") as writer:
        writer.write(np.zeros((4, 5), np.uint8))
        writer.write(np.zeros((6, 7), np.uint8))
    write_tiff(tmp_path / "one.tif", dtype=np.uint8)
    report = inspect_images(tmp_path, ["multi.tif", "one.tif"])
    found = issue(report.issues, "multiple_series")
    assert found.severity is Severity.ERROR
    assert found.paths == ("multi.tif",)
    assert "separate image file" in found.action


def test_pyramid_levels_are_not_separate_series(tmp_path):
    path = tmp_path / "pyramid.ome.tif"
    with tifffile.TiffWriter(path, ome=True) as writer:
        writer.write(np.zeros((32, 32), np.uint16), subifds=1, photometric="minisblack")
        writer.write(np.zeros((16, 16), np.uint16), subfiletype=1, photometric="minisblack")
    info = inspect_tiff(path)
    assert info.series_count == 1 and info.shape == (32, 32)
    assert check_metadata([info]) == []


def test_ome_channel_names_and_significant_bits(tmp_path, no_pixel_reads):
    path = tmp_path / "a.ome.tif"
    tifffile.imwrite(
        path,
        np.zeros((2, 6, 7), np.uint16),
        ome=True,
        photometric="minisblack",
        metadata={"axes": "CYX", "Channel": {"Name": ["DAPI", "GFP"]}, "SignificantBits": 12},
    )
    info = inspect_tiff(path)
    assert info.channel_names == ("DAPI", "GFP")
    assert info.significant_bits == 12


def test_no_channel_names_without_ome_metadata(tmp_path):
    info = inspect_tiff(write_tiff(tmp_path / "a.tif", (2, 6, 7), axes="CYX", imagej=True))
    assert info.channel_names == () and info.significant_bits is None


def test_unnamed_ome_channels_are_empty(tmp_path):
    info = inspect_tiff(write_tiff(tmp_path / "a.tif", (2, 6, 7), axes="CYX", ome=True))
    assert info.channel_names == ()


def test_differing_channel_names_are_a_warning():
    base = meta("a.tif", (2, 8, 8), axes="CYX")
    files = [
        replace(base, channel_names=("DAPI", "GFP")),
        replace(base, relative_path="b.tif", channel_names=("DAPI", "GFP")),
        replace(base, relative_path="c.tif", channel_names=("DAPI", "RFP")),
        replace(base, relative_path="d.tif", channel_names=(None, None)),
    ]
    found = issue(check_metadata(files), "channel_names_differ")
    assert found.severity is Severity.WARNING
    assert found.paths == ("c.tif",)
    assert "channel 1: GFP (2 images), RFP (1 image)" in found.message


def test_matching_or_missing_channel_names_give_no_warning():
    unnamed = meta("b.tif", (2, 8, 8), axes="CYX")
    named = replace(unnamed, relative_path="a.tif", channel_names=("A",))
    assert check_metadata([named, replace(named, relative_path="c.tif")]) == []
    assert check_metadata([named, unnamed]) == []


@pytest.mark.parametrize(
    "dtype", ["int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"]
)
def test_supported_integer_dtypes(tmp_path, dtype):
    info = inspect_tiff(write_tiff(tmp_path / "a.tif", dtype=dtype))
    assert info.dtype == np.dtype(dtype) and info.dtype_supported


@pytest.mark.parametrize("dtype", ["float32", "float64"])
def test_supported_float_dtypes(tmp_path, dtype):
    info = inspect_tiff(write_tiff(tmp_path / "a.tif", dtype=dtype))
    assert info.dtype == np.dtype(dtype) and info.dtype_supported


def test_big_endian_dtype_is_native(tmp_path):
    info = inspect_tiff(write_tiff(tmp_path / "a.tif", dtype=">u2", byteorder=">"))
    assert info.dtype == np.dtype("uint16")


@pytest.mark.parametrize(
    "content",
    [b"", b"not a tiff at all", None],
    ids=["empty", "garbage", "truncated"],
)
def test_unreadable_files(tmp_path, content):
    path = tmp_path / "bad.tif"
    if content is None:
        write_tiff(path)
        path.write_bytes(path.read_bytes()[:60])
    else:
        write_file(path, content)
    info = inspect_tiff(path, "bad.tif")
    assert not info.readable
    assert info.shape == () and info.dtype is None
    assert info.error
    assert not info.dtype_supported


# Experiment-wide checks ---------------------------------------------------------------


def test_consistent_experiment_has_no_issues(tmp_path, no_pixel_reads):
    make_tree(tmp_path, ["c1/a.tif", "c1/b.tif", "c2/a.tif"])
    report = inspect_experiment(discover_experiment(tmp_path))
    assert report.ok and report.issues == ()
    assert [f.relative_path for f in report.files] == ["c1/a.tif", "c1/b.tif", "c2/a.tif"]
    assert report.dtype == np.dtype("uint16")


def test_unreadable_tiff_in_leaf_is_an_error(tmp_path):
    make_tree(tmp_path, ["cond/a.tif"])
    write_file(tmp_path / "cond" / "b.tif", b"garbage")
    report = inspect_experiment(discover_experiment(tmp_path))
    assert not report.ok
    found = issue(report.issues, "unreadable_tiff")
    assert found.severity is Severity.ERROR
    assert found.paths == ("cond/b.tif",)
    assert "Traceback" not in str(found)
    # The unreadable file does not count as a second pixel type.
    assert codes(report.issues) == ["unreadable_tiff"]
    assert report.dtype == np.dtype("uint16")


def test_mixed_dtypes_are_an_error(tmp_path):
    write_tiff(tmp_path / "c/a.tif", dtype=np.uint16)
    write_tiff(tmp_path / "c/b.tif", dtype=np.uint16)
    write_tiff(tmp_path / "c/c.tif", dtype=np.uint8)
    report = inspect_images(tmp_path, ["c/a.tif", "c/b.tif", "c/c.tif"])
    found = issue(report.issues, "mixed_dtypes")
    assert found.severity is Severity.ERROR
    assert found.paths == ("c/c.tif",)
    assert "uint16 (2 images), uint8 (1 image)" in found.message
    assert report.dtype is None


@pytest.mark.parametrize("dtype", ["float16", "complex64", "bool"])
def test_unsupported_dtype_is_an_error(tmp_path, dtype):
    write_tiff(tmp_path / "a.tif", dtype=dtype)
    report = inspect_images(tmp_path, ["a.tif"])
    found = issue(report.issues, "unsupported_dtype")
    assert found.severity is Severity.ERROR
    assert found.paths == ("a.tif",)
    assert dtype in found.message


def test_different_arrangement_is_an_error(tmp_path):
    write_tiff(tmp_path / "a.tif", (3, 6, 7), axes="ZYX")
    write_tiff(tmp_path / "b.tif", (3, 6, 7), axes="ZYX")
    write_tiff(tmp_path / "c.tif", (3, 6, 7), axes="CYX")
    report = inspect_images(tmp_path, ["a.tif", "b.tif", "c.tif"])
    found = issue(report.issues, "inconsistent_dimensions")
    assert found.severity is Severity.ERROR
    assert found.paths == ("c.tif",)
    assert "ZYX (2 images), CYX (1 image)" in found.message


def test_different_axis_order_is_an_error(tmp_path):
    write_tiff(tmp_path / "a.tif", (2, 3, 6, 7), axes="ZCYX")
    write_tiff(tmp_path / "b.tif", (3, 2, 6, 7), axes="CZYX")
    report = inspect_images(tmp_path, ["a.tif", "b.tif"])
    assert codes(report.issues) == ["inconsistent_dimensions"]


def test_missing_axis_is_reported_separately(tmp_path):
    write_tiff(tmp_path / "a.tif", (2, 3, 6, 7), axes="ZCYX")
    write_tiff(tmp_path / "b.tif", (2, 6, 7), axes="ZYX")
    report = inspect_images(tmp_path, ["a.tif", "b.tif"])
    found = issue(report.issues, "missing_axes")
    assert found.severity is Severity.ERROR
    assert "axis review" in found.action


def test_missing_axes_with_two_longest_arrangements_is_inconsistent():
    files = [
        meta("a.tif", (2, 3, 6, 7), axes="ZCYX"),
        meta("b.tif", (3, 2, 6, 7), axes="CZYX"),
        meta("c.tif", (6, 7), axes="YX"),
    ]
    assert codes(check_metadata(files)) == ["inconsistent_dimensions"]


def test_different_sizes_are_a_warning(tmp_path):
    write_tiff(tmp_path / "a.tif", (3, 6, 7), axes="ZYX")
    write_tiff(tmp_path / "b.tif", (3, 6, 7), axes="ZYX")
    write_tiff(tmp_path / "c.tif", (5, 8, 9), axes="ZYX")
    report = inspect_images(tmp_path, ["a.tif", "b.tif", "c.tif"])
    assert report.ok
    found = issue(report.issues, "different_sizes")
    assert found.severity is Severity.WARNING
    assert found.paths == ("c.tif",)
    assert "3 x 6 x 7 (2 images), 5 x 8 x 9 (1 image)" in found.message


def test_check_metadata_errors_before_warnings():
    files = [
        meta("a.tif", dtype="uint16"),
        meta("b.tif", dtype="uint8", shape=(9, 9)),
        TiffMetadata("c.tif", readable=False, error="TiffFileError: bad"),
    ]
    assert codes(check_metadata(files)) == ["unreadable_tiff", "mixed_dtypes", "different_sizes"]


def test_check_metadata_empty():
    assert check_metadata([]) == []


def test_inspection_follows_discovery_labels(tmp_path):
    make_tree(tmp_path, ["stray.tif", "cond/bio/a.tif"])
    discovery = discover_experiment(tmp_path)
    report = inspect_experiment(discovery)
    # Files outside leaf folders are not inspected.
    assert [f.relative_path for f in report.files] == ["cond/bio/a.tif"]
