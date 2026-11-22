"""Override one listed unit of *Tag by repeat*, starting from its row's tags (owner requirement).

A unit whose header disagrees with its repeat's row is listed. The user can override it from
the row's tags, with the disagreements highlighted; it counts as tagged only after Confirm,
and the confirmation lapses when the unit's tags, its header, the reference's header or the
saved aliases change.
"""

from __future__ import annotations

import pytest

from morphobricks_preprocessing.channel_labels import UnitLayout, tags_of
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.repeat_tags import Override, header_names, override_holds

LABELS = ("DNA", "ACTIN", "PHASE")


def _layout(key, names):
    return UnitLayout(key, "/".join(key) + "/a.tif", 3, tuple(names))


def test_pure_a_confirmation_holds_only_while_tags_and_the_units_own_header_are_unchanged():
    unit = _layout(("drug", "rep2", "t1"), ("P", "Q", "Ch3"))
    confirmed = Override(LABELS, header_names(unit))
    assert confirmed.names == ("P", "Q", "Ch3")
    assert override_holds(confirmed, unit, LABELS)
    assert not override_holds(None, unit, LABELS)
    assert not override_holds(confirmed, unit, ("ACTIN", "DNA", "PHASE"))  # other tags
    assert not override_holds(confirmed, unit, (*LABELS[:2], None))
    moved = _layout(unit.key, ("P", "R", "Ch3"))  # the unit's header changed
    assert not override_holds(confirmed, moved, LABELS)


pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import answer_all, opened  # noqa: E402
from test_notebook_tag_by_repeat import TAGS, UNITS, _experiment, _set_row  # noqa: E402
from test_notebook_tag_by_repeat_recheck import (  # noqa: E402
    _recheck,
    _rewrite_header,
    _two_odd,
)

GONE, KEPT = ("drug", "rep2", "t1"), ("drug", "rep1", "t1")
LEAD = ("ctrl", "rep2", "t1")


def _listed(tmp_path):
    root, masks, out = _experiment(tmp_path, names=_two_odd)
    nb = opened(root, masks, out)
    set_labels(nb, [*TAGS, "EXTRA"])  # one more label, to change a tag without a duplicate
    _set_row(nb, ("rep1", 3), TAGS)
    _set_row(nb, ("rep2", 3), TAGS)
    rows = nb.channel_grid.by_repeat
    assert set(rows.listed()) == {GONE, KEPT}
    return nb, root, out, rows


def _html(rows, key):
    return " ".join(
        w.value
        for w in _walk(rows.override.build(3))
        if hasattr(w, "value") and isinstance(w.value, str)
    )


def _walk(widgets_):
    for w in widgets_:
        yield w
        yield from _walk(getattr(w, "children", ()))


def _open_editor(nb, rows, key=GONE):
    rows.override.buttons[key].click()
    assert rows.override.editing == key
    return rows.override


def _confirm_gone(nb, rows):
    """Override GONE, then tag the other listed unit by hand so the draft can publish."""
    ov = _open_editor(nb, rows)
    ov.confirm_button.click()
    assert GONE in ov.confirmed
    return ov


def test_start_prefills_the_rows_tags_highlights_the_differences_and_saves_nothing(
    tmp_path, statement_off
):
    nb, _, _, rows = _listed(tmp_path)
    grid, before = nb.channel_grid, nb.draft_answers
    ov = _open_editor(nb, rows)
    assert [ov.cells[(GONE, i)].value for i in range(3)] == list(TAGS)
    assert nb.draft_answers == before  # nothing saved
    text = " ".join(
        w.value for w in _walk(rows.box.children) if isinstance(getattr(w, "value", None), str)
    )
    assert "C0: header ‘P’" in text and "C1: header ‘Q’" in text and "C2: header" not in text
    assert "#D97706" in text and "as the row" in text  # orange box; C2 is as the row
    assert not grid.complete and GONE in rows.listed()
    assert not nb.save_experiment()
    # changing the draft redraws the highlights and still saves nothing
    ov.cells[(GONE, 2)].value = None
    assert nb.draft_answers == before and rows.override.drafts[GONE][2] is None
    assert "not tagged" in " ".join(
        w.value for w in _walk(rows.box.children) if isinstance(getattr(w, "value", None), str)
    )


def test_no_button_for_a_unit_without_a_reference_and_cancel_drops_the_draft(
    tmp_path, statement_off
):
    nb, _, _, rows = _listed(tmp_path)
    assert set(rows.override.buttons) == {GONE, KEPT}
    ov = _open_editor(nb, rows)
    ov.cancel_button.click()
    assert ov.editing is None and GONE not in ov.drafts and not ov.confirmed
    assert GONE in rows.listed()


def test_confirm_counts_the_unit_and_publishes_its_tags(tmp_path, statement_off):
    nb, _, out, rows = _listed(tmp_path)
    ov = _confirm_gone(nb, rows)
    assert GONE not in rows.listed() and KEPT in rows.listed()
    assert nb.draft_answers.units[GONE].channels == TAGS
    assert ov.editing is None
    ov2 = _open_editor(nb, rows, KEPT)
    ov2.confirm_button.click()
    assert not rows.listed()
    answer_all(nb)
    assert nb.save_experiment()
    published = description_from_bytes((out / DESCRIPTION_FILENAME).read_bytes())
    units = [
        u
        for repeat in published.repeats.values()
        for member in repeat.members.values()
        for u in member.technical_repeats.values()
    ]
    assert len(units) == len(UNITS) and all(u.channels == TAGS for u in units)


def _fast_names(key):
    if key[1] != "rep2":
        return None
    return ["P", "Q", "Ch3", "W"] if key == GONE else ["X", "Y", "Ch3", "W"]


def test_fast_mode_confirm_marks_the_unit_as_tagged_on_its_own(tmp_path):
    from test_notebook_tag_by_repeat import EXTRA

    root, masks, out = _experiment(tmp_path, extra=True, names=_fast_names)
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    grid = nb.channel_grid
    assert grid.same_channels.value
    for index, tag in enumerate(TAGS):
        grid.matrix.template_cells[(("ctrl", "rep1", "t1"), index)].value = tag
    rows = grid.by_repeat
    _set_row(nb, ("rep2", 4), EXTRA)
    assert list(rows.listed()) == [GONE]
    ov = _open_editor(nb, rows)
    assert [ov.cells[(GONE, i)].value for i in range(4)] == list(EXTRA)
    ov.confirm_button.click()
    assert not rows.listed()
    assert nb.draft_answers.units[GONE].channels == EXTRA
    assert GONE in grid.matrix.own
    answer_all(nb)
    assert nb.draft_answers.units[GONE].channels_from == "unit"
    assert nb.save_experiment()


@pytest.mark.parametrize("draft", [("DNA", None, "PHASE"), ("DNA", "DNA", "PHASE")])
def test_confirm_is_refused_on_an_incomplete_or_duplicate_draft(tmp_path, statement_off, draft):
    nb, _, _, rows = _listed(tmp_path)
    before = nb.draft_answers
    ov = _open_editor(nb, rows)
    for i, tag in enumerate(draft):
        ov.cells[(GONE, i)].value = tag
    ov.confirm_button.click()
    assert nb.draft_answers == before and not ov.confirmed
    assert GONE in rows.listed() and ov.editing == GONE
    assert "Tag every channel of the row" in rows.message


def test_a_failed_save_confirms_nothing(tmp_path, statement_off, monkeypatch):
    nb, _, _, rows = _listed(tmp_path)
    ov = _open_editor(nb, rows)
    monkeypatch.setattr(type(nb.channel_grid), "commit", lambda self, tags, cells: False)
    ov.confirm_button.click()
    assert not ov.confirmed and GONE in rows.listed()


def test_editing_after_the_confirmation_voids_it(tmp_path, statement_off):
    # the grid's own dropdown saves the new tag and the header name as its alias, so the unit is
    # then answered by that edit (not listed); the confirmation no longer covers its tags
    nb, _, _, rows = _listed(tmp_path)
    ov = _confirm_gone(nb, rows)
    grid = nb.channel_grid
    layout = next(u for u in grid.layouts if u.key == GONE)
    held = tags_of(layout, nb.draft_answers.units[GONE])
    assert override_holds(ov.confirmed[GONE], layout, held)
    grid.cells[(GONE, 0)].value = "EXTRA"
    edited = tags_of(layout, nb.draft_answers.units[GONE])
    assert edited[0] == "EXTRA"
    assert not override_holds(ov.confirmed[GONE], layout, edited)


def test_a_recheck_that_changes_nothing_keeps_the_unit_unlisted(tmp_path, statement_off):
    nb, _, _, rows = _listed(tmp_path)
    _confirm_gone(nb, rows)
    _recheck(nb)
    assert GONE not in rows.listed()
    assert nb.draft_answers.units[GONE].channels == TAGS


def test_a_changed_unit_header_voids_the_confirmation_but_keeps_the_saved_tags(
    tmp_path, statement_off
):
    nb, root, _, rows = _listed(tmp_path)
    _confirm_gone(nb, rows)
    _rewrite_header(root, GONE, ["P", "R", "Ch3"])  # a name nobody confirmed
    _recheck(nb)
    assert GONE in rows.listed()
    assert nb.draft_answers.units[GONE].channels == TAGS


def test_a_changed_reference_header_leaves_a_confirmed_unit_counted(tmp_path, statement_off):
    # saving the tags also saves the unit's header names as the labels' aliases, so its
    # names then say a label and no longer depend on the reference's header
    nb, root, _, rows = _listed(tmp_path)
    _confirm_gone(nb, rows)
    _rewrite_header(root, LEAD, ["Z", "Y", "Ch3"])
    _recheck(nb)
    assert GONE not in rows.listed()
    assert nb.draft_answers.units[GONE].channels == TAGS


def test_a_removed_unit_drops_its_confirmation(tmp_path, statement_off):
    import shutil

    nb, root, _, rows = _listed(tmp_path)
    rows.override.confirmed[GONE] = Override(
        TAGS, ("P", "Q", "Ch3")
    )  # as if a confirmation were held
    shutil.rmtree(root.joinpath(*GONE))
    _recheck(nb)
    assert GONE not in rows.override.confirmed


def test_a_row_save_that_fills_the_unit_drops_its_confirmation(tmp_path, statement_off):
    nb, root, _, rows = _listed(tmp_path)
    _confirm_gone(nb, rows)
    _rewrite_header(root, GONE, [None, None, None])
    _recheck(nb)
    assert GONE in rows.override.confirmed or GONE not in rows.refused
    _set_row(nb, ("rep2", 3), TAGS)
    assert GONE not in rows.override.confirmed


def test_the_confirmation_survives_a_mode_switch_and_a_reopen(tmp_path, statement_off):
    nb, _, _, rows = _listed(tmp_path)
    _confirm_gone(nb, rows)
    grid = nb.channel_grid
    for fast in (True, False):
        grid.same_channels.value = fast
        grid.open(grid.layouts, grid.labels, grid.rows.thumbs)
        assert GONE not in rows.listed(), fast
        assert nb.draft_answers.units[GONE].channels == TAGS, fast
