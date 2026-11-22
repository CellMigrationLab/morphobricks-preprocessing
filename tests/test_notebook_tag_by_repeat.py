"""*Tag this repeat folder (all conditions)* (owner request of 20 November 2026).

One row of tags per repeat folder name and channel count; setting it saves the tags for every
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
from morphobricks_preprocessing.draft_answers import DraftAnswers, UnitAnswer
from morphobricks_preprocessing.repeat_tags import (
    pairing_collision,
    repeat_apart,
    repeat_fill,
    repeat_groups,
    repeat_lead,
    repeat_shown,
)

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


def test_4_one_folder_name_paired_into_different_repeats_is_a_collision():
    from dataclasses import replace

    group = tuple(_layout(k) for k in UNITS if k[1] == "rep2")
    answers = DraftAnswers("absent", "0" * 40)
    assert pairing_collision(answers, group) is None
    paired = replace(answers, pairing={("drug", "rep2"): "repX"})
    assert pairing_collision(paired, group) == {"ctrl": "rep2", "drug": "repX"}


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


def _contradicting(key):
    """drug/rep2/t1's header names its channels in another order than every other unit."""
    return (
        ["Actin", "Hoechst", "Ch3"]
        if key == ("drug", "rep2", "t1")
        else ["Hoechst", "Actin", "Ch3"]
    )


def test_1_5_the_list_is_the_current_state_and_publishing_waits_for_it(tmp_path, statement_off):
    root, masks, out = _experiment(tmp_path, names=_contradicting)
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    rows = nb.channel_grid.by_repeat
    _set_row(nb, ("rep2", 3), TAGS)
    odd = ("drug", "rep2", "t1")
    assert list(rows.listed()) == [odd] and "not filled" in rows.message
    nb.channel_grid.refresh()  # shown again on every redraw while it holds
    assert "drug / rep2 / t1" in rows.message
    for index, tag in enumerate(("ACTIN", "DNA", "PHASE")):  # the user tags it by hand
        nb.channel_grid.cells[(odd, index)].value = tag
    assert not rows.listed()
    nb.channel_grid.refresh()
    assert "not filled" not in rows.message  # no stale contradiction
    answer_all(nb)
    assert nb.save_experiment()


def test_1_5_publishing_is_refused_while_a_unit_is_listed(tmp_path, statement_off):
    root, masks, out = _experiment(tmp_path, names=_contradicting)
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    odd = ("drug", "rep2", "t1")
    for index, tag in enumerate(("EXTRA", "DNA", "PHASE")):  # tagged by hand against its header
        nb.channel_grid.cells[(odd, index)].value = tag
    answer_all(nb)
    rows = nb.channel_grid.by_repeat
    _set_row(nb, ("rep2", 3), TAGS)  # the row contradicts the unit's header: listed
    assert list(rows.listed()) == [odd]
    nb._refresh_check_save()
    assert nb.save_button.disabled and not nb.save_experiment()
    assert "row did not fill: see Channels" in nb.save_blocker.value
    assert not (out / DESCRIPTION_FILENAME).exists()


def test_2_a_refused_save_moves_no_unit_under_tagged_on_their_own(tmp_path, monkeypatch):
    root, masks, out = _experiment(tmp_path, extra=True)
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    grid = nb.channel_grid
    monkeypatch.setattr(type(grid), "commit", lambda self, tags, cells: False)
    _set_row(nb, ("rep2", 4), EXTRA)
    assert not grid.matrix.own  # nothing saved, nothing switched


def test_3_fast_mode_a_row_offers_only_the_units_outside_the_layout(tmp_path):
    def channels(key):
        return 4 if key in (("ctrl", "rep2", "t2"), ("drug", "rep2", "t2")) else 3

    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, key in enumerate(UNITS):
        kinds = KINDS4[: channels(key)]
        write_channels(root.joinpath(*key, "a.tif"), None, kinds, seed=i + 1)
        write_masks(masks, {"/".join(key) + "/a.tif": i + 1}, {"Nuclei": "labels"})
    nb = opened(root, masks, tmp_path / "out")
    set_labels(nb, list(EXTRA))
    grid = nb.channel_grid
    for index, tag in enumerate(TAGS):
        grid.matrix.template_cells[(("ctrl", "rep1", "t1"), index)].value = tag
    rows = grid.by_repeat
    assert set(rows.buttons) == {("rep2", 4)}  # (rep2, 3) fits the layout: no row
    _set_row(nb, ("rep2", 4), EXTRA)
    units = nb.draft_answers.units
    assert {k for k, u in units.items() if u.channels == EXTRA} == {
        ("ctrl", "rep2", "t2"),
        ("drug", "rep2", "t2"),
    }
    assert units[("ctrl", "rep2", "t1")].channels == TAGS  # fits the layout: untouched


def test_4_a_folder_name_paired_into_different_repeats_flags_its_row_and_fills_nothing(
    tmp_path, statement_off
):
    from dataclasses import replace

    root, masks, out = _experiment(tmp_path)
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    assert nb._answer(replace(nb.draft_answers, pairing={("drug", "rep2"): "repX"}))
    nb.channel_grid.refresh()
    rows = nb.channel_grid.by_repeat
    assert rows.buttons[("rep2", 3)].disabled
    assert "ctrl → rep2, drug → repX" in repr(rows.box.children)
    for drop, tag in zip(rows.drops[("rep2", 3)], TAGS, strict=True):
        drop.value = tag
    assert not rows.apply(
        ("rep2", 3), tuple(u for u in nb.channel_grid.layouts if u.key[1] == "rep2")
    )
    assert not any(u.channels for u in nb.draft_answers.units.values())


def _listed_unit(tmp_path):
    """Open the contradicting design and click rep2's row: drug/rep2/t1 is listed."""
    root, masks, out = _experiment(tmp_path, names=_contradicting)
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))  # one more label, to change a tag without a duplicate
    _set_row(nb, ("rep2", 3), TAGS)
    odd = ("drug", "rep2", "t1")
    assert list(nb.channel_grid.by_repeat.listed()) == [odd]
    return nb, odd


def _tag(nb, unit, tags):
    for index, tag in enumerate(tags):
        nb.channel_grid.cells[(unit, index)].value = tag


def test_5_a_listed_unit_leaves_the_list_when_its_tags_are_changed_and_restored(
    tmp_path, statement_off
):
    nb, odd = _listed_unit(tmp_path)
    rows = nb.channel_grid.by_repeat
    _tag(nb, odd, ("ACTIN", "DNA", "PHASE"))  # the header's own order: answered
    assert not rows.listed()
    _tag(nb, odd, ("EXTRA", "DNA", "PHASE"))  # changed to what the header contradicts: listed
    assert list(rows.listed()) == [odd]
    _tag(nb, odd, ("ACTIN", "DNA", "PHASE"))  # restored: the current answer fits the header
    assert not rows.listed()
    nb.channel_grid.refresh()
    assert "not filled" not in rows.message


def test_5_a_change_of_the_image_type_alone_keeps_the_unit_listed(tmp_path, statement_off):
    from dataclasses import replace

    nb, odd = _listed_unit(tmp_path)
    rows = nb.channel_grid.by_repeat
    answers = nb.draft_answers
    units = dict(answers.units)
    units[odd] = replace(units.get(odd) or UnitAnswer((None,) * 3), image_type="channels")
    assert nb._answer(replace(answers, units=units))  # same tags, another image type
    nb.channel_grid.refresh()
    assert list(rows.listed()) == [odd]  # its tags did not change: still not answered


def test_5_a_unit_tagged_to_its_header_before_the_click_is_not_listed_after_change_and_restore(
    tmp_path, statement_off
):
    root, masks, out = _experiment(tmp_path, names=_contradicting)
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    odd = ("drug", "rep2", "t1")
    _tag(nb, odd, ("ACTIN", "DNA", "PHASE"))  # the header's own order, before any click
    _set_row(nb, ("rep2", 3), TAGS)  # the row contradicts the header, but the unit fits it
    rows = nb.channel_grid.by_repeat
    assert not rows.listed()
    _tag(nb, odd, ("EXTRA", "DNA", "PHASE"))
    assert list(rows.listed()) == [odd]
    _tag(nb, odd, ("ACTIN", "DNA", "PHASE"))
    assert not rows.listed()


# --- #366: mixed units, collisions across counts, refusals after a failed save ------------------


def _layout_mixed(key, uniform=True, agree=True):
    return UnitLayout(
        key, "/".join(key) + "/a.tif", 3, (None,) * 3, uniform=uniform, counts_agree=agree
    )


def test_b3_a_row_never_fills_a_unit_whose_images_disagree_pure():
    odd, short = ("ctrl", "rep2", "t1"), ("drug", "rep2", "t1")
    group = (
        _layout_mixed(odd, uniform=False),
        _layout_mixed(short, uniform=False, agree=False),
        _layout_mixed(("drug", "rep2", "t2")),
    )
    save, cells, refused = repeat_fill(DraftAnswers("absent", "0" * 40), group, TAGS, TAGS)
    assert list(save) == [("drug", "rep2", "t2")] and len(cells) == 3
    assert refused == {}  # the mixed units are named by repeat_apart, not by a header difference
    assert set(repeat_apart(group)) == {odd, short}


def test_b3_the_reference_of_a_row_is_never_a_unit_whose_images_disagree():
    from dataclasses import replace

    names = ("Ch1", "Ch2", "Ch3")
    mixed = replace(_layout(("ctrl", "rep2", "t1"), names=names), uniform=False)
    group = (
        mixed,
        _layout(("ctrl", "rep2", "t2"), names=("n1", "n2", "n3")),
        _layout(("drug", "rep2", "t2"), names=("n1", "n2", "n3")),
    )
    assert repeat_lead(group).key == ("ctrl", "rep2", "t2")
    save, cells, refused = repeat_fill(DraftAnswers("absent", "0" * 40), group, TAGS, TAGS)
    assert list(save) == [("ctrl", "rep2", "t2"), ("drug", "rep2", "t2")] and not refused
    assert repeat_lead(group[:1]) is None  # all apart: nothing to compare, nothing filled
    assert repeat_fill(DraftAnswers("absent", "0" * 40), group[:1], TAGS, TAGS)[0] == {}


def _mixed_experiment(tmp_path):
    """rep2/t1 of ctrl and of drug each have two images that name different channels.

    (Two images of one unit with different channel counts are refused earlier, when the axis
    roles are confirmed, so that case is covered by the pure test above.)
    """
    root, masks, out = _experiment(tmp_path)
    write_channels(root / "ctrl/rep2/t1/a.tif", ["Hoechst", "Actin", "Ch3"], KINDS4[:3], seed=40)
    write_channels(root / "ctrl/rep2/t1/b.tif", ["Actin", "Hoechst", "Ch3"], KINDS4[:3], seed=41)
    write_channels(root / "drug/rep2/t1/a.tif", ["Hoechst", "Actin", "Ch3"], KINDS4[:3], seed=42)
    write_channels(root / "drug/rep2/t1/b.tif", ["Hoechst", "Actin", "Other"], KINDS4[:3], seed=43)
    write_masks(
        masks,
        {"ctrl/rep2/t1/b.tif": 40, "drug/rep2/t1/b.tif": 41},
        {"Nuclei": "labels"},
    )
    return root, masks, out


def test_b3_a_row_does_not_fill_a_unit_with_conflicting_headers_or_counts(tmp_path, statement_off):
    root, masks, out = _mixed_experiment(tmp_path)
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    _set_row(nb, ("rep2", 3), TAGS)
    units = nb.draft_answers.units
    mixed = [("ctrl", "rep2", "t1"), ("drug", "rep2", "t1")]
    assert not any(units.get(k) and units[k].channels for k in mixed)
    assert units[("ctrl", "rep2", "t2")].channels == TAGS  # a uniform unit is filled
    rows = nb.channel_grid.by_repeat
    assert set(rows.listed()) == set(mixed)
    shown = rows.message
    assert "ctrl / rep2 / t1" in shown and "drug / rep2 / t1" in shown
    assert "its images do not all have one header" in shown  # the matrix's own reason
    assert "corrected or separated" in shown
    drawn = repr(rows.box.children)  # before any click it already says what it will not fill
    assert "2 unit(s) with disagreeing images will not be filled" in drawn
    assert "sets 2 imaging unit(s)" in drawn
    nb.channel_grid.refresh()
    assert set(rows.listed()) == set(mixed)  # still listed on every redraw


def _counts(ctrl_rep2, drug_rep2):
    def counts(key):
        if key[1] != "rep2":
            return 3
        return ctrl_rep2 if key[0] == "ctrl" else drug_rep2

    return counts


def _write_counts(tmp_path, counts, names=None):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, key in enumerate(UNITS):
        named = None if names is None else names(key)
        write_channels(root.joinpath(*key, "a.tif"), named, KINDS4[: counts(key)], seed=i + 1)
        write_masks(masks, {"/".join(key) + "/a.tif": i + 1}, {"Nuclei": "labels"})
    return root, masks, tmp_path / "out"


def _pair_drug_rep2(nb):
    from dataclasses import replace

    assert nb._answer(replace(nb.draft_answers, pairing={("drug", "rep2"): "repX"}))
    nb.channel_grid.refresh()


def test_b4_a_collision_is_found_when_the_conditions_have_other_channel_counts(
    tmp_path, statement_off
):
    root, masks, out = _write_counts(tmp_path, _counts(3, 4))
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    _pair_drug_rep2(nb)
    rows = nb.channel_grid.by_repeat
    assert {k for k in rows.buttons if k[0] == "rep2"} == {("rep2", 3), ("rep2", 4)}
    assert rows.buttons[("rep2", 3)].disabled and rows.buttons[("rep2", 4)].disabled
    text = repr(rows.box.children)
    assert text.count("ctrl → rep2, drug → repX") == 2
    for key, tags in ((("rep2", 3), TAGS), (("rep2", 4), EXTRA)):  # apply() re-checks
        group = rows._offered()[key]
        for drop, tag in zip(rows.drops[key], tags, strict=True):
            drop.value = tag
        assert not rows.apply(key, group)
    assert not any(u.channels for u in nb.draft_answers.units.values())


def test_b4_a_collision_is_found_in_fast_mode_when_filtering_hides_a_unit(tmp_path):
    def names(key):
        return ["Actin", "Hoechst", "Ch3"] if key[:2] == ("drug", "rep2") else None

    root, masks, out = _write_counts(tmp_path, _counts(3, 3), names)
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    grid = nb.channel_grid
    for index, tag in enumerate(TAGS):
        grid.matrix.template_cells[(("ctrl", "rep1", "t1"), index)].value = tag
    _pair_drug_rep2(nb)
    rows = grid.by_repeat
    offered = rows._offered()
    assert grid.same_channels.value and offered  # fast mode: some rep2 unit is offered
    assert {u.key[0] for g in offered.values() for u in g if u.key[1] == "rep2"} != {
        "ctrl",
        "drug",
    }  # the filter hides one condition's units: the row alone sees one condition
    for key in offered:
        if key[0] == "rep2":
            assert rows.buttons[key].disabled
            assert "ctrl → rep2, drug → repX" in repr(rows.box.children)
            assert not rows.apply(key, offered[key])
    assert not nb.draft_answers.units.get(("drug", "rep2", "t1"))


def test_b5_a_failed_corrective_save_keeps_the_unit_listed_and_check_save_refusing(
    tmp_path, statement_off, monkeypatch
):
    def names(key):
        return ["ACTIN", "DNA", "PHASE"] if key == ("drug", "rep2", "t1") else None

    root, masks, out = _experiment(tmp_path, names=names)
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    odd = ("drug", "rep2", "t1")
    grid = nb.channel_grid
    layout = next(u for u in grid.layouts if u.key == odd)
    grid.tag_like([layout], ("EXTRA", "DNA", "PHASE"))  # tagged by hand, against its header
    answer_all(nb)
    rows = grid.by_repeat
    _set_row(nb, ("rep2", 3), TAGS)  # the row contradicts the unit's header: refused
    answer_all(nb)  # whatever the row left unconfirmed
    assert list(rows.listed()) == [odd]
    group = rows._offered()[("rep2", 3)]
    for drop, tag in zip(rows.drops[("rep2", 3)], ("ACTIN", "DNA", "PHASE"), strict=True):
        drop.value = tag  # a corrective row that fits the header

    def failing(self, tags, cells):
        raise RuntimeError("the save failed")

    monkeypatch.setattr(type(grid), "commit", failing)
    with pytest.raises(RuntimeError):
        rows.apply(("rep2", 3), group)
    monkeypatch.undo()
    assert list(rows.listed()) == [odd]  # nothing was saved: the contradiction is still there
    nb._refresh_check_save()
    assert nb.save_button.disabled and not nb.save_experiment()
    assert "row did not fill: see Channels" in nb.save_blocker.value
    assert not (out / DESCRIPTION_FILENAME).exists()
    assert rows.apply(("rep2", 3), group)  # the same corrective save, now succeeding
    assert not rows.listed()


def test_b5_a_corrective_save_the_grid_refuses_keeps_the_unit_listed(
    tmp_path, statement_off, monkeypatch
):
    def names(key):
        return ["ACTIN", "DNA", "PHASE"] if key == ("drug", "rep2", "t1") else None

    root, masks, out = _experiment(tmp_path, names=names)
    nb = opened(root, masks, out)
    set_labels(nb, list(EXTRA))
    odd = ("drug", "rep2", "t1")
    grid = nb.channel_grid
    grid.tag_like([next(u for u in grid.layouts if u.key == odd)], ("EXTRA", "DNA", "PHASE"))
    answer_all(nb)
    rows = grid.by_repeat
    _set_row(nb, ("rep2", 3), TAGS)
    group = rows._offered()[("rep2", 3)]
    for drop, tag in zip(rows.drops[("rep2", 3)], ("ACTIN", "DNA", "PHASE"), strict=True):
        drop.value = tag
    monkeypatch.setattr(type(grid), "commit", lambda self, tags, cells: False)
    assert not rows.apply(("rep2", 3), group)
    assert list(rows.listed()) == [odd]
