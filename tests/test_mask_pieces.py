"""piece_counts gives byte-identical results to the former per-label one by both routes (#69).

Every test runs three times (the ``route`` fixture): runs forced, boxes forced, and the
route piece_counts chooses.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import ndimage

from morphobricks_preprocessing import mask_pieces, piece_counts
from morphobricks_preprocessing.masks import check_labels
from morphobricks_preprocessing.masks.binary import STRUCTURE

DTYPES = (np.bool_, np.uint8, np.int8, np.uint16, np.int16, np.uint32, np.int32, np.int64)
DTYPES += (np.uint64,)


@pytest.fixture(autouse=True, params=["runs", "boxes", "chosen"])
def route(request, monkeypatch):
    limit = {"runs": np.inf, "boxes": -1.0}.get(request.param)
    if limit is not None:
        monkeypatch.setattr(mask_pieces, "RUNS_PER_PIXEL_LIMIT", limit)
    return request.param


def boxes_piece_counts(mask):
    """Return piece_counts as it was before issue #69 (main c8c3808), kept as the reference."""
    check_labels(mask)
    values, dense = np.unique(mask, return_inverse=True)
    dense = dense.reshape(mask.shape)
    if values.size and values[0] == 0:
        values = values[1:]
    else:
        dense = dense + 1
    counts = np.zeros(values.size, dtype=np.int64)
    for index, box in enumerate(ndimage.find_objects(dense)):
        if box is not None:
            counts[index] = ndimage.label(dense[box] == index + 1, STRUCTURE)[1]
    return values, counts


def assert_identical(mask):
    new, old = piece_counts(mask), boxes_piece_counts(mask)
    for found, expected in zip(new, old, strict=True):
        assert found.dtype == expected.dtype
        assert found.shape == expected.shape
        assert found.tobytes() == expected.tobytes()


def random_mask(rng, dtype):
    """Return a random mask of one of several kinds, in the pixel type given."""
    shape = tuple(int(n) for n in rng.integers(1, 40, 2))
    kind = rng.integers(6)
    top = 1 if dtype is np.bool_ else int(min(np.iinfo(dtype).max, 2_147_483_647))
    if kind == 0:  # few labels, densely touching, holes of background
        mask = rng.integers(0, min(top, 4) + 1, shape)
    elif kind == 1:  # single pixels scattered on background
        mask = np.where(rng.random(shape) < 0.05, rng.integers(1, top + 1, shape), 0)
    elif kind == 2:  # blocks (touching objects with holes), labels far apart
        small = rng.integers(0, 4, (shape[0] // 3 + 1, shape[1] // 3 + 1))
        mask = np.kron(small, np.ones((3, 3), int))[: shape[0], : shape[1]]
        values = rng.integers(1, top + 1, 3)  # possibly equal: one label in several blocks
        mask = np.where(mask == 0, 0, values[(mask - 1) % 3])
        mask[rng.random(shape) < 0.1] = 0  # holes
    elif kind == 3:  # no background at all
        mask = rng.integers(1, min(top, 3) + 1, shape)
    elif kind == 4:  # one label touching the edges, corners only (diagonals)
        mask = (np.add.outer(np.arange(shape[0]), np.arange(shape[1])) % 2) * min(top, 7)
    else:  # empty
        mask = np.zeros(shape, int)
    return np.asarray(mask).astype(dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_piece_counts_equal_the_per_label_reference_on_random_masks(dtype):
    rng = np.random.default_rng(69)
    for _ in range(300):
        assert_identical(random_mask(rng, dtype))


@pytest.mark.parametrize(
    "mask",
    [
        np.zeros((1, 1), np.int32),
        np.ones((1, 1), np.int32),
        np.full((3, 3), 2_147_483_647, np.int64),
        np.array([[1, 0, 1]], np.uint8),
        np.eye(5, dtype=np.uint8) + np.fliplr(np.eye(5, dtype=np.uint8)) * 2,
        np.array([[1, 2], [2, 1]], np.int32),  # two labels crossing at a corner
        np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], np.int32),  # a ring around a hole
        np.array([[5], [0], [5], [5]], np.int32),
        np.array([[True, False], [False, True]]),
    ],
)
def test_piece_counts_equal_the_reference_on_edge_cases(mask):
    assert_identical(mask)


def test_piece_counts_equal_the_reference_on_the_benchmark_masks():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    try:
        import benchmark_inputs
    finally:
        sys.path.pop(0)
    cells, nuclei = benchmark_inputs._label_masks(512, 256, 0)
    assert_identical(cells)
    assert_identical(nuclei)
    assert_identical(np.where(nuclei != 0, nuclei + cells.max(), cells))  # rings round nuclei


@pytest.mark.parametrize("shape", [(0, 0), (0, 5), (4, 0)])
def test_a_mask_without_pixels_has_no_labels(shape):
    # the reference failed here (find_objects of no pixels: a NumPy error, not a result);
    # a plane of no pixels holds no labels, as a plane of background
    values, counts = piece_counts(np.zeros(shape, np.uint16))
    assert values.dtype == np.uint16 and values.size == 0
    assert counts.dtype == np.int64 and counts.size == 0
    with pytest.raises(ValueError, match="zero-size"):
        boxes_piece_counts(np.zeros(shape, np.uint16))


def test_piece_counts_refuse_a_mask_that_is_not_2d():
    with pytest.raises(ValueError, match="2D"):
        piece_counts(np.ones((2, 2, 2), np.int32))
    with pytest.raises(ValueError, match="negative"):
        piece_counts(np.full((2, 2), -1, np.int32))


def test_pixel_scale_masks_take_the_boxes_route_and_others_the_runs_route(route, monkeypatch):
    if route != "chosen":
        pytest.skip("the choice itself is tested with the default limit")
    taken = []
    for name in ("_pieces_by_boxes", "_pieces_by_runs"):
        real = getattr(mask_pieces, name)
        monkeypatch.setattr(
            mask_pieces, name, lambda *a, real=real, name=name: taken.append(name) or real(*a)
        )
    checkerboard = (np.add.outer(np.arange(64), np.arange(64)) % 2 + 1).astype(np.uint16)
    assert_identical(checkerboard)  # one run per pixel
    blocks = np.kron(np.arange(16, dtype=np.int32).reshape(4, 4), np.ones((16, 16), np.int32))
    assert_identical(blocks)  # 1/16 run per pixel
    assert taken == ["_pieces_by_boxes", "_pieces_by_runs"]


@pytest.mark.parametrize(
    ("shape", "reach", "kind"),
    [
        ((1, 2**31 - 3), 1, np.int64),  # review of #568: width + reach is 2**31
        ((1, 2**31 - 4), 1, np.int32),
        ((1, 2**31 - 3), 0, np.int32),
        ((2, 2**30 - 2), 1, np.int64),  # 2 * (2**30) + 1 is above the int32 limit
        ((4096, 4096), 1, np.int32),
    ],
)
def test_places_are_int32_only_when_every_search_fits(route, shape, reach, kind):
    if route != "chosen":
        pytest.skip("the choice of place type does not depend on the route")
    assert mask_pieces._place_kind(*shape, reach) is kind
    if kind is np.int32:  # the lowest search value of a run in the first row fits
        width = shape[1] + 2
        assert np.array([0], np.int32) - (width + reach) >= np.iinfo(np.int32).min
