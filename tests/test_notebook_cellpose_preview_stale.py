"""Every Preview shows its own result only: nothing of an earlier Preview stays (owner, 26 Nov).

The owner's Colab report: some nuclei results of the first Preview were not cleared after
pressing Preview several times. The result was displayed into the step's output area, and
its clearing was left to the frontend; a Preview that failed (the model, or its load) also
left the earlier result in ``previewed`` and the Segment step's line saying what "the last
Preview" used. The result is now held by one box of widgets (``workflow.preview_box``),
emptied before each Preview starts, before the model is loaded, and when the channels change;
a failed Preview leaves it empty. These cases drive ``segmentation_app`` headless with the
stand-in Cellpose (``tests/cellpose_stub.py``) on a run whose every image has DNA and TUB.
"""

import ipywidgets as widgets
import pytest

from cellpose_stub import install, two_channel_run
from morphobricks_preprocessing.notebook_cellpose_app import segmentation_app
from morphobricks_preprocessing.notebook_cellpose_channels import BY_NUMBER
from morphobricks_preprocessing.segmentation_record import record_path


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


@pytest.fixture
def run(tmp_path):
    return two_channel_run(tmp_path / "run")


def walk(widget):
    yield widget
    for child in getattr(widget, "children", ()):
        yield from walk(child)


def texts(widget):
    return " ".join(str(w.value) for w in walk(widget) if isinstance(w, widgets.HTML))


def pictures(widget):
    return [w for w in walk(widget) if isinstance(w, widgets.Image)]


def choose(app, output):
    form = app.workflow.forms["channels"]
    form.fields["output"].value = output
    form.button.click()
    assert form.refusal == "", form.refusal
    picker = app.workflow.picker
    picker.cell.value = "TUB"
    if output == "both":
        picker.nuclear.value = "DNA"


def both_previewed(run):
    """An app whose Preview showed the cells and the nuclei of Both once."""
    app = segmentation_app(run.out, run.masks)
    choose(app, "both")
    app.workflow.forms["model"].fields["device"].value = "cpu"
    press_preview(app)
    assert [r.name for r in app.workflow.previewed] == ["Cell mask", "Nuclei mask"]
    assert "Segment will use what the last Preview used" in app.workflow._summary.value
    return app


def press_preview(app):
    form = app.workflow.forms["preview"]
    form.submit()
    assert form.refusal == "", form.refusal
    return form


def snapshot(folder):
    if not folder.exists():
        return None
    return sorted((str(p.relative_to(folder)), p.stat().st_mtime_ns) for p in folder.rglob("*"))


def test_the_preview_page_holds_the_last_previews_result_only(run, stub):
    app = both_previewed(run)
    workflow = app.workflow
    assert "Nuclei mask" in texts(workflow.preview_box)
    page = app.stepper.pages[2]  # Model and preview
    assert sum(w is workflow.preview_box for w in walk(page)) == 1
    assert len(pictures(page)) == 4  # channels and Cellpose's panels, for cells and nuclei
    workflow.forms["model"].fields["diameter"].value = "17"
    press_preview(app)
    shown = texts(workflow.preview_box)
    assert shown.count("Preview of") == 1 and shown.count("Nuclei mask") == 1
    assert "diameter 17" in shown and len(pictures(page)) == 4


def test_a_preview_of_cells_only_shows_no_nuclei_of_the_preview_of_both(run, stub):
    app = both_previewed(run)
    workflow = app.workflow
    choose(app, "cells")  # the output changed: the Both preview goes at once
    assert workflow.previewed == []
    assert workflow.preview_box.children == ()
    press_preview(app)
    assert [r.name for r in workflow.previewed] == ["Cell mask"]
    assert "Nuclei" not in texts(workflow.preview_box)
    assert len(pictures(workflow.preview_box)) == 2
    assert "Nuclei" not in texts(app.stepper.pages[2])


def test_a_preview_whose_model_fails_leaves_no_earlier_result(run, stub):
    app = both_previewed(run)
    workflow = app.workflow

    def fail_nuclei(stack):
        if stack.shape[0] == 1:
            raise RuntimeError("out of memory")

    stub.fail = fail_nuclei
    with pytest.raises(RuntimeError, match="out of memory"):  # the output area shows it
        workflow.forms["preview"].submit()
    assert workflow.previewed == []
    assert "not previewed yet" in workflow._summary.value
    assert workflow.preview_box.children == ()


def test_a_preview_whose_model_does_not_load_leaves_no_earlier_result(run, stub):
    app = both_previewed(run)
    workflow = app.workflow
    workflow.forms["model"].fields["device"].value = "cuda"  # no GPU: the load is refused
    form = workflow.forms["preview"]
    form.submit()
    assert form.refusal and workflow.loaded is None
    assert workflow.previewed == []
    assert "not previewed yet" in workflow._summary.value
    assert workflow.preview_box.children == ()


def test_a_line_names_the_model_while_cellpose_runs_on_the_preview_image(run, stub):
    app = segmentation_app(run.out, run.masks)
    choose(app, "both")
    model = app.workflow.forms["model"].fields
    model["device"].value = "cpu"
    model["separate_nuclei"].value, model["nuclei_model"].value = True, "cpsam"
    seen = []
    stub.fail = lambda stack: seen.append(texts(app.workflow.preview_box))
    press_preview(app)
    assert seen and all("on the preview image" in line for line in seen)
    assert all("cpsam_v2 and cpsam" in line for line in seen)
    assert "on the preview image" not in texts(app.workflow.preview_box)  # replaced by the result


def test_previews_write_nothing(run, stub):
    before = snapshot(run.masks)
    app = both_previewed(run)
    app.workflow.forms["model"].fields["diameter"].value = "17"
    press_preview(app)
    choose(app, "cells")
    press_preview(app)
    assert snapshot(run.masks) == before
    assert app.workflow.results == {} and app.workflow.result is None
    assert all(not record_path(run.masks, t).exists() for t in ("Cells", "Nuclei"))


# Review of #576: the channel dropdowns are live (Preview and Segment re-read them), and so are
# the model step's fields; any change takes the preview off, as a change of output does.


def test_removing_the_nuclear_channel_takes_the_preview_of_both_off(run, stub):
    app = both_previewed(run)
    workflow = app.workflow
    workflow.picker.nuclear.value = ""
    assert workflow.previewed == []
    assert workflow.preview_box.children == ()
    assert "Nuclei" not in texts(app.stepper.pages[2])
    assert "not previewed yet" in workflow._summary.value


def test_changing_the_cell_channel_takes_the_preview_off(run, stub):
    app = both_previewed(run)
    workflow = app.workflow
    workflow.picker.cell.value = "DNA"
    assert workflow.previewed == []
    assert workflow.preview_box.children == ()
    assert "not previewed yet" in workflow._summary.value
    workflow.picker.cell.value = "TUB"
    press_preview(app)  # a new Preview shows its own result again
    assert [r.name for r in workflow.previewed] == ["Cell mask", "Nuclei mask"]


def test_a_channel_number_takes_the_preview_off(run, stub):
    app = both_previewed(run)
    workflow = app.workflow
    workflow.picker.by.value = BY_NUMBER
    assert workflow.previewed == [] and workflow.preview_box.children == ()


def test_changing_a_model_setting_takes_the_preview_off_and_names_the_change(run, stub):
    app = both_previewed(run)
    workflow = app.workflow
    workflow.forms["model"].fields["diameter"].value = "17"
    assert workflow.previewed == []
    assert workflow.preview_box.children == ()
    assert "Changed since the last Preview" in workflow._summary.value


def test_the_style_sheet_is_shown_once_after_a_failed_first_preview(run, stub, monkeypatch):
    from morphobricks_preprocessing import notebook_cellpose
    from morphobricks_preprocessing.notebook_style import STYLE_CSS

    sheets = []
    monkeypatch.setattr(
        notebook_cellpose,
        "display",
        lambda w: sheets.append(w) if getattr(w, "value", None) == STYLE_CSS else None,
    )
    workflow = notebook_cellpose.CellposeWorkflow()
    workflow._mount_preview()  # a first Preview, refused: no card shown
    workflow._mount_preview()
    workflow._show(widgets.HTML("a card"))
    assert len(sheets) == 1
