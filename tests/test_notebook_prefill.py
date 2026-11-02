"""Audit I4: the notebook's inputs start from those the earlier run recorded.

A run given no value for an input keeps the recorded one (design v0.4 §1, PP-7). The
notebook gives every input explicitly, so its widgets are filled from the run in the output
folder with the run's own rule (:func:`.run.planned.recorded_inputs`): an untouched widget
keeps the recorded value, and an edit is a change named in "What will happen" and in the
run's ``input_changes``. Changing the output folder fills them again.
"""

import numpy as np
import pytest
from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.ranges import HistogramSettings

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import prefill_mapping, read_manifest, run_experiment  # noqa: E402
from morphobricks_preprocessing.cli import main  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from tiff_tree import write_data, write_plain  # noqa: E402

IMAGES = ["ctrl/rep1/a.tif", "ctrl/rep2/a.tif", "drug/rep1/a.tif", "drug/rep2/a.tif"]


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    for rel in IMAGES:
        write_data(root / rel, rng.integers(0, 200, (2, 16, 16), dtype=np.uint8), axes="ZYX")
    return root


def output_of(root):
    return root.parent / f"{root.name}_MorphoBricks_Preprocessing"


def ready(root, **kwargs):
    """Open the notebook, check the folder and confirm everything step 3 asks for."""
    nb = PreprocessingNotebook(str(root), **kwargs)
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        step()
    nb.check_button.click()
    nb.labels_confirmed.button.click()
    for toggle in nb.group_confirmations.values():
        if not toggle.value:  # a design the earlier run confirmed is not asked again
            toggle.button.click()
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    nb.image_type_checked.button.click()
    assert not nb.run_button.disabled
    return nb


def test_a_notebook_run_with_no_edits_keeps_what_the_cli_recorded(experiment):
    """The audit case: CLI ``--bins 64 --exclude``, then the notebook untouched."""
    args = ["run", str(experiment), "--axes", "ZYX", "--image-type", "fluorescence"]
    args += ["--bins", "64", "--exclude", "drug/rep1/a.tif"]
    assert main(args) == 1  # the design is to be confirmed first
    assert main(["design", "--output", str(output_of(experiment)), "--confirm-all"]) == 0
    assert main(args) == 0
    nb = ready(experiment)
    assert nb.exclude.value == "drug/rep1/a.tif"
    assert nb.exclude_box.selected_index == 0  # shown, not hidden in a closed panel
    assert nb.bins.value == 64 and not nb.recommended.value
    assert "drug/rep1/a.tif" not in [f.relative_path for f in nb.preflight.metadata.files]
    assert "Inputs that differ" not in nb.plan.value
    result = nb.run()
    assert result.written and result.input_changes == () and result.read == ()
    settings = read_manifest(result.manifest_path).settings
    assert settings.bin_count == 64 and settings.exclude == ("drug/rep1/a.tif",)


def test_every_recorded_input_is_prefilled(experiment, tmp_path):
    masks = tmp_path / "masks"
    for rel in IMAGES:
        write_plain(masks / "cells" / rel, np.ones((2, 16, 16), np.uint16))  # IYX
    histogram = HistogramSettings("robust", (0.5, 99.5), min_central_bins=50)
    intensity = IntensityCheckSettings(outlier_limit=2.5, saturation_limit=0.07, min_images=3)
    first = run_experiment(
        experiment,
        prefill_mapping("ZYX"),
        bin_count=128,
        exclude=["ctrl/rep2/a.tif"],
        masks_folder=masks,
        mask_axes={"cells": "ZYX"},
        image_type="fluorescence",
        histogram_settings=histogram,
        intensity_settings=intensity,
    )
    assert first.written
    nb = ready(experiment)
    assert nb.exclude.value == "ctrl/rep2/a.tif" and nb.bins.value == 128
    assert (nb.histogram_range.value, nb.robust_low.value, nb.robust_high.value) == (
        "robust",
        0.5,
        99.5,
    )
    assert nb.run_checks.settings() == intensity
    assert nb._histogram_settings() == histogram  # min_central_bins too, with no widget
    assert nb._planned_changes(output_of(experiment).resolve()) == ()
    result = nb.run()
    assert result.written and result.input_changes == () and result.read == ()
    manifest = read_manifest(result.manifest_path)
    assert manifest.masks["folder"] == read_manifest(first.manifest_path).masks["folder"]
    assert manifest.intensity_checks == intensity and manifest.settings.histogram == histogram


def test_an_edit_is_a_change_in_the_plan_and_the_run(experiment):
    run_experiment(experiment, prefill_mapping("ZYX"), bin_count=64, exclude=["drug/rep1/a.tif"])
    nb = ready(experiment)
    nb.exclude.value = ""
    nb.check_button.click()  # the exclusions are read by the check
    nb.labels_confirmed.button.click()
    for toggle in nb.group_confirmations.values():
        if not toggle.value:  # a design the earlier run confirmed is not asked again
            toggle.button.click()
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    nb.image_type_checked.button.click()
    assert nb.bins.value == 64  # a new check keeps the recorded bin count
    nb.bins.value = 32
    nb.run_checks.outlier_limit.value = 4.0
    planned = nb._planned_changes(output_of(experiment).resolve())
    assert {change.name for change in planned} == {
        "exclude",
        "bin_count",
        "intensity_settings",
        "image_type",
    }
    assert "<b>bin_count</b>: 64 → 32" in nb.plan.value
    assert "<b>exclude</b>: drug/rep1/a.tif → none" in nb.plan.value
    assert nb.run().input_changes == planned


def test_ticking_the_recommended_settings_is_a_change(experiment):
    run_experiment(experiment, prefill_mapping("ZYX"), bin_count=64)
    nb = ready(experiment)
    assert not nb.recommended.value
    nb.recommended.value = True
    planned = nb._planned_changes(output_of(experiment).resolve())
    assert [(c.name, c.recorded, c.given) for c in planned if c.name == "bin_count"] == [
        ("bin_count", "64", "256")
    ]
    assert nb.run().input_changes == planned


def test_changing_the_output_folder_prefills_again(experiment, tmp_path):
    other = tmp_path / "other_output"
    run_experiment(experiment, prefill_mapping("ZYX"), bin_count=64, exclude=["drug/rep1/a.tif"])
    run_experiment(
        experiment, prefill_mapping("ZYX"), exclude=["ctrl/rep1/a.tif"], output_folder=other
    )
    nb = PreprocessingNotebook(str(experiment))
    nb.folder_step()
    nb.run_step()
    assert nb.exclude.value == "drug/rep1/a.tif"
    nb.output.value = str(other)
    assert nb.exclude.value == "ctrl/rep1/a.tif"
    nb.output.value = str(tmp_path / "new_output")  # no earlier run: back to the defaults
    assert nb.exclude.value == ""
    nb.exclude.value = "ctrl/rep2/a.tif"  # typed by the user: kept for a folder without a run
    nb.output.value = str(tmp_path / "another_new_output")
    assert nb.exclude.value == "ctrl/rep2/a.tif"


def test_the_recorded_missing_axes_answer_is_kept(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((2, 3, 8, 8), np.uint8), axes="ZTYX")
    write_data(root / "b.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    first = run_experiment(root, prefill_mapping("ZTYX"), missing_as_singleton=True)
    assert first.written
    nb = ready(root)  # the confirmation is the recorded one: Confirm needs no new answer
    assert nb.missing_as_singleton.value
    assert [c.name for c in nb.run().input_changes] == ["image_type"]  # none recorded


def test_the_recorded_image_type_is_preselected(tmp_path):
    root = tmp_path / "exp"
    rng = np.random.default_rng(1)
    for rel in ["ctrl/rep1/a.tif", "drug/rep1/a.tif"]:
        data = rng.integers(0, 200, (2, 8, 8, 3)).astype(np.uint8)
        write_data(root / rel, data, axes="ZYXS", photometric="rgb")
    nb = ready(root)
    assert nb.confirmed_image_type == "rgb"  # the suggestion
    assert run_experiment(root, nb.confirmed_mapping, image_type="fluorescence").written
    nb = ready(root)
    assert nb.confirmed_image_type == "fluorescence"
    assert nb.run().input_changes == ()


def test_recorded_inputs_is_the_run_rule(experiment):
    from morphobricks_preprocessing.run.planned import recorded_inputs

    assert recorded_inputs(output_of(experiment)) is None  # no earlier run
    run_experiment(experiment, prefill_mapping("ZYX"), bin_count=64, exclude=["drug/rep1/a.tif"])
    recorded = recorded_inputs(output_of(experiment))
    assert recorded.exclude == ("drug/rep1/a.tif",) and recorded.bin_count == 64
    assert recorded.bins_recorded_for == "uint8" and recorded.changes == ()


def test_a_new_check_fills_only_the_unedited_bins(experiment):
    """Review of #137: a check never overwrites a bin choice the user made."""
    run_experiment(experiment, prefill_mapping("ZYX"), bin_count=64)
    nb = ready(experiment)
    nb.check_button.click()
    assert nb.bins.value == 64 and not nb.recommended.value  # unedited: filled again
    nb.recommended.value = True  # the user's choice
    nb.check_button.click()
    nb.check_button.click()
    assert nb.recommended.value and nb.bins.value == 256


def test_a_withdrawn_missing_axes_answer_is_not_given_again(tmp_path):
    """Review of #137: a check asks again rather than re-applying the recorded answer."""
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((2, 3, 8, 8), np.uint8), axes="ZTYX")
    write_data(root / "b.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    assert run_experiment(root, prefill_mapping("ZTYX"), missing_as_singleton=True).written
    nb = ready(root)
    nb.missing_as_singleton.set(False)
    nb.check_button.click()
    assert not nb.missing_as_singleton.value


def test_the_recorded_image_type_still_needs_confirming(experiment):
    run_experiment(experiment, prefill_mapping("ZYX"), image_type="fluorescence")
    nb = PreprocessingNotebook(str(experiment))
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        step()
    nb.check_button.click()
    nb.labels_confirmed.button.click()
    for toggle in nb.group_confirmations.values():
        if not toggle.value:
            toggle.button.click()
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert nb.image_type.value == "fluorescence"
    assert nb.confirmed_image_type is None and nb.run_button.disabled


def test_a_newer_run_from_the_cli_is_a_change_before_the_notebook_runs(experiment):
    """Review of #137: the widgets keep their values; the plan and run name the change."""
    args = ["run", str(experiment), "--axes", "ZYX", "--image-type", "fluorescence"]
    assert main([*args, "--bins", "64"]) == 1  # the design is to be confirmed first
    assert main(["design", "--output", str(output_of(experiment)), "--confirm-all"]) == 0
    assert main([*args, "--bins", "64"]) == 0
    nb = ready(experiment)
    assert main([*args, "--bins", "32"]) == 0  # written meanwhile
    nb._refresh_run_step()
    assert nb.bins.value == 64 and "<b>bin_count</b>: 32 → 64" in nb.plan.value
    planned = nb._planned_changes(output_of(experiment).resolve())
    assert nb.run().input_changes == planned and [c.name for c in planned] == ["bin_count"]


@pytest.mark.parametrize("saturation", [0.07, 0.123456789, 1 / 3, 0.29, 0.57])
def test_recorded_intensity_limits_come_back_exactly(experiment, saturation):
    limits = IntensityCheckSettings(outlier_limit=7 / 3, saturation_limit=saturation)
    run_experiment(experiment, prefill_mapping("ZYX"), intensity_settings=limits)
    nb = ready(experiment)
    assert nb.run_checks.settings() == limits
    assert "intensity_settings" not in {
        c.name for c in nb._planned_changes(output_of(experiment).resolve())
    }
