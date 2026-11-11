"""Issue #281: a lost header name is confirmed by one explicit click on Channels.

A confirmed channel that loses a saved header name is a changed entity (experiment-v1 4.6 (c),
``R_CONFIRMED_ID_CHANGED``). Owner decision (11 November 2026): the Channels step lists it and
asks "Yes, this header name changed"; only that click records it, as a move is recorded since
#278 (one ``reconfirmation``: the channel ID, ``new_id`` null, the date of the click, both
SHA-256). Nothing is ticked, the click covers exactly the change shown, and every blocked Save
says why beside the button. The dead end of #281 (a later re-tag refused for good after a removal
kept the removed folder's names) is resolved by that click.
"""

import json
import shutil
from datetime import date

import pytest

from channel_images import write_channels
from morphobricks_preprocessing.description_file import RECORDS_FILENAME, sha256_hex
from test_notebook_check_save import FOLDERS, KINDS, answer_all, exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from test_notebook_axes_quick import stepper  # noqa: E402
from test_notebook_prerelease import _tag, _text  # noqa: E402

GONE = "drug/rep2"


def _saved(exp, names=None):  # noqa: F811
    """Save the experiment; ``names`` rewrites folders' headers first (folder -> names)."""
    for folder, given in (names or {}).items():
        seed = FOLDERS.index(folder) + 1
        write_channels(exp[0] / folder / "a.tif", given, KINDS, seed=seed)
    first = stepper(*exp)
    first.quick_confirm.click()
    answer_all(first)
    assert first.save_experiment(), _text(first.save_status.value)
    return first


def _reopen(exp, gone=None):  # noqa: F811
    if gone is not None:
        shutil.rmtree(exp[0] / gone)
        shutil.rmtree(exp[1] / "Nuclei" / gone)
    nb = stepper(*exp)
    nb.quick_confirm.click()
    return nb


def _remove(nb):
    answer_all(nb)
    (row,) = nb.removals_box.children
    row.children[1].click()  # Yes, remove


def _records(exp):  # noqa: F811
    path = exp[2] / RECORDS_FILENAME
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def _record(channel, day, old_sha, new_sha):
    return {
        "confirmed_on": day,
        "id_kind": "channels",
        "new_id": None,
        "new_sha256": new_sha,
        "old_id": channel,
        "old_sha256": old_sha,
        "type": "reconfirmation",
    }


def test_the_dead_end_of_281_is_resolved_by_the_click(exp):  # noqa: F811
    # 1. ctrl/rep1 alone names DNA "Hoechst"; saved. 2. drug/rep2 is removed and saved without
    # a re-tag: "Hoechst" stays in the file. 3. Later, ctrl/rep1 says "DAPI" and is re-tagged:
    # on main every save was refused for good ("undo that change"), with no way out.
    _saved(exp, {"ctrl/rep1": ["Hoechst", "Actin", "Ch3"]})
    write_channels(exp[0] / "ctrl/rep1/a.tif", ["DAPI", "Actin", "Ch3"], KINDS, seed=1)
    nb = _reopen(exp, GONE)
    _remove(nb)
    assert nb.save_experiment(), _text(nb.save_status.value)
    assert nb._published().description.channels["DNA"].header_names == ("DAPI", "Hoechst")
    before = _records(exp)
    later = _reopen(exp)
    answer_all(later)
    _tag(later, 0, "DNA")
    assert later.draft_answers.channels["DNA"].header_names == ("DAPI",)
    # Shown on Channels, nothing ticked; Save disabled with the reason beside it.
    change = "DNA: header names “DAPI”, “Hoechst” → “DAPI”"
    assert ui.is_visible(later.names_box) and not later.names_confirm.value
    assert change in _text(later.names_list.value)
    assert "1 saved channel lost a header name" in _text(later.names_list.value)
    assert later.save_button.disabled and not later.save_experiment()
    assert "a header name that changed" in _text(later.checks.value)  # the way out is named
    assert f"{change} is not confirmed: see Channels" in _text(later.save_blocker.value)
    assert _records(exp) == before
    published = exp[2] / "morphobricks.toml"
    old_sha = sha256_hex(published.read_bytes())
    later.names_confirm.button.click()  # Yes, this header name changed
    today = date.today().isoformat()
    assert f"Confirmed on {today}" in later.names_confirm.message.value
    assert not later.save_button.disabled
    assert later.save_experiment(), _text(later.save_status.value)
    new_sha = sha256_hex(published.read_bytes())
    assert _records(exp) == [*before, _record("DNA", today, old_sha, new_sha)]
    assert later._published().description.channels["DNA"].header_names == ("DAPI",)
    assert not ui.is_visible(later.names_box)  # saved: nothing lost against the new file


def test_a_lost_name_alone_needs_the_click_undo_takes_it_back(exp):  # noqa: F811
    _saved(exp, {"ctrl/rep1": ["Hoechst", "Actin", "Ch3"]})
    write_channels(exp[0] / "ctrl/rep1/a.tif", ["Nuc", "Actin", "Ch3"], KINDS, seed=1)
    nb = _reopen(exp)
    answer_all(nb)
    _tag(nb, 0, "DNA")
    nb.names_confirm.button.click()
    assert not nb.save_button.disabled
    nb.names_confirm.button.click()  # Undo
    assert not nb.names_confirm.value and nb._names_clicked == {}
    assert nb.save_button.disabled and not nb.save_experiment()
    assert "is not confirmed: see Channels" in _text(nb.save_blocker.value)
    assert not (exp[2] / RECORDS_FILENAME).exists()


def test_a_click_covers_only_the_change_shown_when_it_was_clicked(exp):  # noqa: F811
    _saved(exp, {"ctrl/rep1": ["Hoechst", "Actin", "Ch3"]})
    write_channels(exp[0] / "ctrl/rep1/a.tif", ["Nuc", "Actin", "Ch3"], KINDS, seed=1)
    nb = _reopen(exp)
    answer_all(nb)
    _tag(nb, 0, "DNA")
    assert "DNA: header names “DAPI”, “Hoechst” → “DAPI”, “Nuc”" in _text(nb.names_list.value)
    nb.names_confirm.button.click()
    assert not nb.save_button.disabled
    # Another tagging after the click (DNA and PHASE swapped in ctrl/rep1) gives DNA other
    # names: another change, asked again, and Save waits for it.
    key = ("ctrl", "rep1", "single")
    for index in (0, 2):
        nb.channel_grid.cells[(key, index)].value = None
    nb.channel_grid.cells[(key, 0)].value = "PHASE"
    nb.channel_grid.cells[(key, 2)].value = "DNA"
    shown = _text(nb.names_list.value)
    assert "DNA: header names “DAPI”, “Hoechst” → “Ch3”, “DAPI”" in shown
    assert not nb.names_confirm.value
    assert nb.save_button.disabled and not nb.save_experiment()
    assert not (exp[2] / RECORDS_FILENAME).exists()


def test_folders_that_never_had_names_are_told_what_changed(exp):  # noqa: F811
    # #280 review, question 2: drug/rep2 alone gave names; the other folders' headers never
    # stated any. After its removal any tag action drops those names. Main said "DNA lost a
    # saved header name in ctrl/rep1", a folder that never had one, and refused for good.
    _saved(exp, {f: None for f in FOLDERS if f != GONE})
    nb = _reopen(exp, GONE)
    assert nb._published().description.channels["DNA"].header_names == ("DAPI",)
    _tag(nb, 0, "DNA")
    _remove(nb)
    shown = _text(nb.names_list.value)
    assert "3 saved channels lost a header name" in shown
    for line in ("ACTIN: header name “Actin” → none", "DNA: header name “DAPI” → none"):
        assert line in shown
    blocker = _text(nb.save_blocker.value)
    assert "DNA: header name “DAPI” → none is not confirmed: see Channels" in blocker
    assert "lost a saved header name in" not in blocker
    assert nb.save_button.disabled and not nb.save_experiment()
    nb.names_confirm.button.click()
    assert nb.save_experiment(), _text(nb.save_status.value)
    kept = [r["old_id"] for r in _records(exp) if r.get("id_kind") == "channels"]
    assert sorted(kept) == ["ACTIN", "DNA", "PHASE"]  # one line per ID, removal and click alike
    assert nb._published().description.channels["DNA"].header_names is None


def test_no_question_when_no_saved_header_name_is_lost(exp):  # noqa: F811
    _saved(exp)
    nb = _reopen(exp)
    answer_all(nb)
    _tag(nb, 0, "DNA")
    assert not ui.is_visible(nb.names_box) and nb.names_list.value == ""
    assert not nb.save_button.disabled or nb._saved_current()


def test_the_line_beside_save_names_the_changes_waiting_while_an_answer_is_missing(exp):  # noqa: F811
    # Re-exported with DNA and ACTIN swapped and DNA renamed: rep1's "same way?" is reopened
    # (an answer missing) and both Channels questions wait. The line beside Save names them
    # all, not only the missing answer, so nothing waiting is found only after answering.
    _saved(exp, {"ctrl/rep1": ["Hoechst", "Actin", "Ch3"]})
    write_channels(exp[0] / "ctrl/rep1/a.tif", ["Actin", "Nuc", "Ch3"], KINDS, seed=1)
    nb = _reopen(exp)
    key = ("ctrl", "rep1", "single")
    for index in (0, 1):
        nb.channel_grid.cells[(key, index)].value = None
    nb.channel_grid.cells[(key, 0)].value = "ACTIN"
    nb.channel_grid.cells[(key, 1)].value = "DNA"
    assert ui.is_visible(nb.moves_box) and ui.is_visible(nb.names_box)
    blocker = _text(nb.save_blocker.value)
    assert nb.save_button.disabled
    assert "1 answer still missing (listed above the button)" in blocker
    assert "the moved channels are not confirmed: see Channels" in blocker
    change = "DNA: header names “DAPI”, “Hoechst” → “DAPI”, “Nuc”"
    assert f"{change} is not confirmed: see Channels" in blocker
