"""Masks: pairing every image with its masks and checking them from their headers (contract §15)."""

import json

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    prefill_mapping,
    preflight,
    read_manifest,
    run_experiment,
)
from morphobricks_preprocessing.cli import main
from tiff_tree import write_data, write_file

IMAGES = ["ctrl/rep1/a.tif", "ctrl/rep1/b.tif", "drug/rep1/a.tif"]


def label_image(seed, shape=(16, 20)):
    mask = np.zeros(shape, dtype=np.uint16)
    mask[2:8, 3:9] = 1 + seed
    mask[10:14, 10:18] = 2 + seed
    return mask


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    for seed, rel in enumerate(IMAGES):
        write_data(root / rel, np.full((2, 16, 20), seed, dtype=np.uint8), "CYX")
    return root


@pytest.fixture
def masks(tmp_path):
    folder = tmp_path / "masks"
    for kind in ("Cells", "Nuclei"):
        for seed, rel in enumerate(IMAGES):
            write_data(folder / kind / rel, label_image(seed))
    return folder


def inventory(experiment, masks, **kwargs):
    pre = preflight(experiment, masks_folder=masks, **kwargs)
    return pre.masks


def codes(issues):
    return [issue.code for issue in issues]


@pytest.fixture
def no_pixel_reads(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("pixel data was read")

    for cls in (tifffile.TiffFile, tifffile.TiffPage, tifffile.TiffFrame, tifffile.TiffPageSeries):
        monkeypatch.setattr(cls, "asarray", fail)


# Pairing ------------------------------------------------------------------------------


def test_every_image_gets_its_masks_by_type(experiment, masks, no_pixel_reads):
    found = inventory(experiment, masks)
    assert found.ok and found.issues == ()
    assert found.types == ("Cells", "Nuclei")
    assert found.images == tuple(IMAGES)
    by_type = found.for_image("ctrl/rep1/b.tif")
    assert sorted(by_type) == ["Cells", "Nuclei"]
    assert by_type["Nuclei"].path == "Nuclei/ctrl/rep1/b.tif"
    assert (by_type["Nuclei"].shape, by_type["Nuclei"].dtype) == ((16, 20), np.dtype("uint16"))
    assert found.for_image("unknown.tif") == {}


def test_any_number_of_mask_types(experiment, masks):
    for kind in ("Nucleoli", "Vesicles", "Organoid", "Background"):
        for seed, rel in enumerate(IMAGES):
            write_data(masks / kind / rel, label_image(seed))
    found = inventory(experiment, masks)
    assert found.types == ("Background", "Cells", "Nuclei", "Nucleoli", "Organoid", "Vesicles")
    assert len(found.files) == 6 * len(IMAGES)


def test_masks_match_images_by_position_whatever_their_extension(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "img_01.ome.tif", np.zeros((16, 20), dtype=np.uint8))
    write_data(tmp_path / "masks" / "Cells" / "IMG_01.TIFF", label_image(0))
    found = inventory(root, tmp_path / "masks")
    assert [(f.image, f.path) for f in found.files] == [("img_01.ome.tif", "Cells/IMG_01.TIFF")]


def test_missing_and_unpaired_masks_are_warnings(experiment, masks):
    (masks / "Nuclei" / "drug" / "rep1" / "a.tif").unlink()
    write_data(masks / "Nuclei" / "drug" / "rep2" / "c.tif", label_image(0))
    write_file(masks / "notes.txt")
    found = inventory(experiment, masks)
    assert found.ok
    assert codes(found.issues) == ["mask_missing", "mask_outside_type_folder", "mask_without_image"]
    missing, loose, unpaired = found.issues
    assert missing.paths == ("drug/rep1/a.tif",) and "no Nuclei mask" in missing.message
    assert loose.paths == ("notes.txt",)
    assert unpaired.paths == ("Nuclei/drug/rep2/c.tif",)


def test_masks_of_excluded_images_are_not_used(experiment, masks):
    found = inventory(experiment, masks, exclude=["drug/rep1/a.tif"])
    assert "drug/rep1/a.tif" not in found.images
    assert codes(found.issues) == ["mask_without_image"]


# The type after the image name -------------------------------------------------------


def test_masks_named_image_then_type_are_paired(experiment, tmp_path, no_pixel_reads):
    folder = tmp_path / "masks"
    for seed, rel in enumerate(IMAGES):
        leaf, name = rel.rsplit("/", 1)
        stem = name.rsplit(".", 1)[0]
        write_data(folder / leaf / f"{stem}_cells.tif", label_image(seed))
        write_data(folder / leaf / f"{stem}_nuclei.tif", label_image(seed))
    found = inventory(experiment, folder)
    assert found.ok and found.issues == ()
    assert found.types == ("cells", "nuclei")
    by_type = found.for_image("ctrl/rep1/b.tif")
    assert by_type["nuclei"].path == "ctrl/rep1/b_nuclei.tif"


def test_the_two_layouts_may_be_mixed(experiment, tmp_path):
    folder = tmp_path / "masks"
    for seed, rel in enumerate(IMAGES):
        write_data(folder / "Cells" / rel, label_image(seed))
        leaf, name = rel.rsplit("/", 1)
        write_data(folder / leaf / f"{name.rsplit('.', 1)[0]}_Nuclei.tif", label_image(seed))
    found = inventory(experiment, folder)
    assert found.ok and found.types == ("Cells", "Nuclei")
    assert len(found.files) == 2 * len(IMAGES)


def test_images_in_the_experiment_folder_itself(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "img_01.tif", np.zeros((16, 20), dtype=np.uint8))
    write_data(tmp_path / "masks" / "img_01_cells.tiff", label_image(0))
    write_data(tmp_path / "masks" / "img_02_cells.tif", label_image(0))
    found = inventory(root, tmp_path / "masks")
    assert [(f.image, f.mask_type) for f in found.files] == [("img_01.tif", "cells")]
    assert codes(found.issues) == ["mask_without_image"]


def test_a_mask_that_could_name_two_images_is_refused(tmp_path):
    root = tmp_path / "exp"
    for name in ("a.tif", "a_b.tif"):
        write_data(root / "ctrl" / name, np.zeros((16, 20), dtype=np.uint8))
    write_data(tmp_path / "masks" / "ctrl" / "a_b_cells.tif", label_image(0))  # a + b_cells?
    write_data(tmp_path / "masks" / "ctrl" / "a_nuclei.tif", label_image(0))  # a only
    found = inventory(root, tmp_path / "masks")
    ambiguous = next(issue for issue in found.issues if issue.code == "ambiguous_mask")
    assert ambiguous.paths == ("ctrl/a_b_cells.tif",)
    assert not found.ok
    assert [(f.image, f.mask_type) for f in found.files] == [("ctrl/a.tif", "nuclei")]


# Errors -------------------------------------------------------------------------------


def test_the_masks_folder_must_exist_and_be_outside_the_experiment(experiment, tmp_path):
    assert codes(inventory(experiment, tmp_path / "nowhere").issues) == ["masks_folder_not_found"]
    write_data(experiment / "masks" / "Cells" / "ctrl" / "rep1" / "a.tif", label_image(0))
    inside = inventory(experiment, experiment / "masks")
    assert "masks_inside_experiment" in codes(inside.issues)


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (np.zeros((16, 21), dtype=np.uint16), "mask_size_mismatch"),
        (np.zeros((2, 16, 20), dtype=np.uint16), "mask_not_2d"),
        (np.zeros((16, 20), dtype=np.float32), "mask_pixel_type"),
    ],
)
def test_masks_are_checked_from_their_headers(experiment, masks, data, code, no_pixel_reads):
    write_data(masks / "Cells" / "ctrl" / "rep1" / "a.tif", data)
    found = inventory(experiment, masks)
    assert codes(found.issues) == [code]
    assert found.issues[0].paths == ("Cells/ctrl/rep1/a.tif",)
    assert not found.ok


def test_a_length_one_axis_and_boolean_masks_are_accepted(experiment, masks):
    write_data(masks / "Cells" / "ctrl" / "rep1" / "a.tif", np.zeros((1, 16, 20), dtype=np.uint8))
    write_data(masks / "Nuclei" / "ctrl" / "rep1" / "a.tif", np.zeros((16, 20), dtype=bool))
    assert inventory(experiment, masks).ok


def test_unreadable_masks_are_errors(experiment, masks):
    write_file(masks / "Cells" / "ctrl" / "rep1" / "a.tif", b"not a tiff")
    assert codes(inventory(experiment, masks).issues) == ["unreadable_mask"]


def test_mask_names_must_differ_by_more_than_case(experiment, masks):
    try:
        (masks / "cells").mkdir()
    except FileExistsError:
        pytest.skip("the file system does not distinguish upper and lower case")
    write_data(masks / "cells" / "ctrl" / "rep1" / "a.tif", label_image(0))
    found = inventory(experiment, masks)
    assert "mask_names_differ_in_case" in codes(found.issues)


def test_one_mask_per_image_and_type(experiment, masks):
    write_data(masks / "Cells" / "ctrl" / "rep1" / "a.tiff", label_image(0))
    found = inventory(experiment, masks)
    duplicate = next(i for i in found.issues if i.code == "duplicate_mask")
    assert duplicate.paths == ("Cells/ctrl/rep1/a.tif", "Cells/ctrl/rep1/a.tiff")


# OME-Zarr label images ----------------------------------------------------------------


def test_label_images_of_ome_zarr_images_are_masks(tmp_path):
    pytest.importorskip("zarr")
    from zarr_tree import write_labels, write_ngff

    path = write_ngff(tmp_path / "exp" / "ctrl" / "a.ome.zarr", np.zeros((16, 20), np.uint8), "yx")
    write_labels(path, {"cells": label_image(0), "nuclei": label_image(1)}, "yx")
    found = preflight(tmp_path / "exp", ome_zarr_labels=True).masks
    assert found.ok
    assert found.types == ("cells", "nuclei")
    assert [f.path for f in found.files] == [
        "ctrl/a.ome.zarr/labels/cells",
        "ctrl/a.ome.zarr/labels/nuclei",
    ]
    assert preflight(tmp_path / "exp").masks is None  # only when masks are asked for


# Run, manifest and command line -------------------------------------------------------


def test_the_run_records_the_masks_in_the_manifest(experiment, masks, tmp_path):
    out = tmp_path / "out"
    result = run_experiment(
        experiment, prefill_mapping("CYX"), output_folder=out, masks_folder=masks
    )
    assert result.written
    recorded = read_manifest(out / "run_manifest.json").masks
    assert recorded["types"] == ["Cells", "Nuclei"]
    assert recorded["files"][0] == {
        "image": "ctrl/rep1/a.tif",
        "type": "Cells",
        "path": "Cells/ctrl/rep1/a.tif",
        "source": "folder",
    }
    # Without masks the manifest has no masks key, as before.
    run_experiment(experiment, prefill_mapping("CYX"), output_folder=out)
    data = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert "masks" not in data and read_manifest(out / "run_manifest.json").masks is None


def test_a_mask_error_stops_the_run(experiment, masks, tmp_path):
    write_data(masks / "Cells" / "ctrl" / "rep1" / "a.tif", np.zeros((3, 3), dtype=np.uint8))
    result = run_experiment(
        experiment, prefill_mapping("CYX"), output_folder=tmp_path / "out", masks_folder=masks
    )
    assert not result.written
    assert "mask_size_mismatch" in codes(result.issues)


def test_check_lists_the_masks(experiment, masks, capsys):
    assert main(["check", str(experiment), "--masks", str(masks)]) == 0
    out = capsys.readouterr().out
    assert "Masks: 2 mask types for 3 images:" in out
    assert "  Nuclei: 3 images (folder)" in out


def test_masks_command_writes_the_report(experiment, masks, tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["masks", str(experiment), "--masks", str(masks), "--output", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "[3/3] drug/rep1/a.tif" in printed
    assert "Hierarchy (direct parents only):" in printed
    report = json.loads((out / "mask_relationships.json").read_text(encoding="utf-8"))
    assert report["report_version"] == "1"
    assert sorted(report["masks"]) == ["Cells", "Nuclei"]
    assert report["same_objects"] == [["Cells", "Nuclei"]]


def test_masks_command_stops_on_mask_errors_or_without_masks(experiment, masks, capsys):
    assert main(["masks", str(experiment)]) == 1
    assert "Masks: none found." in capsys.readouterr().out
    write_data(masks / "Cells" / "ctrl" / "rep1" / "a.tif", np.zeros((3, 3), dtype=np.uint8))
    assert main(["masks", str(experiment), "--masks", str(masks)]) == 1
    assert "do not have the size" in capsys.readouterr().out


def test_masks_command_takes_thresholds(experiment, masks, tmp_path, capsys):
    out = tmp_path / "out"
    args = ["masks", str(experiment), "--masks", str(masks), "--output", str(out)]
    assert main([*args, "--same-objects-iou", "0.8", "--inside-object-share", "0.7"]) == 0
    printed = capsys.readouterr().out
    assert "Thresholds: inside_object_share 0.7, same_objects_iou 0.8; the others" in printed
    report = json.loads((out / "mask_relationships.json").read_text(encoding="utf-8"))
    assert report["thresholds"]["same_objects_iou"] == 0.8
    assert main([*args, "--grouped-label-share", "1.5"]) == 1
    assert "must be above 0 and at most 1" in capsys.readouterr().out


def test_masks_command_refuses_negative_labels(experiment, masks, tmp_path, capsys):
    signed = label_image(0).astype(np.int32)
    signed[0, 0] = -5
    write_data(masks / "Cells" / "ctrl" / "rep1" / "a.tif", signed)
    out = tmp_path / "out"
    assert main(["masks", str(experiment), "--masks", str(masks), "--output", str(out)]) == 1
    assert "Cells/ctrl/rep1/a.tif holds negative labels (smallest -5)" in capsys.readouterr().out
    assert not (out / "mask_relationships.json").exists()
