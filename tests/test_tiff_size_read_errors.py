"""Round-2 audit A-m6: a TIFF calibration that cannot be read is invalid, never "none".

``read_physical_sizes`` caught every exception and logged it at debug level, so a file it
could not read read as uncalibrated; only OME-XML that does not parse was ``invalid``. Now:

- no metadata is still no calibration (all None, ``invalid`` None);
- a file that cannot be opened as a TIFF, and metadata whose bytes cannot be read, give no
  sizes with the reason in ``PhysicalSizes.invalid`` (``invalid_physical_calibration``);
- a resolution tag tifffile drops because it cannot read it (it only logs) leaves its pair
  alone, which is named instead of read as an uncalibrated axis;
- any other exception is a defect and is raised.
"""

import struct

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import inspect_images, read_physical_sizes
from morphobricks_preprocessing import physical_sizes as physical_sizes_module


def write_imagej(path, **metadata):
    tifffile.imwrite(
        path, np.zeros((8, 8), np.uint8), imagej=True, resolution=(2.0, 2.0),
        metadata={"unit": "um", "axes": "YX", **metadata},
    )  # fmt: skip
    return path


def tag_entry(path, code):
    with tifffile.TiffFile(path) as tif:
        return tif.pages.first.tags[code].offset


def invalid_issue(folder, name):
    found = inspect_images(folder, [name]).issues
    (issue,) = [i for i in found if i.code == "invalid_physical_calibration"]
    return issue


def test_an_image_without_metadata_is_still_uncalibrated(tmp_path):
    path = tmp_path / "a.tif"
    tifffile.imwrite(path, np.zeros((8, 8), np.uint8))
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y, sizes.invalid) == (None, None, None)


@pytest.mark.parametrize(
    ("field", "value"),
    [("value offset", lambda size: struct.pack("<I", size + 1000)), ("type", lambda _: b"c")],
)
def test_an_unreadable_resolution_tag_is_an_invalid_calibration(tmp_path, field, value):
    # tifffile drops the XResolution tag with a log message; before, X read as uncalibrated
    # with no reason while Y kept its size.
    path = write_imagej(tmp_path / "a.tif")
    raw = bytearray(path.read_bytes())
    offset = tag_entry(path, 282)
    if field == "type":
        raw[offset + 2 : offset + 4] = struct.pack("<H", 99)
    else:
        raw[offset + 8 : offset + 12] = value(len(raw))
    path.write_bytes(bytes(raw))
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y) == (None, 0.5)
    assert "XResolution tag is missing or unreadable, YResolution is given" in sizes.invalid
    assert "XResolution" in invalid_issue(tmp_path, "a.tif").message


def test_a_corrupt_imagej_metadata_tag_leaves_the_sizes(tmp_path):
    path = write_imagej(tmp_path / "a.tif", spacing=0.25, Info="notes")
    raw = bytearray(path.read_bytes())
    with tifffile.TiffFile(path) as tif:
        start = tif.pages.first.tags[50839].valueoffset  # IJMetadata: not where sizes are
    raw[start : start + 8] = b"XXXXXXXX"
    path.write_bytes(bytes(raw))
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y, sizes.z, sizes.invalid) == (0.5, 0.5, 0.25, None)


@pytest.mark.parametrize(
    "content", [b"", b"hello, not a TIFF", b"II*\x00\x08\x00\x00\x00\x05\x00", "truncated"]
)
def test_a_file_that_cannot_be_read_as_a_tiff_is_invalid(tmp_path, content):
    path = tmp_path / "a.tif"
    if content == "truncated":
        content = write_imagej(tmp_path / "b.tif").read_bytes()[:60]
    path.write_bytes(content)
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y) == (None, None)
    assert sizes.invalid.startswith("the file cannot be read as a TIFF (")


def test_a_missing_file_is_invalid_not_uncalibrated(tmp_path):
    sizes = read_physical_sizes(tmp_path / "gone.tif")
    assert "cannot be read as a TIFF (FileNotFoundError" in sizes.invalid


@pytest.mark.parametrize(
    "error", [tifffile.TiffFileError("bad tag"), OSError("I/O error"), struct.error("short")]
)
def test_metadata_that_cannot_be_read_is_an_invalid_calibration(tmp_path, monkeypatch, error):
    # A tag value tifffile reads on access, whose bytes cannot be read.
    path = write_imagej(tmp_path / "a.tif")
    get = tifffile.TiffTags.get

    def unreadable(self, key, *args, **kwargs):
        if key == "XResolution":
            raise error
        return get(self, key, *args, **kwargs)

    monkeypatch.setattr(tifffile.TiffTags, "get", unreadable)
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y) == (None, None)
    assert sizes.invalid == f"the TIFF metadata cannot be read ({type(error).__name__}: {error})"
    issue = invalid_issue(tmp_path, "a.tif")  # the header is still readable: a warning
    assert issue.paths == ("a.tif",) and "TIFF metadata cannot be read" in issue.message


def test_imagej_metadata_that_cannot_be_read_makes_the_header_unreadable(tmp_path, monkeypatch):
    # The header check reads it too: the image is refused, which is not silent either.
    path = write_imagej(tmp_path / "a.tif")

    def unreadable(self):
        raise OSError("I/O error")

    monkeypatch.setattr(tifffile.TiffFile, "imagej_metadata", property(unreadable))
    assert "OSError: I/O error" in read_physical_sizes(path).invalid
    codes = [i.code for i in inspect_images(tmp_path, ["a.tif"]).issues]
    assert "unreadable_tiff" in codes


@pytest.mark.parametrize("error", [KeyError("unit"), AttributeError("tags"), ValueError("bug")])
def test_a_defect_is_raised_not_read_as_uncalibrated(tmp_path, monkeypatch, error):
    path = write_imagej(tmp_path / "a.tif")

    def broken(tif):
        raise error

    monkeypatch.setattr(physical_sizes_module, "_imagej_sizes", broken)
    with pytest.raises(type(error)):
        read_physical_sizes(path)


@pytest.mark.parametrize("path", [None, 5, b"a.tif"])
def test_a_value_that_is_not_a_path_is_refused(path):
    with pytest.raises(TypeError, match="takes a path"):
        read_physical_sizes(path)


@pytest.mark.parametrize(
    "error",
    [OSError("I/O"), ValueError("not a TIFF"), TypeError("bad IFD"), struct.error("short")],
)
def test_each_documented_open_error_is_an_invalid_calibration(tmp_path, monkeypatch, error):
    path = write_imagej(tmp_path / "a.tif")

    def unopenable(*args, **kwargs):
        raise error

    monkeypatch.setattr(tifffile, "TiffFile", unopenable)
    sizes = read_physical_sizes(path)
    assert sizes.invalid == f"the file cannot be read as a TIFF ({type(error).__name__}: {error})"


@pytest.mark.parametrize("error", [KeyError("tag"), IndexError("pages"), RuntimeError("bug")])
def test_another_error_while_opening_is_raised(tmp_path, monkeypatch, error):
    path = write_imagej(tmp_path / "a.tif")

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(tifffile, "TiffFile", broken)
    with pytest.raises(type(error)):
        read_physical_sizes(path)


def test_a_lone_resolution_tag_without_a_unit_is_not_a_calibration(tmp_path):
    # Without a unit the resolution tags are a default, never a size: nothing is lost.
    path = tmp_path / "a.tif"
    tifffile.imwrite(
        path, np.zeros((8, 8), np.uint8), imagej=True, resolution=(2.0, 2.0),
        metadata={"axes": "YX"},
    )  # fmt: skip
    raw = bytearray(path.read_bytes())
    offset = tag_entry(path, 282)
    raw[offset + 2 : offset + 4] = struct.pack("<H", 99)  # XResolution of an unknown type
    path.write_bytes(bytes(raw))
    with tifffile.TiffFile(path) as tif:
        assert tif.is_imagej and tif.pages.first.tags.get("XResolution") is None
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y, sizes.invalid) == (None, None, None)


def drop_resolution_tags(path):
    """Renumber both resolution tags as private tags, so the file has neither."""
    raw = bytearray(path.read_bytes())
    for code, private in ((282, 65000), (283, 65001)):
        offset = tag_entry(path, code)
        raw[offset : offset + 2] = struct.pack("<H", private)
    path.write_bytes(bytes(raw))
    with tifffile.TiffFile(path) as tif:
        tags = tif.pages.first.tags
        assert tags.get("XResolution") is None and tags.get("YResolution") is None
    return path


def test_a_unit_without_either_resolution_tag_is_an_invalid_calibration(tmp_path):
    # Owner, 3 November 2026: before, read as uncalibrated with no reason.
    path = drop_resolution_tags(write_imagej(tmp_path / "a.tif", spacing=0.25))
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y) == (None, None)
    assert sizes.invalid == (
        "X, Y: the unit 'um' is given but neither the XResolution nor the YResolution tag "
        "is (a unit without a resolution)"
    )
    message = invalid_issue(tmp_path, "a.tif").message
    assert "a unit without a resolution" in message


def test_the_unit_named_is_the_one_written(tmp_path):
    path = drop_resolution_tags(write_imagej(tmp_path / "a.tif", unit="\\u00B5m"))
    assert "the unit 'µm' is given" in read_physical_sizes(path).invalid  # escapes decoded


def test_no_unit_and_no_resolution_is_still_uncalibrated(tmp_path):
    path = tmp_path / "a.tif"
    tifffile.imwrite(path, np.zeros((8, 8), np.uint8), imagej=True, metadata={"axes": "YX"})
    drop_resolution_tags(path)
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y, sizes.invalid) == (None, None, None)


def test_a_tiff_without_pages_is_an_invalid_calibration(tmp_path):
    # tifffile opens it with only a log message; before, it read as uncalibrated.
    path = tmp_path / "a.tif"
    path.write_bytes(b"II*\x00" + struct.pack("<I", 0))
    with tifffile.TiffFile(path) as tif:
        assert len(tif.pages) == 0
    sizes = read_physical_sizes(path)
    assert (sizes.x, sizes.y) == (None, None)
    assert sizes.invalid == "the TIFF file has no pages (no image)"


def test_a_tiff_without_pages_is_unreadable_in_the_folder_check(tmp_path):
    # check and run never reach its sizes: the header has no image (as before rc5).
    (tmp_path / "a.tif").write_bytes(b"II*\x00" + struct.pack("<I", 0))
    codes = {i.code for i in inspect_images(tmp_path, ["a.tif"]).issues}
    assert "unreadable_tiff" in codes and "invalid_physical_calibration" not in codes
