"""check_metadata(..., allow_z=True): header checks of Z-stacks for other tools (issue #438)."""

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    check_metadata,
    inspect_image,
    inspect_images,
    inspect_tiff,
    read_physical_sizes,
)

SHAPE = (4, 5, 32, 32)  # T, Z, Y, X


def write_imagej(path, dtype=np.uint16, shape=SHAPE, axes="TZYX"):
    tifffile.imwrite(
        path,
        np.zeros(shape, dtype),
        imagej=True,
        metadata={"axes": axes, "spacing": 0.5, "unit": "um", "finterval": 2.5},
        resolution=(5, 5),
    )
    return path


def write_ome(path):
    tifffile.imwrite(
        path,
        np.zeros(SHAPE, np.uint16),
        ome=True,
        photometric="minisblack",
        metadata={
            "axes": "TZYX",
            "PhysicalSizeX": 0.2,
            "PhysicalSizeY": 0.2,
            "PhysicalSizeZ": 0.5,
            "PhysicalSizeXUnit": "um",
            "PhysicalSizeYUnit": "um",
            "PhysicalSizeZUnit": "um",
            "TimeIncrement": 2.5,
            "TimeIncrementUnit": "s",
        },
    )
    return path


def codes(issues):
    return [i.code for i in issues]


def test_default_still_refuses_a_z_stack(tmp_path):
    path = write_imagej(tmp_path / "a.tif")
    found = [i for i in check_metadata([inspect_image(path, "a.tif")]) if i.is_error]
    assert codes(found) == ["z_stack_not_supported"]
    assert found[0].paths == ("a.tif",)
    assert codes(inspect_images(tmp_path, ["a.tif"]).issues) == ["z_stack_not_supported"]


def test_allow_z_reports_no_z_error_for_a_clean_stack(tmp_path):
    path = write_imagej(tmp_path / "a.tif")
    files = [inspect_image(path, "a.tif")]
    assert codes(check_metadata(files)) == ["z_stack_not_supported"]
    assert check_metadata(files, allow_z=True) == []
    assert inspect_images(tmp_path, ["a.tif"], allow_z=True).issues == ()


def test_allow_z_keeps_every_other_check(tmp_path):
    write_imagej(tmp_path / "a.tif")
    write_imagej(tmp_path / "b.tif", dtype=np.uint8)  # another pixel type
    # Two channels where the others have none.
    write_imagej(tmp_path / "c.tif", shape=(4, 5, 2, 32, 32), axes="TZCYX")
    (tmp_path / "d.tif").write_bytes(b"garbage")  # unreadable
    names = ["a.tif", "b.tif", "c.tif", "d.tif"]
    files = [inspect_image(tmp_path / n, n) for n in names]
    default = codes(check_metadata(files))
    allowed = codes(check_metadata(files, allow_z=True))
    # The Z error would be reported without the flag ...
    assert "z_stack_not_supported" in default
    # ... and is the only difference with it.
    assert "z_stack_not_supported" not in allowed
    assert [c for c in default if c != "z_stack_not_supported"] == allowed
    for code in ("unreadable_tiff", "mixed_dtypes", "different_channel_counts"):
        assert code in allowed
    report = inspect_images(tmp_path, names, allow_z=True)
    assert codes(report.issues) == allowed


@pytest.mark.parametrize("writer", [write_imagej, write_ome], ids=["imagej", "ome"])
def test_headers_of_tzyx_files_are_read(tmp_path, writer):
    path = writer(tmp_path / "a.tif")
    meta = inspect_tiff(path)
    assert meta.readable
    assert meta.axes == "TZYX"
    assert meta.shape == SHAPE
    assert meta.shape[meta.axes.index("Z")] == 5
    sizes = read_physical_sizes(path)
    assert sizes.z == 0.5
    assert sizes.space_unit == "micrometer"
    assert sizes.x == pytest.approx(0.2)
    # The time interval is read as well (ImageJ finterval, OME TimeIncrement).
    assert sizes.t == 2.5
    assert sizes.time_unit == "second"
