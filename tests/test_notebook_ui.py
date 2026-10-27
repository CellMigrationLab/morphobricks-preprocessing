import ast
import json
import os
from pathlib import Path

import numpy as np
import pytest
import tifffile

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import (  # noqa: E402
    Plane,
    notebook_ui,
    read_experiment_design,
    read_manifest,
    read_plane_histograms,
)
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.axes import AxisMapping  # noqa: E402
from morphobricks_preprocessing.manifest import file_sha256  # noqa: E402
from morphobricks_preprocessing.notebook_ui import (  # noqa: E402
    PreprocessingNotebook,
    display_range,
    distribution_png,
    issues_html,
    plane_png,
    plane_summary_html,
    preview_caption,
    role_effect,
)
from morphobricks_preprocessing.planes import DEFAULT_MEMORY_LIMIT, plane_layout  # noqa: E402
from morphobricks_preprocessing.validation import error, warning  # noqa: E402
from morphobricks_preprocessing.zarr_export import ConversionSettings  # noqa: E402
from tiff_tree import write_data, write_file  # noqa: E402

NOTEBOOK = Path(__file__).parents[1] / "notebooks" / "MorphoBricks_Preprocessing.ipynb"


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    for rel in ["ctrl/rep1/a.tif", "ctrl/rep1/b.tif", "drug/rep1/a.tif"]:
        write_data(root / rel, rng.integers(0, 200, (3, 2, 16, 16), dtype=np.uint8), axes="ZCYX")
    return root


def checked(experiment, **kwargs):
    nb = PreprocessingNotebook(str(experiment), **kwargs)
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        step()
    nb.check_button.click()
    return nb


def confirm_repeats(nb):
    """Answer every experiment-design question of step 1 with yes."""
    for toggle in nb.group_confirmations.values():
        if not toggle.value:
            toggle.button.click()


def confirmed(experiment, **kwargs):
    """Check the folder and confirm everything step 3 needs: labels, design, roles, type."""
    nb = checked(experiment, **kwargs)
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    nb.image_type_checked.button.click()
    return nb


# Step 1 -----------------------------------------------------------------------------------


def test_pages_build_without_a_folder():
    nb = PreprocessingNotebook()
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        assert step().children
    assert nb.run_button.disabled
    assert "Still to do: check the experiment folder" in nb.readiness.value
    assert "Waiting for step 1" in nb.axis_box.children[0].value
    assert nb.check_folder() is None
    assert "Enter the experiment folder first" in nb.folder_status.value


def test_welcome_shows_the_version():
    page = PreprocessingNotebook().welcome()
    root = page.children[1]
    assert "The notebook is ready" in root.children[-2].value
    assert "What next?" in root.children[-1].value
    assert "Go to Step 1" in root.children[-1].value
    assert "<li>" not in root.children[-1].value


def test_check_shows_status_tiles_labels_and_roles(experiment):
    nb = checked(experiment)
    assert nb.preflight.ok
    assert "The folder looks good" in nb.folder_status.value
    assert "images to analyse" in nb.folder_tiles.value and "uint8" in nb.folder_tiles.value
    assert ui.is_visible(nb.labels_card)
    assert "<b>ctrl</b>" in nb.labels_table.value and "mb-pill-single" in nb.labels_table.value
    assert not nb.labels_confirmed.disabled and not nb.labels_confirmed.value
    assert not ui.is_visible(nb.issues_card)
    assert [d.value for d in nb.role_dropdowns] == ["Z", "C", "Y", "X"]
    assert nb.bins.value == 256 and nb.bins.max == 256


def test_output_hint_follows_the_folders(experiment, tmp_path):
    nb = PreprocessingNotebook(str(experiment))
    assert "exp_MorphoBricks_Preprocessing" in nb.output_hint.value
    assert "(default)" in nb.output_hint.value
    nb.output.value = str(experiment / "inside")
    assert "inside the experiment folder" in nb.output_hint.value
    nb.output.value = f'"{tmp_path / "elsewhere"}"'  # quotes from a pasted path are removed
    assert str(tmp_path / "elsewhere") in nb.output_hint.value
    assert "(default)" not in nb.output_hint.value


def test_warnings_are_shown_with_paths_and_actions(experiment):
    write_file(experiment / "ctrl/notes.txt")
    nb = checked(experiment)
    assert "Ready, with 1 warning" in nb.folder_status.value
    assert ui.is_visible(nb.issues_card)
    assert "ctrl/notes.txt" in nb.folder_issues.value
    assert "What to do:" in nb.folder_issues.value
    assert nb.issue_details.selected_index is None
    assert "mb-warn" in nb.issue_counts.value and "1 warning" in nb.issue_counts.value
    nb.issue_details.selected_index = 0
    assert nb.exclude_button in nb.issue_details.children[0].children
    nb.check_folder()
    assert nb.issue_details.selected_index is None


def test_errors_and_warnings_remain_counted_when_messages_are_collapsed(experiment):
    write_file(experiment / "ctrl/notes.txt")
    write_file(experiment / "ctrl/rep1/bad.tif", b"garbage")
    nb = checked(experiment)
    assert nb.issue_details.selected_index is None
    assert "mb-error'>1 error" in nb.issue_counts.value
    assert "mb-warn'>1 warning" in nb.issue_counts.value
    assert nb.folder_next.value == ""
    nb.issue_details.selected_index = 0
    nb.exclude_button.click()
    assert nb.preflight.ok
    assert "mb-error" not in nb.issue_counts.value
    assert nb.issue_details.selected_index is None


def test_dataset_consistency_panel_shows_the_balance(experiment):
    nb = checked(experiment)
    assert ui.is_visible(nb.consistency_card)
    table = nb.balance_table.value
    assert "<th>ctrl</th>" in table and "<th>drug</th>" in table
    assert "<b>2 images</b>" in table and "<b>1 image</b>" in table
    assert "no technical-repeat folders" in table
    assert "Median: 1.5 images" in table and "mb-cell-warn" not in table
    assert "consistent with each other" in nb.consistency_counts.value
    assert not ui.is_visible(nb.consistency_details)


def test_consistency_warnings_are_in_their_own_panel(experiment):
    rng = np.random.default_rng(1)
    for name in ("b", "c", "d", "e"):
        write_data(
            experiment / f"drug/rep1/{name}.tif",
            rng.integers(0, 9, (3, 2, 16, 16), dtype=np.uint8),
            "ZCYX",
        )
    write_data(
        experiment / "drug/rep2/a.tif", rng.integers(0, 9, (3, 2, 20, 16), dtype=np.uint8), "ZCYX"
    )
    write_file(experiment / "ctrl/notes.txt")
    nb = checked(experiment)
    # drug/rep1 has 5 images against a median of 2, and rep2 is missing from ctrl.
    assert "mb-cell-warn'><b>5 images" in nb.balance_table.value
    assert "mb-cell-warn'>not found" in nb.balance_table.value
    assert "mb-warn'>3 warnings" in nb.consistency_counts.value
    assert ui.is_visible(nb.consistency_details)
    assert nb.consistency_details.selected_index is None
    for code in ("unbalanced images", "repeat missing conditions", "different sizes"):
        assert code in nb.consistency_issues.value
        assert code not in nb.folder_issues.value
    # The messages card keeps the other problems, and the tiles count everything.
    assert "non tiff file" in nb.folder_issues.value
    assert "mb-warn'>1 warning" in nb.issue_counts.value
    assert "0 / 4" in nb.folder_tiles.value
    nb.folder.value = str(experiment / "ctrl")
    assert not ui.is_visible(nb.consistency_card)


def test_different_channel_counts_are_an_error_in_the_consistency_panel(experiment):
    rng = np.random.default_rng(1)
    three = rng.integers(0, 9, (3, 3, 16, 16), dtype=np.uint8)
    write_data(experiment / "drug/rep1/b.tif", three, "ZCYX")
    nb = checked(experiment)
    assert "mb-error'>1 error" in nb.consistency_counts.value
    assert "different channel counts" in nb.consistency_issues.value
    assert "2 channels (3 images), 3 channels (1 image)" in nb.consistency_issues.value
    assert "drug/rep1/b.tif" in nb.consistency_issues.value
    assert "different channel counts" not in nb.folder_issues.value
    assert "Action required" in nb.folder_status.value
    # Confirming the axis roles does not lift it: the run stays locked.
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert nb.run_button.disabled and nb.run() is None
    assert "fix any errors" in nb.readiness.value


def test_next_steps_follow_confirmation_and_folder_changes(experiment):
    nb = checked(experiment)
    assert "Check the labels in Step 1" in nb.folder_next.value
    assert "Go to Step 2" not in nb.folder_next.value
    nb.labels_confirmed.button.click()
    assert "Confirm the biological repeats in Step 1" in nb.folder_next.value
    confirm_repeats(nb)
    assert "Go to Step 2" in nb.folder_next.value
    assert "Continue" not in nb.labels_confirmed.message.value
    nb.labels_confirmed.button.click()
    assert "Go to Step 2" not in nb.folder_next.value
    nb.labels_confirmed.button.click()
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert "Confirm the image type in Step 2" in nb.axis_next.value
    nb.image_type_checked.button.click()
    assert "Go to Step 3" in nb.axis_next.value
    assert "Continue" not in nb.confirmed_status.value
    nb.labels_confirmed.button.click()
    assert "Go to Step 1" in nb.axis_next.value
    nb.labels_confirmed.button.click()
    assert "Go to Step 3" in nb.axis_next.value
    nb.confirm_button.click()
    assert "Go to Step 3" not in nb.axis_next.value
    nb.folder.value += "_other"
    assert nb.folder_next.value == nb.axis_next.value == nb.run_next.value == ""


def test_unreadable_image_can_be_excluded(experiment):
    write_file(experiment / "ctrl/rep1/bad.tif", b"garbage")
    nb = checked(experiment)
    assert "Action required" in nb.folder_status.value
    assert "Processing is blocked" in nb.folder_issues.value
    assert ui.is_visible(nb.exclude_button)
    nb.exclude_button.click()
    assert nb.exclude.value == "ctrl/rep1/bad.tif"
    assert nb.preflight.ok and not ui.is_visible(nb.exclude_button)
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    nb.image_type_checked.button.click()
    assert nb.run().excluded == ("ctrl/rep1/bad.tif",)


# Experiment design (follow-up P3) ------------------------------------------------------


def design_tree(root, folders):
    rng = np.random.default_rng(2)
    for folder in folders:
        data = rng.integers(0, 200, (3, 2, 16, 16), dtype=np.uint8)
        write_data(root / folder / "a.tif", data, axes="ZCYX")
    return root


def labelled(root, tmp_path):
    nb = checked(root, output_folder=str(tmp_path / "out"))
    nb.labels_confirmed.button.click()
    return nb


def test_design_is_asked_after_the_labels_and_saved(tmp_path):
    root = design_tree(tmp_path / "exp", ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2b"])
    nb = checked(root, output_folder=str(tmp_path / "out"))
    assert not ui.is_visible(nb.design_card)
    nb.labels_confirmed.button.click()
    assert ui.is_visible(nb.design_card) and ui.is_visible(nb.design_editor)
    assert ui.is_visible(nb.pairing_box)
    assert sorted(nb.group_confirmations) == ["rep1", "rep2", "rep2b"]
    toggle = nb.group_confirmations["rep1"]
    assert "Repeat group rep1: were these images prepared and acquired" in toggle.prompt.value
    assert toggle.button.description.strip() == "Yes, they are comparable"
    assert "Not found in drug" in nb.group_confirmations["rep2"].prompt.value
    assert "Still to confirm: 3 repeat groups" in nb.design_status.value
    assert "Confirm the biological repeats in Step 1" in nb.folder_next.value
    assert "Still to do: confirm the biological repeats" in nb.readiness.value
    # Saved straight away, unconfirmed, like the command-line template.
    path = tmp_path / "out" / "experiment_design.csv"
    assert not read_experiment_design(path).confirmed


def test_pairing_a_repeat_updates_the_groups_and_the_balance(tmp_path):
    root = design_tree(tmp_path / "exp", ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2b"])
    nb = labelled(root, tmp_path)
    assert "repeat missing conditions" in nb.consistency_issues.value
    nb.group_confirmations["rep1"].button.click()
    (dropdown,) = [
        box.children[2]
        for box in nb.pairing_grid.children[1:]
        if "rep2b" in box.children[1].value and "drug" in box.children[0].value
    ]
    dropdown.value = "rep2"
    assert sorted(nb.group_confirmations) == ["rep1", "rep2"]
    assert nb.group_confirmations["rep1"].value and not nb.group_confirmations["rep2"].value
    assert "repeat missing conditions" not in nb.consistency_issues.value
    assert "<th>Biological repeat</th>" in nb.balance_table.value
    assert "rep2b" not in nb.balance_table.value.split("<tbody>")[1].split("</td>")[0]
    saved = read_experiment_design(tmp_path / "out" / "experiment_design.csv")
    assert saved.repeat_groups()[("drug", "rep2b")] == "rep2"
    nb.group_confirmations["rep2"].button.click()
    assert "The biological repeats are confirmed on" in nb.design_status.value
    assert "Go to Step 2" in nb.folder_next.value
    assert read_experiment_design(tmp_path / "out" / "experiment_design.csv").confirmed


def test_two_folders_of_a_condition_cannot_be_confirmed(tmp_path):
    root = design_tree(tmp_path / "exp", ["ctrl/rep1", "ctrl/rep2", "drug/rep1"])
    nb = labelled(root, tmp_path)
    (dropdown,) = [
        box.children[2] for box in nb.pairing_grid.children[1:] if "rep2" in box.children[1].value
    ]
    dropdown.value = "rep1"
    toggle = nb.group_confirmations["rep1"]
    assert toggle.disabled and "more than one folder of ctrl" in toggle.prompt.value
    assert "holds more than one folder of a condition" in nb.design_status.value
    assert nb.run_button.disabled


def test_without_repeat_folders_one_question_is_asked(tmp_path):
    root = design_tree(tmp_path / "exp", ["ctrl", "drug"])
    nb = labelled(root, tmp_path)
    assert not ui.is_visible(nb.pairing_box)
    (toggle,) = nb.group_confirmations.values()
    assert "All images of every condition come from one biological repeat" in toggle.prompt.value
    toggle.button.click()
    assert nb.design.confirmed


def test_controls_are_saved(tmp_path):
    root = design_tree(tmp_path / "exp", ["ctrl/rep1", "drug/rep1"])
    nb = labelled(root, tmp_path)
    nb.control_checks["ctrl"].value = True
    assert "Controls: ctrl" not in nb.design_status.value  # shown once confirmed
    confirm_repeats(nb)
    assert "Controls: ctrl" in nb.design_status.value
    assert read_experiment_design(tmp_path / "out" / "experiment_design.csv").controls == ("ctrl",)


def test_a_confirmed_design_is_not_asked_again(tmp_path):
    root = design_tree(tmp_path / "exp", ["ctrl/rep1", "drug/rep1"])
    nb = labelled(root, tmp_path)
    nb.control_checks["drug"].value = True
    confirm_repeats(nb)
    again = labelled(root, tmp_path)
    assert again.design.confirmed and again.design.controls == ("drug",)
    assert not ui.is_visible(again.design_editor) and ui.is_visible(again.design_change)
    assert "The biological repeats are confirmed on" in again.design_status.value
    assert "Go to Step 2" in again.folder_next.value
    again.design_change.click()
    assert ui.is_visible(again.design_editor) and not ui.is_visible(again.design_change)
    assert again.group_confirmations["rep1"].value


def test_a_new_repeat_asks_only_for_its_group(tmp_path):
    root = design_tree(tmp_path / "exp", ["ctrl/rep1", "drug/rep1"])
    confirm_repeats(labelled(root, tmp_path))
    design_tree(root, ["ctrl/rep2", "drug/rep2"])
    nb = labelled(root, tmp_path)
    assert ui.is_visible(nb.design_editor)
    assert "The folders changed since the design was saved" in nb.design_status.value
    assert nb.group_confirmations["rep1"].value
    assert not nb.group_confirmations["rep2"].value
    assert "Still to confirm: 1 repeat group (rep2)" in nb.design_status.value


def test_an_unreadable_design_is_asked_again(tmp_path):
    root = design_tree(tmp_path / "exp", ["ctrl/rep1", "drug/rep1"])
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "experiment_design.csv").write_bytes(b"not a design")
    nb = labelled(root, tmp_path)
    assert "could not be read" in nb.design_status.value
    confirm_repeats(nb)
    assert read_experiment_design(tmp_path / "out" / "experiment_design.csv").confirmed


def test_undoing_the_labels_closes_the_design(tmp_path):
    root = design_tree(tmp_path / "exp", ["ctrl/rep1", "drug/rep1"])
    nb = labelled(root, tmp_path)
    confirm_repeats(nb)
    nb.labels_confirmed.button.click()
    assert nb.design is None and not ui.is_visible(nb.design_card)
    assert "Still to do: confirm the labels" in nb.readiness.value


def test_the_run_writes_the_design(tmp_path):
    root = design_tree(tmp_path / "exp", ["ctrl/rep1", "drug/rep1"])
    nb = confirmed(root, output_folder=str(tmp_path / "out"))
    nb.run_button.click()
    assert nb.result.written
    path = tmp_path / "out" / "experiment_design.csv"
    assert "Experiment design: " in nb.run_status.value
    manifest = read_manifest(tmp_path / "out" / "run_manifest.json")
    assert manifest.experiment_design_sha256 == file_sha256(path)


def test_changing_the_folder_resets_later_steps(experiment):
    nb = confirmed(experiment)
    assert not nb.run_button.disabled
    nb.folder.value = str(experiment) + "_other"
    assert nb.preflight is None and nb.confirmed_mapping is None
    assert "The folder changed" in nb.folder_status.value
    assert nb.run_button.disabled
    assert not ui.is_visible(nb.labels_card)
    nb.folder.value = str(experiment)  # back to the checked folder: nothing more happens
    assert nb.preflight is None


def test_browse_fills_the_path_or_explains(experiment, monkeypatch):
    nb = PreprocessingNotebook()
    monkeypatch.setattr(ui, "ask_directory", lambda initial: str(experiment))
    nb.folder_browse.click()
    assert nb.folder.value == str(experiment)
    monkeypatch.setattr(ui, "ask_directory", lambda initial: "")  # cancelled
    nb.output_browse.click()
    assert nb.output.value == ""
    monkeypatch.setattr(ui, "ask_directory", lambda initial: None)  # no dialog possible
    nb.folder_browse.click()
    assert "could not be opened" in nb.folder_status.value
    assert nb.folder.value == str(experiment)


def test_check_button_is_restored_after_checking(experiment):
    nb = checked(experiment)
    assert not nb.check_button.disabled
    assert nb.check_button.description == " Check folder"


# Step 2 -----------------------------------------------------------------------------------


def test_role_notes_and_plane_summary(experiment):
    nb = checked(experiment)
    notes = [n.value for n in nb.role_notes]
    assert "3 depth slices, numbered z = 0 to 2" in notes[0]
    assert "2 channels, numbered c = 0 to 1" in notes[1]
    assert "16 pixels in height" in notes[2]
    assert "split into 6 plane(s) of 16 × 16 pixels" in nb.plane_summary.value
    assert "These roles are valid for every image" in nb.axis_status.value


def test_confirm_needs_valid_roles_and_a_checked_preview(experiment):
    nb = checked(experiment)
    assert nb.confirm_button.disabled
    assert nb.preview_checked.disabled  # the preview must be shown first
    nb.preview_button.click()
    assert not nb.preview_checked.disabled
    nb.preview_checked.button.click()
    assert not nb.confirm_button.disabled
    nb.role_dropdowns[1].value = "-"
    assert "needs a role" in nb.role_notes[1].value
    assert "No role is chosen for the axis C (channel)" in nb.axis_status.value
    assert nb.confirm_button.disabled
    nb.role_dropdowns[1].value = "T"
    assert nb.confirm_button.disabled  # new roles: the preview must be checked again
    nb.preview_checked.button.click()
    assert not nb.confirm_button.disabled
    assert nb.mapping.roles == ("Z", "T", "Y", "X")


def test_confirmations_are_buttons_that_can_be_undone(experiment):
    nb = checked(experiment)
    toggle = nb.labels_confirmed
    assert "Do the labels in the table match your experiment?" in toggle.prompt.value
    assert toggle.button.description.strip() == "Yes, the labels are correct"
    assert toggle.button.button_style == "success"
    toggle.button.click()
    assert toggle.value and "Labels confirmed" in toggle.message.value
    assert toggle.button.description.strip() == "Undo: the labels are not right"
    assert "mb-confirmed" in toggle.widget._dom_classes
    assert "Still to do: confirm the biological repeats" in nb.readiness.value
    confirm_repeats(nb)
    assert "Still to do: confirm the axis roles" in nb.readiness.value
    toggle.button.click()
    assert not toggle.value and toggle.message.value == ""
    assert "Still to do: confirm the labels" in nb.readiness.value


def test_changing_a_role_asks_for_the_preview_again(experiment):
    nb = checked(experiment)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    assert not nb.confirm_button.disabled
    nb.role_dropdowns[0].value = "T"
    assert not nb.preview_checked.value
    assert nb.confirm_button.disabled


def test_confirmation_locks_and_unlocks(experiment):
    nb = confirmed(experiment)
    assert nb.confirmed_mapping == nb.mapping
    assert all(d.disabled for d in nb.role_dropdowns)
    assert nb.confirm_button.description == " Change axis roles"
    assert "Axis roles confirmed" in nb.confirmed_status.value
    assert "Z (depth) → Z" in nb.confirmed_status.value
    nb.confirm_button.click()
    assert nb.confirmed_mapping is None
    assert not any(d.disabled for d in nb.role_dropdowns)
    assert nb.confirmed_status.value == ""


def test_preview_sliders_browse_planes(experiment):
    nb = checked(experiment)
    assert not any(ui.is_visible(s) for s in nb.preview_sliders.values())
    nb.preview_button.click()
    assert bytes(nb.preview_image.value).startswith(b"\x89PNG")
    assert "ctrl/rep1/a.tif" in nb.preview_caption.value
    assert "z = 1, c = 0" in nb.preview_caption.value  # the middle slice first
    z, c, t = (nb.preview_sliders[r] for r in "ZCT")
    assert (z.max, c.max) == (2, 1) and ui.is_visible(z) and ui.is_visible(c)
    assert not ui.is_visible(t)
    z.value = 2
    c.value = 1
    assert "z = 2, c = 1" in nb.preview_caption.value
    nb.preview_file.value = "drug/rep1/a.tif"
    assert "drug/rep1/a.tif" in nb.preview_caption.value


def test_preview_follows_role_changes(experiment):
    nb = checked(experiment)
    nb.preview_button.click()
    nb.role_dropdowns[0].value = "T"
    assert "c = 0, t = 0" in nb.preview_caption.value
    assert ui.is_visible(nb.preview_sliders["T"]) and not ui.is_visible(nb.preview_sliders["Z"])


def test_preview_reports_planes_it_cannot_read(experiment):
    nb = checked(experiment)
    # Height on the first axis: every plane would span several TIFF pages.
    nb.role_dropdowns[2].value = "Z"
    nb.role_dropdowns[0].value = "Y"
    nb.preview_button.click()
    assert "cannot be shown" in nb.preview_caption.value


def test_preview_and_roles_with_an_ome_zarr_image(tmp_path):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((3, 2, 8, 8), np.uint8), axes="ZCYX", imagej=True)
    data = np.arange(2 * 3 * 8 * 8, dtype=np.uint8).reshape(2, 3, 8, 8)
    write_ngff(root / "b.ome.zarr", data, "czyx")
    nb = checked(root)
    assert "OME-Zarr images" in nb.folder_tiles.value
    # Same roles in another order: nothing is missing.
    assert not ui.is_visible(nb.missing_card)
    labels = [label for label, _ in nb.preview_file.options]
    assert any("b.ome.zarr" in label and "OME-Zarr, CZYX" in label for label in labels)
    nb.preview_file.value = "b.ome.zarr"
    nb.preview_button.click()
    assert "b.ome.zarr" in nb.preview_caption.value and "z = 1, c = 0" in nb.preview_caption.value


def test_missing_axes_card(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((2, 3, 8, 8), np.uint8), axes="ZTYX")
    write_data(root / "b.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    nb = checked(root)
    assert ui.is_visible(nb.missing_card)
    assert "1 image with axes <b>ZYX</b> lack T (time)" in nb.missing_note.value
    nb.preview_button.click()
    nb.preview_checked.button.click()
    assert nb.confirm_button.disabled
    nb.missing_as_singleton.button.click()
    assert not nb.confirm_button.disabled


@pytest.mark.parametrize("blocking_file", [False, True])
def test_confirmed_missing_axes_allow_running_unless_other_errors_remain(tmp_path, blocking_file):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((2, 3, 8, 8), np.uint8), axes="ZTYX")
    write_data(root / "b.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    if blocking_file:
        write_file(root / "bad.tif", b"garbage")
    nb = checked(root)
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    assert nb.run_button.disabled
    nb.missing_as_singleton.button.click()
    nb.confirm_button.click()
    nb.image_type_checked.button.click()
    assert nb.confirmed_mapping is not None
    assert nb.run_button.disabled == blocking_file
    if blocking_file:
        assert nb.run() is None
        assert "could not be opened" in nb.folder_issues.value
    else:
        result = nb.run()
        assert result.written
        rows = read_plane_histograms(result.csv_path)
        assert [(r.z, r.t) for r in rows if r.source_relative_path == "b.tif"] == [(0, 0), (1, 0)]


def test_undoing_missing_axes_confirmation_blocks_running_again(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((2, 3, 8, 8), np.uint8), axes="ZTYX")
    write_data(root / "b.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    nb = checked(root)
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)
    nb.missing_as_singleton.button.click()
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    nb.image_type_checked.button.click()
    assert not nb.run_button.disabled
    nb.confirm_button.click()  # unlock the roles before undoing singleton confirmation
    nb.missing_as_singleton.button.click()
    assert nb.run_button.disabled and nb.confirm_button.disabled
    assert nb.run() is None


# Image type (follow-up P4) ----------------------------------------------------------------


def roles_confirmed(root, **kwargs):
    """Check the folder and confirm the labels, the design and the axis roles, not the type."""
    nb = checked(root, **kwargs)
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    return nb


def rgb_tree(root, dtype=np.uint8):
    rng = np.random.default_rng(0)
    for rel in ["ctrl/rep1/a.tif", "drug/rep1/a.tif"]:
        data = rng.integers(0, 200, (2, 8, 8, 3)).astype(dtype)
        write_data(root / rel, data, axes="ZYXS", photometric="rgb")
    return root


def test_image_type_is_asked_after_the_axis_roles(experiment):
    nb = checked(experiment)
    assert not ui.is_visible(nb.image_type_card)
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert ui.is_visible(nb.image_type_card)
    assert nb.image_type.value == "fluorescence" and nb.confirmed_image_type is None
    assert "Suggested: <b>Fluorescence</b>" in nb.image_type_note.value
    # Two channels cannot be RGB: the choice is fixed and the reason shown.
    assert nb.image_type.disabled
    assert "RGB cannot be chosen" in nb.image_type_note.value
    assert "2 channels of type uint8" in nb.image_type_note.value
    assert nb.run_button.disabled and "Confirm the image type in Step 2" in nb.axis_next.value
    nb.image_type_checked.button.click()
    assert nb.confirmed_image_type == "fluorescence"
    assert "Image type confirmed: Fluorescence." in nb.image_type_checked.message.value
    assert not nb.run_button.disabled and "Go to Step 3" in nb.axis_next.value
    assert "Image type: <b>Fluorescence</b>" in nb.plan.value
    assert read_manifest(nb.run().manifest_path).image_type == "fluorescence"


def test_rgb_is_suggested_but_only_used_once_confirmed(tmp_path):
    nb = roles_confirmed(rgb_tree(tmp_path / "exp"))
    assert nb.image_type.value == "rgb" and not nb.image_type.disabled
    assert "Suggested: <b>RGB</b>" in nb.image_type_note.value
    assert nb.confirmed_image_type is None and nb.run_button.disabled
    assert nb.run() is None
    # Choosing the other type is possible, with a reminder of the suggestion.
    nb.image_type.value = "fluorescence"
    assert "mb-warn" in nb.image_type_note.value and "not the suggested type" in (
        nb.image_type_note.value
    )
    nb.image_type.value = "rgb"
    nb.image_type_checked.button.click()
    assert nb.confirmed_image_type == "rgb" and nb.image_type.disabled
    manifest = read_manifest(nb.run().manifest_path)
    assert manifest.image_type == "rgb" and manifest.channels_from_rgb_samples
    nb.image_type_checked.button.click()  # undo
    assert nb.confirmed_image_type is None and not nb.image_type.disabled
    assert nb.run_button.disabled


def test_float_rgb_images_cannot_be_rgb(tmp_path):
    nb = roles_confirmed(rgb_tree(tmp_path / "exp", np.float32))
    assert nb.image_type.value == "fluorescence" and nb.image_type.disabled
    assert "stored as RGB colour, but cannot be of type RGB" in nb.image_type_note.value
    assert "3 channels of type float32" in nb.image_type_note.value


def test_changing_the_axis_roles_asks_for_the_image_type_again(experiment):
    nb = confirmed(experiment)
    assert nb.confirmed_image_type == "fluorescence"
    nb.confirm_button.click()  # change the axis roles
    assert nb.confirmed_image_type is None and not ui.is_visible(nb.image_type_card)
    assert not nb.image_type_checked.value and nb.run_button.disabled
    nb.confirm_button.click()
    assert ui.is_visible(nb.image_type_card) and nb.confirmed_image_type is None
    nb.image_type_checked.button.click()
    nb.folder.value = str(experiment) + "_other"
    assert nb.confirmed_image_type is None and not ui.is_visible(nb.image_type_card)


def test_conflicting_axes_explain_why_no_roles_are_offered(tmp_path):
    write_data(tmp_path / "exp/a.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    write_data(tmp_path / "exp/b.tif", np.zeros((2, 8, 8), np.uint8), axes="CYX")
    nb = checked(tmp_path / "exp")
    (message,) = nb.axis_box.children
    assert "No axis roles can be suggested" in message.value
    assert "check the experiment folder and fix any errors" in nb.readiness.value


# Steps 1 and 2 embedded in another brick ---------------------------------------------------


def page_parts(page):
    """Return the title, the purpose and every widget below them in a step's page."""
    root = page.children[1]
    found = []

    def walk(widget):
        found.append(widget)
        for child in getattr(widget, "children", ()):
            walk(child)

    for child in root.children[2:]:
        walk(child)
    return root.children[0].value, root.children[1].value, found


def intros(widgets_found):
    return [w.value for w in widgets_found if "mb-intro" in str(getattr(w, "value", ""))]


def test_steps_keep_their_words_by_default():
    nb = PreprocessingNotebook()
    title, purpose, found = page_parts(nb.folder_step())
    assert "Step 1 · Check the experiment folder" in title
    assert "Choose the folder with your images." in purpose
    assert len(intros(found)) == 1 and "What happens here" in intros(found)[0]
    assert nb.output_card in found and nb.folder_next in found
    assert ui.is_visible(nb.folder_next)
    title, purpose, found = page_parts(nb.axis_step())
    assert "Step 2 · Review the axis roles" in title
    assert "Tell the brick which image dimension" in purpose
    assert len(intros(found)) == 1 and "flat 2D planes" in intros(found)[0]
    assert nb.axis_box in found and ui.is_visible(nb.axis_next)


def test_folder_step_with_its_own_words_and_no_output_card(experiment):
    nb = PreprocessingNotebook(str(experiment))
    title, purpose, found = page_parts(
        nb.folder_step(
            "Step 1 · Your data",
            "Choose the folder with your <b>images</b>.",
            intro="<b>Here</b> your folders are read.",
            show_output=False,
            show_next=False,
        )
    )
    assert "Step 1 · Your data" in title and "Step 1 · Check" not in title
    assert purpose == "<div class='mb-subtitle'>Choose the folder with your <b>images</b>.</div>"
    assert intros(found) == ["<div class='mb-intro'><b>Here</b> your folders are read.</div>"]
    assert not ui.is_visible(nb.output_card)
    assert nb.folder_card in found and nb.check_button in found and nb.labels_card in found
    # The check works without the card, with the default output folder.
    nb.check_button.click()
    assert nb.preflight is not None and nb.preflight.ok
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)  # embedded steps keep the experiment-design gating (P3)
    assert "Go to Step 2" in nb.folder_next.value and not ui.is_visible(nb.folder_next)


def test_hidden_output_card_comes_back_when_its_folder_is_inside_the_experiment(experiment):
    nb = PreprocessingNotebook(str(experiment), output_folder=str(experiment / "out"))
    page = nb.folder_step(show_output=False)
    assert nb.output_card in page_parts(page)[2] and not ui.is_visible(nb.output_card)
    pre = nb.check_folder()
    assert "output_inside_experiment" in [i.code for i in pre.issues]
    assert ui.is_visible(nb.output_card)  # the box to fix it is shown
    nb.output.value = ""
    assert nb.check_folder().ok
    assert not ui.is_visible(nb.output_card)
    nb.folder_step()  # the default page shows it again
    assert ui.is_visible(nb.output_card)


def test_steps_without_an_intro_box():
    nb = PreprocessingNotebook()
    assert intros(page_parts(nb.folder_step(intro=""))[2]) == []
    assert intros(page_parts(nb.axis_step(intro=""))[2]) == []


def test_axis_step_with_its_own_words(experiment):
    nb = PreprocessingNotebook(str(experiment))
    nb.folder_step()
    title, purpose, found = page_parts(
        nb.axis_step("Step 2 · Image dimensions", "Say which dimension is which.", show_next=False)
    )
    assert "Step 2 · Image dimensions" in title and purpose.endswith(
        "Say which dimension is which.</div>"
    )
    assert len(intros(found)) == 1  # the default intro is kept
    nb.check_button.click()
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)  # embedded steps keep the experiment-design gating (P3)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert nb.confirmed_mapping is not None
    # The embedded step asks for the image type too, with the same gating (P4).
    assert ui.is_visible(nb.image_type_card) and nb.run_button.disabled
    nb.image_type_checked.button.click()
    assert nb.confirmed_image_type == "fluorescence" and not nb.run_button.disabled
    assert "compute the histograms" in nb.axis_next.value and not ui.is_visible(nb.axis_next)
    # Building the default page again shows the box again.
    nb.axis_step()
    assert ui.is_visible(nb.axis_next)


# Step 3 -----------------------------------------------------------------------------------


def test_readiness_checklist(experiment):
    nb = checked(experiment)
    assert "Almost ready" in nb.readiness.value
    assert "Still to do: confirm the labels" in nb.readiness.value
    assert "✅ Check the experiment folder" in nb.readiness.value
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)
    assert "Still to do: confirm the axis roles" in nb.readiness.value
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert "Still to do: confirm the image type" in nb.readiness.value and nb.run_button.disabled
    nb.image_type_checked.button.click()
    assert "Ready to run" in nb.readiness.value and not nb.run_button.disabled


def test_recommended_settings_hide_and_reset_the_advanced_ones(experiment):
    nb = checked(experiment)
    assert not ui.is_visible(nb.advanced)
    assert "256 bins per plane" in nb.recommended_hint.value
    nb.recommended.value = False
    assert ui.is_visible(nb.advanced)
    assert "1 to 256 bins" in nb.bins_hint.value and "exact histogram" in nb.bins_hint.value
    nb.bins.value = 16
    nb.force_rebuild.value = True
    assert "Every image will be read" in nb.plan.value
    nb.recommended.value = True
    assert nb.bins.value == 256 and not nb.force_rebuild.value


def test_plan_describes_the_run(experiment):
    nb = confirmed(experiment)
    assert "<b>3 images</b> will be analysed" in nb.plan.value
    assert "exp_MorphoBricks_Preprocessing" in nb.plan.value
    assert "No earlier run was found" in nb.plan.value
    nb.run()
    nb._refresh_run_step()
    assert "An earlier run was found (3 images" in nb.plan.value


def large_planes_experiment(tmp_path, **big_kwargs):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    big = rng.integers(0, 4000, (2, 64, 64), dtype=np.uint16)
    write_data(root / "ctrl/rep1/big.tif", big, axes="ZYX", **big_kwargs)
    small = rng.integers(0, 4000, (2, 8, 8), dtype=np.uint16)
    write_data(root / "ctrl/rep1/small.tif", small, axes="ZYX")
    return root


def set_memory_limit(nb, limit):
    nb.recommended.value = False
    nb.memory_gib.min = 0  # the widget stops at 0.01 GiB; the test images are tiny
    nb.memory_gib.value = limit / 2**30
    assert nb._memory_limit() == limit


def small_estimate(root):
    mapping = AxisMapping("ZYX", ("Z", "Y", "X"))
    return plane_layout(root / "ctrl/rep1/small.tif", mapping, bin_count=65_536).memory_estimate


def test_plan_names_images_read_in_blocks(tmp_path, monkeypatch):
    root = large_planes_experiment(tmp_path)
    nb = checked(root)
    set_memory_limit(nb, small_estimate(root))
    # Before the axis roles are confirmed the planes are not known yet.
    assert "read in blocks" not in nb.plan.value

    def fail(*args, **kwargs):
        raise AssertionError("pixel data was read")

    nb.labels_confirmed.button.click()
    confirm_repeats(nb)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    for cls in (tifffile.TiffFile, tifffile.TiffPage, tifffile.TiffFrame, tifffile.TiffPageSeries):
        monkeypatch.setattr(cls, "asarray", fail)
    nb.confirm_button.click()
    nb.image_type_checked.button.click()
    plan = nb.plan.value
    assert "<b>1 image</b> has planes too large to read whole" in plan
    assert "will be read in blocks" in plan and "exactly the same" in plan
    assert "<b>ctrl/rep1/big.tif</b>" in plan and "small.tif" not in plan
    assert "cannot be read within the memory limit" not in plan
    assert plan.index("read in blocks") < plan.index("No earlier run was found")
    assert not nb.run_button.disabled
    # Raising the limit reads the planes whole again.
    set_memory_limit(nb, DEFAULT_MEMORY_LIMIT)
    assert "read in blocks" not in nb.plan.value


def test_plan_warns_about_images_that_cannot_be_read_within_the_limit(tmp_path):
    # Compressed in one strip per plane: cannot be decoded in parts.
    root = large_planes_experiment(tmp_path, compression="zlib", rowsperstrip=64)
    nb = confirmed(root)
    assert "cannot be read" not in nb.plan.value
    set_memory_limit(nb, small_estimate(root))
    plan = nb.plan.value
    assert "mb-error" in plan
    assert "1 image cannot be read within the memory limit; the run will stop." in plan
    assert "<b>ctrl/rep1/big.tif</b>" in plan and "exclude these images in step 1" in plan
    assert "read in blocks" not in plan
    # The run itself reports the error, so Run stays enabled.
    assert not nb.run_button.disabled
    nb.run()
    assert "plane over memory limit" in nb.run_issues.value


def test_plan_layouts_are_cached_until_their_inputs_change(tmp_path, monkeypatch):
    root = large_planes_experiment(tmp_path)
    calls = []
    real = notebook_ui.plane_layout

    def spy(path, *args, **kwargs):
        calls.append(Path(path).name)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(notebook_ui, "plane_layout", spy)
    nb = checked(root)
    nb.recommended.value = False
    assert calls == []  # nothing is opened before the axis roles are confirmed
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert sorted(calls) == ["big.tif", "small.tif"]

    def recomputed():
        count = len(calls)
        calls.clear()
        return count

    calls.clear()
    nb.force_rebuild.value = True
    nb._refresh_run_step()
    assert recomputed() == 0
    nb.memory_gib.value = 2.0
    assert recomputed() == 2
    nb.bins.value = 1_000
    assert recomputed() == 2
    nb.confirm_button.click()  # undo: the roles may change
    assert recomputed() == 0
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert recomputed() == 0  # the same roles and settings as before
    nb.check_button.click()
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert recomputed() == 2  # a new check of the folder


def test_conversion_card_reads_directly_by_default(experiment):
    nb = checked(experiment)
    assert nb.conversion.value == "read"
    assert not ui.is_visible(nb.conversion_settings) and nb.space_warning.value == ""
    help_text = nb.conversion_help.value
    assert help_text.count("<li>") >= 4 and "nothing extra is saved" in help_text
    assert "OME-Zarr images are always read directly" in help_text
    assert "<code>" not in help_text
    page = nb.run_step()
    root = page.children[1]
    assert nb.conversion_card in root.children
    assert root.children.index(nb.conversion_card) == root.children.index(nb.settings_card) + 1


def test_conversion_card_survives_the_recommended_settings(experiment):
    nb = checked(experiment)
    if nb.conversion.disabled:
        pytest.skip("zarr is not installed")
    nb.conversion.value = "convert"
    nb.recommended.value = False
    nb.recommended.value = True
    assert nb.conversion.value == "convert"  # a choice about the data, not a tuning value


def test_conversion_shows_settings_and_the_disk_space_warning(experiment):
    nb = checked(experiment)
    if nb.conversion.disabled:
        pytest.skip("zarr is not installed")
    nb.conversion.value = "convert"
    assert ui.is_visible(nb.conversion_settings)
    warning = nb.space_warning.value
    assert "Converting duplicates the space your images take" in warning
    assert "All 3 TIFF images will be stored again in the converted format" in warning
    assert "all the space they occupy is duplicated" in warning
    assert "Free space on the drive of the output folder" in warning
    nb.compression.value = "none"
    assert not ui.is_visible(nb.compression_level_box)
    nb.pyramid.value = "fixed"
    assert ui.is_visible(nb.pyramid_count_box)
    nb.pyramid_count.value = 3
    nb.chunk_size.value = 256
    assert nb._conversion_settings() == ConversionSettings(256, "none", 5, 3)
    assert "chunks of 256 x 256 px, no compression, 3 smaller level(s)" in nb.plan.value
    nb.conversion.value = "read"
    assert not ui.is_visible(nb.conversion_settings) and nb.space_warning.value == ""


def test_conversion_cards_can_be_collapsed_without_losing_choices(experiment):
    nb = checked(experiment)
    if nb.conversion.disabled:
        pytest.skip("zarr is not installed")
    nb.conversion.value = "convert"
    assert nb.conversion_settings.selected_index == 0
    assert nb.conversion_settings.get_title(0) == "OME-Zarr conversion settings"
    cards = nb.conversion_grid.children
    assert len(cards) == 3
    assert "Storage block size" in cards[0].children[0].value
    assert nb.chunk_size in cards[0].children
    assert nb.compression in cards[1].children
    assert nb.pyramid in cards[2].children
    nb.conversion_settings.selected_index = None
    nb.chunk_size.value = 512
    nb.compression.value = "zstd"
    nb.compression_level.value = 7
    nb.pyramid.value = "none"
    nb.recommended.value = False
    nb.recommended.value = True
    assert nb.conversion_settings.selected_index is None
    assert "Free space on the drive" in nb.space_warning.value
    settings = nb._conversion_settings()
    assert settings == ConversionSettings(512, "zstd", 7, 0)
    nb.conversion.value = "read"
    nb.conversion.value = "convert"
    assert nb.conversion_settings.selected_index == 0
    assert nb._conversion_settings() == settings


def test_disk_space_warning_turns_red_when_space_is_short(experiment, monkeypatch):
    from morphobricks_preprocessing import notebook_ui

    nb = checked(experiment)
    if nb.conversion.disabled:
        pytest.skip("zarr is not installed")
    monkeypatch.setattr(notebook_ui, "free_space", lambda path: 10)
    nb = confirmed(nb.folder.value)
    nb.conversion.value = "convert"
    assert "may not fit on the drive" in nb.space_warning.value
    assert "choose an output folder on another drive" in nb.space_warning.value
    assert not nb.run_button.disabled  # a warning, never a block


def test_nothing_to_convert_in_an_ome_zarr_experiment(tmp_path):
    pytest.importorskip("zarr")
    from zarr_tree import write_ngff

    write_ngff(tmp_path / "exp/a.ome.zarr", np.zeros((2, 8, 8), np.uint8), "zyx")
    nb = checked(tmp_path / "exp")
    nb.conversion.value = "convert"
    assert "There is nothing to convert" in nb.space_warning.value


def test_conversion_without_zarr(experiment, monkeypatch):
    from morphobricks_preprocessing import notebook_ui

    monkeypatch.setattr(notebook_ui, "zarr_available", lambda: False)
    nb = checked(experiment)
    assert nb.conversion.disabled and nb.conversion.value == "read"
    assert "Converting is not available" in nb.conversion_help.value
    assert '<b>pip install "morphobricks-preprocessing[zarr]"</b>' in nb.conversion_help.value


def test_conversion_from_the_notebook(experiment, tmp_path):
    pytest.importorskip("zarr")
    nb = confirmed(experiment, output_folder=str(tmp_path / "out"))
    assert "converted to OME-Zarr" not in nb.plan.value
    nb.conversion.value = "convert"
    nb.chunk_size.value = 16
    assert "<b>3 TIFF images</b> will be converted to OME-Zarr" in nb.plan.value
    result = nb.run()
    assert result.converted == ("ctrl/rep1/a.tif", "ctrl/rep1/b.tif", "drug/rep1/a.tif")
    assert "TIFF images converted" in nb.run_tiles.value
    assert "3 images converted in this run" in nb.run_status.value
    assert (tmp_path / "out" / "ome_zarr" / "ctrl" / "rep1" / "a.ome.zarr").is_dir()
    settings = read_manifest(result.manifest_path).settings
    assert settings.convert_tiffs and settings.conversion.chunk_size == 16


def test_output_folder_edits_refresh_the_plan(experiment, tmp_path):
    nb = confirmed(experiment)
    nb.output.value = str(tmp_path / "elsewhere")
    assert "elsewhere" in nb.plan.value


def test_full_run_and_statistics(experiment, tmp_path):
    nb = confirmed(experiment, output_folder=str(tmp_path / "out"))
    nb.recommended.value = False
    nb.bins.value = 64
    nb.run_button.click()
    assert nb.result.written
    assert "Done: 9 planes in the table" in nb.run_status.value
    assert "Step 4 (optional)" in nb.run_next.value
    assert "What next?" in nb.run_next.value
    assert "Calculate statistics" in nb.run_next.value
    assert "planes in the table" in nb.run_tiles.value
    assert nb.progress.value == nb.progress.max == 3
    assert nb.progress.bar_style == "success"
    assert nb.run_button.description == " Compute histograms" and not nb.run_button.disabled
    assert "Wrote 9 planes" in nb.details.value  # the log reaches "Technical details"
    assert read_manifest(tmp_path / "out" / "run_manifest.json").settings.bin_count == 64
    # Step 4 is optional: the new table is selected but nothing is calculated yet.
    assert nb.csv_file.value == str(tmp_path / "out" / "plane_histograms.csv")
    assert "The table from step 3 is selected" in nb.stats_status.value
    assert not ui.is_visible(nb.statistics_card)
    nb.statistics_button.click()
    assert ui.is_visible(nb.statistics_card) and "<td>ctrl</td>" in nb.statistics.value
    assert "Statistics of 2 conditions calculated" in nb.stats_status.value
    assert "plane_histograms.csv" in nb.stats_status.value
    assert ui.is_visible(nb.distribution_card)
    assert bytes(nb.distribution.value).startswith(b"\x89PNG")
    assert "What next?" in nb.stats_next.value
    assert "Review the group statistics" in nb.stats_next.value
    nb.percentiles.value = "invalid"
    nb.statistics_button.click()
    assert nb.stats_next.value == ""


def test_run_with_errors_shows_what_to_do(experiment, monkeypatch):
    nb = confirmed(experiment)
    nb.run()  # a failed rerun must remove the earlier success guidance
    assert nb.run_next.value
    write_file(experiment / "ctrl/rep1/bad.tif", b"garbage")  # appears after the check
    nb.run()
    assert not nb.result.written
    assert "Nothing was written" in nb.run_status.value
    assert "could not be opened" in nb.run_issues.value
    assert nb.progress.bar_style == "danger"
    assert nb.run_next.value == ""


def test_unexpected_failures_are_reported(experiment, monkeypatch):
    import morphobricks_preprocessing.notebook_ui as module

    nb = confirmed(experiment)

    def broken(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(module, "run_experiment", broken)
    assert nb.run() is None
    assert "unexpected problem" in nb.run_status.value
    assert "RuntimeError: disk on fire" in nb.run_status.value
    assert "Nothing was written" not in nb.run_status.value
    assert "Traceback" in nb.details.value
    assert not nb.run_button.disabled


# Step 4 -----------------------------------------------------------------------------------


@pytest.mark.parametrize("failure", [None, "read", "pool", "plot"])
def test_statistics_stay_busy_until_finished_and_recover_on_failure(
    experiment, monkeypatch, failure
):
    import morphobricks_preprocessing.notebook_ui as module

    nb = confirmed(experiment)
    nb.run()
    stages = []
    for stage, name in (
        ("read", "read_plane_histograms"),
        ("pool", "group_summaries"),
        ("plot", "distribution_png"),
    ):
        original = getattr(module, name)

        def while_busy(*args, stage=stage, original=original, **kwargs):
            assert nb.statistics_button.disabled
            assert nb.statistics_button.description.strip() == "Calculating…"
            assert nb.statistics_button.icon == "spinner"
            assert "mb-spinner" in nb.stats_status.value
            assert nb.stats_next.value == ""
            assert "calculated." not in nb.stats_status.value
            stages.append(stage)
            nb.show_statistics()  # a second request must not start another calculation
            if failure == stage:
                if stage == "read":
                    raise OSError("simulated unreadable table")
                raise RuntimeError("simulated calculation failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, while_busy)
    nb.statistics_button.click()
    assert stages == ["read", "pool", "plot"][: {"read": 1, "pool": 2}.get(failure, 3)]
    assert not nb.statistics_button.disabled
    assert nb.statistics_button.description.strip() == "Calculate statistics"
    assert nb.statistics_button.icon == "calculator"
    assert "mb-spinner" not in nb.stats_status.value
    if failure:
        assert "mb-error" in nb.stats_status.value
        assert not ui.is_visible(nb.statistics_card)
        assert not ui.is_visible(nb.distribution_card)
        assert nb.stats_next.value == ""
        if failure == "read":
            assert "The table cannot be read" in nb.stats_status.value
        else:
            assert "simulated calculation failure" in nb.stats_status.value
            assert "Traceback" in nb.details.value
    else:
        assert "Statistics of 2 conditions calculated" in nb.stats_status.value
        assert ui.is_visible(nb.statistics_card)
        assert ui.is_visible(nb.distribution_card)
        assert "What next?" in nb.stats_next.value


def test_statistics_by_level_and_percentiles(experiment):
    nb = confirmed(experiment)
    nb.run()
    nb.statistics_button.click()
    nb.level.value = "image"  # recalculated at once once statistics are shown
    assert "<th>image</th>" in nb.statistics.value
    assert "image groups" in nb.stats_tiles.value
    nb.percentiles.value = "50"
    nb.statistics_button.click()
    assert "<th>p50</th>" in nb.statistics.value


def test_statistics_explain_a_table_of_an_earlier_version(experiment, tmp_path):
    nb = checked(experiment)
    nb.csv_file.value = str(Path(__file__).parent / "data" / "v2_float32.csv")
    nb.statistics_button.click()
    assert "The table cannot be read" in nb.stats_status.value
    assert "schema version 2" in nb.stats_status.value
    assert "Run the preprocessing brick again" in nb.stats_status.value
    assert not ui.is_visible(nb.statistics_card)


def test_statistics_need_a_table_and_valid_percentiles(experiment):
    nb = checked(experiment)
    nb.show_statistics()
    assert "Choose a histogram table first" in nb.stats_status.value
    assert not ui.is_visible(nb.statistics_card)
    nb.csv_file.value = str(experiment / "missing.csv")
    nb.show_statistics()
    assert "does not exist" in nb.stats_status.value
    nb.labels_confirmed.button.click()
    confirm_repeats(nb)
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    nb.image_type_checked.button.click()
    nb.run()
    nb.percentiles.value = "1, 120"
    nb.show_statistics()
    assert "percentiles cannot be read" in nb.stats_status.value


def test_statistics_of_another_table(experiment, monkeypatch):
    table = confirmed(experiment).run().csv_path
    later = PreprocessingNotebook()
    assert later.csv_file.value == ""  # no table calculated in this session
    monkeypatch.setattr(ui, "ask_open_file", lambda initial, filetypes: str(table))
    later.csv_browse.click()
    assert later.csv_file.value == str(table)
    later.statistics_button.click()
    assert "<td>drug</td>" in later.statistics.value
    # Choosing another table clears the results of the previous one.
    later.csv_file.value = str(table) + "x"
    assert later.statistics.value == "" and not ui.is_visible(later.statistics_card)
    assert later.stats_next.value == ""
    monkeypatch.setattr(ui, "ask_open_file", lambda initial, filetypes: None)
    later.csv_browse.click()
    assert "could not be opened" in later.stats_status.value


def test_step_4_is_optional_and_keeps_its_controls_in_the_group_card():
    nb = PreprocessingNotebook()
    page = nb.results_step()
    root = page.children[1]
    assert "Calculate the statistics (optional)" in root.children[0].value
    assert "This step is optional" in root.children[2].value
    assert nb.statistics_button in nb.results_card.children
    assert nb.stats_status in nb.results_card.children
    assert nb.statistics_button.description.strip() == "Calculate statistics"


# Rendering helpers -------------------------------------------------------------------------


def test_issues_html_escapes_and_folds_long_path_lists():
    paths = [f"<f{i}>.tif" for i in range(25)]
    text = issues_html(
        [warning("w_code", "Odd & strange.", paths), error("e", "Bad.", action="Fix.")]
    )
    assert text.index("Error · e") < text.index("Warning · w code")
    assert "Odd &amp; strange." in text and "&lt;f0&gt;.tif" in text
    assert "Show 5 more" in text and "Processing is blocked" in text
    assert "No problems found" in issues_html([])


@pytest.mark.parametrize(
    ("role", "low", "high", "text"),
    [
        ("Z", 1, 1, "A single depth slice: z = 0."),
        ("C", 2, 4, "2 to 4 channels, numbered c = 0 to 3"),
        ("T", 5, 5, "5 time points"),
        ("X", 512, 512, "Each plane is 512 pixels in width."),
        (None, 1, 1, "no role is needed"),
        (None, 1, 3, "needs a role"),
    ],
)
def test_role_effect(role, low, high, text):
    assert text in role_effect(role, low, high)


def test_plane_summary_with_ranges():
    mapping = AxisMapping("ZYX", "ZYX")
    text = plane_summary_html(mapping, [(2, 5), (8, 8), (9, 10)])
    assert "2 to 5 plane(s) of 8–8 × 9–10 pixels" in text
    assert plane_summary_html(AxisMapping("ZYX", "Z-X"), [(2, 2), (8, 8), (9, 9)]) == ""


def test_display_range_ignores_non_finite_values():
    pixels = np.array([[np.nan, 1.0], [2.0, np.inf]])
    assert display_range(pixels, 0, 100) == (1.0, 2.0)
    assert display_range(np.full((2, 2), np.nan)) == (0.0, 1.0)
    assert display_range(np.zeros((2, 2), np.uint8)) == (0.0, 1.0)


def test_plane_png():
    plane = Plane(None, None, None, np.arange(12, dtype=np.uint16).reshape(3, 4))
    assert plane_png(plane).startswith(b"\x89PNG")


def test_distribution_png_skips_empty_groups():
    assert distribution_png({}) is None


# Style helpers -------------------------------------------------------------------------------


def test_status_escapes_text_unless_raw():
    assert "&lt;b&gt;" in ui.status("ok", "Title", ["<b>"])
    assert "<li><b>x</b></li>" in ui.status("warn", "Title", ["<b>x</b>"], raw=True)
    assert "mb-error" in ui.status("error", "T")


def test_tiles_table_and_checklist():
    assert "mb-tile-value'>🧩 3<" in ui.tiles([("🧩", "3", "planes")])
    table = ui.table([["name", "n"], ["a<", "1"]], numeric_from=1)
    assert "<td>a&lt;</td><td class='mb-num'>1</td>" in table
    assert ui.table([]) == ""
    assert "✅ done" in ui.checklist([(True, "done"), (False, "todo")])


@pytest.mark.parametrize(
    ("text", "expected"),
    [('  "C:/data/exp"  ', "C:/data/exp"), ("‘/data’", "/data"), ("", "")],
)
def test_clean_path(text, expected):
    assert ui.clean_path(text) == expected


def test_clean_path_expands_home():
    assert ui.clean_path("~/data") == os.path.expanduser("~/data")


# The notebook file -------------------------------------------------------------------------


def _notebook():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def test_notebook_is_valid_and_has_no_outputs():
    notebook = _notebook()
    assert notebook["nbformat"] == 4
    code = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    assert all(cell["outputs"] == [] and cell["execution_count"] is None for cell in code)


def test_notebook_code_cells_are_hidden_and_titled():
    for cell in _notebook()["cells"]:
        if cell["cell_type"] == "code":
            assert cell["metadata"]["jupyter"]["source_hidden"] is True
            assert cell["metadata"]["jupyterlabHideCode"]["locked"] is True
            assert cell["source"][0].startswith("# @markdown ### Run to ")


def test_notebook_keeps_processing_in_the_package():
    sources = [
        "".join(c["source"][1:]).strip() for c in _notebook()["cells"] if c["cell_type"] == "code"
    ]
    # Step 0 must work before the package or third-party dependencies are installed.
    setup_imports = set()
    for node in ast.walk(ast.parse(sources[0])):
        if isinstance(node, ast.Import):
            setup_imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            setup_imports.add(node.module.split(".")[0])
    assert setup_imports <= {"subprocess", "sys", "importlib", "pathlib"}
    assert sources[1:] == [
        "notebook.folder_step()",
        "notebook.axis_step()",
        "notebook.run_step()",
        "notebook.results_step()",
    ]


def test_widgets_use_99_percent_width_and_bold_paths(experiment):
    import morphobricks_preprocessing.notebook_style as style_module
    import morphobricks_preprocessing.notebook_ui as ui_module

    for module in (style_module, ui_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "100%" not in source
        assert "<code>" not in source
    nb = checked(experiment)
    assert "<b>" in nb.output_hint.value and "<code>" not in nb.labels_table.value


def test_notebook_names_buttons_that_exist():
    text = "".join("".join(c["source"]) for c in _notebook()["cells"])
    nb = PreprocessingNotebook()
    for button in (
        nb.check_button,
        nb.labels_confirmed.button,
        nb.preview_button,
        nb.preview_checked.button,
        nb.missing_as_singleton.button,
        nb.confirm_button,
        nb.image_type_checked.button,
        nb.run_button,
        nb.statistics_button,
    ):
        assert f"**{button.description.strip()}**" in text


def test_notebook_cells_run():
    namespace: dict = {}
    for cell in _notebook()["cells"]:
        if cell["cell_type"] == "code":
            exec("".join(cell["source"]), namespace)  # noqa: S102 - our own notebook
    assert isinstance(namespace["notebook"], PreprocessingNotebook)


def test_preview_caption_of_a_reduced_plane():
    from morphobricks_preprocessing import BlockReader

    reader = BlockReader((9000, 7000), np.dtype("uint16"), lambda: iter(()))
    pixels = np.arange(12, dtype=np.uint16).reshape(3, 4)
    plane = Plane(2, None, None, pixels, step=2250, reader=reader)
    caption = preview_caption("ctrl/big.tif", plane)
    assert "9000 × 7000 px" in caption and "1 pixel in 2250" in caption
    assert "3 × 4 px shown" in caption and "shown values 0 to 11" in caption
    whole = preview_caption("ctrl/a.tif", Plane(None, None, None, pixels))
    assert "3 × 4 px" in whole and "reduced" not in whole
