"""#484: mask-axis statements belong to the masks folder they were made for.

A plain stack's axes are a statement the user made about the files of one masks folder. A
later run given another masks folder must not reuse it, even for a mask type of the same
name and shape: each of the new folder's plain stacks is refused (``mask_not_2d``) until
its axes are stated again. The same folder keeps its statements, and OME-Zarr label images
(in the experiment, not in the masks folder) keep theirs.
"""

import numpy as np
import pytest

from described_tree import run_described
from morphobricks_preprocessing import prefill_mapping, read_manifest
from morphobricks_preprocessing.run.recorded import GivenInputs, resolve_inputs
from morphobricks_preprocessing.run.reuse import read_previous
from tiff_tree import write_data, write_plain
from zarr_tree import write_labels, write_ngff

IMAGES = ["ctrl/r1/a.tif", "drug/r1/a.tif"]


def stated_run(tmp_path):
    """Run once with the old masks folder's plain stacks stated ``Cells=TYX``."""
    root, output = tmp_path / "exp", tmp_path / "out"
    old, new = tmp_path / "old-segmentation", tmp_path / "new-segmentation"
    for seed, rel in enumerate(IMAGES):
        write_data(root / rel, np.random.default_rng(seed).integers(0, 9, (3, 8, 8), "u1"), "TYX")
        for folder in (old, new):  # unnamed IYX headers, the same shape in both folders
            write_plain(folder / "Cells" / rel, np.ones((3, 8, 8), np.uint16))
    first = run_described(
        root,
        prefill_mapping("TYX"),
        output_folder=output,
        masks_folder=old,
        mask_axes={"Cells": "TYX"},
    )
    assert first.written, first.issues
    return root, output, old, new


def test_another_masks_folder_does_not_inherit_the_stated_mask_axes(tmp_path):
    root, output, old, new = stated_run(tmp_path)
    previous = read_previous(output)[0]
    switched = resolve_inputs(GivenInputs(masks_folder=new.resolve()), previous)
    assert switched.masks_folder == new.resolve() and switched.mask_axes == {}
    change = next(change for change in switched.changes if change.name == "masks_folder")
    assert "Cells" in change.effect and "state them again" in change.effect
    result = run_described(root, output_folder=output, masks_folder=new)
    assert not result.written
    refused = [issue for issue in result.issues if issue.code == "mask_not_2d"]
    assert refused and all("--mask-axes Cells=TYX" in issue.action for issue in refused)


def test_the_same_masks_folder_keeps_its_stated_mask_axes(tmp_path):
    root, output, old, _ = stated_run(tmp_path)
    previous = read_previous(output)[0]
    for given in (None, old.resolve()):
        kept = resolve_inputs(GivenInputs(masks_folder=given), previous)
        assert kept.mask_axes == {"Cells": "TYX"} and kept.changes == ()
    result = run_described(root, output_folder=output, masks_folder=old)
    assert result.written, result.issues
    assert result.input_changes == ()


def test_another_masks_folder_runs_with_its_axes_stated_again(tmp_path):
    root, output, _, new = stated_run(tmp_path)
    result = run_described(root, output_folder=output, masks_folder=new, mask_axes={"Cells": "TYX"})
    assert result.written, result.issues
    recorded = read_manifest(result.manifest_path).masks
    assert recorded["folder"] == new.resolve().as_posix()
    assert {(f["type"], f["roles"], f["axes_from"]) for f in recorded["files"]} == {
        ("Cells", "TYX", "stated")
    }


def test_another_masks_folder_keeps_the_mask_axes_of_label_images(tmp_path):
    """Label images are the experiment's, not the masks folder's: their statements stay."""
    pytest.importorskip("zarr")
    root, output = tmp_path / "exp", tmp_path / "out"
    old, new = tmp_path / "old-segmentation", tmp_path / "new-segmentation"
    for rel in ["ctrl/r1/a.ome.zarr", "drug/r1/a.ome.zarr"]:
        image = write_ngff(root / rel, np.zeros((2, 8, 8), np.uint8), "tyx")
        write_labels(image, {"lab": np.ones((2, 8, 8), np.uint16)}, "qyx")  # unnamed: stated
    for rel in ["ctrl/r1/b.tif", "drug/r1/b.tif"]:
        write_data(root / rel, np.ones((2, 8, 8), np.uint8), "TYX")
        for folder in (old, new):
            write_plain(folder / "Cells" / rel, np.ones((2, 8, 8), np.uint16))
    first = run_described(
        root,
        prefill_mapping("TYX"),
        output_folder=output,
        masks_folder=old,
        mask_axes={"Cells": "TYX", "lab": "TYX"},
    )
    assert first.written, first.issues
    previous = read_previous(output)[0]
    switched = resolve_inputs(GivenInputs(masks_folder=new.resolve()), previous)
    assert switched.mask_axes == {"lab": "TYX"}
