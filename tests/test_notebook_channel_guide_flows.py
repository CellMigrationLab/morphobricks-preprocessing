"""Saved-experiment flows through the guided channel step (review of #546, F2).

The modules that drive the per-unit grid through the stepper pin it (``legacy_grid``). These
tests take their flows through the guided step instead, as the shipped notebook does: the #281
header-name loss and its click, the re-confirmation scope of a removal (#279), what a change of
answer between fast and strict mode does to the aliases (the guided step's counterpart of the
untick tests), and the pre-release header rename and channel swap (#257).
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import date

import pytest

from channel_images import write_channels
from morphobricks_preprocessing.description_file import RECORDS_FILENAME
from test_notebook_check_save import FOLDERS, KINDS, exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_check_save  # noqa: E402
from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from test_notebook_axes_quick import stepper  # noqa: E402
from test_notebook_channel_step_acceptance import choose, finish, published  # noqa: E402

TAGS = ["DNA", "ACTIN", "PHASE"]
KEY = ("ctrl", "rep1", "single")
GONE = "drug/rep2"


def _text(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value))


def submit(nb, scope="yes", rows=None):
    """Answer the image type and labels if needed, then the guide, and Submit."""
    if not nb.type_line.complete:
        nb.type_line.apply.click()
    card = nb.channel_labels
    if not card.confirmed:
        while len(card.rows) < len(TAGS):
            card.add_button.click()
        for row, text in zip(card.rows, TAGS, strict=False):
            row.id_box.value = text
        card.confirm.button.click()
    guide = nb.channel_grid.guide
    if scope == "yes":
        guide.same.value = "yes"
    else:
        guide.same.value = "no"
        guide.narrow.value = scope
    for key, tags in (rows or ({("whole", 3): TAGS} if scope == "yes" else {})).items():
        choose(nb, key, tags)
    return guide.submit()


def _open(exp):  # noqa: F811
    nb = stepper(*exp)
    nb.quick_confirm.click()
    return nb


def _saved(exp, names=None):  # noqa: F811
    """Save the experiment through the guided step; ``names`` rewrites headers first."""
    for name, given in (names or {}).items():
        write_channels(exp[0] / name / "a.tif", given, KINDS, seed=FOLDERS.index(name) + 1)
    first = _open(exp)
    if names:  # headers that differ between units: tagged unit by unit (deliberate hand-tags)
        keys = [(*name.split("/"), "single") for name in FOLDERS]
        assert submit(first, "unit", {("unit", tuple(k)): TAGS for k in keys})
    else:
        assert submit(first)
    finish(first)
    assert first.save_experiment(), _text(first.save_status.value)
    return first


def _drop(exp, gone):  # noqa: F811
    shutil.rmtree(exp[0] / gone)
    shutil.rmtree(exp[1] / "Nuclei" / gone)


def _records(exp):  # noqa: F811
    path = exp[2] / RECORDS_FILENAME
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def _remove(nb):
    finish(nb)
    (row,) = nb.removals_box.children
    row.children[1].click()  # Yes, remove
    nb._refresh_check_save()


# --- #281: a lost header name is confirmed by its own click -------------------------------------


def test_281_the_dead_end_is_resolved_by_the_click_through_the_guided_step(exp):  # noqa: F811
    _saved(exp, {"ctrl/rep1": ["Hoechst", "Actin", "Ch3"]})
    write_channels(exp[0] / "ctrl/rep1/a.tif", ["DAPI", "Actin", "Ch3"], KINDS, seed=1)
    _drop(exp, GONE)
    nb = _open(exp)
    _remove(nb)
    assert nb.save_experiment(), _text(nb.save_status.value)
    assert nb._published().description.channels["DNA"].header_names == ("DAPI", "Hoechst")
    before = _records(exp)
    later = _open(exp)
    guide = later.channel_grid.guide
    assert (guide.same.value, guide.narrow.value) == ("no", "repeat")  # from the saved tags
    assert submit(later, "repeat")  # every unit tagged again: "Hoechst" is in no cell now
    finish(later)
    assert later.draft_answers.channels["DNA"].header_names == ("DAPI",)
    change = "DNA: header names “DAPI”, “Hoechst” → “DAPI”"
    assert ui.is_visible(later.names_box) and not later.names_confirm.value  # nothing ticked
    assert change in _text(later.names_list.value)
    assert later.save_button.disabled and not later.save_experiment()
    assert _records(exp) == before
    later.names_confirm.button.click()  # Yes, this header name changed
    assert later.save_experiment(), _text(later.save_status.value)
    records = _records(exp)[len(before) :]
    assert [(r["old_id"], r["new_id"], r["type"]) for r in records] == [
        ("DNA", None, "reconfirmation")
    ]
    assert later._published().description.channels["DNA"].header_names == ("DAPI",)


# --- #279: a removal re-confirms only the change it answers -------------------------------------


def test_279_a_removal_never_accepts_a_name_lost_in_a_folder_still_there(exp):  # noqa: F811
    _saved(exp, {"ctrl/rep1": ["Hoechst", "Actin", "Ch3"]})
    write_channels(exp[0] / "ctrl/rep1/a.tif", ["Nuc", "Actin", "Ch3"], KINDS, seed=1)
    _drop(exp, GONE)
    nb = _open(exp)
    before = len(_records(exp))
    assert submit(nb, "repeat")  # the guided re-tag: Hoechst is no longer a name of DNA
    assert "Hoechst" not in (nb.draft_answers.channels["DNA"].header_names or ())
    _remove(nb)
    change = "DNA: header names “DAPI”, “Hoechst” → “DAPI”, “Nuc”"
    assert ui.is_visible(nb.names_box) and not nb.names_confirm.value
    assert change in _text(nb.names_list.value)
    assert nb.save_button.disabled and not nb.save_experiment()
    assert "R_CONFIRMED_ID_CHANGED at channels.DNA" in _text(nb.checks.value)
    assert len(_records(exp)) == before
    nb.names_confirm.button.click()
    assert nb.save_experiment(), _text(nb.save_status.value)
    assert nb._published().description.channels["DNA"].header_names == ("DAPI", "Nuc")


# --- the aliases when the answer moves between fast and strict mode -----------------------------


def test_moving_between_fast_and_strict_keeps_exactly_the_aliases_of_tagged_cells(exp):  # noqa: F811
    nb = _open(exp)
    assert submit(nb)  # Yes: fast mode
    aliases = {k: c.header_names for k, c in nb.draft_answers.channels.items()}
    assert aliases == {"DNA": ("DAPI",), "ACTIN": ("Actin",), "PHASE": ("Ch3",)}
    assert nb.draft_answers.channel_layout is not None
    assert submit(nb, "repeat")  # No, per repeat: each row starts from the Yes choice
    answers = nb.draft_answers
    assert answers.channel_layout is None and all(
        u.channels_from is None for u in answers.units.values()
    )
    assert {k: c.header_names for k, c in answers.channels.items()} == aliases
    # a strict per-unit order that contradicts the saved aliases is refused, not saved
    rows = {("unit", k): list(u.channels) for k, u in answers.units.items()}
    rows[("unit", KEY)] = ["ACTIN", "DNA", "PHASE"]
    assert submit(nb, "unit", rows)
    assert set(nb.channel_grid.by_repeat.listed()) == {KEY}
    assert nb.draft_answers.units[KEY].channels == tuple(TAGS)  # never reordered
    assert {k: c.header_names for k, c in nb.draft_answers.channels.items()} == aliases
    nb.channel_grid.by_repeat.override.buttons[KEY].click()  # its own tags, confirmed
    for index, tag in enumerate(TAGS):
        nb.channel_grid.by_repeat.override.cells[(KEY, index)].value = tag
    assert nb.channel_grid.by_repeat.override.confirm()
    assert submit(nb)  # back to Yes: fast mode again, the same aliases
    assert nb.draft_answers.channel_layout.channels == tuple(TAGS)
    assert {k: c.header_names for k, c in nb.draft_answers.channels.items()} == aliases
    finish(nb)
    assert nb.save_experiment()
    published(exp[2])  # the Spec's reference validator


# --- #257: a header rename and a channel swap on a saved experiment -----------------------------


class _FirstOfJanuary(date):
    @classmethod
    def today(cls):
        return date(2026, 1, 1)


SAVED_ON = "2026-01-01"


def _saved_long_ago(exp, monkeypatch, names):  # noqa: F811
    monkeypatch.setattr(notebook_check_save, "date", _FirstOfJanuary)
    _saved(exp)
    monkeypatch.setattr(notebook_check_save, "date", date)
    write_channels(exp[0] / "ctrl/rep1/a.tif", names, KINDS, seed=1)
    return _open(exp)


def _dates(nb):
    return {label: r.confirmed_on for label, r in nb.draft_answers.repeats.items()}


def test_257_a_rename_resolved_by_submit_keeps_the_dates_saves_and_runs(exp, monkeypatch):  # noqa: F811
    nb = _saved_long_ago(exp, monkeypatch, ["Hoechst", "Actin", "Ch3"])
    assert submit(nb)
    assert set(nb.draft_answers.channels["DNA"].header_names) == {"DAPI", "Hoechst"}
    assert _dates(nb) == {"rep1": SAVED_ON, "rep2": SAVED_ON}  # not reopened: kept
    finish(nb)
    assert nb.save_experiment(), _text(nb.save_status.value)
    assert nb.run().written


def test_257_a_swap_is_refused_on_submit_and_released_by_its_own_row(exp, monkeypatch):  # noqa: F811
    nb = _saved_long_ago(exp, monkeypatch, ["Actin", "DAPI", "Ch3"])
    assert submit(nb)  # the saved aliases say ACTIN, DNA at C0, C1: contradicts the row
    rows = nb.channel_grid.by_repeat
    assert set(rows.listed()) == {KEY}
    assert nb.draft_answers.units[KEY].channels == tuple(TAGS)  # never reordered
    finish(nb)
    assert nb.save_button.disabled or not nb.save_experiment()
    unit_rows = {("unit", k): list(u.channels) for k, u in nb.draft_answers.units.items()}
    unit_rows[("unit", KEY)] = ["ACTIN", "DNA", "PHASE"]
    assert submit(nb, "unit", unit_rows)  # a row of its own that fits its header
    assert rows.listed() == {}
    assert nb.draft_answers.units[KEY].channels == ("ACTIN", "DNA", "PHASE")
    assert None in _dates(nb).values()  # its repeat's list changed: asked again
    assert ui.is_visible(nb.moves_box) and not nb.moves_confirm.value  # the move, unticked
