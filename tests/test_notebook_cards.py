"""PP-10: the notebook's cards for calibration, design, recorded inputs, report and lock.

The notebook catches up with the command line and API of PP-7, PP-8, PP-8b and PP-8c, with
the same functions: the check writes its report and the design template; pixel sizes are
shown with their source and stated through ``update_calibration``; design answers go
through ``apply_design_edit`` and are saved under the lock; the run step shows the mode
and the input changes; a run that stops says what it wrote; a stale lock is removed only on
an explicit click, never one of a process still running here.
"""

import json
import os
import socket

import numpy as np
import pytest
import tifffile

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.calibration import Calibration, calibration_entry  # noqa: E402
from morphobricks_preprocessing.design import read_experiment_design  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from morphobricks_preprocessing.run import LOCK_FILENAME, write_calibration  # noqa: E402
from run_files import check_outputs, finished_pid  # noqa: E402
from tiff_tree import write_data, write_plain  # noqa: E402

HEADER = {"PhysicalSizeX": 0.5, "PhysicalSizeY": 0.5, "PhysicalSizeZ": 0.43}


@pytest.fixture
def experiment(tmp_path):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    for rel in ["ctrl/rep1/a.tif", "ctrl/rep2/a.tif", "drug/rep1/a.tif", "drug/rep2/a.tif"]:
        write_data(root / rel, rng.integers(0, 200, (2, 16, 16), dtype=np.uint8), axes="ZYX")
    return root


def output_of(root):
    return root.parent / f"{root.name}_MorphoBricks_Preprocessing"


def checked(root, **kwargs):
    nb = PreprocessingNotebook(str(root), **kwargs)
    for step in (nb.welcome, nb.folder_step, nb.axis_step, nb.run_step, nb.results_step):
        step()
    nb.check_button.click()
    return nb


def confirm_axes(nb):
    nb.preview_button.click()
    nb.preview_checked.button.click()
    nb.confirm_button.click()


def ready(root, **kwargs):
    """Check the folder and confirm the labels, every repeat group, the roles and the type."""
    nb = checked(root, **kwargs)
    nb.labels_confirmed.button.click()
    for toggle in nb.group_confirmations.values():
        toggle.button.click()
    confirm_axes(nb)
    nb.image_type_checked.button.click()
    assert not nb.run_button.disabled
    return nb


def hold_lock(output, pid, host=None):
    output.mkdir(parents=True, exist_ok=True)
    holder = {
        "host": host or socket.gethostname(),
        "pid": pid,
        "started_at": "2026-11-02T10:00:00+00:00",
    }
    (output / LOCK_FILENAME).write_text(json.dumps(holder), encoding="utf-8")


# 4. The check writes its report and the design template, as the command line's check -----


def test_the_check_writes_its_report_and_the_design_template(experiment):
    nb = checked(experiment)
    output = output_of(experiment)
    assert check_outputs(output)  # the report and the design template, nothing else
    assert "The check wrote its report" in nb.check_report.where.value
    assert "discovery_report.txt" in nb.check_report.where.value
    assert ui.is_visible(nb.check_report.box)
    assert "MorphoBricks preprocessing report: check, stopped" in nb.check_report.text.value
    design = read_experiment_design(output / "experiment_design.csv")
    assert not any(row.confirmed or row.control for row in design.rows)  # nothing confirmed
    nb.labels_confirmed.button.click()
    assert "Check folder wrote experiment_design.csv" in nb.design_status.value
    assert "Nothing is confirmed" in nb.design_status.value
    assert not nb._design_confirmed()


def test_an_embedded_check_without_the_output_card_writes_nothing(experiment):
    nb = PreprocessingNotebook(str(experiment))
    nb.folder_step(show_output=False, show_design=False)
    nb.check_button.click()
    assert nb.preflight.ok
    assert not output_of(experiment).exists()
    assert nb.check_report.where.value == ""


def test_the_check_without_the_design_card_writes_the_report_only(experiment):
    nb = PreprocessingNotebook(str(experiment))
    nb.folder_step(show_design=False)
    nb.check_button.click()
    output = output_of(experiment)
    assert (output / "discovery_report.json").is_file()
    assert not (output / "experiment_design.csv").exists()


def test_a_check_that_cannot_write_its_report_still_shows_its_results(experiment, monkeypatch):
    from morphobricks_preprocessing import notebook_output

    def read_only(*args, **kwargs):
        raise PermissionError("read-only output folder")

    monkeypatch.setattr(notebook_output, "write_check_outputs", read_only)
    nb = checked(experiment)
    assert nb.preflight.ok and ui.is_visible(nb.labels_card)
    assert "could not write its report" in nb.check_report.where.value
    assert "Cannot write the check report" in nb.details.value


# 2. Design answers through design_edit, saved under the lock ------------------------------


def test_design_answers_use_the_design_command_rules(experiment):
    nb = checked(experiment)
    output = output_of(experiment)
    nb.labels_confirmed.button.click()
    nb.group_confirmations["rep1"].button.click()
    on_file = read_experiment_design(output / "experiment_design.csv")
    assert on_file.group_confirmed("rep1") and not on_file.group_confirmed("rep2")
    nb.control_checks["ctrl"].value = True
    assert read_experiment_design(output / "experiment_design.csv").controls == ("ctrl",)
    nb.group_confirmations["rep1"].button.click()  # undo, through DesignEdit(unconfirm=...)
    assert not read_experiment_design(output / "experiment_design.csv").group_confirmed("rep1")
    assert not nb._design_confirmed()


def test_a_refused_confirmation_is_shown_and_not_taken(experiment, monkeypatch):
    from morphobricks_preprocessing import notebook_design
    from morphobricks_preprocessing.design_edit import DesignEditError

    nb = checked(experiment)
    nb.labels_confirmed.button.click()

    def refuse(design, edit, on):
        raise DesignEditError("repeat group 'rep1' cannot be confirmed: a reason")

    monkeypatch.setattr(notebook_design, "apply_design_edit", refuse)
    nb.group_confirmations["rep1"].button.click()
    assert not nb.group_confirmations["rep1"].value
    assert "This answer was not taken" in nb.design_status.value
    assert "a reason" in nb.design_status.value
    assert not nb.design.group_confirmed("rep1")


def test_a_design_answer_is_not_saved_while_another_run_holds_the_lock(experiment):
    nb = checked(experiment)
    output = output_of(experiment)
    nb.labels_confirmed.button.click()
    before = (output / "experiment_design.csv").read_bytes()
    hold_lock(output, os.getpid())  # this process: a run still going here
    nb.group_confirmations["rep1"].button.click()
    assert (output / "experiment_design.csv").read_bytes() == before
    assert "Not saved: another run holds the lock" in nb.design_status.value
    assert ui.is_visible(nb.folder_lock.card)


def test_a_design_that_cannot_be_saved_reaches_the_technical_details(experiment, monkeypatch):
    from morphobricks_preprocessing import notebook_design

    def read_only(*args, **kwargs):
        raise OSError("read-only output folder")

    monkeypatch.setattr(notebook_design, "write_design", read_only)
    nb = checked(experiment)
    nb.labels_confirmed.button.click()
    assert "Cannot save the design" in nb.details.value
    assert "read-only output folder" in nb.details.value


# 1 and 7. Pixel sizes with their source, stated through update_calibration -------------------


def projections(tmp_path):
    root = tmp_path / "exp"
    rng = np.random.default_rng(0)
    for rel in ["ctrl/a.ome.tif", "drug/a.ome.tif"]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = rng.integers(0, 4000, (2, 8, 8), dtype=np.uint16)
        tifffile.imwrite(
            path, data, ome=True, photometric="minisblack", metadata={"axes": "CYX", **HEADER}
        )
    return root


def test_the_calibration_card_shows_each_size_with_its_source(tmp_path):
    nb = checked(projections(tmp_path))
    assert ui.is_visible(nb.calibration_card)
    table = nb.calibration_table.value
    assert "0.5 micrometer (header)" in table
    assert "2 images" in table
    # The folder check cannot know the roles yet: the header's z-step is shown, with a note.
    assert "0.43 micrometer (header)" in table
    assert "until the axis roles are confirmed" in table


def test_confirmed_roles_show_no_size_for_an_axis_the_images_lack(tmp_path):
    nb = checked(projections(tmp_path))
    nb.labels_confirmed.button.click()
    confirm_axes(nb)
    table = nb.calibration_table.value
    assert "0.43" not in table and "no such axis" in table
    assert "With the confirmed axis roles" in table
    nb.confirm_button.click()  # undo: the roles may change again
    assert "0.43 micrometer (header)" in nb.calibration_table.value


def test_a_stated_size_is_written_with_the_calibrate_api_and_shown(tmp_path):
    root = projections(tmp_path)
    nb = checked(root)
    nb.labels_confirmed.button.click()
    nb.calibration_target.value = "ctrl"
    nb.calibration_axes.value = "XY"
    nb.calibration_size.value = "0.25"
    nb.calibration_state.click()
    text = (output_of(root) / "calibration.csv").read_text(encoding="utf-8")
    assert "ctrl,X,0.25,micrometer" in text and "ctrl,Y,0.25,micrometer" in text
    table = nb.calibration_table.value
    assert "0.25 micrometer (stated)" in table and "0.5 micrometer (header)" in table
    assert "calibration_differs_from_header" in nb.calibration_issues.value.replace(" ", "_")
    assert nb.labels_confirmed.value  # the answers given so far are kept
    nb.calibration_unit.value = "pixels"
    assert nb.calibration_size.disabled
    nb.calibration_target.value = "."
    nb.calibration_axes.value = "Z"
    nb.calibration_state.click()
    assert ".,Z,,pixels" in (output_of(root) / "calibration.csv").read_text(encoding="utf-8")
    assert "pixels (stated)" in nb.calibration_table.value
    confirm_axes(nb)
    assert "calibration axis absent" in nb.calibration_issues.value
    nb.confirm_button.click()
    nb.calibration_axes.value = "XY"
    nb.calibration_target.value = "ctrl"
    nb.calibration_remove.click()
    assert "0.25" not in nb.calibration_table.value


def test_a_wrong_statement_changes_nothing_and_says_why(tmp_path):
    root = projections(tmp_path)
    nb = checked(root)
    nb.calibration_size.value = "-1"
    nb.calibration_state.click()
    assert "Nothing was changed" in nb.calibration_message.value
    assert not (output_of(root) / "calibration.csv").exists()
    nb.calibration_size.value = "0.3"
    nb.calibration_target.value = "elsewhere"
    nb.calibration_state.click()
    assert "calibration target not found" in nb.calibration_issues.value
    assert not nb.preflight.ok


# 3 and 4. Mode, input changes, and what a run wrote --------------------------------------


def test_the_run_step_names_the_mode_and_the_input_changes(experiment):
    nb = ready(experiment)
    assert "No earlier run was found" in nb.plan.value
    nb.mode.value = "full"
    assert "Mode <b>full</b>" in nb.plan.value
    nb.mode.value = "restart"
    result = nb.run()
    assert result.written and "Mode: <b>restart</b>" in nb.run_status.value
    assert "The run wrote its report" in nb.run_report.where.value
    nb.exclude.value = "ctrl/rep1/a.tif"
    nb._refresh_run_step()
    assert "Inputs that differ from those of the earlier run" in nb.plan.value
    assert "<b>exclude</b>: none → ctrl/rep1/a.tif" in nb.plan.value
    assert "the rows of newly excluded images are removed" in nb.plan.value
    nb.mode.value = "full"
    result = nb.run()
    assert result.written and result.mode == "full"
    assert "Inputs that differ from the earlier run" in nb.run_status.value
    assert "<b>exclude</b>" in nb.run_status.value and "Mode: <b>full</b>" in nb.run_status.value


def test_the_plan_reads_the_calibration_the_run_reads(experiment):
    """A ``calibration.csv`` edited after the check (e.g. by ``calibrate``) is in the plan."""
    nb = ready(experiment)
    assert nb.run().written
    output = output_of(experiment)
    write_calibration(Calibration((calibration_entry(".", "X", 0.7, "um"),)), output)
    planned = nb._planned_changes(output.resolve())
    assert [change.name for change in planned] == ["calibration"]
    assert nb.run().input_changes == planned


def test_the_plan_names_the_mask_changes_the_run_names(experiment, tmp_path):
    """The masks folder and mask axes given, changed or dropped: plan and run agree (PP-10c)."""
    first, second = tmp_path / "masks_a", tmp_path / "masks_b"
    for folder in (first, second):
        for rel in ["ctrl/rep1/a.tif", "ctrl/rep2/a.tif", "drug/rep1/a.tif", "drug/rep2/a.tif"]:
            write_plain(folder / "cells" / rel, np.ones((2, 16, 16), np.uint16))  # IYX
    nb = PreprocessingNotebook(str(experiment))
    for step in (nb.folder_step, nb.axis_step, nb.run_step):
        step()
    nb.masks_folder.value = str(first)
    nb.check_button.click()
    nb._mask_choices["cells"].value = "ZYX"  # stated as --mask-axes cells=ZYX
    nb.masks_button.click()
    nb.labels_confirmed.button.click()
    for toggle in nb.group_confirmations.values():
        toggle.button.click()
    confirm_axes(nb)
    nb.image_type_checked.button.click()
    assert nb.run().written
    output = output_of(experiment).resolve()
    assert nb._planned_changes(output) == ()
    nb.masks_folder.value = str(second)  # a new masks folder: its statements start over
    nb._mask_choices["cells"].value = "ZYX"
    nb.masks_button.click()
    planned = nb._planned_changes(output)
    assert [change.name for change in planned] == ["masks_folder"]
    assert nb.run().input_changes == planned
    nb.masks_folder.value = ""
    nb.masks_drop.click()  # explicit, as --no-masks
    nb.masks_button.click()
    planned = nb._planned_changes(output)
    assert [(change.name, change.given) for change in planned] == [
        ("masks_folder", "none (dropped)")
    ]
    assert nb.run().input_changes == planned


def test_a_stopped_run_says_it_wrote_its_report(experiment):
    nb = ready(experiment)
    (experiment / "drug/rep2/a.tif").write_bytes(b"no longer an image")
    result = nb.run()
    assert not result.written and result.report_written
    assert "The run stopped: no table or manifest was written." in nb.run_status.value
    assert "Its report was written" in nb.run_status.value
    assert "The run wrote its report" in nb.run_report.where.value
    assert not (output_of(experiment) / "plane_histograms.csv").exists()


# 5. The stale-lock button ---------------------------------------------------------------


def test_a_stale_lock_is_removed_only_on_the_button(experiment):
    nb = ready(experiment)
    output = output_of(experiment)
    pid = finished_pid()
    hold_lock(output, pid)
    result = nb.run()
    assert not result.written and not result.report_written
    assert "Nothing was written." in nb.run_status.value
    assert ui.is_visible(nb.run_lock.card)
    holder = nb.run_lock.holder.value
    assert socket.gethostname() in holder and str(pid) in holder and "2026-11-02T10:00" in holder
    assert "one session per output folder" in nb.run_lock.card.children[2].value
    assert (output / LOCK_FILENAME).exists()  # nothing is removed without the click
    nb.run_lock.button.click()
    assert not (output / LOCK_FILENAME).exists()
    assert "The lock of the stopped run was removed" in nb.run_lock.result.value
    assert nb.run().written
    assert not ui.is_visible(nb.run_lock.card)


def test_the_button_removes_only_the_lock_it_names(experiment):
    """A lock taken by another run after the card was shown is named, not removed."""
    nb = ready(experiment)
    output = output_of(experiment)
    hold_lock(output, finished_pid())
    assert not nb.run().written
    hold_lock(output, 4242, host="another-computer")
    nb.run_lock.button.click()
    assert (output / LOCK_FILENAME).exists()
    assert "Nothing was removed" in nb.run_lock.result.value
    assert "another-computer" in nb.run_lock.holder.value and "4242" in nb.run_lock.holder.value
    nb.run_lock.button.click()  # the user has now seen this holder too
    assert not (output / LOCK_FILENAME).exists()


@pytest.mark.skipif(os.name != "posix", reason="only POSIX can tell a process runs")
def test_the_lock_of_a_process_running_here_is_kept(experiment):
    nb = checked(experiment)
    output = output_of(experiment)
    hold_lock(output, os.getpid())
    nb.check_button.click()
    assert ui.is_visible(nb.folder_lock.card)
    nb.folder_lock.button.click()
    assert (output / LOCK_FILENAME).exists()
    assert "The lock was kept" in nb.folder_lock.result.value
    assert str(os.getpid()) in nb.folder_lock.result.value


# 8. The preview of roles not confirmed yet ------------------------------------------------


def test_the_preview_says_whether_its_roles_are_confirmed(experiment):
    nb = checked(experiment)
    nb.preview_button.click()
    assert "Preview of the roles above, not confirmed yet" in nb.preview_caption.value
    assert "Nothing is written" in nb.preview_caption.value
    nb.preview_checked.button.click()
    nb.confirm_button.click()
    assert "Preview with the confirmed axis roles" in nb.preview_caption.value
