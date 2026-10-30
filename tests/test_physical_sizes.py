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


def test_imagej_boolean_values_are_not_sizes(tmp_path):
    # tifffile reads "spacing=true" as True, which float() would turn into 1.
    path = tmp_path / "a.tif"
    tifffile.imwrite(
        path,
        np.zeros((3, 8, 8), np.uint16),
        imagej=True,
        resolution=(1 / 0.25, 1 / 0.25),
        metadata={"axes": "ZYX", "unit": "um", "spacing": True, "finterval": True},
    )
    assert tifffile.TiffFile(path).imagej_metadata["spacing"] is True
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.z, sizes.t) == (0.25, None, None)


def test_ngff_boolean_scales_are_not_sizes():
    multiscale = {
        "axes": [{"name": "y", "type": "space"}, {"name": "x", "type": "space"}],
        "datasets": [
            {"path": "0", "coordinateTransformations": [{"type": "scale", "scale": [True, 0.5]}]}
        ],
    }
    sizes = physical_sizes_module.ngff_physical_sizes(multiscale, "YX")
    assert (sizes.y, sizes.x) == (None, 0.5)


# The shared unit rule (MorphoBricks D86, decision D-a2) ---------------------------------


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("pm", "picometer"),
        ("picometre", "picometer"),
        ("Å", "angstrom"),
        ("Å", "angstrom"),
        ("angstrom", "angstrom"),
        ("Angstroms", "angstrom"),
        ("nm", "nanometer"),
        ("nanometre", "nanometer"),
        ("µm", "micrometer"),
        ("μm", "micrometer"),
        ("um", "micrometer"),
        ("\\u00B5m", "micrometer"),
        ("micron", "micrometer"),
        ("microns", "micrometer"),
        ("micrometer", "micrometer"),
        ("micrometers", "micrometer"),
        ("micrometre", "micrometer"),
        ("Micrometres", "micrometer"),
        (" µm ", "micrometer"),
        ("mm", "millimeter"),
        ("millimetre", "millimeter"),
        ("cm", "centimeter"),
        ("centimeter", "centimeter"),
        ("m", "meter"),
        ("metre", "meter"),
        ("meters", "meter"),
    ],
)
def test_space_unit_reads_the_microscopy_units(text, name):
    from morphobricks_preprocessing import space_unit

    assert space_unit(text) == name


@pytest.mark.parametrize(
    "text",
    # Units MorphoRelate and fiberfeatures converted before D86 (UDUNITS lengths, inches),
    # and short spellings in the wrong case: refused by name, never converted or guessed.
    ["inch", "in", "decimeter", "dm", "km", "kilometer", "parsec", "foot", "pixel", "Mm", "UM"],
)
def test_space_unit_refuses_other_units_by_name(text):
    from morphobricks_preprocessing import UnsupportedUnitError, space_unit

    with pytest.raises(UnsupportedUnitError, match=repr(text).replace("\\", "\\\\")):
        space_unit(text)


def test_space_unit_none_when_no_unit_is_given():
    from morphobricks_preprocessing import space_unit

    assert space_unit(None) is None
    assert space_unit("  ") is None


def test_to_micrometers():
    from morphobricks_preprocessing import UnsupportedUnitError, to_micrometers

    assert to_micrometers(0.1625, "µm") == 0.1625
    assert to_micrometers(162.5, "nanometre") == pytest.approx(0.1625)
    assert to_micrometers(5.0, "Å") == pytest.approx(5e-4)
    assert to_micrometers(1, "mm") == 1000.0
    assert to_micrometers(None, "µm") is None
    assert to_micrometers(0.5, None) is None
    with pytest.raises(UnsupportedUnitError, match="'inch'"):
        to_micrometers(1 / 300, "inch")
    for bad in (0, -1.0, float("nan"), float("inf"), True, "0.5"):
        with pytest.raises(ValueError):
            to_micrometers(bad, "µm")


def test_space_units_are_the_converted_ones():
    from morphobricks_preprocessing import SPACE_UNITS

    assert set(SPACE_UNITS) == set(physical_sizes_module._MICROMETERS)


@pytest.mark.parametrize("unit", ["micrometre", "nanometre", "Micrometres"])
def test_imagej_british_spellings_are_calibrated(tmp_path, unit):
    # Before D86 these left the image uncalibrated (unit None).
    path = tmp_path / "a.tif"
    tifffile.imwrite(
        path,
        np.zeros((8, 8), np.uint16),
        imagej=True,
        resolution=(1 / 0.25, 1 / 0.25),
        metadata={"unit": unit},
    )
    sizes = read_physical_sizes(path)
    assert sizes.x == 0.25
    assert sizes.unit("X") == ("micrometer" if "micro" in unit.lower() else "nanometer")


def test_ngff_british_spelling_is_converted():
    multiscale = {
        "axes": [
            {"name": "y", "type": "space", "unit": "micrometre"},
            {"name": "x", "type": "space", "unit": "nanometres"},
        ],
        "datasets": [
            {"path": "0", "coordinateTransformations": [{"type": "scale", "scale": [0.5, 500]}]}
        ],
    }
    sizes = physical_sizes_module.ngff_physical_sizes(multiscale, "YX")
    assert (sizes.micrometers("Y"), sizes.micrometers("X")) == (0.5, 0.5)


@pytest.mark.parametrize(("unit", "dpi"), [(2, 300.0), (3, 300.0 / 2.54)])
def test_plain_tiff_resolution_is_never_a_calibration(tmp_path, unit, dpi):
    # Decision D-c: a plain TIFF's resolution tags (here 300 dpi, ResolutionUnit inch or
    # centimetre, as scanners and Photoshop write them) are not a pixel size: without OME or
    # ImageJ metadata the image is uncalibrated, never 84.67 micrometres per pixel.
    path = tmp_path / "scan.tif"
    tifffile.imwrite(
        path,
        np.zeros((8, 8), np.uint8),
        photometric="minisblack",
        resolution=(dpi, dpi),
        resolutionunit=unit,
        metadata=None,
    )
    with tifffile.TiffFile(path) as tif:
        assert not tif.is_ome and not tif.is_imagej
        assert tif.pages.first.tags["ResolutionUnit"].value == unit
    sizes = read_physical_sizes(path)
    assert sizes == PhysicalSizes()
    assert sizes.micrometers("X") is None and sizes.micrometers("Y") is None
