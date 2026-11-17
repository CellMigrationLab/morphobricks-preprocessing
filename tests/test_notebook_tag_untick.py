"""*Tag channels* fast mode: unticking after a save that resynced the aliases (review of 223d055).

Unticking restores the aliases the draft held before the no-click tags (223d055), but only while
no save resynced them since: a click, the label list or an image type saved on the strict page
drops the aliases no tagged cell holds, so the untick restores none (owner rules of 16 November
2026: unticked, the draft is byte-equal to the strict page's; ticking again trusts only the
aliases still saved; a hand tag is never taken back).
"""

import shutil

import pytest

from channel_images import write_channels
from morphobricks_preprocessing.description_file import DRAFT_FILENAME
from test_notebook_channels import KINDS
from test_notebook_check_save import answer_all

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from test_notebook_row_fold import stepper  # noqa: E402
from test_notebook_tag_matrix import SAVED, TAGS, _add  # noqa: E402


def _stale_aliases(tmp_path):
    """Save DAPI, Actin, Ch3 as aliases, then drop the saved units' header names.

    A new unit names them: opened ticked it is trusted with no click, and it alone holds them.
    Returns the experiment, masks, output folder and a copy of it made before any reopen.
    """
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(SAVED):
        _add(root, masks, folder, i + 1)
    first = stepper(root, masks, out)
    answer_all(first)
    assert first.save_experiment()
    for i, folder in enumerate(SAVED):
        write_channels(root / folder / "a.tif", None, KINDS, seed=i + 1)
    _add(root, masks, "drug/rep3", 10)
    shutil.copytree(out, tmp_path / "copy")
    return root, masks, out


def _labels_saved(nb):
    """Save the label list again, with a label added (the strict page resyncs the aliases)."""
    from test_notebook_channels import set_labels

    nb.channel_labels.confirm.button.click()  # change the labels
    set_labels(nb, [*TAGS, "EXTRA"])


def _label_removed_and_added(nb):
    """Take DNA out of the list, then add it again (two saves of the list)."""
    from test_notebook_channels import set_labels

    nb.channel_labels.confirm.button.click()
    set_labels(nb, ["ACTIN", "PHASE"])
    nb.channel_labels.confirm.button.click()
    set_labels(nb, list(TAGS))


def _type_answered(nb):
    """Answer the new unit's image type (the strict page resyncs the aliases)."""
    nb.channel_types.radios[("drug", "rep3", "single")].value = "channels"


@pytest.mark.parametrize(
    "save",
    [_labels_saved, _label_removed_and_added, _type_answered],
    ids=["labels", "label-removed-and-added", "type"],
)
def test_unticking_restores_no_alias_a_save_since_dropped_on_the_strict_page(
    tmp_path, monkeypatch, save
):
    """A save that resyncs the aliases after the no-click tags is one the strict page made too.

    Opened unticked, DAPI (held by no tagged cell) is dropped by the label list's save or the
    image type's, as by a click. Ticked, the trusted unit holds it through that save; unticking
    must not restore it: the draft is byte-equal to the strict page's after the same save, and
    ticking again trusts no alias the draft no longer holds.
    """
    from morphobricks_preprocessing.notebook_channels_grid import TagGrid

    root, masks, out = _stale_aliases(tmp_path)
    nb = stepper(root, masks, out)
    named = ("drug", "rep3", "single")
    assert nb.draft_answers.units[named].channels == TAGS  # trusted on opening
    save(nb)
    nb.channel_grid.same_channels.value = False
    monkeypatch.setattr(TagGrid, "SAME_CHANNELS", False)
    strict = stepper(root, masks, tmp_path / "copy")
    save(strict)
    assert not strict.draft_answers.channels["DNA"].header_names  # the save dropped DAPI
    assert nb.draft_answers == strict.draft_answers
    assert (out / DRAFT_FILENAME).read_bytes() == (tmp_path / "copy" / DRAFT_FILENAME).read_bytes()
    nb.channel_grid.same_channels.value = True
    unit = nb.draft_answers.units.get(named)
    assert unit is None or not any(unit.channels)


def test_untick_retick_and_a_hand_tag_on_a_reopen_with_stale_aliases(tmp_path):
    """Untick and retick repeat the opening exactly; a hand tag is never taken back after."""
    root, masks, out = _stale_aliases(tmp_path)
    nb = stepper(root, masks, out)
    named = ("drug", "rep3", "single")
    grid = nb.channel_grid
    opened = nb.draft_answers
    for _ in range(2):
        grid.same_channels.value = False
        unit = nb.draft_answers.units.get(named)
        assert unit is None or not any(unit.channels)
        grid.same_channels.value = True
        assert nb.draft_answers == opened
    grid.cells[(named, 0)].value = None  # the user's own clicks on the trusted unit
    grid.cells[(named, 0)].value = "DNA"
    grid.same_channels.value = False
    assert nb.draft_answers.units[named].channels == TAGS  # the hand tag stays
    assert nb.draft_answers.channels["DNA"].header_names == ("DAPI",)  # held by it
