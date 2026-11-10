"""Final review of v0.5.0: what is confirmed is what the user answered, for the folders shown.

- A header refusal is never lifted by an answer about another cell. A tag confirms the header
  name of the cell the user tagged; a tag saved while the cell's header had another name
  confirms nothing about the name it has now. Before the fix, tagging any channel of any folder
  recomputed every label's header names from every saved tag, so ``R_HEADER_CONTRADICTION`` of
  folders the user never looked at again vanished, the save said "the images agree" and the run
  wrote the table.
- A "same way?" answered in a draft (nothing published yet) is the answer for the folders and
  the layout it was given for. Before the fix it was kept when a folder joined or left the
  replicate before the next check, and when a tag or an image type changed after it, and it
  was published as the user's confirmation.
"""

import re
import shutil

import pytest

from channel_images import write_channels
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from test_notebook_check_save import (  # noqa: F401
    FOLDERS,
    KINDS,
    add_folder,
    answer_all,
    exp,
    opened,
    saved_nb,
    tag,
)

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

CTRL, DRUG = ("ctrl", "rep1", "single"), ("drug", "rep1", "single")


def _text(html):
    return re.sub(r"<[^>]+>", " ", html)


def _dates(nb):
    return {label: r.confirmed_on for label, r in nb.draft_answers.repeats.items()}


def _retag(nb, key, index, label):
    """Tag one channel again by hand (through no tag, as a re-tag passes)."""
    nb.channel_grid.cells[(key, index)].value = None
    nb.channel_grid.cells[(key, index)].value = label


# --- a header refusal is lifted only by tagging the cell it names ----------------------------


def _saved_then_drug_renamed(folders):
    """Save the experiment, then rewrite the drug folders with channel 1 named Hoechst."""
    saved_nb(folders)
    for seed, folder in enumerate(FOLDERS[2:]):
        write_channels(folders[0] / folder / "a.tif", ["Hoechst", "Actin", "Ch3"], KINDS, seed=seed)
    nb = opened(*folders)
    nb._refresh_check_save()
    assert "R_HEADER_CONTRADICTION" in _text(nb.checks.value)
    assert nb.draft_answers.channels["DNA"].header_names == ("DAPI",)
    return nb


def test_a_header_refusal_is_not_lifted_by_tagging_another_folder(exp):  # noqa: F811
    nb = _saved_then_drug_renamed(exp)
    _retag(nb, CTRL, 2, "PHASE")  # a folder whose images did not change
    nb._refresh_check_save()
    assert nb.draft_answers.channels["DNA"].header_names == ("DAPI",)  # Hoechst: not confirmed
    shown = _text(nb.checks.value)
    assert "The images contradict it: the run would stop" in shown
    assert "R_HEADER_CONTRADICTION" in shown and "&#x27;Hoechst&#x27;" in shown
    assert not nb._check.can_run and not nb._run_allowed() and nb.run() is None


def test_a_header_refusal_is_not_lifted_by_tagging_another_channel_of_the_folder(exp):  # noqa: F811
    nb = _saved_then_drug_renamed(exp)
    _retag(nb, DRUG, 2, "PHASE")  # the changed folders, but not the channel renamed
    nb._refresh_check_save()
    assert nb.draft_answers.channels["DNA"].header_names == ("DAPI",)
    assert "R_HEADER_CONTRADICTION" in _text(nb.checks.value) and not nb._check.can_run


def test_tagging_the_renamed_channel_again_confirms_its_name_and_lifts_the_refusal(exp):  # noqa: F811
    nb = _saved_then_drug_renamed(exp)
    _retag(nb, DRUG, 0, "DNA")  # the user looks at channel 1 of the drug folders: still DNA
    nb._refresh_check_save()
    assert set(nb.draft_answers.channels["DNA"].header_names) == {"DAPI", "Hoechst"}
    assert nb._check.can_run and not nb._check.refusals
    assert nb.save_experiment(), _text(nb.save_status.value)
    assert nb.run().written


def test_swapped_channels_stay_refused_until_their_own_cells_are_tagged(exp):  # noqa: F811
    # A draft (nothing published): the drug images are rewritten with the two channels swapped.
    root, masks, out = exp
    first = opened(*exp)
    answer_all(first)
    for seed, folder in enumerate(FOLDERS[2:]):
        swapped = ["filaments", "blobs", "cells"]
        write_channels(root / folder / "a.tif", ["Actin", "DAPI", "Ch3"], swapped, seed=seed)
    nb = opened(*exp)
    _retag(nb, CTRL, 2, "PHASE")
    nb._refresh_check_save()
    assert nb.draft_answers.channels["DNA"].header_names == ("DAPI",)
    assert nb.draft_answers.channels["ACTIN"].header_names == ("Actin",)
    assert "R_HEADER_CONTRADICTION" in _text(nb.checks.value) and not nb._check.can_run
    for index in (0, 1):
        nb.channel_grid.cells[(DRUG, index)].value = None
    nb.channel_grid.cells[(DRUG, 0)].value = "ACTIN"
    nb.channel_grid.cells[(DRUG, 1)].value = "DNA"
    nb._refresh_check_save()
    assert nb.draft_answers.channels["DNA"].header_names == ("DAPI",)
    assert "R_HEADER_CONTRADICTION" not in _text(nb.checks.value)


# --- "same way?" in a draft is the answer for the folders and layout it was given for --------


def _joined(nb, out):
    """Answer what the new folder needs; the replicate it joined must be asked again."""
    assert _dates(nb) == {"rep1": None, "rep2": _dates(nb)["rep2"]} and _dates(nb)["rep2"]
    assert "Asked again on Check and save" in nb.names_status.value
    tag(nb)
    nb.reference_boxes["drugB"].value = "ctrl"
    nb._refresh_check_save()
    assert not nb.same_way["rep1"].value and nb.same_way["rep2"].value
    assert "replicate rep1: answer" in _text(nb.todo.value)
    assert nb.save_button.disabled and not nb.save_experiment()
    assert not (out / DESCRIPTION_FILENAME).exists()
    nb.same_way["rep1"].button.click()  # the user's answer, for the three folders
    assert nb.save_experiment(), _text(nb.save_status.value)
    assert "[repeats.rep1.members.drugB]" in (out / DESCRIPTION_FILENAME).read_text("utf-8")


def test_a_folder_that_joined_a_replicate_reopens_its_same_way_in_a_resumed_draft(exp):  # noqa: F811
    root, masks, out = exp
    answer_all(opened(*exp))  # every answer in the draft, nothing published
    add_folder(root, masks, "drugB/rep1", 9)
    _joined(opened(*exp), out)  # a new notebook


def test_a_folder_that_joined_a_replicate_reopens_its_same_way_on_a_new_check(exp):  # noqa: F811
    root, masks, out = exp
    nb = opened(*exp)
    answer_all(nb)
    add_folder(root, masks, "drugB/rep1", 9)
    nb.check_button.click()  # the same notebook
    nb.preview_checked.set(True)
    nb.show_preview()
    nb.confirm_button.click()
    _joined(nb, out)


def test_a_folder_that_left_a_replicate_reopens_its_same_way_in_a_resumed_draft(exp):  # noqa: F811
    root, masks, out = exp
    answer_all(opened(*exp))
    shutil.rmtree(root / "drug" / "rep1")
    nb = opened(*exp)
    assert _dates(nb)["rep1"] is None and _dates(nb)["rep2"]
    nb._refresh_check_save()
    assert nb.save_button.disabled and not nb.same_way["rep1"].value


def test_a_draft_resumed_on_unchanged_folders_keeps_every_same_way(exp):  # noqa: F811
    first = opened(*exp)
    answer_all(first)
    nb = opened(*exp)
    assert _dates(nb) == _dates(first) and all(_dates(nb).values())
    assert "Asked again" not in nb.names_status.value
    nb._refresh_check_save()
    assert not nb.save_button.disabled and nb.save_experiment()


def test_a_tag_changed_after_same_way_asks_it_again_in_a_draft(exp):  # noqa: F811
    root, masks, out = exp
    nb = opened(*exp)
    answer_all(nb)
    assert all(_dates(nb).values())
    for index in (0, 1):
        nb.channel_grid.cells[(CTRL, index)].value = None
    assert all(_dates(nb).values())  # an untagged channel on the way declares no list
    nb.channel_grid.cells[(CTRL, 0)].value = "DNA"
    nb.channel_grid.cells[(CTRL, 1)].value = "ACTIN"
    assert all(_dates(nb).values())  # tagged as it was answered for: nothing is asked again
    for index in (0, 1):
        nb.channel_grid.cells[(CTRL, index)].value = None
    nb.channel_grid.cells[(CTRL, 0)].value = "ACTIN"
    nb.channel_grid.cells[(CTRL, 1)].value = "DNA"
    assert _dates(nb) == {"rep1": None, "rep2": None}  # the four folders are one block
    nb._refresh_check_save()
    assert not any(toggle.value for toggle in nb.same_way.values())
    assert nb.save_button.disabled and not nb.save_experiment()
    assert not (out / DESCRIPTION_FILENAME).exists()


def test_an_image_type_changed_after_same_way_asks_it_again_in_a_draft(exp):  # noqa: F811
    nb = opened(*exp)
    answer_all(nb)
    nb.channel_types.radios[("drug", "rep2", "single")].value = "rgb"
    assert _dates(nb)["rep2"] is None and _dates(nb)["rep1"]
    nb._refresh_check_save()
    assert nb.save_button.disabled and not nb.same_way["rep2"].value
