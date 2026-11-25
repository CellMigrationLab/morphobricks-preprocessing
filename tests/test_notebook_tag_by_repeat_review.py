"""Review of the per-unit override: a changed label, a new session, the current reference.

The owner rule of 22 November: a refused unit counts only when the user confirms THAT unit, in
every session. The refusals and confirmations are kept in the draft file (``tag_review``), and a
label renamed or removed after a refusal never breaks the override editor.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing.description_file import DRAFT_FILENAME  # noqa: E402
from morphobricks_preprocessing.draft_format import answers_from_bytes  # noqa: E402
from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import opened  # noqa: E402
from test_notebook_tag_by_repeat import EXTRA, TAGS, _experiment, _set_row  # noqa: E402
from test_notebook_tag_by_repeat_oneclick import (  # noqa: E402
    GONE,
    OTHER,
    _confirm,
    _fast,
    _like,
    _strict,
    _text,
)


def _rename(nb, old, new):
    """Rename one label on the labels card (open it, retype the label, confirm again)."""
    card = nb.channel_labels
    card.confirm.button.click()
    row = next(r for r in card.rows if r.id_box.value == old)
    row.id_box.value = new
    card.confirm.button.click()
    assert card.confirmed and new in nb.channel_grid.labels


def _picked(rows, key, tags):
    """Choose each tag in the open editor's dropdowns (nothing is saved before Confirm)."""
    for index, tag in enumerate(tags):
        rows.override.cells[(key, index)].value = tag


def _renamed_then_override(nb, rows, old, tags):
    """Rename ``old`` after the refusal; the override opens blank there and confirms."""
    assert old in rows.refused[GONE].tags
    _rename(nb, old, "RENAMED")
    assert old in rows.refused[GONE].tags  # the refusal is as it was; the label is gone
    rows.override.buttons[GONE].click()  # at 4bdb64e: TraitError, the editor never opens
    index = rows.refused[GONE].tags.index(old)
    assert rows.override.editing == GONE
    assert rows.override.drafts[GONE][index] is None  # blank, never the old label
    nb.channel_grid.refresh()  # every later refresh worked (it raised, and kept raising)
    assert rows.override.cells[(GONE, index)].value is None
    renamed = ["RENAMED" if t == old else t for t in tags]
    _picked(rows, GONE, renamed)
    assert rows.override.confirm() and GONE not in rows.listed()
    assert nb.draft_answers.units[GONE].channels == tuple(renamed)
    assert rows.override.confirmed[GONE].tags == tuple(renamed)


def test_fast_a_label_renamed_after_the_refusal_opens_the_override_blank_and_it_confirms(tmp_path):
    nb, _, rows = _fast(tmp_path)
    _renamed_then_override(nb, rows, EXTRA[3], EXTRA)


def test_strict_a_label_renamed_after_the_refusal_opens_the_override_blank_and_it_confirms(
    tmp_path, statement_off
):
    nb, _, rows = _strict(tmp_path, statement_off)
    _renamed_then_override(nb, rows, TAGS[2], TAGS)


def test_a_label_renamed_while_the_editor_is_open_is_blanked_not_raised(tmp_path):
    nb, _, rows = _fast(tmp_path)
    rows.override.buttons[GONE].click()
    assert rows.override.drafts[GONE][3] == "EXTRA"
    _rename(nb, "EXTRA", "RENAMED")  # the labels card is above the editor
    assert rows.override.editing == GONE and rows.override.drafts[GONE][3] is None
    assert rows.override.cells[(GONE, 3)].value is None
    assert not rows.override.confirm()  # not every channel is tagged: refused, editor stays
    assert rows.override.editing == GONE


def test_the_one_click_offer_never_offers_a_label_that_is_gone(tmp_path):
    nb, _, rows = _fast(tmp_path)
    _confirm(nb, rows, GONE, "override", EXTRA)
    assert OTHER in rows.override.same_buttons
    _rename(nb, "EXTRA", "RENAMED")
    assert not rows.override.same_buttons and not rows.override.all_buttons
    assert "which you confirmed as" not in _text(rows)
    assert not rows.override.confirm_same(OTHER)
    assert OTHER in rows.listed()


# A new session on the same draft ------------------------------------------------------------


def _saved_fast(tmp_path):
    """Fast mode: GONE and OTHER refused, GONE confirmed (aliases P, Q saved), draft on disk."""
    root, masks, out = _experiment(tmp_path, extra=True, names=_like((GONE, OTHER), True))
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    for index, tag in enumerate(TAGS):
        nb.channel_grid.matrix.template_cells[(("ctrl", "rep1", "t1"), index)].value = tag
    _set_row(nb, ("rep2", 4), EXTRA)
    rows = nb.channel_grid.by_repeat
    assert set(rows.listed()) == {GONE, OTHER}
    _confirm(nb, rows, GONE, "override", EXTRA)
    return nb, root, masks, out


def test_a_new_notebook_on_the_draft_keeps_the_refused_unit_refused(tmp_path):
    _, root, masks, out = _saved_fast(tmp_path)
    nb = opened(root, masks, out)  # a fresh notebook (a kernel restart) on the saved draft
    grid, rows = nb.channel_grid, nb.channel_grid.by_repeat
    assert grid.same_channels.value
    assert set(rows.listed()) == {OTHER} and set(rows.refused) == {GONE, OTHER}
    assert set(rows.override.confirmed) == {GONE}
    own = nb.draft_answers.units.get(OTHER)
    assert own is None or not any(own.channels)  # not tagged from the aliases saved for GONE
    assert OTHER not in grid.matrix.own and not grid.complete
    assert not nb.save_experiment()
    assert set(rows.override.same_buttons) == {OTHER}  # the one-click offer is still made
    rows.override.same_buttons[OTHER].click()
    assert not rows.listed() and nb.draft_answers.units[OTHER].channels == EXTRA


def test_the_review_is_in_the_draft_file_and_a_draft_without_it_opens_as_before(tmp_path):
    nb, root, masks, out = _saved_fast(tmp_path)
    review = answers_from_bytes((out / DRAFT_FILENAME).read_bytes()).tag_review
    assert set(review.refused) == {GONE, OTHER} and set(review.confirmed) == {GONE}
    assert review.confirmed[GONE].tags == EXTRA and review.refused[OTHER].reference == (
        "ctrl",
        "rep2",
        "t1",
    )
    assert review.refused[OTHER].tags == EXTRA  # the row's tags at the click
    rows = nb.channel_grid.by_repeat
    rows.refused.clear()
    rows.override.confirmed.clear()
    rows.persist()  # nothing refused any more: the section is not written at all
    assert b"tag_review" not in (out / DRAFT_FILENAME).read_bytes()
    fresh = opened(root, masks, out)
    assert not fresh.channel_grid.by_repeat.refused
    assert not fresh.channel_grid.by_repeat.override.confirmed


def test_a_refused_unit_that_was_confirmed_by_hand_is_restored_listed_no_more(tmp_path):
    nb, root, masks, out = _saved_fast(tmp_path)
    assert GONE in nb.channel_grid.by_repeat.refused
    fresh = opened(root, masks, out)
    assert GONE in fresh.channel_grid.by_repeat.refused  # the refusal is kept
    assert GONE not in fresh.channel_grid.by_repeat.listed()  # its confirmation still holds


# The reference is the current one ---------------------------------------------------------


def test_standing_reads_the_row_as_it_is_now_not_as_at_the_click(tmp_path):
    nb, _, rows = _fast(tmp_path)
    layouts = {u.key: u for u in nb.channel_grid.layouts}
    assert rows.standing() == {GONE, OTHER}
    # The click-time reference had the names OTHER has (P, Q), so nothing contradicted: the row's
    # reference now is the first unit of the row (X, Y), which OTHER's header contradicts.
    rows.refused[OTHER] = rows.refused[OTHER]._replace(lead=layouts[GONE])
    assert OTHER in rows.standing()  # at 4bdb64e: not standing, so tagged from its header names
    _confirm(nb, rows, GONE, "override", EXTRA)  # the aliases P, Q are saved for GONE
    nb.channel_grid.matrix.trust_headers()
    assert OTHER in rows.standing() and OTHER not in nb.channel_grid.matrix.own
    own = nb.draft_answers.units.get(OTHER)
    assert own is None or not any(own.channels)


def test_confirm_all_buttons_keep_the_order_of_the_listed_units(tmp_path, monkeypatch):
    nb, _, rows = _fast(tmp_path)
    sources = [("s", f"rep{n}", "t1") for n in (7, 3, 9, 1, 8, 2)]  # no order a set would give
    likes = {("u", src[1], f"t{n}"): (src, EXTRA) for src in sources for n in (1, 2)}
    monkeypatch.setattr(type(rows.override), "_likes", lambda self, offered, current: likes)
    rows.override.build(4)
    assert list(rows.override.all_buttons) == sources
