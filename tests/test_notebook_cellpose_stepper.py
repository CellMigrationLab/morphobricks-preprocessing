"""The Cellpose segmentation as a compact stepper (owner, 25 November 2026), driven headless.

``segmentation_app`` shows four steps one at a time with the Preprocessing app's stepper
(chips, Back, Next): Folders, Output and channels, Model and preview, Segment. These tests
press the widgets as a user would, with the stand-in Cellpose of ``tests/cellpose_stub.py``:
the whole flow, the folder refusal at step 1, the run's images in the preprocessing viewer,
Preview loading the model once and reusing it, the download shown as one ``tqdm.auto`` bar,
and that no *What to do next* step is left.
"""

import logging
import sys
import types

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


# the download: one tqdm.auto bar, nothing else changes -----------------------------------------


@pytest.fixture
def auto_bar(monkeypatch):
    """A stand-in ``tqdm.auto`` that records the bars made (tqdm comes with Cellpose)."""
    made = []

    class Bar(TextBar):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    auto = types.ModuleType("tqdm.auto")
    auto.tqdm = Bar
    package = types.ModuleType("tqdm")
    package.auto = auto
    monkeypatch.setitem(sys.modules, "tqdm", package)
    monkeypatch.setitem(sys.modules, "tqdm.auto", auto)
    return made


def test_a_download_shows_one_notebook_bar_and_the_same_weights(mixed_run, stub, auto_bar):
    (stub.models_dir / "cpsam").unlink()
    app = ready(mixed_run)
    fields = app.workflow.forms["model"].fields
    fields["model"].value, fields["download_weights"].value = "cpsam", True
    preview(app)
    assert stub.downloaded == ["cpsam"]
    assert stub.bars == [download.notebook_bar]  # Cellpose's bar, drawn by tqdm.auto
    (bar,) = auto_bar
    assert bar.kwargs["desc"] == download.BAR_LABEL and bar.kwargs["unit"] == "B"
    assert bar.done == len(b"weights of cpsam")
    assert sys.modules["cellpose.utils"].tqdm is TextBar  # put back
    assert app.workflow.loaded.downloaded and app.workflow.loaded.name == "cpsam"
    assert app.workflow.loaded.path == stub.models_dir / "cpsam"


def test_no_download_without_the_tick_even_with_the_quiet_display(mixed_run, stub, auto_bar):
    (stub.models_dir / "cpsam").unlink()
    app = ready(mixed_run)
    app.workflow.forms["model"].fields["model"].value = "cpsam"
    form = app.workflow.forms["preview"]
    form.button.click()
    assert "Allow the download on purpose" in form.refusal
    assert stub.downloaded == [] and auto_bar == [] and stub.constructed == []


def test_cellpose_logs_are_quiet_and_its_warnings_are_kept(stub, auto_bar):
    logger = logging.getLogger("cellpose")
    before = logger.propagate
    with download.quiet_cellpose() as said:
        logging.getLogger("cellpose.models").info("Downloading: noise")
        logging.getLogger("cellpose.models").warning("pretrained model x not found")
        assert sys.modules["cellpose.utils"].tqdm is download.notebook_bar
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


def test_without_tqdm_auto_cellposes_own_bar_is_left_as_it_is(stub, monkeypatch):
    monkeypatch.setitem(sys.modules, "tqdm.auto", None)  # import tqdm.auto fails
    with download.quiet_cellpose():
        assert sys.modules["cellpose.utils"].tqdm is TextBar
