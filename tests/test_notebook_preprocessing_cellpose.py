"""Cells 4 and 5 of the combined notebook (#441) on a run of the real Preprocessing app.

The app (``PreprocessingNotebook`` with its seven steps, as cell 3 shows it) runs a small
experiment; cell 4 (:func:`.notebook_preprocessing_cellpose.segmentation_step`) segments that
run with the stand-in Cellpose of ``cellpose_stub.py`` (no GPU, no download) and cell 5
(:func:`.notebook_mask_register.register_step`) offers the declarations and registers them
through the app's own steps once the user ticked them.
"""

import json
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from cellpose_stub import install
from test_notebook_check_save import FOLDERS, add_folder, answer_all

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import cellpose_install  # noqa: E402
from morphobricks_preprocessing import segmentation_cellpose_model as model  # noqa: E402
from morphobricks_preprocessing.axes import AxisMapping  # noqa: E402
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME  # noqa: E402
from morphobricks_preprocessing.manifest import MANIFEST_FILENAME  # noqa: E402
from morphobricks_preprocessing.mask_scan import MASK_REPORT_FILENAME  # noqa: E402
from morphobricks_preprocessing.notebook_cellpose_app import app_folders  # noqa: E402
from morphobricks_preprocessing.notebook_mask_register import (  # noqa: E402
    AXES_AGAIN,
    NO_MASKS,
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


# installing Cellpose on first use ------------------------------------------------------------


@pytest.fixture
def pip(monkeypatch, stub):
    """Cellpose not installed; ``pip`` installs it (and may change numpy) when it runs."""
    state = SimpleNamespace(commands=[], numpy=None)
    stub.version = None
    real = model.installed_version

    def version(name):
        if name == "cellpose":
            return stub.version
        if name == "numpy" and state.numpy:
            return state.numpy[1 if state.commands else 0]
        return real(name)

    def run(command, check):
        state.commands.append(command)
        stub.version = "4.2.1.1"

    monkeypatch.setattr(model, "installed_version", version)
    monkeypatch.setattr(cellpose_install.subprocess, "run", run)
    return state


def test_cellpose_is_installed_only_when_ticked_then_the_component_appears(tmp_path, pip):
    step = segmentation_step(ran_app(tmp_path))
    assert step.segmentation is None and step.install_button.disabled
    assert "tick the installation" in step.install_blocker.value
    assert not step.install()  # a press without the tick installs nothing
    assert pip.commands == [] and "Tick the installation first" in step.install_status.value
    step.install_tick.value = True
    assert not step.install_button.disabled and step.install()
    (command,) = pip.commands
    assert command[1:4] == ["-m", "pip", "install"] and command[-1] == "cellpose==4.2.1.1"
    assert step.segmentation is not None and step.workflow.inputs is None  # no masks folder yet


def test_an_install_that_changes_a_loaded_numpy_asks_for_a_restart(tmp_path, pip):
    pip.numpy = ("2.0.0", "2.3.0")
    step = segmentation_step(ran_app(tmp_path))
    step.install_tick.value = True
    assert not step.install()
    assert "numpy 2.0.0 -&gt; 2.3.0" in step.install_status.value
    assert "Restart the runtime" in step.install_status.value and step.segmentation is None


def test_preprocessing_alone_never_imports_cellpose_or_torch():
    code = (
        "import sys\n"
        "from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook\n"
        "nb = PreprocessingNotebook()\n"
        "nb.welcome(); nb.steps(); nb.results_step()\n"
        "names = ('cellpose', 'torch', 'morphobricks_preprocessing.cellpose_install',\n"
        "         'morphobricks_preprocessing.notebook_preprocessing_cellpose')\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in names[:2] or m in names))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# cell 5 ---------------------------------------------------------------------------------------


def test_cell_5_offers_the_declaration_and_registers_only_after_the_tick(tmp_path, stub):
    nb = ran_app(tmp_path)
    out = nb.result.output_folder
    step = segmentation_step(nb)
    segment(step, tmp_path / "masks")
    registration = register_step(nb, step)
    assert list(registration.ticks) == ["Cells"]
    section = toml_section("Cells")
    assert section == '[masks.Cells]\nsource_type = "Cells"\nrepresentation = "instances"'
    assert "[masks.Cells]" in repr(registration.children)
    assert registration.button.disabled and not registration.register()  # nothing silent
    assert "[masks" not in (out / DESCRIPTION_FILENAME).read_text(encoding="utf-8")
    registration.ticks["Cells"].value = True
    # The app's folder check with the masks folder asks for the axes again: the user's click.
    assert not registration.register()
    assert AXES_AGAIN.split(":")[0] in registration.status.value
    nb.quick_confirm.click()
    assert registration.register() and registration.registered
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
    assert list(registration.ticks) == ["Cells"], registration.status.value
    registration.ticks["Cells"].value = True
    assert not registration.register()  # the folder check is made again: axes to confirm
    assert AXES_AGAIN.split(":")[0] in registration.status.value
    nb.quick_confirm.click()
    assert registration.register(), registration.status.value
    manifest = json.loads((out / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert sorted(manifest["masks"]["types"]) == ["Cells", "Nuclei"]
    assert RunInputs.open(out).mask_types() == ("Cells", "Nuclei")


def stopped_for_the_axes(tmp_path, stub):
    """Return the app and cell 5 after its first press stopped for the axes, and the output."""
    nb = ran_app(tmp_path)
    step = segmentation_step(nb)
    segment(step, tmp_path / "masks")
    registration = register_step(nb, step)
    registration.ticks["Cells"].value = True
    assert not registration.register()
    assert AXES_AGAIN.split(":")[0] in registration.status.value
    return nb, registration, nb._output_path(tmp_path / "exp")


def snapshot(folder):
    return {p: p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_cell_5_run_again_after_the_axes_stop_continues(tmp_path, stub):
    # Before, a new widget saw the app's run cleared by the folder check and refused OTHER_RUN.
    nb, registration, out = stopped_for_the_axes(tmp_path, stub)
    nb.quick_confirm.click()
    again = register_step(nb, registration.segmentation)  # cell 5 run again
    assert list(again.ticks) == ["Cells"], again.status.value
    again.ticks["Cells"].value = True
    assert again.register(), again.status.value
    assert RunInputs.open(out).mask_types() == ("Cells",)


def test_cell_5_refuses_axes_other_than_those_the_segmented_run_recorded(tmp_path, stub):
    # B2: after the axes stop, only "some axes are confirmed" was checked.
    nb, registration, out = stopped_for_the_axes(tmp_path, stub)
    before = snapshot(out)
    recorded = json.loads((out / MANIFEST_FILENAME).read_text(encoding="utf-8"))["axis_mapping"]
    roles = recorded["roles"]
    swapped = [*roles[:-2], roles[-1], roles[-2]]  # Y and X swapped
    nb.confirmed_mapping = AxisMapping(recorded["axes"], tuple(swapped))
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
    workflow = SimpleNamespace(
        results={mask_type: batch}, inputs=RunInputs.open(nb.result.output_folder), masks_root=masks
    )
    return SimpleNamespace(workflow=workflow)


def refused(registration):
    assert registration.ticks == {}
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
