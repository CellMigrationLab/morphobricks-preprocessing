"""Regressions of the preprocessing audit of 3 November 2026: I2, I3, I5, m4, m5 and m6.

Each reproduces the audit's finding on the code before the fix (they changed what
``RunInputs`` gives the bricks).
"""

import struct

import numpy as np
import pytest
import tifffile

from described_tree import check_masks, run_described
from morphobricks_preprocessing import (
    AxisMapping,
    inspect_images,
    prefill_mapping,
    read_physical_sizes,
)
from morphobricks_preprocessing.length_units import to_micrometers
from morphobricks_preprocessing.physical_sizes import same_pixel_size
from morphobricks_preprocessing.planes import read_plane
from morphobricks_preprocessing.recorded_sizes import header_sizes
from morphobricks_preprocessing.run_inputs import RunInputs
from tiff_tree import write_data, write_plain
from zarr_tree import write_ngff

# The audit's sizes at the 1 GiB default, scaled down 100 times (plane and limit).
LIMIT = 2**30 // 100


def write_ome(path, shape=(8, 8), **sizes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        path, np.zeros(shape, np.uint16), ome=True, photometric="minisblack", metadata=sizes
    )
    return path


def codes(issues):
    return [issue.code for issue in issues]


def issue(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


# I2: reading a plane counts no histogram memory -------------------------------------------------


@pytest.mark.parametrize(("side", "dtype"), [(500, "float32"), (300, "uint32")])
def test_reading_a_plane_counts_no_histogram_memory(tmp_path, side, dtype):
    # 5000² float32 needed "2.0 GiB" and 3000² uint32 1.1 GiB at the 1 GiB limit before.
    data = np.arange(side * side, dtype=dtype).reshape(side, side)
    path = write_data(tmp_path / "a.tif", data, "YX")
    plane = read_plane(path, prefill_mapping("YX"), memory_limit=LIMIT)
    assert not plane.in_blocks
    np.testing.assert_array_equal(plane.pixels, data)


def test_run_inputs_reads_a_wide_integer_plane_within_its_limit(tmp_path):
    data = np.arange(300 * 300, dtype=np.uint32).reshape(300, 300)
    write_data(tmp_path / "exp/ctrl/a.tif", data, "YX")
    result = run_described(tmp_path / "exp", prefill_mapping("YX"))
    inputs = RunInputs.open(result.output_folder)
    np.testing.assert_array_equal(inputs.read_plane("ctrl/a.tif", memory_limit=LIMIT), data[None])


# I3: a header size is recorded only with a supported unit ---------------------------------------


@pytest.mark.parametrize("unit", ["pixel", "inch"])
def test_a_header_size_in_an_unsupported_unit_is_not_recorded(tmp_path, unit):
    path = write_ome(
        tmp_path / "exp/ctrl/a.ome.tif",
        axes="YX",
        PhysicalSizeX=1.0,
        PhysicalSizeXUnit=unit,
        PhysicalSizeY=1.0,
        PhysicalSizeYUnit=unit,
    )
    recorded = {size.role: size for size in header_sizes(read_physical_sizes(path))}
    assert (recorded["X"].value, recorded["X"].unit, recorded["X"].source) == (None, None, None)
    result = run_described(tmp_path / "exp", prefill_mapping("YX"))
    sizes = RunInputs.open(result.output_folder).pixel_sizes("ctrl/a.ome.tif")
    assert sizes["X"].value is None and sizes["Y"].source is None
    assert f"X and Y in {unit!r}" in issue(result.issues, "unsupported_pixel_size_unit").message


# I5: z and t are checked against the image ----------------------------------------------------


def test_a_mask_is_not_read_outside_the_image(tmp_path):
    # A shared 2-D mask until the owner decision of 5 November 2026; a mask of every time
    # point since.
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_plain(root / "ctrl/a.tif", np.zeros((3, 6, 8), np.uint8))
    write_plain(masks / "Cells/ctrl/a.tif", np.ones((3, 6, 8), np.uint16))
    result = run_described(
        root, AxisMapping("IYX", "TYX"), masks_folder=masks, mask_axes={"Cells": "TYX"}
    )
    inputs = RunInputs.open(check_masks(result.output_folder))
    assert inputs.read_mask("ctrl/a.tif", "Cells", t=2).shape == (6, 8)
    for z, t in ((None, 7), (99, 0), (0, -1), (1, 0)):
        with pytest.raises(IndexError, match="outside"):
            inputs.read_mask("ctrl/a.tif", "Cells", z=z, t=t)
    with pytest.raises(IndexError):
        inputs.read_plane("ctrl/a.tif", t=3)
    with pytest.raises(TypeError):
        inputs.read_mask("ctrl/a.tif", "Cells", t=1.0)


# m4: numpy real scalars are lengths ------------------------------------------------------------


@pytest.mark.parametrize(
    "value", [np.float32(0.5), np.float16(0.5), np.int64(2), np.uint8(2), np.longdouble(0.5)]
)
def test_numpy_real_scalars_are_lengths(value):
    converted = to_micrometers(value, "nm")
    assert type(converted) is float
    assert converted == to_micrometers(float(value), "nm") == {0.5: 0.0005, 2: 0.002}[float(value)]


@pytest.mark.parametrize(
    "value",
    [True, np.bool_(True), 1 + 0j, np.complex64(1), np.float32("nan"), np.float32("inf")]
    + [np.float32(-0.5), np.int64(0), "0.5"],
)
def test_other_values_are_still_refused(value):
    with pytest.raises(ValueError, match="not"):
        to_micrometers(value, "um")
    with pytest.raises(ValueError, match="not"):  # the shared D-a comparison as well
        same_pixel_size([value, 0.5], [0.5, 0.5])


def test_numpy_real_scalars_are_compared_as_pixel_sizes():
    assert same_pixel_size([np.float32(0.5), np.float64(0.5)], [0.5, np.int64(1) / 2])


# m5: partial and non-square calibrations are named ---------------------------------------------


def test_partial_and_non_square_pixel_sizes_are_named(tmp_path):
    root = tmp_path / "exp"
    write_ome(root / "c/square.ome.tif", axes="YX", PhysicalSizeX=0.5, PhysicalSizeY=0.5)
    write_ome(root / "c/x_only.ome.tif", axes="YX", PhysicalSizeX=0.5)
    write_ome(root / "c/wide.ome.tif", axes="YX", PhysicalSizeX=0.5, PhysicalSizeY=0.5004)
    report = inspect_images(root, ["c/square.ome.tif", "c/x_only.ome.tif", "c/wide.ome.tif"])
    partial = issue(report.issues, "partial_pixel_size")
    assert partial.paths == ("c/x_only.ome.tif",)
    assert "X 0.5 micrometres, Y unknown" in partial.message
    assert "refuse" in partial.message
    wide = issue(report.issues, "non_square_pixels")
    assert wide.paths == ("c/wide.ome.tif",) and "0.5004" in wide.message
    assert "exactly equal" in wide.message and "refuse" in wide.message


def test_square_and_uncalibrated_images_are_not_named(tmp_path):
    root = tmp_path / "exp"
    write_ome(root / "c/a.ome.tif", axes="YX", PhysicalSizeX=0.5, PhysicalSizeY=0.5)
    nm = {"PhysicalSizeX": 500, "PhysicalSizeXUnit": "nm"}  # 0.5 micrometres, exactly
    write_ome(root / "c/b.ome.tif", axes="YX", PhysicalSizeY=0.5, **nm)
    write_data(root / "c/c.tif", np.zeros((8, 8), np.uint16), "YX")
    report = inspect_images(root, ["c/a.ome.tif", "c/b.ome.tif", "c/c.tif"])
    assert not {"partial_pixel_size", "non_square_pixels"} & set(codes(report.issues))


# m6: damaged OME-XML is an invalid calibration, not an uncalibrated image ----------------------


def test_damaged_ome_xml_is_an_invalid_calibration(tmp_path):
    path = write_ome(tmp_path / "a.ome.tif", axes="YX", PhysicalSizeX=0.5, PhysicalSizeY=0.5)
    raw = path.read_bytes()
    damaged = raw.replace(b'PhysicalSizeX="0.5"', b'PhysicalSizeX="0<5"')  # same length
    assert damaged != raw
    path.write_bytes(damaged)
    sizes = read_physical_sizes(path)
    assert sizes.x is None and "OME-XML metadata cannot be read" in sizes.invalid
    report = inspect_images(tmp_path, ["a.ome.tif"])
    found = issue(report.issues, "invalid_physical_calibration")
    assert found.paths == ("a.ome.tif",) and "OME-XML" in found.message


# Review of #136: a malformed ImageJ resolution tag and an OME-Zarr scale without a unit ------


@pytest.mark.parametrize("entry", [struct.pack("<HIHH", 3, 1, 3, 0), struct.pack("<HII", 4, 1, 2)])
def test_a_malformed_imagej_resolution_tag_is_an_invalid_calibration(tmp_path, entry):
    path = tmp_path / "a.tif"
    tifffile.imwrite(
        path, np.zeros((8, 8), np.uint8), imagej=True, resolution=(2.0, 2.0),
        metadata={"unit": "um", "axes": "YX"},
    )  # fmt: skip
    with tifffile.TiffFile(path) as tif:
        offsets = [tif.pages.first.tags[code].offset for code in (282, 283)]
    raw = bytearray(path.read_bytes())
    for offset in offsets:  # the type, count and value of the IFD entry: SHORT or LONG
        raw[offset + 2 : offset + 12] = entry
    path.write_bytes(bytes(raw))
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y) == (None, None)
    assert "XResolution tag" in sizes.invalid and "YResolution tag" in sizes.invalid
    found = issue(inspect_images(tmp_path, ["a.tif"]).issues, "invalid_physical_calibration")
    assert found.paths == ("a.tif",) and "malformed" in found.message


def test_a_well_formed_imagej_resolution_is_not_invalid(tmp_path):
    path = tmp_path / "a.tif"
    tifffile.imwrite(
        path, np.zeros((8, 8), np.uint8), imagej=True, resolution=(2.0, 2.0),
        metadata={"unit": "um", "axes": "YX"},
    )  # fmt: skip
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y, sizes.invalid) == (0.5, 0.5, None)


def test_an_ome_zarr_time_scale_without_a_unit_is_named_not_taken_as_seconds(tmp_path):
    """Issue #155: a time step without a unit warns, like a space axis without one."""
    root = tmp_path / "exp"
    write_ngff(
        root / "c/a.ome.zarr", np.zeros((2, 8, 8), np.uint16), "tyx", scale=[3.0, 0.5, 0.5],
        units={"y": "micrometer", "x": "micrometer"},
    )  # fmt: skip
    write_ngff(
        root / "c/b.ome.zarr", np.zeros((2, 8, 8), np.uint16), "tyx", scale=[3.0, 0.5, 0.5],
        units={"t": "second", "y": "micrometer", "x": "micrometer"},
    )  # fmt: skip
    result = run_described(root, prefill_mapping("TYX"))
    found = issue(result.issues, "unsupported_pixel_size_unit")
    assert found.paths == ("c/a.ome.zarr",)
    assert "c/a.ome.zarr (T without a unit)" in found.message


def test_an_ome_zarr_scale_without_a_unit_is_unknown_and_named(tmp_path):
    root = tmp_path / "exp"
    write_ngff(root / "c/a.ome.zarr", np.zeros((8, 8), np.uint16), "yx", scale=[0.5, 0.5])
    write_ngff(
        root / "c/b.ome.zarr", np.zeros((8, 8), np.uint16), "yx", scale=[0.5, 0.5],
        units={"y": "micrometer", "x": "micrometer"},
    )  # fmt: skip
    result = run_described(root, prefill_mapping("YX"))
    found = issue(result.issues, "unsupported_pixel_size_unit")
    assert found.paths == ("c/a.ome.zarr",)
    assert "c/a.ome.zarr (X and Y without a unit)" in found.message
    sizes = RunInputs.open(result.output_folder).pixel_sizes("c/a.ome.zarr")
    assert (sizes["X"].value, sizes["Y"].value, sizes["X"].source) == (None, None, None)


def test_a_time_step_without_a_unit_is_not_sent_to_calibration_csv(tmp_path):
    root = tmp_path / "exp"
    write_ngff(root / "c/a.ome.zarr", np.zeros((2, 8, 8), np.uint16), "tyx", scale=[3.0, 0.5, 0.5])
    write_ngff(
        root / "c/b.ome.zarr", np.zeros((2, 8, 8), np.uint16), "tyx", scale=[3.0, 0.5, 0.5],
        units={"t": "second", "y": "micrometer", "x": "micrometer"},
    )  # fmt: skip
    result = run_described(root, prefill_mapping("TYX"))
    found = issue(result.issues, "unsupported_pixel_size_unit")
    assert "T metadata" in found.action and "export the images again" in found.action
    assert "T without a unit" in found.message
    assert "state it in calibration.csv" not in found.action
