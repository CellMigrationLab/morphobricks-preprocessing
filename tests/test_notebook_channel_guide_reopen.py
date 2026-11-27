"""The guided channel step on a reopened draft (review of #546, F1 and F3).

The tick of fast mode is hidden in the guided step and only Submit sets it, so it is not in an
unpublished draft. On the review's head a strict draft (per repeat, per unit, or one list per
channel count) reopened ticked by default, and opening Channels wrote, with no click,
``channel_layout`` = the first unit's order with every unit ``experiment``: Check and save then
published every unit in rep1's order. A reopened draft now keeps its own mode, opening Channels
writes nothing, and the guide is answered as the draft already is.
"""

from __future__ import annotations

import pytest

from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from morphobricks_preprocessing.draft_format import answers_from_bytes

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_description  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402
from test_notebook_channel_step_acceptance import (  # noqa: E402
    LABELS4,
    TAGS3,
    UNITS,
    choose,
    draft,
    experiment,
    finish,
    folder,
    opened,
    published,
    shown,
    unit_channels,
)

ORDERS = {"rep1": TAGS3, "rep2": ["ACTIN", "DNA", "PHASE"], "rep3": ["PHASE", "ACTIN", "DNA"]}


def _per_repeat(exp):
    nb = opened(*exp, TAGS3)
    guide = nb.channel_grid.guide
    guide.same.value = "no"
    guide.narrow.value = "repeat"
    for rep, tags in ORDERS.items():
        choose(nb, ("repeat", rep, 3), tags)
    assert guide.submit()
    return nb


def _reopened(exp):
    """Open the draft in a new notebook, up to Channels (nothing answered on the way)."""
    before = draft(exp[2])
    nb = opened(*exp, [], typed=False)
    assert draft(exp[2]) == before  # opening Channels on a reopened draft writes 0 bytes
    return nb


def _saved(exp):
    return answers_from_bytes(draft(exp[2]))


def test_a_reopened_per_repeat_draft_stays_strict_and_publishes_each_repeat_s_order(tmp_path):
    exp = experiment(tmp_path)
    _per_repeat(exp)
    nb = _reopened(exp)
    assert _saved(exp).channel_layout is None  # never converted with no click
    assert not nb.channel_grid.same_channels.value
    guide = nb.channel_grid.guide
    assert (guide.same.value, guide.narrow.value) == ("no", "repeat")  # as the draft is
    assert "Pre-answered from your saved tags" in guide.draft_note.value
    assert shown(nb) == {("repeat", rep, 3): tags for rep, tags in ORDERS.items()}
    finish(nb)
    assert nb.save_experiment()
    doc = published(exp[2])
    assert "channel_layout" not in doc
    assert unit_channels(doc) == {folder(k): (ORDERS[k[1]], None) for k in UNITS}


def test_a_reopened_per_unit_draft_stays_strict(tmp_path):
    exp = experiment(tmp_path)
    nb = opened(*exp, TAGS3)
    guide = nb.channel_grid.guide
    guide.same.value = "no"
    guide.narrow.value = "unit"
    shifts = {k: (i + (k[0] == "drug")) % 3 for i, k in enumerate(sorted(UNITS))}
    orders = {k: TAGS3[n:] + TAGS3[:n] for k, n in shifts.items()}  # no repeat has one order
    for key, tags in orders.items():
        choose(nb, ("unit", key), tags)
    assert guide.submit()
    again = _reopened(exp)
    assert _saved(exp).channel_layout is None
    assert (again.channel_grid.guide.same.value, again.channel_grid.guide.narrow.value) == (
        "no",
        "unit",
    )
    finish(again)
    assert again.save_experiment()
    assert unit_channels(published(exp[2])) == {folder(k): (orders[k], None) for k in UNITS}


def test_a_reopened_mixed_count_yes_draft_stays_strict(tmp_path):
    exp = experiment(tmp_path, count=lambda key: 4 if key[1] == "rep2" else 3)
    nb = opened(*exp, LABELS4)
    guide = nb.channel_grid.guide
    guide.same.value = "yes"
    choose(nb, ("whole", 3), TAGS3)
    choose(nb, ("whole", 4), LABELS4)
    assert guide.submit()
    again = _reopened(exp)
    assert _saved(exp).channel_layout is None
    assert again.channel_grid.guide.same.value == "yes"  # one list per count reproduces it
    assert shown(again) == {("whole", 3): TAGS3, ("whole", 4): LABELS4}
    finish(again)
    assert again.save_experiment()
    doc = published(exp[2])
    assert "channel_layout" not in doc
    assert unit_channels(doc) == {
        folder(k): (LABELS4 if k[1] == "rep2" else TAGS3, None) for k in UNITS
    }


def test_a_reopened_fast_draft_stays_fast(tmp_path):
    exp = experiment(tmp_path)
    nb = opened(*exp, TAGS3)
    nb.channel_grid.guide.same.value = "yes"
    choose(nb, ("whole", 3), TAGS3)
    assert nb.channel_grid.guide.submit()
    again = _reopened(exp)
    assert again.channel_grid.same_channels.value
    assert _saved(exp).channel_layout.channels == tuple(TAGS3)
    finish(again)
    assert again.save_experiment()
    assert unit_channels(published(exp[2])) == {folder(k): (TAGS3, "experiment") for k in UNITS}


def test_an_all_colour_reopen_still_publishes_r_g_b(tmp_path):
    exp = experiment(tmp_path, rgb=lambda key: True)
    opened(*exp, [])  # the type for all folders: colour; nothing else
    again = _reopened(exp)
    finish(again)
    assert again.save_experiment()
    doc = published(exp[2])
    assert doc["channel_layout"]["channels"] == ["R", "G", "B"]
    assert all(v == (["R", "G", "B"], "experiment") for v in unit_channels(doc).values())


def test_a_reopened_draft_with_a_listed_unit_keeps_its_tags_and_says_so(tmp_path):
    odd = ("drug", "rep2", "single")
    names = ["ACTIN", "DNA", "PHASE"]
    exp = experiment(tmp_path, names=lambda key, n: names if key == odd else TAGS3)
    nb = opened(*exp, TAGS3)
    guide = nb.channel_grid.guide
    guide.same.value = "no"
    guide.narrow.value = "repeat"
    for rep in ("rep1", "rep2", "rep3"):
        choose(nb, ("repeat", rep, 3), TAGS3)
    assert guide.submit() and set(nb.channel_grid.by_repeat.listed()) == {odd}
    again = _reopened(exp)
    assert not again.channel_grid.same_channels.value  # strict: saved tags of its own
    assert set(again.channel_grid.by_repeat.listed()) == {odd}  # still listed
    assert again.channel_grid.guide.same.value is None  # not every unit tagged: asked
    assert "Saved per-unit tags are kept" in again.channel_grid.guide.draft_note.value
    finish(again)
    assert not again.save_experiment()
    assert not (exp[2] / DESCRIPTION_FILENAME).exists()
    assert _saved(exp).channel_layout is None


def _per_unit(exp):
    nb = opened(*exp, TAGS3)
    guide = nb.channel_grid.guide
    guide.same.value = "no"
    guide.narrow.value = "unit"
    for i, key in enumerate(sorted(UNITS)):
        choose(nb, ("unit", key), TAGS3[i % 3 :] + TAGS3[: i % 3])
    assert guide.submit()


def _mixed_yes(exp):
    nb = opened(*exp, LABELS4)
    nb.channel_grid.guide.same.value = "yes"
    choose(nb, ("whole", 3), TAGS3)
    choose(nb, ("whole", 4), LABELS4)
    assert nb.channel_grid.guide.submit()


def _fast(exp):
    nb = opened(*exp, TAGS3)
    nb.channel_grid.guide.same.value = "yes"
    choose(nb, ("whole", 3), TAGS3)
    assert nb.channel_grid.guide.submit()


DRAFTS = {
    "per_repeat": (_per_repeat, {}),
    "per_unit": (_per_unit, {}),
    "mixed_count_yes": (_mixed_yes, {"count": lambda key: 4 if key[1] == "rep2" else 3}),
    "fast": (_fast, {}),
    "all_colour": (lambda exp: opened(*exp, []), {"rgb": lambda key: True}),
}


@pytest.mark.parametrize("kind", sorted(DRAFTS))
def test_opening_channels_on_a_reopened_draft_writes_no_byte(tmp_path, monkeypatch, kind):
    answer, shape = DRAFTS[kind]
    exp = experiment(tmp_path, **shape)
    answer(exp)
    before = draft(exp[2])
    writes = []
    real = notebook_description.save_draft

    def counted(*args, **kwargs):
        writes.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(notebook_description, "save_draft", counted)
    nb = opened(*exp, [], typed=False)  # a new notebook on the draft, up to Channels
    assert writes == []  # no draft write at all, not even of the same bytes
    assert draft(exp[2]) == before
    assert nb.channel_grid.guided and ui.is_visible(nb.channel_grid.widget)


def test_a_restored_per_repeat_draft_opens_strict_with_no_draft_write(tmp_path, monkeypatch):
    """The restore of #547 (output folder set, nothing clicked) reopens the same way."""
    exp = experiment(tmp_path)
    _per_repeat(exp)
    before = draft(exp[2])
    writes = []
    real = notebook_description.save_draft

    def counted(*args, **kwargs):
        writes.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(notebook_description, "save_draft", counted)
    nb = PreprocessingNotebook()
    nb.steps()
    nb.results_step()
    nb.folder.value = str(exp[0])
    nb.output.value = str(exp[2])
    nb.scan_button.click()  # the restore, on the press (#579): the check, the saved answers
    assert nb.preflight is not None and nb.confirmed_mapping is None
    nb.masks_folder.value = str(exp[1])
    nb.preview_checked.set(True)
    nb.show_preview()
    nb.confirm_button.click()  # the axes: the user's one click
    nb._stepper.go(2)  # Channels
    assert writes == [] and draft(exp[2]) == before
    assert not nb.channel_grid.same_channels.value
    guide = nb.channel_grid.guide
    assert (guide.same.value, guide.narrow.value) == ("no", "repeat")
    assert shown(nb) == {("repeat", rep, 3): tags for rep, tags in ORDERS.items()}
    monkeypatch.undo()
    finish(nb)
    assert nb.save_experiment()
    doc = published(exp[2])
    assert "channel_layout" not in doc
    assert unit_channels(doc) == {folder(k): (ORDERS[k[1]], None) for k in UNITS}


def test_an_abandoned_yes_never_replaces_the_saved_repeat_orders(tmp_path):
    """Review of #546 at e40f1ad: Yes filled with rep1's order, then back to No, per repeat.

    The repeat rows showed rep1's order (a broader choice ranked above the saved tags) under
    "Pre-answered from your saved tags", and one Submit published rep1's order everywhere.
    """
    exp = experiment(tmp_path)
    _per_repeat(exp)
    nb = _reopened(exp)
    guide = nb.channel_grid.guide
    assert "Pre-answered from your saved tags" in guide.draft_note.value
    guide.same.value = "yes"
    assert guide.draft_note.value == ""  # the answer changed: the note no longer holds
    choose(nb, ("whole", 3), ORDERS["rep1"])
    guide.same.value = "no"
    assert guide.narrow.value == "repeat"
    assert shown(nb) == {("repeat", rep, 3): tags for rep, tags in ORDERS.items()}
    assert all("as saved" in guide.notes[("repeat", rep, 3)].value for rep in ORDERS)
    assert "Pre-answered from your saved tags" not in guide.draft_note.value
    assert guide.submit()
    finish(nb)
    assert nb.save_experiment()
    doc = published(exp[2])
    assert "channel_layout" not in doc
    assert unit_channels(doc) == {folder(k): (ORDERS[k[1]], None) for k in UNITS}


def test_a_pick_in_a_pre_answered_row_takes_the_note_back(tmp_path):
    exp = experiment(tmp_path)
    _per_repeat(exp)
    nb = _reopened(exp)
    guide = nb.channel_grid.guide
    assert "Pre-answered from your saved tags" in guide.draft_note.value
    choose(nb, ("repeat", "rep2", 3), ORDERS["rep3"])
    assert guide.draft_note.value == ""
