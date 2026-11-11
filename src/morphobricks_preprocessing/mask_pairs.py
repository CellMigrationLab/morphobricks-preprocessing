"""How the objects of two masks of one plane overlap: best parents and one-to-one matches.

Pure helpers of :func:`.mask_scan.pair_counts` (``docs/contract.md`` §15.1), moved from
:mod:`.mask_scan`: each child's parent with the largest overlap, a tie giving none
(:func:`best_parents`, plan §12 question 74), and the maximum matching of ``same_objects``
(:func:`one_to_one_matches`, issue #67). Every count they give is independent of the
label numbering. No files are read or written.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import maximum_bipartite_matching

__all__ = ["PIECE_STRUCTURE", "best_parents", "match_counts", "one_to_one_matches"]

PIECE_STRUCTURE = ndimage.generate_binary_structure(2, 2)
"""The connectivity of the mask step's pieces (:func:`.mask_scan.piece_counts`): 8-connected,
``generate_binary_structure(2, 2)``, the structure of CellPatcher's ``BINARY_MASK_CONNECTIVITY =
2`` built here, CellPatcher not imported (owner decision "8-connected everywhere", 11 November
2026). Two pixels of one label touching only at a corner are one piece."""


def best_parents(
    kid: np.ndarray, host: np.ndarray, shared: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return each child's parent sharing most of its pixels, and whether that is a tie.

    A child whose largest overlap is shared exactly by two or more parents has no parent:
    it is tied, and the parent returned for it is the first of them and must not be used
    (owner, 3 November 2026, plan §12 question 74). Before, the parent with the lower
    label index was taken, so ``inside`` and ``parents_with_N_children`` depended on the
    label numbering. Every other child has one parent that does not depend on it.

    Parameters
    ----------
    kid, host, shared
        Each overlapping (child, parent) pair as indices, sorted by child, and its number of
        shared pixels; each pair at most once.

    Returns
    -------
    tuple of numpy.ndarray
        Per child with an overlap, in order: the child, its parent, the pixels they share,
        and True when two or more parents share that many.
    """
    new_child = np.r_[True, kid[1:] != kid[:-1]]
    starts = np.flatnonzero(new_child)
    child_of = np.cumsum(new_child) - 1
    most = np.maximum.reduceat(shared, starts)
    top = np.flatnonzero(shared == most[child_of])  # every pair with its child's largest
    ties = np.bincount(child_of[top], minlength=starts.size) > 1
    first = top[np.r_[True, child_of[top][1:] != child_of[top][:-1]]]
    return kid[starts], host[first], most, ties


def one_to_one_matches(kid: np.ndarray, host: np.ndarray, children: int, parents: int) -> int:
    """Return the number of one-to-one matches: a maximum matching of the eligible pairs.

    Each child and each parent is matched at most once, and the number of matches is the
    largest possible (Hopcroft-Karp, :func:`scipy.sparse.csgraph.maximum_bipartite_matching`).
    That number does not depend on which of several equally good matchings is found, so
    the counts are deterministic. Above an intersection over union of 0.5 an object can
    overlap only one partner that much, and every eligible pair is matched; at 0.5 or below,
    two children covering one parent are one match, not two (issue #67).

    Parameters
    ----------
    kid, host
        The child and parent of each eligible pair (intersection over union at least the
        threshold), as indices below ``children`` and ``parents``; each pair at most once.
    children, parents
        Numbers of child and parent objects.

    Returns
    -------
    int
    """
    if kid.size == 0:
        return 0
    graph = csr_matrix((np.ones(kid.size, dtype=np.int8), (kid, host)), shape=(children, parents))
    partner = maximum_bipartite_matching(graph, perm_type="column")
    return int(np.count_nonzero(partner >= 0))


def match_counts(kid: np.ndarray, host: np.ndarray, children: int, parents: int) -> dict:
    """Return the matched, unmatched and several-partner counts of the eligible pairs.

    Parameters
    ----------
    kid, host
        The child and parent of each eligible pair, as in :func:`one_to_one_matches`.
    children, parents
        Numbers of child and parent objects.

    Returns
    -------
    dict
        The six one-to-one counts of the evidence (contract §15.1), by name.
    """
    matched = one_to_one_matches(kid, host, children, parents)
    return {
        "children_matched_one_to_one": matched,
        "parents_matched_one_to_one": matched,
        "children_not_matched": children - matched,
        "parents_not_matched": parents - matched,
        "children_over_iou_with_several_parents": int(
            np.count_nonzero(np.bincount(kid, minlength=children) > 1)
        ),
        "parents_over_iou_with_several_children": int(
            np.count_nonzero(np.bincount(host, minlength=parents) > 1)
        ),
    }
