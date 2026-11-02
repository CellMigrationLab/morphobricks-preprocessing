"""Checks bricks rely on (PP-8b): X-before-Y masks refused, unrecorded files named."""

import json
import pickle
import sys

import numpy as np
import pytest

from morphobricks_preprocessing import (
    MANIFEST_FILENAME,
    RunInputs,
    RunInputsError,
    UnrecordedFilesError,
    find_masks,
    prefill_mapping,
    run_experiment,
)
from morphobricks_preprocessing.discovery import discover_experiment
from morphobricks_preprocessing.mask_record import mask_manifest
from morphobricks_preprocessing.metadata import inspect_experiment
from morphobricks_preprocessing.testing import make_clean_run
from tiff_tree import write_data, write_file
from zarr_tree import write_labels, write_ngff


def _labels(shape):
    labels = np.zeros(shape, np.uint16)
    labels[0, 0] = 1
    return labels


def _codes(issues):
    return sorted({issue.code for issue in issues})


# 1. A mask whose header puts X before Y ----------------------------------------------


@pytest.mark.parametrize(("axes", "shape"), [("XY", (8, 6)), ("TXY", (3, 8, 6))])
def test_a_run_refuses_a_mask_whose_header_puts_x_before_y(tmp_path, axes, shape):
    # rc1 wrote this run, recording the mask with axes "" (CellPatcher PR 69, question 2).
    root = tmp_path / "exp"
    image_shape = (6, 8) if len(shape) == 2 else (3, 6, 8)
    write_data(root / "ctrl/a.tif", np.zeros(image_shape, np.uint8), axes[:-2] + "YX")
    write_data(tmp_path / "masks/Cells/ctrl/a.tif", _labels(shape), axes)
    result = run_experiment(
        root, prefill_mapping(axes[:-2] + "YX"), masks_folder=tmp_path / "masks"
    )
    assert not result.written
    assert not (result.output_folder / MANIFEST_FILENAME).exists()
    (issue,) = [i for i in result.issues if i.code == "mask_layout"]
    assert issue.paths == ("Cells/ctrl/a.tif",)
    assert f"Cells/ctrl/a.tif: header axes {axes}: X is stored before Y" in issue.message
    assert "numpy.swapaxes(mask, -1, -2)" in issue.action


def test_a_mask_without_roles_is_never_recorded(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    write_data(tmp_path / "masks/Cells/ctrl/a.tif", _labels((8, 6)), "XY")
    discovery = discover_experiment(root)
    inventory = find_masks(tmp_path / "masks", discovery, inspect_experiment(discovery).files)
    assert inventory.files[0].axes == ""
    with pytest.raises(ValueError, match="Masks without roles cannot be recorded"):
        mask_manifest(inventory, root)


def test_run_inputs_refuses_an_older_mask_record_without_axes(tmp_path):
    clean = make_clean_run(tmp_path, mask_types=("Cells",))
    path = clean.output / MANIFEST_FILENAME
    manifest = json.loads(path.read_text())
    manifest["masks"]["files"][0].update(axes="", shape=[], roles="")
    path.write_text(json.dumps(manifest))
    with pytest.raises(RunInputsError) as raised:
        RunInputs.open(clean.output)
    message = str(raised.value)
    assert "without axes: Cells/ctrl/rep1/a.tif" in message
    assert "numpy.swapaxes" in message and "changed or missing" not in message


# 2. Files the run did not record -----------------------------------------------------


@pytest.fixture
def clean(tmp_path):
    return make_clean_run(tmp_path, mask_types=("Cells",))


def test_an_image_added_after_the_run_is_named(clean):
    write_data(clean.experiment / "drug/rep1/c.tif", np.zeros((2, 16, 20), np.uint16), "CYX")
    with pytest.raises(UnrecordedFilesError) as raised:
        RunInputs.open(clean.output)
    assert raised.value.files.images == ("drug/rep1/c.tif",)
    assert raised.value.files.masks == ()
    message = str(raised.value)
    assert "1 image (drug/rep1/c.tif)" in message
    assert "Run preprocessing again" in message and "--exclude" in message
    assert isinstance(raised.value, RunInputsError)
    again = pickle.loads(pickle.dumps(raised.value))
    assert again.files == raised.value.files and str(again) == message


def test_an_image_in_a_moved_experiment_is_named(clean, tmp_path):
    moved = tmp_path / "moved"
    clean.experiment.rename(moved)
    write_data(moved / "ctrl/rep1/new.ome.tif", np.zeros((2, 16, 20), np.uint16), "CYX")
    with pytest.raises(UnrecordedFilesError, match="ctrl/rep1/new.ome.tif"):
        RunInputs.open(clean.output, experiment_root=moved)


def test_files_discovery_ignores_or_refuses_are_not_named(clean):
    write_file(clean.experiment / "ctrl/rep1/notes.txt")
    write_file(clean.experiment / "ctrl/rep1/a.png")
    write_file(clean.experiment / "ctrl/rep1/.DS_Store")
    write_file(clean.experiment / "ctrl/rep1/Thumbs.db")
    write_file(clean.experiment / "ctrl/rep1/._a.tif")
    # A TIFF beside the condition folders is outside every leaf folder: discovery refuses
    # it (tiff_outside_leaf), so a run would not analyse it either.
    write_data(clean.experiment / "stray.tif", np.zeros((4, 4), np.uint16), "YX")
    write_file(clean.masks / "Cells/ctrl/rep1/zz.tif")  # pairs with no image
    write_file(clean.masks / "readme.txt")
    inputs = RunInputs.open(clean.output)
    assert len(inputs.fovs()) == 4


def test_excluded_images_are_not_named(tmp_path):
    root = tmp_path / "exp"
    for name in ("a", "b"):
        write_data(root / f"ctrl/{name}.tif", np.arange(48, dtype=np.uint8).reshape(6, 8), "YX")
    result = run_experiment(root, prefill_mapping("YX"), exclude=["ctrl/b.tif"])
    assert result.written, result.issues
    assert [f.relative_path for f in RunInputs.open(result.output_folder).fovs()] == ["ctrl/a.tif"]
    write_data(root / "ctrl/c.tif", np.zeros((6, 8), np.uint8), "YX")
    with pytest.raises(UnrecordedFilesError) as raised:
        RunInputs.open(result.output_folder)
    assert raised.value.files.images == ("ctrl/c.tif",)


def test_a_mask_added_after_the_run_is_named(clean):
    write_data(clean.masks / "Nuclei/drug/rep1/b.tif", _labels((16, 20)), "YX")
    with pytest.raises(UnrecordedFilesError) as raised:
        RunInputs.open(clean.output)
    assert raised.value.files == type(raised.value.files)((), ("Nuclei/drug/rep1/b.tif",))
    assert "1 mask (Nuclei/drug/rep1/b.tif)" in str(raised.value)


def test_a_label_image_added_after_the_run_is_named(tmp_path):
    pytest.importorskip("zarr")
    image = np.arange(40, dtype=np.uint8).reshape(2, 4, 5)
    path = write_ngff(tmp_path / "exp/ctrl/a.ome.zarr", image, "zyx")
    write_labels(path, {"cells": _labels((2, 4, 5))}, "zyx")
    result = run_experiment(tmp_path / "exp", prefill_mapping("ZYX"))
    assert result.written, result.issues
    RunInputs.open(result.output_folder)
    # Written as the only listed label image: the recorded cells label image is unchanged.
    write_labels(path, {"nuclei": _labels((2, 4, 5))}, "zyx")
    with pytest.raises(UnrecordedFilesError) as raised:
        RunInputs.open(result.output_folder)
    assert raised.value.files.masks == ("ctrl/a.ome.zarr/labels/nuclei",)


# 3. Label images are never skipped (owner, 2 November 2026) ---------------------------


@pytest.fixture
def labelled(tmp_path):
    pytest.importorskip("zarr")
    image = np.arange(40, dtype=np.uint8).reshape(2, 4, 5)
    path = write_ngff(tmp_path / "exp/ctrl/a.ome.zarr", image, "zyx")
    write_labels(path, {"cells": _labels((2, 4, 5))}, "zyx")
    result = run_experiment(tmp_path / "exp", prefill_mapping("ZYX"))
    assert result.written, result.issues
    RunInputs.open(result.output_folder)
    return result.output_folder, path


def test_ome_zarr_images_without_the_zarr_extra_are_refused(labelled, monkeypatch):
    output, _ = labelled
    monkeypatch.setitem(sys.modules, "zarr", None)  # import zarr raises ImportError
    with pytest.raises(RunInputsError) as raised:
        RunInputs.open(output)
    assert not isinstance(raised.value, UnrecordedFilesError)
    assert "morphobricks-preprocessing[zarr]" in str(raised.value)
    assert "label images cannot be checked" in str(raised.value)


def test_a_labels_group_that_cannot_be_read_is_refused(labelled):
    output, path = labelled
    attrs = path / "labels" / ".zattrs"
    if not attrs.exists():  # Zarr format 3 keeps the attributes in zarr.json
        attrs = path / "labels" / "zarr.json"
        meta = json.loads(attrs.read_text())
        meta["attributes"] = {"labels": "cells"}
        attrs.write_text(json.dumps(meta))
    else:
        attrs.write_text(json.dumps({"labels": "cells"}))
    with pytest.raises(RunInputsError) as raised:
        RunInputs.open(output)
    message = str(raised.value)
    assert not isinstance(raised.value, UnrecordedFilesError)
    assert "the label images of ctrl/a.ome.zarr cannot be read" in message
    assert "run preprocessing again" in message


def test_masks_given_to_a_run_without_masks_ask_for_a_run_with_them(tmp_path):
    clean = make_clean_run(tmp_path)
    write_data(tmp_path / "masks/Cells/ctrl/rep1/a.tif", _labels((16, 20)), "YX")
    with pytest.raises(UnrecordedFilesError) as raised:
        RunInputs.open(clean.output, masks_root=tmp_path / "masks")
    assert raised.value.files.masks == ("Cells/ctrl/rep1/a.tif",)
    message = str(raised.value)
    assert "with its masks folder (--masks <masks folder>" in message
    assert "--exclude" not in message
