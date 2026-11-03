"""A child that lies equally in two or more parents has no parent (owner, 3 November 2026).

Before, ``pair_counts`` gave such a child the parent with the lower label index, so
``inside`` and ``parents_with_N_children`` depended on how the parents were numbered. An
exact tie now gives no parent; when the tied share would make the child inside, it is
counted as ``ambiguous_parent`` (plan §12 question 74, contract §15.1), and the report,
``masks`` text and notebook show it.
"""

import numpy as np
import pytest

from morphobricks_preprocessing import pair_counts
from morphobricks_preprocessing.mask_pairs import best_parents
from morphobricks_preprocessing.mask_scan import MASK_REPORT_VERSION
from morphobricks_preprocessing.mask_text import (
    mask_ambiguous_lines,
    mask_relationship_rows,
    mask_report_summary,
)
from test_mask_scan import boxes, by_pair, scan

PER_PARENT = (
    "parents_with_0_children",
    "parents_with_1_child",
    "parents_with_2_children",
    "parents_with_3_or_more_children",
)


def relabel(children, parents, rng):
    """Give the labels other numbers, in another order: one map for both masks.

    One map keeps a child label equal to the parent label under it where it was
    (``child_pixels_with_parent_label``, the evidence of ``grouped_by``).
    """
    labels = np.unique(np.r_[children[children != 0], parents[parents != 0]])
    new = rng.permutation(np.arange(1, 4 * labels.size + 1))[: labels.size] * 3
    out = []
    for mask in (children, parents):
        relabelled = np.zeros(mask.shape, dtype=np.int32)
        for old, value in zip(labels.tolist(), new.tolist(), strict=True):
            relabelled[mask == old] = value
        out.append(relabelled)
    return out


def halves():
    """Parents 1 and 2 side by side; child 5 exactly half in each, child 6 inside parent 2."""
    parents = boxes({1: (0, 10, 0, 10), 2: (0, 10, 10, 20)})
    children = boxes({5: (2, 4, 8, 12), 6: (6, 8, 14, 16)})
    return children, parents


def test_a_child_exactly_half_in_two_parents_has_no_parent():
    children, parents = halves()
    counts = pair_counts(children, parents)
    assert counts["children"] == 2
    assert counts["inside"] == 1 and counts["inside_parent_larger"] == 1
    assert counts["ambiguous_parent"] == 1
    assert counts["split_between_parents"] == 0 and counts["outside_parents"] == 0
    assert tuple(counts[k] for k in PER_PARENT) == (1, 1, 0, 0)


def test_the_tie_does_not_depend_on_which_parent_has_the_lower_label():
    # Before: (0, 0, 1, 0) with the parents swapped (child 5 went to the parent labelled 1,
    # which then held both children), (0, 2, 0, 0) as written.
    children, parents = halves()
    swapped = parents.copy()
    swapped[parents == 1], swapped[parents == 2] = 2, 1
    assert pair_counts(children, swapped) == pair_counts(children, parents)


def test_three_tied_parents_at_a_lower_inside_share():
    parents = boxes({1: (0, 9, 0, 3), 2: (0, 9, 3, 6), 3: (0, 9, 6, 9)})
    child = boxes({7: (0, 3, 0, 9)})  # 9 pixels, 3 in each parent
    default = pair_counts(child, parents)
    assert (default["inside"], default["ambiguous_parent"]) == (0, 0)
    assert default["split_between_parents"] == 1  # a third is below one half: as before
    lower = pair_counts(child, parents, thresholds={"inside_pixel_share": 0.3})
    assert (lower["inside"], lower["ambiguous_parent"], lower["split_between_parents"]) == (
        0,
        1,
        0,
    )
    assert lower["parents_with_0_children"] == 3


def test_a_tie_below_the_largest_overlap_changes_nothing():
    parents = boxes({1: (0, 10, 0, 6), 2: (0, 10, 6, 8), 3: (0, 10, 8, 10)})
    child = boxes({4: (0, 1, 0, 10)})  # 6, 2 and 2 pixels: parent 1 holds it
    counts = pair_counts(child, parents)
    assert (counts["inside"], counts["ambiguous_parent"]) == (1, 0)
    assert tuple(counts[k] for k in PER_PARENT) == (2, 1, 0, 0)


def random_masks(rng):
    """Small blocky masks, so that exact ties are common."""
    shape = tuple(rng.integers(4, 12, 2))
    parents = np.kron(rng.integers(0, 5, (shape[0] // 2, shape[1] // 2)), np.ones((2, 2)))
    children = rng.integers(0, 6, parents.shape)
    return children.astype(np.int32), parents.astype(np.int32)


@pytest.mark.parametrize("share", [0.5, 0.25])
def test_every_count_is_the_same_under_any_label_numbering(share):
    rng = np.random.default_rng(74)
    limits = {"inside_pixel_share": share}
    ties = 0
    for _ in range(300):
        children, parents = random_masks(rng)
        counts = pair_counts(children, parents, thresholds=limits)
        ties += counts["ambiguous_parent"]
        for _ in range(3):
            again = pair_counts(*relabel(children, parents, rng), thresholds=limits)
            assert again == counts
        assert counts["children"] == sum(
            counts[k]
            for k in ("inside", "ambiguous_parent", "split_between_parents", "outside_parents")
        )
        assert sum(counts[k] for k in PER_PARENT) == counts["parents"]
    assert ties > 20  # the data hold ties, so the test can fail


def reference_best(kid, host, shared):
    """Each child's largest overlap and how many parents have it, by plain loops."""
    found = {}
    for k, h, s in zip(kid.tolist(), host.tolist(), shared.tolist(), strict=True):
        found.setdefault(k, []).append((s, h))
    result = {}
    for k, pairs in found.items():
        most = max(s for s, _ in pairs)
        hosts = sorted(h for s, h in pairs if s == most)
        result[k] = (most, hosts)
    return result


def test_best_parents_matches_a_reference():
    rng = np.random.default_rng(3)
    for _ in range(200):
        children, parents = rng.integers(1, 6), rng.integers(1, 6)
        pairs = np.unique(rng.integers(0, children * parents, rng.integers(1, 15)))
        kid, host = pairs // parents, pairs % parents
        shared = rng.integers(1, 4, kid.size)
        best_kid, best_host, most, tied = best_parents(kid, host, shared)
        reference = reference_best(kid, host, shared)
        assert best_kid.tolist() == sorted(reference)
        found = zip(best_kid.tolist(), best_host.tolist(), most.tolist(), tied, strict=True)
        for k, h, s, t in found:
            assert s == reference[k][0] and bool(t) == (len(reference[k][1]) > 1)
            assert h in reference[k][1]


def test_the_report_and_its_texts_show_the_tie(tmp_path):
    children, parents = halves()
    report = scan(tmp_path, {"Cells": parents, "Nuclei": children})
    pair = by_pair(report)[("Nuclei", "Cells")]
    assert pair.evidence["ambiguous_parent"] == 2  # one per image, two images
    assert pair.relation == "none"  # 2 of 4 inside is below 90%
    data = report.to_dict()
    assert MASK_REPORT_VERSION == data["report_version"] == "3"
    (written,) = [p for p in data["pairs"] if (p["child"], p["parent"]) == ("Nuclei", "Cells")]
    assert written["evidence"]["ambiguous_parent"] == 2
    assert mask_ambiguous_lines(report) == [
        "Nuclei in Cells: 2 objects of 4 equally in two or more Cells"
    ]
    text = mask_report_summary(report)
    assert "Objects given no parent (equally in two or more objects" in text
    assert "Nuclei in Cells: 2 objects of 4" in text


def test_the_relationship_table_has_the_tied_children(tmp_path):
    parents = boxes({1: (0, 30, 0, 30), 2: (0, 30, 30, 60)})
    inside = {n: (2 * n, 2 * n + 1, 2, 4) for n in range(1, 13)}
    children = boxes({**inside, 20: (28, 29, 28, 32)})  # 12 inside, 1 tied: 92% inside
    report = scan(tmp_path, {"Cells": parents, "Nuclei": children})
    assert by_pair(report)[("Nuclei", "Cells")].relation == "inside"
    header, *rows = mask_relationship_rows(report)
    assert header[-1] == "Equally in two or more of its objects (no parent)"
    (row,) = [r for r in rows if r[0] == "Nuclei"]
    assert row[3] == "24 of 26" and row[-1] == "2"
    assert "2 equally in two or more Cells (no parent given)" in mask_report_summary(report)
