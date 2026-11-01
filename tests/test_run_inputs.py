"""The contract of RunInputs: what a clean run gives the bricks (design v0.4 §6, PP-6)."""

import json
import multiprocessing
import os
import pickle
import shutil

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import DESIGN_FILENAME, AxisMapping, prefill_mapping, run_experiment
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError
from morphobricks_preprocessing.testing import make_clean_run
from tiff_tree import write_data, write_plain
from zarr_tree import write_labels, write_ngff


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


# Review additions: worker processes, design, canonical order, stored mask axes -----------------


def test_run_inputs_work_in_spawned_worker_processes(clean):
    inputs = RunInputs.open(clean.output)
    paths = [f.relative_path for f in inputs.fovs()]
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        planes = pool.starmap(RunInputs.read_plane, [(inputs, p) for p in paths])
        masks = pool.starmap(RunInputs.read_mask, [(inputs, p, "Cells") for p in paths])
    for path, plane, mask in zip(paths, planes, masks, strict=True):
        np.testing.assert_array_equal(plane, tifffile.imread(clean.experiment / path))
        np.testing.assert_array_equal(mask, tifffile.imread(clean.masks / "Cells" / mask_of(path)))


def mask_of(image):
    return image.replace(".ome.tif", ".tif")


def test_the_design_of_a_run_with_one(tmp_path):
    clean = make_clean_run(tmp_path, design=True)
    design = RunInputs.open(clean.output).design()
    assert design.repeat_groups() == {("ctrl", "rep1"): "rep1", ("drug", "rep1"): "rep1"}
    assert {row.condition: row.control for row in design.rows} == {"ctrl": True, "drug": False}
    assert all(row.confirmed for row in design.rows)


def test_a_changed_or_missing_design_is_refused(tmp_path):
    clean = make_clean_run(tmp_path, design=True)
    path = clean.output / DESIGN_FILENAME
    inputs = RunInputs.open(clean.output)
    path.write_text(path.read_text(encoding="utf-8").replace("true", "false"), encoding="utf-8")
    with pytest.raises(RunInputsError, match="design"):
        inputs.design()
    with pytest.raises(RunInputsError, match="design"):
        RunInputs.open(clean.output)
    path.unlink()
    with pytest.raises(RunInputsError, match="design"):
        RunInputs.open(clean.output)


def test_a_header_naming_x_before_y_is_read_y_before_x(tmp_path):
    # Owner rule (design v0.4 change log): the header names the axes, so reordering is exact.
    stored = np.arange(2 * 2 * 5 * 7, dtype=np.uint16).reshape(2, 2, 5, 7)  # Z, C, X, Y
    write_data(tmp_path / "exp/ctrl/a.tif", stored, "ZCXY")
    result = run_experiment(tmp_path / "exp", prefill_mapping("ZCXY"))
    assert result.written, result.issues
    inputs = RunInputs.open(result.output_folder)
    (fov,) = inputs.fovs()
    canonical = stored.transpose(0, 1, 3, 2)  # Z, C, Y, X
    np.testing.assert_array_equal(inputs.read_plane(fov, z=1), canonical[1])
    image = inputs.read_image(fov)
    assert image.shape == (1, 2, 2, 7, 5)
    np.testing.assert_array_equal(np.asarray(image)[0], canonical)


def test_read_image_is_t_z_c_y_x_whatever_the_stored_order(tmp_path):
    stored = np.random.default_rng(1).integers(0, 999, (3, 2, 2, 6, 8), dtype=np.uint16)
    path = tmp_path / "exp/ctrl/a.ome.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(path, stored, ome=True, photometric="minisblack", metadata={"axes": "ZTCYX"})
    with tifffile.TiffFile(path) as tif:
        assert tif.series[0].axes == "ZTCYX"
    result = run_experiment(tmp_path / "exp", prefill_mapping("ZTCYX"))
    inputs = RunInputs.open(result.output_folder)
    (fov,) = inputs.fovs()
    assert (fov.z_count, fov.t_count, fov.c_count) == (3, 2, 2)
    np.testing.assert_array_equal(
        np.asarray(inputs.read_image(fov)), stored.transpose(1, 0, 2, 3, 4)
    )
    np.testing.assert_array_equal(inputs.read_plane(fov, z=2, t=1), stored[2, 1])


@pytest.fixture
def stated(tmp_path):
    """A TYX image with a plain-stack mask whose axes are stated TYX (stored IYX)."""
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_data(root / "ctrl/a.tif", np.zeros((3, 6, 8), np.uint8), "TYX")
    labels = np.zeros((3, 6, 8), np.uint16)
    labels[1, 1:3, 1:3], labels[2, 2:4, 2:4] = 4, 9
    write_plain(masks / "Cells/ctrl/a.tif", labels)
    result = run_experiment(
        root, prefill_mapping("TYX"), masks_folder=masks, mask_axes={"Cells": "TYX"}
    )
    assert result.written, result.issues
    return result.output_folder, masks, labels


def test_a_stated_mask_is_read_through_its_stored_axes(stated):
    output, _, labels = stated
    inputs = RunInputs.open(output)
    (recorded,) = inputs.manifest.masks["files"]
    assert (recorded["axes"], recorded["roles"], recorded["axes_from"]) == ("IYX", "TYX", "stated")
    for t in range(3):
        np.testing.assert_array_equal(inputs.read_mask("ctrl/a.tif", "Cells", t=t), labels[t])
    with pytest.raises(ValueError, match="say which one"):
        inputs.read_mask("ctrl/a.tif", "Cells")


def test_a_mask_stored_otherwise_than_recorded_is_not_read(stated):
    output, _, _ = stated
    path = output / MANIFEST_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["masks"]["files"][0]["axes"] = "TYX"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RunInputsError, match="mask"):
        RunInputs.open(output).read_mask("ctrl/a.tif", "Cells", t=0)


def test_a_mask_changed_or_moved_before_opening_is_refused(stated, tmp_path):
    output, masks, labels = stated
    moved = tmp_path / "moved_masks"
    shutil.copytree(masks, moved, copy_function=shutil.copy2)
    shutil.rmtree(masks)
    with pytest.raises(RunInputsError, match="1 mask changed or missing.*masks_root="):
        RunInputs.open(output)
    inputs = RunInputs.open(output, masks_root=moved)
    np.testing.assert_array_equal(inputs.read_mask("ctrl/a.tif", "Cells", t=2), labels[2])
    write_plain(moved / "Cells/ctrl/a.tif", labels + 1)
    with pytest.raises(RunInputsError, match="Cells/ctrl/a.tif"):
        RunInputs.open(output, masks_root=moved)


def test_ome_zarr_label_images_are_read_by_their_pairing(tmp_path):
    pytest.importorskip("zarr")
    image = np.random.default_rng(2).integers(0, 99, (2, 4, 5)).astype(np.uint8)
    path = write_ngff(tmp_path / "exp/ctrl/a.ome.zarr", image, "zyx")
    labels = np.zeros((2, 4, 5), np.uint16)
    labels[1, 1:3, 1:3] = 3
    write_labels(path, {"cells": labels}, "zyx")
    result = run_experiment(tmp_path / "exp", prefill_mapping("ZYX"))
    assert result.written, result.issues
    inputs = RunInputs.open(result.output_folder)
    assert inputs.mask_types() == ("cells",)
    np.testing.assert_array_equal(inputs.read_mask("ctrl/a.ome.zarr", "cells", z=1), labels[1])
    np.testing.assert_array_equal(np.asarray(inputs.read_image("ctrl/a.ome.zarr"))[0, :, 0], image)
