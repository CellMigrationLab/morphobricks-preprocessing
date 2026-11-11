"""Issue #258: a channel re-confirmation after re-ordering a unit's channels (Channels step).

The images of a saved experiment are re-exported with their channels in another order and the
user re-tags that unit to match. Section 10 reopens that replicate's "same way?"; section 4.6 (c)
refuses the moved channel IDs (``R_CONFIRMED_ID_CHANGED``) unless a re-confirmation is recorded.
The Channels step lists each moved channel ID and asks once; only that click records it, one
``reconfirmation`` line per channel (old ID, ``new_id`` null, the date of the click). Without the
click Save stays disabled; with it, the reopened replicate takes today's date and every other one
keeps its date byte for byte.
"""

import json
import re
from dataclasses import replace
from datetime import date

import pytest

from morphobricks_preprocessing.channel_moves import ChannelMove, Moved, channel_moves
from morphobricks_preprocessing.description_file import RECORDS_FILENAME, sha256_hex
from test_notebook_check_save import answer_all, exp  # noqa: F401
from test_notebook_prerelease import (
    KEY,
    SAVED_ON,
    UNIT,
    _dates,
    _published_dates,
    _repeat_of,
    _saved_long_ago,
)

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402


def _retag_swapped(exp, monkeypatch, gone=None):  # noqa: F811
    """Save on 2026-01-01, swap ctrl/rep1's first two header channels, re-tag them to match.

    ``gone`` is a folder deleted (images and masks) after the save, before reopening.
    """
    nb = _saved_long_ago(exp, monkeypatch, ["Actin", "DAPI", "Ch3"], gone)
    for index in (0, 1):
        nb.channel_grid.cells[(KEY, index)].value = None
    nb.channel_grid.cells[(KEY, 0)].value = "ACTIN"
    nb.channel_grid.cells[(KEY, 1)].value = "DNA"
    assert nb.draft_answers.units[KEY].channels == ("ACTIN", "DNA", "PHASE")
    return nb


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_a_re_order_confirmed_on_channels_saves_with_today_for_that_replicate_only(
    exp,  # noqa: F811
    monkeypatch,
):
    nb = _retag_swapped(exp, monkeypatch)
    rid = _repeat_of(nb, UNIT)
    other = next(label for label in _dates(nb) if label != rid)
    # Listed on Channels at once, before "same way?" is answered again; nothing ticked.
    assert ui.is_visible(nb.moves_box) and not nb.moves_confirm.value
    shown = _text(nb.moves_list.value)
    assert "2 saved channels moved" in shown
    assert "ACTIN: ctrl/rep1 channel 2 → channel 1" in shown
    assert "DNA: ctrl/rep1 channel 1 → channel 2" in shown
    nb.same_way[rid].button.click()
    today = date.today().isoformat()
    # Without the click: Save disabled, the reason named, the way out said.
    assert {i.code for i in nb._check.refusals} == {"R_CONFIRMED_ID_CHANGED"}
    assert nb.save_button.disabled and not nb.save_experiment()
    assert "a channel that moved is confirmed on Channels" in _text(nb.checks.value)
    assert _published_dates(exp) == [SAVED_ON, SAVED_ON]
    published = exp[2] / "morphobricks.toml"
    old_sha = sha256_hex(published.read_bytes())
    # The explicit click records it, and publication accepts the candidate.
    nb.moves_confirm.button.click()
    assert nb.moves_confirm.value and f"Confirmed on {today}" in nb.moves_confirm.message.value
    assert not nb._check.refusals and not nb.save_button.disabled
    assert nb.save_experiment(), _text(nb.save_status.value)
    text = published.read_text(encoding="utf-8")
    assert re.search(rf'\[repeats\.{rid}\]\nconfirmed = true\nconfirmed_on = "{today}"', text)
    assert re.search(rf'\[repeats\.{other}\]\nconfirmed = true\nconfirmed_on = "{SAVED_ON}"', text)
    new_sha = sha256_hex(published.read_bytes())
    lines = (exp[2] / RECORDS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [
        {
            "confirmed_on": today,
            "id_kind": "channels",
            "new_id": None,
            "new_sha256": new_sha,
            "old_id": channel,
            "old_sha256": old_sha,
            "type": "reconfirmation",
        }
        for channel in ("ACTIN", "DNA")
    ]
    assert not ui.is_visible(nb.moves_box)  # saved: nothing moved against the new file


def test_without_the_click_nothing_is_recorded_and_undo_takes_it_back(exp, monkeypatch):  # noqa: F811
    nb = _retag_swapped(exp, monkeypatch)
    nb.same_way[_repeat_of(nb, UNIT)].button.click()
    nb.moves_confirm.button.click()
    assert not nb.save_button.disabled
    nb.moves_confirm.button.click()  # Undo
    assert not nb.moves_confirm.value and nb._moves_clicked == {}
    assert nb.save_button.disabled and not nb.save_experiment()
    assert not (exp[2] / RECORDS_FILENAME).exists()


def test_a_confirmation_covers_only_the_moves_shown_when_it_was_clicked(exp, monkeypatch):  # noqa: F811
    nb = _retag_swapped(exp, monkeypatch)
    nb.same_way[_repeat_of(nb, UNIT)].button.click()
    nb.moves_confirm.button.click()
    assert not nb.save_button.disabled
    # Another order after the click is another change: asked again, Save disabled meanwhile.
    for index in (1, 2):
        nb.channel_grid.cells[(KEY, index)].value = None
    nb.channel_grid.cells[(KEY, 1)].value = "PHASE"
    nb.channel_grid.cells[(KEY, 2)].value = "DNA"
    assert nb.draft_answers.units[KEY].channels == ("ACTIN", "PHASE", "DNA")
    assert ui.is_visible(nb.moves_box) and not nb.moves_confirm.value
    assert "3 saved channels moved" in _text(nb.moves_list.value)
    assert nb.save_button.disabled and not nb.save_experiment()
    rid = _repeat_of(nb, UNIT)
    if not nb.same_way[rid].value:  # the replicate's layout changed again: asked again
        nb.same_way[rid].button.click()
    assert {i.code for i in nb._check.refusals} == {"R_CONFIRMED_ID_CHANGED"}
    assert nb.save_button.disabled
    nb.moves_confirm.button.click()
    assert not nb.save_button.disabled


def test_a_removal_elsewhere_does_not_confirm_a_move(exp, monkeypatch):  # noqa: F811
    # Yes, remove of drug/rep2 records a re-confirmation of every channel ID it held (4.6 keys
    # it by ID). The move in ctrl/rep1 is still shown and still needs its own click.
    nb = _retag_swapped(exp, monkeypatch, gone="drug/rep2")
    answer_all(nb)
    (row,) = nb.removals_box.children
    row.children[1].click()
    assert ui.is_visible(nb.moves_box) and not nb.moves_confirm.value
    assert "ACTIN: ctrl/rep1 channel 2 → channel 1" in _text(nb.moves_list.value)
    assert nb.save_button.disabled and not nb.save_experiment()
    assert "the moved channels are not confirmed: see Channels" in nb.save_blocker.value
    assert not (exp[2] / RECORDS_FILENAME).exists()
    nb.moves_confirm.button.click()
    assert nb.save_experiment(), _text(nb.save_status.value)
    lines = [json.loads(x) for x in (exp[2] / RECORDS_FILENAME).read_text().splitlines()]
    kept = [(r["id_kind"], r["old_id"]) for r in lines if r["type"] == "reconfirmation"]
    assert len(kept) == len(set(kept))  # one line per ID, the removal's and the move's alike


def test_no_question_when_no_saved_channel_moved(exp, monkeypatch):  # noqa: F811
    nb = _saved_long_ago(exp, monkeypatch, ["DAPI", "Actin", "Ch3"])
    assert not ui.is_visible(nb.moves_box) and nb.moves_list.value == ""
    assert nb._moves_shown == ()


# --- the pure list ----------------------------------------------------------------------------


def _published_and_swapped(exp, monkeypatch):  # noqa: F811
    nb = _retag_swapped(exp, monkeypatch)
    nb.same_way[_repeat_of(nb, UNIT)].button.click()
    from morphobricks_preprocessing.draft_answers import description_from_draft

    candidate = description_from_draft(
        nb._draft_base, nb.draft_answers, channel_counts=nb._channel_counts()
    )
    return nb._published().description, candidate


def test_channel_moves_lists_each_moved_position_from_and_to(exp, monkeypatch):  # noqa: F811
    published, candidate = _published_and_swapped(exp, monkeypatch)
    unit = next(
        (c, m.source_repeat, t)
        for r in candidate.repeats.values()
        for c, m in r.members.items()
        for t in m.technical_repeats
        if (candidate.conditions[c].source_folder, m.source_repeat) == ("ctrl", "rep1")
    )
    assert channel_moves(published, candidate) == (
        ChannelMove("ACTIN", (Moved(unit, 1, 0),)),
        ChannelMove("DNA", (Moved(unit, 0, 1),)),
    )


def test_a_lost_header_name_is_not_offered_as_a_move(exp, monkeypatch):  # noqa: F811
    published, candidate = _published_and_swapped(exp, monkeypatch)
    dna = candidate.channels["DNA"]
    names = tuple(n for n in dna.header_names if n != "DAPI")
    channels = {**candidate.channels, "DNA": replace(dna, header_names=names)}
    lost = replace(candidate, channels=channels)
    assert [m.channel for m in channel_moves(published, lost)] == ["ACTIN"]
