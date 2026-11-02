"""Issue #93: a size converted to micrometres is always a positive finite length.

- ``to_micrometers`` refuses a value whose conversion overflows to infinity (``1e308 m``) or
  underflows to zero (``1e-320 pm``), and a whole number beyond the float range, with a
  ``ValueError`` naming the value and unit; valid sizes near those limits are kept.
- ``PhysicalSizes.micrometers`` goes through ``to_micrometers``: such a size is unknown
  (None), never ``inf``, so ``same_pixel_size`` and ``pixel_size_groups`` never take it for
  another and never raise on it.
- A header size that cannot be given in micrometres is None, with what is wrong in
  ``PhysicalSizes.invalid``, so the folder check names the image and the value
  (``invalid_physical_calibration``); a stated one is refused in ``calibration.csv``.
- Before the fix, ``1e308 m`` next to ``0.5 µm`` raised no issue at all (both sides were
  ``inf`` in the tolerance), and next to two other sizes the preflight raised ValueError.
"""

import math

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    CalibrationFormatError,
    PhysicalSizes,
    calibration_entry,
    pixel_size_groups,
    preflight,
    read_calibration,
    read_physical_sizes,
    same_pixel_size,
    to_micrometers,
)
from morphobricks_preprocessing import physical_sizes as physical_sizes_module
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.recorded_sizes import RecordedSize

HUGE = PhysicalSizes(x=1e308, y=1e308, space_unit="meter")
HALF = PhysicalSizes(x=0.5, y=0.5, space_unit="micrometer")


def write_ome(path, size, unit, y_size=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        path,
        np.zeros((8, 8), np.uint16),
        ome=True,
        photometric="minisblack",
        metadata={
            "axes": "YX",
            "PhysicalSizeX": size,
            "PhysicalSizeXUnit": unit,
            "PhysicalSizeY": size if y_size is None else y_size,
            "PhysicalSizeYUnit": unit,
        },
    )
    return path


def codes(issues):
    return [issue.code for issue in issues]


def issue(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


# to_micrometers -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "unit", "converts_to"),
    [
        (1e308, "m", "inf"),  # overflows
        (1.8e302, "m", "inf"),  # just above the largest float in micrometres
        (1e306, "mm", "inf"),
        (1e-320, "pm", "0.0"),  # subnormal: underflows to zero
        (5e-324, "Å", "0.0"),  # the smallest subnormal
    ],
)
def test_a_conversion_beyond_the_float_range_is_refused(value, unit, converts_to):
    with pytest.raises(ValueError, match="micrometres") as raised:
        to_micrometers(value, unit)
    assert repr(value) in str(raised.value) and converts_to in str(raised.value)


def test_a_whole_number_beyond_the_float_range_is_a_value_error():
    # Before: OverflowError ("int too large to convert to float"), not a ValueError.
    with pytest.raises(ValueError, match="not a positive finite number"):
        to_micrometers(10**400, "µm")


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (1.79e302, "m", 1.79e308),  # just below the largest float
        (5e-324, "µm", 5e-324),  # the smallest subnormal, not converted
        (1e-317, "nm", 1e-320),  # converts to a subnormal, still positive
        (500.0, "nm", 0.5),
        (0.0005, "mm", 0.5),
        (5000.0, "Å", 0.5),
    ],
)
def test_sizes_near_the_limits_and_across_units_are_kept(value, unit, expected):
    found = to_micrometers(value, unit)
    assert math.isfinite(found) and found > 0
    assert found == pytest.approx(expected, rel=1e-12)


# PhysicalSizes, same_pixel_size and pixel_size_groups -----------------------------------


def test_an_overflowing_size_is_unknown_never_inf():
    assert HUGE.micrometers("X") is None and HUGE.micrometers("Y") is None
    assert PhysicalSizes(z=1e-320, space_unit="picometer").micrometers("Z") is None
    mixed = PhysicalSizes(x=500.0, y=0.0005, x_unit="nanometer", y_unit="millimeter")
    assert (mixed.micrometers("X"), mixed.micrometers("Y")) == pytest.approx((0.5, 0.5))


def test_an_overflowing_size_is_never_the_same_as_another():
    # Before: same_pixel_size raised ValueError ("the pixel size inf ...").
    assert same_pixel_size(HUGE, HALF) is None
    assert same_pixel_size(HUGE, HUGE) is None
    # Before: pixel_size_groups raised ValueError; an unknown size is left out.
    assert pixel_size_groups({"huge": HUGE, "half": HALF}) == [("half",)]
    assert not physical_sizes_module._same_length(0.5, math.inf)
    assert not physical_sizes_module._same_length(math.inf, math.inf)
    assert not physical_sizes_module._same_length(math.nan, 0.5)


def test_a_recorded_size_that_overflows_is_unknown():
    huge = RecordedSize("X", 1e308, "meter", "header")
    assert huge.micrometers() is None
    assert RecordedSize("X", 0.5, "micrometer", "header").micrometers() == 0.5


# Header sizes -----------------------------------------------------------------------------


def test_a_header_size_that_overflows_is_invalid_and_named(tmp_path):
    sizes = read_physical_sizes(write_ome(tmp_path / "a.ome.tif", 1e308, "m", y_size=0.5e-6))
    assert sizes.x is None and sizes.micrometers("X") is None
    assert sizes.micrometers("Y") == pytest.approx(0.5)
    assert sizes.invalid.startswith("X: the length 1e+308 meter")
    assert "inf" in sizes.invalid and "Y" not in sizes.invalid


def test_a_tiny_header_size_that_underflows_is_invalid(tmp_path):
    sizes = read_physical_sizes(write_ome(tmp_path / "a.ome.tif", 1e-320, "pm"))
    assert (sizes.x, sizes.y) == (None, None)
    assert "X: the length 1e-320 picometer" in sizes.invalid and "Y: " in sizes.invalid


def test_usable_header_sizes_are_not_invalid(tmp_path):
    sizes = read_physical_sizes(write_ome(tmp_path / "a.ome.tif", 500.0, "nm"))
    assert sizes.invalid is None and sizes.micrometers("X") == pytest.approx(0.5)


def ngff(scale, unit, overall=None):
    multiscale = {
        "axes": [{"name": a, "type": "space", "unit": unit} for a in "yx"],
        "datasets": [
            {"path": "0", "coordinateTransformations": [{"type": "scale", "scale": scale}]}
        ],
    }
    if overall is not None:
        multiscale["coordinateTransformations"] = [{"type": "scale", "scale": overall}]
    return physical_sizes_module.ngff_physical_sizes(multiscale, "YX")


def test_an_ngff_scale_that_overflows_in_micrometres_is_invalid():
    sizes = ngff([0.5, 1e303], "meter")
    assert sizes.y == 0.5 and sizes.x is None
    assert sizes.invalid.startswith("X: the length 1e+303 meter")


def test_an_ngff_scale_whose_product_overflows_is_invalid():
    # Before: the product was inf and the size silently None.
    sizes = ngff([1e200, 0.5], "micrometer", overall=[1e200, 1.0])
    assert sizes.y is None and sizes.x == 0.5
    assert "the scale along Y, level 0's times the image's, is inf" in sizes.invalid
    tiny = ngff([1e-200, 0.5], "micrometer", overall=[1e-200, 1.0])
    assert tiny.y is None and "is 0.0" in tiny.invalid
    assert ngff([0.5, 0.5], "micrometer", overall=[2.0, 2.0]).invalid is None


# The folder check -------------------------------------------------------------------------


def test_the_folder_check_names_an_overflowing_size_never_equal(tmp_path):
    # Before: no issue at all (0.5 and inf were "the same" size).
    root = tmp_path / "exp"
    write_ome(root / "ctrl/rep1/a.ome.tif", 0.5, "µm")
    write_ome(root / "ctrl/rep1/b.ome.tif", 1e308, "m")
    pre = preflight(root, tmp_path / "out")
    found = issue(pre.issues, "invalid_physical_calibration")
    assert found.paths == ("ctrl/rep1/b.ome.tif",)
    assert "ctrl/rep1/b.ome.tif: X: the length 1e+308 meter" in found.message
    assert "calibration.csv" in found.action and "coordinateTransformations" in found.action
    assert issue(pre.issues, "missing_pixel_size").paths == ("ctrl/rep1/b.ome.tif",)
    assert pre.ok  # warnings: the image is uncalibrated, as design v0.4 §3 treats one


def test_the_folder_check_still_compares_the_other_sizes(tmp_path):
    # Before: pixel_size_groups raised ValueError out of the preflight.
    root = tmp_path / "exp"
    write_ome(root / "ctrl/rep1/a.ome.tif", 0.5, "µm")
    write_ome(root / "ctrl/rep1/b.ome.tif", 1e308, "m")
    write_ome(root / "ctrl/rep1/c.ome.tif", 1000.0, "nm")
    write_ome(root / "ctrl/rep1/d.ome.tif", 0.0005, "mm")
    pre = preflight(root, tmp_path / "out")
    assert {"different_pixel_sizes", "invalid_physical_calibration"} <= set(codes(pre.issues))
    different = issue(pre.issues, "different_pixel_sizes")
    assert different.paths == ("ctrl/rep1/c.ome.tif",)
    assert "1 x 1 micrometres" in different.message and "inf" not in different.message


# Stated sizes -----------------------------------------------------------------------------


def test_a_stated_size_that_overflows_is_refused(tmp_path):
    with pytest.raises(ValueError, match="1e\\+308 meter is not a positive finite number of mic"):
        calibration_entry(".", "X", "1e308", "m")
    with pytest.raises(ValueError, match="not a positive finite number"):
        calibration_entry(".", "X", 10**400, "µm")
    assert calibration_entry(".", "X", "1e-317", "nm").size == 1e-317
    path = tmp_path / "calibration.csv"
    path.write_text("target,axis,size,unit\n.,X,1e-320,pm\n", encoding="utf-8")
    with pytest.raises(CalibrationFormatError, match="line 2.*micrometres"):
        read_calibration(path)


def test_the_calibrate_command_refuses_an_overflowing_size(tmp_path, capsys):
    output = tmp_path / "out"
    output.mkdir()
    assert main(["calibrate", "--output", str(output), "--size", ".", "XY", "1e308", "m"]) == 1
    assert "Nothing was changed" in capsys.readouterr().out
    assert not (output / "calibration.csv").exists()
