"""Mask names, pairing and layout in the one discovery-and-validation step (v0.4.0).

Masks are never converted or reordered: they are checked as stored against the images as
read. Plain-stack masks get their axes stated once per mask type; a mask stored with Y and
X the other way round is refused with a ``swapaxes`` hint; every mask pairs with exactly
one image and type; files named like masks are blocked as images (owner decisions of 1
November 2026, design audit M-3, m-6).
"""

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    AxisMapping,
    find_masks,
    prefill_mapping,
    preflight,
    read_mask,
    run_experiment,
)
from morphobricks_preprocessing.mask_axes import mask_roles
from tiff_tree import write_data, write_file


def codes(issues):
    return [i.code for i in issues]


def one(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


def labels(shape, start=1):
    return (np.arange(np.prod(shape)).reshape(shape) % 7 + start).astype(np.uint16)


def plain(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, photometric="minisblack", metadata=None)
    return path


@pytest.fixture
def timelapse(tmp_path):
    """One 3-frame time-lapse (TYX, 6 x 8) and its masks folder."""
    root = tmp_path / "exp"
    write_data(root / "ctrl/r1/a.tif", np.zeros((3, 6, 8), np.uint8), "TYX")
    return root, tmp_path / "masks"


def inventory(root, masks, **kwargs):
    pre = preflight(root)
    return find_masks(masks, pre.discovery, pre.metadata.files, **kwargs)


# Stated mask axes ----------------------------------------------------------------------


def test_a_plain_stack_mask_needs_its_axes_stated(timelapse):
    root, masks = timelapse
    plain(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)))
    found = inventory(root, masks)
    issue = one(found.issues, "mask_not_2d")
    assert "header axes IYX" in issue.message and "--mask-axes Cells=TYX" in issue.action
    stated = inventory(root, masks, mask_axes={"Cells": "TYX"})
    assert stated.ok
    (mask,) = stated.files
    assert (mask.axes, mask.header_axes, mask.axes_from) == ("TYX", "IYX", "stated")
    # Read by the stated roles, as stored: frame 2 is the file's third page.
    plane = read_mask(mask, stated.folder, root, t=2)
    np.testing.assert_array_equal(plane, labels((3, 6, 8))[2])


def test_the_run_checks_stated_mask_planes_against_the_image(timelapse):
    root, masks = timelapse
    plain(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)))
    ok = run_experiment(
        root, prefill_mapping("TYX"), masks_folder=masks, mask_axes={"Cells": "TYX"}
    )
    assert ok.written
    # Stated as depth, the mask's planes are not the time-lapse's.
    wrong = run_experiment(
        root, prefill_mapping("TYX"), masks_folder=masks, mask_axes={"Cells": "ZYX"}
    )
    assert codes(i for i in wrong.issues if i.is_error) == ["mask_planes_mismatch"]


def test_stated_axes_may_not_contradict_a_named_mask(timelapse):
    root, masks = timelapse
    write_data(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)), "TYX")
    found = inventory(root, masks, mask_axes={"Cells": "ZYX"})
    issue = one(found.issues, "mask_axes_contradict_header")
    assert "T stated Z" in issue.message
    # Stating what the header names is fine; a named TYX mask keeps its own axes.
    assert inventory(root, masks, mask_axes={"Cells": "TYX"}).ok
    assert inventory(root, masks).ok


@pytest.mark.parametrize(
    ("stated", "problem"),
    [("TY", "2 roles for 3 axes"), ("CYX", "C is not a mask role"), ("TYY", "Y and X")],
)
def test_invalid_stated_axes(timelapse, stated, problem):
    root, masks = timelapse
    plain(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)))
    issue = one(inventory(root, masks, mask_axes={"Cells": stated}).issues, "invalid_mask_axes")
    assert problem in issue.message


def test_axes_stated_for_an_unknown_mask_type(timelapse):
    root, masks = timelapse
    write_data(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)), "TYX")
    issue = one(inventory(root, masks, mask_axes={"Nuclei": "TYX"}).issues, "invalid_mask_axes")
    assert "Nuclei" in issue.message and "found: Cells" in issue.message


# Layout: never transposed ------------------------------------------------------------


def test_a_mask_stored_x_before_y_is_refused_with_a_swapaxes_hint(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    write_data(tmp_path / "masks/Cells/ctrl/a.tif", labels((8, 6)), "XY")
    pre = preflight(root, masks_folder=tmp_path / "masks")
    issue = one(pre.masks.issues, "mask_layout")
    assert "numpy.swapaxes(mask, -1, -2)" in issue.action
    assert "X is stored before Y" in issue.message


def test_a_mask_with_the_images_height_and_width_swapped_is_a_layout_error(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    write_data(tmp_path / "masks/Cells/ctrl/a.tif", labels((8, 6)), "YX")
    result = run_experiment(root, prefill_mapping("YX"), masks_folder=tmp_path / "masks")
    issue = one(result.issues, "mask_layout")
    assert issue.paths == ("Cells/ctrl/a.tif",) and "swapaxes" in issue.action
    assert "mask_size_mismatch" not in codes(result.issues)


def test_mask_roles():
    assert mask_roles("TYX", "TYX", None) == ("TYX", None, "")
    assert mask_roles("QYX", "???", "TYX") == ("TYX", None, "")
    assert mask_roles("QYX", "???", "TXY")[1] == "mask_layout"
    assert mask_roles("QYX", "???", None)[1] == "mask_not_2d"


# Pairing -------------------------------------------------------------------------------


def test_every_mask_pairs_with_exactly_one_image(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    write_data(tmp_path / "masks/Cells/ctrl/a.tif", labels((6, 8)), "YX")
    write_data(tmp_path / "masks/Cells/ctrl/zz.tif", labels((6, 8)), "YX")
    pre = preflight(root, masks_folder=tmp_path / "masks")
    issue = one(pre.masks.issues, "mask_without_image")
    assert issue.is_error and issue.paths == ("Cells/ctrl/zz.tif",)


def test_masks_of_excluded_images_are_reported_apart(tmp_path):
    root = tmp_path / "exp"
    for name in ("a", "b"):
        write_data(root / f"ctrl/{name}.tif", np.zeros((6, 8), np.uint8), "YX")
        write_data(tmp_path / f"masks/Cells/ctrl/{name}.tif", labels((6, 8)), "YX")
    pre = preflight(root, masks_folder=tmp_path / "masks", exclude=["ctrl/b.tif"])
    assert pre.masks.ok
    assert one(pre.masks.issues, "mask_of_excluded_image").paths == ("Cells/ctrl/b.tif",)


def test_non_tiff_files_in_the_masks_folder_are_warned(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    write_data(tmp_path / "masks/Cells/ctrl/a.tif", labels((6, 8)), "YX")
    write_file(tmp_path / "masks/Cells/ctrl/a.png")
    pre = preflight(root, masks_folder=tmp_path / "masks")
    assert pre.masks.ok and one(pre.masks.issues, "mask_outside_type_folder").paths == (
        "Cells/ctrl/a.png",
    )


@pytest.mark.parametrize("name", ["a_cp_masks.tif", "a_masks.tif", "a_mask.tif"])
def test_files_named_like_masks_are_blocked_as_images(tmp_path, name):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    write_data(root / f"ctrl/{name}", labels((6, 8)), "YX")
    result = run_experiment(root, prefill_mapping("YX"))
    issue = one(result.issues, "image_named_as_mask")
    assert issue.is_error and issue.paths == (f"ctrl/{name}",) and not result.written
    # Excluded, it is no longer an image, so nothing is blocked.
    assert run_experiment(root, prefill_mapping("YX"), exclude=[f"ctrl/{name}"]).written


def test_a_named_tyx_mask_keeps_its_own_axes(timelapse):
    root, masks = timelapse
    write_data(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)), "TYX")
    result = run_experiment(root, AxisMapping("TYX", "TYX"), masks_folder=masks)
    assert result.written


def test_a_2d_mask_on_a_stack_needs_a_shared_statement(timelapse):
    root, masks = timelapse
    write_data(masks / "Cells/ctrl/r1/a.tif", labels((6, 8)), "YX")
    refused = run_experiment(root, prefill_mapping("TYX"), masks_folder=masks)
    assert "mask_planes_mismatch" in codes(i for i in refused.issues if i.is_error)
    # Stated YX for the type: one plane shared by every plane of the image.
    shared = run_experiment(
        root, prefill_mapping("TYX"), masks_folder=masks, mask_axes={"Cells": "YX"}
    )
    assert shared.written
    pre = preflight(root, masks_folder=masks, mask_axes={"Cells": "YX"})
    (mask,) = pre.masks.files
    assert mask.shared and mask.axes_from == "stated"
    np.testing.assert_array_equal(read_mask(mask, pre.masks.folder, root, t=2), labels((6, 8)))
