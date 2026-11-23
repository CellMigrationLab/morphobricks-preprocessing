"""Tag by repeat: what never releases a refused unit unasked (review of PR #376, 22 November).

A refused unit counts only once the user confirms THAT unit (owner decision of 22 November).
Neither a change of its row's reference, nor a confirmation of another unit in one save, nor a
change of its channel count, nor a change of its own header lets it count unasked.
"""

from __future__ import annotations

import shutil

import pytest

from channel_images import write_channels

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import answer_all, opened  # noqa: E402
from test_notebook_tag_by_repeat import (  # noqa: E402
    EXTRA,
    KINDS4,
    TAGS,
    UNITS,
    _experiment,
    _set_row,
)
from test_notebook_tag_by_repeat_oneclick import (  # noqa: E402
    GONE,
    LEAD,
    OTHER,
    THIRD,
    _confirm,
    _fast,
    _like,
    _strict,
    _text,
)
from test_notebook_tag_by_repeat_recheck import _recheck  # noqa: E402
from test_notebook_tag_by_repeat_review import _saved_fast  # noqa: E402


def _untagged(nb, key):
    own = nb.draft_answers.units.get(key)
    return own is None or not any(own.channels)


# Blocker 1: a reference change ----------------------------------------------------------------


def _reference_gone(tmp_path):
    """GONE and OTHER refused (P, Q against X, Y), GONE confirmed; the X, Y units are removed."""
    nb, root, masks, out = _saved_fast(tmp_path)
    for key in (LEAD, THIRD):  # every unit before GONE in the row
        shutil.rmtree(root.joinpath(*key))
    return nb, root, masks, out


def test_a_reference_change_never_lets_fast_mode_tag_or_clear_a_refused_unit(tmp_path):
    nb, *_ = _reference_gone(tmp_path)
    rows = nb.channel_grid.by_repeat
    _recheck(nb)  # GONE is the row's reference now, with OTHER's very header names
    assert _untagged(nb, OTHER) and OTHER not in nb.channel_grid.matrix.own
    assert OTHER in rows.standing()  # unconfirmed: never trusted, whatever the reference
    assert OTHER in rows.refused and OTHER in rows.listed()
    assert not nb.channel_grid.complete and not nb.save_experiment()


def test_a_reference_change_after_a_restart_keeps_the_refused_unit_listed(tmp_path):
    _, root, masks, out = _reference_gone(tmp_path)
    nb = opened(root, masks, out)  # a new notebook on the saved draft
    rows = nb.channel_grid.by_repeat
    assert nb.channel_grid.same_channels.value
    assert _untagged(nb, OTHER) and OTHER in rows.listed() and OTHER in rows.refused
    assert not nb.save_experiment()
    assert THIRD not in rows.refused


# Blocker 2: one draft write per confirmation; the save waits for the review --------------------


def _writes(nb, monkeypatch, fail=lambda answers: False):
    """Record the answers of every draft write; refuse the writes ``fail`` says (a disk error)."""
    written, save = [], nb._save_description

    def recorded():
        if fail(nb.draft_answers):
            nb._save_problem = "the disk refused the write (test)"
            return False
        written.append(nb.draft_answers)
        return save()

    monkeypatch.setattr(nb, "_save_description", recorded)
    return written


def _confirmed_on(answers, key):
    return answers.tag_review is not None and key in answers.tag_review.confirmed


def test_confirm_the_same_tags_writes_the_tags_and_the_confirmation_in_one_write(
    tmp_path, statement_off, monkeypatch
):
    nb, _, rows = _strict(tmp_path, statement_off)
    _confirm(nb, rows, GONE, "override", None)
    written = _writes(nb, monkeypatch)
    rows.override.same_buttons[OTHER].click()
    tagged = [a for a in written if (u := a.units.get(OTHER)) is not None and all(u.channels)]
    assert tagged, "OTHER was never written"
    assert _confirmed_on(tagged[0], OTHER)  # at 3ac71c8: tags first, the confirmation after
    assert len([a for a in written if a.tag_review != written[0].tag_review]) <= 1


def test_a_refused_confirmation_write_saves_neither_tags_nor_confirmation(
    tmp_path, statement_off, monkeypatch
):
    nb, _, rows = _strict(tmp_path, statement_off)
    _confirm(nb, rows, GONE, "override", None)
    _writes(nb, monkeypatch, fail=lambda answers: _confirmed_on(answers, OTHER))
    assert not rows.override.confirm_same(OTHER)  # at 3ac71c8: True, tags saved, review not
    assert _untagged(nb, OTHER) and OTHER not in rows.override.confirmed
    assert OTHER in rows.listed() and not nb.save_experiment()


def test_the_save_stays_closed_while_the_review_is_not_saved(tmp_path, statement_off):
    nb, _, rows = _strict(tmp_path, statement_off)
    _confirm(nb, rows, GONE, "override", None)
    rows.override.same_buttons[OTHER].click()
    answer_all(nb)
    assert not rows.listed() and not nb.save_button.disabled
    rows.unsaved = True  # a later write of the review was refused
    nb._refresh_check_save()
    assert nb.save_button.disabled  # at 3ac71c8: enabled, the review not in the draft
    assert "tag review is not saved" in nb.save_blocker.value
    assert not nb.save_experiment()
    nb.channel_grid.split(OTHER)
    nb.channel_grid.cells[(OTHER, 2)].value = None  # any saved tag change retries the review
    assert not rows.unsaved and OTHER in rows.listed()


# Blocker 3: a channel-count change ------------------------------------------------------------


def _count_changed(nb, root, key, names, kinds, seed):
    """Rewrite one unit's image with another number of channels and check the folder again."""
    write_channels(root.joinpath(*key, "a.tif"), names, kinds, seed=seed)
    _recheck(nb)


def _choose_again(nb, rows, key, count):
    """Open the unit's override after a count change: blank dropdowns, nothing raises."""
    rows.override.buttons[key].click()  # at 3ac71c8 (3 to 4 channels): IndexError
    assert rows.override.editing == key
    assert rows.override.drafts[key] == [None] * count  # never the old tags cut or padded
    assert [rows.override.cells[(key, i)].value for i in range(count)] == [None] * count
    assert "choose every channel again" in _text(rows)
    assert not rows.override.confirm()  # nothing chosen yet: refused, still listed
    assert key in rows.listed() and _untagged(nb, key)


def test_a_refused_unit_with_a_channel_more_asks_every_channel_again(tmp_path, statement_off):
    root, masks, out = _experiment(tmp_path, names=_like((GONE, OTHER), False))
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    _set_row(nb, ("rep2", 3), TAGS)
    rows = nb.channel_grid.by_repeat
    assert GONE in rows.listed() and len(rows.refused[GONE].tags) == 3
    _count_changed(nb, root, GONE, ["P", "Q", "Ch3", "W"], KINDS4, UNITS.index(GONE) + 1)
    assert GONE in rows.listed() and GONE in rows.refused
    _choose_again(nb, rows, GONE, 4)
    for index, tag in enumerate(EXTRA):
        rows.override.cells[(GONE, index)].value = tag
    assert rows.override.confirm() and GONE not in rows.listed()
    assert nb.draft_answers.units[GONE].channels == EXTRA


def test_a_refused_unit_with_a_channel_less_asks_every_channel_again(tmp_path):
    nb, root, rows = _fast(tmp_path)
    assert len(rows.refused[GONE].tags) == 4
    _count_changed(nb, root, GONE, ["P", "Q", "Ch3"], KINDS4[:3], UNITS.index(GONE) + 1)
    assert GONE in rows.listed() and GONE in rows.refused
    _choose_again(nb, rows, GONE, 3)
    rows.override.drafts[GONE] = list(EXTRA)  # four tags for three channels: never cut
    assert not rows.override.confirm() and _untagged(nb, GONE)
    for index, tag in enumerate(TAGS):
        rows.override.cells[(GONE, index)].value = tag
    assert rows.override.confirm() and GONE not in rows.listed()
    assert nb.draft_answers.units[GONE].channels == TAGS
