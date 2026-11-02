"""PP-10c: which mask types need their axes stated, and the statements that fit them."""

import numpy as np

from morphobricks_preprocessing import find_masks, preflight
from morphobricks_preprocessing.mask_review import mask_axes_questions, mask_type_rows
from tiff_tree import write_data, write_plain


def inventory(root, masks, **kwargs):
    pre = preflight(root)
    return find_masks(masks, pre.discovery, pre.metadata.files, **kwargs)


def test_only_masks_without_named_axes_are_asked_about(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    for rel in ["ctrl/r1/a.tif", "ctrl/r1/b.tif"]:
        write_data(root / rel, np.zeros((3, 8, 8), np.uint8), "TYX")
        write_plain(masks / "Cells" / rel, np.ones((3, 8, 8), np.uint16))  # IYX
        write_data(masks / "Nuclei" / rel, np.ones((3, 8, 8), np.uint16), "TYX")
    found = inventory(root, masks)
    (question,) = mask_axes_questions(found, found.issues, {})
    assert question.mask_type == "Cells" and question.header_axes == ("IYX",)
    assert question.stated is None and not question.shared
    assert question.choices[:2] == ("TYX", "ZYX")
    assert all(c.index("Y") < c.index("X") for c in question.choices)  # never X before Y
    rows = {row.mask_type: row for row in mask_type_rows(found, ["ctrl/r1/a.tif", "x.tif"])}
    assert rows["Nuclei"].axes == ("TYX",) and rows["Nuclei"].without == ("x.tif",)
    assert rows["Cells"].axes == ()  # unknown until stated
    stated = inventory(root, masks, mask_axes={"Cells": "TYX"})
    (again,) = mask_axes_questions(stated, stated.issues, {"Cells": "TYX"})
    assert again.stated == "TYX" and stated.ok  # asked again, so it can be changed


def test_masks_of_one_type_stored_differently_have_no_statement(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_data(root / "ctrl/r1/a.tif", np.zeros((3, 8, 8), np.uint8), "TYX")
    write_data(root / "ctrl/r1/b.tif", np.zeros((3, 8, 8), np.uint8), "TYX")
    write_plain(masks / "Cells/ctrl/r1/a.tif", np.ones((3, 8, 8), np.uint16))
    write_data(masks / "Cells/ctrl/r1/b.tif", np.ones((8, 8), np.uint16), "YX")
    found = inventory(root, masks)
    (question,) = mask_axes_questions(found, found.issues, {})
    assert question.choices == ()
