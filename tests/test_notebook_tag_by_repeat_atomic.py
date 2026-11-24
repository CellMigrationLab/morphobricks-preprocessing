"""Tag by repeat: a row click saves its tags and its refusals in ONE draft write (review an).

External re-check of 23 November, blocker 1: a row click that filled some units (A) and refused
others (B) wrote A's tags first and B's refusal in a second write. A failure or an interruption
between the two left a draft with A's tags and no refusal of B; after a restart nothing kept B
out of fast mode's header trust. The draft must hold A's tags and B's refusal, or neither.

External re-check ao (24 November), blocker 1: in fast mode the click's statement save (a second
write, after the one that holds the tags and the review) ran before memory took the refusals. An
interruption there left B refused on disk but not in this session, whose save gate reads memory:
with B's tags complete from before the click, the description could be published. Memory now
takes the write at once, and the save is refused while memory and the draft's review differ.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing.description_file import DRAFT_FILENAME  # noqa: E402
from morphobricks_preprocessing.draft_answers import ChannelAnswer  # noqa: E402
from morphobricks_preprocessing.draft_format import answers_from_bytes  # noqa: E402
from morphobricks_preprocessing.repeat_review import TagReview  # noqa: E402
from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import answer_all, opened  # noqa: E402
from test_notebook_tag_by_repeat import EXTRA, TAGS, _experiment  # noqa: E402
from test_notebook_tag_by_repeat_oneclick import GONE, LEAD, OTHER, _like  # noqa: E402

REFUSED = (GONE, OTHER)
"""B: their header names P, Q contradict the row's reference (X, Y): the row refuses them."""


class Interrupted(BaseException):
    """The kernel stopped during a write (as ``KeyboardInterrupt`` would)."""


def _draft(out):
    return answers_from_bytes((out / DRAFT_FILENAME).read_bytes())


def _before_the_click(tmp_path, fast, b_tagged=False, types=True):
    """The row of rep2 set but not clicked yet; A (LEAD) untagged, nothing refused.

    Every image type is answered, so that in fast mode the click also saves the statement (the
    units it fills are marked as tagged on their own): a second write. With ``b_tagged`` B holds
    complete tags from before the click (DNA and ACTIN swapped: the row refuses them). With
    ``types`` False no image type is answered.
    """
    root, masks, out = _experiment(tmp_path, extra=fast, names=_like(REFUSED, fast))
    nb = opened(root, masks, out)
    tags, row = (EXTRA, ("rep2", 4)) if fast else (TAGS, ("rep2", 3))
    set_labels(nb, list(tags))
    grid = nb.channel_grid
    assert grid.same_channels.value == fast
    for layout in grid.layouts if types else ():
        nb.channel_types.radios[layout.key].value = "channels"
    if fast:
        for index, tag in enumerate(TAGS):
            grid.matrix.template_cells[(("ctrl", "rep1", "t1"), index)].value = tag
    for key in REFUSED if b_tagged else ():
        grid.split(key)  # a hand-tag of B on its own, before the click
        for index, tag in enumerate((tags[1], tags[0], *tags[2:])):
            grid.cells[(key, index)].value = tag
    for drop, tag in zip(grid.by_repeat.drops[row], tags, strict=True):
        drop.value = tag
    assert not grid.by_repeat.refused and _untagged(_draft(out), LEAD)
    return nb, (root, masks, out), row, tags


def _click(nb, row, at, how):
    """Click the row; the draft write number ``at`` fails (refused, or the kernel stops)."""
    count, save = [0], nb._save_description

    def write():
        count[0] += 1
        if count[0] == at:
            if how == "interrupted":
                raise Interrupted
            nb._save_problem = "the disk refused the write (test)"
            return False
        return save()

    nb._save_description = write
    try:
        nb.channel_grid.by_repeat.buttons[row].click()
    except Interrupted:
        return count[0], False
    finally:
        nb._save_description = save
    return count[0], True


def _aliases_of_another_unit(nb):
    """Save P and Q as aliases of DNA and ACTIN, as confirming another unit would."""
    answers = nb.draft_answers
    channels = dict(answers.channels)
    for label, name in (("DNA", "P"), ("ACTIN", "Q")):
        old = channels[label]
        channels[label] = replace(old, header_names=(*(old.header_names or ()), name))
    assert isinstance(channels["DNA"], ChannelAnswer)
    assert nb.channel_grid.store.update(replace(answers, channels=channels))


def _untagged(answers, key):
    own = answers.units.get(key)
    return own is None or not any(own.channels)


@pytest.mark.parametrize("how", ["refused", "interrupted"])
@pytest.mark.parametrize("at", [1, 2, 3])
@pytest.mark.parametrize("fast", [True, False])
def test_a_row_click_saves_a_tags_and_b_refusal_together_or_neither(
    tmp_path, request, fast, at, how
):
    if not fast:
        request.getfixturevalue("statement_off")
    nb, exp, row, tags = _before_the_click(tmp_path, fast)
    rows = nb.channel_grid.by_repeat
    before = rows._review()
    writes, alive = _click(nb, row, at, how)
    disk = _draft(exp[2])
    review = disk.tag_review or TagReview()
    a_saved = LEAD in disk.units and disk.units[LEAD].channels == tags
    b_refused = set(REFUSED) <= set(review.refused)
    # at 40aca1d, failing the second write: A's tags on disk, B's refusal not
    assert a_saved == b_refused, (writes, a_saved, b_refused)
    # this session, interrupted or not: memory is the draft's, never behind or ahead of it
    assert rows._review() == review and not rows.unsaved, (writes, alive)
    if b_refused:  # at 30af180, interrupted at the statement save: B refused on disk only
        assert all(k in rows.listed() and k in rows.standing() for k in REFUSED)
    if alive and not a_saved:
        said = nb.channel_grid.problem.value + rows.message
        assert rows._review() == before and "Not saved" in said
    fresh = opened(*exp)  # a restart on the draft as it is on disk
    if not b_refused:  # nothing of the click was kept: the click is to be made again
        assert _untagged(fresh.draft_answers, LEAD) and not fresh.channel_grid.by_repeat.refused
        return
    _aliases_of_another_unit(fresh)  # another unit's aliases now fit B's header
    again = opened(*exp)  # a restart: fast mode trusts the headers it can
    grid, rows = again.channel_grid, again.channel_grid.by_repeat
    for key in REFUSED:  # B stays blocked: never tagged from its header
        assert _untagged(again.draft_answers, key) and key not in grid.matrix.own
        assert key in rows.listed() and key in rows.standing()
    assert not grid.complete and not again.save_experiment()
    rows.override.buttons[OTHER].click()  # the explicit action on B
    rows.override.confirm_button.click()
    assert OTHER not in rows.listed() and GONE in rows.listed()
    assert OTHER in _draft(exp[2]).tag_review.confirmed


def _publishable_but_for_b(nb, tags):
    """Answer every other question; the cells a redraw did not show are A's, saved already."""
    grid = nb.channel_grid
    for (key, index), cell in list(grid.cells.items()):
        if cell.value is None and key not in REFUSED:
            cell.value = tags[index]
    answer_all(nb)
    assert all(None not in nb.draft_answers.units[k].channels for k in REFUSED)


def _blocked_by_b(nb, out):
    rows = nb.channel_grid.by_repeat
    assert nb.save_button.disabled and not nb.save_experiment()
    assert set(REFUSED) <= set(_draft(out).tag_review.refused)
    assert all(k in rows.listed() and k in rows.standing() for k in REFUSED)


def test_b_tagged_before_the_click_stays_refused_after_an_interrupted_statement_save(tmp_path):
    nb, exp, row, tags = _before_the_click(tmp_path, True, b_tagged=True)
    rows = nb.channel_grid.by_repeat
    writes, alive = _click(nb, row, 2, "interrupted")  # the statement save after the tags
    assert (writes, alive) == (2, False)
    assert set(REFUSED) <= set(_draft(exp[2]).tag_review.refused)  # durable
    _publishable_but_for_b(nb, tags)
    _blocked_by_b(nb, exp[2])  # at 30af180 the save succeeded in this very session
    assert rows._review() == _draft(exp[2]).tag_review  # at 30af180: B refused on disk only
    _blocked_by_b(opened(*exp), exp[2])  # and after a restart


def stop_before_adopt(rows, monkeypatch):
    """The kernel stops right after the next tag write succeeded, before memory takes it."""
    adopt = rows.adopt

    def stopped():
        monkeypatch.setattr(rows, "adopt", adopt)
        raise Interrupted

    monkeypatch.setattr(rows, "adopt", stopped)


def test_the_save_waits_while_memory_differs_from_the_saved_review(tmp_path, monkeypatch):
    nb, exp, row, tags = _before_the_click(tmp_path, True, b_tagged=True)
    rows = nb.channel_grid.by_repeat
    stop_before_adopt(rows, monkeypatch)
    with pytest.raises(Interrupted):
        rows.buttons[row].click()
    assert set(REFUSED) <= set(_draft(exp[2]).tag_review.refused) and not rows.refused
    # review as: the same session goes on. Channels reopening reconciles and persists; at cd1fd15
    # both wrote the stale memory over the saved review, erasing B's refusal from the draft
    rows.reconcile()
    rows.persist()
    assert set(REFUSED) <= set(_draft(exp[2]).tag_review.refused)
    assert all(k in rows.listed() and k in rows.standing() for k in REFUSED)
    assert rows._review() == _draft(exp[2]).tag_review and rows.saved()
    _publishable_but_for_b(nb, tags)
    _blocked_by_b(nb, exp[2])  # at cd1fd15 the save went through, B no longer listed
    for key in REFUSED:  # the explicit action on each unit of B
        rows.override.buttons[key].click()
        rows.override.confirm_button.click()
        assert key not in rows.listed()
    assert set(REFUSED) <= set(_draft(exp[2]).tag_review.confirmed)
    answer_all(nb)  # the design questions the new tags asked again
    assert nb.save_experiment()  # only now


# After the one write: no second statement save or redraw (review an, FILE) ----------------------

FILLED = (LEAD, ("ctrl", "rep2", "t2"))
"""The rep2 units the row fills (their header fits it)."""


def test_a_fast_row_click_marks_its_units_in_its_one_redraw(tmp_path, monkeypatch):
    nb, exp, row, _ = _before_the_click(tmp_path, True)
    for layout in nb.channel_grid.layouts:  # the image types: the markers need them
        nb.channel_types.radios[layout.key].value = "channels"
    grid, calls = nb.channel_grid, []
    refresh, sync = grid.refresh, grid.matrix.sync_statement
    monkeypatch.setattr(grid, "refresh", lambda: calls.append("refresh") or refresh())
    monkeypatch.setattr(grid.matrix, "sync_statement", lambda *a: calls.append("sync") or sync(*a))
    grid.by_repeat.buttons[row].click()
    assert sorted(calls) == ["refresh", "sync"]  # at 1e4c3d6: each twice
    assert all(_draft(exp[2]).units[k].channels_from == "unit" for k in FILLED)
    assert set(FILLED) <= grid.matrix.own


def test_a_refused_fast_row_click_switches_no_unit_to_its_own(tmp_path):
    nb, _, row, _ = _before_the_click(tmp_path, True)
    _click(nb, row, 1, "refused")
    assert not set(FILLED) & nb.channel_grid.matrix.own
