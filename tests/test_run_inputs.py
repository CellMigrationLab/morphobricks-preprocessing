"""The contract of RunInputs: what a clean run gives the bricks (design v0.4 §6, PP-6)."""

import json
import os
import pickle
import shutil

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import AxisMapping, prefill_mapping, run_experiment
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError
from morphobricks_preprocessing.testing import make_clean_run
from tiff_tree import write_data, write_plain


@pytest.fixture
def clean(tmp_path):
    return make_clean_run(tmp_path, mask_types=("Cells",))


def test_fovs_come_from_the_manifest_and_the_table(clean):
    inputs = RunInputs.open(clean.output)
    fovs = inputs.fovs()
    assert [f.key for f in fovs] == [
        ("ctrl", "rep1", "single", "a"),
        ("ctrl", "rep1", "single", "b"),
        ("drug", "rep1", "single", "a"),
        ("drug", "rep1", "single", "b"),
    ]
    first = fovs[0]
    assert (first.axes, first.roles, first.axes_from) == ("CYX", ("C", "Y", "X"), "header")
    assert (first.c_count, first.z_count, first.t_count, first.dtype) == (2, 1, 1, "uint16")
    assert first.plane_ids == ("ctrl/rep1/a.ome.tif::z=0,t=0",)


def test_planes_and_images_are_the_stored_values(clean):
    inputs = RunInputs.open(clean.output)
    for fov in inputs.fovs():
        stored = tifffile.imread(clean.experiment / fov.relative_path)
        np.testing.assert_array_equal(inputs.read_plane(fov), stored)
        image = inputs.read_image(fov)
        assert image.shape == (1, 1, 2, 16, 20)
        np.testing.assert_array_equal(np.asarray(image)[0, 0], stored)
    with pytest.raises(IndexError):
        inputs.read_plane(fov, z=1)


def test_pixel_sizes_carry_their_unit_and_source(clean):
    sizes = RunInputs.open(clean.output).pixel_sizes("ctrl/rep1/a.ome.tif")
    assert (sizes["X"].value, sizes["X"].unit, sizes["X"].source) == (0.5, "micrometer", "header")
    assert sizes["Z"].value is None and sizes["Z"].source is None


def test_masks_by_the_recorded_pairing(clean):
    inputs = RunInputs.open(clean.output)
    assert inputs.mask_types() == ("Cells",)
    mask = inputs.read_mask("drug/rep1/b.ome.tif", "Cells")
    assert mask.shape == (16, 20) and set(np.unique(mask)) == {0, 1, 2}
    with pytest.raises(RunInputsError, match="no Nuclei mask"):
        inputs.read_mask("drug/rep1/b.ome.tif", "Nuclei")


def test_histograms_design_and_image_type(clean):
    inputs = RunInputs.open(clean.output)
    assert len(inputs.histograms()) == 4
    assert {r.source_relative_path for r in inputs.histograms()} == {
        f.relative_path for f in inputs.fovs()
    }
    assert inputs.design() is None and inputs.image_type() is None


def test_run_inputs_pickle(clean):
    inputs = pickle.loads(pickle.dumps(RunInputs.open(clean.output)))
    stored = tifffile.imread(clean.experiment / "ctrl/rep1/a.ome.tif")
    np.testing.assert_array_equal(inputs.read_plane("ctrl/rep1/a.ome.tif"), stored)
    assert inputs.read_mask("ctrl/rep1/a.ome.tif", "Cells").max() == 2


# Refusals --------------------------------------------------------------------------------


def test_a_version_1_run_is_refused(clean):
    path = clean.output / MANIFEST_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["manifest_version"] = "1"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RunInputsError, match="before v0.4.0"):
        RunInputs.open(clean.output)


def test_a_changed_table_is_refused(clean):
    table = clean.output / "plane_histograms.csv"
    table.write_text(table.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(RunInputsError, match="histogram table"):
        RunInputs.open(clean.output)


def test_an_image_changed_before_opening_is_refused(clean):
    image = clean.experiment / "ctrl/rep1/a.ome.tif"
    image.write_bytes(image.read_bytes() + b"\0")
    with pytest.raises(RunInputsError, match="ctrl/rep1/a.ome.tif"):
        RunInputs.open(clean.output)


def test_an_image_changed_after_opening_is_not_read(clean):
    inputs = RunInputs.open(clean.output)
    image = clean.experiment / "ctrl/rep1/b.ome.tif"
    stat = image.stat()
    os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    with pytest.raises(RunInputsError, match="changed or missing"):
        inputs.read_plane("ctrl/rep1/b.ome.tif")
    inputs.read_plane("ctrl/rep1/a.ome.tif")  # the others are still read


def test_a_changed_mask_is_not_read(clean):
    inputs = RunInputs.open(clean.output)
    mask = clean.masks / "Cells/ctrl/rep1/a.tif"
    tifffile.imwrite(mask, np.ones((16, 20), np.uint16), metadata={"axes": "YX"})
    with pytest.raises(RunInputsError, match="mask"):
        inputs.read_mask("ctrl/rep1/a.ome.tif", "Cells")


def test_a_moved_experiment_is_opened_where_it_is(clean, tmp_path):
    moved = tmp_path / "moved"
    shutil.copytree(clean.experiment, moved, copy_function=shutil.copy2)
    shutil.rmtree(clean.experiment)
    with pytest.raises(RunInputsError):
        RunInputs.open(clean.output)
    inputs = RunInputs.open(clean.output, experiment_root=moved)
    assert inputs.read_plane("ctrl/rep1/a.ome.tif").shape == (2, 16, 20)


# Plain stacks, stated masks and labels -------------------------------------------------------


def test_a_confirmed_plain_stack_and_a_shared_mask(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    stack = np.arange(3 * 6 * 8, dtype=np.uint8).reshape(3, 6, 8)
    write_plain(root / "ctrl/a.tif", stack)
    labels = np.zeros((6, 8), np.uint16)
    labels[2:4, 2:5] = 7
    write_data(masks / "Cells/ctrl/a.tif", labels, "YX")
    result = run_experiment(
        root,
        AxisMapping("IYX", "TYX"),
        masks_folder=masks,
        mask_axes={"Cells": "YX"},
    )
    inputs = RunInputs.open(result.output_folder)
    (fov,) = inputs.fovs()
    assert (fov.axes_from, fov.t_count, fov.c_count) == ("confirmed", 3, 1)
    np.testing.assert_array_equal(inputs.read_plane(fov, t=2), stack[2:3])
    for t in range(3):
        np.testing.assert_array_equal(inputs.read_mask(fov, "Cells", t=t), labels)


def test_labels_are_checked_on_every_read(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    signed = np.zeros((6, 8), np.int32)
    signed[0, 0] = -5
    write_data(masks / "Cells/ctrl/a.tif", signed, "YX")
    result = run_experiment(root, prefill_mapping("YX"), masks_folder=masks)
    inputs = RunInputs.open(result.output_folder)
    with pytest.raises(ValueError, match="-5|negative"):
        inputs.read_mask("ctrl/a.tif", "Cells")
