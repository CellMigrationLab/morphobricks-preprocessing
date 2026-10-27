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
    assert "separate image" in found.action


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
    assert codes(check_metadata(files)) == ["inconsistent_dimensions", "different_plane_counts"]


def test_different_sizes_are_a_warning(tmp_path):
    write_tiff(tmp_path / "a.tif", (3, 6, 7), axes="ZYX")
    write_tiff(tmp_path / "b.tif", (3, 6, 7), axes="ZYX")
    write_tiff(tmp_path / "c.tif", (5, 8, 9), axes="ZYX")
    report = inspect_images(tmp_path, ["a.tif", "b.tif", "c.tif"])
    assert report.ok
    found = issue(report.issues, "different_sizes")
    assert found.severity is Severity.WARNING
    assert found.paths == ("c.tif",)
    # Width and height only; the depth is reported as a different number of planes.
    assert "6 x 7 (2 images), 8 x 9 (1 image)" in found.message
    planes = issue(report.issues, "different_plane_counts")
    assert "depth slices (Z): 3 (2 images), 5 (1 image)" in planes.message


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


# Dataset consistency of the headers (follow-up P2) -------------------------------------


def write_ome(path, shape=(8, 8), axes="YX", **sizes):
    """Write an OME-TIFF with the given OME pixel-size attributes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        path,
        np.zeros(shape, np.uint16),
        ome=True,
        photometric="minisblack",
        metadata={"axes": axes, **sizes},
    )
    return path


def test_consistent_images_give_no_warnings(tmp_path, no_pixel_reads):
    for name in ("a", "b"):
        write_ome(
            tmp_path / f"{name}.ome.tif",
            (3, 8, 8),
            "ZYX",
            PhysicalSizeX=0.5,
            PhysicalSizeY=0.5,
            PhysicalSizeZ=2.0,
        )
    report = inspect_images(tmp_path, ["a.ome.tif", "b.ome.tif"])
    assert report.issues == ()
    assert report.files[0].physical_sizes.micrometers("Z") == 2.0


def test_different_pixel_sizes(tmp_path):
    write_ome(tmp_path / "a.ome.tif", PhysicalSizeX=0.1625, PhysicalSizeY=0.1625)
    write_ome(tmp_path / "b.ome.tif", PhysicalSizeX=0.1625, PhysicalSizeY=0.1625)
    # The same size in another unit, with rounding noise, is the same size.
    write_ome(
        tmp_path / "c.ome.tif",
        PhysicalSizeX=162.50001,
        PhysicalSizeXUnit="nm",
        PhysicalSizeY=162.5,
        PhysicalSizeYUnit="nm",
    )
    write_ome(tmp_path / "d.ome.tif", PhysicalSizeX=0.325, PhysicalSizeY=0.325)
    report = inspect_images(tmp_path, ["a.ome.tif", "b.ome.tif", "c.ome.tif", "d.ome.tif"])
    found = issue(report.issues, "different_pixel_sizes")
    assert found.severity is Severity.WARNING and found.paths == ("d.ome.tif",)
    assert "0.1625 x 0.1625 micrometres (3 images)" in found.message
    assert "0.325 x 0.325 micrometres (1 image)" in found.message
    assert found.message.isascii()


def test_z_steps_are_compared_for_stacks_only(tmp_path):
    write_ome(
        tmp_path / "a.ome.tif", (3, 8, 8), "ZYX", PhysicalSizeX=1, PhysicalSizeY=1, PhysicalSizeZ=2
    )
    write_ome(
        tmp_path / "b.ome.tif", (3, 8, 8), "ZYX", PhysicalSizeX=1, PhysicalSizeY=1, PhysicalSizeZ=5
    )
    # A single slice has no z-step to compare, whatever its metadata says.
    write_ome(tmp_path / "c.ome.tif", PhysicalSizeX=1, PhysicalSizeY=1, PhysicalSizeZ=9)
    report = inspect_images(tmp_path, ["a.ome.tif", "b.ome.tif", "c.ome.tif"])
    found = issue(report.issues, "different_pixel_sizes")
    assert "z-step: 2 micrometres (1 image), 5 micrometres (1 image)" in found.message
    assert "pixel size (x by y)" not in found.message
    assert found.paths == ("b.ome.tif",)


def test_pixel_size_missing_for_some_images(tmp_path):
    write_ome(tmp_path / "a.ome.tif", PhysicalSizeX=0.5, PhysicalSizeY=0.5)
    write_tiff(tmp_path / "b.tif", (8, 8), axes="YX")
    report = inspect_images(tmp_path, ["a.ome.tif", "b.tif"])
    found = issue(report.issues, "missing_pixel_size")
    assert found.severity is Severity.WARNING and found.paths == ("b.tif",)
    assert "different_pixel_sizes" not in codes(report.issues)
    # No sizes at all: nothing to compare, nothing reported.
    write_tiff(tmp_path / "c.tif", (8, 8), axes="YX")
    assert inspect_images(tmp_path, ["b.tif", "c.tif"]).issues == ()


def test_imagej_pixel_sizes_are_compared(tmp_path):
    for name, size in (("a", 0.25), ("b", 0.5)):
        tifffile.imwrite(
            tmp_path / f"{name}.tif",
            np.zeros((8, 8), np.uint16),
            imagej=True,
            resolution=(1 / size, 1 / size),
            metadata={"unit": "um"},
        )
    found = issue(inspect_images(tmp_path, ["a.tif", "b.tif"]).issues, "different_pixel_sizes")
    assert "0.25 x 0.25 micrometres" in found.message


def test_different_plane_counts(tmp_path):
    write_tiff(tmp_path / "a.tif", (2, 3, 8, 8), axes="TCYX")
    write_tiff(tmp_path / "b.tif", (2, 3, 8, 8), axes="TCYX")
    write_tiff(tmp_path / "c.tif", (5, 2, 8, 8), axes="TCYX")
    report = inspect_images(tmp_path, ["a.tif", "b.tif", "c.tif"])
    assert "different_sizes" not in codes(report.issues)
    found = issue(report.issues, "different_plane_counts")
    assert found.severity is Severity.WARNING and found.paths == ("c.tif",)
    assert "channels: 3 (2 images), 2 (1 image)" in found.message
    assert "time points: 2 (2 images), 5 (1 image)" in found.message
    assert found.message.index("channels") < found.message.index("time points")


def test_plane_counts_compare_rgb_samples_and_unlabelled_dimensions():
    files = [
        meta("rgb.tif", (8, 8, 3), axes="YXS"),
        meta("two.tif", (2, 8, 8), axes="CYX"),
        meta("q3.tif", (3, 8, 8), axes="QYX"),
    ]
    found = issue(check_metadata(files), "different_plane_counts")
    assert "channels: 1 (1 image), 2 (1 image), 3 (1 image)" in found.message
    assert "planes along Q: 1 (2 images), 3 (1 image)" in found.message
    same = [meta("a.tif", (3, 8, 8), axes="ZYX"), meta("b.tif", (3, 8, 8), axes="ZYX")]
    assert check_metadata(same) == []
