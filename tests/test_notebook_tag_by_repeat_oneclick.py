"""A refused unit counts only when the user confirms THAT unit (owner decision of 22 November).

Aliases saved by confirming or tagging another refused unit are not evidence for this one: it
stays listed with its refusal, in fast mode too (no header trust for it), and its editor keeps its
highlights. A listed unit with the same header names as a unit the user confirmed offers
**Confirm the same tags** (and **Confirm all N like it**): the same save and record as the
editor, one click each, nothing automatic.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing.notebook_channels_repeat import RepeatRows  # noqa: E402
from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import answer_all, opened  # noqa: E402
from test_notebook_tag_by_repeat import EXTRA, TAGS, _experiment, _set_row  # noqa: E402
from test_notebook_tag_by_repeat_recheck import _recheck, _rewrite_header  # noqa: E402

GONE, OTHER, THIRD = ("drug", "rep2", "t1"), ("drug", "rep2", "t2"), ("ctrl", "rep2", "t2")
LEAD = ("ctrl", "rep2", "t1")


def _like(units, fast):
    """Names where ``units`` share headers (P, Q, Ch3) that no label says; the others X, Y, Ch3."""

    def names(key):
        if key[1] != "rep2":
            return None
        own = ["P", "Q", "Ch3"] if key in units else ["X", "Y", "Ch3"]
        return [*own, "W"] if fast else own

    return names


def _fast(tmp_path, units=(GONE, OTHER)):
    root, masks, out = _experiment(tmp_path, extra=True, names=_like(units, True))
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    grid = nb.channel_grid
    assert grid.same_channels.value
    for index, tag in enumerate(TAGS):
        grid.matrix.template_cells[(("ctrl", "rep1", "t1"), index)].value = tag
    _set_row(nb, ("rep2", 4), EXTRA)
    assert set(grid.by_repeat.listed()) == set(units)
    return nb, root, grid.by_repeat


def _strict(tmp_path, statement_off):
    root, masks, out = _experiment(tmp_path, names=_like((GONE, OTHER), False))
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    _set_row(nb, ("rep2", 3), TAGS)
    rows = nb.channel_grid.by_repeat
    assert set(rows.listed()) == {GONE, OTHER}
    return nb, root, rows


def _confirm(nb, rows, key, how, tags):
    """Confirm one listed unit: its editor's Confirm, or its cells tagged by hand."""
    if how == "override":
        rows.override.buttons[key].click()
        rows.override.confirm_button.click()
        assert key in rows.override.confirmed
    else:
        nb.channel_grid.split(key)  # out of its block: a click on a block tags every unit in it
        for index, tag in enumerate(tags):
            nb.channel_grid.cells[(key, index)].value = tag


def _text(rows):
    out = []

    def walk(items):
        for w in items:
            if isinstance(getattr(w, "value", None), str):
                out.append(w.value)
            if isinstance(getattr(w, "description", None), str):
                out.append(w.description)
            walk(getattr(w, "children", ()))

    walk(rows.box.children)
    return " ".join(out)


@pytest.mark.parametrize("how", ["override", "hand"])
def test_fast_mode_another_refused_unit_is_not_tagged_from_aliases_after_a_recheck(tmp_path, how):
    nb, _, rows = _fast(tmp_path)
    grid = nb.channel_grid
    _confirm(nb, rows, GONE, how, EXTRA)
    assert GONE not in rows.listed() and OTHER in rows.listed()
    _recheck(nb)  # fast mode trusts headers: the aliases saved for GONE say P, Q for its labels
    assert set(rows.listed()) == {OTHER} and OTHER in rows.refused
    assert (
        not any(nb.draft_answers.units[OTHER].channels) if OTHER in nb.draft_answers.units else True
    )
    assert OTHER not in grid.matrix.own
    assert not grid.complete and not nb.save_experiment()
    _set_row(nb, ("rep2", 4), EXTRA)  # the row again: still not a confirmation of OTHER
    assert set(rows.listed()) == {OTHER} and not grid.complete


def test_fast_mode_one_click_counts_the_unit_with_the_confirmed_tags(tmp_path):
    nb, _, rows = _fast(tmp_path)
    grid, ov = nb.channel_grid, rows.override
    assert not ov.same_buttons  # nothing confirmed yet: nothing to offer
    _confirm(nb, rows, GONE, "override", EXTRA)
    _recheck(nb)
    text = _text(rows)
    assert (
        "same headers as drug / rep2 / t1, which you confirmed as DNA, ACTIN, PHASE, EXTRA" in text
    )
    assert set(ov.same_buttons) == {OTHER} and not ov.all_buttons  # one listed unit: no "all"
    assert OTHER in rows.listed()  # nothing counts until clicked
    ov.same_buttons[OTHER].click()
    assert not rows.listed()
    assert nb.draft_answers.units[OTHER].channels == EXTRA and OTHER in grid.matrix.own
    assert ov.confirmed[OTHER].tags == EXTRA and ov.confirmed[OTHER].names == ("P", "Q", "Ch3", "W")
    answer_all(nb)
    assert nb.draft_answers.units[OTHER].channels_from == "unit"
    assert nb.save_experiment()


def test_confirm_all_confirms_every_listed_unit_like_the_confirmed_one(tmp_path):
    nb, _, rows = _fast(tmp_path, (GONE, OTHER, THIRD))
    ov = rows.override
    _confirm(nb, rows, GONE, "override", EXTRA)
    assert set(ov.same_buttons) == {OTHER, THIRD} and set(ov.all_buttons) == {GONE}
    assert ov.all_buttons[GONE].description.strip() == "Confirm all 2 like drug / rep2 / t1"
    _recheck(nb)
    assert set(rows.listed()) == {OTHER, THIRD}  # untouched by the confirmation and the re-check
    ov.all_buttons[GONE].click()
    assert not rows.listed() and {OTHER, THIRD} <= set(ov.confirmed)
    assert all(nb.draft_answers.units[k].channels == EXTRA for k in (GONE, OTHER, THIRD))


def test_confirm_all_confirms_none_when_any_would_be_refused(tmp_path, monkeypatch):
    nb, _, rows = _fast(tmp_path, (GONE, OTHER, THIRD))
    ov = rows.override
    _confirm(nb, rows, GONE, "override", EXTRA)
    before = nb.draft_answers
    monkeypatch.setattr(RepeatRows, "_row_error", staticmethod(lambda tags: "refused"))
    assert not ov.confirm_all(GONE) and not ov.confirm_same(OTHER)
    assert nb.draft_answers == before and set(ov.confirmed) == {GONE}
    assert set(rows.listed()) == {OTHER, THIRD}
    monkeypatch.undo()
    monkeypatch.setattr(type(nb.channel_grid), "commit", lambda self, tags, cells, then=None: False)
    assert not ov.confirm_all(GONE)
    assert set(ov.confirmed) == {GONE} and set(rows.listed()) == {OTHER, THIRD}


@pytest.mark.parametrize("how", ["override", "hand"])
def test_strict_mode_the_other_unit_keeps_its_refusal_text_and_highlights(
    tmp_path, statement_off, how
):
    nb, _, rows = _strict(tmp_path, statement_off)
    text_before = rows.listed()[OTHER]
    assert "header ‘P’" in text_before
    _confirm(nb, rows, GONE, how, TAGS)
    assert set(rows.listed()) == {OTHER}
    assert rows.listed()[OTHER] == text_before  # not "its own tags are incomplete"
    _recheck(nb)
    assert rows.listed()[OTHER] == text_before and "incomplete" not in rows.refused[OTHER].text
    assert not nb.channel_grid.complete
    offered = _text(rows)
    rows.override.buttons[OTHER].click()
    text = _text(rows)
    assert "#D97706" in text and "C0: header ‘P’" in text and "C1: header ‘Q’" in text
    assert "C2: header" not in text
    if how == "override":
        assert "same headers as drug / rep2 / t1" in offered  # offered, not applied
        assert nb.draft_answers.units.get(OTHER) is None or not any(
            nb.draft_answers.units[OTHER].channels
        )


def test_a_refused_unit_whose_own_header_changes_to_match_its_row_still_clears(
    tmp_path, statement_off
):
    nb, root, rows = _strict(tmp_path, statement_off)
    _rewrite_header(root, OTHER, ["X", "Y", "Ch3"])  # now the row's own names
    _recheck(nb)
    assert OTHER in rows.listed()  # listed while its own tags are incomplete, as before
    _set_row(nb, ("rep2", 3), TAGS)  # the row fills it: nothing contradicts any more
    assert set(rows.listed()) == {GONE} and OTHER not in rows.refused
    assert nb.draft_answers.units[OTHER].channels == TAGS


def test_a_unit_that_is_not_refused_is_still_tagged_by_hand_and_by_trusted_headers(
    tmp_path, statement_off
):
    nb, _, rows = _strict(tmp_path, statement_off)
    grid = nb.channel_grid
    assert rows.standing() == {GONE, OTHER}
    assert LEAD not in rows.refused
    assert nb.draft_answers.units[LEAD].channels == TAGS  # filled by the row, as today
    grid.split(GONE)
    for index, tag in enumerate(TAGS):
        grid.cells[(GONE, index)].value = tag  # hand-tagging a refused unit is its confirmation
    assert set(rows.listed()) == {OTHER}
