"""Removing a whole biological repeat or condition in the notebook (issue #283).

Spec experiment-v1 sections 4.6, 4.9 and 10: a removal the user confirms (**Yes, remove**)
publishes; the removed entity's ID stays in ``used_ids`` (the registry is append-only and an ID
is never reused) and the removal is written as ``removed`` records. Before the fix the draft
knew only the IDs the lineage had *already* retired, so the ID of a repeat or condition removed
now fell out of ``used_ids`` and every such save was refused with ``R_USED_ID_DROPPED``: the
user had no way forward. Without the user's confirmation nothing is published, as before.
"""

import importlib.util
import json
import shutil
import tomllib
from functools import cache

import pytest

from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME, RECORDS_FILENAME
from morphobricks_preprocessing.design import DESIGN_FILENAME
from morphobricks_preprocessing.draft import DraftError
from spec_oracle import refval
from spec_pin import spec_root

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from test_notebook_check_save import add_folder, exp, opened, saved_nb, tag  # noqa: E402, F401


@cache
def refrecords():
    """Return the Spec's records validator (vendored at the pin, loaded by path)."""
    path = spec_root() / "tools" / "audit" / "refrecords.py"
    spec = importlib.util.spec_from_file_location("spec_refrecords_283", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def answer_all(nb):
    """Answer every open question, whichever conditions are on disk (drug may be gone)."""
    tag(nb)
    question = nb.mask_card.questions.get("Nuclei")
    if question is not None and question.choice.value is None:
        question.choice.value = "instances"
    for folder, box in nb.reference_boxes.items():
        if nb._chosen_reference(folder) is None and box.value is None:
            box.value = "" if folder == "ctrl" else "ctrl"
    for toggle in list(nb.same_way.values()):
        if not toggle.value:
            toggle.button.click()


def used(out):
    return tomllib.loads((out / DESCRIPTION_FILENAME).read_text(encoding="utf-8"))["used_ids"]


def removed_entities(out):
    lines = [json.loads(x) for x in (out / RECORDS_FILENAME).read_text().splitlines()]
    return {
        (r["entity"], r["repeat_id"] or r["condition_id"])
        for r in lines
        if r["type"] == "removed" and r["entity"] != "unit"
    }


def check_with_the_spec(out, before):
    """The published file, its records and the transition pass the Spec's reference tools."""
    data = (out / DESCRIPTION_FILENAME).read_bytes()
    report, doc = refval().validate(data)
    assert report.errors == []
    codes = refval().transition(tomllib.loads(before.decode("utf-8")), doc)
    assert not codes & {"R_USED_ID_DROPPED", "R_USED_ID_REUSED"}
    records = refrecords().validate_records((out / RECORDS_FILENAME).read_bytes())
    assert records.errors == []


def remove_folder(root, masks, folder):
    shutil.rmtree(root / folder)
    shutil.rmtree(masks / "Nuclei" / folder)


def test_removing_every_member_of_a_repeat_publishes_and_keeps_its_id(exp):  # noqa: F811
    root, masks, out = exp
    saved_nb(exp)
    before = (out / DESCRIPTION_FILENAME).read_bytes()
    remove_folder(root, masks, "ctrl/rep2")
    remove_folder(root, masks, "drug/rep2")
    nb = opened(root, masks, out)
    answer_all(nb)
    rows = nb.removals_box.children
    assert len(rows) == 2  # one question per condition
    assert nb.save_button.disabled  # nothing goes without "Yes, remove"
    assert "R_USED_ID_DROPPED" not in nb.checks.value
    for row in rows:
        row.children[1].click()
    assert not nb.save_button.disabled, nb.checks.value
    assert nb.save_experiment(), nb.save_status.value
    assert "rep2" in used(out)["repeats"]  # retired, never reused
    assert ("repeat", "rep2") in removed_entities(out)
    assert ",rep2," not in (out / DESIGN_FILENAME).read_text(encoding="utf-8")
    check_with_the_spec(out, before)


def test_a_condition_renamed_on_disk_publishes_and_keeps_the_old_id(exp):  # noqa: F811
    # Comment of 24 Nov 2026: drug -> drugB on disk, the new folder keeps its own ID (drugB),
    # "Yes, remove" for drug. The ID drug is retired, never dropped.
    root, masks, out = exp
    saved_nb(exp)
    before = (out / DESCRIPTION_FILENAME).read_bytes()
    shutil.move(root / "drug", root / "drugB")
    shutil.move(masks / "Nuclei" / "drug", masks / "Nuclei" / "drugB")
    nb = opened(root, masks, out)
    answer_all(nb)  # drugB is compared with ctrl, like drug was
    (row,) = nb.removals_box.children
    assert "Every folder of drug is gone" in row.children[0].value
    assert nb.save_button.disabled
    row.children[1].click()
    assert not nb.save_button.disabled, nb.checks.value
    assert nb.save_experiment(), nb.save_status.value
    listed = used(out)["conditions"]
    assert "drug" in listed and "drugB" in listed
    assert ("condition", "drug") in removed_entities(out)
    check_with_the_spec(out, before)


def test_removing_a_whole_condition_publishes_and_keeps_its_id(exp):  # noqa: F811
    root, masks, out = exp
    saved_nb(exp)
    before = (out / DESCRIPTION_FILENAME).read_bytes()
    remove_folder(root, masks, "drug")
    nb = opened(root, masks, out)
    answer_all(nb)
    (row,) = nb.removals_box.children
    assert nb.save_button.disabled
    row.children[1].click()
    assert nb.save_experiment(), nb.save_status.value
    assert "drug" in used(out)["conditions"]
    assert ("condition", "drug") in removed_entities(out)
    check_with_the_spec(out, before)


def test_a_retired_repeat_id_is_not_proposed_or_taken_again(exp):  # noqa: F811
    root, masks, out = exp
    saved_nb(exp)
    remove_folder(root, masks, "ctrl/rep2")
    remove_folder(root, masks, "drug/rep2")
    nb = opened(root, masks, out)
    answer_all(nb)
    for row in nb.removals_box.children:
        row.children[1].click()
    assert nb.save_experiment(), nb.save_status.value
    # The folder comes back: it is a new repeat, and its old ID is never proposed for it.
    add_folder(root, masks, "ctrl/rep2", 7)
    again = opened(root, masks, out)
    assert "rep2" in again._draft_base.used_ids["repeats"]
    (slot,) = [r.id for label, r in again._draft_base.repeats.items() if label == "rep2"]
    assert slot.pending and slot.proposed != "rep2"
    with pytest.raises(DraftError) as refused:
        again._draft_base.confirm_id("repeats", "rep2", "rep2")
    assert "R_USED_ID_REUSED" in refused.value.codes
