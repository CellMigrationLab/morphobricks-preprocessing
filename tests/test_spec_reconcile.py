"""Reconciliation of the published description with the folder: conformance P07, P08, P15, P33.

Experiment-v1 section 10. The folder is a plain mapping of source folder labels to what was
seen there (:class:`ObservedUnit`); the fixtures are the Spec's, and publication is the real
``publish_description`` into a temporary output folder.
"""

from dataclasses import replace

import pytest

from morphobricks_preprocessing.description import Unit, description_from_bytes
from morphobricks_preprocessing.description_file import RECORDS_FILENAME
from morphobricks_preprocessing.description_reconcile import (
    CHANNELS_CHANGED,
    NEW_UNIT,
    REMOVED_UNIT,
    RENAMED_FOLDER,
    FolderRename,
    ObservedUnit,
    RenameRefused,
    Status,
    UnitFolders,
    confirm_repeat,
    layout_changed,
    published_units,
    reconcile,
)
from morphobricks_preprocessing.description_removal import (
    Removal,
    candidate,
    reconfirmations,
    removal_questions,
)
from morphobricks_preprocessing.description_rules import Refusal
from morphobricks_preprocessing.description_transition import transition_issues
from morphobricks_preprocessing.description_write import canonical_bytes
from morphobricks_preprocessing.run.description_file import (
    ABSENT,
    PublicationRefused,
    publish_description,
    read_records,
)
from spec_pin import fixtures

BY_NAME = {item.name.split("_")[0]: item for item in fixtures("valid")}
DAY = "2026-11-09"


def model(name):
    return description_from_bytes(BY_NAME[name].data)


def folder(description, changes=None):
    """The folder as the description says it is (every unit with its published count)."""
    seen = {}
    for repeat in description.repeats.values():
        for condition_id, member in repeat.members.items():
            for unit in member.technical_repeats.values():
                key = UnitFolders(
                    description.conditions[condition_id].source_folder,
                    member.source_repeat,
                    unit.source_folder,
                )
                share = member.image_count // len(member.technical_repeats)
                seen[key] = ObservedUnit(share)
    seen.update(changes or {})
    return seen


def published_in(tmp_path, description):
    """Publish the description as the first file and return the output folder and its base."""
    return tmp_path, publish_description(tmp_path, description, base_sha256=ABSENT).sha256


def key(condition, repeat, technical="single"):
    return UnitFolders(condition, repeat, technical)


# P15 ------------------------------------------------------------------------------------


def test_p15_new_fovs_in_an_existing_unit_keep_the_confirmation_and_its_date(tmp_path):
    published = model("v13")
    unit = key("DrugA", "rep2")
    rec = reconcile(published, folder(published, {unit: ObservedUnit(15)}))
    assert rec.reopened == {}
    assert [c.status for c in rec.units] == [Status.UNCHANGED] * 9
    ((change,),) = [rec.counts]
    assert (change.repeat_id, change.condition_id, change.before, change.after) == (
        "rep2",
        "DrugA",
        10,
        15,
    )
    draft = candidate(published, rec)
    assert draft.repeats["rep2"].members["DrugA"].image_count == 15
    assert all(r.confirmed for r in draft.repeats.values())
    assert {k: r.confirmed_on for k, r in draft.repeats.items()} == {
        k: r.confirmed_on for k, r in published.repeats.items()
    }
    assert layout_changed(published, draft) == ()
    output, base = published_in(tmp_path, published)
    result = publish_description(output, draft, base_sha256=base)
    assert result.records == ()  # the change is reported, and needs no re-confirmation
    assert b'confirmed_on = "2026-10-30"' in (output / "morphobricks.toml").read_bytes()


def test_a_change_of_image_count_alone_never_reopens_even_with_fewer_images():
    published = model("v13")
    rec = reconcile(published, folder(published, {key("CTRL", "rep1"): ObservedUnit(3)}))
    assert rec.reopened == {}
    assert candidate(published, rec).repeats["rep1"].members["CTRL"].image_count == 3


def test_p15_a_changed_channel_list_reopens_with_the_date_kept():
    published = model("v01")
    changed = {key("CTRL", "rep1"): ObservedUnit(12, channel_count=3)}
    rec = reconcile(published, folder(published, changed))
    assert rec.reopened == {"rep1": (CHANNELS_CHANGED,)}
    draft = candidate(published, rec)
    assert draft.repeats["rep1"].confirmed is False
    assert draft.repeats["rep1"].confirmed_on == published.repeats["rep1"].confirmed_on
    assert draft.repeats["rep2"].confirmed is True


def test_the_header_evidence_that_still_fits_does_not_reopen_and_unknown_is_not_a_change():
    published = model("v01")
    fits = ObservedUnit(12, channel_count=2, header_names=("DAPI", "Phalloidin-488"))
    unknown = ObservedUnit(10, channel_count=None, header_names=None)
    unnamed = ObservedUnit(11, channel_count=2, header_names=(None, None))
    seen = folder(published, {key("CTRL", "rep1"): fits, key("CTRL", "rep2"): unknown})
    seen[key("DrugA", "rep1")] = unnamed
    assert reconcile(published, seen).reopened == {}


@pytest.mark.parametrize("names", [("dapi", "Phalloidin-488"), ("DAPI", "Phalloidin")])
def test_a_header_name_that_is_not_a_confirmed_name_reopens_without_case_folding(names):
    published = model("v01")
    seen = folder(published, {key("CTRL", "rep1"): ObservedUnit(12, 2, names)})
    assert reconcile(published, seen).reopened == {"rep1": (CHANNELS_CHANGED,)}


# P07 ------------------------------------------------------------------------------------


def test_p07_a_new_technical_repeat_is_pending_and_reopens_only_its_repeat(tmp_path):
    published = model("v13")
    new = key("DrugA", "rep2", "acquisition_2")
    rec = reconcile(published, folder(published, {new: ObservedUnit(4)}))
    assert [c.folders for c in rec.new] == [new]
    pending = rec.new[0]
    assert (pending.repeat_id, pending.technical_id, pending.proposed_repeat) == (
        None,
        None,
        "rep2",
    )
    assert rec.reopened == {"rep2": (NEW_UNIT,)}
    draft = candidate(published, rec)
    assert {k: r.confirmed for k, r in draft.repeats.items()} == {
        "rep1": True,
        "rep2": False,
        "rep3": True,
    }
    for name in ("rep1", "rep3"):  # the dates of repeats that are not reopened are byte-identical
        assert draft.repeats[name] == published.repeats[name]
    assert draft.repeats["rep2"].confirmed_on == published.repeats["rep2"].confirmed_on
    output, base = published_in(tmp_path, published)
    with pytest.raises(Exception, match="E_REPEAT_UNCONFIRMED|not a valid"):
        publish_description(output, draft, base_sha256=base)
    assert (output / "morphobricks.toml").read_bytes() == canonical_bytes(published)


def test_p07_after_the_user_assigns_the_unit_and_confirms_the_new_date_is_accepted(tmp_path):
    published = model("v06")
    member = published.repeats["R002"].members["CTRL"]
    new = key("CTRL", member.source_repeat, "acquisition_3")
    rec = reconcile(published, folder(published, {new: ObservedUnit(4)}))
    assert rec.reopened == {"R002": (NEW_UNIT,)} and [c.folders for c in rec.new] == [new]
    draft = candidate(published, rec)
    added = {**member.technical_repeats, "acq3": Unit("acquisition_3", ("DNA", "ACTIN"))}
    members = {**draft.repeats["R002"].members, "CTRL": replace(member, technical_repeats=added)}
    repeats = {**draft.repeats, "R002": replace(draft.repeats["R002"], members=members)}
    used = (*draft.used_ids["technical_repeats"], "acq3")
    assigned = replace(
        draft,
        repeats=repeats,
        used_ids={**draft.used_ids, "technical_repeats": tuple(sorted(used))},
    )
    assert layout_changed(published, assigned) == ("R002",)
    output, base = published_in(tmp_path, published)
    result = publish_description(output, confirm_repeat(assigned, "R002", DAY), base_sha256=base)
    assert result.records == ()  # adding a unit is not a changed entity (section 4.6)
    assert 'confirmed_on = "2026-11-09"' in (output / "morphobricks.toml").read_text()


def test_the_new_date_is_refused_without_a_reopening(tmp_path):
    published = model("v13")
    draft = candidate(published, reconcile(published, folder(published)))
    moved = replace(
        draft, repeats={**draft.repeats, "rep1": replace(draft.repeats["rep1"], confirmed_on=DAY)}
    )
    output, base = published_in(tmp_path, published)
    with pytest.raises(PublicationRefused) as refused:
        publish_description(output, moved, base_sha256=base)
    assert refused.value.codes == {Refusal.R_CONFIRMED_ON_CHANGED}


def test_confirm_repeat_needs_an_open_repeat_and_a_real_date():
    published = model("v01")
    with pytest.raises(ValueError, match="confirmed already"):
        confirm_repeat(published, "rep1", DAY)
    rec = reconcile(published, folder(published, {key("CTRL", "rep1"): ObservedUnit(12, 3)}))
    draft = candidate(published, rec)
    for bad in ("2026-13-01", "9 Nov 2026", ""):
        with pytest.raises(ValueError):
            confirm_repeat(draft, "rep1", bad)
    with pytest.raises(ValueError, match="no repeat"):
        confirm_repeat(draft, "nope", DAY)
    assert confirm_repeat(draft, "rep1", DAY).repeats["rep1"].confirmed_on == DAY


# P08 ------------------------------------------------------------------------------------


def renamed_member_folder(published):
    seen = folder(published)
    seen[key("DrugA", "experiment_23b")] = seen.pop(key("DrugA", "experiment_23"))
    return seen


def test_p08_a_renamed_folder_is_removed_plus_new_and_never_paired():
    published = model("v03")
    rec = reconcile(published, renamed_member_folder(published))
    assert [c.folders for c in rec.removed] == [key("DrugA", "experiment_23")]
    assert [c.folders for c in rec.new] == [key("DrugA", "experiment_23b")]
    assert rec.new[0].repeat_id is None and rec.new[0].proposed_repeat is None
    assert rec.reopened == {"Day1": (REMOVED_UNIT,)}
    draft = candidate(published, rec)
    assert draft.repeats["Day1"].members["DrugA"].source_repeat == "experiment_23"  # kept as was
    assert draft.repeats["Day1"].confirmed is False and "Day2" in draft.repeats
    assert draft.repeats["Day2"].confirmed is True


def test_p08_the_removal_is_one_grouped_question_and_publishes_only_when_answered(tmp_path):
    published = model("v03")
    rec = reconcile(published, renamed_member_folder(published))
    (question,) = removal_questions(published, rec)
    assert question.condition_id == "DrugA" and not question.last_member
    assert question.units == (key("DrugA", "experiment_23"),)
    assert question.needs == (
        ("channels", "ACTIN", None),
        ("channels", "DNA", None),
        ("conditions", "DrugA", None),
        ("repeats", "Day1", None),
        ("technical_repeats", "single", None),
    )
    answer = question.answer(DAY)
    draft = confirm_repeat(candidate(published, rec, [answer]), "Day1", DAY)
    assert "DrugA" not in draft.repeats["Day1"].members and "DrugA" in draft.repeats["Day2"].members
    output, base = published_in(tmp_path, published)
    with pytest.raises(PublicationRefused) as refused:  # without the user's record
        publish_description(output, draft, base_sha256=base)
    assert refused.value.codes == {Refusal.R_CONFIRMED_ID_CHANGED}
    found = reconfirmations(published, draft, rec, [answer])
    assert found.open == () and {c.confirmed_on for c in found.accepted} == {DAY}
    assert len(found.accepted) == 5
    publish_description(output, draft, base_sha256=base, confirmations=found.accepted)
    assert len(read_records(output)) == 5
    assert (output / RECORDS_FILENAME).exists()


def test_removing_a_condition_asks_once_for_every_lost_channel_and_technical_repeat(tmp_path):
    published = model("v01")
    gone = {k: v for k, v in folder(published).items() if k.condition != "DrugA"}
    rec = reconcile(published, gone)
    (question,) = removal_questions(published, rec)
    assert question.last_member and question.referenced_by == ()
    assert question.units == (key("DrugA", "rep1"), key("DrugA", "rep2"))
    assert {kind for kind, *_ in question.needs} == {"channels", "repeats", "technical_repeats"}
    assert len(question.needs) == 5  # DNA, ACTIN, rep1, rep2 and the technical repeat single
    answer = question.answer(DAY)
    draft = confirm_repeat(
        confirm_repeat(candidate(published, rec, [answer]), "rep1", DAY), "rep2", DAY
    )
    assert "DrugA" not in draft.conditions and draft.used_ids == published.used_ids
    output, base = published_in(tmp_path, published)
    found = reconfirmations(published, draft, rec, [answer])
    assert {(c.kind, c.old_id) for c in found.accepted} == {(k, i) for k, i, _ in question.needs}
    publish_description(output, draft, base_sha256=base, confirmations=found.accepted)
    again = replace(published, used_ids=draft.used_ids)
    assert Refusal.R_USED_ID_REUSED in {i.code for i in transition_issues(draft, again)}


def test_a_condition_that_another_still_references_cannot_be_removed_silently():
    published = model("v33")
    gone = {k: v for k, v in folder(published).items() if k.condition != "control"}
    rec = reconcile(published, gone)
    (question,) = removal_questions(published, rec)
    assert question.last_member and question.referenced_by == ("DrugA",)
    with pytest.raises(ValueError, match="still referenced"):
        candidate(published, rec, [question.answer(DAY)])


def test_a_removal_of_something_that_is_not_removed_is_refused():
    published = model("v01")
    rec = reconcile(published, folder(published))
    with pytest.raises(ValueError, match="not a removed unit"):
        candidate(published, rec, [Removal((key("CTRL", "rep1"),), DAY)])
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        Removal((), "yesterday")


# P33 ------------------------------------------------------------------------------------


def on_disk_as_ctl(published):
    return {
        replace(k, condition="ctl" if k.condition == "control" else k.condition): v
        for k, v in folder(published).items()
    }


def test_p33_a_a_renamed_condition_folder_is_not_attached_to_the_old_id():
    published = model("v33")
    rec = reconcile(published, on_disk_as_ctl(published))
    assert {c.folders.condition for c in rec.removed} == {"control"}
    assert {c.folders.condition for c in rec.new} == {"ctl"}
    assert all(c.condition_id is None and c.proposed_repeat in ("rep1", "rep2") for c in rec.new)
    draft = candidate(published, rec)
    assert draft.conditions["ctrl"].source_folder == "control"  # never moved without a statement
    attached = replace(
        published,
        conditions={
            **published.conditions,
            "ctrl": replace(published.conditions["ctrl"], source_folder="ctl"),
        },
    )
    codes = {i.code for i in transition_issues(published, attached)}
    assert Refusal.R_CONFIRMED_ID_CHANGED in codes  # t12: a hand-made attachment is refused


def test_p33_b_the_statement_keeps_the_id_moves_the_folder_and_reopens_the_repeats(tmp_path):
    published = model("v33")
    statement = FolderRename("condition", "control", "ctl", DAY)
    rec = reconcile(published, on_disk_as_ctl(published), [statement])
    assert rec.new == () and rec.removed == ()
    assert rec.reopened == {"rep1": (RENAMED_FOLDER,), "rep2": (RENAMED_FOLDER,)}
    draft = candidate(published, rec)
    assert draft.conditions["ctrl"].source_folder == "ctl"
    assert not any(r.confirmed for r in draft.repeats.values())
    assert {r.confirmed_on for r in draft.repeats.values()} == {"2026-11-06"}
    found = reconfirmations(published, draft, rec)
    assert found.open == ()
    assert [(c.kind, c.old_id, c.new_id, c.confirmed_on) for c in found.accepted] == [
        ("conditions", "ctrl", None, DAY)
    ]
    confirmed = confirm_repeat(confirm_repeat(draft, "rep1", DAY), "rep2", DAY)
    output, base = published_in(tmp_path, published)
    with pytest.raises(PublicationRefused) as refused:
        publish_description(output, confirmed, base_sha256=base)
    assert refused.value.codes == {Refusal.R_CONFIRMED_ID_CHANGED}  # without the record
    publish_description(output, confirmed, base_sha256=base, confirmations=found.accepted)
    assert [r["old_id"] for r in read_records(output)] == ["ctrl"]
    assert 'source_folder = "ctl"' in (output / "morphobricks.toml").read_text()


def test_p33_c_the_old_folder_removal_needs_its_confirmation_and_the_id_stays_used():
    published = model("v33")
    rec = reconcile(published, on_disk_as_ctl(published))
    (question,) = removal_questions(published, rec)
    assert question.condition_id == "ctrl" and question.last_member
    assert ("conditions", "ctrl", None) not in question.needs  # the ID goes, it is not "changed"
    assert "ctrl" in published.used_ids["conditions"]


# Statements are checked against the file and the folder -------------------------------


@pytest.mark.parametrize(
    ("statement", "seen", "message"),
    [
        (FolderRename("condition", "nope", "ctl", DAY), "ctl", "no published folder"),
        (FolderRename("condition", "control", "ctl", DAY), "both", "still in the experiment"),
        (FolderRename("condition", "control", "other", DAY), "ctl", "is not in the experiment"),
        (FolderRename("condition", "control", "DrugA", DAY), "ctl", "already a folder"),
    ],
)
def test_a_rename_statement_that_does_not_match_is_refused(statement, seen, message):
    published = model("v33")
    disk = on_disk_as_ctl(published)
    if seen == "both":
        disk.update(folder(published))
    with pytest.raises(RenameRefused, match=message):
        reconcile(published, disk, [statement])


def test_a_member_folder_rename_is_stated_under_its_condition():
    published = model("v03")
    statement = FolderRename("member", "experiment_23", "experiment_23b", DAY, condition="DrugA")
    rec = reconcile(published, renamed_member_folder(published), [statement])
    assert rec.new == () and rec.removed == ()
    assert rec.reopened == {"Day1": (RENAMED_FOLDER,)}
    draft = candidate(published, rec)
    assert draft.repeats["Day1"].members["DrugA"].source_repeat == "experiment_23b"
    found = reconfirmations(published, draft, rec)
    assert found.open == () and {c.confirmed_on for c in found.accepted} == {DAY}
    assert ("repeats", "Day1") in {(c.kind, c.old_id) for c in found.accepted}


def test_a_technical_folder_rename_is_stated_by_its_folder_label():
    published = model("v06")
    units = published_units(published)
    old = next(u.folders for u in units.values() if u.folders.technical != "single")
    seen = folder(published)
    seen[replace(old, technical="renamed_acq")] = seen.pop(old)
    statement = FolderRename("technical", old.technical, "renamed_acq", DAY)
    rec = reconcile(published, seen, [statement])
    assert rec.new == () and rec.removed == ()
    assert RENAMED_FOLDER in sum(rec.reopened.values(), ())


def test_a_statement_needs_a_real_date_and_its_condition_only_for_a_member():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        FolderRename("condition", "a", "b", "")
    with pytest.raises(ValueError, match="names its condition"):
        FolderRename("member", "a", "b", DAY)
    with pytest.raises(ValueError, match="names its condition"):
        FolderRename("condition", "a", "b", DAY, condition="c")
    with pytest.raises(ValueError, match="one of"):
        FolderRename("repeat", "a", "b", DAY)
