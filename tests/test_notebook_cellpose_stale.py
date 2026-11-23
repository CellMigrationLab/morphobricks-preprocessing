"""The Cellpose notebook never writes to a folder, or with a workflow, the forms no longer show.

Review of PR #446 (23 November 2026): the model, the model file and the device were checked
against what was loaded, but not the run and masks folders of cell 3, and running cell 3 again
left the buttons of cells 4 to 8 on screen bound to the old workflow. Each case below wrote
masks into the old masks folder before the fix.
"""

import html

import pytest

from cellpose_stub import install
from morphobricks_preprocessing.notebook_cellpose_form import STOPPED
from morphobricks_preprocessing.notebook_cellpose_guard import REPLACED, RUN_CHANGED
from test_notebook_cellpose import BATCH, MODEL, OPEN, PREVIEW, open_and_choose, run_cell, source


@pytest.fixture
def loaded(mixed_run, monkeypatch, tmp_path):
    """Cells 3 to 5 run (the run open, the channels chosen, the model loaded), 6 and 7 shown."""
    stub = install(monkeypatch, tmp_path)
    cells = type("C", (), {"ns": {}, "stub": stub, "run": mixed_run})()
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    for cell in (PREVIEW, BATCH):  # shown, not pressed
        exec(compile(source(cell), f"<cell {cell + 1}>", "exec"), cells.ns)
    workflow = cells.ns["workflow"]
    assert workflow.loaded is not None and not workflow.forms["segment"].button.disabled
    return cells


def snapshot(folder):
    """Return every path under ``folder`` with its modification time (None: no folder)."""
    if not folder.exists():
        return None
    return sorted((str(p.relative_to(folder)), p.stat().st_mtime_ns) for p in folder.rglob("*"))


def press_segment(form):
    """Press Segment as a click would, even on a disabled button; return the refusal."""
    form.button.click()
    return form.refusal


def test_a_refused_open_leaves_nothing_to_segment_with(loaded, tmp_path):
    workflow, before = loaded.ns["workflow"], snapshot(loaded.run.masks)
    opener = workflow.forms["open"]  # the form on screen: cell 3 is not run again
    opener.fields["run_folder"].value = str(tmp_path / "no_run")
    opener.fields["masks_folder"].value = str(tmp_path / "new_masks")
    opener.button.click()
    assert opener.refusal and html.escape(opener.refusal) in workflow.shown[-1]
    segment = workflow.forms["segment"]
    assert segment.button.disabled and workflow.forms["preview"].button.disabled
    assert press_segment(segment) == "Run cell 5 first."
    assert STOPPED in workflow.shown[-1]
    assert snapshot(loaded.run.masks) == before and loaded.stub.calls == []
    assert snapshot(tmp_path / "new_masks") is None


@pytest.mark.parametrize("box", ["masks_folder", "run_folder"])
def test_a_folder_edited_without_open_is_refused(loaded, box):
    workflow, masks = loaded.ns["workflow"], loaded.run.masks
    elsewhere = masks.parent / "other_masks"
    before = snapshot(masks)
    field = workflow.forms["open"].fields[box]
    field.value = str(elsewhere if box == "masks_folder" else loaded.run.experiment)
    for step in ("preview", "segment"):
        assert press_segment(workflow.forms[step]) == RUN_CHANGED
        assert html.escape(RUN_CHANGED) in workflow.shown[-1]
    assert snapshot(masks) == before and snapshot(elsewhere) is None
    assert loaded.stub.calls == []
    field.value = f'  "{loaded.run.masks if box == "masks_folder" else loaded.run.out}" '
    assert press_segment(workflow.forms["segment"]) == ""  # the same folder, pasted with quotes
    assert workflow.result.counts()["written"] == len(loaded.run.inputs.fovs())


def test_running_cell_3_again_retires_the_forms_already_on_screen(loaded):
    old, masks = loaded.ns["workflow"], loaded.run.masks
    before = snapshot(masks)
    exec(compile(source(OPEN), "<cell 3>", "exec"), loaded.ns)  # cell 3 run again
    new = loaded.ns["workflow"]
    assert new is not old and old.replaced and not new.replaced
    segment = old.forms["segment"]
    assert segment.button.disabled and segment.fields["overwrite"].disabled
    assert all(form.button.disabled for form in old.forms.values())
    assert all(html.escape(REPLACED) in form.notice.value for form in old.forms.values())
    assert all(dropdown.disabled for dropdown in old.picker.dropdowns)
    assert press_segment(segment) == REPLACED
    assert segment.button.disabled  # still disabled after the refused press
    assert snapshot(masks) == before and loaded.stub.calls == []
    assert new.forms["open"].notice.value == "" and not new.forms["open"].button.disabled
