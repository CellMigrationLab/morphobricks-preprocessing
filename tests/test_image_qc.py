"""The image QC metrics of one plane (contract §19): formulas, invariances, blocks.

Known values are worked out by hand on tiny planes; the synthetic tests check the direction
each metric moves under a known distortion (blur, brightness, emptiness, uneven
illumination), and that a plane read in blocks gives the whole plane's values up to the
documented edge effect.
"""

import math

import numpy as np
import pytest
from morphobricks_normalization.histogram import compute_plane_histogram
from scipy.ndimage import gaussian_filter

from morphobricks_preprocessing.image_qc import (
    MEASURED,
    METRICS,
    NO_FINITE,
    NON_FINITE,
    OFFSETS,
    PlaneQc,
)
from morphobricks_preprocessing.image_qc_sums import BlockSums, Window, valid_window

HISTOGRAM_METRICS = ("otsu_threshold", "otsu_separation", "foreground_fraction", "entropy")
SHAPE = (
    "normalized_variance",
    "tenengrad",
    "laplacian_variance",
    "correlation_1",
    "correlation_4",
    "correlation_16",
    "local_focus_median",
    "local_focus_p10",
    "tile_mean_cv",
)
NEIGHBOUR = ("tenengrad", "laplacian_variance", "correlation_1", "correlation_4", "correlation_16")
"""The metrics of neighbour pairs: a block edge leaves some pairs out."""


def measure(pixels, block=None):
    """Measure a plane whole, or in blocks of ``block`` (rows, columns)."""
    qc = PlaneQc(pixels.shape, pixels.dtype)
    rows, cols = block or pixels.shape
    for y in range(0, pixels.shape[0], rows):
        for x in range(0, pixels.shape[1], cols):
            qc.add(y, x, pixels[y : y + rows, x : x + cols])
    return qc.finish(compute_plane_histogram(pixels))


def cells(seed=0, side=256, count=40, noise=20.0):
    """A field of bright round cells on a dark noisy background, as uint16."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[:side, :side]
    image = np.full((side, side), 200.0)
    for cy, cx, r in zip(
        *rng.integers(8, side - 8, (2, count)), rng.integers(3, 9, count), strict=True
    ):
        image[(y - cy) ** 2 + (x - cx) ** 2 <= r * r] += 1500.0
    image += rng.normal(0.0, noise, image.shape)
    return np.clip(image, 0, 65535).astype(np.uint16)


def reference(pixels):
    """The pixel metrics computed directly from their definitions (independent code)."""
    p = pixels.astype(np.float64)
    mean = p.mean()
    gx, gy = (p[:, 2:] - p[:, :-2]) / 2, (p[2:] - p[:-2]) / 2
    lap = p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] - 4 * p[1:-1, 1:-1]
    found = {
        "normalized_variance": p.var() / mean**2,
        "tenengrad": ((gx**2).mean() + (gy**2).mean()) / mean**2,
        "laplacian_variance": lap.var() / mean**2,
    }
    for k in (1, 4, 16):
        cx = np.corrcoef(p[:, :-k].ravel(), p[:, k:].ravel())[0, 1]
        cy = np.corrcoef(p[:-k].ravel(), p[k:].ravel())[0, 1]
        found[f"correlation_{k}"] = (cx + cy) / 2
    rows = [(i * p.shape[0]) // 8 for i in range(9)]
    cols = [(j * p.shape[1]) // 8 for j in range(9)]
    tiles = [p[rows[i] : rows[i + 1], cols[j] : cols[j + 1]] for i in range(8) for j in range(8)]
    focus = np.array([t.var() for t in tiles]) / mean**2
    means = np.array([t.mean() for t in tiles])
    found["local_focus_median"] = np.median(focus)
    found["local_focus_p10"] = np.quantile(focus, 0.1)
    found["tile_mean_cv"] = means.std() / means.mean()
    return found


# Sums ---------------------------------------------------------------------------------------


def test_pair_and_window_sums_equal_brute_force_for_every_offset_and_window():
    rng = np.random.default_rng(1)
    offsets = [(0, 1), (0, 2), (1, 0), (2, 0), (1, 1), (1, -1), (0, 4), (4, 0), (3, -2)]
    for _ in range(150):
        height, width = (int(v) for v in rng.integers(1, 14, 2))
        pixels = rng.normal(size=(height, width))
        edges = sorted({0, *(int(v) for v in rng.integers(0, height, 2))})
        sums = BlockSums(pixels, 0.3, offsets, edges)
        d = pixels - 0.3
        assert math.isclose(sums.total, d.sum(), abs_tol=1e-9)
        for dy, dx in offsets:
            valid = valid_window((height, width), dy, dx)
            if valid.size == 0:
                continue
            y0 = int(rng.integers(valid.y0, valid.y1))
            x0 = int(rng.integers(valid.x0, valid.x1))
            window = Window(
                y0,
                int(rng.integers(y0 + 1, valid.y1 + 1)),
                x0,
                int(rng.integers(x0 + 1, valid.x1 + 1)),
            )
            part = d[window.y0 : window.y1, window.x0 : window.x1]
            moved = window.shifted(dy, dx)
            partner = d[moved.y0 : moved.y1, moved.x0 : moved.x1]
            assert math.isclose(sums.pair_sum(window, dy, dx), (part * partner).sum(), abs_tol=1e-9)
            assert math.isclose(sums.window_sum(window), part.sum(), abs_tol=1e-9)
            assert math.isclose(
                sums.window_sum(window, squared=True), (part**2).sum(), abs_tol=1e-9
            )


def test_every_metric_equals_its_definition_on_a_whole_plane():
    pixels = cells(seed=3, side=203)[:, :187]
    found, expected = measure(pixels), reference(pixels)
    assert found.status == MEASURED
    for name, value in expected.items():
        assert getattr(found, name) == pytest.approx(value, rel=1e-9), name


# Known values on tiny planes ----------------------------------------------------------------


def test_normalized_variance_is_the_variance_over_the_mean_squared():
    found = measure(np.array([[1, 3], [1, 3]], dtype=np.uint8))
    assert found.normalized_variance == pytest.approx(1 / 4)  # mean 2, variance 1


def test_tenengrad_is_the_mean_squared_central_difference_over_the_mean_squared():
    found = measure(np.array([[1, 2, 3]] * 3, dtype=np.uint8))
    # x: (3 - 1) / 2 = 1 at each of 3 pixels; y: 0; mean 2.
    assert found.tenengrad == pytest.approx((1.0 + 0.0) / 4)


def test_laplacian_variance_is_the_variance_of_the_four_neighbour_laplacian():
    pixels = np.array([[1, 1, 1], [1, 5, 1], [1, 1, 1], [1, 1, 1]], dtype=np.uint8)
    # Inner pixels: 4 - 20 = -16 and (5 + 1 + 1 + 1) - 4 = 4; variance 100; mean 16 / 12.
    assert measure(pixels).laplacian_variance == pytest.approx(100 / (16 / 12) ** 2)


def test_correlations_of_a_checkerboard_and_lags_longer_than_the_plane():
    pixels = (np.indices((4, 4)).sum(axis=0) % 2 + 1).astype(np.uint8)
    found = measure(pixels)
    assert found.correlation_1 == pytest.approx(-1.0)
    assert found.correlation_4 is None and found.correlation_16 is None  # no pairs that far


def test_otsu_threshold_fraction_separation_and_entropy_of_two_values():
    found = measure(np.array([[10, 10], [10, 20]], dtype=np.uint8))
    assert found.otsu_threshold == 10.0
    assert found.foreground_fraction == pytest.approx(0.25)
    assert found.otsu_separation == pytest.approx(1.0)  # two values: all variance is between
    # 10 falls in the first of 64 bins over 10-20 and 20 in the last.
    assert found.entropy == pytest.approx(-(0.75 * math.log2(0.75) + 0.25 * math.log2(0.25)))


def test_tile_metrics_of_two_halves():
    pixels = np.ones((16, 16), dtype=np.uint8)
    pixels[:, 8:] = 3
    found = measure(pixels)
    assert found.tile_mean_cv == pytest.approx(0.5)  # tile means 1 and 3: mean 2, std 1
    assert found.local_focus_median == 0.0 and found.local_focus_p10 == 0.0
    assert measure(pixels[:15]).tile_mean_cv is None  # under 2 pixels per tile side


def test_a_constant_plane_has_no_otsu_split_and_no_correlation():
    found = measure(np.full((32, 32), 7, dtype=np.uint16))
    assert found.status == MEASURED
    assert found.otsu_threshold is found.foreground_fraction is found.otsu_separation is None
    assert found.entropy == 0.0 and found.normalized_variance == 0.0
    assert found.correlation_1 is None


# Synthetic distortions ----------------------------------------------------------------------


def test_blur_lowers_focus_metrics_and_raises_the_short_lag_correlation():
    sharp = cells(seed=4, noise=5.0).astype(np.float64)
    series = [measure(gaussian_filter(sharp, sigma)) for sigma in (0.5, 1.0, 2.0, 4.0)]
    for name in ("tenengrad", "laplacian_variance", "local_focus_median", "local_focus_p10"):
        values = [getattr(found, name) for found in series]
        assert values == sorted(values, reverse=True) and len(set(values)) == 4, name
    values = [found.correlation_1 for found in series]
    assert values == sorted(values) and len(set(values)) == 4


@pytest.mark.parametrize("scale", [0.5, 2.0])
def test_brightness_scaling_leaves_every_shape_metric_unchanged(scale):
    pixels = cells(seed=5)
    base, scaled = measure(pixels), measure(pixels.astype(np.float64) * scale)
    for name in SHAPE:
        assert getattr(scaled, name) == pytest.approx(getattr(base, name), rel=1e-9), name


def test_uint16_and_float_copies_give_the_same_metrics():
    pixels = cells(seed=6)
    whole, copy = measure(pixels), measure(pixels.astype(np.float32) / np.float32(65535))
    for name in SHAPE:
        assert getattr(copy, name) == pytest.approx(getattr(whole, name), rel=1e-5), name
    # The histogram metrics depend on the binning (float: 10,000 equal bins), not the scale.
    assert copy.foreground_fraction == pytest.approx(whole.foreground_fraction, abs=0.01)
    assert copy.entropy == pytest.approx(whole.entropy, abs=0.05)


def test_doubling_an_integer_image_leaves_the_histogram_metrics_unchanged():
    pixels = cells(seed=7)
    base, doubled = measure(pixels), measure(pixels * np.uint16(2))
    assert doubled.otsu_threshold == 2 * base.otsu_threshold
    for name in ("foreground_fraction", "otsu_separation", "entropy"):
        assert getattr(doubled, name) == pytest.approx(getattr(base, name), rel=1e-12), name


def test_a_near_empty_field_has_almost_no_foreground():
    empty = cells(seed=8, count=0)
    empty[100:102, 100:102] = 3000  # one speck of debris
    assert measure(empty).foreground_fraction < 0.01
    assert measure(cells(seed=8)).foreground_fraction > 0.05


def test_otsu_on_noise_alone_splits_the_noise_with_a_low_separation():
    noise = cells(seed=9, count=0)
    found, field = measure(noise), measure(cells(seed=9))
    # Two-class Otsu always splits: on unimodal noise near the middle (documented), which
    # otsu_separation tells apart from a real foreground.
    assert 0.3 < found.foreground_fraction < 0.7
    assert found.otsu_separation < 0.7 < field.otsu_separation


def test_a_left_right_illumination_ramp_raises_tile_mean_cv():
    flat = np.random.default_rng(10).normal(1000.0, 50.0, (256, 256))
    ramp = flat * np.linspace(0.3, 1.0, flat.shape[1])[None, :]
    assert measure(flat).tile_mean_cv < 0.01 < 0.2 < measure(ramp).tile_mean_cv


# Blocks ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("block", [(64, 256), (50, 64), (256, 40), (17, 33)])
def test_blocks_give_the_whole_plane_values_up_to_the_edge_effect(block):
    pixels = cells(seed=11)
    whole, blocks = measure(pixels), measure(pixels, block)
    for name in SHAPE:
        if name.startswith("correlation"):  # pairs at the lag that cross an edge are left out
            expected = pytest.approx(getattr(whole, name), abs=0.05)
        else:
            expected = pytest.approx(getattr(whole, name), rel=0.05 if name in NEIGHBOUR else 1e-9)
        assert getattr(blocks, name) == expected, name
    for name in HISTOGRAM_METRICS:
        assert getattr(blocks, name) == getattr(whole, name)


def test_full_width_bands_give_the_whole_plane_values_except_pairs_across_band_edges():
    pixels = cells(seed=12)
    whole, bands = measure(pixels), measure(pixels, (128, 256))
    assert bands.correlation_1 != whole.correlation_1  # vertical pairs across the edge are out
    assert bands.correlation_1 == pytest.approx(whole.correlation_1, rel=1e-3)


def test_finish_refuses_when_the_blocks_do_not_cover_the_plane():
    pixels = cells(seed=13, side=32)
    qc = PlaneQc(pixels.shape, pixels.dtype)
    qc.add(0, 0, pixels[:16])
    with pytest.raises(ValueError, match="pixels"):
        qc.finish(compute_plane_histogram(pixels))


# Special planes -------------------------------------------------------------------------------


def test_non_finite_pixels_blank_the_pixel_metrics_and_keep_the_histogram_ones():
    pixels = cells(seed=14).astype(np.float32)
    pixels[3, 3] = np.nan
    found = measure(pixels, (64, 64))
    assert found.status == NON_FINITE
    assert all(getattr(found, name) is None for name in SHAPE)
    assert found.foreground_fraction is not None and found.entropy is not None


def test_a_plane_without_finite_pixels_has_no_metric():
    found = measure(np.full((8, 8), np.nan, dtype=np.float32))
    assert found.status == NO_FINITE
    assert all(getattr(found, name) is None for name in METRICS)


def test_a_plane_whose_mean_is_not_positive_has_no_ratio_metric():
    pixels = (cells(seed=15).astype(np.int32) - 5000).astype(np.int16)
    found = measure(pixels)
    for name in SHAPE:
        assert (getattr(found, name) is None) == (not name.startswith("correlation")), name


def test_the_offsets_cover_every_metric():
    assert {
        (0, 1),
        (0, 2),
        (0, 4),
        (0, 16),
        (1, 0),
        (2, 0),
        (4, 0),
        (16, 0),
        (1, 1),
        (1, -1),
    } == set(OFFSETS)
