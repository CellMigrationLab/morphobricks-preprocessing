"""``--log`` is checked before it is opened, and opened only after the arguments (issue #99).

Before the fix the log was opened, created or appended to, before anything was checked: in
the experiment folder, over a source image, or over an output file of the run.
"""

import os

import pytest

from morphobricks_preprocessing.cli import main
from morphobricks_preprocessing.testing import make_clean_run


@pytest.fixture
def clean(tmp_path):
    return make_clean_run(
        tmp_path / "run", axes="YX", shape=(16, 20), mask_types=("Cells",), design=True
    )


def _refused(capsys, path, before=None):
    out = capsys.readouterr().out
    assert "was not opened" in out and "Nothing was written" in out
    if before is None:
        assert not path.exists()
    else:
        assert path.read_bytes() == before
    return out


def _run(clean, *more):
    return ["run", str(clean.experiment), "--output", str(clean.output), *more]


def _masks(clean, *more):
    args = ["masks", str(clean.experiment), "--output", str(clean.output)]
    return [*args, "--masks", str(clean.masks), *more]


@pytest.mark.parametrize("command", [_run, _masks])
def test_a_log_in_the_experiment_folder_is_refused(clean, capsys, command):
    log = clean.experiment / "notes.txt"
    assert main(command(clean, "--log", str(log))) == 1
    assert "inside the experiment folder" in _refused(capsys, log)


@pytest.mark.parametrize("command", [_run, _masks])
def test_a_log_over_a_source_image_leaves_it_unchanged(clean, capsys, command):
    image = clean.experiment / "ctrl/rep1/a.ome.tif"
    before = image.read_bytes()
    assert main(command(clean, "--log", str(image))) == 1
    _refused(capsys, image, before)


@pytest.mark.parametrize("command", [_run, _masks])
def test_a_log_in_the_masks_folder_is_refused(clean, capsys, command):
    log = clean.masks / "Cells/notes.txt"
    assert main(command(clean, "--log", str(log))) == 1
    assert "inside the masks folder" in _refused(capsys, log)


@pytest.mark.parametrize("command", [_run, _masks])
@pytest.mark.parametrize(
    "name",
    ["plane_histograms.csv", "run_manifest.json", "discovery_report.txt", "experiment_design.csv"],
)
def test_a_log_over_an_output_file_leaves_it_unchanged(clean, capsys, command, name):
    target = clean.output / name
    before = target.read_bytes()
    assert main(command(clean, "--log", str(target))) == 1
    assert f"the run's {name}" in _refused(capsys, target, before)


@pytest.mark.parametrize(
    "name",
    [
        "calibration.csv",
        "mask_relationships.json",
        ".morphobricks-preprocessing.lock",
        ".publish/log.txt",
        ".staging/log.txt",
    ],
)
def test_a_log_named_as_another_output_is_refused(clean, capsys, name):
    log = clean.output / name
    assert main(_run(clean, "--log", str(log))) == 1
    _refused(capsys, log)


def test_the_recorded_experiment_and_masks_folders_are_refused(clean, capsys):
    # Only --output given: the run uses the recorded experiment and masks folders.
    for log, where in [
        (clean.experiment / "notes.txt", "experiment folder"),
        (clean.masks / "notes.txt", "masks folder of the earlier run"),
    ]:
        assert main(["run", "--output", str(clean.output), "--log", str(log)]) == 1
        assert f"inside the {where}" in _refused(capsys, log)


def test_a_log_is_not_opened_when_the_arguments_are_refused(clean, capsys, tmp_path):
    log = tmp_path / "run.log"
    assert main(_run(clean, "--mask-axes", "Cells", "--log", str(log))) == 1
    capsys.readouterr()
    assert not log.exists()


@pytest.mark.parametrize("command", [_run, _masks])
def test_a_log_elsewhere_in_the_output_folder_is_written(clean, capsys, command):
    log = clean.output / "run.log"
    status = main(command(clean, "--log", str(log)))
    assert "was not opened" not in capsys.readouterr().out
    assert status == 0 and log.is_file()


def test_convert_refuses_a_log_in_the_experiment_or_the_copies(clean, capsys, tmp_path):
    copies = tmp_path / "copies"
    for log, where in [
        (clean.experiment / "notes.txt", "experiment folder"),
        (copies / "notes.txt", "folder of the copies"),
    ]:
        copies.mkdir(exist_ok=True)
        args = ["convert", str(clean.experiment), "--to", str(copies), "--axes", "YX"]
        assert main([*args, "--log", str(log)]) == 1
        assert f"inside the {where}" in _refused(capsys, log)
        assert list(copies.iterdir()) == []


def test_convert_writes_a_log_outside(clean, capsys, tmp_path):
    log = tmp_path / "convert.log"
    args = ["convert", str(clean.experiment), "--to", str(tmp_path / "copies"), "--axes", "YX"]
    assert main([*args, "--log", str(log)]) == 0
    assert log.read_text(encoding="utf-8")


def test_a_log_in_a_missing_folder_or_naming_a_folder_is_refused(clean, capsys, tmp_path):
    for log in (tmp_path / "missing/run.log", tmp_path):
        assert main(_run(clean, "--log", str(log))) == 1
        assert "was not opened" in capsys.readouterr().out


@pytest.mark.parametrize("command", [_run, _masks])
def test_a_log_hard_linked_to_a_source_image_leaves_it_unchanged(clean, capsys, tmp_path, command):
    image = clean.experiment / "ctrl/rep1/a.ome.tif"
    before = image.read_bytes()
    log = tmp_path / "elsewhere.log"
    try:
        os.link(image, log)
    except (OSError, NotImplementedError):
        pytest.skip("hard links cannot be made here")
    assert main(command(clean, "--log", str(log))) == 1
    assert "hard link" in _refused(capsys, image, before)


def test_an_output_named_in_another_case_is_refused(clean, capsys):
    before = (clean.output / "run_manifest.json").read_bytes()
    assert main(_run(clean, "--log", str(clean.output / "Run_Manifest.JSON"))) == 1
    _refused(capsys, clean.output / "run_manifest.json", before)


def test_the_experiment_folder_in_another_case_is_refused(clean, capsys):
    # On a file system that ignores case (macOS, Windows) it is the same folder.
    other = clean.experiment.with_name(clean.experiment.name.swapcase())
    if not other.is_dir():
        pytest.skip("this file system tells names apart by case")
    log = other / "notes.txt"
    assert main(_run(clean, "--log", str(log))) == 1
    assert "inside the experiment folder" in _refused(capsys, clean.experiment / "notes.txt")


def test_a_log_over_the_design_given_leaves_it_unchanged(clean, capsys, tmp_path):
    # Review of #128: the confirmed design given with --design, outside the output folder,
    # was appended to (and the run went on).
    design = tmp_path / "my_design.csv"
    design.write_bytes((clean.output / "experiment_design.csv").read_bytes())
    before = design.read_bytes()
    assert main(_run(clean, "--design", str(design), "--log", str(design))) == 1
    assert "design file given with --design" in _refused(capsys, design, before)
