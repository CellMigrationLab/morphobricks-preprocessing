"""The fixed rule ``he_tissue_v1`` (design he-tissue-mask §1, §7; Spec ADR 0005 BM15).

- the colour maths is normalization's, reused exactly: ``E``, ``H`` and ``OD`` are
  ``np.array_equal`` to calling ``stain.to_scalar`` and ``optical_density`` directly, and the
  modules make no ``log10``, ``log``, ``separate_stains`` or ``rgb2hed`` call;
- every pixel of the §1 boundary table gives the component §1's comparisons specify (B4);
- the clean-up boundaries (review w F5): 8-connected pieces, the image border, pieces of exactly
  14/15/16 and 999/1000/1001 px, the disks, and the order of the clean-up steps.

The morphology is checked against small reference implementations written here with plain
loops (``_ref_*``), not against SciPy.
"""

from __future__ import annotations

import ast
import math
from collections import deque
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("skimage")

from morphobricks_normalization import stain  # noqa: E402
from morphobricks_normalization.optical_density import check_white, optical_density  # noqa: E402
from morphobricks_normalization.scalar_inputs import rgb_yxc  # noqa: E402

from he_tissue_tree import synthetic_he  # noqa: E402
from morphobricks_preprocessing import he_tissue_rule as rule_module  # noqa: E402
from morphobricks_preprocessing.he_tissue_rule import (  # noqa: E402
    clean_up,
    disk,
    open_disk,
    pixel_components,
    rule_record,
    tissue_components,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "morphobricks_preprocessing"
RULE = rule_record("he_tissue_v1", hed_source="skimage.color.hed_from_rgb", hed_sha256="0" * 64)


# Reference morphology (plain loops) -------------------------------------------------------------


def _ref_disk(r):
    return [
        (dy, dx) for dy in range(-r, r + 1) for dx in range(-r, r + 1) if dy * dy + dx * dx <= r * r
    ]


def _ref_dilate(mask, r):
    out = np.zeros_like(mask)
    ys, xs = np.nonzero(mask)
    for y, x in zip(ys, xs, strict=True):
        for dy, dx in _ref_disk(r):
            if 0 <= y + dy < mask.shape[0] and 0 <= x + dx < mask.shape[1]:
                out[y + dy, x + dx] = True
    return out


def _ref_erode_clipped(mask, r):
    out = np.zeros_like(mask)
    for y in range(mask.shape[0]):
        for x in range(mask.shape[1]):
            inside = [
                mask[y + dy, x + dx]
                for dy, dx in _ref_disk(r)
                if 0 <= y + dy < mask.shape[0] and 0 <= x + dx < mask.shape[1]
            ]
            out[y, x] = all(inside)
    return out


def _ref_open(mask, r):
    return _ref_dilate(_ref_erode_clipped(mask, r), r)


def _ref_pieces(mask):
    seen, pieces = np.zeros_like(mask), []
    for y, x in zip(*np.nonzero(mask), strict=True):
        if seen[y, x]:
            continue
        piece, queue = [], deque([(y, x)])
        seen[y, x] = True
        while queue:
            cy, cx = queue.popleft()
            piece.append((cy, cx))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1]:
                        if mask[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            queue.append((ny, nx))
        pieces.append(piece)
    return pieces


def _ref_remove(mask, size):
    out = np.zeros_like(mask)
    for piece in _ref_pieces(mask):
        if len(piece) >= size:
            for y, x in piece:
                out[y, x] = True
    return out


# Synthetic arrays for one component ------------------------------------------------------------


def _arrays(mask, component):
    """E, H and OD where ``mask`` meets only ``component``'s pixel rule (elsewhere: none)."""
    shape = mask.shape
    E, H = np.zeros(shape, np.float32), np.zeros(shape, np.float32)
    od = np.full((*shape, 3), 0.5)
    if component == "nuclei":
        H[mask] = 0.1
    elif component == "red_cells":
        E[mask] = 0.1
        od[mask] = (0.1, 0.6, 0.3)
    else:
        od[mask] = 0.0
    return E, H, od


def _component(mask, component):
    return getattr(tissue_components(*_arrays(mask, component), RULE), component)


def _bar(shape, row, col, length):
    mask = np.zeros(shape, bool)
    mask[row, col : col + length] = True
    return mask


def _open_region(n, shape=(60, 60)):
    """A region of exactly ``n`` px (980 to 1008) that an opening by disk(2) leaves unchanged."""
    core = np.zeros(shape, bool)
    core[10:37, 10:38] = True
    k = n - int(_ref_dilate(core, 2).sum())
    core[9, 12 : 12 + k] = True
    return _ref_dilate(core, 2)


# Colour maths: normalization's, exactly --------------------------------------------------------


def test_e_h_and_od_are_normalizations_arrays_exactly():
    rgb, _ = synthetic_he()
    pixels = np.ascontiguousarray(np.moveaxis(rgb, -1, 0))
    pixels[0, 0, 0] = 0  # a channel at 0: floored
    from morphobricks_preprocessing.he_tissue_mask import _optical_density

    od, floored = _optical_density(pixels)
    expected_od, expected_floored = optical_density(
        rgb_yxc(pixels, "CYX"), check_white((255, 255, 255))
    )
    assert np.array_equal(od, expected_od)  # only the array
    assert floored == expected_floored and floored > 0
    assert od[0, 0, 0] == math.log10(255)
    scalar = stain.to_scalar(pixels, axes="CYX", preset="he_eosin")
    assert scalar.image.dtype == np.float32 and scalar.exclusion.dtype == np.float32


def test_optical_density_must_return_the_pair(monkeypatch):
    from morphobricks_preprocessing import he_tissue_mask

    pixels = np.full((3, 4, 4), 200, np.uint8)
    monkeypatch.setattr(he_tissue_mask, "optical_density", lambda rgb, white: rgb.astype(float))
    with pytest.raises(TypeError, match="pair"):
        he_tissue_mask._optical_density(pixels)
    monkeypatch.setattr(
        he_tissue_mask, "optical_density", lambda rgb, white: (rgb.astype(np.float32), 0.0)
    )
    with pytest.raises(TypeError, match="float64"):
        he_tissue_mask._optical_density(pixels)


@pytest.mark.parametrize("name", ["he_tissue_mask.py", "he_tissue_rule.py"])
def test_the_modules_compute_no_colour_maths_of_their_own(name):
    tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    used |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not used & {"log10", "log", "log2", "separate_stains", "rgb2hed", "hed_from_rgb"}
    strings = {
        node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and node.value
    }
    assert "hed_from_rgb" not in strings  # looked up only by the preset's own name for it


def test_the_hed_matrix_is_hashed_from_the_presets_name_for_it():
    import hashlib

    from skimage import color

    from morphobricks_preprocessing.he_tissue_mask import hed_matrix

    source, digest = hed_matrix()
    assert source == f"skimage.color.{stain.PRESETS['he_eosin'].matrix}"
    expected = np.ascontiguousarray(color.hed_from_rgb, dtype="<f8").tobytes()
    assert digest == hashlib.sha256(expected).hexdigest()


# The rule record ------------------------------------------------------------------------------


def test_the_rule_record_is_the_designs_and_provisional():
    assert RULE["id"] == "he_tissue" and RULE["version"] == 1
    assert RULE["status"] == "provisional"
    assert [c["name"] for c in RULE["components"]] == ["nuclei", "red_cells", "background"]
    # Every threshold and size is pinned here: a change no 8-bit image can show (the
    # background's 0.08 to 0.0801, say; he_tissue_tree.ON_THRESHOLD) still fails (issue #345).
    assert RULE["components"][0]["pixel"] == [["H", ">=", 0.05]]
    assert RULE["components"][0]["cleanup"] == [
        ["remove_pieces_smaller_than_px", 15],
        ["dilate_disk_radius_px", 2],
    ]
    assert RULE["components"][1]["cleanup"] == [
        ["remove_pieces_smaller_than_px", 15],
        ["dilate_disk_radius_px", 1],
    ]
    assert RULE["components"][2]["pixel"] == [["max(OD_R,OD_G,OD_B)", "<", 0.08]]
    assert RULE["components"][1]["pixel"] == [
        ["E", ">=", 0.06],
        ["H", "<", 0.03],
        ["OD_G-OD_R", ">=", 0.40],
        ["OD_B-OD_R", ">=", 0.10],
    ]
    assert RULE["components"][2]["cleanup"] == [
        ["open_disk_radius_px", 2],
        ["remove_pieces_smaller_than_px", 1000],
    ]
    assert RULE["connectivity"] == 8 and RULE["disk"] == "x*x + y*y <= r*r"
    with pytest.raises(ValueError, match="known rules are he_tissue_v1"):
        rule_record("he_tissue_v2", hed_source="x", hed_sha256="0" * 64)


# B4: the boundary table of §1 -----------------------------------------------------------------

# (R, G, B): (nuclei, red_cells, background), as §1's comparisons give them. The red-cell colour
# terms are ratios (R' >= 2.5119 G', R' >= 1.2589 B'); E and H then decide on separate_stains'
# own floor.
BOUNDARY = {
    (253, 101, 101): (False, False, False),  # OD_G - OD_R = 0.3988 < 0.40
    (253, 100, 100): (False, False, False),  # colour terms met, E = 0.044 < 0.06
    (211, 84, 84): (False, False, False),  # 0.40 + 3.2e-6, E = 0.039 < 0.06
    (211, 85, 85): (False, False, False),  # 0.3949 < 0.40
    (200, 79, 158): (False, True, False),  # OD_B - OD_R = 0.1024, E = 0.062, H = 0.007
    (200, 79, 159): (False, False, False),  # OD_B - OD_R = 0.0996 < 0.10
    (141, 50, 112): (False, False, False),  # OD_B - OD_R = 0.10 + 1.1e-6, H = 0.037 >= 0.03
    (141, 50, 113): (False, False, False),  # OD_B - OD_R = 0.0961
    (0, 0, 0): (True, False, False),  # floored: both colour differences 0; H = 1.21
    (3, 0, 0): (False, True, False),  # G, B floored to 1: log10 3 = 0.477
    (2, 0, 0): (False, False, False),  # log10 2 = 0.301 < 0.40
    (255, 0, 0): (False, True, False),
    (213, 213, 213): (False, False, True),  # OD(213) = 0.0782 < 0.08
    (212, 213, 213): (False, False, False),  # OD(212) = 0.0802
    (255, 255, 255): (False, False, True),  # pure white
    (255, 20, 147): (False, True, False),  # saturated eosin pink
}


def test_every_boundary_pixel_gives_the_component_section_1_specifies():
    colours = list(BOUNDARY)
    pixels = np.ascontiguousarray(np.array(colours, np.uint8).T.reshape(3, 1, len(colours)))
    scalar = stain.to_scalar(pixels, axes="CYX", preset="he_eosin")
    od, _ = optical_density(rgb_yxc(pixels, "CYX"), check_white((255, 255, 255)))
    found = pixel_components(scalar.image, scalar.exclusion, od, RULE)
    for index, colour in enumerate(colours):
        got = tuple(bool(found[name][0, index]) for name in ("nuclei", "red_cells", "background"))
        assert got == BOUNDARY[colour], colour


def test_the_colour_terms_are_the_8_bit_ratios_of_section_1():
    def terms(r, g, b):
        od = -np.log10(np.maximum(np.array([r, g, b], float), 1) / 255)
        return od[1] - od[0] >= 0.40, od[2] - od[0] >= 0.10

    assert terms(253, 100, 100) == (True, True) and terms(253, 101, 101)[0] is np.False_
    assert terms(200, 79, 158)[1] and not terms(200, 79, 159)[1]
    assert terms(100, 0, 79)[1] and not terms(100, 0, 80)[1]
    assert terms(3, 0, 0) == (True, True) and terms(2, 0, 0) == (False, True)


def test_a_comparison_is_made_in_float64_never_on_a_float32_threshold():
    # 0.05 in float32 is 0.0500000007; the float32 value just below 0.05 is out, 0.05's float32
    # neighbour above is in: the threshold is never rounded.
    below = np.nextafter(np.float32(0.05), np.float32(0))
    H = np.array([[below, np.float32(0.05)]], np.float32)
    E = np.zeros_like(H)
    od = np.full((1, 2, 3), 0.5)
    found = pixel_components(E, H, od, RULE)["nuclei"]
    assert found.tolist() == [[float(below) >= 0.05, float(np.float32(0.05)) >= 0.05]]


# F5: the clean-up boundaries ------------------------------------------------------------------


def test_the_disks_are_x2_plus_y2_at_most_r2():
    for r in (1, 2):
        expected = np.zeros((2 * r + 1, 2 * r + 1), bool)
        for dy, dx in _ref_disk(r):
            expected[dy + r, dx + r] = True
        assert np.array_equal(disk(r), expected)
    assert disk(1).sum() == 5 and disk(2).sum() == 13


@pytest.mark.parametrize(("component", "radius"), [("nuclei", 2), ("red_cells", 1)])
def test_the_dilation_is_the_disk_pixel_by_pixel(component, radius):
    mask = np.zeros((40, 40), bool)
    mask[18:23, 18:23] = True  # 25 px: kept
    found = _component(mask, component)
    assert np.array_equal(found, _ref_dilate(mask, radius))


@pytest.mark.parametrize("component", ["nuclei", "red_cells"])
def test_fewer_than_15_px_is_removed_and_exactly_15_is_kept(component):
    radius = {"nuclei": 2, "red_cells": 1}[component]
    for length, kept in ((14, False), (15, True), (16, True)):
        mask = _bar((20, 40), 10, 5, length)
        found = _component(mask, component)
        assert np.array_equal(found, _ref_dilate(mask, radius) if kept else ~np.ones_like(mask))


@pytest.mark.parametrize("component", ["nuclei", "red_cells"])
def test_two_pieces_touching_only_diagonally_are_one_8_connected_piece(component):
    mask = np.zeros((30, 30), bool)
    mask[5, 5:13] = True  # 8 px
    mask[6, 13:21] = True  # 8 px, touching (5, 12) only diagonally
    assert len(_ref_pieces(mask)) == 1
    assert _component(mask, component).any()  # 16 px >= 15: kept together
    apart = mask.copy()
    apart[6, 13:21], apart[7, 14:22] = False, True
    assert not _component(apart, component).any()  # two pieces of 8: both removed


def test_a_piece_at_the_border_is_measured_and_dilated_clipped_never_wrapped():
    mask = np.zeros((20, 20), bool)
    mask[0, 0:15] = True  # 15 px on the top border: kept
    found = _component(mask, "nuclei")
    assert np.array_equal(found, _ref_dilate(mask, 2))
    assert not found[-2:].any() and not found[:, -3:].any()  # nothing wrapped round
    corner = np.zeros((20, 20), bool)
    corner[0, 0:14] = True  # 14 px: removed, whatever the border
    assert not _component(corner, "nuclei").any()


@pytest.mark.parametrize(("size", "kept"), [(999, False), (1000, True), (1001, True)])
def test_background_fewer_than_1000_px_after_the_opening_is_kept_as_tissue(size, kept):
    region = _open_region(size)
    assert region.sum() == size and np.array_equal(_ref_open(region, 2), region)
    found = _component(region, "background")
    assert np.array_equal(found, region if kept else np.zeros_like(region))


def test_the_opening_is_the_disk_clipped_by_the_border():
    mask = np.zeros((30, 30), bool)
    mask[0:12, 0:12] = True  # in the corner
    mask[20:24, 20:24] = True  # 4 x 4: the opening by disk(2) keeps only its open part
    mask[15, 0:30] = True  # one pixel wide: removed by the opening
    assert np.array_equal(open_disk(mask, 2), _ref_open(mask, 2))
    opened = open_disk(mask, 2)
    assert opened[0, 0:10].all() and opened[0:10, 0].all()  # not eaten from outside the image
    assert not opened[11, 11]  # the inner corner is
    assert not open_disk(mask, 2)[15].any()


def test_swapping_two_clean_up_steps_changes_the_result():
    speck = np.zeros((20, 20), bool)
    speck[8:11, 8:11] = True  # 9 px: removed before the dilation could grow it
    nuclei = RULE["components"][0]["cleanup"]
    assert not clean_up(speck, nuclei).any()
    assert clean_up(speck, nuclei[::-1]).any()
    assert not _component(speck, "nuclei").any()
    region = _open_region(990)  # unchanged by the opening
    region[25, 40:57] = True  # a one-pixel line of 17 px: 1007 px before the opening
    background = RULE["components"][2]["cleanup"]
    assert region.sum() == 1007
    opened = _ref_open(region, 2)
    assert opened.sum() == 991  # the line is gone but for the pixel next to the region
    assert not clean_up(region, background).any()  # opened to 991 px, then removed
    assert np.array_equal(clean_up(region, background[::-1]), opened)  # 1007 px: kept, opened


# The synthetic H&E image of §7 ----------------------------------------------------------------


def test_the_synthetic_image_gives_each_component_pixel_by_pixel():
    rgb, parts = synthetic_he()
    pixels = np.ascontiguousarray(np.moveaxis(rgb, -1, 0))
    scalar = stain.to_scalar(pixels, axes="CYX", preset="he_eosin")
    od, _ = optical_density(rgb_yxc(pixels, "CYX"), check_white((255, 255, 255)))
    found = tissue_components(scalar.image, scalar.exclusion, od, RULE)
    assert np.array_equal(found.nuclei, _ref_dilate(parts["nucleus"], 2))  # the speck: removed
    assert np.array_equal(found.red_cells, _ref_dilate(parts["red_cell"], 1))
    assert np.array_equal(found.background, _ref_open(parts["big_white"], 2))  # 900 px: kept
    assert parts["big_white"].sum() == 1600 and parts["small_white"].sum() == 900
    assert parts["speck"].sum() == 9
    assert np.array_equal(found.keep, ~(found.nuclei | found.red_cells | found.background))
    assert found.keep[parts["small_white"]].all() and found.keep[parts["speck"]].all()


def test_the_module_reads_no_pixel_size():
    text = (SRC / "he_tissue_rule.py").read_text(encoding="utf-8")
    assert "pixel_sizes" not in text and "micrometer" not in text.lower()
    assert rule_module.CONNECTIVITY == 8
