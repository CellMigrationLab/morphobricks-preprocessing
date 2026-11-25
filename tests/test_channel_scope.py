"""The rows the guided *Tag channels* step opens for each answer (pure, owner 25 Nov 2026)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from morphobricks_preprocessing.channel_labels import UnitLayout
from morphobricks_preprocessing.channel_scope import (
    FROM_BROADER,
    FROM_CHOICE,
    FROM_HEADER,
    FROM_NONE,
    FROM_SAVED,
    REPEAT,
    UNIT,
    WHOLE,
    apart,
    counts,
    form_rows,
    row_error,
    scope_of_draft,
    seed,
    taggable,
)
from morphobricks_preprocessing.draft_answers import DraftAnswers, LayoutAnswer, UnitAnswer

TAGS = ("DNA", "ACTIN", "PHASE")
KEYS = [(c, f"rep{r}", "single") for c in ("ctrl", "drug") for r in (1, 2, 10)]


def layout(key, count=3, names=None, uniform=True):
    return UnitLayout(key, "a.tif", count, tuple(names or (None,) * count), uniform=uniform)


def answers(units=None):
    return DraftAnswers("absent", "0" * 40, units=units or {})


def typed(layouts, kind="channels"):
    return {u.key: UnitAnswer((None,) * u.channels, kind) for u in layouts}


def test_same_count_whole_is_one_row_repeat_one_per_repeat_unit_one_per_unit():
    units = [layout(k) for k in KEYS]
    saved = typed(units)
    assert counts(units, saved) == [3]
    assert [r.key for r in form_rows(units, saved, WHOLE)] == [(WHOLE, 3)]
    assert [r.key for r in form_rows(units, saved, REPEAT)] == [
        (REPEAT, "rep1", 3),
        (REPEAT, "rep2", 3),
        (REPEAT, "rep10", 3),  # natural order: rep2 before rep10
    ]
    assert len(form_rows(units, saved, UNIT)) == len(KEYS)
    whole = form_rows(units, saved, WHOLE)[0]
    assert len(whole.units) == len(KEYS)


def test_mixed_counts_whole_is_one_row_per_count():
    units = [layout(k, 4 if k[1] == "rep2" else 3) for k in KEYS]
    saved = typed(units)
    assert counts(units, saved) == [3, 4]
    rows = form_rows(units, saved, WHOLE)
    assert [(r.key, len(r.units)) for r in rows] == [((WHOLE, 3), 4), ((WHOLE, 4), 2)]
    assert [r.key for r in form_rows(units, saved, REPEAT)] == [
        (REPEAT, "rep1", 3),
        (REPEAT, "rep2", 4),
        (REPEAT, "rep10", 3),
    ]


def test_colour_units_and_units_whose_images_disagree_are_in_no_row():
    units = [layout(k) for k in KEYS]
    odd = replace(units[1], uniform=False)
    units[1] = odd
    saved = typed(units)
    saved[units[0].key] = UnitAnswer(("R", "G", "B"), "rgb")
    assert units[0] not in taggable(units, saved)
    assert apart(taggable(units, saved)) == [odd]
    rowed = {u.key for r in form_rows(units, saved, UNIT) for u in r.units}
    assert rowed == {u.key for u in units[2:]}


def test_a_row_shows_the_choice_then_a_broader_choice_then_saved_then_header_names():
    units = [layout(k, names=TAGS) for k in KEYS]
    saved = typed(units)
    row = form_rows(units, saved, UNIT)[0]
    key = row.units[0].key
    labels = list(TAGS)
    assert seed(row, {}, answers(saved), labels) == (list(TAGS), FROM_HEADER)
    saved[key] = UnitAnswer(("ACTIN", "DNA", "PHASE"), "channels")
    assert seed(row, {}, answers(saved), labels) == (["ACTIN", "DNA", "PHASE"], FROM_SAVED)
    pending = {(WHOLE, 3): ["PHASE", "DNA", "ACTIN"]}
    assert seed(row, pending, answers(saved), labels) == (["PHASE", "DNA", "ACTIN"], FROM_BROADER)
    pending[(REPEAT, key[1], 3)] = ["DNA", "PHASE", "ACTIN"]
    assert seed(row, pending, answers(saved), labels)[0] == ["DNA", "PHASE", "ACTIN"]
    pending[row.key] = ["DNA", None, None]
    assert seed(row, pending, answers(saved), labels) == (["DNA", None, None], FROM_CHOICE)
    assert seed(row, pending, answers(saved), ["DNA"])[0] == ["DNA", None, None]  # label gone


def test_header_names_propose_only_when_every_unit_of_the_row_names_the_same_labels():
    units = [layout(k, names=TAGS) for k in KEYS]
    units[3] = layout(KEYS[3], names=("ACTIN", "DNA", "PHASE"))
    saved = typed(units)
    row = form_rows(units, saved, WHOLE)[0]
    assert seed(row, {}, answers(saved), list(TAGS)) == ([None] * 3, FROM_NONE)


def test_a_row_is_refused_until_every_channel_is_tagged_with_each_label_once():
    assert row_error(["DNA", None, "PHASE"]) == "tag every channel"
    assert row_error(["DNA", "DNA", "PHASE"]) == "each label once"
    assert row_error(list(TAGS)) is None


def test_only_a_fast_mode_draft_answers_the_whole_dataset_question():
    assert scope_of_draft(answers()) is None
    stated = replace(answers(), channel_layout=LayoutAnswer(TAGS, "channels", "2026-11-25"))
    assert scope_of_draft(stated) == WHOLE


def test_an_unknown_scope_is_refused():
    with pytest.raises(ValueError, match="unknown scope"):
        form_rows([], {}, "everything")
