"""Tag by repeat: a click that fills no unit and refuses all is never lost after its write (av).

External re-check av (24 November), blocker 1: when a repeat folder's row refuses every unit
it offers (strict mode, every header contradicts the row's tags), the click fills nothing and
writes only the tag review (``RepeatRows._save_review``, one draft write). That write
acknowledged the saved review in memory (``ReviewMemory.seen``) before memory took the
refusals (``adopt``). An interruption between the two left the draft with the refusals, memory
without them, and nothing for :meth:`.ReviewMemory.sync` to notice: the same session then
reconciled and persisted the stale memory over the draft, erasing the refusals. A review write
is now acknowledged only together with memory taking it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import answer_all, opened  # noqa: E402
from test_notebook_tag_by_repeat import TAGS, UNITS, _experiment, _set_row  # noqa: E402
from test_notebook_tag_by_repeat_atomic import (  # noqa: E402
    Interrupted,
    _draft,
    stop_before_adopt,
)
from test_notebook_tag_by_repeat_stale import _held  # noqa: E402

SWAPPED = ("ACTIN", "DNA", "PHASE")
"""The rep2 row's tags: every rep2 header names DNA, ACTIN, PHASE in this order (refused)."""

REP2 = tuple(k for k in UNITS if k[1] == "rep2")
EARLIER = ("drug", "rep1", "t1")
"""Refused by the rep1 row before (the "preexisting review"): its header P, Q contradicts."""


def _names(key):
    if key == EARLIER:
        return ["P", "Q", "PHASE"]
    return ["DNA", "ACTIN", "PHASE"]


def _writes(nb):
    """Count the draft writes the notebook makes from now on."""
    count, save = [0], nb._save_description

    def counted():
        count[0] += 1
        return save()

    nb._save_description = counted
    return count


@pytest.mark.parametrize("preexisting", [False, True])
def test_a_review_only_click_stopped_before_adopt_keeps_every_refusal(
    tmp_path, monkeypatch, statement_off, preexisting
):
    root, masks, out = _experiment(tmp_path, names=_names)
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    rows = nb.channel_grid.by_repeat
    assert not nb.channel_grid.same_channels.value  # strict mode
    if preexisting:
        _set_row(nb, ("rep1", 3), TAGS)  # fills rep1 but EARLIER: a review already saved
        assert set(rows.refused) == {EARLIER} == set(_draft(out).tag_review.refused)
    before = dict(rows.refused)
    writes = _writes(nb)
    for drop, tag in zip(rows.drops[("rep2", 3)], SWAPPED, strict=True):
        drop.value = tag
    stop_before_adopt(rows, monkeypatch)  # after the write
    with pytest.raises(Interrupted):
        rows.buttons[("rep2", 3)].click()
    assert writes[0] == 1  # save={}: the review alone, one draft write
    disk = _draft(out).tag_review
    assert set(REP2) <= set(disk.refused)
    assert not any(nb.draft_answers.units.get(k) for k in REP2)  # nothing filled
    assert rows.refused == before  # memory is behind the draft (the exposure)
    # The same session goes on: every authority reader, then Channels reopening and a persist.
    # At 8a9ac63 memory had acknowledged the review already: nothing was taken, and reconcile
    # and persist wrote the stale memory over the draft, erasing every rep2 refusal.
    assert set(REP2) <= set(rows.listed()) and set(REP2) <= rows.standing()
    rows.reconcile()
    rows.persist()
    keys = {*REP2, EARLIER} if preexisting else set(REP2)
    _held(nb, out, keys)
    _held(opened(root, masks, out), out, keys)  # a restart restores them


def test_after_the_stop_only_an_action_on_each_unit_releases_it(
    tmp_path, monkeypatch, statement_off
):
    root, masks, out = _experiment(tmp_path, names=_names)
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    rows = nb.channel_grid.by_repeat
    for drop, tag in zip(rows.drops[("rep2", 3)], SWAPPED, strict=True):
        drop.value = tag
    stop_before_adopt(rows, monkeypatch)  # after the write
    with pytest.raises(Interrupted):
        rows.buttons[("rep2", 3)].click()
    rows.reconcile()
    rows.persist()
    _set_row(nb, ("rep1", 3), TAGS)  # another row: never a release of rep2
    assert set(REP2) <= set(_draft(out).tag_review.refused)
    assert set(REP2) <= set(rows.listed())
    for key in REP2:  # the explicit action on each unit
        rows.override.buttons[key].click()
        rows.override.confirm_button.click()
        assert key not in rows.listed()
    assert set(REP2) <= set(_draft(out).tag_review.confirmed)
    answer_all(nb)
    assert nb.save_experiment()
