"""Cyclic mask hierarchies are named and shown whole, never as an empty tree (issue #105)."""

from types import MappingProxyType

import numpy as np

from morphobricks_preprocessing import preflight, scan_masks
from morphobricks_preprocessing.mask_scan import MaskReport, MaskTypeSummary, PairRelation
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
