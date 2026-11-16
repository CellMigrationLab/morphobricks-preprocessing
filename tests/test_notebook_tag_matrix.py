"""*Tag channels* fast mode: "Every imaging unit has the same channels in the same order".

Owner decisions of 16 November 2026. On by default: headers naming every channel with a label
(exactly, or a saved alias) are tagged with no click; the row of tags is tagged once and fills
every unit without header names; any cell of any unit can then be tuned (that unit is
*edited*). The draft holds what tagging every unit by hand holds, byte for byte. A contradiction
(another channel count, header names in another order or other names) is refused, listed, and
tagged on its own; colour (RGB) units are never mixed in. Unticked, the page is the blocks of
PR 17b (the block tests run with the ``statement_off`` fixture).
"""

import shutil

import pytest

from channel_images import write_channels, write_masks
from morphobricks_preprocessing.channel_labels import UnitLayout
from morphobricks_preprocessing.channel_matrix import (
    EDITED,
    HEADER,
    NEW,
    REFUSED,
    SAME,
    layout_groups,
    matrix_rows,
)
from morphobricks_preprocessing.description_file import DRAFT_FILENAME
from test_notebook_channels import KINDS, open_screen, set_labels
from test_notebook_check_save import answer_all

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from test_notebook_row_fold import stepper  # noqa: E402

TAGS = ("DNA", "ACTIN", "PHASE")
UNITS = [(c, b, t) for c in ("ctrl", "drug") for b in ("rep1", "rep2") for t in ("t1", "t2")]
LEAD = UNITS[0]
NAMES = ["Hoechst", "Actin", "Ch3"]


def _design(tmp_path, names_of=lambda key: NAMES, channels_of=lambda key: 3):
    """Write a 2 x 2 x 2 design: condition / biological repeat / technical repeat."""
    root = tmp_path / "exp"
    for i, key in enumerate(UNITS):
        count = channels_of(key)
        names = names_of(key)
        names = None if names is None else names[:count]
        write_channels(root.joinpath(*key, "a.tif"), names, KINDS[:count], seed=i + 1)
    return root


def _screen(tmp_path, root, out="out", labels=TAGS):
    nb = open_screen(root, tmp_path / out)
    set_labels(nb, list(labels))
    return nb


def _tag_once(nb, tags=TAGS):
    """Tag the layout's row of tags (the template), one dropdown per C index."""
    cells = nb.channel_grid.matrix.template_cells
    for index, tag in enumerate(tags):
        cells[(LEAD, index)].value = tag


def _types(nb):
    for layout in nb.channel_grid.layouts:
        nb.channel_types.radios[layout.key].value = "channels"


def _states(nb):
    return {key: row.state for key, row in nb.channel_grid.matrix.rows.items()}


def test_the_statement_is_on_by_default_and_one_tagging_fills_the_2x2x2_matrix(tmp_path):
    nb = _screen(tmp_path, _design(tmp_path, names_of=lambda key: None))
    grid = nb.channel_grid
    assert grid.same_channels.value is True
    assert "Every imaging unit has the same channels in the same order" in (
        grid.same_channels.description
    )
    groups, apart = grid.matrix.groups()
    assert [[u.key for u in g] for g in groups] == [UNITS] and apart == []
    assert not nb.draft_answers.units  # nothing saved before the user's tags
    _tag_once(nb)
    assert {k: u.channels for k, u in nb.draft_answers.units.items()} == dict.fromkeys(UNITS, TAGS)
    assert set(_states(nb).values()) == {SAME}
    text = repr(grid.blocks.children)
    assert "same as the first tagged unit" in text and "ctrl / rep1" in text
    assert not grid.complete  # the image types are still to answer
    _types(nb)
    assert grid.complete


def test_editing_one_cell_marks_only_that_unit_edited(tmp_path):
    nb = _screen(tmp_path, _design(tmp_path), labels=(*TAGS, "UNUSED"))
    grid = nb.channel_grid
    _tag_once(nb)
    tuned = ("drug", "rep2", "t1")
    grid.cells[(tuned, 2)].value = "UNUSED"
    units = nb.draft_answers.units
    assert units[tuned].channels == ("DNA", "ACTIN", "UNUSED")
    assert all(units[k].channels == TAGS for k in UNITS if k != tuned)
    states = _states(nb)
    assert states[tuned] == EDITED and [k for k, s in states.items() if s == EDITED] == [tuned]
    assert "<b>edited</b>" in repr(grid.blocks.children)
    assert repr(grid.blocks.children).count("same as the first tagged unit") == len(UNITS) - 1


def test_an_edited_unit_keeps_its_tags_when_the_row_of_tags_changes(tmp_path):
    nb = _screen(tmp_path, _design(tmp_path), labels=(*TAGS, "UNUSED"))
    grid = nb.channel_grid
    _tag_once(nb)
    tuned = ("ctrl", "rep2", "t2")
    grid.cells[(tuned, 2)].value = "UNUSED"
    grid.matrix.template_cells[(LEAD, 0)].value = "ACTIN"  # twice in the row: refused
    assert "one label cannot stand at two positions" in grid.problem.value
    assert nb.draft_answers.units[LEAD].channels == TAGS
    grid.matrix.template_cells[(LEAD, 0)].value = None
    units = nb.draft_answers.units
    assert units[tuned].channels == ("DNA", "ACTIN", "UNUSED")  # edited: kept
    assert all(units[k].channels == (None, "ACTIN", "PHASE") for k in UNITS if k != tuned)
    grid.cells[(tuned, 2)].value = "PHASE"
    grid.cells[(tuned, 0)].value = None  # tuned back to the row: the same again, it follows
    assert _states(nb)[tuned] == SAME
    grid.matrix.template_cells[(LEAD, 0)].value = "DNA"
    assert all(nb.draft_answers.units[k].channels == TAGS for k in UNITS)


def test_a_unit_with_another_channel_count_is_tagged_on_its_own(tmp_path):
    odd = ("drug", "rep2", "t2")
    root = _design(tmp_path, channels_of=lambda key: 2 if key == odd else 3)
    nb = _screen(tmp_path, root)
    grid = nb.channel_grid
    groups, apart = grid.matrix.groups()
    assert odd not in {u.key for u in groups[0]} and [u.key for u in apart] == [odd]
    assert "different channel count (2, not 3): tag on its own" in repr(grid.blocks.children)
    assert grid.matrix.layout_buttons == {}  # one unit: no other layout to add
    _tag_once(nb)
    _types(nb)
    assert odd not in nb.draft_answers.units or not any(nb.draft_answers.units[odd].channels)
    assert not grid.complete and "2 channel(s) still to tag" in grid.status.value
    stopped = grid.matrix.stopped.value
    assert "Stopped: 1 imaging unit(s)" in stopped and "drug / rep2 / t2" in stopped
    grid.cells[(odd, 0)].value = "DNA"
    grid.cells[(odd, 1)].value = "PHASE"
    assert nb.draft_answers.units[odd].channels == ("DNA", "PHASE")
    assert grid.complete and grid.matrix.stopped.value == ""


def test_add_another_layout_fills_the_units_of_another_count_from_their_own_row(tmp_path):
    odd = {("drug", "rep2", "t1"), ("drug", "rep2", "t2")}
    root = _design(tmp_path, channels_of=lambda key: 2 if key in odd else 3)
    nb = _screen(tmp_path, root)
    grid = nb.channel_grid
    grid.matrix.layout_buttons[2].click()
    groups, apart = grid.matrix.groups()
    assert [{u.key for u in g} for g in groups][1] == odd and apart == []
    _tag_once(nb)
    cells = grid.matrix.template_cells
    cells[(("drug", "rep2", "t1"), 0)].value = "DNA"
    cells[(("drug", "rep2", "t1"), 1)].value = "ACTIN"
    assert all(nb.draft_answers.units[k].channels == ("DNA", "ACTIN") for k in odd)
    assert nb.draft_answers.units[LEAD].channels == TAGS


@pytest.mark.parametrize(
    ("swapped", "differences"),
    [
        (["Actin", "Hoechst", "Ch3"], 2),  # the first unit's names in another order
        (["PHASE", "Actin", "Ch3"], 1),  # a name equal to another label (proposal_for)
        (["Hoechst", "Phalloidin", "Ch3"], 1),  # another name
    ],
)
def test_a_header_that_contradicts_the_row_is_refused_never_prefilled(
    tmp_path, swapped, differences
):
    odd = ("ctrl", "rep2", "t1")
    nb = _screen(tmp_path, _design(tmp_path, names_of=lambda key: swapped if key == odd else NAMES))
    grid = nb.channel_grid
    _tag_once(nb)
    _types(nb)
    row = grid.matrix.rows[odd]
    assert row.state == REFUSED and len(row.differences) == differences
    assert odd not in nb.draft_answers.units or not any(nb.draft_answers.units[odd].channels)
    assert (odd, 0) not in grid.cells  # no dropdown, nothing shown as if tagged
    stopped = grid.matrix.stopped.value
    assert "Stopped: 1 imaging unit(s) contradict" in stopped and "ctrl / rep2 / t1" in stopped
    assert f"C{row.differences[0].index}: header ‘" in stopped
    assert "tag those units on their own" in stopped
    assert not grid.complete
    grid.matrix.own_buttons[odd].click()  # the fix: tag it on its own
    _, apart = grid.matrix.groups()
    assert [u.key for u in apart] == [odd] and grid.matrix.stopped.value == ""  # fix chosen
    assert not grid.complete  # still to tag
    assert "switched to tagging on its own" in repr(grid.blocks.children)
    for index, tag in enumerate(("ACTIN", "DNA", "PHASE")):
        grid.cells[(odd, index)].value = tag  # nothing reordered for the user: their tags
    assert nb.draft_answers.units[odd].channels == ("ACTIN", "DNA", "PHASE")
    assert grid.complete and grid.matrix.stopped.value == ""


def test_every_refused_unit_is_listed_and_switched_at_once(tmp_path):
    odd = {("ctrl", "rep2", "t1"), ("drug", "rep1", "t2")}
    swapped = ["Actin", "Hoechst", "Ch3"]
    nb = _screen(tmp_path, _design(tmp_path, names_of=lambda k: swapped if k in odd else NAMES))
    _tag_once(nb)
    grid = nb.channel_grid
    assert set(grid.matrix.own_buttons) == odd and "Stopped: 2" in grid.matrix.stopped.value
    grid.matrix.all_own[0].click()
    assert {u.key for u in grid.matrix.groups()[1]} == odd
    assert not any(nb.draft_answers.units.get(k) for k in odd)  # switched, nothing saved


LABELLED = ["DNA", "ACTIN", "PHASE"]


def test_headers_naming_every_channel_are_trusted_with_no_click(tmp_path):
    other = ("drug", "rep1", "t1")
    order = lambda key: ["ACTIN", "DNA", "PHASE"] if key == other else LABELLED  # noqa: E731
    nb = _screen(tmp_path, _design(tmp_path, names_of=order))
    units = nb.draft_answers.units
    assert {k: u.channels for k, u in units.items()} == {
        k: tuple(order(k)) for k in UNITS
    }  # tagged on opening: no click
    states = _states(nb)
    assert states[other] == HEADER and {s for k, s in states.items() if k != other} == {SAME}
    assert nb.channel_grid.matrix.stopped.value == ""
    _types(nb)
    assert nb.channel_grid.complete


def test_trusted_headers_write_the_draft_bytes_of_tagging_by_hand(tmp_path):
    root = _design(tmp_path, names_of=lambda key: LABELLED)
    fast = _screen(tmp_path, root, "fast")
    _types(fast)
    hand = _screen(tmp_path, root, "hand")
    _by_hand(hand)
    _types(hand)
    assert fast.draft_answers == hand.draft_answers
    one = (tmp_path / "fast" / DRAFT_FILENAME).read_bytes()
    assert one == (tmp_path / "hand" / DRAFT_FILENAME).read_bytes()


def test_unticked_headers_are_only_a_proposal_as_before(tmp_path, monkeypatch):
    from morphobricks_preprocessing.notebook_channels_grid import TagGrid

    monkeypatch.setattr(TagGrid, "SAME_CHANNELS", False)
    nb = _screen(tmp_path, _design(tmp_path, names_of=lambda key: LABELLED))
    assert not nb.draft_answers.units  # the strict path: a pre-tag needs its confirmation
    assert len(nb.channel_grid.confirm_buttons) == 1


def _by_hand(nb, tags=TAGS):
    """Tag every unit by its own dropdowns, statement unticked (the PR 17b page)."""
    grid = nb.channel_grid
    grid.same_channels.value = False
    for key in UNITS:
        if any(b[0].key == key and len(b) > 1 for b in grid.block_list()):
            grid.split(key)
    for key in UNITS:
        for index, tag in enumerate(tags):
            grid.cells[(key, index)].value = tag


@pytest.mark.parametrize("names", [None, NAMES], ids=["no-header-names", "header-names"])
def test_the_filled_matrix_writes_the_draft_bytes_of_tagging_every_unit_by_hand(tmp_path, names):
    root = _design(tmp_path, names_of=lambda key: names)
    matrix = _screen(tmp_path, root, "matrix")
    _tag_once(matrix)
    _types(matrix)
    hand = _screen(tmp_path, root, "hand")
    _by_hand(hand)
    _types(hand)
    assert matrix.draft_answers == hand.draft_answers
    one = (tmp_path / "matrix" / DRAFT_FILENAME).read_bytes()
    assert one == (tmp_path / "hand" / DRAFT_FILENAME).read_bytes()
    assert one.count(b'"PHASE"') >= len(UNITS)


def test_unticked_the_page_is_the_blocks_of_identical_headers(tmp_path):
    nb = _screen(tmp_path, _design(tmp_path, names_of=lambda key: None))
    grid = nb.channel_grid
    grid.same_channels.value = False
    assert grid.matrix.template_cells == {} and grid.matrix.rows == {}
    assert [len(b) for b in grid.block_list()] == [1] * len(UNITS)  # no name: one by one
    assert "tagged folder by folder" in repr(grid.blocks.children)
    grid.cells[(LEAD, 0)].value = "DNA"
    assert set(nb.draft_answers.units) == {LEAD}  # nothing filled
    grid.same_channels.value = True  # ticked again: the row of tags is the first unit's
    assert _states(nb)[LEAD] == SAME and _states(nb)[UNITS[1]] == NEW  # shown, not saved


def test_colour_units_are_never_filled_from_separate_channels_nor_the_reverse():
    def unit(key, rgb):
        return UnitLayout(key, "a.tif", 3, (None,) * 3, rgb)

    units = [unit(UNITS[0], True), unit(UNITS[1], False), unit(UNITS[2], False)]
    groups, apart = layout_groups(units, colour=set())
    assert [[u.key for u in g] for g in groups] == [UNITS[1:3]] and apart == []
    groups, _ = layout_groups(units, colour={UNITS[1]})  # answered colour: out as well
    assert [[u.key for u in g] for g in groups] == [UNITS[2:3]]


def test_matrix_rows_states_are_against_the_row_of_tags():
    from morphobricks_preprocessing.draft_answers import DraftAnswers, UnitAnswer

    layouts = [UnitLayout(k, "a.tif", 3, ("A", "B", "C")) for k in UNITS[:3]]
    answers = DraftAnswers(
        "absent",
        "0" * 40,
        units={UNITS[0]: UnitAnswer(TAGS), UNITS[1]: UnitAnswer(("DNA", None, None))},
    )
    rows = matrix_rows(layouts, answers, TAGS, TAGS)
    assert [r.state for r in rows] == [SAME, EDITED, NEW]
    assert rows[2].shown == TAGS


# --- reopen ----------------------------------------------------------------------------------

SAVED = ["ctrl/rep1", "ctrl/rep2", "drug/rep1"]
NEW_KEY = ("drug", "rep2", "single")


def _add(root, masks, folder, seed):
    write_channels(root / folder / "a.tif", ["DAPI", "Actin", "Ch3"], KINDS, seed=seed)
    write_masks(masks, {f"{folder}/a.tif": seed}, {"Nuclei": "labels"})


def test_a_reopen_trusts_saved_aliases_and_fills_a_nameless_unit_only_on_the_click(tmp_path):
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(SAVED):
        _add(root, masks, folder, i + 1)
    first = stepper(root, masks, out)
    answer_all(first)
    assert first.save_experiment()
    assert set(_states(first).values()) == {SAME}
    shutil.copytree(out, tmp_path / "copy")
    write_channels(root / "drug/rep2/a.tif", None, KINDS, seed=9)  # no header names
    write_masks(masks, {"drug/rep2/a.tif": 9}, {"Nuclei": "labels"})
    _add(root, masks, "drug/rep3", 10)  # the saved aliases DAPI, Actin, Ch3

    clicked = stepper(root, masks, out)
    grid = clicked.channel_grid
    named = ("drug", "rep3", "single")
    assert clicked.draft_answers.units[named].channels == TAGS  # trusted: no click
    assert _states(clicked)[NEW_KEY] == NEW
    unit = clicked.draft_answers.units.get(NEW_KEY)
    assert unit is None or not any(unit.channels)  # nothing saved before the click
    assert grid.cells[(NEW_KEY, 0)].value is None
    assert len(grid.matrix.fill_buttons) == 1
    grid.matrix.fill_buttons[0].click()
    assert clicked.draft_answers.units[NEW_KEY].channels == TAGS
    assert grid.matrix.fill_buttons == [] and _states(clicked)[NEW_KEY] == SAME

    by_hand = stepper(root, masks, tmp_path / "copy")
    by_hand.channel_grid.same_channels.value = False
    for key in (NEW_KEY, named):
        for index, tag in enumerate(TAGS):
            by_hand.channel_grid.cells[(key, index)].value = tag
    assert (out / DRAFT_FILENAME).read_bytes() == (tmp_path / "copy" / DRAFT_FILENAME).read_bytes()
