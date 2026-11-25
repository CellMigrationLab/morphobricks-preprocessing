"""Tag by repeat: stale memory never overwrites the draft's saved tag review (review as).

External re-check as (24 November), blocker 1: after a tag write that set the whole tag review
(a row click's tags and refusals, an editor's tags and confirmation) succeeded, an interruption
before memory took it (``RepeatRows.adopt``) left the session's refusals and confirmations
behind the draft's. Reopening Channels in the same session then reconciled and persisted that
stale memory OVER the saved review: a persisted refusal or confirmation was erased and the save
gate passed again with the refused unit no longer listed. The same mixed state came from an
interruption inside the draft write itself (``_save_answers`` kept answers the file never got).

Memory now takes the draft's review again whenever it moved since memory last took it, before
anything reads or writes the review; a write of the answers interrupted before the file was
replaced leaves memory as the file is.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing.repeat_review import TagReview  # noqa: E402
from morphobricks_preprocessing.run import draft_file  # noqa: E402
from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import opened  # noqa: E402
from test_notebook_tag_by_repeat import TAGS, _experiment, _set_row  # noqa: E402
from test_notebook_tag_by_repeat_atomic import (  # noqa: E402
    REFUSED,
    Interrupted,
    _before_the_click,
    _draft,
    stop_before_adopt,
)
from test_notebook_tag_by_repeat_oneclick import GONE, LEAD, OTHER, _like  # noqa: E402

EARLIER = ("drug", "rep1", "t1")
"""Refused by the rep1 row before the interrupted click: its header P, Q contradicts X, Y."""


def _two_rows(key):
    if key == EARLIER:
        return ["P", "Q", "Ch3"]
    if key[1] == "rep1":
        return ["X", "Y", "Ch3"]
    return _like(REFUSED, False)(key)


def _went_on(rows):
    """The same session goes on: Channels reopens (reconcile) and the review is persisted."""
    rows.reconcile()
    rows.persist()


def _held(nb, out, keys):
    rows = nb.channel_grid.by_repeat
    review = _draft(out).tag_review or TagReview()
    assert set(keys) <= set(review.refused)
    assert all(k in rows.listed() and k in rows.standing() for k in keys)
    assert rows._review() == review and rows.saved()
    assert not nb.save_experiment()


def test_a_preexisting_refusal_does_not_hide_a_new_one_written_before_adopt(
    tmp_path, monkeypatch, statement_off
):
    exp = _experiment(tmp_path, names=_two_rows)
    nb = opened(*exp)
    set_labels(nb, list(TAGS))
    rows = nb.channel_grid.by_repeat
    _set_row(nb, ("rep1", 3), TAGS)  # refuses EARLIER: in memory and in the draft
    assert set(rows.refused) == {EARLIER}
    for drop, tag in zip(rows.drops[("rep2", 3)], TAGS, strict=True):
        drop.value = tag
    stop_before_adopt(rows, monkeypatch)
    with pytest.raises(Interrupted):
        rows.buttons[("rep2", 3)].click()
    assert {EARLIER, *REFUSED} <= set(_draft(exp[2]).tag_review.refused)
    assert set(rows.refused) == {EARLIER}  # memory is behind the draft
    nb.channel_grid.by_repeat.restore(nb.channel_grid.layouts)  # memory is not empty
    _went_on(rows)  # at cd1fd15: GONE and OTHER erased from the draft
    _held(nb, exp[2], {EARLIER, *REFUSED})
    _held(opened(*exp), exp[2], {EARLIER, *REFUSED})


@pytest.mark.parametrize("fast", [True, False])
def test_an_editor_confirmation_written_before_adopt_is_never_erased(
    tmp_path, request, monkeypatch, fast
):
    if not fast:
        request.getfixturevalue("statement_off")
    nb, exp, row, _ = _before_the_click(tmp_path, fast)
    rows = nb.channel_grid.by_repeat
    rows.buttons[row].click()  # refuses B: memory and draft
    assert set(REFUSED) <= set(rows.refused) and rows.saved()
    rows.override.buttons[OTHER].click()
    stop_before_adopt(rows, monkeypatch)
    with pytest.raises(Interrupted):
        rows.override.confirm_button.click()
    assert OTHER in _draft(exp[2]).tag_review.confirmed  # at cd1fd15 not in memory
    _went_on(rows)  # at cd1fd15: the confirmation erased from the draft
    review = _draft(exp[2]).tag_review
    assert OTHER in review.confirmed and OTHER in rows.override.confirmed
    assert OTHER not in rows.listed() and GONE in rows.listed()
    assert rows._review() == review and rows.saved()
    again = opened(*exp).channel_grid.by_repeat
    assert OTHER not in again.listed() and GONE in again.listed()


@pytest.mark.parametrize("types", [True, False])
@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("fast", [True, False])
def test_a_draft_write_interrupted_around_its_replacement_keeps_tags_and_review_together(
    tmp_path, request, monkeypatch, fast, when, types
):
    if not fast:
        request.getfixturevalue("statement_off")
    nb, exp, row, tags = _before_the_click(tmp_path, fast, types=types)
    rows, out = nb.channel_grid.by_repeat, exp[2]
    real, calls = draft_file.write_text_atomic, [0]

    def write(path, text):
        calls[0] += 1
        if calls[0] == 1 and when == "before":
            raise Interrupted  # nothing replaced: the draft is as before the click
        done = real(path, text)
        if calls[0] == 1:
            raise Interrupted  # the draft replaced, the kernel stops before the save returns
        return done

    monkeypatch.setattr(draft_file, "write_text_atomic", write)
    with pytest.raises(Interrupted):
        rows.buttons[row].click()
    monkeypatch.setattr(draft_file, "write_text_atomic", real)
    disk = _draft(out)
    a_saved = LEAD in disk.units and disk.units[LEAD].channels == tags
    assert a_saved == (when == "after")
    assert nb.draft_answers == disk  # memory is the file, whichever side the stop fell
    _went_on(rows)  # at cd1fd15: the stale review written with the click's tags (or over B)
    disk = _draft(out)
    a_saved = LEAD in disk.units and disk.units[LEAD].channels == tags
    b_refused = set(REFUSED) <= set((disk.tag_review or TagReview()).refused)
    assert a_saved == b_refused == (when == "after")
    assert rows._review() == (disk.tag_review or TagReview()) and rows.saved()
    if b_refused:
        _held(nb, out, REFUSED)
        _held(opened(*exp), out, REFUSED)


def test_unticking_after_a_write_stopped_past_its_replacement_keeps_the_refusal(
    tmp_path, monkeypatch
):
    nb, exp, row, tags = _before_the_click(tmp_path, True)
    assert nb._strict.draft is not None  # the strict page's draft is kept apart
    rows, out = nb.channel_grid.by_repeat, exp[2]
    real = draft_file.write_text_atomic

    def write(path, text):
        monkeypatch.setattr(draft_file, "write_text_atomic", real)
        real(path, text)
        raise Interrupted  # the draft replaced, the kernel stops before the save returns

    monkeypatch.setattr(draft_file, "write_text_atomic", write)
    with pytest.raises(Interrupted):
        rows.buttons[row].click()
    nb.channel_grid.same_channels.value = False  # the strict page's draft is written back
    disk = _draft(out)
    assert set(REFUSED) <= set(disk.tag_review.refused)  # at cd1fd15: erased with A's tags
    assert disk.units[LEAD].channels == tags  # the click on the row is the user's in both
    assert all(k in rows.listed() and k in rows.standing() for k in REFUSED)
