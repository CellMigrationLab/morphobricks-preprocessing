"""Reopening folds the unchanged, answered rows; new or changed ones stay open, in orange (17b-2).

Owner decision (UX proposal 2026-11-09): inside the pairing, the names, the image types, the tag
blocks and the replicate blocks of *Check and save*, a row whose folders are found exactly as
saved and whose question is answered folds behind one line with an **open** toggle. Display
only: the answers and the draft are those of an unfolded page.
"""

import pytest

from morphobricks_preprocessing.description_reconcile import (
    CountChange,
    Reconciliation,
    Status,
    UnitChange,
    UnitFolders,
)
from morphobricks_preprocessing.notebook_row_fold import (
    CHANGED,
    NEW,
    UNCHANGED,
    reopen_units,
    row_status,
)
from test_notebook_check_save import add_folder, answer_all

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402


def test_a_row_folds_only_when_every_folder_is_as_saved_and_it_is_answered():
    rec = Reconciliation(
        (
            UnitChange(UnitFolders("ctrl", "rep1", "single"), Status.UNCHANGED, "rep1", "ctrl"),
            UnitChange(UnitFolders("drug", "rep1", "single"), Status.UNCHANGED, "rep1", "drug"),
            UnitChange(UnitFolders("drug", "rep2", "single"), Status.NEW),
            UnitChange(UnitFolders("ctrl", "rep2", "single"), Status.UNCHANGED, "rep2", "ctrl"),
        ),
        {},
        (CountChange("rep2", "ctrl", 2, 3),),
    )
    units = reopen_units(rec)
    assert units == {
        ("ctrl", "rep1", "single"): UNCHANGED,
        ("drug", "rep1", "single"): UNCHANGED,
        ("drug", "rep2", "single"): NEW,
        ("ctrl", "rep2", "single"): CHANGED,  # its image count changed
    }
    rep1 = [("ctrl", "rep1", "single"), ("drug", "rep1", "single")]
    assert row_status(units, rep1, answered=True) == UNCHANGED
    assert row_status(units, rep1, answered=False) == ""  # still to answer: not folded
    assert row_status(units, [("ctrl", "rep2", "single")], True) == CHANGED
    assert row_status(units, [("ctrl", "rep2", "single"), ("drug", "rep2", "single")], True) == NEW
    assert row_status(units, [("other", "x", "single")], True) == NEW  # unknown: never folded
    assert row_status(None, rep1, True) == "" and reopen_units(None) is None


def stepper(root, masks, out):
    nb = PreprocessingNotebook(str(root), output_folder=str(out), describe=True)
    nb.welcome()
    nb.steps()
    nb.masks_folder.value = str(masks)
    nb.check_button.click()
    nb.quick_confirm.click()
    return nb


@pytest.fixture
def reopened(tmp_path):
    """Three folders saved, then drug/rep2 added: a changed reopen."""
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(["ctrl/rep1", "ctrl/rep2", "drug/rep1"]):
        add_folder(root, masks, folder, i + 1)
    first = stepper(root, masks, out)
    answer_all(first)
    assert first.save_experiment()
    add_folder(root, masks, "drug/rep2", 9)
    return stepper(root, masks, out)


def _text(widget):
    out = getattr(widget, "value", "")
    out = out if isinstance(out, str) else ""
    return out + "".join(_text(c) for c in getattr(widget, "children", ()))


def test_a_changed_reopen_folds_the_unchanged_rows_and_marks_the_new_ones(reopened):
    nb = reopened
    grid, types = nb.channel_grid, nb.type_rows
    assert grid.fold.count == 1 and not ui.is_visible(grid.fold.folded)
    assert "1 unchanged folder block<" in grid.fold.line.value
    assert "new since saved" in _text(grid.blocks)  # drug/rep2's block stays open
    assert types.fold.count == 3 and "new since saved" in _text(types.rows)
    conditions = nb._name_folds["conditions"]
    assert conditions.count == 1  # ctrl; drug gained a folder
    assert nb._name_folds["repeats"].count == 1  # rep1; rep2 gained drug/rep2
    assert nb._replicate_fold.count == 1  # rep1 is asked nothing again
    assert "new since saved" in _text(nb.replicates_box)


def test_opening_a_fold_shows_the_rows_and_changes_no_answer(reopened):
    nb = reopened
    before = (nb.draft_answers, {k: d.value for k, d in nb.channel_grid.cells.items()})
    nb.channel_grid.fold.toggle.click()
    assert ui.is_visible(nb.channel_grid.fold.folded)
    assert "close" in nb.channel_grid.fold.toggle.description
    nb.channel_grid.refresh()  # stays open while the page is rebuilt
    assert ui.is_visible(nb.channel_grid.fold.folded)
    assert (nb.draft_answers, {k: d.value for k, d in nb.channel_grid.cells.items()}) == before


def test_an_unsaved_experiment_folds_nothing(tmp_path):
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(["ctrl/rep1", "drug/rep1"]):
        add_folder(root, masks, folder, i + 1)
    nb = stepper(root, masks, out)
    answer_all(nb)
    assert nb.channel_grid.fold.count == 0 and nb.type_rows.fold.count == 0
    assert nb._replicate_fold.count == 0
    assert "unchanged" not in _text(nb.channel_grid.blocks)
