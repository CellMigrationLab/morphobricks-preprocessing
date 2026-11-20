"""*Tag this biological repeat (all conditions)* (owner request of 20 November 2026).

One row of tags per biological repeat and channel count; setting it saves the tags for every
condition's units of that repeat (technical repeats included), each unit keeping its own list.
Headers are checked per unit: a contradiction is listed and that unit is not filled. In fast
mode (Spec ADR 0006) the units it fills are tagged on their own, the owner's way to describe
repeats with an extra channel.
"""

from __future__ import annotations

import pytest

from channel_images import write_channels, write_masks
from morphobricks_preprocessing.channel_labels import UnitLayout
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.draft_answers import DraftAnswers
from morphobricks_preprocessing.repeat_tags import repeat_fill, repeat_groups, repeat_shown

UNITS = [(c, b, t) for c in ("ctrl", "drug") for b in ("rep1", "rep2") for t in ("t1", "t2")]
TAGS = ("DNA", "ACTIN", "PHASE")
EXTRA = (*TAGS, "EXTRA")
KINDS4 = ["blobs", "filaments", "cells", "blobs"]


def _layout(key, count=3, names=None):
    return UnitLayout(key, "/".join(key) + "/a.tif", count, tuple(names or (None,) * count))


# --- the rows (pure) --------------------------------------------------------------------------


def test_c_a_row_holds_every_condition_and_technical_repeat_of_one_repeat_and_count():
    layouts = [_layout(k, 4 if k[1] == "rep2" and k[2] == "t2" else 3) for k in UNITS]
    groups = repeat_groups(layouts)
    assert list(groups) == [("rep1", 3), ("rep2", 3), ("rep2", 4)]
    assert [u.key for u in groups[("rep1", 3)]] == [k for k in sorted(UNITS) if k[1] == "rep1"]
    assert {u.key for u in groups[("rep2", 4)]} == {("ctrl", "rep2", "t2"), ("drug", "rep2", "t2")}


def test_a_unit_whose_header_contradicts_the_row_is_listed_not_filled():
    group = (
        _layout(("ctrl", "rep2", "t1"), names=("Hoechst", "Actin", "Ch3")),
        _layout(("drug", "rep2", "t1"), names=("Actin", "Hoechst", "Ch3")),
    )
    save, cells, refused = repeat_fill(DraftAnswers("absent", "0" * 40), group, TAGS, TAGS)
    assert list(save) == [("ctrl", "rep2", "t1")] and len(cells) == 3
    assert [d.index for d in refused[("drug", "rep2", "t1")]] == [0, 1]


def test_d_a_row_shows_saved_tags_only_when_every_unit_holds_them():
    from dataclasses import replace

    from morphobricks_preprocessing.draft_answers import UnitAnswer

    group = tuple(_layout(k) for k in UNITS if k[1] == "rep2")
    answers = DraftAnswers("absent", "0" * 40)
    assert repeat_shown(answers, group) is None
    units = {u.key: UnitAnswer(TAGS, "channels") for u in group}
    assert repeat_shown(replace(answers, units=units), group) == TAGS
    units[group[0].key] = UnitAnswer(("ACTIN", "DNA", "PHASE"), "channels")
    assert repeat_shown(replace(answers, units=units), group) is None


# --- the notebook -----------------------------------------------------------------------------

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import answer_all, opened  # noqa: E402


def _experiment(tmp_path, extra=False, names=None):
    """Write the 2 x 2 x 2 design; with ``extra`` rep2 has a fourth channel in every condition."""
    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, key in enumerate(UNITS):
        four = extra and key[1] == "rep2"
        kinds = KINDS4 if four else KINDS4[:3]
        named = None if names is None else names(key)
        write_channels(root.joinpath(*key, "a.tif"), named, kinds, seed=i + 1)
        write_masks(masks, {"/".join(key) + "/a.tif": i + 1}, {"Nuclei": "labels"})
    return root, masks, tmp_path / "out"


def _set_row(nb, key, tags):
    rows = nb.channel_grid.by_repeat
    for drop, tag in zip(rows.drops[key], tags, strict=True):
        drop.value = tag
    rows.buttons[key].click()


def test_b_strict_mode_one_row_fills_every_condition_of_the_repeat(tmp_path, statement_off):
    root, masks, out = _experiment(tmp_path)
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    rows = nb.channel_grid.by_repeat
    assert set(rows.buttons) == {("rep1", 3), ("rep2", 3)}  # every repeat, unticked
    _set_row(nb, ("rep2", 3), TAGS)
    units = nb.draft_answers.units
    assert {k for k, u in units.items() if u.channels == TAGS} == {
        k for k in UNITS if k[1] == "rep2"
    }
    assert not any(units.get(k) for k in UNITS if k[1] == "rep1")  # another repeat: untouched
    assert all(u.channels_from is None for u in units.values())  # strict: no marker


def test_a_owner_case_one_repeat_with_an_extra_channel_one_row_one_action_publishes(tmp_path):
    root, masks, out = _experiment(tmp_path, extra=True)
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    grid = nb.channel_grid
    assert grid.same_channels.value  # fast mode
    for index, tag in enumerate(TAGS):  # the layout's row: rep1, 3 channels
        grid.matrix.template_cells[(("ctrl", "rep1", "t1"), index)].value = tag
    assert set(grid.by_repeat.buttons) == {("rep2", 4)}  # only the repeat outside the layout
    _set_row(nb, ("rep2", 4), EXTRA)  # one row, one action, for ctrl and drug
    answer_all(nb)
    units = nb.draft_answers.units
    rep2 = [k for k in UNITS if k[1] == "rep2"]
    assert all(units[k].channels == EXTRA and units[k].channels_from == "unit" for k in rep2)
    assert all(units[k].channels_from == "experiment" for k in UNITS if k[1] == "rep1")
    assert nb.save_experiment()
    published = description_from_bytes((out / DESCRIPTION_FILENAME).read_bytes())
    assert published.channel_layout.channels == TAGS
    reopened = opened(root, masks, out)  # (d) the row shows what every rep2 unit holds
    rows = reopened.channel_grid.by_repeat
    assert [d.value for d in rows.drops[("rep2", 4)]] == list(EXTRA)
    assert "every unit already holds these tags" in repr(rows.box.children)


def test_a_fast_mode_a_contradicting_unit_is_listed_and_never_filled(tmp_path):
    def names(key):
        if key == ("drug", "rep2", "t1"):
            return ["Actin", "Hoechst", "Ch3", "X"]
        return ["Hoechst", "Actin", "Ch3", "X"] if key[1] == "rep2" else None

    root, masks, out = _experiment(tmp_path, extra=True, names=names)
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    _set_row(nb, ("rep2", 4), EXTRA)
    units = nb.draft_answers.units
    assert units.get(("drug", "rep2", "t1")) is None or not any(
        units[("drug", "rep2", "t1")].channels
    )
    assert units[("ctrl", "rep2", "t1")].channels == EXTRA
    shown = nb.channel_grid.by_repeat.message
    assert "drug / rep2 / t1" in shown and "not filled" in shown


def test_a_row_with_an_untagged_channel_saves_nothing(tmp_path, statement_off):
    root, masks, out = _experiment(tmp_path)
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    _set_row(nb, ("rep1", 3), ("DNA", None, "PHASE"))
    assert not nb.draft_answers.units
    assert "Tag every channel of the row" in nb.channel_grid.by_repeat.message
