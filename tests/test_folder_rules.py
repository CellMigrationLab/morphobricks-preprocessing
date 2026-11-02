"""The experiment, masks and output folders may not hold one another (audit B1, I1).

The reproductions of the audit of 3 November 2026: an experiment inside the masks folder,
named-axis copies inside it, and an output folder inside it, which must be refused before
anything is written.
"""

import json
import os

import numpy as np
import pytest

from morphobricks_preprocessing import (
    CONVERSION_RECORD_FILENAME,
    convert_experiment,
    default_output_folder,
    prefill_mapping,
    preflight,
    run_experiment,
)
from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.conversion_plan import preview_conversion
from morphobricks_preprocessing.design_edit import DesignEdit
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME
from morphobricks_preprocessing.run.calibrate import update_calibration
from morphobricks_preprocessing.run.check_step import write_check_outputs
from morphobricks_preprocessing.run.design_file import edit_design
from morphobricks_preprocessing.run.output_guard import OutputInsideMasksError
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError
from morphobricks_preprocessing.testing import make_clean_run
from tiff_tree import write_data

YX = prefill_mapping("YX")


def errors(issues):
    return [i.code for i in issues if i.is_error]


def labels(shape):
    return (np.arange(np.prod(shape)).reshape(shape) % 7 + 1).astype(np.uint16)


def experiment(root):
    write_data(root / "ctrl/a.tif", np.arange(48, dtype=np.uint8).reshape(6, 8), "YX")
    return root


def masks_next_to(tmp_path):
    experiment(tmp_path / "exp")
    write_data(tmp_path / "masks/Cells/ctrl/a.tif", labels((6, 8)), "YX")
    return tmp_path / "exp", tmp_path / "masks"


def test_an_experiment_inside_the_masks_folder_is_refused(tmp_path):
    # B1: m1/{exp, Cells} with masks_folder=m1 read the images as masks of type "exp".
    m1 = tmp_path / "m1"
    experiment(m1 / "exp")
    write_data(m1 / "Cells/ctrl/a.tif", labels((6, 8)), "YX")
    result = run_experiment(m1 / "exp", YX, masks_folder=m1, output_folder=tmp_path / "out")
    assert not result.written and errors(result.issues) == ["experiment_inside_masks"]
    (issue,) = [i for i in result.issues if i.code == "experiment_inside_masks"]
    assert issue.paths == ("exp",)
    assert errors(preflight(m1 / "exp", masks_folder=m1).masks.issues) == [
        "experiment_inside_masks"
    ]


def test_an_experiment_inside_the_masks_folder_by_a_link_is_refused(tmp_path):
    m1 = tmp_path / "m1"
    experiment(m1 / "exp")
    write_data(m1 / "Cells/ctrl/a.tif", labels((6, 8)), "YX")
    try:
        os.symlink(m1, tmp_path / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links cannot be made here")
    pre = preflight(m1 / "exp", masks_folder=tmp_path / "link")
    assert errors(pre.masks.issues) == ["experiment_inside_masks"]


def test_copies_inside_the_masks_folder_are_refused(tmp_path):
    # B1: after convert_experiment(exp, YX, masks/copies), type "copies" held intensities.
    root, masks = masks_next_to(tmp_path)
    assert convert_experiment(root, YX, masks / "copies").written
    result = run_experiment(root, YX, masks_folder=masks, output_folder=tmp_path / "out")
    assert not result.written and errors(result.issues) == ["copies_inside_masks"]
    (issue,) = [i for i in result.issues if i.code == "copies_inside_masks"]
    assert issue.paths == (f"copies/{CONVERSION_RECORD_FILENAME}",)


def test_a_copy_folder_inside_the_masks_folder_is_refused(tmp_path):
    root, masks = masks_next_to(tmp_path)
    # inside the masks folder, the masks folder itself, and a folder holding it
    for target in (masks / "copies", masks, tmp_path):
        result = convert_experiment(root, YX, target, masks_folder=masks)
        expected = "copy_folder_overlaps_experiment" if target == tmp_path else None
        assert not result.written
        assert errors(result.issues) == [expected or "copy_folder_overlaps_masks"]
        preview = preview_conversion(root, YX, target, masks_folder=masks)
        assert errors(preview.issues) == [expected or "copy_folder_overlaps_masks"]
    outside = tmp_path / "elsewhere"
    result = convert_experiment(root, YX, outside / "copies", masks_folder=outside)
    assert errors(result.issues) == ["copy_folder_overlaps_masks"]
    assert not (masks / "copies").exists() and not outside.exists()


def test_the_convert_command_takes_the_recorded_masks_folder(tmp_path, capsys):
    root, masks = masks_next_to(tmp_path)
    assert run_experiment(root, YX, masks_folder=masks).written  # the default output folder
    assert main(["convert", str(root), "--to", str(masks / "copies"), "--axes", "YX"]) == 1
    assert "Copies inside the masks folder would be read as masks" in capsys.readouterr().out
    assert not (masks / "copies").exists()
    other = tmp_path / "other_masks"
    other.mkdir()
    args = ["convert", str(root), "--to", str(other / "copies"), "--axes", "YX"]
    assert main([*args, "--masks", str(other)]) == 1
    assert main(["convert", str(root), "--to", str(tmp_path / "copies"), "--axes", "YX"]) == 0


def test_the_run_writes_nothing_into_the_masks_folder(tmp_path):
    # I1: the output folder, its lock and the report were made before output_inside_masks.
    root, masks = masks_next_to(tmp_path)
    result = run_experiment(root, YX, masks_folder=masks, output_folder=masks / "out")
    assert not result.written and errors(result.issues) == ["output_inside_masks"]
    assert not (masks / "out").exists()


def test_check_and_masks_write_nothing_into_the_masks_folder(tmp_path):
    root, masks = masks_next_to(tmp_path)
    out = masks / "out"
    assert main(["check", str(root), "--masks", str(masks), "--output", str(out)]) == 1
    assert not out.exists()
    pre = preflight(root, out, masks_folder=masks)
    assert errors(pre.issues) == ["output_inside_masks"]
    assert write_check_outputs(pre, out).report is None and not out.exists()
    assert main(["masks", str(root), "--masks", str(masks), "--output", str(out)]) == 1
    assert not out.exists()


def test_a_recorded_masks_folder_holding_the_output_folder_is_refused(tmp_path, capsys):
    # A run recorded before the rule: its masks folder is made the parent of the output.
    clean = make_clean_run(tmp_path, mask_types=("Cells",), design=True)
    path = clean.output / MANIFEST_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["masks"]["folder"] = str(tmp_path)
    path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    before = sorted(p.name for p in clean.output.iterdir())
    with pytest.raises(OutputInsideMasksError, match="inside the masks folder"):
        update_calibration(clean.output, lambda calibration: calibration)
    with pytest.raises(OutputInsideMasksError, match="inside the masks folder"):
        edit_design(clean.output, DesignEdit(confirm_all=True))
    assert main(["design", "--output", str(clean.output), "--confirm-all"]) == 1
    # 'check' is not given the masks folder, so only the recorded one refuses it: exit 1.
    assert main(["check", str(clean.experiment), "--output", str(clean.output)]) == 1
    assert "inside the masks folder" in capsys.readouterr().out
    result = run_experiment(clean.experiment, output_folder=clean.output)
    assert not result.written and "output_inside_masks" in errors(result.issues)
    assert sorted(p.name for p in clean.output.iterdir()) == before
    with pytest.raises(RunInputsError, match="inside the masks folder"):
        RunInputs.open(clean.output)


def test_a_recorded_run_with_copies_in_its_masks_folder_cannot_be_opened(tmp_path):
    clean = make_clean_run(tmp_path, mask_types=("Cells",))
    (clean.masks / "copies").mkdir()
    (clean.masks / "copies" / CONVERSION_RECORD_FILENAME).write_text("{}", encoding="utf-8")
    with pytest.raises(RunInputsError, match="named-axis copies"):
        RunInputs.open(clean.output)


def test_a_recorded_run_on_an_experiment_moved_into_its_masks_cannot_be_opened(tmp_path):
    clean = make_clean_run(tmp_path, mask_types=("Cells",))
    moved = clean.masks / "experiment"
    clean.experiment.rename(moved)
    with pytest.raises(RunInputsError, match="experiment folder is inside the masks folder"):
        RunInputs.open(clean.output, experiment_root=moved)


def test_an_output_folder_holding_the_experiment_and_the_masks_is_allowed(tmp_path):
    # Audit m8, documented: the parent of both is read neither as images nor as masks.
    root, masks = masks_next_to(tmp_path)
    result = run_experiment(root, YX, masks_folder=masks, output_folder=tmp_path)
    assert result.written
    manifest = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["masks"]["types"] == ["Cells"]
    assert default_output_folder(root) != tmp_path
