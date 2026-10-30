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
        "axes": "YX",
        "shape": [16, 20],
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


# Planes: depth slices and time points (issue #36) -------------------------------------


def time_lapse(tmp_path, n_t=3, image_axes="TYX"):
    """An experiment of one time-lapse image (T, Y, X) and a masks folder."""
    root = tmp_path / "exp"
    write_data(root / "ctrl" / "a.tif", np.zeros((n_t, 16, 20), dtype=np.uint8), image_axes)
    return root, tmp_path / "masks"


def run_with_masks(root, masks, roles, tmp_path):
    return run_experiment(
        root, prefill_mapping(roles), output_folder=tmp_path / "out", masks_folder=masks
    )


def test_a_2d_mask_of_a_time_lapse_or_z_stack_is_refused(tmp_path):
    for roles in ("TYX", "ZYX"):
        root, masks = time_lapse(tmp_path / roles, image_axes=roles)
        write_data(masks / "Cells" / "ctrl" / "a.tif", label_image(0))
        assert preflight(root, masks_folder=masks).masks.ok  # the roles are not confirmed yet
        result = run_with_masks(root, masks, roles, tmp_path / roles)
        assert not result.written
        mismatch = next(i for i in result.issues if i.code == "mask_planes_mismatch")
        assert mismatch.paths == ("Cells/ctrl/a.tif",)
        assert "a 2D mask fits only an image with one plane" in mismatch.message


def test_a_mask_with_the_images_time_points_is_accepted_and_recorded(tmp_path, no_pixel_reads):
    root, masks = time_lapse(tmp_path)
    data = np.stack([label_image(t) for t in range(3)])
    write_data(masks / "Cells" / "ctrl" / "a.tif", data, "TYX")
    found = preflight(root, masks_folder=masks).masks
    assert found.ok
    cells = found.for_image("ctrl/a.tif")["Cells"]
    assert (cells.axes, cells.shape, cells.planes) == ("TYX", (3, 16, 20), {"Z": 1, "T": 3})
    assert found.to_manifest()["files"][0]["axes"] == "TYX"
    assert found.to_manifest()["files"][0]["shape"] == [3, 16, 20]


def test_the_run_records_each_masks_planes(tmp_path):
    root, masks = time_lapse(tmp_path)
    write_data(masks / "Cells" / "ctrl" / "a.tif", np.stack([label_image(0)] * 3), "TYX")
    result = run_with_masks(root, masks, "TYX", tmp_path)
    assert result.written, result.issues
    entry = read_manifest(tmp_path / "out" / "run_manifest.json").masks["files"][0]
    assert (entry["axes"], entry["shape"]) == ("TYX", [3, 16, 20])


def test_a_mask_with_other_planes_than_its_image_is_refused(tmp_path):
    root, masks = time_lapse(tmp_path)
    write_data(masks / "Cells" / "ctrl" / "a.tif", np.stack([label_image(0)] * 2), "TYX")
    write_data(masks / "Nuclei" / "ctrl" / "a.tif", np.stack([label_image(0)] * 3), "ZYX")
    result = run_with_masks(root, masks, "TYX", tmp_path)
    assert not result.written
    found = {i.code: i.paths for i in result.issues}
    assert found["mask_planes_mismatch"] == ("Cells/ctrl/a.tif", "Nuclei/ctrl/a.tif")
    # The two masks of one image do not even agree with each other: found from the headers.
    assert found["mask_planes_differ"] == ("Cells/ctrl/a.tif", "Nuclei/ctrl/a.tif")


def test_a_mask_whose_extra_axis_is_not_named_is_refused(tmp_path):
    root, masks = time_lapse(tmp_path)
    # No axes in the metadata: tifffile calls the stack Q, which could be depth or time.
    write_data(masks / "Cells" / "ctrl" / "a.tif", np.stack([label_image(0)] * 3))
    found = preflight(root, masks_folder=masks).masks
    assert codes(found.issues) == ["mask_not_2d"]
    assert "does not name as depth (Z) or time (T)" in found.issues[0].message


def test_read_mask_reads_one_plane_and_never_guesses_which(tmp_path):
    from morphobricks_preprocessing import read_mask

    root, masks = time_lapse(tmp_path)
    data = np.stack([label_image(t) for t in range(3)])
    write_data(masks / "Cells" / "ctrl" / "a.tif", data, "TYX")
    found = preflight(root, masks_folder=masks).masks
    cells = found.for_image("ctrl/a.tif")["Cells"]
    for t in range(3):
        np.testing.assert_array_equal(read_mask(cells, found.folder, root, t=t), data[t])
    with pytest.raises(ValueError, match="3 planes along T"):
        read_mask(cells, found.folder, root)
    with pytest.raises(ValueError, match="no plane T=3"):
        read_mask(cells, found.folder, root, t=3)
    with pytest.raises(ValueError, match="no plane Z=1"):
        read_mask(cells, found.folder, root, z=1, t=0)


def test_read_mask_of_a_volume_stored_in_one_page(tmp_path):
    from morphobricks_preprocessing import read_mask

    root = tmp_path / "exp"
    write_data(root / "ctrl" / "a.tif", np.zeros((3, 16, 20), dtype=np.uint8), "ZYX")
    data = np.stack([label_image(z) for z in range(3)])
    write_data(
        tmp_path / "masks" / "Cells" / "ctrl" / "a.tif",
        data,
        "ZYX",
        volumetric=True,
        tile=(3, 16, 16),
    )
    found = preflight(root, masks_folder=tmp_path / "masks").masks
    cells = found.for_image("ctrl/a.tif")["Cells"]
    assert cells.planes == {"Z": 3, "T": 1}
    np.testing.assert_array_equal(read_mask(cells, found.folder, root, z=2), data[2])


def test_label_images_with_time_points(tmp_path):
    pytest.importorskip("zarr")
    from morphobricks_preprocessing import read_mask
    from zarr_tree import write_labels, write_ngff

    path = write_ngff(tmp_path / "exp" / "a.ome.zarr", np.zeros((3, 16, 20), np.uint8), "tyx")
    data = np.stack([label_image(t) for t in range(3)])
    write_labels(path, {"cells": data}, "tyx")
    found = preflight(tmp_path / "exp", ome_zarr_labels=True).masks
    assert found.ok
    cells = found.for_image("a.ome.zarr")["cells"]
    assert (cells.axes, cells.planes) == ("TYX", {"Z": 1, "T": 3})
    np.testing.assert_array_equal(read_mask(cells, None, tmp_path / "exp", t=1), data[1])
    result = run_experiment(
        tmp_path / "exp",
        prefill_mapping("TYX"),
        output_folder=tmp_path / "out",
        ome_zarr_labels=True,
    )
    assert result.written, result.issues
