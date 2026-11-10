"""PP-10d: the mask step shared by the command line's ``masks`` and the notebook.

:func:`prepare_mask_step` pairs the masks with the inputs the run in the output folder
recorded unless given, and :func:`describe_masks` scans them and writes
``mask_relationships.json`` holding the folder's lock; ``masks`` only prints their results.
"""

import json

import pytest

from described_tree import main_described, without_mask_step
from morphobricks_preprocessing.mask_scan import MASK_REPORT_FILENAME
from morphobricks_preprocessing.run import (
    MaskStep,
    describe_masks,
    mask_step,
    prepare_mask_step,
    recorded_masks,
)
from morphobricks_preprocessing.run.lock import output_lock, read_lock
from morphobricks_preprocessing.testing import make_clean_run


@pytest.fixture
def clean(tmp_path):
    return without_mask_step(
        make_clean_run(tmp_path, axes="YX", shape=(8, 8), mask_types=("Cells", "Nuclei"))
    )


def without_versions(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("versions")
    return data


def test_the_step_takes_every_input_the_run_recorded(clean):
    step = prepare_mask_step(None, clean.output)
    assert step.refusal is None and step.ready
    assert step.changes == ()
    assert step.preflight.discovery.root == clean.experiment.resolve()
    assert step.masks.folder == clean.masks.resolve()
    assert step.masks.types == ("Cells", "Nuclei")
    assert step.output_folder == clean.output


def test_describe_masks_writes_the_report_the_command_writes(clean, capsys):
    seen = []
    result = describe_masks(
        prepare_mask_step(None, clean.output), progress=lambda *a: seen.append(a)
    )
    assert result.refusal is None
    assert result.path == clean.output / MASK_REPORT_FILENAME
    assert seen[0] == (0, 4, "ctrl/rep1/a.ome.tif") and seen[-1] == (4, 4, "")
    assert set(result.report.types) == {"Cells", "Nuclei"}
    written = without_versions(result.path)
    result.path.unlink()
    assert main_described(["masks", "--output", str(clean.output)]) == 0
    assert without_versions(clean.output / MASK_REPORT_FILENAME) == written
    assert f"Report written to {result.path}" in capsys.readouterr().out


def test_without_an_experiment_or_a_recorded_run_the_step_is_refused(tmp_path):
    step = prepare_mask_step(None, tmp_path / "out")
    assert step.refusal.code == "experiment_not_recorded"
    assert step.preflight is None and not step.ready
    assert step.masks.types == ()
    with pytest.raises(ValueError, match="cannot be described"):
        describe_masks(step)


def test_a_masks_folder_given_is_used_and_named_with_its_effect(clean, tmp_path):
    other = tmp_path / "other"
    clean.masks.rename(other)
    step = prepare_mask_step(None, clean.output, masks_folder=other)
    assert step.ready
    (change,) = step.changes
    assert change.name == "masks_folder"
    assert "used for this scan only" in change.effect


def test_mask_errors_stop_the_step_before_any_mask_is_read(clean):
    step = prepare_mask_step(clean.experiment, clean.output, masks_folder=clean.experiment / "ctrl")
    assert "masks_inside_experiment" in {issue.code for issue in step.issues}
    assert not step.ready
    with pytest.raises(ValueError):
        describe_masks(step)


def test_a_locked_output_folder_is_refused_before_any_mask_is_read(clean, monkeypatch):
    step = prepare_mask_step(None, clean.output)
    seen = []
    monkeypatch.setattr(mask_step, "scan_masks", lambda *a, **k: seen.append(a))
    with output_lock(clean.output):
        result = describe_masks(step, progress=lambda *a: seen.append(a))
    assert result.refusal.code == "output_locked"
    assert result.path is None and result.report is None
    assert seen == []
    assert not (clean.output / MASK_REPORT_FILENAME).exists()


def test_the_lock_is_held_from_the_first_mask_read_to_the_write(clean):
    held = []
    result = describe_masks(
        prepare_mask_step(None, clean.output),
        progress=lambda *a: held.append(read_lock(clean.output) is not None),
    )
    assert result.refusal is None
    assert held and all(held)
    assert read_lock(clean.output) is None


def test_the_command_refuses_a_locked_folder_before_reading_a_mask(clean, capsys):
    with output_lock(clean.output):
        assert main_described(["masks", "--output", str(clean.output)]) == 1
    out = capsys.readouterr().out
    assert "output_locked" in out or "Another run is using this output folder" in out
    assert "[1/4]" not in out
    assert not (clean.output / MASK_REPORT_FILENAME).exists()


def test_no_masks_found_is_not_ready(tmp_path):
    bare = make_clean_run(tmp_path, axes="YX", shape=(8, 8))
    step = prepare_mask_step(None, bare.output)
    assert isinstance(step, MaskStep)
    assert step.refusal is None and step.masks.types == () and not step.ready


def test_recorded_masks_names_the_run_the_step_describes(clean, tmp_path):
    recorded = recorded_masks(clean.output)
    assert recorded.output_folder == clean.output.resolve()
    assert recorded.experiment_root == clean.experiment.resolve()
    assert recorded.masks_folder == clean.masks.resolve()
    assert recorded.mask_types == ("Cells", "Nuclei") and recorded.written_at
    assert recorded_masks(tmp_path / "nothing") is None
    bare = make_clean_run(tmp_path / "bare", axes="YX", shape=(8, 8))
    assert recorded_masks(bare.output).mask_types == ()
