"""Safe display (design section 2): reduced first, bounded in memory, never guessing."""

import tracemalloc

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import preview
from morphobricks_preprocessing.axes import AxisMapping, AxisReview
from morphobricks_preprocessing.axis_check import AXES_YX_SWAPPED
from morphobricks_preprocessing.fingerprint import Fingerprint
from morphobricks_preprocessing.preview import (
    DETAIL_SIDE,
    FILE_CHANGED,
    MASK_SIZE_DIFFERS,
    PLANE_TOO_LARGE,
    ROLES_NOT_CONFIRMED,
    THUMBNAIL_SIDE,
    PreviewCache,
    PreviewRefused,
    StripOptions,
    channel_strip,
    mask_thumbnail,
    read_plane,
)
from morphobricks_preprocessing.validation import error
from zarr_tree import write_ngff

MiB = 2**20
RNG = np.random.default_rng(7)


def write(path, data, axes):
    tifffile.imwrite(path, data, metadata={"axes": axes}, photometric="minisblack")
    return path


def review_for(relative_path, mapping, *, issues=()):
    return AxisReview(mapping, {relative_path: mapping}, tuple(issues))


# read_plane(max_side) -------------------------------------------------------------------


def test_a_20000_square_plane_is_shown_without_ever_holding_it(tmp_path):
    path = tmp_path / "big.tif"
    tifffile.imwrite(path, shape=(20000, 20000), dtype=np.uint16, metadata={"axes": "YX"})
    assert path.stat().st_size > 700 * MiB  # 800 MB of pixels (written sparse)
    tracemalloc.start()
    try:
        shown = read_plane(path, AxisMapping("YX", "YX"), max_side=THUMBNAIL_SIDE)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 100 * MiB  # the plane itself is 800 MiB
    assert max(shown.pixels.shape) <= THUMBNAIL_SIDE and shown.reduced
    assert shown.source_shape == (20000, 20000) and shown.pixels.dtype == np.uint16
    assert shown.pixels.shape[0] == -(-20000 // shown.step)


def test_a_large_zarr_plane_is_read_in_blocks_for_display(tmp_path):
    pixels = RNG.integers(0, 60000, (8000, 8000), dtype=np.uint16)  # 122 MiB, over the budget
    path = write_ngff(tmp_path / "big.zarr", pixels, axes="yx", chunks=(512, 512))
    expected = pixels.copy()
    del pixels
    tracemalloc.start()
    try:
        shown = read_plane(path, AxisMapping("YX", "YX"), max_side=DETAIL_SIDE)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 100 * MiB
    assert max(shown.pixels.shape) <= DETAIL_SIDE
    assert np.array_equal(shown.pixels, expected[:: shown.step, :: shown.step])


def test_a_small_plane_is_returned_whole_and_unchanged(tmp_path):
    source = RNG.integers(0, 4000, (60, 90), dtype=np.uint16)
    shown = read_plane(
        write(tmp_path / "a.tif", source, "YX"), AxisMapping("YX", "YX"), max_side=256
    )
    assert shown.step == 1 and not shown.reduced and np.array_equal(shown.pixels, source)


@pytest.mark.parametrize("side", [THUMBNAIL_SIDE, DETAIL_SIDE, 100, 1])
def test_the_display_cap_holds_and_values_are_every_nth_source_pixel(tmp_path, side):
    source = RNG.integers(0, 4000, (1300, 1700), dtype=np.uint16)
    path = write(tmp_path / "a.tif", source, "YX")
    shown = read_plane(path, AxisMapping("YX", "YX"), max_side=side)
    assert max(shown.pixels.shape) <= side
    assert np.array_equal(shown.pixels, source[:: shown.step, :: shown.step])


def test_a_plane_read_in_blocks_is_every_nth_source_pixel(tmp_path, monkeypatch):
    source = RNG.integers(0, 4000, (900, 1100), dtype=np.uint16)
    path = tmp_path / "tiled.tif"
    tifffile.imwrite(path, source, tile=(64, 64), metadata={"axes": "YX"}, photometric="minisblack")
    monkeypatch.setattr(preview, "READ_BUDGET", 300 * 1024)  # smaller than the plane: blocks
    shown = read_plane(path, AxisMapping("YX", "YX"), max_side=200)
    assert shown.reduced and max(shown.pixels.shape) <= 200
    assert np.array_equal(shown.pixels, source[:: shown.step, :: shown.step])


def test_the_coordinates_default_as_the_source_reader_does(tmp_path):
    path = write(tmp_path / "a.tif", RNG.integers(0, 90, (3, 4, 40, 50), dtype=np.uint16), "ZCYX")
    shown = read_plane(path, AxisMapping("ZCYX", "ZCYX"), max_side=64, z=2, c=1)
    assert (shown.z, shown.c, shown.t) == (2, 1, None)
    assert "z=2, c=1" in shown.caption and "50" in shown.caption
    with pytest.raises(IndexError):
        read_plane(path, AxisMapping("ZCYX", "ZCYX"), max_side=64, c=9)


def test_a_cap_below_one_is_a_programming_error(tmp_path):
    path = write(tmp_path / "a.tif", np.zeros((8, 8), np.uint16), "YX")
    with pytest.raises(ValueError, match="max_side"):
        read_plane(path, AxisMapping("YX", "YX"), max_side=0)


def test_a_plane_that_cannot_be_split_and_exceeds_the_limit_is_refused_with_a_card(tmp_path):
    source = RNG.integers(0, 4000, (300, 300), dtype=np.uint16)
    path = tmp_path / "z.tif"
    tifffile.imwrite(path, source, compression="zlib", rowsperstrip=300, metadata={"axes": "YX"})
    with pytest.raises(PreviewRefused) as refused:
        read_plane(path, AxisMapping("YX", "YX"), max_side=64, memory_limit=50_000)
    assert refused.value.code == PLANE_TOO_LARGE


# channel strip: confirmed roles only ----------------------------------------------------


def three_channels(tmp_path):
    data = np.stack([RNG.integers(0, hi, (80, 60), dtype=np.uint16) for hi in (100, 5000, 60000)])
    return write(tmp_path / "cyx.tif", data, "CYX"), data


def test_a_strip_has_one_panel_per_channel_each_with_its_own_contrast(tmp_path):
    path, data = three_channels(tmp_path)
    mapping = AxisMapping("CYX", "CYX")
    strip = channel_strip(
        path,
        "cyx.tif",
        review_for("cyx.tif", mapping),
        StripOptions(header_names=["DAPI", None, " AF "]),
    )
    assert [p.label for p in strip.panels] == ["C0 'DAPI'", "C1", "C2 ' AF '"]
    assert [p.c for p in strip.panels] == [0, 1, 2]
    lows = [p.panel.low for p in strip.panels]
    highs = [p.panel.high for p in strip.panels]
    assert highs[0] < 120 < 4000 < highs[1] < 5100 < 50000 < highs[2]  # never one shared range
    assert all(p.panel.pixels.max() == 255 and p.panel.pixels.min() == 0 for p in strip.panels)
    assert strip.png.startswith(b"\x89PNG") and lows[0] >= 0 and strip.z is None and strip.t is None


def test_the_strip_says_which_z_and_t_it_shows(tmp_path):
    path = write(
        tmp_path / "a.tif", RNG.integers(0, 90, (2, 5, 2, 30, 40), dtype=np.uint16), "TZCYX"
    )
    mapping = AxisMapping("TZCYX", "TZCYX")
    strip = channel_strip(path, "a.tif", review_for("a.tif", mapping))
    assert (strip.z, strip.t, len(strip.panels)) == (2, 0, 2)  # middle of 5 Z, first T
    assert "z=2, c=1, t=0" in strip.panels[1].plane.caption
    again = channel_strip(path, "a.tif", review_for("a.tif", mapping), StripOptions(z=4, t=1))
    assert (again.z, again.t) == (4, 1)


def test_the_channels_are_never_guessed_from_the_shape(tmp_path):
    # The first dimension has length 3, but the user confirmed it is Z, so there is one channel.
    data = RNG.integers(0, 90, (3, 30, 40), dtype=np.uint16)
    path = write(tmp_path / "zyx.tif", data, "ZYX")
    mapping = AxisMapping("ZYX", "ZYX")
    strip = channel_strip(path, "zyx.tif", review_for("zyx.tif", mapping))
    assert len(strip.panels) == 1 and strip.z == 1 and strip.panels[0].c == 0
    assert np.array_equal(strip.panels[0].plane.pixels, data[1])


@pytest.mark.parametrize("case", ["no_review", "file_not_in_review", "axis_error"])
def test_a_strip_is_refused_while_the_roles_are_not_confirmed(tmp_path, case):
    path, _ = three_channels(tmp_path)
    mapping = AxisMapping("CYX", "CYX")
    review = {
        "no_review": None,
        "file_not_in_review": review_for("other.tif", mapping),
        "axis_error": review_for(
            "cyx.tif", mapping, issues=[error(AXES_YX_SWAPPED, "swapped", ["cyx.tif"])]
        ),
    }[case]
    with pytest.raises(PreviewRefused) as refused:
        channel_strip(path, "cyx.tif", review)
    assert refused.value.code == ROLES_NOT_CONFIRMED
    assert "guessed" in str(refused.value)


def test_a_file_that_changed_since_its_fingerprint_is_refused(tmp_path):
    path, data = three_channels(tmp_path)
    mapping = AxisMapping("CYX", "CYX")
    review = review_for("cyx.tif", mapping)
    seen = Fingerprint.of(path)
    assert channel_strip(path, "cyx.tif", review, StripOptions(expected=seen)).panels
    write(path, data[:, :70], "CYX")
    with pytest.raises(PreviewRefused) as refused:
        channel_strip(path, "cyx.tif", review, StripOptions(expected=seen))
    assert refused.value.code == FILE_CHANGED


def test_the_cache_serves_the_same_content_and_rereads_a_changed_file(tmp_path, monkeypatch):
    path, data = three_channels(tmp_path)
    review = review_for("cyx.tif", AxisMapping("CYX", "CYX"))
    reads = []
    original = preview.read_source_plane
    monkeypatch.setattr(
        preview, "read_source_plane", lambda *a, **k: reads.append(k["c"]) or original(*a, **k)
    )
    options = StripOptions(cache=PreviewCache())
    channel_strip(path, "cyx.tif", review, options)
    channel_strip(path, "cyx.tif", review, options)
    assert reads == [0, 1, 2]  # the second strip read nothing
    write(path, data[:, :70], "CYX")
    channel_strip(path, "cyx.tif", review, options)
    assert reads == [0, 1, 2, 0, 1, 2] and len(options.cache) == 6


def test_the_cache_drops_the_least_recently_used_plane():
    plane = preview.ReducedPlane(np.zeros((2, 2), np.uint8), 1, (2, 2), None, None, None)
    cache = PreviewCache(size=2)
    cache.put("a", plane)
    cache.put("b", plane)
    assert cache.get("a") is plane  # a is now the most recent
    cache.put("c", plane)
    assert cache.get("b") is None and cache.get("a") is plane and len(cache) == 2


# masks ------------------------------------------------------------------------------------


def test_a_mask_is_reduced_with_the_step_of_its_image_and_labels_are_untouched(tmp_path):
    image = write(tmp_path / "i.tif", RNG.integers(0, 900, (900, 1100), dtype=np.uint16), "YX")
    labels = RNG.integers(0, 40, (900, 1100), dtype=np.uint16)
    mask = write(tmp_path / "m.tif", labels, "YX")
    mapping = AxisMapping("YX", "YX")
    shown = read_plane(image, mapping, max_side=THUMBNAIL_SIDE)
    thumb = mask_thumbnail(mask, mapping, shown)
    assert thumb.step == shown.step and thumb.pixels.shape == shown.pixels.shape
    assert np.array_equal(thumb.pixels, labels[:: shown.step, :: shown.step])
    assert set(np.unique(thumb.pixels)) <= set(np.unique(labels))  # nothing interpolated


def test_a_mask_of_another_size_is_refused_not_resampled(tmp_path):
    mapping = AxisMapping("YX", "YX")
    image = write(tmp_path / "i.tif", np.zeros((900, 1100), np.uint16), "YX")
    mask = write(tmp_path / "m.tif", np.zeros((450, 550), np.uint16), "YX")
    shown = read_plane(image, mapping, max_side=THUMBNAIL_SIDE)
    with pytest.raises(PreviewRefused) as refused:
        mask_thumbnail(mask, mapping, shown)
    assert refused.value.code == MASK_SIZE_DIFFERS
