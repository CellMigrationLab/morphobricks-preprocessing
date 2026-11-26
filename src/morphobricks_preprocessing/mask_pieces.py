"""Count the connected pieces of every label of a 2D mask (issue #69).

The kernel of :func:`.mask_scan.piece_counts` (``docs/contract.md`` §15), moved from
:mod:`.mask_scan`. Two routes give the same counts as labelling each label alone with the
labeller's structure (:data:`.masks.binary.STRUCTURE`, imported, not restated: Spec ADR 0005
BM1):

- **runs** (most masks): each row is cut into runs (consecutive pixels of one nonzero label),
  runs of one label in neighbouring rows that touch are joined (``scipy.sparse.csgraph``),
  and a label's pieces are its groups of runs. No pixel is sorted and no label is labelled
  again, so labels spread over the plane cost no more than compact ones;
- **boxes** (pixel-scale masks, more than :data:`RUNS_PER_PIXEL_LIMIT` runs per pixel, such
  as noise or checkerboards): the former route, kept unchanged: the labels are renumbered
  with a sort of every pixel and each label is labelled in its bounding box. There, runs are
  almost pixels, and the graph of runs would need several times the memory of the sort.

No files are read or written.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .masks.binary import STRUCTURE
from .masks.labels import check_labels

__all__ = ["RUNS_PER_PIXEL_LIMIT", "piece_counts"]

RUNS_PER_PIXEL_LIMIT = 0.25
"""Above this many runs per pixel, the pieces are counted by boxes (the former route)."""

_INT32_MAX = np.iinfo(np.int32).max


def piece_counts(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return the labels of a 2D mask and the number of 8-connected pieces of each.

    Two pixels are in one piece when they carry the same label and touch along an edge or at
    a corner (:data:`.masks.binary.STRUCTURE`). Label 0 is the background.

    Returns
    -------
    tuple of numpy.ndarray
        The sorted nonzero labels (of the mask's pixel type), and the number of pieces of
        each (``int64``).

    Raises
    ------
    ValueError
        If the mask is not 2D, holds a label outside 0 to 2,147,483,647, or pixels that are
        not whole numbers (:func:`.masks.check_labels`).
    """
    mask = np.asarray(mask)
    check_labels(mask)
    if mask.ndim != 2:
        raise ValueError(f"The mask must be one 2D plane, not {mask.ndim}D.")
    first = mask != 0
    first[:, 1:] &= mask[:, 1:] != mask[:, :-1]
    if np.count_nonzero(first) > RUNS_PER_PIXEL_LIMIT * mask.size:
        del first
        return _pieces_by_boxes(mask)
    return _pieces_by_runs(mask, first)


def _pieces_by_boxes(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return :func:`piece_counts` by labelling each label in its bounding box (before #69)."""
    values, dense = np.unique(mask, return_inverse=True)
    dense = dense.reshape(mask.shape)
    if values.size and values[0] == 0:  # labels are never negative, so 0 comes first
        values = values[1:]
    else:
        dense = dense + 1  # no background: every value is a label
    counts = np.zeros(values.size, dtype=np.int64)
    for index, box in enumerate(ndimage.find_objects(dense)):
        if box is not None:
            counts[index] = ndimage.label(dense[box] == index + 1, STRUCTURE)[1]
    return values, counts


def _pieces_by_runs(mask: np.ndarray, first: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return :func:`piece_counts` from the runs; ``first`` marks the first pixel of each."""
    starts, ends, label = _runs(mask, first)
    piece = _join_runs(starts, ends, label, mask.shape[1] + 2, _corner_reach())
    del starts, ends
    # every piece is runs of one label, so a label's pieces are the pieces carrying it
    label_of_piece = np.empty(int(piece.max(initial=-1)) + 1, dtype=mask.dtype)
    label_of_piece[piece] = label
    values, counts = np.unique(label_of_piece, return_counts=True)
    return values, counts.astype(np.int64)


def _corner_reach() -> int:
    """Return 1 if :data:`STRUCTURE` joins pixels touching at a corner, 0 if not."""
    full, cross = np.ones((3, 3), bool), np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)
    if np.array_equal(STRUCTURE, full):
        return 1
    if np.array_equal(STRUCTURE, cross):
        return 0
    raise ValueError("piece_counts supports only the 8- or 4-connected 3 x 3 structure.")


def _runs(mask: np.ndarray, first: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return the first and last pixel of every run, as places, and the run's label.

    A place is ``row * (columns + 2) + column``: the two spare columns keep runs of rows that
    are not neighbours out of each other's reach. Places are ``int32`` when every place fits,
    ``int64`` otherwise. Runs come in row-major order, so both places increase.
    """
    rows, columns = mask.shape
    width = columns + 2
    kind = np.int32 if rows * width <= _INT32_MAX else np.int64
    flat = np.flatnonzero(first)
    label = np.ascontiguousarray(mask).reshape(-1)[flat]  # a view when already contiguous
    starts = flat.astype(kind)
    del flat
    last = first  # reused: every first pixel is found
    np.not_equal(mask, 0, out=last)
    last[:, :-1] &= mask[:, :-1] != mask[:, 1:]
    ends = np.flatnonzero(last).astype(kind)
    del last
    if columns:  # no columns, no runs
        starts += 2 * (starts // columns)
        ends += 2 * (ends // columns)
    return starts, ends, label


def _join_runs(
    starts: np.ndarray, ends: np.ndarray, label: np.ndarray, width: int, reach: int
) -> np.ndarray:
    """Return the piece of every run: runs of one label in neighbouring rows that touch join.

    A run touches a run of the row above when their columns overlap, widened by ``reach``
    (1: corners touch). The runs of the row above that a run's widened columns overlap are
    one slice of the runs (places increase), found by two binary searches; of those, the
    ones with the same label are joined to it. ``width`` is the places per row.
    """
    size = starts.size
    if size == 0:
        return np.zeros(0, dtype=np.intp)
    low = np.searchsorted(ends, starts - (width + reach), side="left")
    high = np.searchsorted(starts, ends - (width - reach), side="right")
    touching = np.maximum(high - low, 0).astype(starts.dtype)
    del high
    total = int(touching.sum())
    kind = np.int32 if total <= _INT32_MAX and size <= _INT32_MAX else np.int64
    below = np.repeat(np.arange(size, dtype=kind), touching)
    above = np.arange(total, dtype=kind)
    shift = (np.cumsum(touching, dtype=np.int64) - touching - low).astype(kind)
    above -= np.repeat(shift, touching)
    del low, touching, shift
    same = label[below] == label[above]
    below, above = below[same], above[same]
    del same
    graph = coo_matrix((np.ones(below.size, bool), (below, above)), shape=(size, size))
    del below, above
    return connected_components(graph, directed=False)[1]
