import numpy as np
import pytest

from morphobricks_preprocessing import UnsupportedDtypeError, compute_plane_histogram
from morphobricks_preprocessing import histogram as histogram_module

rng = np.random.default_rng(42)


def test_uint16_default_is_one_bin_per_value():
    plane = rng.integers(100, 5000, (128, 128)).astype(np.uint16)
    hist = compute_plane_histogram(plane)
    assert hist.bin_count == 65_536
    assert (hist.binning_min, hist.binning_max) == (int(plane.min()), int(plane.max()))
    assert hist.is_exact
    width = hist.binning_max - hist.binning_min + 1
    expected = np.bincount(plane.ravel() - hist.binning_min, minlength=width)
    np.testing.assert_array_equal(hist.counts[:width], expected)
    assert hist.counts[width:].sum() == 0
    np.testing.assert_array_equal(hist.representatives[:width], np.arange(width) + hist.binning_min)


@pytest.mark.parametrize("bin_count", [1, 7, 1000, 4900, 8192])
def test_integer_bins_have_whole_number_contiguous_limits(bin_count):
    plane = np.arange(100, 5000, dtype=np.uint16)  # 4,900 distinct values
    hist = compute_plane_histogram(plane, bin_count)
    limits = [(a, b) for a, b in hist.integer_bin_limits() if a <= b]
    assert limits[0][0] == 100 and limits[-1][1] == 4999
    for (_, last), (first, _) in zip(limits, limits[1:], strict=False):
        assert first == last + 1  # no gaps, no overlaps
    widths = {b - a + 1 for a, b in limits}
    assert max(widths) - min(widths) <= 1
    # each value is counted in the bin whose limits contain it
    for (a, b), count in zip(hist.integer_bin_limits(), hist.counts, strict=True):
        assert count == max(0, b - a + 1)


def test_range_narrower_than_bin_count_is_exact():
    plane = rng.integers(0, 300, (64, 64)).astype(np.uint16)
    hist = compute_plane_histogram(plane, 1024)
    assert hist.is_exact and hist.max_representative_error == 0


def test_signed_integers_keep_negative_values():
    plane = rng.integers(-500, 500, (64, 64)).astype(np.int16)
    hist = compute_plane_histogram(plane)
    assert hist.binning_min == int(plane.min()) < 0
    assert hist.is_exact
    width = hist.binning_max - hist.binning_min + 1
    np.testing.assert_array_equal(
        hist.counts[:width], np.bincount(plane.ravel().astype(int) - hist.binning_min)
    )


@pytest.mark.parametrize("dtype", [np.int64, np.uint64, np.int32, np.uint32])
def test_wide_integers_over_their_full_range(dtype):
    info = np.iinfo(dtype)
    plane = np.array([info.min, info.max, 0, 1, info.max - 1], dtype=dtype)
    hist = compute_plane_histogram(plane, 65_536)
    assert hist.counts.sum() == plane.size
    assert hist.counts[0] >= 1 and hist.counts[-1] >= 2  # minimum first, the two largest last
    limits = hist.integer_bin_limits()
    assert limits[0][0] == info.min and limits[-1][1] == info.max
    assert np.all(np.isfinite(hist.representatives))


def test_per_value_and_search_paths_agree(monkeypatch):
    plane = rng.integers(0, 40_000, (64, 64)).astype(np.uint16)
    fast = compute_plane_histogram(plane, 1000)
    monkeypatch.setattr(histogram_module, "_PER_VALUE_COUNT_LIMIT", 0)
    slow = compute_plane_histogram(plane, 1000)
    np.testing.assert_array_equal(fast.counts, slow.counts)


def test_bin_count_limits():
    with pytest.raises(ValueError, match="between 1 and 256"):
        compute_plane_histogram(np.zeros(4, np.uint8), 257)
    assert compute_plane_histogram(np.zeros(4, np.uint8)).bin_count == 256
    with pytest.raises(ValueError, match="between 2 and 65,536"):
        compute_plane_histogram(np.zeros(4), 1)
    with pytest.raises(ValueError, match="whole number"):
        compute_plane_histogram(np.zeros(4), 10.5)


@pytest.mark.parametrize("dtype", [bool, np.complex64, np.float16])
def test_unsupported_dtypes_are_rejected(dtype):
    with pytest.raises(UnsupportedDtypeError):
        compute_plane_histogram(np.zeros(4, dtype=dtype))


def test_float_minimum_in_first_bin_maximum_in_last():
    plane = rng.normal(0, 10, (64, 64))
    hist = compute_plane_histogram(plane, 100)
    assert hist.counts.sum() == plane.size == hist.valid_pixel_count
    assert hist.counts[0] >= 1 and hist.counts[-1] >= 1
    assert (hist.pixel_min, hist.pixel_max) == (plane.min(), plane.max())
    assert hist.max_representative_error == pytest.approx((plane.max() - plane.min()) / 200)


def test_float_default_bin_count_is_10000():
    assert compute_plane_histogram(rng.normal(size=16)).bin_count == 10_000


def test_float_non_finite_pixels_are_ignored_and_counted():
    plane = rng.normal(0, 1, 100)
    plane[:3] = [np.nan, np.inf, -np.inf]
    hist = compute_plane_histogram(plane, 50)
    assert (hist.total_pixel_count, hist.valid_pixel_count) == (100, 97)
    assert hist.non_finite_pixel_count == 3 and hist.counts.sum() == 97


def test_float_plane_without_finite_pixels():
    hist = compute_plane_histogram(np.full((4, 4), np.nan), 10)
    assert hist.is_empty and hist.binning_min is None and hist.binning_max is None
    assert hist.counts.sum() == 0


def test_float_constant_plane():
    hist = compute_plane_histogram(np.full((8, 8), 3.25), 10)
    assert (hist.binning_min, hist.binning_max) == (3.25, 3.25)
    assert hist.counts[0] == 64 and hist.counts[1:].sum() == 0
    assert hist.representatives[0] == 3.25 and hist.max_representative_error == 0


def test_float_extreme_range_does_not_overflow():
    plane = np.array([-1e308, 0.0, 1e308])
    hist = compute_plane_histogram(plane, 4)
    np.testing.assert_array_equal(hist.counts, [1, 0, 1, 1])
    assert np.all(np.isfinite(hist.representatives))


def test_float32_input():
    plane = rng.normal(0, 1, (32, 32)).astype(np.float32)
    hist = compute_plane_histogram(plane, 64)
    assert hist.dtype == np.float32 and hist.counts.sum() == plane.size
    assert hist.pixel_min == float(plane.min())
