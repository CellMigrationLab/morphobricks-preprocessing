"""QC metrics of planes read in blocks: the edge effect is an observation, not a bound (§19).

The neighbour metrics leave out the pairs across a block edge. How much that matters depends on
the image: ``test_image_qc.py`` observes small differences on cell-like fields, and the first
test here a large one for an image whose strongest change lies exactly on a block edge. The
second runs the real blocked reader with blocks of at most 16 rows.
"""

import numpy as np
import pytest

from morphobricks_preprocessing.image_qc_record import IMAGE_QC_FILENAME
from morphobricks_preprocessing.image_qc_table import read_image_qc
from morphobricks_preprocessing.planes import iter_xyc_planes, plane_layout
from qc_tree import CTYX, IMAGES, make_experiment, run
from test_image_qc import measure


@pytest.fixture
def experiment(tmp_path):
    return make_experiment(tmp_path)


def test_a_step_exactly_at_a_block_edge_is_missed_by_the_neighbour_metrics():
    rng = np.random.default_rng(0)
    pixels = rng.integers(95, 105, (64, 64)).astype(np.uint16)
    pixels[16:] += 900  # the step lies between row 15 and row 16: a band edge
    whole, bands = measure(pixels), measure(pixels, (16, 64))
    # The histogram, mean, variance and tile metrics are the whole plane's...
    for name in ("normalized_variance", "tile_mean_cv", "otsu_separation", "entropy"):
        assert getattr(bands, name) == pytest.approx(getattr(whole, name), rel=1e-9), name
    # ...but the vertical differences across the edge are the plane's strongest, and are left
    # out: Tenengrad drops to a fraction of the whole plane's. The 5 % and 0.05 seen on
    # cell-like fields are observations (contract §19), not guarantees.
    assert bands.tenengrad < 0.5 * whole.tenengrad
    assert bands.laplacian_variance < 0.5 * whole.laplacian_variance
    assert abs(bands.correlation_1 - whole.correlation_1) > 0.002
    assert whole.correlation_16 is not None and bands.correlation_16 is None  # blank, not zero


def rows_limit(path, mapping, rows):
    """Return the smallest memory limit at which the plane's blocks are ``rows`` rows high."""
    low, high = plane_layout(path, mapping, memory_limit=1).block_memory_estimate, 2**30
    while low < high:  # the blocks grow with the limit until the plane fits whole
        middle = (low + high) // 2
        layout = plane_layout(path, mapping, memory_limit=middle)
        if not layout.in_blocks or layout.block_shape[0] >= rows:
            high = middle
        else:
            low = middle + 1
    assert plane_layout(path, mapping, memory_limit=low).block_shape[0] == rows
    return low


def test_blocks_of_sixteen_rows_leave_lag_16_blank_and_never_zero(tmp_path, experiment):
    path = experiment / IMAGES[0]
    limit = rows_limit(path, CTYX, 16)
    heights = {
        block.pixels.shape[0]
        for plane in iter_xyc_planes(path, CTYX, memory_limit=limit)
        for block in plane.channel(0).blocks()
    }
    assert heights == {16, 8}  # 40 rows: two bands of 16 and one of 8
    whole, blocked = tmp_path / "whole", tmp_path / "blocked"
    run(experiment, whole)
    run(experiment, blocked, memory_limit=limit)
    text = (blocked / IMAGE_QC_FILENAME).read_text(encoding="utf-8")
    assert text.count("correlation_16=no_pairs") == len(IMAGES) * 4
    rows = read_image_qc(blocked / IMAGE_QC_FILENAME)
    assert len(rows) == len(IMAGES) * 4 and all(row.read_in_blocks for row in rows)
    for row in rows:
        assert row.qc.correlation_16 is None  # no vertical pair 16 rows apart in a 16-row band
        assert row.qc.correlation_1 is not None and row.qc.correlation_4 is not None
        assert ("correlation_16", "no_pairs") in row.qc.reasons
    for row in read_image_qc(whole / IMAGE_QC_FILENAME):
        assert not row.read_in_blocks and row.qc.correlation_16 is not None
