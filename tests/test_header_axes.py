"""The shared header-axes rules (MorphoBricks D86, API A4, decision D-d)."""

import dataclasses
import itertools

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    OPEN_AXIS,
    PLACEHOLDER_AXES,
    TiffMetadata,
    check_declared_axes,
    declared_axes_transposed,
    header_axes,
    inspect_image,
    named_axes,
    read_mask_file,
    series_axes_from_metadata,
)
from tiff_tree import write_data

# check_declared_axes and declared_axes_transposed are deprecated in v0.4.0 (removed in
# v0.5.0); their rules stay tested until then, and the warning has its own test below.
pytestmark = pytest.mark.filterwarnings(
    "ignore:morphobricks_preprocessing.(check_declared_axes|declared_axes_transposed) is "
    "deprecated:DeprecationWarning"
)


@pytest.mark.filterwarnings("error")
def test_declared_axes_checks_are_deprecated(tmp_path):
    meta = inspect_image(write_data(tmp_path / "a.tif", np.zeros((2, 4, 5), np.uint8), "CYX"))
    with pytest.warns(DeprecationWarning, match="check_declared_axes is deprecated.*v0.5.0"):
        assert check_declared_axes(meta, "CYX") is None
    with pytest.warns(DeprecationWarning, match="declared_axes_transposed is deprecated"):
        assert declared_axes_transposed(meta, "CXY")


def imagej(path, data, axes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, imagej=True, metadata={"axes": axes})
    return path


def plain(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, photometric="minisblack", metadata=None)
    return path


def labels(shape, start=1):
    return (np.arange(np.prod(shape)).reshape(shape) % 7 + start).astype(np.uint16)


# header_axes --------------------------------------------------------------------------------


def test_placeholder_axes_are_tifffiles_unknown_letters():
    assert frozenset("IQ") == PLACEHOLDER_AXES


@pytest.mark.parametrize(
    ("write", "expected"),
    [
        # Decision D-d: tifffile "shaped" metadata axes count (MorphoRelate refused them).
        (lambda p: write_data(p, np.zeros((3, 8, 8), np.uint8), "CYX"), "CYX"),
        (lambda p: imagej(p, np.zeros((3, 8, 8), np.uint8), "ZYX"), "ZYX"),
        (lambda p: imagej(p, np.zeros((2, 3, 8, 8), np.uint8), "TZYX"), "TZYX"),
        (
            lambda p: tifffile.imwrite(
                p, np.zeros((2, 8, 8), np.uint8), ome=True, metadata={"axes": "TYX"}
            ),
            "TYX",
        ),
        (lambda p: plain(p, np.zeros((8, 8), np.uint8)), "YX"),
        (
            lambda p: tifffile.imwrite(p, np.zeros((8, 8, 3), np.uint8), photometric="rgb"),
            "YXS",
        ),
        # The file does not say what the third dimension is: never guessed.
        (lambda p: plain(p, np.zeros((3, 8, 8), np.uint8)), None),
        (lambda p: write_data(p, np.zeros((3, 8, 8), np.uint8)), None),
    ],
)
def test_header_axes(tmp_path, write, expected):
    path = tmp_path / "a.tif"
    write(path)
    assert header_axes(inspect_image(path, "a.tif")) == expected


@pytest.mark.parametrize(
    ("write", "axes", "named"),
    [
        (lambda p: plain(p, np.zeros((3, 8, 8), np.uint8)), "IYX", "???"),
        (lambda p: write_data(p, np.zeros((3, 8, 8), np.uint8)), "QYX", "???"),
        (lambda p: write_data(p, np.zeros((2, 3, 8, 8), np.uint8)), "QQYX", "????"),
        (lambda p: write_data(p, np.zeros((3, 8, 8), np.uint8), "QYX"), "QYX", "?YX"),
        (lambda p: write_data(p, np.zeros((3, 8, 8), np.uint8), "CYX"), "CYX", "CYX"),
        (lambda p: imagej(p, np.zeros((3, 8, 8), np.uint8), "ZYX"), "ZYX", "ZYX"),
        (lambda p: plain(p, np.zeros((8, 8), np.uint8)), "YX", "YX"),
        (lambda p: write_data(p, np.zeros((8, 8), np.uint8)), "YX", "YX"),
        (
            lambda p: tifffile.imwrite(
                p, np.zeros((2, 8, 8, 3), np.uint8), photometric="rgb", metadata=None
            ),
            "IYXS",
            "???S",
        ),
    ],
)
def test_named_axes(tmp_path, write, axes, named):
    path = tmp_path / "a.tif"
    write(path)
    meta = inspect_image(path, "a.tif")
    assert (meta.axes, named_axes(meta)) == (axes, named)
    assert OPEN_AXIS == "?"


def test_series_axes_from_metadata_reads_each_series(tmp_path):
    # A brick building a TiffMetadata for another series passes the same flag.
    path = tmp_path / "two.tif"
    tifffile.imwrite(path, np.zeros((3, 8, 8), np.uint8), photometric="minisblack", metadata={})
    tifffile.imwrite(
        path,
        np.zeros((2, 8, 8), np.uint8),
        photometric="minisblack",
        metadata={"axes": "CYX"},
        append=True,
    )
    with tifffile.TiffFile(path) as tif:
        assert [series_axes_from_metadata(series) for series in tif.series] == [False, True]


def test_a_header_built_by_hand_reads_strictly():
    # Without the flag, a placeholder stack's Y and X stay binding (refuse rather than guess).
    meta = TiffMetadata("a.tif", True, (3, 8, 8), np.dtype("uint8"), "QYX", 1, 3)
    assert named_axes(meta) == "?YX"
    assert check_declared_axes(meta, "YXC").code == "declared_axes_mismatch"
    plain_stack = dataclasses.replace(meta, axes_from_metadata=False)
    assert check_declared_axes(plain_stack, "YXC") is None


def test_header_axes_of_an_unreadable_file(tmp_path):
    path = tmp_path / "a.tif"
    path.write_bytes(b"not a tiff")
    assert header_axes(inspect_image(path, "a.tif")) is None


def test_header_axes_of_an_ome_zarr_image(tmp_path):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    path = write_ngff(tmp_path / "a.ome.zarr", np.zeros((2, 3, 8, 8), np.uint8), "tcyx")
    assert header_axes(inspect_image(path, "a.ome.zarr")) == "TCYX"


# check_declared_axes: the report's probes ---------------------------------------------------


def test_a_depth_stack_declared_as_channels_is_refused(tmp_path):
    # MF-1: an ImageJ ZYX image declared image_axes="CYX" had its Z slices read as channels.
    meta = inspect_image(imagej(tmp_path / "a.tif", np.zeros((3, 8, 8), np.uint8), "ZYX"), "a.tif")
    issue = check_declared_axes(meta, "CYX")
    assert issue.code == "declared_axes_mismatch" and issue.is_error
    assert issue.paths == ("a.tif",)
    assert "'CYX'" in issue.message and "'ZYX'" in issue.message
    assert check_declared_axes(meta, "ZYX") is None
    assert check_declared_axes(meta, "zyx") is None


@pytest.mark.parametrize(
    ("header", "declared"),
    [
        ("ZYX", "TYX"),  # CP-4: a ZYX mask declared TYX (Z slices used as time points)
        ("ZYX", "CYX"),  # MR-1: a ZYX mask written into the project as CYX
        ("TYX", "ZYX"),  # MF-1: a TYX mask declared ZYX
    ],
)
def test_a_mask_declared_with_other_axes_is_refused(tmp_path, header, declared):
    meta = inspect_image(imagej(tmp_path / "m.tif", labels((3, 8, 8)), header), "m.tif")
    assert check_declared_axes(meta, declared).code == "declared_axes_mismatch"
    assert check_declared_axes(meta, header) is None


def test_shaped_axes_are_trusted(tmp_path):
    # MR-4 and decision D-d: a shaped CYX TIFF is CYX, and only CYX.
    meta = inspect_image(write_data(tmp_path / "a.tif", np.zeros((3, 8, 8), np.uint8), "CYX"))
    assert check_declared_axes(meta, "CYX") is None
    assert check_declared_axes(meta, "ZYX").code == "declared_axes_mismatch"


def test_a_placeholder_axis_may_be_declared_depth_time_or_channel(tmp_path):
    meta = inspect_image(plain(tmp_path / "a.tif", np.zeros((3, 8, 8), np.uint8)), "a.tif")
    assert meta.axes == "IYX"
    for declared in ("ZYX", "TYX", "CYX"):
        assert check_declared_axes(meta, declared) is None


def shaped_without_axes(path, data):
    return write_data(path, data)


@pytest.mark.parametrize("write", [plain, shaped_without_axes])
@pytest.mark.parametrize("declared", ["YXC", "CYX", "ZYX", "TYX", "XYZ", "yxc"])
def test_a_plain_stack_leaves_every_dimension_open(tmp_path, write, declared):
    # The Y and X tifffile reports for a plain stack are only where each page's rows and
    # columns are stored: a (Y, X, C) array written without metadata reads as QYX or IYX.
    meta = inspect_image(write(tmp_path / "a.tif", np.zeros((8, 8, 3), np.uint8)), "a.tif")
    assert meta.axes in ("QYX", "IYX")
    assert check_declared_axes(meta, declared) is None


def test_a_plain_stack_of_two_unnamed_dimensions_is_open(tmp_path):
    meta = inspect_image(write_data(tmp_path / "a.tif", np.zeros((2, 3, 8, 8), np.uint8)))
    assert meta.axes == "QQYX"
    for declared in ("ZCYX", "TZYX", "YXZC", "CYXT"):
        assert check_declared_axes(meta, declared) is None


@pytest.mark.parametrize("declared", ["YX", "ZCYX", "ZZX", "ZYQ", "YXS", "", None])
def test_a_plain_stack_still_needs_one_role_per_dimension(tmp_path, declared):
    meta = inspect_image(plain(tmp_path / "a.tif", np.zeros((3, 8, 8), np.uint8)), "a.tif")
    assert check_declared_axes(meta, declared).code == "declared_axes_mismatch"


@pytest.mark.parametrize("declared", ["CTZ", "ZCX", "YCT"])
def test_a_plain_stack_must_still_declare_y_and_x(tmp_path, declared):
    # Its Y and X are open, but the image still has rows and columns: a declaration
    # without them reads no plane at all.
    meta = inspect_image(plain(tmp_path / "a.tif", np.zeros((3, 8, 8), np.uint8)), "a.tif")
    issue = check_declared_axes(meta, declared)
    assert issue.code == "declared_axes_mismatch"
    assert "not declared" in issue.message


def test_named_axes_stay_binding_beside_a_placeholder(tmp_path):
    # Axes metadata that names Y and X but leaves one dimension open: only that one is open.
    path = write_data(tmp_path / "a.tif", np.zeros((3, 8, 8), np.uint8), "QYX")
    meta = inspect_image(path, "a.tif")
    assert check_declared_axes(meta, "CYX") is None
    assert check_declared_axes(meta, "YXC").code == "declared_axes_mismatch"


def test_rgb_samples_of_a_plain_stack_stay_binding(tmp_path):
    # S comes from the SamplesPerPixel tag, not from where the pages are stored.
    path = tmp_path / "rgb.tif"
    tifffile.imwrite(path, np.zeros((2, 8, 8, 3), np.uint8), photometric="rgb", metadata=None)
    meta = inspect_image(path, "rgb.tif")
    assert meta.axes == "IYXS"
    assert check_declared_axes(meta, "TYXC") is None
    assert check_declared_axes(meta, "TYXZ").code == "declared_axes_mismatch"


def test_an_ome_channel_stack_declared_samples_last_is_refused(tmp_path):
    path = tmp_path / "a.ome.tif"
    tifffile.imwrite(path, np.zeros((3, 8, 8), np.uint8), ome=True, metadata={"axes": "CYX"})
    meta = inspect_image(path, "a.ome.tif")
    assert meta.axes == "CYX"
    assert check_declared_axes(meta, "CYX") is None
    assert check_declared_axes(meta, "YXC").code == "declared_axes_mismatch"


def test_shaped_axes_declared_in_another_order_are_refused(tmp_path):
    # Decision D-d: tifffile "shaped" axes are header axes, so they bind like OME's.
    meta = inspect_image(write_data(tmp_path / "a.tif", np.zeros((3, 8, 8), np.uint8), "CYX"))
    assert check_declared_axes(meta, "YXC").code == "declared_axes_mismatch"


@pytest.mark.parametrize(
    "write",
    [
        lambda p: plain(p, np.zeros((3, 8, 8), np.uint8)),
        lambda p: write_data(p, np.zeros((3, 8, 8), np.uint8)),
        lambda p: write_data(p, np.zeros((3, 8, 8), np.uint8), "CYX"),
        lambda p: write_data(p, np.zeros((3, 8, 8), np.uint8), "QYX"),
        lambda p: imagej(p, np.zeros((3, 8, 8), np.uint8), "ZYX"),
        lambda p: plain(p, np.zeros((8, 8), np.uint8)),
        lambda p: write_data(p, np.zeros((8, 8), np.uint8)),
    ],
)
def test_header_axes_are_exactly_what_check_declared_axes_binds(tmp_path, write):
    path = tmp_path / "a.tif"
    write(path)
    meta = inspect_image(path, "a.tif")
    roles = "XYZCT"
    accepted = {
        "".join(order)
        for order in itertools.permutations(roles, len(meta.axes))
        if check_declared_axes(meta, "".join(order)) is None
    }
    named = header_axes(meta)
    if named is None:
        # Something is left open, so more than one declaration fits.
        assert len(accepted) > 1
    else:
        # Every axis is named, so only the header's own axes fit, or them with Y and X
        # swapped (read transposed, and reported as such).
        swapped = named.translate(str.maketrans("XY", "YX"))
        assert accepted == {named, swapped}
        assert [declared_axes_transposed(meta, d) for d in (named, swapped)] == [False, True]


def ome(path, data, axes):
    tifffile.imwrite(path, data, ome=True, metadata={"axes": axes})
    return path


@pytest.mark.parametrize(
    ("write", "header", "declared"),
    [
        (lambda p: imagej(p, np.zeros((3, 8, 6), np.uint8), "CYX"), "CYX", "CXY"),
        (lambda p: imagej(p, np.zeros((3, 8, 6), np.uint8), "ZYX"), "ZYX", "ZXY"),
        (lambda p: ome(p, np.zeros((3, 8, 6), np.uint8), "CYX"), "CYX", "cxy"),
        (lambda p: write_data(p, np.zeros((3, 8, 6), np.uint8), "CYX"), "CYX", "CXY"),
        (lambda p: plain(p, np.zeros((8, 6), np.uint8)), "YX", "XY"),
    ],
)
def test_named_y_and_x_declared_swapped_are_read_transposed(tmp_path, write, header, declared):
    # Owner decision (31 October 2026): only a swap of the named Y and X is accepted, and
    # the caller is told so it can record that the image is read transposed.
    path = tmp_path / "a.tif"
    write(path)
    meta = inspect_image(path, "a.tif")
    assert meta.axes == header
    assert check_declared_axes(meta, declared) is None
    assert declared_axes_transposed(meta, declared) is True
    assert declared_axes_transposed(meta, header) is False


@pytest.mark.parametrize(
    ("header", "declared"),
    [
        ("CYX", "YXC"),  # the named C moved: not a swap of Y and X
        ("ZYX", "CYX"),
        ("ZYX", "CXY"),  # a swap together with another change
        ("ZYX", "XZY"),
    ],
)
def test_any_other_disagreement_stays_refused(tmp_path, header, declared):
    meta = inspect_image(imagej(tmp_path / "a.tif", np.zeros((3, 8, 6), np.uint8), header))
    assert check_declared_axes(meta, declared).code == "declared_axes_mismatch"
    with pytest.raises(ValueError, match="do not fit"):
        declared_axes_transposed(meta, declared)


def test_a_plain_stack_is_never_reported_transposed(tmp_path):
    # Its Y and X are open, not named: there is nothing to swap.
    meta = inspect_image(plain(tmp_path / "a.tif", np.zeros((3, 8, 6), np.uint8)), "a.tif")
    assert declared_axes_transposed(meta, "CXY") is False
    assert declared_axes_transposed(meta, "YXC") is False


def test_rgb_samples_may_be_declared_channels(tmp_path):
    path = tmp_path / "rgb.tif"
    tifffile.imwrite(path, np.zeros((8, 8, 3), np.uint8), photometric="rgb")
    meta = inspect_image(path, "rgb.tif")
    assert check_declared_axes(meta, "YXC") is None
    assert check_declared_axes(meta, "YXZ").code == "declared_axes_mismatch"


@pytest.mark.parametrize("declared", ["YX", "ZCYX", "ZZX", "ZYQ", "", None])
def test_malformed_declarations_are_refused(tmp_path, declared):
    meta = inspect_image(imagej(tmp_path / "a.tif", np.zeros((3, 8, 8), np.uint8), "ZYX"), "a.tif")
    assert check_declared_axes(meta, declared).code == "declared_axes_mismatch"


def test_an_unreadable_image_cannot_be_checked(tmp_path):
    path = tmp_path / "a.tif"
    path.write_bytes(b"not a tiff")
    assert check_declared_axes(inspect_image(path, "a.tif"), "YX").code == "unreadable_tiff"


# read_mask_file --------------------------------------------------------------------------------


def test_read_mask_file_reads_the_plane_its_header_names(tmp_path):
    data = np.stack([labels((8, 8), start=10 * z + 1) for z in range(3)])
    path = imagej(tmp_path / "m.tif", data, "ZYX")
    np.testing.assert_array_equal(read_mask_file(path, z=2), data[2])
    # The same file declared as time points is not readable as one: Z is not T.
    with pytest.raises(ValueError, match="no plane T=1"):
        read_mask_file(path, z=0, t=1)
    with pytest.raises(ValueError, match="3 planes along Z; say which one"):
        read_mask_file(path)


def test_read_mask_file_takes_a_relative_path(tmp_path, monkeypatch):
    data = labels((8, 8))
    write_data(tmp_path / "masks" / "m.tif", data)
    monkeypatch.chdir(tmp_path)
    np.testing.assert_array_equal(read_mask_file("masks/m.tif"), data)


def test_read_mask_file_time_points(tmp_path):
    data = np.stack([labels((8, 8), start=t + 1) for t in range(2)])
    path = imagej(tmp_path / "m.tif", data, "TYX")
    np.testing.assert_array_equal(read_mask_file(path, t=1), data[1])


def test_read_mask_file_refuses_unnamed_dimensions(tmp_path):
    # A plain stack could be depth or time: never guessed (CP-4, MR-1, FF-6).
    path = plain(tmp_path / "m.tif", np.stack([labels((8, 8))] * 3))
    with pytest.raises(ValueError, match="'IYX'"):
        read_mask_file(path, z=0)


def test_read_mask_file_refuses_a_channel_axis(tmp_path):
    path = write_data(tmp_path / "m.tif", np.stack([labels((8, 8))] * 2), "CYX")
    with pytest.raises(ValueError, match="'CYX'"):
        read_mask_file(path)


def test_read_mask_file_checks_labels_and_pixel_type(tmp_path):
    big = labels((8, 8)).astype(np.uint32)
    big[0, 0] = 3_000_000_000
    with pytest.raises(ValueError, match="labels above 2147483647"):
        read_mask_file(write_data(tmp_path / "big.tif", big))
    negative = labels((8, 8)).astype(np.int16)
    negative[0, 0] = -1
    with pytest.raises(ValueError, match="negative labels"):
        read_mask_file(write_data(tmp_path / "negative.tif", negative))
    with pytest.raises(ValueError, match="float32, not integer labels.*save the mask as integers"):
        read_mask_file(write_data(tmp_path / "float.tif", np.ones((8, 8), np.float32)))


def test_read_mask_file_refuses_a_file_that_does_not_open(tmp_path):
    path = tmp_path / "m.tif"
    path.write_bytes(b"garbage")
    with pytest.raises(ValueError, match="could not be opened"):
        read_mask_file(path)


def test_read_mask_file_reads_an_ome_zarr_label_image(tmp_path):
    pytest.importorskip("zarr")
    from zarr_tree import write_labels, write_ngff

    image = write_ngff(tmp_path / "a.ome.zarr", np.zeros((2, 8, 8), np.uint8), "tyx")
    data = np.stack([labels((8, 8), start=t + 1) for t in range(2)])
    write_labels(image, {"cells": data}, "tyx")
    np.testing.assert_array_equal(read_mask_file(image / "labels" / "cells", t=1), data[1])


# Masks one pixel high or wide (the fiberfeatures report, plan §12 question 44) -----------------


ONE_PIXEL_SHAPES = [(1, 9), (9, 1), (1, 1)]
WRITERS = {
    "plain": lambda path, data: plain(path, data),
    "shaped YX": lambda path, data: write_data(path, data, "YX"),
    "imagej": lambda path, data: imagej(path, data, "YX"),
    "ome": lambda path, data: ome(path, data, "YX"),
}


@pytest.mark.parametrize("shape", ONE_PIXEL_SHAPES)
@pytest.mark.parametrize("writer", WRITERS)
def test_read_mask_file_reads_a_mask_one_pixel_high_or_wide(tmp_path, shape, writer):
    # tifffile gives these masks the axes YX; the length-1 Y (or X) used to be dropped with
    # the other length-1 axes, so the mask was refused as having the axes 'X' (or 'Y', '').
    data = labels(shape)
    path = WRITERS[writer](tmp_path / "m.tif", data)
    assert inspect_image(path, "m.tif").axes == "YX"
    pixels = read_mask_file(path)
    assert pixels.shape == shape
    np.testing.assert_array_equal(pixels, data)


@pytest.mark.parametrize(
    ("shape", "axis", "writer"),
    [
        ((3, 1, 9), "Z", imagej),
        ((3, 1, 9), "Z", ome),
        # tifffile's ImageJ writer cannot store a trailing length-1 X (it writes one page),
        # so these are OME-TIFF only.
        ((3, 9, 1), "T", ome),
        ((3, 1, 1), "Z", ome),
    ],
)
def test_read_mask_file_reads_one_pixel_planes_of_a_named_stack(tmp_path, shape, axis, writer):
    data = np.stack([labels(shape[1:], start=10 * i + 1) for i in range(shape[0])])
    path = writer(tmp_path / "m.tif", data, f"{axis}YX")
    np.testing.assert_array_equal(read_mask_file(path, **{axis.lower(): 2}), data[2])


def test_read_mask_file_reads_one_pixel_planes_of_an_ome_zarr_label_image(tmp_path):
    pytest.importorskip("zarr")
    from zarr_tree import write_labels, write_ngff

    image = write_ngff(tmp_path / "a.ome.zarr", np.zeros((2, 1, 9), np.uint8), "zyx")
    data = np.stack([labels((1, 9), start=z + 1) for z in range(2)])
    write_labels(image, {"cells": data}, "zyx")
    np.testing.assert_array_equal(read_mask_file(image / "labels" / "cells", z=1), data[1])


def test_an_unnamed_stack_of_one_pixel_planes_is_refused_by_its_real_axes(tmp_path):
    # Still never guessed, and the message names the header's axes, not 'IX'.
    path = plain(tmp_path / "m.tif", np.stack([labels((1, 9))] * 3))
    with pytest.raises(
        ValueError, match=r"'IYX' \(shape \(3, 1, 9\)\): the dimension I \(length 3\)"
    ):
        read_mask_file(path, z=0)


@pytest.mark.parametrize("shape", ONE_PIXEL_SHAPES)
def test_header_axes_of_an_image_one_pixel_high_or_wide(tmp_path, shape):
    # Guard: the image side keeps Y and X of length 1 (tifffile never drops them).
    for writer in WRITERS.values():
        meta = inspect_image(writer(tmp_path / "a.tif", labels(shape)), "a.tif")
        assert (meta.axes, meta.shape) == ("YX", shape)
        assert named_axes(meta) == header_axes(meta) == "YX"
        assert check_declared_axes(meta, "YX") is None
        assert check_declared_axes(meta, "ZX").code == "declared_axes_mismatch"


def test_read_mask_file_reads_a_1d_array_saved_as_one_row(tmp_path):
    # tifffile's default metadata saves a 1D array as {"shape": [9]}, which it reads back with
    # the axes 'X' and no Y; the file is one TIFF page of 1 row and 9 columns, so the mask
    # is that page: YX, (1, 9).
    data = labels((9,))
    path = tmp_path / "m.tif"
    tifffile.imwrite(path, data)
    with tifffile.TiffFile(path) as tif:
        assert tif.series[0].axes == "X"
    np.testing.assert_array_equal(read_mask_file(path), data.reshape(1, 9))
