"""The working memory of the image QC sums is counted in the block and plane estimates.

The band buffer is sized from the block (``min(64, rows) + reach`` rows, not always the full
band), the finite scan of a float block holds a mask, and ``planes.plane_layout`` adds the QC
term to the estimate of a whole plane and of a block (contract §19).
"""

import tracemalloc

import numpy as np
import pytest
import tifffile
from morphobricks_normalization.histogram import estimate_histogram_memory

from morphobricks_preprocessing import plane_binning, prefill_mapping
from morphobricks_preprocessing.image_qc import PlaneQc, qc_memory
from morphobricks_preprocessing.plane_binning import Binning
from morphobricks_preprocessing.planes import plane_layout

SHAPES = [
    (8, 4096, np.uint16),
    (64, 2048, np.uint16),
    (200, 300, np.float32),
    (16, 1000, np.float64),
    (3000, 20, np.uint16),
    (5, 7, np.uint8),
    (1, 1, np.uint8),
    (300, 2048, np.uint16),
    (17, 33, np.uint16),
    (40, 5, np.float32),
]


def peak_of_add(pixels):
    """Return the peak of extra memory (bytes) while one block is added to the QC sums."""
    qc = PlaneQc(pixels.shape, pixels.dtype)
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        qc.add(0, 0, pixels)
        return tracemalloc.get_traced_memory()[1] - before
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize(("rows", "cols", "dtype"), SHAPES)
def test_the_qc_estimate_bounds_the_measured_peak_of_a_block(rows, cols, dtype):
    pixels = (np.random.default_rng(0).random((rows, cols)) * 1000).astype(dtype)
    floating = np.dtype(dtype).kind == "f"
    assert peak_of_add(pixels) <= qc_memory(rows, cols, floating)


def test_a_short_block_does_not_allocate_the_full_band():
    # 8 rows of 4096: the buffer holds 8 + 16 rows, not 64 + 16.
    pixels = np.random.default_rng(1).integers(0, 1000, (8, 4096)).astype(np.uint16)
    assert peak_of_add(pixels) < 80 * 4096 * 8


def test_the_estimate_counts_the_finite_mask_of_float_blocks_only():
    assert qc_memory(512, 512, True) - qc_memory(512, 512, False) == 512 * 512


def test_a_block_over_the_band_height_adds_only_its_strips():
    # The band buffer is full at 64 + 16 rows; taller blocks add the strips, not more band.
    assert qc_memory(10_000, 1000, False) - qc_memory(64, 1000, False) == 2 * 8 * 16 * (10_000 - 64)


def test_binning_adds_the_qc_term_to_the_histogram_memory_unless_the_plane_is_only_read():
    dtype = np.dtype(np.uint16)
    histogram = estimate_histogram_memory(300 * 200, dtype, range_check=True)
    assert Binning().memory(300, 200, dtype) == histogram + qc_memory(300, 200, False)
    assert Binning().memory(200, 300, dtype, transposed=True) == Binning().memory(300, 200, dtype)
    assert Binning(histogram=False).memory(300, 200, dtype) == 0


@pytest.mark.parametrize("dtype", [np.uint16, np.float32])
def test_the_plane_and_block_estimates_include_the_qc_term(tmp_path, dtype, monkeypatch):
    path = tmp_path / "plane.tif"
    tifffile.imwrite(path, np.zeros((600, 800), dtype), photometric="minisblack")
    mapping = prefill_mapping("YX")
    floating = np.dtype(dtype).kind == "f"
    with_qc = [plane_layout(path, mapping, memory_limit=limit) for limit in (2**30, 20_000_000)]
    monkeypatch.setattr(plane_binning, "qc_memory", lambda rows, cols, floating: 0)
    without = [plane_layout(path, mapping, memory_limit=limit) for limit in (2**30, 20_000_000)]
    whole, blocks = with_qc
    assert not whole.in_blocks and blocks.in_blocks and blocks.block_shape is not None
    assert whole.memory_estimate - without[0].memory_estimate == qc_memory(600, 800, floating)
    # The QC term takes memory from the block: it is shorter than without the term, and fits.
    assert blocks.block_shape[0] < without[1].block_shape[0]
    assert blocks.block_memory_estimate <= 20_000_000
    assert blocks.block_memory_estimate >= qc_memory(*blocks.block_shape, floating)
