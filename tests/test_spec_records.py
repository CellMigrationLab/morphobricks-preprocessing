"""The ``removed`` (RR2) and ``image_count_changed`` (RR1, RR3) records at publication (4.6, 10).

The published file is a Spec fixture in a real output folder; the folder shows fewer units or
another count, the draft and the user's answers come from the real reconciliation, and the lines
are what ``publish_description`` appends to ``morphobricks.records.jsonl``, read back with
``read_records``. Dates are the user's: the confirmation date of a removal, ``recorded_on`` of a
count change.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME, RECORDS_FILENAME
from morphobricks_preprocessing.description_reconcile import (
    ObservedUnit,
    UnitFolders,
    confirm_repeat,
    reconcile,
)
from morphobricks_preprocessing.description_removal import (
    Removal,
    candidate,
    reconfirmations,
    removal_questions,
)
from morphobricks_preprocessing.run import description_file
from morphobricks_preprocessing.run.description_file import (
    ABSENT,
    PublicationRefused,
    publish_description,
    read_records,
)
from spec_pin import fixtures

BY_NAME = {item.name.split("_")[0]: item for item in fixtures("valid")}
DAY = "2026-11-09"
LATER = "2026-11-10"
NULLS = {
    "condition_id": None,
    "condition_source_folder": None,
    "repeat_id": None,
    "source_repeat": None,
    "technical_repeat_id": None,
    "technical_repeat_source_folder": None,
}


def model(name):
    return description_from_bytes(BY_NAME[name].data)


def key(condition, source_repeat, technical="single"):
    return UnitFolders(condition, source_repeat, technical)


def on_disk(description, changes=None, without=()):
    seen = {}
    for repeat in description.repeats.values():
        for condition_id, member in repeat.members.items():
            for unit in member.technical_repeats.values():
                folders = UnitFolders(
                    description.conditions[condition_id].source_folder,
                    member.source_repeat,
                    unit.source_folder,
                )
                if folders not in without:
                    seen[folders] = ObservedUnit(
                        member.image_count // len(member.technical_repeats)
                    )
    seen.update(changes or {})
    return seen


def removal(published, gone, changes=None):
    """The draft, re-confirmations and answers when the folder loses the ``gone`` units."""
    rec = reconcile(published, on_disk(published, changes, without=gone))
    answers = [question.answer(DAY) for question in removal_questions(published, rec)]
    draft = candidate(published, rec, answers)
    for repeat_id in rec.reopened:
        if repeat_id in draft.repeats:  # a repeat left without members is gone, not reopened
            draft = confirm_repeat(draft, repeat_id, DAY)
    found = reconfirmations(published, draft, rec, answers)
    assert found.open == ()
    return draft, found.accepted, answers


def publish_first(folder, published):
    return publish_description(folder, published, base_sha256=ABSENT).sha256


def removed_lines(folder):
    return [r for r in read_records(folder) if r["type"] == "removed"]


def snapshot(folder):
    return sorted((p.name, p.read_bytes()) for p in folder.iterdir())


def unit_row(condition, repeat, source_repeat, technical, folder=None, label=None, day=DAY):
    return {
        "type": "removed",
        "entity": "unit",
        "condition_id": condition,
        "condition_source_folder": folder or condition,
        "repeat_id": repeat,
        "source_repeat": source_repeat,
        "technical_repeat_id": technical,
        "technical_repeat_source_folder": label or technical,
        "confirmed_on": day,
    }


# removed -------------------------------------------------------------------------------


def test_a_removed_unit_writes_one_unit_record_naming_all_six_keys(tmp_path):
    published = model("v03")  # DrugA/experiment_23 is a member of Day1; DrugA stays in Day2
    base = publish_first(tmp_path, published)
    draft, accepted, answers = removal(published, {key("DrugA", "experiment_23")})
    publish_description(
        tmp_path, draft, base_sha256=base, confirmations=accepted, removals=tuple(answers)
    )
    assert removed_lines(tmp_path) == [unit_row("DrugA", "Day1", "experiment_23", "single")]
    kinds = [r["type"] for r in read_records(tmp_path)]
    assert kinds == ["reconfirmation"] * len(accepted) + ["removed"]  # one append, in this order


def test_a_removed_repeat_writes_the_repeat_then_each_of_its_units(tmp_path):
    published = model("v01")
    base = publish_first(tmp_path, published)
    gone = {key("CTRL", "rep1"), key("DrugA", "rep1")}
    draft, accepted, answers = removal(published, gone)
    assert "rep1" not in draft.repeats and "rep1" in draft.used_ids["repeats"]
    publish_description(
        tmp_path, draft, base_sha256=base, confirmations=accepted, removals=tuple(answers)
    )
    assert removed_lines(tmp_path) == [
        {**NULLS, "type": "removed", "entity": "repeat", "repeat_id": "rep1", "confirmed_on": DAY},
        unit_row("CTRL", "rep1", "rep1", "single"),
        unit_row("DrugA", "rep1", "rep1", "single"),
    ]


def test_a_removed_condition_writes_the_condition_then_each_of_its_units(tmp_path):
    published = model("v01")
    base = publish_first(tmp_path, published)
    gone = {key("DrugA", "rep1"), key("DrugA", "rep2")}
    draft, accepted, answers = removal(published, gone)
    assert "DrugA" not in draft.conditions
    publish_description(
        tmp_path, draft, base_sha256=base, confirmations=accepted, removals=tuple(answers)
    )
    assert removed_lines(tmp_path) == [
        {
            **NULLS,
            "type": "removed",
            "entity": "condition",
            "condition_id": "DrugA",
            "condition_source_folder": "DrugA",
            "confirmed_on": DAY,
        },
        unit_row("DrugA", "rep1", "rep1", "single"),
        unit_row("DrugA", "rep2", "rep2", "single"),
    ]


def test_a_removed_technical_repeat_names_its_id_and_folder_then_its_unit(tmp_path):
    published = model("v06")  # acquisition_2 is held by one unit only
    base = publish_first(tmp_path, published)
    draft, accepted, answers = removal(published, {key("CTRL", "rep2", "acquisition_2")})
    publish_description(
        tmp_path,
        draft,
        base_sha256=base,
        confirmations=accepted,
        removals=tuple(answers),
        recorded_on=LATER,  # the member's count falls with its unit: recorded too (RR3)
    )
    counted = [r for r in read_records(tmp_path) if r["type"] == "image_count_changed"]
    assert [(r["condition_id"], r["old_image_count"], r["new_image_count"]) for r in counted] == [
        ("CTRL", 9, 4)
    ]
    assert removed_lines(tmp_path) == [
        {
            **NULLS,
            "type": "removed",
            "entity": "technical_repeat",
            "technical_repeat_id": "acquisition_2",
            "technical_repeat_source_folder": "acquisition_2",
            "confirmed_on": DAY,
        },
        unit_row("CTRL", "R002", "rep2", "acquisition_2"),
    ]


def test_the_lines_are_canonical_json_with_null_keys_and_pass_the_reader(tmp_path):
    published = model("v01")
    base = publish_first(tmp_path, published)
    draft, accepted, answers = removal(published, {key("CTRL", "rep1"), key("DrugA", "rep1")})
    publish_description(
        tmp_path, draft, base_sha256=base, confirmations=accepted, removals=tuple(answers)
    )
    text = (tmp_path / RECORDS_FILENAME).read_text(encoding="utf-8")
    assert text.endswith("\n")
    repeat_line = next(line for line in text.split("\n") if '"entity": "repeat"' in line)
    assert repeat_line == (
        '{"condition_id": null, "condition_source_folder": null, "confirmed_on": "2026-11-09", '
        '"entity": "repeat", "repeat_id": "rep1", "source_repeat": null, '
        '"technical_repeat_id": null, "technical_repeat_source_folder": null, '
        '"type": "removed"}'
    )
    assert [json.loads(line)["type"] for line in text.split("\n")[:-1]] == [
        r["type"] for r in read_records(tmp_path)
    ]


def test_the_date_of_each_record_is_the_users_confirmation_not_the_clock(tmp_path):
    published = model("v03")
    base = publish_first(tmp_path, published)
    draft, accepted, _ = removal(published, {key("DrugA", "experiment_23")})
    answer = Removal((key("DrugA", "experiment_23"),), LATER)
    accepted = tuple(replace(c, confirmed_on=LATER) for c in accepted)
    publish_description(
        tmp_path, draft, base_sha256=base, confirmations=accepted, removals=(answer,)
    )
    assert [r["confirmed_on"] for r in read_records(tmp_path)] == [LATER] * 6


def test_a_dropped_unit_without_a_removal_is_not_published(tmp_path):
    published = model("v03")
    base = publish_first(tmp_path, published)
    draft, accepted, _ = removal(published, {key("DrugA", "experiment_23")})
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="no removal confirms"):
        publish_description(tmp_path, draft, base_sha256=base, confirmations=accepted)
    assert snapshot(tmp_path) == before


def test_a_removal_of_a_unit_the_candidate_keeps_is_not_published(tmp_path):
    published = model("v03")
    base = publish_first(tmp_path, published)
    before = snapshot(tmp_path)
    answer = Removal((key("DrugA", "experiment_23"),), DAY)
    with pytest.raises(ValueError, match="does not remove"):
        publish_description(tmp_path, published, base_sha256=base, removals=(answer,))
    assert snapshot(tmp_path) == before


def test_nothing_is_written_when_publication_is_refused(tmp_path):
    published = model("v03")
    base = publish_first(tmp_path, published)
    draft, accepted, answers = removal(published, {key("DrugA", "experiment_23")})
    before = snapshot(tmp_path)
    with pytest.raises(PublicationRefused):  # the base is not the published file
        publish_description(tmp_path, draft, base_sha256="0" * 64, removals=tuple(answers))
    with pytest.raises(PublicationRefused):  # the changed IDs are not re-confirmed
        publish_description(tmp_path, draft, base_sha256=base, removals=tuple(answers))
    assert snapshot(tmp_path) == before
    assert not (tmp_path / RECORDS_FILENAME).exists()


def test_a_failed_records_write_puts_back_the_file_and_leaves_no_records(tmp_path, monkeypatch):
    published = model("v03")
    base = publish_first(tmp_path, published)
    draft, accepted, answers = removal(published, {key("DrugA", "experiment_23")})
    before = snapshot(tmp_path)

    def broken(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(description_file, "_replace_file_durably", broken)
    with pytest.raises(OSError, match="disk full"):
        publish_description(
            tmp_path, draft, base_sha256=base, confirmations=accepted, removals=tuple(answers)
        )
    assert snapshot(tmp_path) == before
    assert (tmp_path / DESCRIPTION_FILENAME).exists() and not (tmp_path / RECORDS_FILENAME).exists()


# image_count_changed -------------------------------------------------------------------


def _counted(published, changes):
    rec = reconcile(published, on_disk(published, changes))
    assert rec.reopened == {}
    return candidate(published, rec)


def test_a_changed_image_count_writes_the_old_and_new_counts_with_recorded_on(tmp_path):
    published = model("v06")  # CTRL/rep2 has two technical repeats
    base = publish_first(tmp_path, published)
    draft = _counted(published, {key("CTRL", "rep2", "acquisition_1"): ObservedUnit(8)})
    result = publish_description(tmp_path, draft, base_sha256=base, recorded_on=LATER)
    expected = {
        "type": "image_count_changed",
        "condition_id": "CTRL",
        "condition_source_folder": "CTRL",
        "repeat_id": "R002",
        "source_repeat": "rep2",
        "technical_repeats": [
            {"id": "acquisition_1", "source_folder": "acquisition_1"},
            {"id": "acquisition_2", "source_folder": "acquisition_2"},
        ],
        "old_image_count": 9,
        "new_image_count": 12,
        "recorded_on": LATER,
    }
    assert read_records(tmp_path) == (expected,) == result.records
    assert "confirmed_on" not in expected  # never a confirmation
    assert draft.repeats["R002"].confirmed and draft.repeats["R002"].confirmed_on == (
        published.repeats["R002"].confirmed_on
    )


def test_an_image_count_change_needs_a_date_and_writes_nothing_without_one(tmp_path):
    published = model("v06")
    base = publish_first(tmp_path, published)
    draft = _counted(published, {key("CTRL", "rep2", "acquisition_1"): ObservedUnit(8)})
    before = snapshot(tmp_path)
    for bad in (None, "09/11/2026", "2026-13-40"):
        with pytest.raises(ValueError, match="recorded_on"):
            publish_description(tmp_path, draft, base_sha256=base, recorded_on=bad)
    assert snapshot(tmp_path) == before


def test_several_changed_members_are_written_in_condition_then_repeat_order(tmp_path):
    published = model("v13")
    base = publish_first(tmp_path, published)
    draft = _counted(
        published,
        {key("DrugA", "rep2"): ObservedUnit(11), key("CTRL", "rep3"): ObservedUnit(12)},
    )
    publish_description(tmp_path, draft, base_sha256=base, recorded_on=DAY)
    lines = read_records(tmp_path)
    assert [(r["condition_id"], r["repeat_id"]) for r in lines] == [
        ("CTRL", "rep3"),
        ("DrugA", "rep2"),
    ]
    assert [(r["old_image_count"], r["new_image_count"]) for r in lines] == [(10, 12), (10, 11)]


def test_an_unchanged_count_writes_no_record_and_needs_no_date(tmp_path):
    published = model("v13")
    base = publish_first(tmp_path, published)
    draft = replace(published, schema_version=published.schema_version)
    assert publish_description(tmp_path, draft, base_sha256=base).records == ()
    assert not (tmp_path / RECORDS_FILENAME).exists()


def test_a_removal_and_a_count_change_in_one_publication_write_both_in_order(tmp_path):
    """RR3: the count line is written although the same publication removes something."""
    published = model("v06")
    base = publish_first(tmp_path, published)
    gone = {key("CTRL", "rep2", "acquisition_2")}
    draft, accepted, answers = removal(
        published, gone, {key("DrugA", "experiment_23"): ObservedUnit(7)}
    )
    assert draft.repeats["R001"].members["DrugA"].image_count == 7
    result = publish_description(
        tmp_path,
        draft,
        base_sha256=base,
        confirmations=accepted,
        removals=tuple(answers),
        recorded_on=LATER,
    )
    lines = read_records(tmp_path)
    assert lines == result.records
    assert [(r["type"], r.get("entity")) for r in lines] == [
        *[("reconfirmation", None)] * len(accepted),
        ("removed", "technical_repeat"),
        ("removed", "unit"),
        ("image_count_changed", None),
        ("image_count_changed", None),
    ]
    counts = [r for r in lines if r["type"] == "image_count_changed"]
    assert [(r["condition_id"], r["repeat_id"]) for r in counts] == [
        ("CTRL", "R002"),
        ("DrugA", "R001"),
    ]
    assert [(r["old_image_count"], r["new_image_count"]) for r in counts] == [(9, 4), (5, 7)]
    assert all(r["recorded_on"] == LATER and "confirmed_on" not in r for r in counts)
