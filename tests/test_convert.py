"""Named-axis OME-TIFF copies (design v0.4 §5, PP-11): the copies themselves.

Every converted plane equals the original read under the confirmed roles, bit for bit,
with its pixel type: plain stacks, ImageJ hyperstacks, Y/X-swapped headers, several
channels, Z and T, X before Y, planes read in blocks. Pixel sizes follow the roles and a
relabelled axis gets an unknown scale (a Y/X swap never exchanges sizes). The originals are
only read; the copy tree is outside the experiment.
"""

import hashlib
import os
import stat

import numpy as np
import pytest
import tifffile

import morphobricks_preprocessing.ome_tiff_copy as copy_module
import morphobricks_preprocessing.planes as planes_module
from morphobricks_preprocessing import (
    AxisMapping,
    convert_experiment,
    inspect_image,
    iter_planes,
    named_axes,
    plane_layout,
    read_conversion_record,
)
from morphobricks_preprocessing.copy_axes import copy_layout

ZYX_SWAPPED = AxisMapping("ZYX", ("Z", "X", "Y"))


def ome(path, data, axes, **metadata):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        path, data, ome=True, photometric="minisblack", metadata={"axes": axes, **metadata}
    )
    return path


def imagej(path, data, axes, **kwargs):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, imagej=True, metadata={"axes": axes}, **kwargs)
    return path


def plain(path, data, **kwargs):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, photometric="minisblack", metadata=None, **kwargs)
    return path


def pixels(shape, dtype=np.uint16, seed=0):
    rng = np.random.default_rng(seed)
    if np.dtype(dtype).kind == "f":
        values = rng.normal(100, 50, shape).astype(dtype)
        values.flat[::7] = np.nan
        values.flat[3] = np.inf
        return values
    info = np.iinfo(dtype)
    return rng.integers(info.min, info.max, shape, dtype=dtype, endpoint=True)


def originals(path, mapping, **kwargs):
    """Every plane of ``path`` read under ``mapping``, by its (z, c, t) coordinates."""
    planes = {}
    for plane in iter_planes(path, mapping, **kwargs):
        whole = np.zeros(plane.shape, plane.dtype)
        for block in plane.blocks():
            h, w = block.pixels.shape
            whole[block.y : block.y + h, block.x : block.x + w] = block.pixels
        planes[(plane.z, plane.c, plane.t)] = whole
    return planes


def assert_bitwise_equal(source, mapping, copy, **kwargs):
    """Each plane of ``copy`` (named axes) has the bytes of the source plane read under roles."""
    meta = inspect_image(copy)
    assert "?" not in named_axes(meta), "the copy names every axis"
    expected = originals(source, mapping, **kwargs)
    found = originals(copy, AxisMapping(meta.axes, tuple(meta.axes)))
    assert found.keys() == expected.keys()
    for key, plane in expected.items():
        assert found[key].dtype == plane.dtype
        assert found[key].shape == plane.shape
        assert found[key].tobytes() == plane.tobytes(), key


def fingerprints(folder):
    """Every file under ``folder`` with its bytes' SHA-256 and modification time."""
    return {
        path.relative_to(folder).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def convert(tmp_path, mapping, **kwargs):
    return convert_experiment(tmp_path / "exp", mapping, tmp_path / "copies", **kwargs)


# Pixel exactness ----------------------------------------------------------------------


def test_a_plain_stack_is_written_with_the_confirmed_roles(tmp_path):
    source = plain(tmp_path / "exp" / "a" / "img.tif", pixels((4, 9, 13)))
    mapping = AxisMapping("IYX", ("Z", "Y", "X"))
    result = convert(tmp_path, mapping)
    assert result.written and result.converted == ("a/img.tif",)
    copy = tmp_path / "copies" / "a" / "img.tif"
    with tifffile.TiffFile(copy) as tif:
        assert tif.is_ome and tif.series[0].axes == "ZYX" and tif.series[0].shape == (4, 9, 13)
    assert_bitwise_equal(source, mapping, copy)
    assert result.suggested_axes == "ZYX"


def test_a_y_x_swap_is_written_rows_then_columns_and_never_exchanges_sizes(tmp_path):
    data = pixels((3, 10, 14))
    source = ome(
        tmp_path / "exp" / "img.tif", data, "ZYX", PhysicalSizeX=0.3, PhysicalSizeY=0.4,
        PhysicalSizeZ=1.5,
    )  # fmt: skip
    result = convert(tmp_path, ZYX_SWAPPED)
    copy = tmp_path / "copies" / "img.tif"
    assert result.written and result.converted == ("img.tif",)
    with tifffile.TiffFile(copy) as tif:
        assert tif.series[0].axes == "ZYX" and tif.series[0].shape == (3, 14, 10)
        np.testing.assert_array_equal(tif.series[0].asarray(), data.transpose(0, 2, 1))
    assert_bitwise_equal(source, ZYX_SWAPPED, copy)
    sizes = inspect_image(copy).physical_sizes
    # The header's X size describes what the user calls Y: it is not taken for either.
    assert (sizes.x, sizes.y, sizes.z) == (None, None, 1.5)
    entry = read_conversion_record(tmp_path / "copies")[0].entry("img.tif")
    assert entry.reason == "axes_yx_swapped" and entry.copy_axes == "ZYX"
    assert entry.roles == ("Z", "X", "Y") and entry.source_axes == "ZYX"
    assert set(entry.unknown_sizes) == {"X", "Y"}
    assert "relabelled" in entry.unknown_sizes["X"]


def test_an_imagej_slice_stack_confirmed_as_time_drops_its_z_step(tmp_path):
    data = pixels((5, 8, 6), np.uint8)
    source = tmp_path / "exp" / "img.tif"
    source.parent.mkdir(parents=True)
    tifffile.imwrite(
        source, data, imagej=True, resolution=(2.0, 4.0),
        metadata={"axes": "ZYX", "spacing": 2.5, "unit": "um"},
    )  # fmt: skip
    mapping = AxisMapping("ZYX", ("T", "Y", "X"))
    result = convert(tmp_path, mapping)
    copy = tmp_path / "copies" / "img.tif"
    assert result.written and result.converted == ("img.tif",)
    assert inspect_image(copy).axes == "TYX"
    assert_bitwise_equal(source, mapping, copy)
    sizes = inspect_image(copy).physical_sizes
    assert (sizes.x, sizes.y, sizes.z, sizes.t) == (0.5, 0.25, None, None)
    assert sizes.unit("X") == "micrometer"
    entry = read_conversion_record(tmp_path / "copies")[0].entry("img.tif")
    assert entry.reason == "axes_contradict_header" and set(entry.unknown_sizes) == {"Z"}


def test_an_imagej_hyperstack_with_channels_depth_and_time(tmp_path):
    data = pixels((2, 3, 2, 7, 5))  # TZCYX
    source = imagej(tmp_path / "exp" / "img.tif", data, "TZCYX")
    # Confirmed with the header's Y and X the other way round.
    mapping = AxisMapping("TZCYX", ("T", "Z", "C", "X", "Y"))
    result = convert(tmp_path, mapping)
    copy = tmp_path / "copies" / "img.tif"
    assert result.written and inspect_image(copy).axes == "TZCYX"
    assert inspect_image(copy).shape == (2, 3, 2, 5, 7)
    assert_bitwise_equal(source, mapping, copy)


def test_channel_names_are_kept_when_the_channel_axis_keeps_its_role(tmp_path):
    source = ome(
        tmp_path / "exp" / "img.tif", pixels((2, 3, 6, 9)), "CZYX",
        Channel={"Name": ["DAPI", "actin"]},
    )  # fmt: skip
    mapping = AxisMapping("CZYX", ("C", "Z", "X", "Y"))
    result = convert(tmp_path, mapping)
    copy = tmp_path / "copies" / "img.tif"
    assert result.written
    assert inspect_image(copy).channel_names == ("DAPI", "actin")
    assert_bitwise_equal(source, mapping, copy)


def test_tifffiles_cyx_default_confirmed_as_depth_loses_the_channel_names(tmp_path):
    source = ome(
        tmp_path / "exp" / "img.tif", pixels((3, 6, 8)), "CYX", Channel={"Name": list("abc")}
    )
    mapping = AxisMapping("CYX", ("Z", "Y", "X"))
    result = convert(tmp_path, mapping)
    copy = tmp_path / "copies" / "img.tif"
    assert result.written and inspect_image(copy).axes == "ZYX"
    assert inspect_image(copy).channel_names == ()
    assert_bitwise_equal(source, mapping, copy)


def test_a_plain_stack_confirmed_x_before_y_is_written_y_first(tmp_path):
    data = pixels((3, 6, 11))
    plain(tmp_path / "exp" / "img.tif", data)
    mapping = AxisMapping("IYX", ("T", "X", "Y"))
    result = convert(tmp_path, mapping)
    copy = tmp_path / "copies" / "img.tif"
    assert result.written
    assert inspect_image(copy).axes == "TYX" and inspect_image(copy).shape == (3, 11, 6)
    np.testing.assert_array_equal(tifffile.imread(copy), data.transpose(0, 2, 1))
    entry = read_conversion_record(tmp_path / "copies")[0].entry("img.tif")
    assert entry.reason == "axes_x_before_y"


@pytest.mark.parametrize("dtype", [np.int8, np.uint16, np.int32, np.float32, ">f8"])
def test_every_pixel_type_is_kept_value_for_value(tmp_path, dtype):
    data = pixels((2, 5, 7), np.dtype(dtype).newbyteorder("=")).astype(dtype)
    source = plain(tmp_path / "exp" / "img.tif", data)
    mapping = AxisMapping("IYX", ("Z", "Y", "X"))
    assert convert(tmp_path, mapping).written
    copy = tmp_path / "copies" / "img.tif"
    found = tifffile.imread(copy)
    assert found.dtype == np.dtype(dtype).newbyteorder("=")
    # Bit for bit: NaN and infinities included, no rescaling, no rounding.
    assert found.tobytes() == np.ascontiguousarray(data, found.dtype).tobytes()
    assert_bitwise_equal(source, mapping, copy)


def test_planes_over_the_memory_limit_are_read_and_written_in_blocks(tmp_path, monkeypatch):
    data = pixels((2, 64, 48))
    source = ome(tmp_path / "exp" / "img.tif", data, "ZYX")
    tifffile.imwrite(
        source, data, ome=True, photometric="minisblack", rowsperstrip=4,
        metadata={"axes": "ZYX"},
    )  # fmt: skip
    limit = plane_layout(source, ZYX_SWAPPED, memory_limit=1).block_memory_estimate
    assert limit < plane_layout(source, ZYX_SWAPPED).memory_estimate
    reads = []
    original = planes_module._read_segment
    monkeypatch.setattr(
        planes_module, "_read_segment", lambda *a: reads.append(a[1]) or original(*a)
    )
    for cls in (tifffile.TiffFile, tifffile.TiffPage, tifffile.TiffFrame, tifffile.TiffPageSeries):
        monkeypatch.setattr(cls, "asarray", lambda *a, **k: pytest.fail("decoded whole"))
    result = convert(tmp_path, ZYX_SWAPPED, memory_limit=limit)
    assert result.written and len(reads) >= 2 * 64 // 4
    monkeypatch.undo()
    np.testing.assert_array_equal(
        tifffile.imread(tmp_path / "copies" / "img.tif"), data.transpose(0, 2, 1)
    )


@pytest.mark.parametrize("dtype", [np.int64, np.uint64])
def test_64_bit_integers_which_ome_tiff_cannot_hold_are_refused(tmp_path, dtype):
    plain(tmp_path / "exp" / "img.tif", pixels((2, 4, 5), dtype))
    result = convert(tmp_path, AxisMapping("IYX", ("Z", "Y", "X")))
    assert not result.written
    assert [i.code for i in result.issues if i.is_error] == ["cannot_convert_pixel_type"]
    assert not (tmp_path / "copies").exists()


def test_a_large_copy_is_written_as_bigtiff(tmp_path, monkeypatch):
    monkeypatch.setattr(copy_module, "BIGTIFF_FROM", 0)
    source = plain(tmp_path / "exp" / "img.tif", pixels((2, 5, 6)))
    mapping = AxisMapping("IYX", ("Z", "Y", "X"))
    assert convert(tmp_path, mapping).written
    copy = tmp_path / "copies" / "img.tif"
    with tifffile.TiffFile(copy) as tif:
        assert tif.is_bigtiff and tif.is_ome
    assert_bitwise_equal(source, mapping, copy)


# What is copied, and the originals ------------------------------------------------------


def test_images_with_named_agreeing_axes_and_excluded_images_are_copied_byte_for_byte(tmp_path):
    # A 2-D OME image beside plain stacks: its Z is a confirmed single plane.
    good = ome(tmp_path / "exp" / "a" / "good.tif", pixels((5, 6)), "YX")
    plain(tmp_path / "exp" / "a" / "stack.tif", pixels((3, 5, 6)))
    damaged = tmp_path / "exp" / "a" / "damaged.tif"
    damaged.write_bytes(b"not a tiff at all")
    mapping = AxisMapping("IYX", ("Z", "Y", "X"))
    before = fingerprints(tmp_path / "exp")
    result = convert(tmp_path, mapping, exclude=["a/damaged.tif"], missing_as_singleton=True)
    assert result.written, result.issues
    assert result.converted == ("a/stack.tif",)
    assert result.copied == ("a/damaged.tif", "a/good.tif")
    copies = tmp_path / "copies" / "a"
    assert (copies / "good.tif").read_bytes() == good.read_bytes()
    assert (copies / "damaged.tif").read_bytes() == damaged.read_bytes()
    record = read_conversion_record(tmp_path / "copies")[0]
    assert record.mapping == mapping and record.missing_as_singleton
    assert record.source_root == (tmp_path / "exp").resolve().as_posix()
    assert record.entry("a/damaged.tif").reason == "excluded"
    assert record.entry("a/good.tif").reason is None
    assert fingerprints(tmp_path / "exp") == before, "the originals are never changed"


def test_new_copies_get_the_mode_a_plain_open_gives(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX file modes")
    plain(tmp_path / "exp" / "img.tif", pixels((2, 4, 4)))
    old = os.umask(0o027)
    try:
        assert convert(tmp_path, AxisMapping("IYX", ("Z", "Y", "X"))).written
    finally:
        os.umask(old)
    for name in ("img.tif", ".morphobricks-conversion.json"):
        mode = stat.S_IMODE((tmp_path / "copies" / name).stat().st_mode)
        assert mode == 0o640, (name, oct(mode))


def test_the_copy_tree_must_be_outside_the_experiment_and_not_hold_it(tmp_path):
    plain(tmp_path / "exp" / "img.tif", pixels((2, 4, 4)))
    mapping = AxisMapping("IYX", ("Z", "Y", "X"))
    for target in (tmp_path / "exp", tmp_path / "exp" / "copies", tmp_path):
        result = convert_experiment(tmp_path / "exp", mapping, target)
        assert not result.written
        assert [i.code for i in result.issues] == ["copy_folder_overlaps_experiment"]
    assert not (tmp_path / "exp" / "copies").exists()


def test_another_error_stops_the_conversion_before_anything_is_written(tmp_path):
    plain(tmp_path / "exp" / "img.tif", pixels((2, 4, 4)))
    result = convert(tmp_path, AxisMapping("IYX", (None, "Y", "X")))
    assert not result.written and "unassigned_axis" in [i.code for i in result.issues]
    assert not (tmp_path / "copies").exists()


def test_copy_layout_says_why_each_size_is_not_written(tmp_path):
    source = ome(
        tmp_path / "img.tif", pixels((2, 4, 5)), "CYX", PhysicalSizeX=0.2, PhysicalSizeY=0.2,
        PhysicalSizeZ=3.0,
    )  # fmt: skip
    layout = copy_layout(inspect_image(source), AxisMapping("CYX", ("Z", "Y", "X")))
    assert layout.axes == "ZYX" and layout.plane_count == 2
    assert [s.value for s in layout.sizes] == [0.2, 0.2, None, None]
    assert "relabelled" in layout.unknown["Z"] and "header names this axis C" in layout.unknown["Z"]
    assert layout.ome_metadata()["PhysicalSizeXUnit"] == "µm"
    assert layout.plane_index({"z": 1, "c": None, "t": None}) == 1


def test_an_rgb_image_with_swapped_y_and_x_is_written_as_channel_pages(tmp_path):
    data = pixels((6, 9, 3), np.uint8)
    source = tmp_path / "exp" / "img.tif"
    source.parent.mkdir(parents=True)
    tifffile.imwrite(source, data, photometric="rgb", metadata={"axes": "YXS"})
    mapping = AxisMapping("YXS", ("X", "Y", "C"))
    result = convert(tmp_path, mapping)
    copy = tmp_path / "copies" / "img.tif"
    assert result.written and inspect_image(copy).axes == "CYX"
    np.testing.assert_array_equal(tifffile.imread(copy), data.transpose(2, 1, 0))
    assert_bitwise_equal(source, mapping, copy)


def test_planes_that_cannot_be_read_within_the_memory_limit_stop_it_before_writing(tmp_path):
    # Review of PP-11 (issue #76's criterion): the memory budget fails fast. It used to be
    # found while writing, after the copies before it were written.
    ome(tmp_path / "exp" / "a.tif", pixels((2, 8, 9)), "ZYX")
    compressed = pixels((2, 64, 48))
    tifffile.imwrite(
        tmp_path / "exp" / "b.tif", compressed, ome=True, photometric="minisblack",
        compression="zlib", rowsperstrip=64, metadata={"axes": "ZYX"},
    )  # fmt: skip
    limit = plane_layout(tmp_path / "exp" / "a.tif", ZYX_SWAPPED).memory_estimate
    result = convert(tmp_path, ZYX_SWAPPED, memory_limit=limit)
    assert not result.written
    assert [i.code for i in result.issues if i.is_error] == ["plane_over_memory_limit"]
    assert result.issues[0].paths == ("b.tif",)
    assert not (tmp_path / "copies").exists(), "nothing written, not even the folder"


@pytest.mark.parametrize(
    ("keyword", "value"),
    [("memory_limit", 0), ("memory_limit", 2.5), ("memory_limit", True), ("memory_limit", "1GiB"),
     ("missing_as_singleton", 1), ("missing_as_singleton", "yes")],
)  # fmt: skip
def test_settings_that_are_not_allowed_are_refused_before_anything_is_read(
    tmp_path, keyword, value
):
    # Review of PP-11 (issue #80's criterion): strict settings, never coerced.
    plain(tmp_path / "exp" / "img.tif", pixels((2, 4, 5)))
    with pytest.raises(ValueError, match=keyword):
        convert(tmp_path, AxisMapping("IYX", ("Z", "Y", "X")), **{keyword: value})
    assert not (tmp_path / "copies").exists()
