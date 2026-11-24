"""An ID changed together with a folder change of the same entity (issue #475).

The transition checks pair an old and a new ID only through unchanged folders, so an ID edit
made in the same save as a folder change of that entity is never paired. It is refused before
Save (``folder_and_id_changed``) and by publication with a code, never a bare ``ValueError``.
The fixtures are the Spec's (see ``test_spec_reconcile``).
"""

from dataclasses import replace

import pytest

from morphobricks_preprocessing import description_candidate
from morphobricks_preprocessing.description_file import RECORDS_FILENAME
from morphobricks_preprocessing.description_reconcile import FolderRename, confirm_repeat, reconcile
from morphobricks_preprocessing.description_removal import candidate, reconfirmations
from morphobricks_preprocessing.description_transition import Reconfirmation, transition_issues
from morphobricks_preprocessing.run.description_file import publish_description
from test_spec_reconcile import (
    DAY,
    model,
    on_disk_as_ctl,
    published_in,
    renamed_member_folder,
    with_channel_id,
)


def with_repeat_id(description, old, new):
    """Return ``description`` with the repeat ID ``old`` changed to ``new`` (same members)."""
    repeats = {new if k == old else k: v for k, v in description.repeats.items()}
    used = {
        **description.used_ids,
        "repeats": tuple(sorted({*description.used_ids["repeats"], new})),
    }
    return replace(description, repeats=repeats, used_ids=used)


def candidate_check(monkeypatch, edited, published, confirmations):
    """Return the pre-save check of ``edited`` (the physical checks are not this test's)."""
    monkeypatch.setattr(description_candidate, "check_description", lambda found, *_: (found, []))
    return description_candidate.check_candidate(edited, None, None, published, confirmations)


def test_a_repeat_id_changed_with_a_member_folder_rename_is_refused_before_save(
    tmp_path, monkeypatch
):
    # Issue #475: the rename statement DrugA/experiment_23 -> experiment_23b and the repeat ID
    # Day1 -> DayX in one candidate. The transition checks cannot pair Day1 with DayX (its
    # member folders changed too) and the answers accept everything, so Save looked ready and
    # publication raised a bare ValueError about a removal the user never made.
    published = model("v03")
    statement = FolderRename("member", "experiment_23", "experiment_23b", DAY, condition="DrugA")
    rec = reconcile(published, renamed_member_folder(published), [statement])
    edited = with_repeat_id(confirm_repeat(candidate(published, rec), "Day1", DAY), "Day1", "DayX")
    assert "repeats" not in {i.kind for i in transition_issues(published, edited)}
    found = reconfirmations(published, edited, rec)
    assert found.open == ()  # every answer is given: only the new check stops it
    check = candidate_check(monkeypatch, edited, published, found.accepted)
    assert not check.can_save
    first = check.refusals[0]
    assert first.code == "folder_and_id_changed"
    assert "the folder and the ID of repeat Day1" in first.message
    assert "Day1 -> DayX" in first.message
    assert "CTRL/rep1/single is now under DayX" in first.message
    assert "DrugA/experiment_23/single" in first.message
    assert first.action == "Undo the ID edit: put Day1 back."
    output, base = published_in(tmp_path, published)
    before = (output / "morphobricks.toml").read_bytes()
    with pytest.raises(ValueError, match="folder_and_id_changed") as refused:
        publish_description(output, edited, base_sha256=base, confirmations=found.accepted)
    assert refused.value.codes == {"folder_and_id_changed"}  # coded, never a bare ValueError
    assert (output / "morphobricks.toml").read_bytes() == before
    assert not (output / RECORDS_FILENAME).exists()
    # The folder rename alone publishes. Only the publication API can then change the ID, with a
    # Reconfirmation naming the new ID (the notebook never builds one, #402).
    draft = confirm_repeat(candidate(published, rec), "Day1", DAY)
    accepted = reconfirmations(published, draft, rec).accepted
    assert candidate_check(monkeypatch, draft, published, accepted).refusals == ()
    renamed = publish_description(output, draft, base_sha256=base, confirmations=accepted)
    second = with_repeat_id(draft, "Day1", "DayX")
    change = ("repeats", "Day1", "DayX")
    assert [i.change for i in transition_issues(draft, second)] == [change]
    own = (Reconfirmation(*change, DAY),)
    assert candidate_check(monkeypatch, second, draft, own).refusals == ()
    publish_description(output, second, base_sha256=renamed.sha256, confirmations=own)


def test_a_condition_id_changed_with_its_folder_rename_names_the_condition_first(monkeypatch):
    # Issue #475: ctrl -> ctlX together with the stated rename control -> ctl. Main refused it
    # only through channel and repeat lines ("lost a saved position"); the first refusal now
    # names the condition and says how to proceed.
    published = model("v33")
    statement = FolderRename("condition", "control", "ctl", DAY)
    rec = reconcile(published, on_disk_as_ctl(published), [statement])
    draft = confirm_repeat(confirm_repeat(candidate(published, rec), "rep1", DAY), "rep2", DAY)
    conditions = {("ctlX" if k == "ctrl" else k): v for k, v in draft.conditions.items()}
    conditions["DrugA"] = replace(conditions["DrugA"], reference="ctlX")
    repeats = {
        rid: replace(r, members={("ctlX" if k == "ctrl" else k): m for k, m in r.members.items()})
        for rid, r in draft.repeats.items()
    }
    used = {**draft.used_ids, "conditions": tuple(sorted({*draft.used_ids["conditions"], "ctlX"}))}
    edited = replace(draft, conditions=conditions, repeats=repeats, used_ids=used)
    found = reconfirmations(published, edited, rec)
    check = candidate_check(monkeypatch, edited, published, found.accepted)
    assert not check.can_save
    first = check.refusals[0]
    assert first.code == "folder_and_id_changed"
    assert "the folder and the ID of condition ctrl" in first.message
    assert "control/rep1/single, control/rep2/single" in first.message
    assert "put ctrl back" in first.action
    # The pure folder rename keeps its check: nothing is refused.
    accepted = reconfirmations(published, draft, rec).accepted
    assert candidate_check(monkeypatch, draft, published, accepted).refusals == ()


def test_a_confirmed_id_change_says_old_to_new_and_to_undo_the_id_edit(monkeypatch):
    published = model("v33")
    edited = with_channel_id(published, "DNA", "DAPI")
    check = candidate_check(monkeypatch, edited, published, ())
    (refusal,) = check.refusals
    assert refusal.code == "R_CONFIRMED_ID_CHANGED"
    assert "DNA -> DAPI" in refusal.message
    assert refusal.action.startswith("Undo the ID edit: put DNA back.")
