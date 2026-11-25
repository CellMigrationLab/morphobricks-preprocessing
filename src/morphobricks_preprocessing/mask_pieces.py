"""Count the connected pieces of every label of a 2D mask in one pass (issue #69).

The kernel of :func:`.mask_scan.piece_counts` (``docs/contract.md`` §15), moved from
:mod:`.mask_scan`. Before, the labels were renumbered with a sort of every pixel
(``np.unique(..., return_inverse=True)``) and each label was labelled again in its bounding
box, so labels spread over the plane read most of it once each. Now each row is cut into
runs (consecutive pixels of one nonzero label), runs of one label in neighbouring rows are
joined when they touch (``scipy.sparse.csgraph``), and a label's pieces are its groups of
runs. Each pixel is read a fixed number of times, whatever the labels; the counts are the
same as labelling each label alone with the labeller's structure (:data:`.masks.binary.
STRUCTURE`, imported, not restated: Spec ADR 0005 BM1). No files are read or written.
"""

from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .masks.binary import STRUCTURE
from .masks.labels import check_labels

__all__ = ["piece_counts"]


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
    row, start, end, label = _runs(mask)
    piece = _join_runs(row, start, end, label, _corner_reach())
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


def _runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return the row, first and last column and label of every run of one nonzero label.

    Runs come in row-major order. Pixels of one label next to each other in a row are one
    run, so only runs in neighbouring rows still have to be joined.
    """
    on = mask != 0
    first = on.copy()
    first[:, 1:] &= mask[:, 1:] != mask[:, :-1]
    last = on  # reused: ``on`` is not needed after this
    last[:, :-1] &= mask[:, :-1] != mask[:, 1:]
    row, start = np.nonzero(first)
    end = np.nonzero(last)[1]
    return row, start, end, mask[row, start]


def _join_runs(
    row: np.ndarray, start: np.ndarray, end: np.ndarray, label: np.ndarray, reach: int
) -> np.ndarray:
    """Return the piece of every run: runs of one label in neighbouring rows that touch join.

    A run of row ``r + 1`` touches a run of the same label in row ``r`` when their columns
    overlap, widened by ``reach`` (1: corners touch). Runs are grouped by (row, label) and
    sorted by first column within a group; as runs of one group never overlap, their last
    columns are sorted too, so the runs above each run that touch it are one slice of the
    group, found by two binary searches.
    """
    if row.size == 0:
        return np.zeros(0, dtype=np.intp)
    key = row.astype(np.int64) * (int(label.max()) + 1) + label.astype(np.int64)
    order = np.argsort(key, kind="stable")  # stable: by first column within a group
    keys, group = np.unique(key[order], return_inverse=True)
    width = int(end.max()) + 3  # every column + 2 fits below it: no overlap between groups
    low = group * width + (end[order] + 1)
    high = group * width + (start[order] + 1)
    above = np.searchsorted(keys, key - (int(label.max()) + 1))
    found = above < keys.size
    found[found] = keys[above[found]] == key[found] - (int(label.max()) + 1)
    below, above = np.nonzero(found)[0], above[found]
    first = np.searchsorted(low, above * width + (start[below] + 1 - reach), side="left")
    stop = np.searchsorted(high, above * width + (end[below] + 1 + reach), side="right")
    touching = np.maximum(stop - first, 0)
    pairs = np.repeat(below, touching)
    offset = np.arange(touching.sum()) - np.repeat(np.cumsum(touching) - touching, touching)
    other = order[np.repeat(first, touching) + offset]
    size = row.size
    graph = coo_matrix((np.ones(pairs.size, np.int8), (pairs, other)), shape=(size, size))
    return connected_components(graph, directed=False)[1]
