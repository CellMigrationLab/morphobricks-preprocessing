"""The shared header-axes rules (MorphoBricks D86, API A4, decision D-d)."""

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    PLACEHOLDER_AXES,
    check_declared_axes,
    header_axes,
    inspect_image,
    read_mask_file,
)
from tiff_tree import write_data


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
    # but never X or Y, and the header's Y and X stay where they are
    assert check_declared_axes(meta, "XZY").code == "declared_axes_mismatch"
    assert check_declared_axes(meta, "YXZ").code == "declared_axes_mismatch"


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
