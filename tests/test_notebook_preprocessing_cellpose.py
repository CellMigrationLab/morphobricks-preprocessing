"""Cells 4 and 5 of the combined notebook (#441) on a run of the real Preprocessing app.

The app (``PreprocessingNotebook`` with its seven steps, as cell 3 shows it) runs a small
experiment; cell 4 (:func:`.notebook_preprocessing_cellpose.segmentation_step`) segments that
run with the stand-in Cellpose of ``cellpose_stub.py`` (no GPU, no download) and cell 5
(:func:`.notebook_mask_register.register_step`) offers the declarations and registers them
through the app's own steps once the user answered what each mask is.
"""

import json
import os
import shutil
from types import SimpleNamespace

import pytest

from cellpose_stub import install
from test_notebook_check_save import FOLDERS, KINDS, add_folder, answer_all, write_channels

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing.axes import AxisMapping  # noqa: E402
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME  # noqa: E402
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME  # noqa: E402
from morphobricks_preprocessing.mask_scan import MASK_REPORT_FILENAME  # noqa: E402
from morphobricks_preprocessing.notebook_cellpose_app import app_folders  # noqa: E402
from morphobricks_preprocessing.notebook_mask_register import (  # noqa: E402
    AXES_AGAIN,
    NO_MASKS,
    STOPPED,
    register_step,
    toml_section,
)
from morphobricks_preprocessing.notebook_preprocessing_cellpose import (  # noqa: E402
    NO_RUN,
    RUN_REFUSED,
    segmentation_step,
)
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from morphobricks_preprocessing.run_inputs import RunInputs  # noqa: E402

# the per-unit grid driven through the stepper (the guided step is pinned by its own tests)
pytestmark = pytest.mark.usefixtures("legacy_grid")


def ran_app(tmp_path, *, with_masks=False):
    """Return the app of cell 3 after a finished run of four 3-channel images."""
    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, folder in enumerate(FOLDERS):
        add_folder(root, masks, folder, i + 1)  # also writes a Nuclei mask per image
    if not with_masks:
        shutil.rmtree(masks)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.welcome()
    nb.steps()
    if with_masks:
        nb.masks_folder.value = str(masks)
    nb.check_button.click()
    nb.quick_confirm.click()
    answer_all(nb)
    assert nb.save_experiment() and nb.run().written
    return nb


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def segment(step, masks_folder=None, mask_type="Cells"):
    """Open the run (typing the masks folder when given), choose, load and segment."""
    workflow = step.workflow
    opener = workflow.forms["open"]
    if masks_folder is not None:
        opener.fields["masks_folder"].value = str(masks_folder)
        opener.button.click()
    assert opener.refusal == "", opener.refusal
    chooser = workflow.forms["channels"]
    chooser.fields["mask_type"].value = mask_type
    chooser.button.click()
    workflow.picker.cell.value, workflow.picker.nuclear.value = "ACTIN", "DNA"
    workflow.forms["model"].fields["device"].value = "cpu"
    for name in ("model", "segment"):
        workflow.forms[name].button.click()
        assert workflow.forms[name].refusal == "", workflow.forms[name].refusal


# cell 4 ---------------------------------------------------------------------------------------


def test_cell_4_segments_the_run_the_app_just_finished(tmp_path, stub):
    nb = ran_app(tmp_path)
    step = segmentation_step(nb)
    assert step.run_folder == str(nb.result.output_folder)
    assert step.masks_folder == ""  # the run recorded no masks folder: nothing is guessed
    opener = step.workflow.forms["open"]
    assert opener.fields["run_folder"].value == str(nb.result.output_folder)
    segment(step, tmp_path / "masks")
    (batch,) = step.workflow.results.values()
    assert batch.counts()["written"] == len(FOLDERS)
    assert sorted(p.name for p in (tmp_path / "masks" / "Cells").rglob("*.tif")) == ["a.tif"] * 4


def test_the_masks_folder_is_the_apps_rule_not_what_its_box_says_now(tmp_path):
    nb = ran_app(tmp_path, with_masks=True)
    nb.masks_folder.value = str(tmp_path / "typed_after_the_run")
    assert app_folders(nb) == (str(nb.result.output_folder), str((tmp_path / "masks").resolve()))


def test_cell_4_without_a_finished_run_names_cell_3_and_guesses_nothing(tmp_path, stub):
    nb = PreprocessingNotebook(str(tmp_path / "exp"), output_folder=str(tmp_path / "out"))
    nb.welcome()
    nb.steps()
    step = segmentation_step(nb)
    assert NO_RUN in step.notice.value.replace("&#x27;", "'") and "cell 3" in NO_RUN
    fields = step.workflow.forms["open"].fields
    assert (fields["run_folder"].value, fields["masks_folder"].value) == ("", "")
    assert step.workflow.inputs is None


def test_cell_4_refuses_a_run_whose_images_changed_and_names_cell_3(tmp_path, stub):
    nb = ran_app(tmp_path, with_masks=True)
    image = tmp_path / "exp" / FOLDERS[0] / "a.tif"
    image.write_bytes(image.read_bytes()[:-1] + b"\x07")
    step = segmentation_step(nb)
    assert step.workflow.inputs is None
    assert step.workflow.forms["open"].refusal
    assert RUN_REFUSED in step.notice.value and "cell 3" in RUN_REFUSED


def test_cell_4_refuses_changed_images_even_when_the_run_has_no_masks_folder(tmp_path, stub):
    # Before, the run was opened only with both folders known, so nothing was said here.
    nb = ran_app(tmp_path)
    image = tmp_path / "exp" / FOLDERS[0] / "a.tif"
    image.write_bytes(image.read_bytes()[:-1] + b"\x07")
    step = segmentation_step(nb)
    assert step.masks_folder == "" and step.workflow.inputs is None
    assert RUN_REFUSED in step.notice.value and "changed" in step.notice.value
    assert step.notice.value.count(RUN_REFUSED) == 1


def test_the_line_after_the_batch_in_cell_4_names_cell_5_and_no_form_is_left(tmp_path, stub):
    step = segmentation_step(ran_app(tmp_path))
    segment(step, tmp_path / "masks")
    assert "next" not in step.workflow.forms  # no What to do next form (owner, 25 Nov 2026)
    shown = step.workflow.shown[-1]
    assert "Next: run cell 5 (Register the masks) to declare [masks.Cells]" in shown
    assert "step 4" not in shown and "Save the description" not in shown


# cell 5 ---------------------------------------------------------------------------------------


def test_cell_5_asks_what_each_mask_is_and_registers_only_once_answered(tmp_path, stub):
    nb = ran_app(tmp_path)
    out = nb.result.output_folder
    step = segmentation_step(nb)
    segment(step, tmp_path / "masks")
    registration = register_step(nb, step)
    assert list(registration.choices) == ["Cells"]
    section = toml_section("Cells")
    assert section == '[masks.Cells]\nsource_type = "Cells"\nrepresentation = "instances"'
    assert "[masks.Cells]" in repr(registration.children)
    assert registration.button.disabled and not registration.register()  # nothing silent
    assert "[masks" not in (out / DESCRIPTION_FILENAME).read_text(encoding="utf-8")
    registration.choices["Cells"].value = "instances"
    # v0.9.1: nothing changed since cell 3's check, so its confirmed axes are kept and the
    # masks are paired by the app's Check the masks again: one press registers them.
    assert registration.register() and registration.registered, registration.status.value
    assert AXES_AGAIN.split(":")[0] not in registration.status.value
    assert section in (out / DESCRIPTION_FILENAME).read_text(encoding="utf-8")
    assert nb.result.written and nb.result.read == () and nb.result.rebuild_reason is None
    manifest = json.loads((out / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert {e["type"] for e in manifest["masks"]["files"]} == {"Cells"}
    assert len(manifest["masks"]["files"]) == len(FOLDERS)
    assert (out / MASK_REPORT_FILENAME).is_file()
    assert RunInputs.open(out).mask_types() == ("Cells",)
    assert "The masks are registered" in registration.status.value


def test_cell_5_registers_masks_written_into_the_runs_own_masks_folder(tmp_path, stub):
    # B1: the run recorded a masks folder (Nuclei); cell 4 writes Cells masks into it. Before,
    # opening the run refused the new masks as unrecorded ("images changed since the run"),
    # and the app's folder check, already made with that folder, was not made again.
    nb = ran_app(tmp_path, with_masks=True)
    out = nb.result.output_folder
    step = segmentation_step(nb)
    assert step.masks_folder == str((tmp_path / "masks").resolve())
    segment(step)  # into the prefilled masks folder
    registration = register_step(nb, step)
    assert list(registration.choices) == ["Cells"], registration.status.value
    registration.choices["Cells"].value = "instances"
    assert registration.register(), registration.status.value  # one press (v0.9.1)
    manifest = json.loads((out / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert sorted(manifest["masks"]["types"]) == ["Cells", "Nuclei"]
    assert RunInputs.open(out).mask_types() == ("Cells", "Nuclei")


def stopped_for_the_axes(tmp_path, stub):
    """Return the app and cell 5 after its first press stopped for the axes, and the output.

    Since v0.9.1 the press stops only when something changed since cell 3's check: here the
    user took the axes confirmation back in cell 3 (Change axis roles).
    """
    nb = ran_app(tmp_path)
    step = segmentation_step(nb)
    segment(step, tmp_path / "masks")
    registration = register_step(nb, step)
    registration.choices["Cells"].value = "instances"
    nb.confirm_button.click()  # Change axis roles: the axes are no longer confirmed
    assert nb.confirmed_mapping is None
    assert not registration.register()
    assert AXES_AGAIN.split(":")[0] in registration.status.value
    assert "the axes are not confirmed in cell 3" in registration.status.value  # says why
    return nb, registration, nb._output_path(tmp_path / "exp")


def snapshot(folder):
    return {p: p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_cell_5_run_again_after_the_axes_stop_continues(tmp_path, stub):
    # Before, a new widget saw the app's run cleared by the folder check and refused OTHER_RUN.
    nb, registration, out = stopped_for_the_axes(tmp_path, stub)
    nb.quick_confirm.click()
    again = register_step(nb, registration.segmentation)  # cell 5 run again
    assert list(again.choices) == ["Cells"], again.status.value
    again.choices["Cells"].value = "instances"
    assert again.register(), again.status.value
    assert RunInputs.open(out).mask_types() == ("Cells",)


def test_cell_5_refuses_axes_other_than_those_the_segmented_run_recorded(tmp_path, stub):
    # B2: after the axes stop, only "some axes are confirmed" was checked.
    nb, registration, out = stopped_for_the_axes(tmp_path, stub)
    before = snapshot(out)
    recorded = json.loads((out / MANIFEST_FILENAME).read_text(encoding="utf-8"))["axis_mapping"]
    assert recorded == {"axes": "CYX", "roles": ["C", "Y", "X"]}
    nb.quick_confirm.click()
    # Other roles than the run's: the app refuses Y and X swapped for these TIFFs (their header
    # names the axes), but not for images without axes in their header, so set them directly.
    nb.confirmed_mapping = AxisMapping("CYX", ("C", "X", "Y"))
    assert not registration.register() and not registration.registered
    assert "not those the segmented run recorded" in registration.status.value
    assert snapshot(out) == before  # nothing declared, saved or run


def test_cell_5_refuses_an_app_now_on_another_experiment(tmp_path, stub):
    nb, registration, out = stopped_for_the_axes(tmp_path, stub)
    other = tmp_path / "other_exp"
    shutil.copytree(tmp_path / "exp", other)
    nb.folder.value = str(other)  # cell 3 now checks another experiment, same output
    nb.check_button.click()
    nb.quick_confirm.click()
    before = snapshot(out)  # after cell 3's own check (it writes its check report)
    assert not registration.register() and not registration.registered
    assert "another experiment" in registration.status.value
    assert snapshot(out) == before


# cell 5 refuses rather than guesses ----------------------------------------------------------


def segmented(tmp_path, nb, masks, mask_type="Cells"):
    """Return a stand-in of cell 4 that wrote ``mask_type`` into ``masks`` for the app's run."""
    batch = SimpleNamespace(counts=lambda: {"written": 4, "skipped": 0})
    inputs = RunInputs.open(nb.result.output_folder)
    workflow = SimpleNamespace(
        results={mask_type: batch},
        inputs=inputs,
        masks_root=masks,
        run_manifest_sha256=inputs.manifest_sha256,
    )
    return SimpleNamespace(workflow=workflow)


def refused(registration):
    assert registration.choices == {}
    return registration.status.value.replace("&#x27;", "'")


def test_no_masks_written_in_cell_4_is_refused(tmp_path):
    nb = ran_app(tmp_path)
    assert NO_MASKS in refused(register_step(nb, SimpleNamespace(workflow=None)))


def test_a_mask_type_that_collides_with_a_declaration_is_refused(tmp_path):
    nb = ran_app(tmp_path, with_masks=True)
    nb.mask_card.questions["Nuclei"].choice.value = "binary"  # Nuclei declared as regions
    assert nb.save_experiment() and nb.run().written
    text = refused(register_step(nb, segmented(tmp_path, nb, tmp_path / "masks", "nuclei")))
    assert "collides with the mask Nuclei" in text


def test_a_masks_folder_inside_the_experiment_is_refused(tmp_path):
    nb = ran_app(tmp_path)
    inside = tmp_path / "exp" / "masks"
    text = refused(register_step(nb, segmented(tmp_path, nb, inside)))
    assert "inside the experiment" in text


def test_masks_of_another_masks_folder_than_the_runs_are_refused(tmp_path):
    nb = ran_app(tmp_path, with_masks=True)
    other = tmp_path / "other_masks"
    other.mkdir()
    text = refused(register_step(nb, segmented(tmp_path, nb, other)))
    assert "uses the masks folder" in text


def test_a_folder_differing_only_in_case_from_a_written_type_is_refused(tmp_path):
    # #459: cells/ appeared in the masks folder beside the Cells masks cell 4 wrote; on
    # Windows and macOS they are one folder. Refused naming both, nothing declared or renamed.
    nb = ran_app(tmp_path)
    masks = tmp_path / "masks"
    (masks / "cells").mkdir(parents=True)
    text = refused(register_step(nb, segmented(tmp_path, nb, masks))).replace("&quot;", '"')
    assert "mask_type_differs_in_case" in text and '"cells"' in text and '"Cells"' in text
    assert sorted(p.name for p in masks.iterdir()) == ["cells"]
    assert "[masks" not in (nb.result.output_folder / DESCRIPTION_FILENAME).read_text("utf-8")


def test_images_changed_since_the_run_are_refused(tmp_path):
    nb = ran_app(tmp_path)
    segmentation = segmented(tmp_path, nb, tmp_path / "masks")
    image = tmp_path / "exp" / FOLDERS[0] / "a.tif"
    image.write_bytes(image.read_bytes()[:-1] + b"\x07")
    assert "The images changed since the run" in refused(register_step(nb, segmentation))


def test_files_the_run_did_not_record_besides_cell_4s_masks_are_still_refused(tmp_path):
    nb = ran_app(tmp_path, with_masks=True)
    segmentation = segmented(tmp_path, nb, tmp_path / "masks")
    image = tmp_path / "exp" / FOLDERS[0] / "a.tif"
    shutil.copyfile(image, image.with_name("b.tif"))  # an image added since the run
    assert "did not record" in refused(register_step(nb, segmentation))


def test_cell_5_refuses_masks_of_a_run_cell_3_published_again_after_cell_4(tmp_path, stub):
    # Review of the #458 merge: cell 5 checked the run as it is now, not the one cell 4
    # segmented. Cell 3 run again after cell 4 (here on a changed image) published another
    # run at the same folder, which records the new image; its masks were made on the old
    # one, yet they were offered and registered in the new run.
    nb = ran_app(tmp_path)
    out = nb.result.output_folder
    step = segmentation_step(nb)
    segment(step, tmp_path / "masks")
    image = tmp_path / "exp" / FOLDERS[0] / "a.tif"
    write_channels(image, ["DAPI", "Actin", "Ch3"], KINDS, seed=99)  # other pixels
    stat = image.stat()
    os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**10))
    nb.check_button.click()
    nb.quick_confirm.click()
    assert nb.run().written  # cell 3 published again (the description is unchanged)
    assert nb.result.read  # the changed image was read again
    before = snapshot(out)
    text = refused(register_step(nb, step))
    assert "published again since cell 4 segmented it" in text
    assert snapshot(out) == before  # nothing declared, saved or run


def test_cell_5_run_again_after_it_registered_the_masks_is_not_refused(tmp_path, stub):
    # Cell 5's own run publishes the run again: that publication is the registration's.
    nb, registration, out = stopped_for_the_axes(tmp_path, stub)
    nb.quick_confirm.click()
    assert registration.register(), registration.status.value
    again = register_step(nb, registration.segmentation)
    assert list(again.choices) == ["Cells"], again.status.value


def pressed_with_a_failed_check(tmp_path, monkeypatch, error):
    """Return the app, cell 5 and the run after a press whose folder check raised ``error``."""
    from morphobricks_preprocessing import notebook_folder

    nb = ran_app(tmp_path)
    out = nb.result.output_folder
    step = segmentation_step(nb)
    segment(step, tmp_path / "masks")
    registration = register_step(nb, step)
    registration.choices["Cells"].value = "instances"
    nb.confirm_button.click()  # a change since cell 3's check: the press checks the folders
    real = notebook_folder.preflight

    def failing(*args, **kwargs):
        raise error

    monkeypatch.setattr(notebook_folder, "preflight", failing)
    before = snapshot(out)
    return nb, registration, out, before, real


def test_a_failed_folder_check_says_so_in_cell_5_not_the_axes(tmp_path, stub, monkeypatch):
    # Before, cell 5 said to confirm the axes again (there was no check to confirm), the
    # reason was drawn only in cell 3, and every later press raised AttributeError out of the
    # click handler with the axes line left on screen.
    from morphobricks_preprocessing import notebook_folder

    nb, registration, out, before, real = pressed_with_a_failed_check(
        tmp_path, monkeypatch, OSError("Drive stopped answering")
    )
    assert not registration.register()
    status = registration.status.value
    assert "did not finish" in status and "Drive stopped answering" in status
    assert AXES_AGAIN.split(":")[0] not in status
    assert snapshot(out) == before  # nothing declared, saved or run
    assert not registration.register()  # pressed again: the same refusal, no exception
    assert "did not finish" in registration.status.value
    again = register_step(nb, registration.segmentation)  # cell 5 run again
    assert "did not finish" in again.status.value
    monkeypatch.setattr(notebook_folder, "preflight", real)  # Drive answers again
    nb.check_button.click()  # cell 3's check, as the refusal says; the axes, then Register
    nb.quick_confirm.click()
    assert registration.register(), registration.status.value
    assert RunInputs.open(out).mask_types() == ("Cells",)


def test_an_unexpected_error_during_register_is_shown_in_cell_5(tmp_path, stub, monkeypatch):
    _nb, registration, out, before, _real = pressed_with_a_failed_check(
        tmp_path, monkeypatch, RuntimeError("unexpected")
    )
    with pytest.raises(RuntimeError):
        registration.register()
    assert STOPPED.split("(")[0] in registration.status.value
    assert "RuntimeError: unexpected" in registration.status.value
    assert snapshot(out) == before
