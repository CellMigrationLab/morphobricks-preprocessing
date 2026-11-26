"""The Cellpose segmentation as a compact stepper (owner, 25 November 2026), driven headless.

``segmentation_app`` shows four steps one at a time with the Preprocessing app's stepper
(chips, Back, Next): Folders, Output and channels, Model and preview, Segment. These tests
press the widgets as a user would, with the stand-in Cellpose of ``tests/cellpose_stub.py``:
the whole flow, the folder refusal at step 1, the run's images in the preprocessing viewer,
Preview loading the model once and reusing it, the first-use download shown as one line (#574),
and that no *What to do next* step is left.
"""

import html
import logging
import sys
import urllib.error

import pytest

from cellpose_stub import TextBar, install
from morphobricks_preprocessing import notebook_cellpose_download as download
from morphobricks_preprocessing.notebook_cellpose_app import PAGES, STEPS, segmentation_app
from morphobricks_preprocessing.notebook_cellpose_form import CellposeNotebook
from morphobricks_preprocessing.notebook_style import is_visible
from morphobricks_preprocessing.segmentation_record import read_record


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def walk(widget):
    yield widget
    for child in getattr(widget, "children", ()):
        yield from walk(child)


def texts(widget):
    return " ".join(str(getattr(w, "value", "")) for w in walk(widget) if hasattr(w, "value"))


def page_shown(app):
    return [is_visible(page) for page in app.stepper.pages].index(True)


def open_run(app, run, masks):
    opener = app.workflow.forms["open"]
    opener.fields["run_folder"].value, opener.fields["masks_folder"].value = str(run), str(masks)
    opener.button.click()
    return opener


def choose(app, cell="TUB", nuclear="DNA", output="cells"):
    form = app.workflow.forms["channels"]
    form.fields["output"].value = output
    form.button.click()
    assert form.refusal == "", form.refusal
    app.workflow.picker.cell.value, app.workflow.picker.nuclear.value = cell, nuclear


def preview(app, image=None):
    form = app.workflow.forms["preview"]
    if image is not None:
        form.fields["preview_image"].value = image
    form.button.click()
    assert form.refusal == "", form.refusal


def ready(mixed_run):
    """An app on step 3 with the run open, the channels chosen and the device cpu."""
    app = segmentation_app()
    open_run(app, mixed_run.out, mixed_run.masks)
    choose(app)
    app.workflow.forms["model"].fields["device"].value = "cpu"
    return app


# the flow -------------------------------------------------------------------------------------


def test_the_four_steps_are_clicked_through_with_next_and_write_the_masks(mixed_run, stub):
    app = segmentation_app()
    assert [p[0] for p in PAGES] == ["Folders", "Output and channels", "Model and preview",
                                     "Segment"]  # fmt: skip
    assert [c.description for c in app.stepper.chips][0] == "1 Folders"
    assert page_shown(app) == 0 and not is_visible(app.stepper.back)

    opener = open_run(app, mixed_run.out, mixed_run.masks)  # step 1
    assert opener.refusal == "" and app.workflow.inputs is not None
    assert app.stepper.chips[0].description.startswith("✓")
    app.stepper.next.click()
    assert page_shown(app) == 1

    choose(app)  # step 2
    app.stepper.next.click()
    assert page_shown(app) == 2

    model = app.workflow.forms["model"]  # step 3: no Load button, Preview loads
    assert not is_visible(model.button)
    model.fields["device"].value = "cpu"
    preview(app)
    assert app.workflow.loaded is not None and len(stub.constructed) == 1
    app.stepper.next.click()
    assert page_shown(app) == 3 and not is_visible(app.stepper.next)

    segment = app.workflow.forms["segment"]  # step 4
    segment.button.click()
    assert segment.refusal == "", segment.refusal
    assert len(stub.constructed) == 1  # the model of the preview, reused
    assert read_record(mixed_run.masks, "Cells").batches
    assert app.workflow.results["Cells"].counts()["written"] == len(mixed_run.inputs.fovs())
    assert app.workflow.shown[-1].count("Next: declare [masks.Cells]") == 1
    assert all(state.done for state in app.stepper.states())


def test_back_and_next_keep_every_answer(mixed_run, stub):
    app = segmentation_app()
    open_run(app, mixed_run.out, mixed_run.masks)
    app.workflow.forms["channels"].fields["mask_type"].value = "MyCells"  # before choosing
    choose(app)
    app.workflow.forms["model"].fields["diameter"].value = "17"
    app.stepper.go(2)
    app.stepper.back.click()
    app.stepper.back.click()
    assert page_shown(app) == 0
    app.stepper.next.click()
    app.stepper.next.click()
    assert app.workflow.forms["channels"].fields["mask_type"].value == "MyCells"
    assert app.workflow.forms["model"].fields["diameter"].value == "17"
    assert (app.workflow.picker.cell.value, app.workflow.picker.nuclear.value) == ("TUB", "DNA")
    assert app.workflow.inputs is not None


def test_a_later_step_says_what_it_waits_for(mixed_run, stub):
    app = segmentation_app()
    app.stepper.go(1)
    assert "Open the run in step 1" in app.stepper.waits[1].value
    open_run(app, mixed_run.out, mixed_run.masks)
    app.stepper.go(2)
    assert "Choose the channels in step 2" in app.stepper.waits[2].value
    assert app.workflow.forms["preview"].button.disabled
    choose(app)
    app.stepper.go(2)
    assert app.stepper.waits[2].value == "" and not app.workflow.forms["preview"].button.disabled


# step 1: the folders, checked there, and the run's images ---------------------------------------


@pytest.mark.parametrize(
    ("where", "code"),
    [
        ("run", "output_inside_masks"),  # the masks folder is the run's output folder
        ("experiment", "masks_inside_experiment"),  # the masks folder is the data folder
        ("inside_run", "inside the output folder"),  # nested in the run's output folder
        ("inside_experiment", "masks_inside_experiment"),
        ("around", "experiment_inside_masks"),  # holding the experiment and the run
    ],
)
def test_step_1_refuses_a_masks_folder_that_is_or_nests_with_the_run_or_data(
    mixed_run, stub, where, code
):
    masks = {
        "run": mixed_run.out,
        "experiment": mixed_run.experiment,
        "inside_run": mixed_run.out / "masks",
        "inside_experiment": mixed_run.experiment / "masks",
        "around": mixed_run.out.parent,
    }[where]
    app = segmentation_app()
    opener = open_run(app, mixed_run.out, masks)
    assert code in opener.refusal
    assert app.workflow.inputs is None and app.workflow.viewer.entries == ()
    assert not app.stepper.chips[0].description.startswith("✓")
    assert not (masks / "Cells").exists()  # nothing written


def test_step_1_shows_the_runs_images_with_the_preprocessing_viewer(mixed_run, stub):
    app = segmentation_app(mixed_run.out, mixed_run.masks)
    viewer = app.workflow.viewer
    paths = [f.relative_path for f in mixed_run.inputs.fovs()]
    assert [e.image for e in viewer.entries] == paths
    assert is_visible(viewer.widget) and viewer.widget in list(walk(app.stepper.pages[0]))
    assert viewer.view is not None and viewer.tiles.children  # drawn by the shared reader
    first = mixed_run.inputs.manifest.file(viewer.image)
    assert all(c in texts(viewer.tiles) for c in first.channel_ids)  # captioned by channel ID
    viewer.select(paths[-1])
    assert viewer.image == paths[-1] and viewer.view is not None


def test_step_1_does_not_draw_an_image_changed_since_the_run(mixed_run, stub):
    app = segmentation_app(mixed_run.out, mixed_run.masks)
    viewer = app.workflow.viewer
    image = mixed_run.experiment / viewer.image
    image.write_bytes(image.read_bytes()[:-1] + b"\x07")
    viewer.draw()
    assert viewer.view is None and "No picture" in viewer.caption.value


# step 3: Preview loads the model once --------------------------------------------------------


def test_preview_loads_the_model_once_and_another_image_reuses_it(mixed_run, stub):
    app = ready(mixed_run)
    paths = [f.relative_path for f in mixed_run.inputs.fovs()]
    preview(app, paths[0])
    assert len(stub.constructed) == 1 and len(stub.calls) == 1
    assert f"Preview of {paths[0]}" in app.workflow.shown[-1]
    stub.calls.clear()
    preview(app, paths[2])  # the preview image switch
    assert len(stub.constructed) == 1, "another image must not reload the model"
    assert len(stub.calls) == 1 and f"Preview of {paths[2]}" in app.workflow.shown[-1]
    app.workflow.forms["model"].fields["diameter"].value = "12"  # a setting, not the model
    preview(app, paths[2])
    assert len(stub.constructed) == 1 and stub.calls[-1].kwargs["diameter"] == 12


@pytest.mark.parametrize(
    ("field", "value"),
    [("model", "cpsam"), ("device", "cuda"), ("custom_model_file", "custom")],
)
def test_changing_what_loading_depends_on_reloads_at_the_next_preview(
    mixed_run, stub, tmp_path, field, value
):
    from cellpose_stub import custom_model

    stub.cuda = True
    stub.params = None
    app = ready(mixed_run)
    preview(app)
    assert len(stub.constructed) == 1
    if value == "custom":
        value = str(custom_model(tmp_path))
    app.workflow.forms["model"].fields[field].value = value
    preview(app)
    assert len(stub.constructed) == 2
    assert app.workflow.loaded.device == app.workflow.forms["model"].fields["device"].value
    app.workflow.forms["segment"].button.click()
    assert app.workflow.forms["segment"].refusal == ""
    assert len(stub.constructed) == 2  # Segment reuses the model the preview loaded


def test_an_unavailable_gpu_is_refused_at_preview_not_replaced(mixed_run, stub):
    app = ready(mixed_run)
    app.workflow.forms["model"].fields["device"].value = "cuda"
    form = app.workflow.forms["preview"]
    form.button.click()
    assert "Nothing falls back" in form.refusal and app.workflow.loaded is None
    assert stub.constructed == []


def test_the_nuclei_box_shows_for_both_only(mixed_run, stub):
    app = ready(mixed_run)
    box = app.workflow._nuclei_box
    assert not is_visible(box)
    choose(app, output="both")
    assert is_visible(box) and "Output Both: the nuclei" in texts(box)


# the download on first use (#574): one "Downloading <model> weights…" line, no tick ----------


def test_preview_downloads_the_chosen_models_weights_with_a_visible_line(mixed_run, stub):
    (stub.models_dir / "cpsam").unlink()
    app = ready(mixed_run)
    fields = app.workflow.forms["model"].fields
    assert "download_weights" not in fields and "install_dinov3" not in fields
    fields["model"].value = "cpsam"
    preview(app)
    assert stub.downloaded == ["cpsam"] and stub.cellpose_downloaded == []
    shown = " ".join(app.workflow.shown)
    assert "Downloading cpsam weights…" in shown  # the line as first shown
    assert app.workflow.loaded.downloaded and app.workflow.loaded.name == "cpsam"
    assert app.workflow.loaded.path == stub.models_dir / "cpsam"
    preview(app)  # loaded once, then reused: nothing downloads again
    assert stub.downloaded == ["cpsam"]


def test_the_download_line_counts_megabytes_and_says_how_it_ended():
    shown = []
    line = download.DownloadLine(shown.append)
    line.finish(True)
    assert shown == [] and line.widget.value == ""  # nothing to say: nothing shown
    mb = download.MEGABYTE
    line("Downloading cpsam weights…", 0, 300 * mb)
    assert shown == [line.widget]
    line("Downloading cpsam weights…", 300 * mb, 300 * mb)
    value = html.unescape(line.widget.value)
    assert "Downloading cpsam weights…" in value and "300 of 300" in value and "MB" in value
    line.finish(True)
    assert (
        "Downloading cpsam weights: done in" in line.widget.value and "mb-ok" in line.widget.value
    )
    now = [0.0]
    stopped = download.DownloadLine(shown.append, clock=lambda: now[0])
    stopped("Downloading cpdino weights…", 0, 0)
    now[0] = 1.0
    stopped("Downloading cpdino weights…", 5 * mb, 0)  # no size from the server: MB so far
    assert "5 so far" in stopped.widget.value
    stopped.finish(False)
    assert "stopped after" in stopped.widget.value and "mb-error" in stopped.widget.value


def test_a_failed_download_on_preview_is_a_refusal_and_nothing_loads(mixed_run, stub):
    (stub.models_dir / "cpsam").unlink()
    stub.download_fail = urllib.error.URLError("blocked")
    app = ready(mixed_run)
    app.workflow.forms["model"].fields["model"].value = "cpsam"
    form = app.workflow.forms["preview"]
    form.button.click()
    assert "weights of cpsam could not be downloaded from https://huggingface.co/" in form.refusal
    assert stub.constructed == [] and app.workflow.loaded is None and stub.calls == []


def test_cellpose_logs_are_quiet_and_its_warnings_are_kept(stub):
    logger = logging.getLogger("cellpose")
    before = logger.propagate
    with download.quiet_cellpose() as said:
        logging.getLogger("cellpose.models").info("Downloading: noise")
        logging.getLogger("cellpose.models").warning("pretrained model x not found")
    assert said == ["Warning: pretrained model x not found"]
    assert logger.propagate is before and sys.modules["cellpose.utils"].tqdm is TextBar


# What to do next is gone -----------------------------------------------------------------------


def test_there_is_no_what_to_do_next_step(mixed_run, stub):
    assert "next" not in STEPS and not hasattr(CellposeNotebook, "next_form")
    app = ready(mixed_run)
    preview(app)
    app.workflow.forms["segment"].button.click()
    assert "What to do next" not in texts(app) and "Show the next steps" not in texts(app)
    assert not any("What to do next" in (getattr(w, "description", "") or "") for w in walk(app))


# review of #548 ---------------------------------------------------------------------------------


def test_a_model_file_overwritten_at_the_same_path_is_loaded_again(mixed_run, stub, tmp_path):
    # M1: the path alone is not the model; its size and modification time are part of it.
    from cellpose_stub import custom_model

    path = custom_model(tmp_path)
    app = ready(mixed_run)
    app.workflow.forms["model"].fields["custom_model_file"].value = str(path)
    preview(app)
    first = app.workflow.loaded.sha256
    preview(app)
    assert len(stub.constructed) == 1  # the same file: reused
    path.write_bytes(b"other weights, retrained")
    preview(app)
    assert len(stub.constructed) == 2 and app.workflow.loaded.sha256 != first
    app.workflow.forms["segment"].button.click()
    assert len(stub.constructed) == 2  # Segment uses the file as it was just loaded


def summary(app):
    return html.unescape(app.workflow._summary.value)


def test_segment_says_what_it_will_use_and_what_differs_from_the_last_preview(mixed_run, stub):
    # M2: the line on the Segment step; a difference is shown, not blocked.
    app = ready(mixed_run)
    assert app.workflow._summary in list(walk(app.stepper.pages[3]))
    line = summary(app)
    assert "Model cpsam_v2 on cpu · diameter native scale · flow 0.4 · cellprob 0 · batch 8" in line
    assert "not previewed yet" in line
    preview(app)
    assert "what the last Preview used" in summary(app)
    fields = app.workflow.forms["model"].fields
    fields["diameter"].value, fields["cellprob_threshold"].value = "60", -1.0
    line = summary(app)
    assert "mb-warn" in app.workflow._summary.value and "diameter 60" in line
    assert "diameter: native scale → 60" in line and "cellprob: 0 → -1" in line
    segment = app.workflow.forms["segment"]
    segment.button.click()
    assert segment.refusal == ""  # not blocked
    batch = read_record(mixed_run.masks, "Cells").batches[-1]
    assert batch.parameters["diameter"] == 60 and batch.parameters["cellprob_threshold"] == -1


def test_the_line_names_the_nuclei_model_and_settings_of_both(mixed_run, stub):
    app = ready(mixed_run)
    choose(app, output="both")
    fields = app.workflow.forms["model"].fields
    fields["separate_nuclei"].value = True
    fields["nuclei_model"].value, fields["nuclei_diameter"].value = "cpsam", "9"
    line = summary(app)
    assert "nuclei model cpsam" in line and "nuclei diameter 9" in line


def test_the_model_chip_is_not_done_once_the_model_changes(mixed_run, stub):
    # L1: the strip says the loaded model is no longer the one shown.
    app = ready(mixed_run)
    preview(app)
    assert app.stepper.states()[2].done
    app.workflow.forms["model"].fields["model"].value = "cpsam"
    state = app.stepper.states()[2]
    assert not state.done and "Preview loads it" in state.text
    assert not app.stepper.chips[2].description.startswith("✓")
    preview(app)
    assert app.stepper.states()[2].done and "cpsam" in app.stepper.states()[2].text


def test_cellposes_unpinned_dinov3_advice_becomes_the_notebooks_pinned_install(mixed_run, stub):
    # L2: Cellpose logs "run pip install git+.../dinov3" (unpinned) when it cannot import
    # DINOv3; the card points to the notebook's own pinned installation instead.
    from cellpose_stub import CPDINO_WARNING

    stub.cpdino_log = True
    app = ready(mixed_run)
    preview(app)
    (card,) = [s for s in app.workflow.shown if "Cellpose said" in s]
    assert "pip install" not in card and "git+" not in card
    assert "installs the pinned commit" in html.unescape(card) and "restart" in card
    with download.quiet_cellpose() as said:
        logging.getLogger("cellpose.models").warning(CPDINO_WARNING)
    assert said == ["Warning: " + download.CPDINO_NOTE]


def test_the_stepper_never_says_to_load_the_model_by_hand(mixed_run, stub):
    # L3: no Load button in the stepper, so a changed choice names Preview.
    app = ready(mixed_run)
    app.workflow.forms["channels"].fields["output"].value = "nuclei"
    text = texts(app.workflow.forms["channels"].body)
    assert "then press Preview (it loads the model)" in text and "then load the model" not in text


def test_the_help_about_the_folders_the_mask_type_and_both_is_shown_in_full(mixed_run, stub):
    # L4: the stepper's style cuts a card's help to one line; these three are never cut.
    app = ready(mixed_run)
    wanted = ("not the run folder or the experiment", "The mask type is the masks' folder name",
              "writes the cells and the nuclei")  # fmt: skip
    for needle in wanted:
        (box,) = [w for w in walk(app) if needle in str(getattr(w, "value", ""))]
        assert "-webkit-line-clamp:unset" in box.value and "display:block" in box.value
