"""Tag by repeat: an explicit action on a refused unit is the only release (review am, 23 Nov).

The owner rule: a refused unit is never tagged, counted, unlisted or saveable without an
explicit action on that unit (its editor's Confirm, a one-click confirmation, a deliberate
hand-tag of that unit, or a visibly scoped strict-mode block click). A header or an alias that
matches never releases a refusal on its own, and only a unit removed from the folder drops its
record.
"""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from channel_images import write_channels  # noqa: E402
from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import answer_all, opened  # noqa: E402
from test_notebook_tag_by_repeat import (  # noqa: E402
    EXTRA,
    KINDS4,
    TAGS,
    UNITS,
    _experiment,
    _mixed_experiment,
    _set_row,
)
from test_notebook_tag_by_repeat_drafts import _review  # noqa: E402
from test_notebook_tag_by_repeat_lapses import _untagged  # noqa: E402
from test_notebook_tag_by_repeat_oneclick import GONE, OTHER, _confirm, _strict  # noqa: E402
from test_notebook_tag_by_repeat_recheck import _recheck  # noqa: E402


def _hand(nb, key, tags):
    """Tag one unit by hand, on its own (out of its block)."""
    nb.channel_grid.split(key)
    for index, tag in enumerate(tags):
        nb.channel_grid.cells[(key, index)].value = tag


# The Check-and-save button after the last confirmation ----------------------------------------


def test_the_save_button_opens_right_after_the_last_confirmation(tmp_path, statement_off):
    nb, _, rows = _strict(tmp_path, statement_off)
    rows.override.buttons[GONE].click()
    rows.override.confirm_button.click()  # GONE: the row's tags
    _hand(nb, OTHER, ("ACTIN", "DNA", "PHASE"))  # contradicts the aliases GONE saved: listed
    answer_all(nb)
    assert set(rows.listed()) == {OTHER} and nb.save_button.disabled
    rows.override.buttons[OTHER].click()
    for index, tag in enumerate(("ACTIN", "DNA", "PHASE")):
        rows.override.cells[(OTHER, index)].value = tag
    rows.override.confirm_button.click()  # the last listed unit, confirmed as an override
    assert not rows.listed()
    # at 7e9daf4 the confirmation was noted after the save had refreshed the button: disabled
    assert not nb.save_button.disabled


# BLOCKER 1: a tagged refused unit and the aliases another unit's confirmation saves -----------


def _header_changed(tmp_path, fast):
    """Every rep2 unit tagged by its row; GONE's and OTHER's headers change; the row refuses them.

    Both keep their complete old tags; neither is confirmed.
    """
    extra = ["W"] if fast else []
    old, new = ["X", "Y", "Ch3", *extra], ["P", "Q", "Ch3", *extra]
    root, masks, out = _experiment(
        tmp_path, extra=fast, names=lambda k: old if k[1] == "rep2" else None
    )
    nb = opened(root, masks, out)
    tags, row = (EXTRA, ("rep2", 4)) if fast else (TAGS, ("rep2", 3))
    set_labels(nb, list(tags))
    if fast:
        for index, tag in enumerate(TAGS):
            nb.channel_grid.matrix.template_cells[(("ctrl", "rep1", "t1"), index)].value = tag
    _set_row(nb, row, tags)
    rows = nb.channel_grid.by_repeat
    assert not rows.listed()
    kinds = KINDS4 if fast else KINDS4[:3]
    for key in (GONE, OTHER):
        write_channels(root.joinpath(*key, "a.tif"), new, kinds, seed=UNITS.index(key) + 1)
    _recheck(nb)
    _set_row(nb, row, tags)  # the row again: the changed headers contradict it
    assert set(rows.listed()) == {GONE, OTHER}
    assert nb.draft_answers.units[GONE].channels == tags  # complete old tags
    return nb, root, masks, out


def _gone_blocks_the_save(nb, out):
    rows = nb.channel_grid.by_repeat
    answer_all(nb)
    assert GONE in rows.listed() and GONE in rows.refused and GONE in rows.standing()
    review = _review(out)
    assert GONE in review.refused and GONE not in review.confirmed  # on disk, unconfirmed
    assert nb.save_button.disabled
    assert "row did not fill" in nb.save_blocker.value
    assert not nb.save_experiment()


@pytest.mark.parametrize("action", ["override", "hand"])
@pytest.mark.parametrize("fast", [True, False])
def test_b1_a_tagged_refused_unit_stays_listed_after_another_unit_adds_matching_aliases(
    tmp_path, request, fast, action
):
    if not fast:
        request.getfixturevalue("statement_off")
    nb, root, masks, out = _header_changed(tmp_path, fast)
    rows = nb.channel_grid.by_repeat
    assert nb.channel_grid.same_channels.value == fast
    _confirm(nb, rows, OTHER, "override", None)  # OTHER: P, Q saved as aliases of its labels
    # at 7e9daf4 GONE left listed() (the live aliases fit it) and the save opened
    _gone_blocks_the_save(nb, out)
    fresh = opened(root, masks, out)  # a restart on the saved draft
    again = fresh.channel_grid.by_repeat
    assert fresh.channel_grid.same_channels.value == fast
    _gone_blocks_the_save(fresh, out)
    if action == "override":
        _confirm(fresh, again, GONE, "override", None)  # the explicit action on GONE
    else:  # a deliberate hand-tag of GONE: one channel cleared and chosen again
        _hand(fresh, GONE, ())
        fresh.channel_grid.cells[(GONE, 2)].value = None
        assert GONE in again.listed()  # incomplete: not a confirmation
        fresh.channel_grid.cells[(GONE, 2)].value = "PHASE"
    assert not again.listed() and not fresh.save_button.disabled
    assert GONE in _review(out).confirmed  # recorded with its tags, in the same write
    assert GONE not in opened(root, masks, out).channel_grid.by_repeat.listed()  # a restart


# BLOCKER 2: a unit whose images disagreed, once they agree -------------------------------------

MIXED = ("drug", "rep2", "t1")
"""Its two images name the third channel differently (``_mixed_experiment``)."""


def _mixed_refused(tmp_path):
    root, masks, out = _mixed_experiment(tmp_path)
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    _set_row(nb, ("rep2", 3), TAGS)
    rows = nb.channel_grid.by_repeat
    assert MIXED in rows.listed() and rows.refused[MIXED].lead is None
    assert _review(out).refused[MIXED].reference is None
    return nb, root, masks, out


def test_b2_a_unit_whose_images_agree_again_keeps_its_refusal(tmp_path, statement_off):
    nb, root, masks, out = _mixed_refused(tmp_path)
    write_channels(root / "drug/rep2/t1/b.tif", ["Hoechst", "Actin", "Ch3"], KINDS4[:3], seed=43)
    _recheck(nb)  # its images agree now; nothing was confirmed
    rows = nb.channel_grid.by_repeat
    # at 7e9daf4 the refusal was dropped here and the smaller review saved
    assert MIXED in rows.refused and MIXED in rows.listed() and MIXED in rows.standing()
    assert "agree again" in rows.listed()[MIXED]
    assert MIXED in _review(out).refused and _review(out).refused[MIXED].reference is not None
    fresh = opened(root, masks, out)  # a restart, then fast mode trusts headers
    grid = fresh.channel_grid
    grid.same_channels.value = True
    rows = grid.by_repeat
    assert _untagged(fresh, MIXED) and MIXED not in grid.matrix.own  # never from its header
    assert MIXED in rows.listed() and MIXED in _review(out).refused
    assert not grid.complete and not fresh.save_experiment()
    rows.override.buttons[MIXED].click()  # its editor, from the row's tags
    assert rows.override.drafts[MIXED] == list(TAGS)
    assert rows.override.confirm() and MIXED not in rows.listed()
    assert fresh.draft_answers.units[MIXED].channels == TAGS
    assert MIXED in _review(out).confirmed


def test_b2_removing_the_folder_of_a_unit_whose_images_disagreed_drops_its_record(
    tmp_path, statement_off
):
    nb, root, masks, out = _mixed_refused(tmp_path)
    shutil.rmtree(root.joinpath(*MIXED))
    shutil.rmtree(masks / "Nuclei" / "drug/rep2/t1", ignore_errors=True)
    _recheck(nb)
    rows = nb.channel_grid.by_repeat
    assert MIXED not in rows.refused and MIXED not in rows.listed()
    assert MIXED not in _review(out).refused


# The words: the list says what releases a refusal ---------------------------------------------


def test_the_list_says_only_an_action_on_the_unit_releases_it(tmp_path, statement_off):
    from morphobricks_preprocessing import notebook_channels_repeat as repeat

    nb, _, rows = _strict(tmp_path, statement_off)
    nb.channel_grid.refresh()
    # at 7e9daf4: "tag the first on their own", and "however it got there" in listed()
    assert "only by an action on it" in rows.message
    assert "a header or alias that fits later never releases it" in rows.message
    assert "however it got there" not in repeat.RepeatRows.listed.__doc__
    assert "a changed image header" not in repeat.Refusal.channels.__doc__
