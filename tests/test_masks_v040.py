"""Masks in v0.4.0: stated axes, layout, pairing, names and label images (design v0.4 §2, §3).

Masks are never converted or reordered: they are checked as stored against the images as
read. Plain-stack masks get their axes stated once per mask type; a 2-D mask is shared by
every plane of its image only when stated; a mask stored with Y and X the other way round
is refused with a ``swapaxes`` hint; files named like masks are blocked as images; the
label images of OME-Zarr images are always used and fingerprinted apart from their image.
"""

import json

import numpy as np
import pytest

from morphobricks_preprocessing import (
    MASK_NAME_SUFFIXES,
    AxisMapping,
    find_masks,
    mask_axes_option,
    prefill_mapping,
    preflight,
    read_manifest,
    read_mask,
    run_experiment,
)
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.manifest import source_fingerprint
from morphobricks_preprocessing.masks.stated import mask_roles
from tiff_tree import write_data, write_file, write_plain


def codes(issues):
    return [i.code for i in issues]


def errors(issues):
    return [i.code for i in issues if i.is_error]


def one(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


def labels(shape, start=1):
    return (np.arange(np.prod(shape)).reshape(shape) % 7 + start).astype(np.uint16)


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
    write_plain(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)))
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
    write_plain(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)))
    ok = run_experiment(
        root, prefill_mapping("TYX"), masks_folder=masks, mask_axes={"Cells": "TYX"}
    )
    assert ok.written
    (recorded,) = read_manifest(ok.manifest_path).masks["files"]
    assert (recorded["roles"], recorded["axes_from"], recorded["shared"]) == (
        "TYX",
        "stated",
        False,
    )
    # The axes as stored, beside the roles they are read as: nothing is reordered.
    assert (recorded["axes"], recorded["shape"]) == ("IYX", [3, 6, 8])
    # Stated as depth, the mask's planes are not the time-lapse's.
    wrong = run_experiment(
        root, prefill_mapping("TYX"), masks_folder=masks, mask_axes={"Cells": "ZYX"}
    )
    assert errors(wrong.issues) == ["mask_planes_mismatch"]


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
    write_plain(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)))
    issue = one(inventory(root, masks, mask_axes={"Cells": stated}).issues, "invalid_mask_axes")
    assert problem in issue.message


def test_axes_stated_for_an_unknown_mask_type(timelapse):
    root, masks = timelapse
    write_data(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)), "TYX")
    issue = one(inventory(root, masks, mask_axes={"Nuclei": "TYX"}).issues, "invalid_mask_axes")
    assert "Nuclei" in issue.message and "found: Cells" in issue.message


def test_a_2d_mask_on_a_stack_needs_a_shared_statement(timelapse):
    root, masks = timelapse
    write_data(masks / "Cells/ctrl/r1/a.tif", labels((6, 8)), "YX")
    refused = run_experiment(root, prefill_mapping("TYX"), masks_folder=masks)
    assert "mask_planes_mismatch" in errors(refused.issues)
    # Stated YX for the type: one plane shared by every plane of the image.
    shared = run_experiment(
        root, prefill_mapping("TYX"), masks_folder=masks, mask_axes={"Cells": "YX"}
    )
    assert shared.written
    (recorded,) = read_manifest(shared.manifest_path).masks["files"]
    assert (recorded["axes_from"], recorded["shared"]) == ("stated", True)
    pre = preflight(root, masks_folder=masks, mask_axes={"Cells": "YX"})
    (mask,) = pre.masks.files
    assert mask.shared and mask.axes_from == "stated"
    np.testing.assert_array_equal(read_mask(mask, pre.masks.folder, root, t=2), labels((6, 8)))


def test_a_2d_mask_on_a_single_plane_image_needs_no_statement(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    write_plain(tmp_path / "masks/Cells/ctrl/a.tif", labels((6, 8)))
    result = run_experiment(root, prefill_mapping("YX"), masks_folder=tmp_path / "masks")
    assert result.written
    (recorded,) = read_manifest(result.manifest_path).masks["files"]
    assert (recorded["axes_from"], recorded["shared"]) == ("header", False)


def test_a_named_tyx_mask_keeps_its_own_axes(timelapse):
    root, masks = timelapse
    write_data(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)), "TYX")
    assert run_experiment(root, AxisMapping("TYX", "TYX"), masks_folder=masks).written


def test_mask_roles():
    assert mask_roles("TYX", "TYX", None) == ("TYX", None, "")
    assert mask_roles("QYX", "???", "TYX") == ("TYX", None, "")
    assert mask_roles("QYX", "???", "TXY")[1] == "mask_layout"
    assert mask_roles("QYX", "???", None)[1] == "mask_not_2d"


def test_the_mask_axes_option():
    assert mask_axes_option(["Cells=TYX", " Nuclei = YX "]) == {"Cells": "TYX", "Nuclei": "YX"}
    assert mask_axes_option(["Cells=TYX", "Cells=TYX"]) == {"Cells": "TYX"}
    with pytest.raises(ValueError, match="give the mask type and its roles"):
        mask_axes_option(["TYX"])
    with pytest.raises(ValueError, match="two different roles for Cells"):
        mask_axes_option(["Cells=TYX", "Cells=ZYX"])


def test_the_command_line_states_mask_axes(timelapse, capsys):
    root, masks = timelapse
    write_plain(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)))
    args = ["check", str(root), "--masks", str(masks)]
    assert main(args) == 1
    assert "--mask-axes Cells=TYX" in capsys.readouterr().out
    assert main([*args, "--mask-axes", "Cells=TYX"]) == 0
    assert main([*args, "--mask-axes", "TYX"]) == 1
    assert "give the mask type and its roles" in capsys.readouterr().out


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
    assert "mask_size_mismatch" not in codes(result.issues) and not result.written


# Pairing -------------------------------------------------------------------------------


def test_unpaired_masks_stay_a_warning(tmp_path):
    # Design v0.4 §3: every existing check keeps its severity.
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    write_data(tmp_path / "masks/Cells/ctrl/a.tif", labels((6, 8)), "YX")
    write_data(tmp_path / "masks/Cells/ctrl/zz.tif", labels((6, 8)), "YX")
    write_file(tmp_path / "masks/Cells/ctrl/a.png")
    pre = preflight(root, masks_folder=tmp_path / "masks")
    issue = one(pre.masks.issues, "mask_without_image")
    assert pre.masks.ok and issue.paths == ("Cells/ctrl/a.png", "Cells/ctrl/zz.tif")


def test_masks_of_excluded_images_are_reported_apart(tmp_path):
    root = tmp_path / "exp"
    for name in ("a", "b"):
        write_data(root / f"ctrl/{name}.tif", np.zeros((6, 8), np.uint8), "YX")
        write_data(tmp_path / f"masks/Cells/ctrl/{name}.tif", labels((6, 8)), "YX")
    pre = preflight(root, masks_folder=tmp_path / "masks", exclude=["ctrl/b.tif"])
    assert pre.masks.ok and codes(pre.masks.issues) == ["mask_of_excluded_image"]
    assert one(pre.masks.issues, "mask_of_excluded_image").paths == ("Cells/ctrl/b.tif",)
    assert [f.image for f in pre.masks.files] == ["ctrl/a.tif"]


def test_the_output_folder_may_not_be_inside_the_masks_folder(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    masks = tmp_path / "masks"
    write_data(masks / "Cells/ctrl/a.tif", labels((6, 8)), "YX")
    result = run_experiment(
        root, prefill_mapping("YX"), masks_folder=masks, output_folder=masks / "out"
    )
    assert errors(result.issues) == ["output_inside_masks"] and not result.written
    assert one(result.issues, "output_inside_masks").paths == ("out",)


# Files named like masks are blocked as images (design v0.4 §3) ---------------------------


@pytest.mark.parametrize("name", ["a_cp_masks.tif", "a_masks.tif", "a_mask.tif", "a_SEG.tif"])
def test_files_named_like_masks_are_blocked_as_images(tmp_path, name):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    write_data(root / f"ctrl/{name}", labels((6, 8)), "YX")
    result = run_experiment(root, prefill_mapping("YX"))
    issue = one(result.issues, "image_named_as_mask")
    assert issue.is_error and issue.paths == (f"ctrl/{name}",) and not result.written
    assert "(of ctrl/a.tif)" in issue.message and "exclude them" in issue.action
    # Excluded, it is no longer an image, so nothing is blocked.
    assert run_experiment(root, prefill_mapping("YX"), exclude=[f"ctrl/{name}"]).written


def test_the_mask_name_suffixes_include_cellposes():
    assert {"_cp_masks", "_masks"} < set(MASK_NAME_SUFFIXES)


def test_a_mask_named_after_an_image_in_another_folder_is_blocked(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/rep1/f2.tif", np.zeros((6, 8), np.uint16), "YX")
    write_data(root / "drug/rep1/f2_SEG.ome.tif", labels((6, 8)), "YX")
    pre = preflight(root)
    issue = one(pre.issues, "image_named_as_mask")
    assert "drug/rep1/f2_SEG.ome.tif (of ctrl/rep1/f2.tif)" in issue.message
    assert [i.code for i in pre.issues if i.is_error] == ["image_named_as_mask"]


# OME-Zarr label images: always used, fingerprinted on their own (design v0.4 §2) -----------


def zarr_experiment(tmp_path):
    pytest.importorskip("zarr")
    from zarr_tree import write_labels, write_ngff

    path = write_ngff(tmp_path / "exp/ctrl/a.ome.zarr", np.zeros((6, 8), np.uint8), "yx")
    write_labels(path, {"cells": labels((6, 8))}, "yx")
    return tmp_path / "exp", path


def test_label_images_are_always_used(tmp_path):
    root, _ = zarr_experiment(tmp_path)
    with pytest.raises(TypeError, match="ome_zarr_labels"):
        preflight(root, ome_zarr_labels=True)  # removed: there is no flag any more
    result = run_experiment(root, prefill_mapping("YX"))
    assert result.written
    recorded = read_manifest(result.manifest_path).masks
    assert recorded["folder"] is None and recorded["types"] == ["cells"]
    (entry,) = recorded["files"]
    size, mtime_ns = source_fingerprint(root / "ctrl/a.ome.zarr/labels/cells")
    assert (entry["size"], entry["mtime_ns"]) == (size, mtime_ns)


def test_label_images_are_left_out_of_the_image_fingerprint(tmp_path):
    from zarr_tree import write_labels

    root, path = zarr_experiment(tmp_path)
    before = source_fingerprint(path)
    first = run_experiment(root, prefill_mapping("YX"))
    write_labels(path, {"cells": labels((6, 8), 2), "nuclei": labels((6, 8))}, "yx")
    assert source_fingerprint(path) == before
    again = run_experiment(root, prefill_mapping("YX"), output_folder=first.output_folder)
    assert again.read == ()  # the image's rows are reused
    data = json.loads(again.manifest_path.read_text(encoding="utf-8"))
    assert data["masks"]["types"] == ["cells", "nuclei"]
