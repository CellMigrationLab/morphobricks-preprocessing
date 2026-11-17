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


def _renamed_headers(tmp_path):
    """Save DAPI, Actin, Ch3 as aliases, then rename every header to the labels themselves.

    The saved units keep their tags; their headers now say DNA, ACTIN, PHASE, which no saved
    alias holds. A new unit says them too: opened ticked, it is tagged with no click.
    """
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(SAVED):
        _add(root, masks, folder, i + 1)
    first = stepper(root, masks, out)
    answer_all(first)
    assert first.save_experiment()
    for i, folder in enumerate(SAVED):
        write_channels(root / folder / "a.tif", list(TAGS), KINDS, seed=i + 1)
    _add(root, masks, "drug/rep3", 10)
    write_channels(root / "drug/rep3/a.tif", list(TAGS), KINDS, seed=10)
    shutil.copytree(out, tmp_path / "copy")
    return root, masks, out


def _nothing(nb):
    """No save between opening and unticking."""


def _retagged_by_hand(nb):
    """Tag a saved unit's first channel again by hand: the click saves its header name."""
    cell = nb.channel_grid.cells[("ctrl", "rep1", "single"), 0]
    cell.value = None
    cell.value = "DNA"


@pytest.mark.parametrize(
    ("save", "dna"),
    [(_nothing, ("DAPI",)), (_labels_saved, None), (_retagged_by_hand, ("DNA",))],
    ids=["no-save", "labels", "hand-tag"],
)
def test_unticking_takes_out_an_alias_only_the_no_click_tags_added(
    tmp_path, monkeypatch, save, dna
):
    """An exact header name the no-click tags added is no alias of the strict page's.

    A saved unit whose header now says that name holds it too, so the untick's resync alone
    kept it (review of e623cad): unticked, the draft must still be byte-equal to the strict
    page's after the same actions. A click that tags a cell with that name saves it on both.
    """
    from morphobricks_preprocessing.notebook_channels_grid import TagGrid

    root, masks, out = _renamed_headers(tmp_path)
    nb = stepper(root, masks, out)
    named = ("drug", "rep3", "single")
    assert nb.draft_answers.units[named].channels == TAGS  # trusted on opening
    assert nb.draft_answers.channels["DNA"].header_names == ("DNA",)
    save(nb)
    nb.channel_grid.same_channels.value = False
    monkeypatch.setattr(TagGrid, "SAME_CHANNELS", False)
    strict = stepper(root, masks, tmp_path / "copy")
    save(strict)
    assert strict.draft_answers.channels["DNA"].header_names == dna
    assert nb.draft_answers == strict.draft_answers
    if save is not _nothing:  # the strict page writes no draft until something is saved
        draft = (tmp_path / "copy" / DRAFT_FILENAME).read_bytes()
        assert (out / DRAFT_FILENAME).read_bytes() == draft


def test_a_click_on_a_unit_tagged_with_no_click_keeps_its_aliases(tmp_path):
    """Once clicked, the unit's tags are the user's, and so are the names its header says."""
    root, masks, out = _renamed_headers(tmp_path)
    nb = stepper(root, masks, out)
    named = ("drug", "rep3", "single")
    cell = nb.channel_grid.cells[named, 1]
    cell.value = None
    cell.value = "ACTIN"
    nb.channel_grid.same_channels.value = False
    assert nb.draft_answers.units[named].channels == TAGS  # the hand tag stays
    assert nb.draft_answers.channels["DNA"].header_names == ("DNA",)  # held by its tags
    assert nb.draft_answers.channels["ACTIN"].header_names == ("ACTIN",)


# --- the strict page's draft (shadow model): a new check of the folders ----------------------


def test_unticking_after_a_new_check_takes_back_the_no_click_tags_made_before_it(
    tmp_path, monkeypatch
):
    """Known limitation of the delta model (review of #325), closed: tags made before a check.

    The headers name every channel, so fast mode tags every unit with no click on opening. A
    unit is added and the folder checked again: the saved draft is read again, and the strict
    page's draft is carried over with it. Unticked, the draft is the strict page's after the
    same check, byte for byte (the delta model forgot the no-click tags at the new check).
    """
    from morphobricks_preprocessing.draft_format import answers_to_bytes
    from morphobricks_preprocessing.notebook_channels_grid import TagGrid
    from test_notebook_tag_matrix import LABELLED, _design, _screen

    def check_again(nb):
        nb.check_button.click()
        if nb.confirmed_mapping is None:
            nb.preview_checked.set(True)
            nb.show_preview()
            nb.confirm_button.click()

    root = _design(tmp_path, names_of=lambda key: LABELLED)
    fast = _screen(tmp_path, root, "fast")
    assert {u.channels for u in fast.draft_answers.units.values()} == {TAGS}  # no click
    monkeypatch.setattr(TagGrid, "SAME_CHANNELS", False)
    strict = _screen(tmp_path, root, "strict")
    write_channels(root / "drug" / "rep2" / "t3" / "a.tif", None, KINDS, seed=50)
    for nb in (fast, strict):
        check_again(nb)
    assert len(fast.channel_grid.layouts) == 9
    fast.channel_grid.same_channels.value = False
    assert not fast.draft_answers.units
    assert fast.draft_answers == strict.draft_answers
    assert (tmp_path / "fast" / DRAFT_FILENAME).read_bytes() == answers_to_bytes(
        strict.draft_answers
    )
