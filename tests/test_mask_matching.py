"""One-to-one matches of the mask step are a matching: each object matched once (issue #67)."""

from itertools import permutations

import numpy as np
import pytest

from morphobricks_preprocessing import pair_counts, write_mask_report
from morphobricks_preprocessing.mask_scan import one_to_one_matches
from test_mask_scan import boxes, by_pair, scan


def matches(counts):
    return (
        counts["children_matched_one_to_one"],
        counts["parents_matched_one_to_one"],
        counts["children_not_matched"],
        counts["parents_not_matched"],
        counts["children_over_iou_with_several_parents"],
        counts["parents_over_iou_with_several_children"],
    )


def test_identical_objects_are_matched_one_to_one():
    cells = boxes({1: (0, 10, 0, 10), 2: (0, 10, 20, 30), 3: (20, 30, 0, 10)})
    assert matches(pair_counts(cells, cells)) == (3, 3, 0, 0, 0, 0)


def test_two_children_halving_one_parent_are_one_match_not_two():
    # The issue's counterexample: one 4-pixel parent, two 2-pixel children; each IoU 0.5.
    parent = boxes({1: (0, 2, 0, 2)})
    children = boxes({10: (0, 1, 0, 2), 11: (1, 2, 0, 2)})
    counts = pair_counts(children, parent)
    assert matches(counts) == (1, 1, 1, 0, 0, 1)
    # The other way round: one child split over two parents.
    assert matches(pair_counts(parent, children)) == (1, 1, 0, 1, 1, 0)


def test_many_to_one_at_a_lower_threshold():
    # Three children in thirds of one parent: IoU 1/3 each, eligible at 0.3.
    parent = boxes({1: (0, 3, 0, 3)})
    children = boxes({10: (0, 1, 0, 3), 11: (1, 2, 0, 3), 12: (2, 3, 0, 3)})
    counts = pair_counts(children, parent, thresholds={"same_objects_iou": 0.3})
    assert matches(counts) == (1, 1, 2, 0, 0, 1)
    assert matches(pair_counts(children, parent)) == (0, 0, 3, 1, 0, 0)  # not at 0.5


def test_partial_overlaps_at_the_threshold():
    parents = boxes({1: (0, 1, 0, 2), 2: (5, 6, 0, 2)})
    # Child 10 holds parent 1 in 4 pixels (IoU 0.5: eligible), child 11 parent 2 in 5 (0.4).
    children = boxes({10: (0, 2, 0, 2), 11: (5, 6, 0, 5)})
    assert matches(pair_counts(children, parents)) == (1, 1, 1, 1, 0, 0)
    loose = pair_counts(children, parents, thresholds={"same_objects_iou": 0.4})
    assert matches(loose) == (2, 2, 0, 0, 0, 0)


def test_the_matching_is_maximum_not_greedy():
    # Child 10 overlaps parents 1 and 2 at IoU 1/3, child 11 only parent 1 (IoU 0.5):
    # matching child by child in label order (10 with 1) would leave 11 alone; the maximum
    # matching pairs both.
    parents = boxes({1: (0, 1, 0, 4), 2: (0, 1, 4, 8)})
    children = boxes({10: (0, 1, 2, 6), 11: (0, 1, 0, 2)})
    counts = pair_counts(children, parents, thresholds={"same_objects_iou": 0.3})
    assert matches(counts) == (2, 2, 0, 0, 1, 1)


def test_counts_do_not_depend_on_label_numbers():
    parent = boxes({1: (0, 2, 0, 2), 2: (0, 2, 2, 4)})
    halves = {(0, 1, 0, 2), (1, 2, 0, 2), (0, 2, 2, 4)}
    found = set()
    for labels in permutations((3, 5, 9)):
        children = boxes(dict(zip(labels, sorted(halves), strict=True)))
        found.add(matches(pair_counts(children, parent)))
    assert found == {(2, 2, 1, 0, 0, 1)}


def reference_matching(kid, host):
    """Largest matching by trying every assignment (independent of the brick's code)."""
    edges = set(zip(kid.tolist(), host.tolist(), strict=True))
    children = sorted({k for k, _ in edges})
    best = 0

    def walk(i, used, size):
        nonlocal best
        best = max(best, size)
        if i == len(children):
            return
        walk(i + 1, used, size)
        for k, h in edges:
            if k == children[i] and h not in used:
                walk(i + 1, used | {h}, size + 1)

    walk(0, frozenset(), 0)
    return best


@pytest.mark.parametrize("seed", range(20))
def test_matching_size_against_a_brute_force_reference(seed):
    rng = np.random.default_rng(seed)
    graph = rng.random((5, 4)) < 0.4
    kid, host = np.nonzero(graph)
    assert one_to_one_matches(kid, host, 5, 4) == reference_matching(kid, host)


def test_a_split_segmentation_is_not_the_same_objects(tmp_path):
    cells = {1: (2, 12, 2, 12), 2: (2, 12, 20, 30), 3: (20, 30, 2, 12), 4: (20, 30, 20, 30)}
    # Each cell segmented again as two halves: IoU 0.5 each.
    halves = {}
    for label, (r0, r1, c0, c1) in cells.items():
        middle = (r0 + r1) // 2
        halves[10 * label] = (r0, middle, c0, c1)
        halves[10 * label + 1] = (middle, r1, c0, c1)
    report = scan(tmp_path, {"Cells": boxes(cells), "Halves": boxes(halves)})
    pair = by_pair(report)[("Halves", "Cells")]
    assert pair.relation != "same_objects" and report.same_objects == ()
    e = pair.evidence  # two images, four cells and eight halves each
    assert (e["children_matched_one_to_one"], e["parents_matched_one_to_one"]) == (8, 8)
    assert (e["children_not_matched"], e["parents_not_matched"]) == (8, 0)
    assert e["parents_over_iou_with_several_children"] == 8
    data = write_mask_report(report, tmp_path / "out.json").read_text(encoding="utf-8")
    assert '"report_version": "2"' in data
    assert '"children_over_iou_with_several_parents": 0' in data
