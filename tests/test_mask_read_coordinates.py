"""The direct mask reads refuse non-integer coordinates (#477)."""

import numpy as np
import pytest

from morphobricks_preprocessing import find_masks, preflight, read_mask, read_mask_file
from morphobricks_preprocessing.masks.window import read_mask_window
from tiff_tree import write_data

FRAMES = np.stack([np.full((6, 8), 100 + t, np.uint16) for t in range(3)])


@pytest.fixture
def tyx_mask(tmp_path):
    """A 3-frame time-lapse and its TYX mask, found with its header bound."""
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_data(root / "ctrl/r1/a.tif", np.zeros((3, 6, 8), np.uint8), "TYX")
    path = write_data(masks / "Cells/ctrl/r1/a.tif", FRAMES, "TYX")
    pre = preflight(root)
    found = find_masks(masks, pre.discovery, pre.metadata.files, bind=True)
    assert found.ok
    (mask,) = found.files
    return mask, found.folder, root, path


@pytest.mark.parametrize("t", [True, False, np.bool_(True), 1.0, 1.5, "1", float("nan")])
def test_a_coordinate_that_is_not_an_integer_is_refused(tyx_mask, t):
    mask, folder, root, path = tyx_mask
    with pytest.raises(ValueError, match="whole number"):
        read_mask(mask, folder, root, t=t)
    with pytest.raises(ValueError, match="whole number"):
        read_mask_file(path, t=t)
    with pytest.raises(ValueError, match="whole number"):
        read_mask_window(mask, folder, root, bounds=(0, 2, 0, 2), t=t)


@pytest.mark.parametrize("t", [1, np.int64(1), np.uint8(1)])
def test_integer_coordinates_read_their_plane(tyx_mask, t):
    mask, folder, root, path = tyx_mask
    np.testing.assert_array_equal(read_mask(mask, folder, root, t=t), FRAMES[1])
    np.testing.assert_array_equal(read_mask_file(path, t=t), FRAMES[1])
