"""A removal or rename answer re-confirms only the change it answers (issues #279 and #281).

Experiment-v1 4.6 keys a re-confirmation by ID (old ID, new ID, date), never by folder: the
"Yes, remove" of one folder records every channel ID that folder held, and that record would
accept any other change of the same ID, such as a saved header name lost in another folder that
is still there and that the user never saw. A lost header name is therefore never the removal's:
the Channels step shows it and only its own click ("Yes, this header name changed", owner
decision of 11 November 2026) lets it publish; until then the ID is refused
(``R_CONFIRMED_ID_CHANGED``) and the reason is said beside the Save button.
"""

import json
import shutil

import pytest

from channel_images import write_channels
from morphobricks_preprocessing.description_file import RECORDS_FILENAME
from test_notebook_check_save import KINDS, answer_all, exp  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from test_notebook_axes_quick import stepper  # noqa: E402
from test_notebook_prerelease import _tag, _text  # noqa: E402

GONE = "drug/rep2"
NO_NAMES = object()
"""The rewritten header states no channel name at all (not one empty name)."""


def _reopened(exp, unique, now):  # noqa: F811
    """Save with ``unique`` as ctrl/rep1's DNA header name, then rewrite it to ``now``; drop B."""
    write_channels(exp[0] / "ctrl/rep1/a.tif", [unique, "Actin", "Ch3"], KINDS, seed=1)
    first = stepper(*exp)
    first.quick_confirm.click()
    answer_all(first)
    assert first.save_experiment()
    assert first.draft_answers.channels["DNA"].header_names == ("DAPI", unique)
    names = None if now is NO_NAMES else [now, "Actin", "Ch3"]
    write_channels(exp[0] / "ctrl/rep1/a.tif", names, KINDS, seed=1)
    shutil.rmtree(exp[0] / GONE)
    shutil.rmtree(exp[1] / "Nuclei" / GONE)
    nb = stepper(*exp)
    nb.quick_confirm.click()
    return nb


def _remove(nb):
    answer_all(nb)
    (row,) = nb.removals_box.children
    row.children[1].click()  # Yes, remove (drug/rep2 holds DNA too)
    nb._refresh_check_save()


def _records(exp):  # noqa: F811
    path = exp[2] / RECORDS_FILENAME
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


NOW = {
    "Nuc": ("“DAPI”, “Nuc”", ("DAPI", "Nuc")),
    "DAPI": ("“DAPI”", ("DAPI",)),
    None: ("“DAPI”", ("DAPI",)),
    NO_NAMES: ("“DAPI”", ("DAPI",)),
}
"""The DNA header names after the re-tag: as shown on Channels, and as published."""


@pytest.mark.parametrize(
    "now",
    ["Nuc", "DAPI", None, NO_NAMES],
    ids=["other-name", "other-saved-name", "no-name", "no-header-names"],
)
def test_a_removal_does_not_accept_a_header_name_lost_in_a_folder_still_there(exp, now):  # noqa: F811
    # "DAPI" is another saved DNA name (#280 review, question 1): the published file keeps no
    # names per folder, so only the click on Channels can accept the loss of "Hoechst".
    nb = _reopened(exp, "Hoechst", now)
    before = len(_records(exp))
    _tag(nb, 0, "DNA")  # re-tag ctrl/rep1's channel 1: Hoechst is no longer a name of DNA
    assert "Hoechst" not in (nb.draft_answers.channels["DNA"].header_names or ())
    _remove(nb)
    shown, published = NOW[now]
    change = f"DNA: header names “DAPI”, “Hoechst” → {shown}"
    assert ui.is_visible(nb.names_box) and not nb.names_confirm.value  # nothing ticked
    assert change in _text(nb.names_list.value)
    assert nb.save_button.disabled and not nb.save_experiment()
    assert "R_CONFIRMED_ID_CHANGED at channels.DNA" in _text(nb.checks.value)
    assert f"{change} is not confirmed: see Channels" in _text(nb.save_blocker.value)
    assert len(_records(exp)) == before  # nothing published, nothing recorded
    nb.names_confirm.button.click()  # Yes, this header name changed
    assert nb.save_experiment(), _text(nb.save_status.value)
    kept = [r["old_id"] for r in _records(exp) if r["type"] == "reconfirmation"]
    assert kept.count("DNA") == 1  # one line per ID: the removal's and the click's alike
    assert nb._published().description.channels["DNA"].header_names == published


def test_without_the_tag_change_the_removal_saves(exp):  # noqa: F811
    # The way out the reason names: DNA keeps its saved names, so only the removal changes it
    # (the run then stops on the header itself, R_HEADER_CONTRADICTION, as without a removal).
    nb = _reopened(exp, "Hoechst", "Nuc")
    _remove(nb)
    assert not nb.save_button.disabled
    assert nb.save_experiment(), _text(nb.save_status.value)
    kept = {r["old_id"] for r in _records(exp) if r["type"] == "reconfirmation"}
    assert {"ACTIN", "DNA", "PHASE"} <= kept


def _removed_held_hoechst(exp):  # noqa: F811
    """Save with drug/rep2 alone naming DNA "Hoechst", then delete drug/rep2 and reopen."""
    write_channels(exp[0] / GONE / "a.tif", ["Hoechst", "Actin", "Ch3"], KINDS, seed=4)
    first = stepper(*exp)
    first.quick_confirm.click()
    answer_all(first)
    assert first.save_experiment()
    shutil.rmtree(exp[0] / GONE)
    shutil.rmtree(exp[1] / "Nuclei" / GONE)
    nb = stepper(*exp)
    nb.quick_confirm.click()
    return nb


def test_a_header_name_held_only_by_the_removed_folder_is_the_removals(exp):  # noqa: F811
    # The removal alone covers the name its folder held: nothing else is asked, DNA gets the
    # removal's one record and keeps "Hoechst" in the published file.
    nb = _removed_held_hoechst(exp)
    _remove(nb)
    assert not ui.is_visible(nb.names_box)
    assert not nb.save_button.disabled, _text(nb.save_blocker.value)
    assert nb.save_experiment(), _text(nb.save_status.value)
    kept = [r["old_id"] for r in _records(exp) if r["type"] == "reconfirmation"]
    assert kept.count("DNA") == 1
    assert nb._published().description.channels["DNA"].header_names == ("DAPI", "Hoechst")


def test_dropping_a_name_of_the_removed_folder_is_asked_as_any_other_loss(exp):  # noqa: F811
    # A re-tag that drops "Hoechst" cannot be told apart from a folder still there that no
    # longer gives it (the published file keeps no names per folder; the other-saved-name case
    # above): it is shown on Channels and needs its click, then DNA has one record.
    nb = _removed_held_hoechst(exp)
    _tag(nb, 0, "DNA")
    assert nb.draft_answers.channels["DNA"].header_names == ("DAPI",)
    _remove(nb)
    assert "DNA: header names “DAPI”, “Hoechst” → “DAPI”" in _text(nb.names_list.value)
    assert nb.save_button.disabled and not nb.save_experiment()
    nb.names_confirm.button.click()
    assert nb.save_experiment(), _text(nb.save_status.value)
    kept = [r["old_id"] for r in _records(exp) if r["type"] == "reconfirmation"]
    assert kept.count("DNA") == 1
