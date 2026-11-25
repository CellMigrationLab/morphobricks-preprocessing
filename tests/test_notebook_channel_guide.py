"""The guided channel step: Submit is the only save, and a choice never lags or resets (25 Nov).

The owner's Colab test of the v0.9.0 candidate: every dropdown change saved and redrew the
page (lag), there was one row per unit even when every image had the same channels, and values
jumped after picking them. In the shipped notebook (the stepper) the step is now a question, a
few rows and one Submit (:mod:`.notebook_channels_guide`); these tests pin what fixes each
complaint and that Submit keeps the #376 guarantees (one draft write for tags and review, header
contradictions refused, R_DRAFT_CHANGED).
"""

from __future__ import annotations

import pytest

from morphobricks_preprocessing.description_file import DRAFT_FILENAME
from morphobricks_preprocessing.draft_format import answers_from_bytes

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

import ipywidgets as widgets  # noqa: E402

from test_notebook_channel_step_acceptance import (  # noqa: E402
    TAGS3,
    UNITS,
    choose,
    experiment,
    folder,
    opened,
    shown,
)

OTHER_ORDER = ["ACTIN", "DNA", "PHASE"]


def dropdowns(widget):
    """Yield every dropdown in a widget tree."""
    if isinstance(widget, widgets.Dropdown):
        yield widget
    for child in getattr(widget, "children", ()):
        yield from dropdowns(child)


def counting(nb, monkeypatch):
    """Count the draft writes and the redraws of the channel card from now on."""
    calls = {"saves": 0, "refresh": 0}
    save, refresh = nb._save_answers, nb.channel_grid.refresh

    def counted_save(*args, **kwargs):
        calls["saves"] += 1
        return save(*args, **kwargs)

    def counted_refresh():
        calls["refresh"] += 1
        return refresh()

    monkeypatch.setattr(nb, "_save_answers", counted_save)
    monkeypatch.setattr(nb.channel_grid, "refresh", counted_refresh)
    return calls


def test_a_dropdown_change_saves_nothing_and_redraws_nothing(tmp_path, monkeypatch):
    exp = experiment(tmp_path)
    nb = opened(*exp, TAGS3)
    guide = nb.channel_grid.guide
    guide.same.value = "yes"
    calls, renders = counting(nb, monkeypatch), guide.renders
    drops = guide.drops[("whole", 3)]
    choose(nb, ("whole", 3), TAGS3)
    assert calls == {"saves": 0, "refresh": 0} and guide.renders == renders
    assert guide.drops[("whole", 3)] is drops  # the very same widgets: nothing rebuilt
    assert [d.value for d in drops] == TAGS3


def test_submit_is_one_draft_write_holding_tags_review_and_statement(tmp_path, monkeypatch):
    swapped = ("drug", "rep2", "single")
    exp = experiment(tmp_path, names=lambda key, n: OTHER_ORDER if key == swapped else TAGS3)
    nb = opened(*exp, TAGS3)
    guide = nb.channel_grid.guide
    guide.same.value = "yes"
    choose(nb, ("whole", 3), TAGS3)
    calls = counting(nb, monkeypatch)
    assert guide.submit()
    assert calls["saves"] == 1  # tags, aliases, tag review and channel_layout: one write
    saved = answers_from_bytes((exp[2] / DRAFT_FILENAME).read_bytes())
    assert saved.channel_layout.channels == tuple(TAGS3)
    assert set(saved.tag_review.refused) == {swapped}
    filled = {k for k, u in saved.units.items() if u.channels == tuple(TAGS3)}
    assert filled == set(UNITS) - {swapped}
    assert all(saved.units[k].channels_from == "experiment" for k in filled)
    assert saved.channels["DNA"].header_names == ("DNA",)  # the confirmed header evidence


def test_choices_survive_any_redraw_of_the_card(tmp_path):
    exp = experiment(tmp_path, count=lambda key: 4 if key[1] == "rep2" else 3)
    nb = opened(*exp, [*TAGS3, "EXTRA"])
    guide = nb.channel_grid.guide
    guide.same.value = "yes"
    choose(nb, ("whole", 3), OTHER_ORDER)
    guide.drops[("whole", 4)][0].value = "EXTRA"
    nb.channel_grid.refresh()  # what a type change, a label or a listed unit redraws
    nb._refresh_channels()
    assert shown(nb) == {("whole", 3): OTHER_ORDER, ("whole", 4): ["EXTRA", None, None, None]}
    assert "tag every channel" in guide.notes[("whole", 4)].value


def test_an_incomplete_row_or_a_label_twice_saves_nothing(tmp_path, monkeypatch):
    exp = experiment(tmp_path, count=lambda key: 4 if key[1] == "rep2" else 3)
    nb = opened(*exp, [*TAGS3, "EXTRA"])
    guide = nb.channel_grid.guide
    guide.same.value = "yes"
    choose(nb, ("whole", 3), ["DNA", "DNA", "PHASE"])
    calls = counting(nb, monkeypatch)
    assert not guide.submit()
    assert calls["saves"] == 0
    assert "3 channels: each label once" in guide.message.value
    assert "4 channels: tag every channel" in guide.message.value
    guide.same.value = None
    assert not guide.submit() and "Answer the question" in guide.message.value


def test_per_repeat_submit_refuses_a_contradicting_unit_and_fills_the_rest(tmp_path):
    odd = ("ctrl", "rep1", "single")
    exp = experiment(tmp_path, names=lambda key, n: OTHER_ORDER if key == odd else TAGS3)
    nb = opened(*exp, TAGS3)
    guide, rows = nb.channel_grid.guide, nb.channel_grid.by_repeat
    guide.same.value = "no"
    guide.narrow.value = "repeat"
    assert len(guide.rows) == 3  # one row per repeat, not one per unit
    for rep in ("rep1", "rep2", "rep3"):
        choose(nb, ("repeat", rep, 3), TAGS3)
    assert guide.submit()
    assert set(rows.listed()) == {odd}
    units = nb.draft_answers.units
    assert all(units[k].channels == tuple(TAGS3) for k in UNITS if k != odd)
    assert nb.draft_answers.channel_layout is None  # per repeat: a strict file
    # a deliberate tag of that unit alone, fitting its header, is an action on it
    guide.narrow.value = "unit"
    assert shown(nb)[("unit", odd)] == TAGS3  # from its repeat's choice, not saved
    choose(nb, ("unit", odd), OTHER_ORDER)
    assert guide.submit()
    assert rows.listed() == {}
    assert nb.draft_answers.units[odd].channels == tuple(OTHER_ORDER)


def test_a_header_that_still_contradicts_stays_listed_whatever_is_submitted(tmp_path):
    odd = ("ctrl", "rep1", "single")
    exp = experiment(tmp_path, names=lambda key, n: OTHER_ORDER if key == odd else TAGS3)
    nb = opened(*exp, TAGS3)
    guide, rows = nb.channel_grid.guide, nb.channel_grid.by_repeat
    guide.same.value = "yes"
    choose(nb, ("whole", 3), TAGS3)
    assert guide.submit() and set(rows.listed()) == {odd}
    assert guide.submit() and set(rows.listed()) == {odd}  # again: never filled, never dropped
    guide.same.value = "no"
    guide.narrow.value = "unit"
    choose(nb, ("unit", odd), TAGS3)  # contradicts its own header names
    assert guide.submit() and set(rows.listed()) == {odd}
    assert not any(nb.draft_answers.units.get(odd, None).channels or ())


def test_a_draft_changed_by_another_notebook_refuses_submit(tmp_path):
    exp = experiment(tmp_path)
    a = opened(*exp, TAGS3)
    b = opened(*exp, TAGS3)
    a.channel_grid.guide.same.value = "yes"
    choose(a, ("whole", 3), TAGS3)
    assert a.channel_grid.guide.submit()
    saved = (exp[2] / DRAFT_FILENAME).read_bytes()
    guide = b.channel_grid.guide
    guide.same.value = "no"
    guide.narrow.value = "repeat"
    for rep in ("rep1", "rep2", "rep3"):
        choose(b, ("repeat", rep, 3), OTHER_ORDER)
    assert not guide.submit()
    assert (exp[2] / DRAFT_FILENAME).read_bytes() == saved  # A's answers, byte for byte
    assert "changed since this notebook read or saved it" in guide.message.value


def test_a_reopened_fast_mode_draft_answers_yes_and_shows_its_saved_tags(tmp_path):
    exp = experiment(tmp_path)
    nb = opened(*exp, TAGS3)
    nb.channel_grid.guide.same.value = "yes"
    choose(nb, ("whole", 3), OTHER_ORDER)
    assert nb.channel_grid.guide.submit()
    again = opened(*exp, [])
    guide = again.channel_grid.guide
    assert guide.same.value == "yes"
    assert shown(again) == {("whole", 3): OTHER_ORDER}
    assert "Saved tags" in guide.saved_box.value
    assert folder(UNITS[0]).split("/")[0] in guide.saved_box.value


def test_the_number_of_rows_follows_the_answer(tmp_path):
    exp = experiment(tmp_path)
    nb = opened(*exp, TAGS3)
    guide = nb.channel_grid.guide
    counts = []
    for same, narrow in (("yes", None), ("no", "repeat"), ("no", "unit")):
        guide.same.value = same
        guide.narrow.value = narrow
        counts.append(len([d for d in guide.drops.values()]))
    assert counts == [1, 3, len(UNITS)]
    assert not list(dropdowns(nb.channel_grid.blocks))  # no per-unit grid in the stepper
