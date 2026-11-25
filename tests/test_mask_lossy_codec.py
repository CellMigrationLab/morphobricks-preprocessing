"""Masks stored with a lossy codec are refused, naming the file and the codec (#360)."""

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import find_masks, preflight, read_mask, read_mask_file
from morphobricks_preprocessing.masks.model import FOLDER, MaskFile
from tiff_tree import write_data

LABELS = (np.arange(48).reshape(6, 8) % 5).astype(np.uint8)


def _tree(tmp_path, **kwargs):
    root = tmp_path / "exp"
    write_data(root / "ctrl/r1/a.tif", np.zeros((6, 8), np.uint8), "YX")
    masks = tmp_path / "masks"
    path = masks / "Cells/ctrl/r1/a.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(path, LABELS, **kwargs)
    return root, masks, path


def _inventory(root, masks):
    pre = preflight(root)
    return find_masks(masks, pre.discovery, pre.metadata.files, bind=True)


def test_a_jpeg_mask_is_refused_by_the_inventory_naming_file_and_codec(tmp_path):
    root, masks, _ = _tree(tmp_path, compression="jpeg")
    found = _inventory(root, masks)
    (issue,) = [i for i in found.issues if i.is_error]
    assert issue.code == "mask_lossy_codec"
    assert tuple(issue.paths) == ("Cells/ctrl/r1/a.tif",)
    assert "Cells/ctrl/r1/a.tif: JPEG" in issue.message
    assert "lossless" in issue.action


def test_a_jpeg_mask_is_refused_by_the_direct_reads(tmp_path):
    _, masks, path = _tree(tmp_path, compression="jpeg")
    with pytest.raises(ValueError, match=r"^mask_lossy_codec: .*a\.tif.* JPEG"):
        read_mask_file(path)
    mask = MaskFile(
        "ctrl/r1/a.tif", "Cells", "Cells/ctrl/r1/a.tif", FOLDER, (6, 8), np.dtype("uint8"), "YX"
    )
    with pytest.raises(ValueError, match=r"^mask_lossy_codec: .*JPEG"):
        read_mask(mask, masks, tmp_path)


@pytest.mark.parametrize("compression", [None, "zlib", "lzw"])
def test_a_lossless_mask_is_read_as_before(tmp_path, compression):
    root, masks, path = _tree(tmp_path, compression=compression)
    found = _inventory(root, masks)
    assert not [i for i in found.issues if i.is_error]
    np.testing.assert_array_equal(read_mask_file(path), LABELS)


def test_a_jpeg_mask_window_is_refused(tmp_path):
    from morphobricks_preprocessing.masks.window import read_mask_window

    _, masks, _ = _tree(tmp_path, compression="jpeg")
    mask = MaskFile(
        "ctrl/r1/a.tif", "Cells", "Cells/ctrl/r1/a.tif", FOLDER, (6, 8), np.dtype("uint8"), "YX"
    )
    with pytest.raises(ValueError, match=r"^mask_lossy_codec: "):
        read_mask_window(mask, masks, tmp_path, bounds=(0, 2, 0, 2))
