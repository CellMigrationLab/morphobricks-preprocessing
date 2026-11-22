"""Tag by repeat after a folder re-check (issue #372).

A re-check of the experiment folder drops the refusals of units it removed, compares the
surviving ones with the row's current reference, and never clears a contradiction that still
stands just because the screen reopened (in fast mode too).
"""

from __future__ import annotations

from channel_images import write_channels
from test_notebook_channels import set_labels
from test_notebook_check_save import opened
from test_notebook_tag_by_repeat import KINDS4, TAGS, UNITS, _experiment, _set_row, _tag


def _recheck(nb):
    """Check the experiment folder again in the same notebook, and confirm the axes again."""
    nb.check_button.click()
    nb.preview_checked.set(True)
    nb.show_preview()
    nb.confirm_button.click()


def _two_odd(key):
    """drug/rep1/t1 and drug/rep2/t1 name their channels (no label) differently from their rows."""
    if key[0] == "drug" and key[2] == "t1":
        return ["P", "Q", "Ch3"]
    return ["X", "Y", "Ch3"]


def _refused_twice(tmp_path):
    root, masks, out = _experiment(tmp_path, names=_two_odd)
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    _set_row(nb, ("rep1", 3), TAGS)
    _set_row(nb, ("rep2", 3), TAGS)
    rows = nb.channel_grid.by_repeat
    gone, kept = ("drug", "rep2", "t1"), ("drug", "rep1", "t1")
    assert set(rows.listed()) == {gone, kept}
    return nb, root, rows, gone, kept


def test_372_a_a_unit_removed_by_a_recheck_leaves_the_list_a_surviving_one_stays(
    tmp_path, statement_off
):
    import shutil

    nb, root, rows, gone, kept = _refused_twice(tmp_path)
    shutil.rmtree(root.joinpath(*gone))
    _recheck(nb)  # the same notebook: the experiment folder is checked again
    assert gone not in {u.key for u in nb.channel_grid.layouts}
    assert set(rows.listed()) == {kept}  # the removed unit is not listed, the other still is
    assert gone not in rows.refused and kept in rows.refused
    nb.channel_grid.refresh()
    assert "drug / rep2 / t1" not in rows.message and "drug / rep1 / t1" in rows.message


def _rewrite_header(root, key, names):
    write_channels(root.joinpath(*key, "a.tif"), names, KINDS4[:3], seed=UNITS.index(key) + 1)


def test_372_b_a_changed_reference_header_is_compared_with_the_current_reference(
    tmp_path, statement_off
):
    nb, root, rows, gone, kept = _refused_twice(tmp_path)
    lead = ("ctrl", "rep2", "t1")  # the reference of the rep2 row
    _rewrite_header(root, lead, ["Z", "Q", "Ch3"])  # another reference, still unlike
    _recheck(nb)
    assert set(rows.listed()) == {gone, kept}
    assert "is ‘Z’" in rows.listed()[gone] and "is ‘X’" not in rows.listed()[gone]
    _rewrite_header(root, lead, ["P", "Q", "Ch3"])  # the reference now names what the unit names
    _recheck(nb)
    assert set(rows.listed()) == {gone, kept}  # no contradiction left, but not tagged yet
    assert "incomplete" in rows.listed()[gone]
    _tag(nb, gone, TAGS)  # tagged as its reference: it fits the row, and leaves the list
    assert gone not in rows.listed()  # (its sibling's header now names the same labels)


def test_372_c_a_recheck_that_changes_nothing_keeps_every_unresolved_refusal(
    tmp_path, statement_off
):
    nb, _, rows, gone, kept = _refused_twice(tmp_path)
    _recheck(nb)
    assert set(rows.listed()) == {gone, kept}
    nb.channel_grid.open(
        nb.channel_grid.layouts, nb.channel_grid.labels, nb.channel_grid.rows.thumbs
    )  # the screen reopened: still unresolved, still listed
    assert set(rows.listed()) == {gone, kept}


def test_372_d_a_refusal_survives_a_switch_to_fast_mode_and_a_reopen(tmp_path, statement_off):
    nb, _, rows, gone, kept = _refused_twice(tmp_path)
    grid = nb.channel_grid
    grid.same_channels.value = True  # fast mode: the rows offer only units outside the layout
    grid.open(grid.layouts, grid.labels, grid.rows.thumbs)
    # the reference is the whole row's, not the lone offered unit: the contradiction stands
    assert set(rows.listed()) == {gone, kept}


def test_372_e_a_refusal_stays_listed_while_the_units_own_tags_are_incomplete(
    tmp_path, statement_off
):
    nb, root, rows, gone, kept = _refused_twice(tmp_path)
    _rewrite_header(root, ("ctrl", "rep2", "t1"), ["P", "Q", "Ch3"])  # no contradiction left
    _recheck(nb)
    grid = nb.channel_grid
    unit = nb.draft_answers.units.get(gone)
    assert unit is None or not all(unit.channels)  # its own tags are not confirmed
    assert gone in rows.listed() and gone in rows.refused  # listed while they are incomplete
    assert not grid.complete  # never counted as tagged
