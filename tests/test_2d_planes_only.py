"""MorphoBricks v1 is 2-D per plane (owner decision of 5 November 2026, tracker row 36).

1. An image with more than one depth slice is refused (``z_stack_not_supported``) before
   anything but the report is written, whatever route names its Z axis: an ImageJ
   hyperstack, an OME-TIFF, tifffile's shaped metadata, an OME-Zarr image, or a plain
   stack whose roles the user confirms. Every such image is named. Z = 1 is accepted.
2. A time-lapse is accepted when its masks have its time points; a 2-D mask of a
   time-lapse is refused, saying the masks need the same T. The stated shared 2-D mask
   (``--mask-axes TYPE=YX``) is removed: refused with ``shared_mask_not_supported``.
"""

import json

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    AxisMapping,
    default_design,
    preflight,
    read_manifest,
    review_axes,
    run_experiment,
    write_experiment_design,
)
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.mask_review import _statements, mask_axes_questions
from morphobricks_preprocessing.masks.stated import mask_roles
from morphobricks_preprocessing.run.recorded import recorded_mask_axes
from morphobricks_preprocessing.run_inputs import RunInputs
from tiff_tree import write_data, write_plain

Z_STACK = "z_stack_not_supported"
SHARED = "shared_mask_not_supported"
TCYX = AxisMapping("TCYX", "TCYX")


def labels(shape, start=1):
    return (np.arange(np.prod(shape)).reshape(shape) % 5 + start).astype(np.uint16)


def errors(issues):
    return {i.code for i in issues if i.is_error}


def one(issues, code):
    (found,) = [i for i in issues if i.code == code]
    return found


def imagej(path, data, axes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, imagej=True, metadata={"axes": axes})


def ome(path, data, axes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata={"axes": axes})


def written_outputs(out):
    return sorted(p.name for p in out.iterdir()) if out.exists() else []


def confirm_design(root, output):
    design = default_design(preflight(root).dataset.repeats)
    for group in design.groups:
        design = design.with_confirmed(group, True, "2026-11-05")
    output.mkdir(parents=True, exist_ok=True)
    write_experiment_design(design, output / "experiment_design.csv")


# 1. Z > 1 is refused, by every route that names a Z axis -------------------------------


def write_named(route, path, slices):
    data = np.zeros((slices, 2, 6, 8), np.uint8)
    if route == "imagej":
        imagej(path, data, "ZCYX")
    elif route == "ome":
        ome(path, data, "ZCYX")
    else:
        write_data(path, data, "ZCYX")


@pytest.mark.parametrize("route", ["imagej", "ome", "shaped"])
def test_a_named_z_stack_is_refused_from_its_header(tmp_path, route):
    root, out = tmp_path / "exp", tmp_path / "out"
    write_named(route, root / "ctrl/r1/a.tif", 3)
    write_named(route, root / "ctrl/r1/b.tif", 4)
    pre = preflight(root)
    issue = one(pre.metadata.issues, Z_STACK)  # before any role is confirmed
    assert issue.is_error and issue.paths == ("ctrl/r1/a.tif", "ctrl/r1/b.tif")
    assert "ctrl/r1/a.tif: 3 slices" in issue.message and "b.tif: 4 slices" in issue.message
    assert "2-D planes" in issue.message and "project" in issue.action.lower()
    result = run_experiment(root, AxisMapping("ZCYX", "ZCYX"), output_folder=out)
    assert not result.written and Z_STACK in errors(result.issues)
    # Nothing but the report: no table, no manifest.
    assert "plane_histograms.csv" not in written_outputs(out)
    assert "run_manifest.json" not in written_outputs(out)


def test_every_z_stack_is_named_not_only_the_first(tmp_path):
    root = tmp_path / "exp"
    paths = [f"ctrl/r{i}/a.tif" for i in range(7)]
    for path in paths:
        write_named("shaped", root / path, 2)
    issue = one(preflight(root).metadata.issues, Z_STACK)
    assert issue.paths == tuple(paths)
    assert "7 images are Z-stacks" in issue.message and "and 2 more" in issue.message


def test_check_refuses_a_z_stack_and_writes_only_the_report(tmp_path, capsys):
    root, out = tmp_path / "exp", tmp_path / "out"
    write_named("ome", root / "ctrl/r1/a.ome.tif", 3)
    assert main(["check", str(root), "--output", str(out)]) == 1
    printed = capsys.readouterr().out
    assert "1 image is a Z-stack" in printed and "Z Project" in printed
    report = json.loads((out / "discovery_report.json").read_text(encoding="utf-8"))
    assert Z_STACK in {i["code"] for i in report["issues"] if i["severity"] == "error"}
    assert "plane_histograms.csv" not in written_outputs(out)


def test_a_plain_stack_confirmed_with_a_z_role_is_refused(tmp_path):
    root = tmp_path / "exp"
    write_plain(root / "ctrl/a.tif", np.zeros((3, 6, 8), np.uint8))
    assert Z_STACK not in {i.code for i in preflight(root).metadata.issues}  # unnamed yet
    review = review_axes(preflight(root).metadata, AxisMapping("IYX", "ZYX"))
    assert one(review.issues, Z_STACK).paths == ("ctrl/a.tif",)
    result = run_experiment(root, AxisMapping("IYX", "ZYX"))
    assert not result.written and Z_STACK in errors(result.issues)
    # The same stack confirmed as time points is a time-lapse, and is analysed.
    assert run_experiment(root, AxisMapping("IYX", "TYX")).written


def test_a_header_z_confirmed_as_time_is_the_axis_error_that_convert_fixes(tmp_path):
    # An ImageJ stack of time points that tifffile reads as Z slices (likely_axis_cause):
    # confirmed as T it is never read under the contradicted header, and its named-axis
    # copy (TYX) is a time-lapse the run accepts. A Z-stack error here would block the fix.
    root, out = tmp_path / "exp", tmp_path / "out"
    imagej(root / "ctrl/a.tif", np.zeros((3, 6, 8), np.uint8), "ZYX")
    pre = preflight(root)
    assert Z_STACK in {i.code for i in pre.metadata.issues}  # the header alone: a Z-stack
    review = review_axes(pre.metadata, AxisMapping("ZYX", "TYX"))
    combined = {i.code for i in review.combined_issues(pre.metadata) if i.is_error}
    assert combined == {"axes_contradict_header"}
    copies = tmp_path / "copies"
    assert main(["convert", str(root), "--to", str(copies), "--axes", "TYX"]) == 0
    assert run_experiment(copies, AxisMapping("TYX", "TYX"), output_folder=out).written
    # Confirmed as the depth its header names, it is a Z-stack.
    refused = review_axes(pre.metadata, AxisMapping("ZYX", "ZYX"))
    assert one(refused.issues, Z_STACK).paths == ("ctrl/a.tif",)


def test_the_review_supersedes_the_header_error(tmp_path):
    root = tmp_path / "exp"
    write_named("shaped", root / "ctrl/a.tif", 3)
    pre = preflight(root)
    review = review_axes(pre.metadata, AxisMapping("ZCYX", "ZCYX"))
    assert [i.code for i in review.combined_issues(pre.metadata)].count(Z_STACK) == 1


def test_an_ome_zarr_z_stack_is_refused(tmp_path):
    from zarr_tree import write_ngff

    root = tmp_path / "exp"
    write_ngff(root / "ctrl/a.ome.zarr", np.zeros((2, 3, 6, 8), np.uint16), "czyx")
    assert one(preflight(root).metadata.issues, Z_STACK).paths == ("ctrl/a.ome.zarr",)
    result = run_experiment(root, AxisMapping("CZYX", "CZYX"))
    assert not result.written and Z_STACK in errors(result.issues)


def test_convert_refuses_a_z_stack_too(tmp_path, capsys):
    root = tmp_path / "exp"
    write_plain(root / "ctrl/a.tif", np.zeros((3, 6, 8), np.uint8))
    args = ["convert", str(root), "--to", str(tmp_path / "copies"), "--axes", "ZYX"]
    assert main(args) == 1
    assert "1 image is a Z-stack" in capsys.readouterr().out
    assert not (tmp_path / "copies/ctrl/a.tif").exists()


# Z = 1 stays allowed ---------------------------------------------------------------------


@pytest.mark.parametrize("route", ["ome", "shaped"])
def test_a_z_axis_of_one_slice_is_accepted_as_it_is(tmp_path, route):
    root = tmp_path / "exp"
    write_named(route, root / "ctrl/a.tif", 1)
    pre = preflight(root)
    assert Z_STACK not in {i.code for i in pre.metadata.issues}
    axes = pre.metadata.files[0].axes
    result = run_experiment(root, AxisMapping(axes, axes))
    assert result.written, result.issues
    (fov,) = RunInputs.open(result.output_folder).fovs()
    assert fov.z_count == 1


def test_a_2d_image_is_accepted(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "ctrl/a.tif", np.zeros((2, 6, 8), np.uint8), "CYX")
    assert run_experiment(root, AxisMapping("CYX", "CYX")).written


# 2. T > 1 only with masks of the same T --------------------------------------------------


@pytest.fixture
def timelapse(tmp_path):
    """One 4-frame time-lapse (TCYX), with an empty masks folder next to it."""
    root, masks = tmp_path / "exp", tmp_path / "masks"
    ome(root / "ctrl/r1/a.tif", np.zeros((4, 2, 6, 8), np.uint16), "TCYX")
    masks.mkdir()
    return root, masks


def test_a_time_lapse_with_masks_of_its_t_is_accepted(timelapse):
    root, masks = timelapse
    ome(masks / "Cells/ctrl/r1/a.tif", labels((4, 6, 8)), "TYX")
    result = run_experiment(root, TCYX, masks_folder=masks)
    assert result.written, result.issues
    inputs = RunInputs.open(result.output_folder)
    (fov,) = inputs.fovs()
    for t in range(4):
        np.testing.assert_array_equal(inputs.read_mask(fov, "Cells", t=t), labels((4, 6, 8))[t])


def test_a_stated_plain_stack_mask_of_its_t_is_accepted(timelapse):
    root, masks = timelapse
    write_plain(masks / "Cells/ctrl/r1/a.tif", labels((4, 6, 8)))
    result = run_experiment(root, TCYX, masks_folder=masks, mask_axes={"Cells": "TYX"})
    assert result.written, result.issues
    (entry,) = read_manifest(result.manifest_path).masks["files"]
    assert (entry["roles"], entry["axes_from"]) == ("TYX", "stated")
    assert "shared" not in entry


def test_a_stated_plain_stack_mask_of_another_t_is_refused(timelapse):
    root, masks = timelapse
    write_plain(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)))
    result = run_experiment(root, TCYX, masks_folder=masks, mask_axes={"Cells": "TYX"})
    assert not result.written and "mask_planes_mismatch" in errors(result.issues)


@pytest.mark.parametrize("named", [False, True])
def test_a_2d_mask_of_a_time_lapse_is_refused(timelapse, named):
    root, masks = timelapse
    path = masks / "Cells/ctrl/r1/a.tif"
    if named:
        ome(path, labels((6, 8)), "YX")
    else:
        write_plain(path, labels((6, 8)))
    result = run_experiment(root, TCYX, masks_folder=masks)
    assert not result.written
    issue = one(result.issues, "mask_planes_mismatch")
    assert issue.paths == ("Cells/ctrl/r1/a.tif",)
    assert "same T as their image" in issue.message
    assert "--mask-axes" not in issue.action or "Cells=YX" not in issue.action
    assert "plane_histograms.csv" not in written_outputs(result.output_folder)


def test_masks_of_one_image_with_other_planes_name_no_statement(timelapse):
    root, masks = timelapse
    ome(masks / "Cells/ctrl/r1/a.tif", labels((4, 6, 8)), "TYX")
    ome(masks / "Nuclei/ctrl/r1/a.tif", labels((6, 8)), "YX")
    issue = one(preflight(root, masks_folder=masks).masks.issues, "mask_planes_differ")
    assert "=YX" not in issue.action and "time point" in issue.action


# The stated shared 2-D mask is removed ---------------------------------------------------


def test_the_shared_statement_is_refused_by_the_api(timelapse):
    root, masks = timelapse
    ome(masks / "Cells/ctrl/r1/a.tif", labels((6, 8)), "YX")
    result = run_experiment(root, TCYX, masks_folder=masks, mask_axes={"Cells": "YX"})
    assert not result.written
    issue = one(result.issues, SHARED)
    assert issue.paths == ("Cells/ctrl/r1/a.tif",)
    assert "removed" in issue.message and "same time points (T)" in issue.message
    assert "Remove the statement" in issue.action


def test_the_shared_statement_is_refused_on_a_one_plane_image_too(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_data(root / "ctrl/a.tif", np.zeros((6, 8), np.uint8), "YX")
    write_plain(masks / "Cells/ctrl/a.tif", labels((6, 8)))
    assert SHARED in errors(preflight(root, masks_folder=masks, mask_axes={"Cells": "yx"}).issues)
    assert run_experiment(root, AxisMapping("YX", "YX"), masks_folder=masks).written


def test_the_shared_statement_is_refused_by_the_command_line(timelapse, tmp_path, capsys):
    root, masks = timelapse
    ome(masks / "Cells/ctrl/r1/a.tif", labels((6, 8)), "YX")
    out = tmp_path / "out"
    common = [str(root), "--masks", str(masks), "--output", str(out), "--mask-axes", "Cells=YX"]
    assert main(["check", *common]) == 1
    assert "That option was removed" in capsys.readouterr().out
    confirm_design(root, out)
    assert main(["run", *common, "--axes", "TCYX"]) == 1
    assert "That option was removed" in capsys.readouterr().out
    assert not (out / "plane_histograms.csv").exists()


def test_mask_roles_refuse_a_yx_statement():
    assert mask_roles("YX", "YX", "YX")[1] == SHARED
    assert mask_roles("QYX", "???", "TYX") == ("TYX", None, "")


def test_the_notebook_never_offers_yx(timelapse):
    root, masks = timelapse
    ome(masks / "Cells/ctrl/r1/a.tif", labels((6, 8)), "YX")
    assert "YX" not in _statements(2) and "YX" not in _statements(3)
    pre = preflight(root, masks_folder=masks)
    review = review_axes(pre.metadata, AxisMapping("TCYX", "TCYX"))
    from morphobricks_preprocessing.mask_checks import mask_issues

    issues = mask_issues(pre.masks, pre.metadata, review)
    assert "mask_planes_mismatch" in errors(issues)
    assert mask_axes_questions(pre.masks, issues, {}) == []  # nothing to state: save again


def test_a_yx_statement_recorded_by_an_earlier_run_is_not_applied_again():
    class Manifest:
        masks = {
            "files": [
                {"type": "Nuclei", "roles": "YX", "axes_from": "stated", "source": "folder"},
                {"type": "Cells", "roles": "TYX", "axes_from": "stated", "source": "folder"},
            ]
        }

    assert recorded_mask_axes(Manifest()) == {"Cells": "TYX"}
