"""The direct mask reads refuse a mask whose axes are not confirmed (#478)."""

import numpy as np
import pytest

from morphobricks_preprocessing import find_masks, preflight, read_mask
from morphobricks_preprocessing.masks.window import read_mask_window
from tiff_tree import write_data, write_plain

FRAMES = np.stack([np.full((6, 8), 100 + t, np.uint16) for t in range(3)])


def test_a_mask_whose_axes_are_not_confirmed_is_refused(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_data(root / "ctrl/r1/a.tif", np.zeros((3, 6, 8), np.uint8), "TYX")
    write_plain(masks / "Cells/ctrl/r1/a.tif", FRAMES)
    pre = preflight(root)
    found = find_masks(masks, pre.discovery, pre.metadata.files, bind=True)
    (mask,) = found.files
    assert mask.axes == "" and mask.header_axes == "IYX"
    for call in (
        lambda: read_mask(mask, found.folder, root),
        lambda: read_mask(mask, found.folder, root, t=0),
        lambda: read_mask_window(mask, found.folder, root, bounds=(0, 2, 0, 2)),
    ):
        with pytest.raises(ValueError, match="axes are not confirmed") as caught:
            call()
        assert "Cells/ctrl/r1/a.tif" in str(caught.value) and "IYX" in str(caught.value)
