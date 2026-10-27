import pickle
from dataclasses import replace

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import PhysicalSizes, read_physical_sizes, zarr_export
from morphobricks_preprocessing import physical_sizes as physical_sizes_module

# PhysicalSizes(x=0.1, y=0.2, z=0.5, space_unit="micrometer") pickled before per-axis units.
OLD_PICKLE = bytes.fromhex(
    "80049597000000000000008c266d6f7270686f627269636b735f70726570726f63657373696e672e7a617272"
    "5f6578706f7274948c0d506879736963616c53697a65739493942981947d94288c017894473fb99999999999"
    "9a8c017994473fc999999999999a8c017a94473fe00000000000008c0174944e8c0a73706163655f756e6974"
    "948c0a6d6963726f6d65746572948c0974696d655f756e6974944e75622e"
)


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


def test_per_axis_units_fall_back_to_space_unit():
    sizes = PhysicalSizes(x=1.0, y=2.0, x_unit="nanometer", y_unit="micrometer")
    assert (sizes.unit("X"), sizes.unit("Y")) == ("nanometer", "micrometer")
    # An axis given no unit has space_unit, here none, even when X has one.
    assert PhysicalSizes(x=1.0, y=2.0, x_unit="nanometer", z_unit="meter").unit("Y") is None
    partial = PhysicalSizes(y=1.0, z=1.0, space_unit="micrometer", y_unit="nanometer")
    assert (partial.unit("Y"), partial.unit("Z")) == ("nanometer", "micrometer")
    # An axis's own unit wins over space_unit.
    assert PhysicalSizes(x=1.0, space_unit="micrometer", x_unit="nanometer").unit("X") == (
        "nanometer"
    )


def test_replace_keeps_units_consistent():
    sizes = PhysicalSizes(x=0.1, y=0.1, space_unit="micrometer")
    assert replace(sizes, space_unit="nanometer").unit("Y") == "nanometer"
    assert replace(sizes, x_unit="nanometer").unit("X") == "nanometer"
    assert replace(sizes, x_unit="nanometer").unit("Y") == "micrometer"
    assert replace(sizes, space_unit=None).unit("X") is None


def test_sizes_pickled_before_per_axis_units_keep_their_unit():
    # PhysicalSizes(x=0.1, y=0.2, z=0.5, space_unit="micrometer") pickled by the version
    # before per-axis units (as morphobricks_preprocessing.zarr_export.PhysicalSizes).
    sizes = pickle.loads(OLD_PICKLE)
    assert [sizes.unit(role) for role in "XYZ"] == ["micrometer"] * 3
    assert sizes == PhysicalSizes(x=0.1, y=0.2, z=0.5, space_unit="micrometer")
    assert pickle.loads(pickle.dumps(sizes)) == sizes


def test_equal_sizes_hash_alike():
    given = PhysicalSizes(x=0.1, space_unit="micrometer")
    spelled_out = PhysicalSizes(
        x=0.1, space_unit="micrometer", x_unit="micrometer", y_unit="micrometer"
    )
    assert given == spelled_out and len({given, spelled_out}) == 1
    assert given != PhysicalSizes(x=0.1, space_unit="micrometer", y_unit="nanometer")
    assert given != "not sizes"


def test_units_of_unknown_sizes_and_channels_are_none():
    sizes = PhysicalSizes(x=1.0, space_unit="micrometer", time_unit="second")
    assert sizes.unit("Y") is None and sizes.unit("T") is None and sizes.unit("C") is None
    assert sizes.scale("Y") == 1.0


def test_still_importable_from_zarr_export():
    assert zarr_export.PhysicalSizes is physical_sizes_module.PhysicalSizes is PhysicalSizes
    assert zarr_export.read_physical_sizes is read_physical_sizes


# Unit spellings ------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", ["Å", "Å"])
def test_ome_angstroms(tmp_path, symbol):
    path = write_ome(
        tmp_path / "a.ome.tif",
        PhysicalSizeX=5.0,
        PhysicalSizeXUnit=symbol,
        PhysicalSizeY=5.0,
        PhysicalSizeYUnit=symbol,
    )
    sizes = read_physical_sizes(path)
    assert (sizes.unit("X"), sizes.unit("Y")) == ("angstrom", "angstrom")


def test_imagej_escaped_micro_sign(tmp_path):
    # ImageJ writes the micro sign as µ in the description; tifffile keeps the text.
    path = tmp_path / "a.tif"
    tifffile.imwrite(
        path,
        np.zeros((3, 8, 8), np.uint16),
        imagej=True,
        resolution=(1 / 0.25, 1 / 0.5),
        metadata={
            "axes": "ZYX",
            "unit": "\\u00B5m",
            "yunit": "\\u00B5m",
            "zunit": "\\u00B5m",
            "spacing": 1.5,
        },
    )
    assert tifffile.TiffFile(path).imagej_metadata["unit"] == "\\u00B5m"
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y, sizes.z) == (0.25, 0.5, 1.5)
    assert [sizes.unit(role) for role in "XYZ"] == ["micrometer"] * 3


def test_ngff_unit_spellings_become_udunits_names():
    multiscale = {
        "axes": [
            {"name": "t", "type": "time", "unit": "s"},
            {"name": "z", "type": "space", "unit": "µm"},
            {"name": "y", "type": "space", "unit": "Micron"},
            {"name": "x", "type": "space", "unit": "um"},
        ],
        "datasets": [
            {"path": "0", "coordinateTransformations": [{"type": "scale", "scale": [1, 2, 3, 4]}]}
        ],
    }
    sizes = physical_sizes_module.ngff_physical_sizes(multiscale, "TZYX")
    assert [sizes.unit(role) for role in "TZYX"] == ["second"] + ["micrometer"] * 3
    # Other units are kept as written; short spellings are matched exactly.
    multiscale["axes"][1]["unit"] = "kilometer"
    multiscale["axes"][2]["unit"] = "Mm"
    sizes = physical_sizes_module.ngff_physical_sizes(multiscale, "TZYX")
    assert (sizes.unit("Z"), sizes.unit("Y")) == ("kilometer", "Mm")
