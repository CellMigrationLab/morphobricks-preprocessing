"""The mask step: kinds of masks, relationships with their evidence, hierarchy (contract §15)."""

import json
from collections import deque

import numpy as np
import pytest

from morphobricks_preprocessing import (
    DEFAULT_THRESHOLDS,
    mask_thresholds,
    pair_counts,
    piece_counts,
    preflight,
    scan_masks,
    write_mask_report,
)
from tiff_tree import write_data

SHAPE = (64, 64)
# Cells in four quadrants; cell 1 has two nuclei, cell 3 none.
CELLS = {1: (2, 30, 2, 30), 2: (2, 30, 34, 62), 3: (34, 62, 2, 30), 4: (34, 62, 34, 62)}
NUCLEI = {10: (5, 11, 5, 11), 11: (18, 24, 18, 24), 12: (10, 16, 40, 46), 13: (40, 46, 40, 46)}


def boxes(spec):
    mask = np.zeros(SHAPE, dtype=np.uint16)
    for label, (r0, r1, c0, c1) in spec.items():
        mask[r0:r1, c0:c1] = label
    return mask


def hierarchy_masks():
    """Organoid > Cells > Nuclei > Nucleoli; NucleiAlt = the nuclei segmented again; Vesicles
    = single pixels carrying their cell's label; Left and Top = two overlapping regions."""
    nucleoli = {100 + n: (r0 + 2, r0 + 4, c0 + 2, c0 + 4) for n, (r0, _, c0, _) in NUCLEI.items()}
    alt = {200 + n: (r0 - 1, r1 + 1, c0 - 1, c1 + 1) for n, (r0, r1, c0, c1) in NUCLEI.items()}
    vesicles = np.zeros(SHAPE, dtype=np.uint16)
    for label, (r0, _, c0, _) in CELLS.items():
        for dr, dc in ((1, 1), (1, 20), (20, 1), (20, 20)):
            vesicles[r0 + dr, c0 + dc] = label
    organoid = np.zeros(SHAPE, dtype=np.uint8)
    organoid[1:63, 1:63] = 1
    return {
        "Organoid": organoid,
        "Cells": boxes(CELLS),
        "Nuclei": boxes(NUCLEI),
        "NucleiAlt": boxes(alt),
        "Nucleoli": boxes(nucleoli),
        "Vesicles": vesicles,
    }


def write_experiment(tmp_path, masks, images=("ctrl/a.tif", "drug/a.tif")):
    root, folder = tmp_path / "exp", tmp_path / "masks"
    for rel in images:
        write_data(root / rel, np.zeros(SHAPE, dtype=np.uint8))
        for name, mask in masks.items():
            write_data(folder / name / rel, mask)
    return root, folder


def scan(tmp_path, masks):
    root, folder = write_experiment(tmp_path, masks)
    inventory = preflight(root, masks_folder=folder).masks
    assert inventory.ok, inventory.issues
    return scan_masks(inventory, root)


def by_pair(report):
    return {(p.child, p.parent): p for p in report.pairs}


# Pieces -------------------------------------------------------------------------------


def reference_pieces(mask):
    """Pieces per label by breadth-first search (independent of the brick's code)."""
    seen = np.zeros(mask.shape, dtype=bool)
    pieces = {}
    for start in zip(*np.nonzero(mask), strict=True):
        if seen[start]:
            continue
        label = mask[start]
        pieces[int(label)] = pieces.get(int(label), 0) + 1
        queue = deque([start])
        seen[start] = True
        while queue:
            r, c = queue.popleft()
            for nr, nc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                inside = 0 <= nr < mask.shape[0] and 0 <= nc < mask.shape[1]
                if inside and not seen[nr, nc] and mask[nr, nc] == label:
                    seen[nr, nc] = True
                    queue.append((nr, nc))
    return pieces


def test_pieces_are_4_connected_and_counted_per_label():
    rng = np.random.default_rng(0)
    for _ in range(50):
        mask = rng.integers(0, 4, size=tuple(rng.integers(1, 20, 2))).astype(np.int32)
        labels, counts = piece_counts(mask)
        assert dict(zip(labels.tolist(), counts.tolist(), strict=True)) == reference_pieces(mask)
    diagonal = np.eye(3, dtype=np.uint8)  # touching only at corners: three pieces
    assert piece_counts(diagonal)[1].tolist() == [3]
    assert piece_counts(np.zeros((3, 3), np.uint8))[0].size == 0
    assert piece_counts(np.full((2, 2), 7, np.uint8))[1].tolist() == [1]


# Pair counts --------------------------------------------------------------------------


def test_several_children_per_parent_outside_and_split_children_are_counted():
    cells = boxes({1: (0, 10, 0, 10), 2: (0, 10, 12, 22), 3: (12, 22, 0, 10)})
    nuclei = boxes(
        {
            5: (1, 3, 1, 3),  # in cell 1
            6: (5, 7, 5, 7),  # in cell 1: two nuclei in one cell
            7: (2, 4, 14, 16),  # in cell 2
            8: (30, 32, 30, 32),  # outside every cell
            9: (4, 6, 9, 13),  # split between cells 1 and 2
        }
    )
    counts = pair_counts(nuclei, cells)
    assert counts["children"] == 5 and counts["parents"] == 3
    assert counts["inside"] == 3 and counts["inside_parent_larger"] == 3
    assert counts["outside_parents"] == 1
    assert counts["split_between_parents"] == 1
    assert (
        counts["parents_with_0_children"],
        counts["parents_with_1_child"],
        counts["parents_with_2_children"],
        counts["parents_with_3_or_more_children"],
    ) == (1, 1, 1, 0)
    assert counts["children_matched_one_to_one"] == 0


def test_pair_counts_of_an_empty_or_disjoint_child():
    cells = boxes({1: (0, 10, 0, 10)})
    assert pair_counts(np.zeros(SHAPE, np.uint16), cells)["parents_with_0_children"] == 1
    far = boxes({3: (40, 42, 40, 42)})
    counts = pair_counts(far, cells)
    assert (counts["outside_parents"], counts["parents_with_0_children"]) == (1, 1)


# Whole experiment ---------------------------------------------------------------------


def test_kinds_of_masks(tmp_path):
    report = scan(tmp_path, hierarchy_masks())
    kinds = {name: summary.kind for name, summary in report.types.items()}
    assert kinds == {
        "Cells": "instances",
        "Nuclei": "instances",
        "NucleiAlt": "instances",
        "Nucleoli": "instances",
        "Organoid": "one_value",
        "Vesicles": "grouped",
    }
    assert report.images == 2
    assert report.types["Cells"].objects == 8  # 4 per image
    assert report.types["Vesicles"].median_pieces_per_label == 4


def test_relationships_and_their_evidence(tmp_path):
    pairs = by_pair(scan(tmp_path, hierarchy_masks()))
    nuclei = pairs[("Nuclei", "Cells")]
    assert nuclei.relation == "inside" and nuclei.direct
    # Over two images: cell 1 has two nuclei, cells 2 and 4 one, cell 3 none.
    e = nuclei.evidence
    assert (e["children"], e["inside"]) == (8, 8)
    assert (
        e["parents_with_0_children"],
        e["parents_with_1_child"],
        e["parents_with_2_children"],
        e["parents_with_3_or_more_children"],
    ) == (2, 4, 2, 0)
    assert pairs[("Vesicles", "Cells")].relation == "grouped_by"
    assert pairs[("Cells", "Organoid")].relation == "inside"
    assert pairs[("Nuclei", "NucleiAlt")].relation == "same_objects"
    assert pairs[("NucleiAlt", "Nuclei")].relation == "same_objects"
    assert pairs[("Cells", "Nuclei")].relation == "none"  # parents are never inside children


def test_nested_hierarchy_keeps_direct_parents_only(tmp_path):
    report = scan(tmp_path, hierarchy_masks())
    pairs = by_pair(report)
    # Nucleoli are inside cells too, but through their nuclei.
    assert pairs[("Nucleoli", "Cells")].relation == "inside"
    assert not pairs[("Nucleoli", "Cells")].direct
    assert dict(report.hierarchy) == {
        "Cells": ("Organoid",),
        "Nuclei": ("Cells",),
        "NucleiAlt": ("Cells",),
        "Nucleoli": ("Nuclei", "NucleiAlt"),
        "Organoid": (),
        "Vesicles": ("Cells",),
    }
    assert report.same_objects == (("Nuclei", "NucleiAlt"),)


def test_a_mask_may_have_two_unrelated_direct_parents(tmp_path):
    left = np.zeros(SHAPE, np.uint8)
    left[:, :40] = 1
    top = np.zeros(SHAPE, np.uint8)
    top[:40, :] = 1
    dots = boxes({1: (5, 7, 5, 7), 2: (20, 22, 30, 32), 3: (30, 32, 10, 12)})
    report = scan(tmp_path, {"Left": left, "Top": top, "Dots": dots})
    assert report.hierarchy["Dots"] == ("Left", "Top")
    assert by_pair(report)[("Left", "Top")].relation == "none"  # same size: not nested


def test_label_numbers_shared_with_the_parent_do_not_make_a_mask_grouped(tmp_path):
    cells = boxes(CELLS)
    nuclei = boxes(
        {label: (r0 + 5, r0 + 10, c0 + 5, c0 + 10) for label, (r0, _, c0, _) in CELLS.items()}
    )
    pairs = by_pair(scan(tmp_path, {"Cells": cells, "Nuclei": nuclei}))
    assert pairs[("Nuclei", "Cells")].relation == "inside"
    assert pairs[("Nuclei", "Cells")].evidence["child_pixels_with_parent_label"] == 2 * 4 * 25


def test_report_file(tmp_path):
    report = scan(tmp_path, hierarchy_masks())
    path = write_mask_report(report, tmp_path / "out" / "mask_relationships.json")
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    data = json.loads(raw)
    assert data["report_version"] == "1"
    assert data["thresholds"]["inside_object_share"] == 0.9
    assert data["hierarchy"]["Nucleoli"] == ["Nuclei", "NucleiAlt"]
    assert data["same_objects"] == [["Nuclei", "NucleiAlt"]]
    assert {r["relation"] for r in data["relationships"]} == {
        "inside",
        "grouped_by",
        "same_objects",
    }
    assert len(data["pairs"]) == 6 * 5
    assert "morphobricks-preprocessing" in data["versions"]
    second = write_mask_report(report, tmp_path / "again.json")
    assert json.loads(second.read_bytes())["pairs"] == data["pairs"]


def test_progress_is_reported_per_image(tmp_path):
    root, folder = write_experiment(tmp_path, {"Cells": boxes(CELLS)})
    calls = []
    scan_masks(
        preflight(root, masks_folder=folder).masks, root, progress=lambda *a: calls.append(a)
    )
    assert calls == [(0, 2, "ctrl/a.tif"), (1, 2, "drug/a.tif"), (2, 2, "")]


# Thresholds ---------------------------------------------------------------------------


def test_thresholds_can_be_changed_and_are_checked():
    assert dict(mask_thresholds()) == dict(DEFAULT_THRESHOLDS)
    changed = mask_thresholds({"inside_object_share": 0.75, "grouped_median_pieces": 3})
    assert changed["inside_object_share"] == 0.75 and changed["grouped_median_pieces"] == 3
    assert changed["same_objects_iou"] == DEFAULT_THRESHOLDS["same_objects_iou"]
    for bad in (
        {"inside_share": 0.5},
        {"same_objects_iou": 0},
        {"inside_object_share": 1.2},
        {"same_objects_share": float("nan")},
        {"grouped_median_pieces": 0.5},
    ):
        with pytest.raises(ValueError):
            mask_thresholds(bad)


def test_changed_thresholds_change_the_relations_and_are_reported(tmp_path):
    # Half of the dots lie inside the region: not inside at 90%, inside at 50%.
    region = np.zeros(SHAPE, np.uint8)
    region[:, :32] = 1
    dots = boxes({1: (5, 7, 5, 7), 2: (20, 22, 10, 12), 3: (5, 7, 40, 42), 4: (20, 22, 50, 52)})
    root, folder = write_experiment(tmp_path, {"Region": region, "Dots": dots})
    inventory = preflight(root, masks_folder=folder).masks
    assert by_pair(scan_masks(inventory, root))[("Dots", "Region")].relation == "none"
    report = scan_masks(inventory, root, thresholds={"inside_object_share": 0.5})
    assert by_pair(report)[("Dots", "Region")].relation == "inside"
    assert report.to_dict()["thresholds"]["inside_object_share"] == 0.5
    # A stricter one-to-one match makes the two nuclei segmentations different objects.
    pairs = by_pair(scan(tmp_path / "strict", hierarchy_masks()))
    assert pairs[("Nuclei", "NucleiAlt")].relation == "same_objects"
    root, folder = write_experiment(tmp_path / "iou", hierarchy_masks())
    strict = scan_masks(
        preflight(root, masks_folder=folder).masks, root, thresholds={"same_objects_iou": 0.9}
    )
    assert by_pair(strict)[("Nuclei", "NucleiAlt")].relation != "same_objects"


def test_invalid_thresholds_stop_before_any_mask_is_read(tmp_path):
    root, folder = write_experiment(tmp_path, {"Cells": boxes(CELLS)})
    calls = []
    with pytest.raises(ValueError, match="at most 1"):
        scan_masks(
            preflight(root, masks_folder=folder).masks,
            root,
            progress=lambda *a: calls.append(a),
            thresholds={"inside_pixel_share": 2},
        )
    assert calls == []


def test_both_layouts_give_the_same_report(tmp_path):
    masks = hierarchy_masks()
    by_folder = scan(tmp_path / "folders", masks)
    root = tmp_path / "suffix" / "exp"
    folder = tmp_path / "suffix" / "masks"
    for rel in ("ctrl/a.tif", "drug/a.tif"):
        write_data(root / rel, np.zeros(SHAPE, dtype=np.uint8))
        for name, mask in masks.items():
            write_data(folder / rel.replace("a.tif", f"a_{name}.tif"), mask)
    inventory = preflight(root, masks_folder=folder).masks
    assert inventory.ok, inventory.issues
    by_suffix = scan_masks(inventory, root)
    assert by_suffix.to_dict()["pairs"] == by_folder.to_dict()["pairs"]
    assert dict(by_suffix.hierarchy) == dict(by_folder.hierarchy)


# Masks with time points (issue #36) ---------------------------------------------------


def test_masks_with_time_points_are_scanned_plane_by_plane(tmp_path):
    frames = hierarchy_masks()
    # Three time points: the same masks, then the nuclei moved, then no nuclei at all.
    moved = np.roll(frames["Nuclei"], 30, axis=1)
    series = {
        "Cells": np.stack([frames["Cells"]] * 3),
        "Nuclei": np.stack([frames["Nuclei"], moved, np.zeros_like(moved)]),
    }
    root, folder = tmp_path / "exp", tmp_path / "masks"
    write_data(root / "ctrl" / "a.tif", np.zeros((3, *SHAPE), dtype=np.uint8), "TYX")
    for name, data in series.items():
        write_data(folder / name / "ctrl" / "a.tif", data, "TYX")
    inventory = preflight(root, masks_folder=folder).masks
    assert inventory.ok, inventory.issues
    report = scan_masks(inventory, root)
    # The same as three single-plane images, one per time point.
    flat_root, flat_folder = tmp_path / "flat", tmp_path / "flat_masks"
    for t in range(3):
        write_data(flat_root / "ctrl" / f"a{t}.tif", np.zeros(SHAPE, dtype=np.uint8))
        for name, data in series.items():
            write_data(flat_folder / name / "ctrl" / f"a{t}.tif", data[t])
    flat = scan_masks(preflight(flat_root, masks_folder=flat_folder).masks, flat_root)
    assert report.images == 1 and flat.images == 3
    assert report.types["Nuclei"].objects == flat.types["Nuclei"].objects == 8
    assert report.types["Cells"].images == 1
    assert [(p.child, p.parent, dict(p.evidence)) for p in report.pairs] == [
        (p.child, p.parent, dict(p.evidence)) for p in flat.pairs
    ]
