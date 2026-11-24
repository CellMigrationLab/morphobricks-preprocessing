"""Tag by repeat: a strict block click confirms only refused units it names (review an).

External re-check of 23 November, blocker 2: on the strict page a click on a block of several
folders is recorded as the confirmation of each refused unit in it. In the shipped ``steps()``
UI a block of more than three folders lists them inside a closed accordion, so a refused unit
could be confirmed without being visible. Its name is now said beside the block's dropdowns.
The paths that are no action on a refused unit never record a confirmation: a fast-mode block of
several units, a fast-mode template edit, *Tag like the saved group* and a header pre-tag.

External re-check ao (24 November), blocker 2: in fast mode those paths never change a refused
unit's tags either. A blank layout template edited through its shared dropdown wrote the new
template to an unconfirmed refused follower whose header names another unit's confirmation had
saved as aliases: the unit was tagged with no action on it.
"""

from __future__ import annotations

import ipywidgets as widgets
import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from channel_images import write_channels  # noqa: E402
from morphobricks_preprocessing.notebook_channels_view import unit_name  # noqa: E402
from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import opened  # noqa: E402
from test_notebook_stepper import stepped  # noqa: E402
from test_notebook_tag_by_repeat import KINDS4, TAGS, UNITS, _experiment, _set_row  # noqa: E402
from test_notebook_tag_by_repeat_atomic import _draft  # noqa: E402
from test_notebook_tag_by_repeat_drafts import _review  # noqa: E402
from test_notebook_tag_by_repeat_oneclick import GONE, OTHER, _fast, _strict  # noqa: E402
from test_notebook_tag_by_repeat_recheck import _recheck  # noqa: E402


def _visible(widget):
    """Return the text on screen: a closed accordion's contents are not."""
    if isinstance(widget, widgets.Accordion):
        if widget.selected_index is None:
            return ""
    out = [widget.value] if isinstance(getattr(widget, "value", None), str) else []
    out += [_visible(child) for child in getattr(widget, "children", ())]
    return " ".join(out)


def _block_of(grid, key):
    """Return the block holding a unit's dropdowns, and the row of those dropdowns."""
    drop = grid.cells[(key, 0)]
    for block in grid.blocks.children:
        for child in getattr(block, "children", ()):
            if any(
                drop in getattr(cell, "children", ()) for cell in getattr(child, "children", ())
            ):
                return block, child
    raise AssertionError(f"no block holds {key}")


def _five_with_one_refused(tmp_path):
    """rep1's four units and GONE: one header (P, Q, Ch3), one tags; only GONE refused."""
    names = lambda k: ["P", "Q", "Ch3"] if k[1] == "rep1" else ["X", "Y", "Ch3"]  # noqa: E731
    root, masks, out = _experiment(tmp_path, names=names)
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    _set_row(nb, ("rep2", 3), TAGS)  # every rep2 unit tagged; X, Y saved as aliases
    write_channels(root.joinpath(*GONE, "a.tif"), ["P", "Q", "Ch3"], KINDS4[:3], seed=6)
    _recheck(nb)
    _set_row(nb, ("rep2", 3), TAGS)  # GONE's new header contradicts the row: refused
    _set_row(nb, ("rep1", 3), TAGS)  # rep1 (P, Q) tagged by its own row: not an action on GONE
    rows = nb.channel_grid.by_repeat
    assert set(rows.listed()) == {GONE} and nb.draft_answers.units[GONE].channels == TAGS
    assert GONE in _review(out).refused and not _review(out).confirmed
    return root, masks, out


def test_a_strict_block_of_five_names_its_refused_unit_beside_the_dropdowns(
    tmp_path, statement_off
):
    root, masks, out = _five_with_one_refused(tmp_path)
    nb = stepped(root, masks, out)  # the shipped notebook, on the saved draft
    grid, rows = nb.channel_grid, nb.channel_grid.by_repeat
    assert not grid.rows.pictures and not grid.same_channels.value
    assert GONE in rows.listed()
    block, answer = _block_of(grid, GONE)
    members = [k for k in UNITS if k[1] == "rep1"] + [GONE]
    assert all(grid.cells[(k, 0)] is grid.cells[(GONE, 0)] for k in members)  # one block
    assert any(
        isinstance(w, widgets.Accordion) and w.selected_index is None for w in block.children
    )
    other = unit_name(("ctrl", "rep1", "t1"))
    assert other not in _visible(block)  # the folder list is folded
    # at 40aca1d GONE's name was only inside the closed accordion
    beside = block.children[block.children.index(answer) + 1]
    assert unit_name(GONE) in _visible(beside) and "confirms" in beside.value
    assert other not in beside.value  # only the refused units are named
    grid.cells[(GONE, 2)].value = None  # the shared click, with GONE named beside it
    assert GONE in rows.listed()
    grid.cells[(GONE, 2)].value = "PHASE"
    assert GONE not in rows.listed() and GONE in _review(out).confirmed


def test_a_strict_block_without_a_refused_unit_names_nothing(tmp_path, statement_off):
    root, masks, out = _experiment(tmp_path, names=lambda k: ["P", "Q", "Ch3"])
    nb = stepped(root, masks, out)
    set_labels(nb, list(TAGS))
    block, answer = _block_of(nb.channel_grid, GONE)
    assert "confirms" not in _visible(block)


# Never a confirmation: the paths that are no action on a refused unit ---------------------------


def _layouts(grid, keys):
    return [u for u in grid.layouts if u.key in keys]


def _tags(nb, out, keys=(GONE, OTHER)):
    """Return the units' saved tags, in this session and on disk (None: no answer)."""
    disk = _draft(out).units
    own = nb.draft_answers.units
    return [tuple(getattr(a.get(k), "channels", None) or ()) for a in (own, disk) for k in keys]


def test_a_fast_mode_block_of_several_refused_units_records_no_confirmation(tmp_path):
    nb, root, rows = _fast(tmp_path)
    grid = nb.channel_grid
    units = _layouts(grid, (GONE, OTHER))
    assert rows.hand_tagged(units) is None
    before = _tags(nb, tmp_path / "out")
    grid._tagged(units, 3, "EXTRA")  # one change for both: not an action on each
    assert _tags(nb, tmp_path / "out") == before  # at 30af180 both took EXTRA at C3
    assert not rows.override.confirmed and set(rows.listed()) == {GONE, OTHER}
    assert not _review(tmp_path / "out").confirmed
    assert "confirms" not in _visible(grid.blocks)


def test_a_fast_mode_template_edit_records_no_confirmation(tmp_path):
    nb, root, rows = _fast(tmp_path)
    before = _tags(nb, tmp_path / "out")
    cell = nb.channel_grid.matrix.template_cells[(("ctrl", "rep1", "t1"), 2)]
    cell.value = None
    cell.value = "PHASE"
    assert _tags(nb, tmp_path / "out") == before
    assert not rows.override.confirmed and set(rows.listed()) == {GONE, OTHER}
    assert not _review(tmp_path / "out").confirmed


@pytest.mark.parametrize("fast", [True, False])
def test_tag_like_the_saved_group_records_no_confirmation(tmp_path, request, fast):
    if fast:
        nb, root, rows = _fast(tmp_path)
        tags = ("DNA", "ACTIN", "PHASE", "EXTRA")
    else:
        nb, root, rows = _strict(tmp_path, request.getfixturevalue("statement_off"))
        tags = TAGS
    grid = nb.channel_grid
    before = _tags(nb, tmp_path / "out")
    for units in ([GONE], [GONE, OTHER]):
        grid.tag_like(_layouts(grid, units), tags)
        assert not rows.override.confirmed and set(rows.listed()) == {GONE, OTHER}
        if fast:  # at 30af180 GONE, then OTHER, took the tags (a strict block names them)
            assert _tags(nb, tmp_path / "out") == before
    assert not _review(tmp_path / "out").confirmed


def test_a_header_pre_tag_records_no_confirmation(tmp_path, statement_off):
    named = lambda k: (  # noqa: E731
        ["ACTIN", "DNA", "PHASE"] if k == GONE else ["X", "Y", "Ch3"]
    )
    root, masks, out = _experiment(tmp_path, names=named)
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    _set_row(nb, ("rep2", 3), TAGS)  # GONE's header names the labels in another order
    grid, rows = nb.channel_grid, nb.channel_grid.by_repeat
    assert set(rows.listed()) == {GONE}
    assert grid._pending(_layouts(grid, [GONE])[0])  # pre-tagged from its header
    grid._confirm_layout(_layouts(grid, [GONE]))  # "Yes, these are right"
    assert nb.draft_answers.units[GONE].channels == ("ACTIN", "DNA", "PHASE")
    assert GONE in rows.listed() and not rows.override.confirmed
    assert not _review(out).confirmed


# Fast mode never tags a refused unit with no action on it (review ao, blocker 2) ---------------


def _blank_layout_with_b(tmp_path):
    """GONE (B) refused and untagged; OTHER (C) confirmed, so P and Q are aliases of its tags.

    The 4-channel units the row filled are on their own; **Add another layout** for them makes
    a layout of B alone, its template blank: B follows it.
    """
    nb, root, rows = _fast(tmp_path)
    rows.override.buttons[OTHER].click()
    rows.override.confirm_button.click()  # C: P, Q saved as aliases of DNA, ACTIN
    assert set(rows.listed()) == {GONE} and GONE in rows.standing()
    matrix = nb.channel_grid.matrix
    matrix.layout_buttons[4].click()
    groups, _ = matrix.groups()
    group = next(g for g in groups if GONE in [u.key for u in g])
    assert matrix.template(group) == (None,) * 4
    return nb, root, rows, group


def test_a_blank_fast_template_edit_never_tags_a_refused_unit(tmp_path):
    nb, root, rows, group = _blank_layout_with_b(tmp_path)
    out = tmp_path / "out"
    before = _tags(nb, out, (GONE,))
    assert before == [(), ()]  # untagged, in this session and on disk
    cell = nb.channel_grid.matrix.template_cells[(group[0].key, 0)]
    cell.value = "DNA"  # the layout's shared dropdown: no action on GONE itself
    assert _tags(nb, out, (GONE,)) == before  # at 30af180: GONE saved with DNA at C0
    assert GONE in rows.listed() and GONE in rows.standing()
    assert GONE in _draft(out).tag_review.refused and GONE not in _draft(out).tag_review.confirmed
    again = opened(root, tmp_path / "masks", out)  # a restart on the saved draft
    assert _tags(again, out, (GONE,)) == before
    assert GONE in again.channel_grid.by_repeat.listed()
    assert GONE in again.channel_grid.by_repeat.standing()


def test_a_hand_edit_of_the_refused_unit_alone_still_confirms_it_in_fast_mode(tmp_path):
    nb, root, rows, group = _blank_layout_with_b(tmp_path)
    nb.channel_grid.matrix.template_cells[(group[0].key, 0)].value = "DNA"  # nothing on GONE
    grid = nb.channel_grid
    grid.matrix.tag_on_own([GONE])  # its own dropdowns: a deliberate hand-tag of GONE alone
    for index, tag in enumerate(("DNA", "ACTIN", "PHASE", "EXTRA")):
        grid.cells[(GONE, index)].value = tag
    assert GONE not in rows.listed() and GONE not in rows.standing()
    assert GONE in _draft(tmp_path / "out").tag_review.confirmed


def test_a_visibly_scoped_strict_block_edit_still_confirms_the_refused_unit(
    tmp_path, statement_off
):
    nb, root, rows = _strict(tmp_path, statement_off)
    rows.override.buttons[OTHER].click()
    rows.override.confirm_button.click()  # OTHER: P, Q saved as aliases
    grid = nb.channel_grid
    block, answer = _block_of(grid, GONE)
    beside = block.children[block.children.index(answer) + 1]
    assert unit_name(GONE) in _visible(beside)  # the click names GONE
    for index, tag in enumerate(TAGS):
        grid.cells[(GONE, index)].value = tag
    assert GONE not in rows.listed()
    assert GONE in _draft(tmp_path / "out").tag_review.confirmed
