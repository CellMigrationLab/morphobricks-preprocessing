import ast
import json
import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import Plane, read_manifest, read_plane_histograms  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.axes import AxisMapping  # noqa: E402
from morphobricks_preprocessing.notebook_ui import (  # noqa: E402
    PreprocessingNotebook,
    display_range,
    distribution_png,
    issues_html,
    plane_png,
    plane_summary_html,
    role_effect,
)
from morphobricks_preprocessing.validation import error, warning  # noqa: E402
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


def confirmed(experiment, **kwargs):
    nb = checked(experiment, **kwargs)
    nb.labels_confirmed.button.click()
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
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
    assert "The notebook is ready" in root.children[-1].value


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
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert nb.run().excluded == ("ctrl/rep1/bad.tif",)


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


def test_missing_axes_card(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((2, 3, 8, 8), np.uint8), axes="ZCYX")
    write_data(root / "b.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    nb = checked(root)
    assert ui.is_visible(nb.missing_card)
    assert "1 image with axes <b>ZYX</b> lack C (channel)" in nb.missing_note.value
    nb.preview_button.click()
    nb.preview_checked.button.click()
    assert nb.confirm_button.disabled
    nb.missing_as_singleton.button.click()
    assert not nb.confirm_button.disabled


@pytest.mark.parametrize("blocking_file", [False, True])
def test_confirmed_missing_axes_allow_running_unless_other_errors_remain(tmp_path, blocking_file):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((2, 3, 8, 8), np.uint8), axes="ZCYX")
    write_data(root / "b.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    if blocking_file:
        write_file(root / "bad.tif", b"garbage")
    nb = checked(root)
    nb.labels_confirmed.button.click()
    nb.preview_button.click()
    nb.preview_checked.button.click()
    assert nb.run_button.disabled
    nb.missing_as_singleton.button.click()
    nb.confirm_button.click()
    assert nb.confirmed_mapping is not None
    assert nb.run_button.disabled == blocking_file
    if blocking_file:
        assert nb.run() is None
        assert "could not be opened" in nb.folder_issues.value
    else:
        result = nb.run()
        assert result.written
        rows = read_plane_histograms(result.csv_path)
        assert [(r.z, r.c) for r in rows if r.source_relative_path == "b.tif"] == [(0, 0), (1, 0)]


def test_undoing_missing_axes_confirmation_blocks_running_again(tmp_path):
    root = tmp_path / "exp"
    write_data(root / "a.tif", np.zeros((2, 3, 8, 8), np.uint8), axes="ZCYX")
    write_data(root / "b.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    nb = checked(root)
    nb.labels_confirmed.button.click()
    nb.missing_as_singleton.button.click()
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert not nb.run_button.disabled
    nb.confirm_button.click()  # unlock the roles before undoing singleton confirmation
    nb.missing_as_singleton.button.click()
    assert nb.run_button.disabled and nb.confirm_button.disabled
    assert nb.run() is None


def test_conflicting_axes_explain_why_no_roles_are_offered(tmp_path):
    write_data(tmp_path / "exp/a.tif", np.zeros((2, 8, 8), np.uint8), axes="ZYX")
    write_data(tmp_path / "exp/b.tif", np.zeros((2, 8, 8), np.uint8), axes="CYX")
    nb = checked(tmp_path / "exp")
    (message,) = nb.axis_box.children
    assert "No axis roles can be suggested" in message.value
    assert "check the experiment folder and fix any errors" in nb.readiness.value


# Step 3 -----------------------------------------------------------------------------------


def test_readiness_checklist(experiment):
    nb = checked(experiment)
    assert "Almost ready" in nb.readiness.value
    assert "Still to do: confirm the labels" in nb.readiness.value
    assert "✅ Check the experiment folder" in nb.readiness.value
    nb.labels_confirmed.button.click()
    assert "Still to do: confirm the axis roles" in nb.readiness.value
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
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


def test_ome_zarr_option_is_off_and_survives_the_recommended_settings(experiment):
    nb = checked(experiment)
    assert not nb.ome_zarr.value
    assert "OME-Zarr" in nb.ome_zarr_hint.value and "<code>" not in nb.ome_zarr_hint.value
    if nb.ome_zarr.disabled:
        return
    nb.ome_zarr.value = True
    nb.recommended.value = False
    nb.recommended.value = True
    assert nb.ome_zarr.value  # a separate output, not one of the recommended settings


def test_ome_zarr_option_without_zarr(experiment, monkeypatch):
    from morphobricks_preprocessing import notebook_ui

    monkeypatch.setattr(notebook_ui, "zarr_available", lambda: False)
    nb = checked(experiment)
    assert nb.ome_zarr.disabled
    assert "Not available" in nb.ome_zarr_hint.value
    assert '<b>pip install "morphobricks-preprocessing[zarr]"</b>' in nb.ome_zarr_hint.value


def test_ome_zarr_copies_from_the_notebook(experiment, tmp_path):
    pytest.importorskip("zarr")
    nb = confirmed(experiment, output_folder=str(tmp_path / "out"))
    assert "OME-Zarr copies" not in nb.plan.value
    nb.ome_zarr.value = True
    assert "OME-Zarr copies of the images go to" in nb.plan.value
    result = nb.run()
    assert result.converted == ("ctrl/rep1/a.tif", "ctrl/rep1/b.tif", "drug/rep1/a.tif")
    assert "OME-Zarr copies written" in nb.run_tiles.value
    assert "3 images copied in this run" in nb.run_status.value
    assert (tmp_path / "out" / "ome_zarr" / "ctrl" / "rep1" / "a.ome.zarr").is_dir()
    assert read_manifest(result.manifest_path).settings.ome_zarr


def test_full_run_and_statistics(experiment, tmp_path):
    nb = confirmed(experiment, output_folder=str(tmp_path / "out"))
    nb.recommended.value = False
    nb.bins.value = 64
    nb.run_button.click()
    assert nb.result.written
    assert "Done: 18 planes in the table" in nb.run_status.value
    assert "Step 4 (optional)" in nb.run_status.value
    assert "planes in the table" in nb.run_tiles.value
    assert nb.progress.value == nb.progress.max == 3
    assert nb.progress.bar_style == "success"
    assert nb.run_button.description == " Compute histograms" and not nb.run_button.disabled
    assert "Wrote 18 planes" in nb.details.value  # the log reaches "Technical details"
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


def test_run_with_errors_shows_what_to_do(experiment, monkeypatch):
    nb = confirmed(experiment)
    write_file(experiment / "ctrl/rep1/bad.tif", b"garbage")  # appears after the check
    nb.run()
    assert not nb.result.written
    assert "Nothing was written" in nb.run_status.value
    assert "could not be opened" in nb.run_issues.value
    assert nb.progress.bar_style == "danger"


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
        if failure == "read":
            assert "The table cannot be read" in nb.stats_status.value
        else:
            assert "simulated calculation failure" in nb.stats_status.value
            assert "Traceback" in nb.details.value
    else:
        assert "Statistics of 2 conditions calculated" in nb.stats_status.value
        assert ui.is_visible(nb.statistics_card)
        assert ui.is_visible(nb.distribution_card)


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


def test_statistics_need_a_table_and_valid_percentiles(experiment):
    nb = checked(experiment)
    nb.show_statistics()
    assert "Choose a histogram table first" in nb.stats_status.value
    assert not ui.is_visible(nb.statistics_card)
    nb.csv_file.value = str(experiment / "missing.csv")
    nb.show_statistics()
    assert "does not exist" in nb.stats_status.value
    nb.labels_confirmed.button.click()
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()
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
