"""Reopening folds the unchanged, answered rows; new or changed ones stay open, in orange (17b-2).

Owner decision (UX proposal 2026-11-09): inside the pairing, the names, the image types, the tag
blocks and the replicate blocks of *Check and save*, a row whose folders are found exactly as
saved and whose question is answered folds behind one line with an **open** toggle. Display
only: the answers and the draft are those of an unfolded page.
"""

from dataclasses import replace

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
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
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


def test_a_folder_whose_header_channels_changed_is_changed_not_folded():
    # Regression (17b-2 review): the reconciliation's reasons were ignored, so a folder whose
    # header names no longer fit the saved channels folded as "unchanged".
    rec = Reconciliation(
        (
            UnitChange(UnitFolders("ctrl", "rep1", "single"), Status.UNCHANGED, "rep1", "ctrl"),
            UnitChange(
                UnitFolders("drug", "rep1", "single"),
                Status.UNCHANGED,
                "rep1",
                "drug",
                reasons=("channels_changed",),
            ),
        ),
        {"rep1": ("channels_changed",)},
        (),
    )
    units = reopen_units(rec)
    assert units[("ctrl", "rep1", "single")] == UNCHANGED
    assert units[("drug", "rep1", "single")] == CHANGED
    assert row_status(units, [("drug", "rep1", "single")], answered=True) == CHANGED


def test_a_row_with_a_warning_never_folds(reopened, monkeypatch):
    # Regression (17b-2 review): a folded row hid the warning under an image type (a type the
    # header contradicts) and the orange A_SCOPE_INCOMPLETE line of a replicate block.
    nb = reopened
    key = ("ctrl", "rep1", "single")
    warn = "<span class='mb-help' style='color:#92400E'>The header says colour (RGB)</span>"
    plain = nb.channel_types.note
    monkeypatch.setattr(nb.channel_types, "note", lambda u: warn if u.key == key else plain(u))
    nb.type_rows.refresh()
    assert nb.type_rows.fold.count == 2  # ctrl/rep1 stays open, with its warning
    assert "The header says colour (RGB)" not in _text(nb.type_rows.fold.folded)

    from morphobricks_preprocessing import notebook_replicates

    real = notebook_replicates.replicates

    def lacking(applied, answers):
        views = real(applied, answers)
        return [replace(v, missing=((key, "DNA"),)) if v.repeat_id == "rep1" else v for v in views]

    monkeypatch.setattr(notebook_replicates, "replicates", lacking)
    nb._refresh_check_save()
    assert nb._replicate_fold.count == 0
    assert "A_SCOPE_INCOMPLETE" in _text(nb.replicates_box)


def test_a_header_rewritten_under_the_same_files_keeps_its_row_open(tmp_path):
    # Regression (batch 34b BLOCKER 4): the reopen reconciled image counts only, so headers
    # rewritten with swapped channel names (same files, shape and count) folded as "nothing
    # changed" while the run's checks refused R_HEADER_CONTRADICTION. Real workflow, no fake.
    from channel_images import write_channels
    from test_notebook_check_save import FOLDERS, KINDS

    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(FOLDERS):
        add_folder(root, masks, folder, i + 1)
    first = stepper(root, masks, out)
    answer_all(first)
    assert first.save_experiment()
    write_channels(root / "ctrl/rep1/a.tif", ["Actin", "DAPI", "Ch3"], KINDS, seed=1)
    nb = stepper(root, masks, out)
    reasons = {u.folders: u.reasons for u in nb._published().reconciliation.units}
    assert reasons.pop(UnitFolders("ctrl", "rep1", "single")) == ("channels_changed",)
    assert set(reasons.values()) == {()}  # produced by the real path, for that unit only
    assert nb._reopen_units()[("ctrl", "rep1", "single")] == CHANGED
    assert not nb._folded and not ui.is_visible(nb.fold_bar)
    assert ui.is_visible(nb.check_card)
    assert "headers changed: ctrl/rep1" in nb.folder_changes.value  # the unit is named
    # Owner decision (review of #257, experiment-v1 section 10 read literally): headers that no
    # longer fit are the run's refusal, not a reopening (the declared ordered channel list did
    # not change), so rep1 keeps its date and nothing waits for an answer. A refusal is never
    # hidden until the user acts: the run's refusal is shown at once, naming the unit.
    assert nb.same_way["rep1"].value and "rep1: answer" not in _text(nb.todo)
    shown = _text(nb.checks)
    assert "The images contradict it: the run would stop" in shown
    assert shown.count("R_HEADER_CONTRADICTION") == 2
    assert "repeat folder &#x27;rep1&#x27;" in shown  # the unit it names
    assert "changed since saved" in _text(nb.replicates_box)  # rep1's block stays open
    assert "can start" not in nb.save_status.value and "nothing changed" not in nb.saved_line.value
    assert nb.run_button.disabled


def test_a_refusal_of_the_run_s_checks_unfolds_an_unchanged_reopen(tmp_path):
    # Regression (batch 34b BLOCKER 4, second layer): a refusal the reconciliation cannot see
    # (a mask type added beside the saved folders) was folded behind "nothing changed".
    from channel_images import write_masks
    from test_notebook_check_save import FOLDERS

    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(FOLDERS):
        add_folder(root, masks, folder, i + 1)
    first = stepper(root, masks, out)
    answer_all(first)
    assert first.save_experiment()
    write_masks(masks, {"ctrl/rep1/a.tif": 1}, {"Cells": "labels"})
    nb = stepper(root, masks, out)
    assert not nb._published().changed  # the folders are as saved
    assert not nb._published_check().can_run
    assert not nb._folded and not ui.is_visible(nb.fold_bar) and ui.is_visible(nb.check_card)
    assert "nothing changed" not in nb.saved_line.value and "can start" not in nb.save_status.value
    assert nb.run_button.disabled


def test_a_header_change_before_the_axes_are_confirmed_is_shown_as_blocking(tmp_path):
    # Review of #257: the header contradiction is never hidden. Before the axis roles are
    # confirmed the run's checks cannot name the refusal (nor can the channels be checked), so
    # the folder check's finding is shown at once beside the missing answers; rep1 is not
    # reopened (section 10: the declared ordered channel list did not change).
    from channel_images import write_channels
    from test_notebook_check_save import FOLDERS, KINDS

    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(FOLDERS):
        add_folder(root, masks, folder, i + 1)
    first = stepper(root, masks, out)
    answer_all(first)
    assert first.save_experiment()
    write_channels(root / "ctrl/rep1/a.tif", ["Actin", "DAPI", "Ch3"], KINDS, seed=1)
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    nb.welcome()
    nb.steps()
    nb.masks_folder.value = str(masks)
    nb.check_button.click()  # the axis roles are not confirmed
    assert nb.confirmed_mapping is None
    assert nb._published().reconciliation.reopened == {"rep1": ("channels_changed",)}
    assert nb.same_way["rep1"].value  # the declared list did not change: the date stays
    shown = _text(nb.checks)
    assert "The run is blocked by both" in shown
    assert "header contradiction: the image headers of ctrl/rep1" in shown
    assert "“same way?” of rep1 is asked again" not in shown
    assert nb.save_button.disabled and nb.run_button.disabled
