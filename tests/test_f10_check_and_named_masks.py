"""F10 (matrix run, 3 November 2026; owner decision, 4 November 2026).

1. ``check`` takes ``--missing-as-singleton`` as ``run`` does (same option, same default,
   same review), so an experiment mixing 2-D images and Z-stacks passes ``check`` once it
   is confirmed, and ``check`` and ``run`` report the same errors for the same inputs.
2. Owner decision of 4 November 2026, reversing #148: a 2-D mask whose own metadata names
   its axes ``YX`` (TIFF, OME-TIFF, OME-Zarr label image) is NOT shared by the planes of a
   Z-stack or time-lapse without a statement. Only ``--mask-axes TYPE=YX`` (or YX under
   the notebook's Mask axes) shares it, recorded ``shared: true``, ``axes_from:
   "stated"``; without it the error names exactly that statement.
"""

import json

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import (
    AxisMapping,
    default_design,
    default_output_folder,
    prefill_mapping,
    preflight,
    read_manifest,
    run_experiment,
    write_experiment_design,
)
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.run.check_step import suggested_review
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError
from tiff_tree import write_data, write_plain


def ome(path, data, axes):
    """Write an OME-TIFF naming its axes, as microscopes and Fiji export them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata={"axes": axes})
    return path


def labels(shape, start=1):
    return (np.arange(np.prod(shape)).reshape(shape) % 5 + start).astype(np.uint16)


def confirm_design(root, output):
    design = default_design(preflight(root).dataset.repeats)
    for group in design.groups:
        design = design.with_confirmed(group, True, "2026-11-04")
    output.mkdir(parents=True, exist_ok=True)
    write_experiment_design(design, output / "experiment_design.csv")


def error_codes(output):
    report = json.loads((output / "discovery_report.json").read_text(encoding="utf-8"))
    return sorted({i["code"] for i in report["issues"] if i["severity"] == "error"})


@pytest.fixture
def mixed(tmp_path):
    """Two 2-D images (CYX) and one Z-stack (ZCYX), each with its own masks (synA)."""
    root, masks = tmp_path / "exp", tmp_path / "masks"
    rng = np.random.default_rng(4)
    for rel in ("ctrl/r1/a.tif", "drug/r1/a.ome.tif"):
        ome(root / rel, rng.integers(0, 200, (2, 6, 8), dtype=np.uint16), "CYX")
        ome(masks / "Cells" / rel, labels((6, 8)), "YX")
    ome(root / "ctrl/r1/b.ome.tif", rng.integers(0, 200, (2, 2, 6, 8), dtype=np.uint16), "ZCYX")
    ome(masks / "Cells/ctrl/r1/b.ome.tif", labels((2, 6, 8)), "ZYX")
    return root, masks


# 1. check --missing-as-singleton ---------------------------------------------------------


def test_check_refuses_a_mixed_experiment_until_it_is_confirmed(mixed, tmp_path, capsys):
    root, masks = mixed
    out = tmp_path / "out"
    args = ["check", str(root), "--masks", str(masks), "--output", str(out)]
    assert main(args) == 1
    printed = capsys.readouterr().out
    assert "lack some of the dimensions of the axis mapping (ZCYX)" in printed
    assert error_codes(out) == ["missing_axes"]
    assert main([*args, "--missing-as-singleton"]) == 0
    printed = capsys.readouterr().out
    assert "Error" not in printed
    assert "--axes ZCYX --missing-as-singleton" in printed
    assert error_codes(out) == []


@pytest.mark.parametrize("singleton", [False, True])
def test_check_and_run_report_the_same_errors(mixed, tmp_path, capsys, singleton):
    root, masks = mixed
    option = ["--missing-as-singleton"] if singleton else []
    checked, ran = tmp_path / "checked", tmp_path / "ran"
    common = [str(root), "--masks", str(masks), *option]
    status = main(["check", *common, "--output", str(checked)])
    confirm_design(root, ran)
    assert main(["run", *common, "--output", str(ran), "--axes", "ZCYX"]) == status
    capsys.readouterr()
    assert error_codes(checked) == error_codes(ran) == ([] if singleton else ["missing_axes"])


def test_check_takes_the_answer_the_earlier_run_recorded(mixed, tmp_path, capsys):
    root, masks = mixed
    out = tmp_path / "out"
    confirm_design(root, out)
    common = [str(root), "--masks", str(masks), "--output", str(out)]
    assert main(["run", *common, "--axes", "ZCYX", "--missing-as-singleton"]) == 0
    assert main(["check", *common]) == 0  # as 'run' would read them again
    assert main(["check", *common, "--no-missing-as-singleton"]) == 1
    assert "lack some of the dimensions" in capsys.readouterr().out


def test_suggested_review_answers_the_missing_axes_as_the_run_does(mixed):
    root, _ = mixed
    pre = preflight(root)
    assert "missing_axes" in {i.code for i in pre.issues}
    refused, _ = suggested_review(pre)
    assert [i.code for i in refused.issues if i.is_error] == ["missing_axes"]
    accepted, review = suggested_review(pre, missing_as_singleton=True)
    assert accepted.ok
    assert review.file_mappings["ctrl/r1/a.tif"].singleton_roles == ("Z",)


# 2. A 2-D mask whose metadata names YX: shared only when stated -------------------------


@pytest.fixture
def z_stack(tmp_path):
    """One ZCYX image with per-plane Cells (ZYX) and a 2-D Nuclei named YX (synZ2D)."""
    root, masks = tmp_path / "exp", tmp_path / "masks"
    image = np.arange(3 * 2 * 6 * 8, dtype=np.uint16).reshape(3, 2, 6, 8)
    ome(root / "ctrl/r1/a.tif", image, "ZCYX")
    ome(masks / "Cells/ctrl/r1/a.tif", labels((3, 6, 8)), "ZYX")
    ome(masks / "Nuclei/ctrl/r1/a.tif", labels((6, 8), 2), "YX")
    return root, masks


STATE_NUCLEI = "--mask-axes Nuclei=YX"


def test_a_named_2d_mask_of_a_z_stack_is_refused_until_stated(z_stack, tmp_path, capsys):
    root, masks = z_stack
    out = tmp_path / "out"
    common = [str(root), "--masks", str(masks), "--output", str(out)]
    assert main(["check", *common]) == 1
    printed = capsys.readouterr().out
    assert error_codes(out) == ["mask_planes_differ"]
    assert STATE_NUCLEI in printed  # the error says exactly what to add
    stated = [*common, "--mask-axes", "Nuclei=YX"]
    assert main(["check", *stated]) == 0, capsys.readouterr().out
    confirm_design(root, out)
    assert main(["run", *stated, "--axes", "ZCYX"]) == 0, capsys.readouterr().out
    entries = {e["type"]: e for e in read_manifest(out / "run_manifest.json").masks["files"]}
    nuclei = entries["Nuclei"]
    assert (nuclei["axes"], nuclei["roles"], nuclei["shape"]) == ("YX", "YX", [6, 8])
    assert (nuclei["shared"], nuclei["axes_from"]) == (True, "stated")
    assert (entries["Cells"]["shared"], entries["Cells"]["roles"]) == (False, "ZYX")
    inputs = RunInputs.open(out)
    (fov,) = inputs.fovs()
    assert fov.z_count == 3
    for z in range(3):
        np.testing.assert_array_equal(inputs.read_mask(fov, "Nuclei", z=z), labels((6, 8), 2))
        np.testing.assert_array_equal(inputs.read_mask(fov, "Cells", z=z), labels((3, 6, 8))[z])
    with pytest.raises(IndexError):
        inputs.read_mask(fov, "Nuclei", z=3)  # outside the image, even for a shared mask


def test_without_a_statement_nothing_is_recorded_shared(z_stack, tmp_path):
    root, masks = z_stack
    named = run_experiment(
        root, prefill_mapping("ZCYX"), output_folder=tmp_path / "named", masks_folder=masks
    )
    assert not named.written
    (differ,) = [i for i in named.issues if i.code == "mask_planes_differ"]
    assert STATE_NUCLEI in differ.action
    (nuclei,) = [m for m in preflight(root, masks_folder=masks).masks.files if m.axes == "YX"]
    assert (nuclei.axes_from, nuclei.shared) == ("header", False)
    assert not hasattr(nuclei, "named_shared")  # the header-derived path is gone


def test_a_named_2d_mask_of_a_time_lapse_needs_its_statement(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_data(root / "ctrl/a.tif", np.zeros((4, 6, 8), np.uint8), "TYX")
    write_data(masks / "Cells/ctrl/a.tif", labels((6, 8)), "YX")  # shaped metadata names YX
    (mask,) = preflight(root, masks_folder=masks).masks.files
    assert (mask.axes_from, mask.shared) == ("header", False)
    refused = run_experiment(root, prefill_mapping("TYX"), masks_folder=masks)
    assert not refused.written
    (mismatch,) = [i for i in refused.issues if i.code == "mask_planes_mismatch"]
    assert "--mask-axes Cells=YX" in mismatch.action
    assert "choose YX for the type under Mask axes" in mismatch.action
    result = run_experiment(
        root, prefill_mapping("TYX"), masks_folder=masks, mask_axes={"Cells": "YX"}
    )
    assert result.written, result.issues
    inputs = RunInputs.open(result.output_folder)
    (fov,) = inputs.fovs()
    for t in range(4):
        np.testing.assert_array_equal(inputs.read_mask(fov, "Cells", t=t), labels((6, 8)))


def test_a_named_2d_mask_of_a_single_plane_image_is_not_marked_shared(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    ome(root / "ctrl/a.ome.tif", np.zeros((2, 6, 8), np.uint16), "CYX")
    ome(masks / "Cells/ctrl/a.ome.tif", labels((6, 8)), "YX")
    result = run_experiment(root, prefill_mapping("CYX"), masks_folder=masks)
    (entry,) = read_manifest(result.manifest_path).masks["files"]
    assert (entry["shared"], entry["axes_from"]) == (False, "header")  # as before F10


def test_a_mask_with_auto_generated_ome_metadata_needs_its_statement(tmp_path):
    # tifffile writes OME metadata into any .ome.tif, naming a 2-D page YX by itself: that
    # is not the user saying the mask is meant for every plane.
    root, masks = tmp_path / "exp", tmp_path / "masks"
    ome(root / "ctrl/a.ome.tif", np.zeros((3, 6, 8), np.uint16), "ZYX")
    path = masks / "Cells/ctrl/a.ome.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(path, labels((6, 8)))  # no axes given: tifffile's own OME header
    with tifffile.TiffFile(path) as tif:
        assert tif.is_ome and tif.series[0].axes == "YX"
    (mask,) = preflight(root, masks_folder=masks).masks.files
    assert (mask.axes, mask.axes_from, mask.shared) == ("YX", "header", False)
    refused = run_experiment(root, prefill_mapping("ZYX"), masks_folder=masks)
    assert "mask_planes_mismatch" in {i.code for i in refused.issues}
    stated = run_experiment(
        root, prefill_mapping("ZYX"), masks_folder=masks, mask_axes={"Cells": "YX"}
    )
    assert stated.written, stated.issues


# Never guessed: a mask or an image whose metadata names nothing -------------------------


def test_an_unnamed_2d_mask_of_a_z_stack_still_needs_mask_axes(z_stack, tmp_path, capsys):
    root, masks = z_stack
    # A plain page (named .tif: tifffile writes OME metadata into any .ome.tif).
    write_plain(masks / "Nuclei/ctrl/r1/a.tif", labels((6, 8), 2))
    (nuclei,) = [
        m for m in preflight(root, masks_folder=masks).masks.files if m.mask_type == "Nuclei"
    ]
    assert not nuclei.shared
    out = tmp_path / "out"
    common = [str(root), "--masks", str(masks), "--output", str(out)]
    assert main(["check", *common]) == 1  # its planes differ from the Cells mask's
    assert "mask_planes_differ" in error_codes(out)
    refused = run_experiment(
        root, prefill_mapping("ZCYX"), output_folder=tmp_path / "run", masks_folder=masks
    )
    assert not refused.written
    differ = next(i for i in refused.issues if i.code == "mask_planes_differ")
    assert STATE_NUCLEI in differ.action
    assert main(["check", *common, "--mask-axes", "Nuclei=YX"]) == 0, capsys.readouterr().out
    stated = run_experiment(
        root,
        prefill_mapping("ZCYX"),
        output_folder=tmp_path / "run",
        masks_folder=masks,
        mask_axes={"Nuclei": "YX"},
    )
    assert stated.written


def test_a_named_2d_mask_of_a_plain_stack_still_needs_mask_axes(tmp_path):
    # The image's metadata does not name its planes: its roles are only confirmed.
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_plain(root / "ctrl/a.tif", np.zeros((3, 6, 8), np.uint8))
    ome(masks / "Cells/ctrl/a.tif", labels((6, 8)), "YX")
    assert not preflight(root, masks_folder=masks).masks.files[0].shared
    refused = run_experiment(root, AxisMapping("IYX", "ZYX"), masks_folder=masks)
    assert "mask_planes_mismatch" in {i.code for i in refused.issues}
    stated = run_experiment(
        root, AxisMapping("IYX", "ZYX"), masks_folder=masks, mask_axes={"Cells": "YX"}
    )
    assert stated.written


def test_the_output_folder_defaults_next_to_the_experiment(mixed, capsys):
    root, masks = mixed
    assert main(["check", str(root), "--masks", str(masks), "--missing-as-singleton"]) == 0
    capsys.readouterr()
    assert error_codes(default_output_folder(root)) == []


def test_the_notebooks_check_report_takes_the_recorded_answer(mixed, capsys):
    pytest.importorskip("ipywidgets")
    pytest.importorskip("matplotlib")
    from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook

    root, masks = mixed
    out = default_output_folder(root)

    def checked():
        nb = PreprocessingNotebook(str(root))
        for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
            step()
        nb.masks_folder.value = str(masks)
        nb.check_button.click()
        return nb

    first = checked()
    assert not first.missing_as_singleton.value
    assert error_codes(out) == ["missing_axes"]
    confirm_design(root, out)
    common = [str(root), "--masks", str(masks), "--output", str(out)]
    assert main(["run", *common, "--axes", "ZCYX", "--missing-as-singleton"]) == 0
    capsys.readouterr()
    again = checked()  # step 2 shows the recorded answer, and the report follows it
    assert again.missing_as_singleton.value
    assert error_codes(out) == []


# Review of PR #148: the reused answer is shown, and nothing else is hidden ---------------


def test_check_and_run_say_when_the_recorded_answer_is_reused(mixed, tmp_path, capsys):
    root, masks = mixed
    out = tmp_path / "out"
    confirm_design(root, out)
    common = [str(root), "--masks", str(masks), "--output", str(out)]
    assert main(["run", *common, "--axes", "ZCYX", "--missing-as-singleton"]) == 0
    assert "as confirmed for the earlier run (give" not in capsys.readouterr().out
    for command in (["check", *common], ["run", *common, "--axes", "ZCYX"]):
        assert main(command) == 0
        printed = capsys.readouterr().out
        assert "Images lacking some axes: read with one plane along them, as confirmed" in printed
    assert main(["check", *common, "--missing-as-singleton"]) == 0  # given: nothing reused
    assert "Images lacking some axes" not in capsys.readouterr().out


def test_check_suggests_the_option_only_for_images_lacking_axes(z_stack, tmp_path, capsys):
    root, masks = z_stack
    out = tmp_path / "out"
    args = ["check", str(root), "--masks", str(masks), "--output", str(out)]
    assert main([*args, "--missing-as-singleton", "--mask-axes", "Nuclei=YX"]) == 0
    assert "Suggested option for 'run': --axes ZCYX (axes" in capsys.readouterr().out


def test_check_keeps_the_other_errors_of_a_confirmed_mixed_experiment(mixed, tmp_path, capsys):
    root, masks = mixed
    (root / "drug/r1/broken.tif").write_bytes(b"not a TIFF file")
    checked, ran = tmp_path / "checked", tmp_path / "ran"
    common = [str(root), "--masks", str(masks), "--missing-as-singleton"]
    assert main(["check", *common, "--output", str(checked)]) == 1
    confirm_design(root, ran)
    assert main(["run", *common, "--output", str(ran), "--axes", "ZCYX"]) == 1
    capsys.readouterr()
    found = error_codes(checked)
    assert found and "missing_axes" not in found
    assert found == error_codes(ran)


def test_a_named_2d_mask_of_a_partly_unnamed_image_is_not_shared(tmp_path):
    # Shaped axes QZYX: the Q is not named, so the image's planes are not all named.
    root, masks = tmp_path / "exp", tmp_path / "masks"
    write_data(root / "ctrl/a.tif", np.zeros((2, 3, 6, 8), np.uint8), "QZYX")
    ome(masks / "Cells/ctrl/a.tif", labels((6, 8)), "YX")
    (mask,) = preflight(root, masks_folder=masks).masks.files
    assert (mask.axes_from, mask.shared) == ("header", False)


def test_a_label_image_of_a_one_plane_ome_zarr_is_not_marked_shared(tmp_path):
    from zarr_tree import write_labels, write_ngff

    image = write_ngff(tmp_path / "exp/ctrl/a.ome.zarr", np.zeros((1, 6, 8), np.uint16), "zyx")
    write_labels(image, {"cells": labels((6, 8))}, "yx")
    (mask,) = preflight(tmp_path / "exp").masks.files
    assert (mask.axes_from, mask.shared) == ("header", False)


def test_a_yx_label_image_of_an_ome_zarr_z_stack_is_shared_only_when_stated(tmp_path):
    # OME-NGFF axes are always the metadata's, and still not a statement (owner, 4 November
    # 2026): a "yx" label image of a zyx image is refused until its type is stated YX.
    from zarr_tree import write_labels, write_ngff

    root = tmp_path / "exp"
    image = write_ngff(root / "ctrl/a.ome.zarr", np.zeros((3, 6, 8), np.uint16), "zyx")
    write_labels(image, {"cells": labels((6, 8))}, "yx")
    (mask,) = preflight(root).masks.files
    assert (mask.axes_from, mask.shared) == ("header", False)
    refused = run_experiment(root, prefill_mapping("ZYX"))
    assert not refused.written
    (mismatch,) = [i for i in refused.issues if i.code == "mask_planes_mismatch"]
    assert mismatch.paths == (mask.path,) and "--mask-axes cells=YX" in mismatch.action
    result = run_experiment(root, prefill_mapping("ZYX"), mask_axes={"cells": "YX"})
    assert result.written, result.issues
    (entry,) = read_manifest(result.manifest_path).masks["files"]
    assert (entry["shared"], entry["axes_from"], entry["roles"]) == (True, "stated", "YX")
    inputs = RunInputs.open(result.output_folder)
    (fov,) = inputs.fovs()
    for z in range(3):
        np.testing.assert_array_equal(inputs.read_mask(fov, "cells", z=z), labels((6, 8)))


# A manifest #148 wrote (main after v0.4.0rc5, no tag) is never trusted -------------------

F10_MAIN = "f7c54bd2985ec3bd72e59938270cb2f398247fee"
"""main with #148 merged: it recorded a named 2-D mask as shared with ``axes_from: "header"``."""

WRITE_WITH_F10 = """
from morphobricks_preprocessing import prefill_mapping, run_experiment
root, masks, out = (Path(a) for a in sys.argv[2:5])
result = run_experiment(root, prefill_mapping("TYX"), output_folder=out, masks_folder=masks)
print(json.dumps(result.written))
"""


def test_a_mask_shared_by_its_header_in_an_f10_manifest_is_refused(tmp_path):
    from released_code import PRELUDE, extract, run_with

    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    write_data(root / "ctrl/a.tif", np.zeros((4, 6, 8), np.uint8), "TYX")
    write_data(masks / "Cells/ctrl/a.tif", labels((6, 8)), "YX")
    src = extract(F10_MAIN, tmp_path / "f10")
    assert run_with(src, PRELUDE + WRITE_WITH_F10, root, masks, out) is True
    (entry,) = read_manifest(out / "run_manifest.json").masks["files"]
    assert (entry["shared"], entry["axes_from"]) == (True, "header")  # as #148 wrote it
    with pytest.raises(RunInputsError, match="without a statement") as refused:
        RunInputs.open(out)
    text = str(refused.value)
    assert "Cells/ctrl/a.tif" in text and "Run preprocessing again" in text
    assert "--mask-axes Cells=YX" in text
    # The rerun the refusal asks for: refused without the statement, recorded with it.
    assert not run_experiment(root, output_folder=out, masks_folder=masks).written
    stated = run_experiment(root, output_folder=out, masks_folder=masks, mask_axes={"Cells": "YX"})
    assert stated.written, stated.issues
    (entry,) = read_manifest(out / "run_manifest.json").masks["files"]
    assert (entry["shared"], entry["axes_from"]) == (True, "stated")
    (fov,) = RunInputs.open(out).fovs()
    assert fov.t_count == 4


def test_only_a_stated_shared_mask_record_is_trusted():
    from morphobricks_preprocessing.mask_record import unstated_shared_text

    stated = {"path": "N/a.tif", "type": "N", "shared": True, "axes_from": "stated"}
    plain = {"path": "C/a.tif", "type": "C", "shared": False, "axes_from": "header"}
    assert unstated_shared_text([stated, plain]) is None
    for how in ("header", "", None):
        record = {"path": "C/a.tif", "type": "C", "shared": True, "axes_from": how}
        text = unstated_shared_text([stated, record])
        assert text is not None and "C/a.tif" in text and "--mask-axes C=YX" in text
        assert "N/a.tif" not in text
