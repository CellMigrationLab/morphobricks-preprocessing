import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import PhysicalSizes, read_physical_sizes, zarr_export
from morphobricks_preprocessing import physical_sizes as physical_sizes_module


def write_ome(path, **sizes):
    tifffile.imwrite(
        path,
        np.zeros((3, 8, 8), np.uint16),
        ome=True,
        photometric="minisblack",
        metadata={"axes": "ZYX", **sizes},
    )
    return path


# Units per axis ------------------------------------------------------------------------


def test_ome_units_are_read_per_axis(tmp_path):
    path = write_ome(
        tmp_path / "a.ome.tif",
        PhysicalSizeX=120.0,
        PhysicalSizeXUnit="nm",
        PhysicalSizeY=0.2,
        PhysicalSizeYUnit="µm",
        PhysicalSizeZ=2.0,
        PhysicalSizeZUnit="mm",
    )
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y, sizes.z) == (120.0, 0.2, 2.0)
    assert [sizes.unit(role) for role in "XYZ"] == ["nanometer", "micrometer", "millimeter"]
    assert (sizes.x_unit, sizes.y_unit, sizes.z_unit) == ("nanometer", "micrometer", "millimeter")
    assert sizes.space_unit == "nanometer"  # the unit of X, as before


def test_ome_units_left_out_are_micrometres(tmp_path):
    # The OME schema's default unit: Y and Z do not take X's unit.
    path = write_ome(
        tmp_path / "a.ome.tif",
        PhysicalSizeX=120.0,
        PhysicalSizeXUnit="nm",
        PhysicalSizeY=0.5,
        PhysicalSizeZ=2.0,
    )
    sizes = read_physical_sizes(path)
    assert [sizes.unit(role) for role in "XYZ"] == ["nanometer", "micrometer", "micrometer"]


def test_imagej_y_and_z_units(tmp_path):
    path = tmp_path / "a.tif"
    tifffile.imwrite(
        path,
        np.zeros((3, 8, 8), np.uint16),
        imagej=True,
        resolution=(1 / 0.25, 1 / 0.5),
        metadata={"axes": "ZYX", "unit": "um", "yunit": "nm", "zunit": "mm", "spacing": 1.5},
    )
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y, sizes.z) == (0.25, 0.5, 1.5)
    assert [sizes.unit(role) for role in "XYZ"] == ["micrometer", "nanometer", "millimeter"]


def test_imagej_without_y_and_z_units_uses_the_unit_for_all(tmp_path):
    path = tmp_path / "a.tif"
    tifffile.imwrite(
        path,
        np.zeros((3, 8, 8), np.uint16),
        imagej=True,
        resolution=(1 / 0.25, 1 / 0.25),
        metadata={"axes": "ZYX", "unit": "um", "spacing": 1.5},
    )
    sizes = read_physical_sizes(path)
    assert [sizes.unit(role) for role in "XYZ"] == ["micrometer"] * 3


# The dataclass, for callers written before per-axis units -------------------------------


def test_space_unit_alone_is_the_unit_of_every_space_axis():
    sizes = PhysicalSizes(0.5, 0.5, 2.0, None, "micrometer")
    assert [sizes.unit(role) for role in "XYZ"] == ["micrometer"] * 3
    assert sizes == PhysicalSizes(
        x=0.5,
        y=0.5,
        z=2.0,
        space_unit="micrometer",
        x_unit="micrometer",
        y_unit="micrometer",
        z_unit="micrometer",
    )


def test_per_axis_units_set_space_unit_to_the_unit_of_x():
    sizes = PhysicalSizes(x=1.0, y=2.0, x_unit="nanometer", y_unit="micrometer")
    assert sizes.space_unit == "nanometer"
    assert sizes.unit("Y") == "micrometer"
    # An axis given no unit has none, even when X has one.
    assert PhysicalSizes(x=1.0, y=2.0, x_unit="nanometer", z_unit="meter").unit("Y") is None


def test_space_unit_and_x_unit_must_agree():
    with pytest.raises(ValueError, match="unit of X"):
        PhysicalSizes(x=1.0, space_unit="micrometer", x_unit="nanometer")


def test_units_of_unknown_sizes_and_channels_are_none():
    sizes = PhysicalSizes(x=1.0, space_unit="micrometer", time_unit="second")
    assert sizes.unit("Y") is None and sizes.unit("T") is None and sizes.unit("C") is None
    assert sizes.scale("Y") == 1.0


def test_still_importable_from_zarr_export():
    assert zarr_export.PhysicalSizes is physical_sizes_module.PhysicalSizes is PhysicalSizes
    assert zarr_export.read_physical_sizes is read_physical_sizes
