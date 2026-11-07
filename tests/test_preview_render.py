"""Display panels and PNGs: flat, non-finite and constant images are shown as what they are."""

import struct
import zlib

import numpy as np
import pytest

from morphobricks_preprocessing.preview_render import (
    BINARY,
    FLAT,
    INSTANCES,
    MAGENTA,
    NO_FINITE,
    NORMAL,
    OverlayRefused,
    display_panel,
    mask_overlay_png,
    png_bytes,
    strip_png,
)


def decode(png):
    """Decode an 8-bit grey or RGB PNG written with filter 0 (the encoder's own)."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(png):
        (size,) = struct.unpack(">I", png[pos : pos + 4])
        kind, data = png[pos + 4 : pos + 8], png[pos + 8 : pos + 8 + size]
        assert struct.unpack(">I", png[pos + 8 + size : pos + 12 + size])[0] == zlib.crc32(
            kind + data
        )
        chunks[kind] = chunks.get(kind, b"") + data
        pos += 12 + size
    width, height, depth, colour = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    assert depth == 8
    channels = 1 if colour == 0 else 3
    raw = np.frombuffer(zlib.decompress(chunks[b"IDAT"]), np.uint8).reshape(
        height, 1 + width * channels
    )
    assert not raw[:, 0].any()
    body = raw[:, 1:]
    return body.reshape(height, width) if channels == 1 else body.reshape(height, width, 3)


def test_a_ramp_gets_percentile_contrast_in_float32_order():
    ramp = np.tile(np.arange(1000, dtype=np.uint16), (4, 1))
    panel = display_panel(ramp)
    assert panel.kind == NORMAL and panel.note == "" and panel.nonfinite == 0
    assert 0 < panel.low < 10 and 990 < panel.high < 999  # p0.5 and p99.5, not min and max
    assert panel.pixels[0, 0] == 0 and panel.pixels[0, -1] == 255 and panel.pixels.dtype == np.uint8


def test_a_flat_image_is_mid_grey_with_its_value_in_the_label_not_zeros():
    panel = display_panel(np.full((8, 8), 1234, np.uint16))
    assert panel.kind == FLAT and (panel.pixels == 128).all()
    assert panel.note == "flat: every finite value is 1234" and panel.low is None
    zeros = display_panel(np.zeros((8, 8), np.uint16))
    assert zeros.kind == FLAT and "is 0" in zeros.note and (zeros.pixels == 128).all()


def test_an_all_nan_image_is_magenta_and_says_so():
    panel = display_panel(np.full((6, 7), np.nan, np.float32))
    assert panel.kind == NO_FINITE and panel.note == "no finite values" and panel.nonfinite == 42
    assert (panel.pixels == MAGENTA).all() and panel.pixels.shape == (6, 7, 3)
    assert display_panel(np.full((3, 3), np.inf)).kind == NO_FINITE


def test_nan_and_inf_pixels_are_magenta_and_do_not_move_the_contrast():
    plane = np.tile(np.linspace(0, 100, 50, dtype=np.float32), (10, 1))
    clean = display_panel(plane)
    plane[3, 3], plane[4, 4], plane[5, 5] = np.nan, np.inf, -np.inf
    panel = display_panel(plane)
    assert (panel.low, panel.high) == pytest.approx((clean.low, clean.high), rel=0.05)
    assert panel.nonfinite == 3 and "3 non-finite pixels shown in magenta" in panel.note
    assert (panel.pixels[3, 3] == MAGENTA).all() and (panel.pixels[4, 4] == MAGENTA).all()
    assert panel.pixels.shape == (10, 50, 3)


def test_a_flat_image_with_nan_keeps_both_statements():
    plane = np.full((5, 5), 7.0, np.float32)
    plane[0, 0] = np.nan
    panel = display_panel(plane)
    assert panel.kind == FLAT and panel.note == (
        "flat: every finite value is 7; 1 non-finite pixels shown in magenta"
    )
    assert tuple(panel.pixels[0, 0]) == MAGENTA and tuple(panel.pixels[1, 1]) == (128, 128, 128)


def test_a_plane_that_is_mostly_one_value_still_shows_its_few_others():
    plane = np.zeros((50, 50), np.uint16)
    plane[0, 0] = 900
    panel = display_panel(plane)
    assert panel.kind == NORMAL and panel.pixels[0, 0] == 255 and panel.pixels[1, 1] == 0


def test_float64_values_beyond_float32_are_finite_not_infinite():
    plane = np.array([[0.0, 1e300], [1e10, 5e39]])
    panel = display_panel(plane)
    assert panel.nonfinite == 0 and panel.kind == NORMAL


def test_a_panel_needs_a_non_empty_plane_of_two_dimensions():
    for bad in (np.zeros((3,)), np.zeros((2, 2, 2)), np.zeros((0, 4))):
        with pytest.raises(ValueError, match="display panel"):
            display_panel(bad)


def test_the_png_round_trips_grey_and_colour_and_refuses_other_arrays():
    grey = np.arange(24, dtype=np.uint8).reshape(4, 6)
    assert np.array_equal(decode(png_bytes(grey)), grey)
    colour = np.arange(72, dtype=np.uint8).reshape(4, 6, 3)
    assert np.array_equal(decode(png_bytes(colour)), colour)
    for bad in (grey.astype(np.uint16), grey.astype(np.float32), np.zeros((2, 2, 4), np.uint8)):
        with pytest.raises(ValueError, match="8-bit"):
            png_bytes(bad)


def test_a_strip_puts_the_panels_side_by_side_and_upgrades_grey_beside_colour():
    one = display_panel(np.arange(20, dtype=np.uint16).reshape(4, 5))
    flat = display_panel(np.full((4, 5), 3.0, np.float32))
    assert decode(strip_png([one, flat])).shape == (4, 5 + 4 + 5)
    bad = display_panel(np.array([[np.nan, 1.0, 2.0, 3.0, 4.0]] * 4, np.float32))
    mixed = decode(strip_png([one, bad]))
    assert mixed.shape == (4, 14, 3) and tuple(mixed[0, 5]) == (255, 255, 255)
    with pytest.raises(ValueError, match="one height"):
        strip_png([one, display_panel(np.arange(30, dtype=np.uint16).reshape(6, 5))])
    with pytest.raises(ValueError, match="at least one"):
        strip_png([])


def labels_and_panel():
    labels = np.zeros((20, 20), np.uint16)
    labels[3:9, 3:9] = 7
    labels[11:17, 11:17] = 12
    return labels, display_panel(np.tile(np.arange(20, dtype=np.uint16), (20, 1)))


def test_instances_are_outlines_and_binary_is_one_tint():
    labels, panel = labels_and_panel()
    outlined = decode(mask_overlay_png(panel, labels, INSTANCES))
    assert tuple(outlined[3, 3]) != tuple(outlined[5, 5])  # the edge is coloured, the inside is not
    assert (outlined[5, 5] == outlined[5, 5][0]).all() and outlined[0, 0, 0] == outlined[0, 0, 1]
    tinted = decode(mask_overlay_png(panel, labels, BINARY))
    base = panel.pixels[5, 5]
    expected = np.rint(0.6 * base + 0.4 * np.array([255, 60, 60])).astype(np.uint8)
    assert tuple(tinted[5, 5]) == tuple(expected)  # a blend, so the image stays visible
    assert tuple(tinted[0, 0]) == (0, 0, 0)  # outside the mask the image is as it was


def test_the_outline_colour_follows_the_label_value_and_does_not_renumber():
    labels, panel = labels_and_panel()
    shifted = labels.copy()
    shifted[shifted == 7] = 1008
    first = decode(mask_overlay_png(panel, labels, INSTANCES))
    second = decode(mask_overlay_png(panel, shifted, INSTANCES))
    assert tuple(first[3, 3]) != tuple(second[3, 3])  # 7 and 1008 are different labels
    assert tuple(first[11, 11]) == tuple(second[11, 11])  # 12 is still 12
    assert labels.max() == 12  # the caller's labels are not changed


@pytest.mark.parametrize("representation", [None, "", "semantic", "Instances"])
def test_an_undeclared_representation_is_refused_never_inferred(representation):
    labels, panel = labels_and_panel()
    with pytest.raises(OverlayRefused, match="never inferred"):
        mask_overlay_png(panel, labels, representation)


def test_a_mask_that_is_not_integer_or_not_the_panels_size_is_refused():
    labels, panel = labels_and_panel()
    with pytest.raises(OverlayRefused, match="integer"):
        mask_overlay_png(panel, labels.astype(np.float32), INSTANCES)
    with pytest.raises(OverlayRefused, match="not resampled"):
        mask_overlay_png(panel, labels[:10], BINARY)
    assert mask_overlay_png(panel, labels > 0, BINARY).startswith(b"\x89PNG")
