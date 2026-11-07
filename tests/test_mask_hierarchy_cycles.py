"""Cyclic mask hierarchies are named and shown whole, never as an empty tree (issue #105)."""

from types import MappingProxyType

import numpy as np

from morphobricks_preprocessing import preflight, scan_masks
from morphobricks_preprocessing.mask_scan import (
    MaskReport,
    MaskTypeSummary,
    PairRelation,
    _relations,
    hierarchy_cycles,
    mask_thresholds,
)
from morphobricks_preprocessing.mask_text import mask_hierarchy_lines, mask_report_summary
from tiff_tree import write_data

EVIDENCE = {
    "children": 1,
    "inside": 1,
    "outside_parents": 0,
    "split_between_parents": 0,
    "ambiguous_parent": 0,
    "parents_with_0_children": 0,
    "parents_with_1_child": 1,
    "parents_with_2_children": 0,
    "parents_with_3_or_more_children": 0,
}


def report(*links, types=()):
    """A MaskReport whose direct relations are the ``(child, parent)`` links given."""
    names = sorted({*types, *(name for link in links for name in link)})
    summaries = {name: MaskTypeSummary(name, "instances", 1, 1, 1.0) for name in names}
    pairs = tuple(PairRelation(c, p, "inside", True, MappingProxyType(EVIDENCE)) for c, p in links)
    return MaskReport(images=1, types=MappingProxyType(summaries), pairs=pairs)


def warnings(lines):
    return [line for line in lines if "mask_hierarchy_cycle" in line]


def test_two_masks_inside_each_other_are_a_named_cycle():
    r = report(("A", "B"), ("B", "A"))
    assert r.hierarchy_cycles == (("A", "B"),)
    lines = mask_hierarchy_lines(r)
    (warning,) = warnings(lines)
    assert "A, B form a cycle (A in B; B in A)" in warning
    assert "no parent-child hierarchy can be inferred" in warning
    assert lines[1:] == ["    [cycle: A, B]"]


def test_a_three_mask_cycle_is_not_an_empty_tree():
    # Before #105 no type was without a parent, so the tree printed nothing at all.
    r = report(("A", "B"), ("B", "C"), ("C", "A"), types=("Free",))
    assert r.hierarchy_cycles == (("A", "B", "C"),)
    lines = mask_hierarchy_lines(r)
    assert "(A in B; B in C; C in A)" in warnings(lines)[0]
    assert lines[1:] == ["    [cycle: A, B, C]", "    Free"]
    text = mask_report_summary(r)
    tree = text.split("Hierarchy (direct parents only):\n")[1]
    assert "Warning (mask_hierarchy_cycle)" in tree and "[cycle: A, B, C]" in tree


def test_a_cycle_below_a_root_is_shown_without_endless_recursion():
    # Cells is a root; Nuclei and Spots are inside each other and both inside Cells.
    r = report(("Nuclei", "Cells"), ("Nuclei", "Spots"), ("Spots", "Nuclei"), ("Dots", "Spots"))
    assert r.hierarchy_cycles == (("Nuclei", "Spots"),)
    lines = mask_hierarchy_lines(r)
    assert len(warnings(lines)) == 1
    assert lines[1:] == ["    Cells", "        [cycle: Nuclei, Spots]", "            Dots"]


def test_a_mask_that_is_its_own_parent_is_a_cycle():
    r = report(("A", "A"), ("B", "A"))
    assert r.hierarchy_cycles == (("A",),)
    lines = mask_hierarchy_lines(r)
    assert "(A in A)" in warnings(lines)[0]
    assert lines[1:] == ["    [cycle: A]", "        B"]


def test_a_valid_hierarchy_is_unchanged():
    r = report(("Cells", "Organoid"), ("Nuclei", "Cells"), types=("Other",))
    assert r.hierarchy_cycles == ()
    assert mask_hierarchy_lines(r) == [
        "    Organoid",
        "        Cells",
        "            Nuclei",
        "    Other",
    ]


def test_a_type_with_two_parents_still_shows_under_each():
    r = report(("Dots", "Left"), ("Dots", "Top"))
    assert r.hierarchy_cycles == ()
    assert mask_hierarchy_lines(r) == ["    Left", "        Dots", "    Top", "        Dots"]


def test_sparse_pairwise_evidence_from_a_scan_makes_a_named_cycle(tmp_path):
    # Each pair of types is seen together in one image only: A in B, B in C, C in A.
    def box(size):
        mask = np.zeros((32, 32), np.uint16)
        start = (32 - size) // 2
        mask[start : start + size, start : start + size] = 1
        return mask

    root, folder = tmp_path / "exp", tmp_path / "masks"
    for image, (small, big) in {"one": ("A", "B"), "two": ("B", "C"), "three": ("C", "A")}.items():
        write_data(root / image / "x.tif", np.zeros((32, 32), np.uint8))
        write_data(folder / small / image / "x.tif", box(6))
        write_data(folder / big / image / "x.tif", box(24))
    inventory = preflight(root, masks_folder=folder).masks
    assert inventory.ok, inventory.issues
    scanned = scan_masks(inventory, root)
    assert dict(scanned.hierarchy) == {"A": ("B",), "B": ("C",), "C": ("A",)}
    assert scanned.hierarchy_cycles == (("A", "B", "C"),)
    lines = mask_hierarchy_lines(scanned)
    assert len(warnings(lines)) == 1 and lines[1:] == ["    [cycle: A, B, C]"]


def relations(*links, names=None):
    """The pairs ``_relations`` marks when every ``(child, parent)`` link is ``inside``."""
    names = names or sorted({name for link in links for name in link})
    types = {name: MaskTypeSummary(name, "instances", 1, 1, 1.0) for name in names}
    none = {"children": 1, "parents": 1, "children_matched_one_to_one": 0}
    none |= {"parents_matched_one_to_one": 0, "inside": 0, "inside_parent_larger": 0}
    inside = none | {"inside": 1, "inside_parent_larger": 1}
    totals = {
        (c, p): inside if (c, p) in links else none for c in names for p in names if c != p
    }
    return _relations(types, totals, mask_thresholds())


def test_a_complete_three_cycle_keeps_its_edges_and_warns():
    links = [(c, p) for c in "ABC" for p in "ABC" if c != p]
    pairs = relations(*links)
    assert all(pair.direct for pair in pairs)
    r = MaskReport(images=1, types=MappingProxyType(
        {n: MaskTypeSummary(n, "instances", 1, 1, 1.0) for n in "ABC"}), pairs=pairs)
    assert r.hierarchy_cycles == (("A", "B", "C"),)
    assert len(warnings(mask_hierarchy_lines(r))) == 1


def test_a_cycle_with_a_chord_keeps_its_edges_and_warns():
    pairs = relations(("A", "B"), ("B", "C"), ("C", "A"), ("A", "C"))
    assert all(pair.direct for pair in pairs if pair.relation == "inside")
    hierarchy = {p.child: [] for p in pairs}
    for p in pairs:
        if p.direct:
            hierarchy[p.child].append(p.parent)
    assert hierarchy_cycles(hierarchy) == (("A", "B", "C"),)


def test_the_reduction_runs_between_cycles_and_acyclic_edges_are_unchanged():
    # Dots in X in Y in Z (chain): Dots-Y and X-Z and Dots-Z are implied, not direct.
    chain = relations(("D", "X"), ("X", "Y"), ("Y", "Z"), ("D", "Y"), ("X", "Z"), ("D", "Z"))
    assert {(p.child, p.parent) for p in chain if p.direct} == {("D", "X"), ("X", "Y"), ("Y", "Z")}
    # A and B nested in each other, both inside Top; Dots inside A (and so inside Top).
    mixed = relations(("A", "B"), ("B", "A"), ("A", "T"), ("B", "T"), ("D", "A"), ("D", "T"))
    direct = {(p.child, p.parent) for p in mixed if p.direct}
    assert direct == {("A", "B"), ("B", "A"), ("A", "T"), ("B", "T"), ("D", "A")}
