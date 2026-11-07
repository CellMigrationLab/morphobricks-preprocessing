"""Publication scenarios P16-P20 and P22 of the conformance plan, on the Spec fixtures.

Each uses a valid fixture as the published file in a real output folder and offers a candidate
through ``publish_description`` (lock, base SHA-256, strict re-read, transition checks). A
refusal reports every applicable ``R_*`` and writes nothing.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_file import (
    DESCRIPTION_FILENAME,
    RECORDS_FILENAME,
    read_description,
)
from morphobricks_preprocessing.description_rules import Refusal
from morphobricks_preprocessing.description_transition import Reconfirmation
from morphobricks_preprocessing.description_write import canonical_bytes, section_digests
from morphobricks_preprocessing.run.description_file import (
    ABSENT,
    PublicationRefused,
    publish_description,
    read_records,
)
from spec_pin import fixtures

BY_NAME = {item.name.split("_")[0]: item for item in [*fixtures("valid"), *fixtures("transitions")]}
DAY = "2026-11-07"


def _model(name: str):
    return description_from_bytes(BY_NAME[name].data)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _publish_as_found(folder: Path, name: str) -> str:
    """Put a fixture's exact bytes in the folder as the published file; return its SHA-256."""
    data = BY_NAME[name].data
    (folder / DESCRIPTION_FILENAME).write_bytes(data)
    return _sha(data)


def _refused(folder: Path, candidate, base, confirmations=()) -> frozenset[Refusal]:
    before = sorted((p.name, p.read_bytes()) for p in folder.iterdir())
    with pytest.raises(PublicationRefused) as refusal:
        publish_description(folder, candidate, base_sha256=base, confirmations=confirmations)
    assert sorted((p.name, p.read_bytes()) for p in folder.iterdir()) == before  # nothing written
    return frozenset(refusal.value.codes)


@pytest.fixture
def folder(tmp_path):
    return tmp_path


def test_p16_a_changed_id_is_refused_and_t02_also_drops_a_used_id(folder):
    base = _publish_as_found(folder, "v01")
    assert _refused(folder, _model("t01"), base) == {Refusal.R_CONFIRMED_ID_CHANGED}
    assert _refused(folder, _model("t02"), base) == {
        Refusal.R_CONFIRMED_ID_CHANGED,
        Refusal.R_USED_ID_DROPPED,
    }


def test_p16_t01_is_accepted_only_with_a_recorded_reconfirmation(folder):
    base = _publish_as_found(folder, "v01")
    one = Reconfirmation("repeats", "rep1", "Day1", DAY)
    assert _refused(folder, _model("t01"), base, (one,)) == {Refusal.R_CONFIRMED_ID_CHANGED}
    both = (one, Reconfirmation("repeats", "rep2", "Day2", DAY))
    done = publish_description(folder, _model("t01"), base_sha256=base, confirmations=both)
    assert read_description(folder / DESCRIPTION_FILENAME) == _model("t01")
    assert done.base_sha256 == base and done.sha256 == _sha(canonical_bytes(_model("t01")))
    rows = read_records(folder)
    assert rows == done.records and len(rows) == 2
    assert rows[0] == {
        "type": "reconfirmation",
        "id_kind": "repeats",
        "old_id": "rep1",
        "new_id": "Day1",
        "confirmed_on": DAY,
        "old_sha256": base,
        "new_sha256": done.sha256,
    }
    assert [(r["old_id"], r["new_id"]) for r in rows] == [("rep1", "Day1"), ("rep2", "Day2")]


def test_p16_a_display_name_change_is_accepted_and_invalidates_nothing(folder):
    base = _publish_as_found(folder, "v01")
    done = publish_description(folder, _model("v16"), base_sha256=base)
    assert done.records == () and not (folder / RECORDS_FILENAME).exists()
    published = read_description(folder / DESCRIPTION_FILENAME)
    assert section_digests(published) == section_digests(_model("v01"))


def test_p17_a_second_draft_from_the_same_base_is_refused_and_not_merged(folder):
    base = _publish_as_found(folder, "v01")
    first = publish_description(folder, _model("v21"), base_sha256=base)
    on_disk = (folder / DESCRIPTION_FILENAME).read_bytes()
    assert on_disk == canonical_bytes(_model("v21"))
    assert _refused(folder, _model("v16"), base) == {Refusal.R_PUBLISHED_FILE_CHANGED}
    assert (folder / DESCRIPTION_FILENAME).read_bytes() == on_disk  # still the first one's
    # Re-derived from the file now published, the same draft is accepted.
    second = publish_description(folder, _model("v16"), base_sha256=first.sha256)
    assert second.base_sha256 == first.sha256
    assert read_description(folder / DESCRIPTION_FILENAME) == _model("v16")


def test_p18_publishing_over_nothing_with_the_absent_base_is_accepted(folder):
    done = publish_description(folder, _model("v01"), base_sha256=ABSENT)
    assert done.base_sha256 == ABSENT
    assert (folder / DESCRIPTION_FILENAME).read_bytes() == canonical_bytes(_model("v01"))
    assert not (folder / RECORDS_FILENAME).exists()


def test_p18_another_file_appears_before_publication(tmp_path):
    (tmp_path / DESCRIPTION_FILENAME).write_bytes(BY_NAME["v03"].data)  # appeared meanwhile
    assert _refused(tmp_path, _model("v01"), ABSENT) == {Refusal.R_PUBLISHED_FILE_CHANGED}


@pytest.mark.parametrize("base", [None, "", "ABSENT", "0" * 63, "G" * 64, _sha(b"x").upper()])
def test_p18_a_draft_without_a_usable_base_is_refused_and_nothing_is_published(folder, base):
    assert _refused(folder, _model("v01"), base) == {Refusal.R_DRAFT_BASE_MISSING}
    _publish_as_found(folder, "v01")
    assert _refused(folder, _model("v16"), base) == {Refusal.R_DRAFT_BASE_MISSING}


def test_p19_a_retired_id_declared_again_is_refused(folder):
    base = _publish_as_found(folder, "v25")
    assert _refused(folder, _model("t03"), base) == {Refusal.R_USED_ID_REUSED}


def test_p19_also_when_the_returning_folder_is_the_one_the_retired_id_had(folder):
    base = _publish_as_found(folder, "v25")
    candidate = _model("t03")
    back = replace(candidate.conditions["DrugA"], source_folder="DrugA")
    candidate = replace(candidate, conditions={**candidate.conditions, "DrugA": back})
    assert candidate.conditions["DrugA"].source_folder == "DrugA"
    assert _refused(folder, candidate, base) == {Refusal.R_USED_ID_REUSED}


def test_p20_a_dropped_used_id_is_refused(folder):
    base = _publish_as_found(folder, "v25")
    assert _refused(folder, _model("t04"), base) == {Refusal.R_USED_ID_DROPPED}


def _without_drug_a(old):
    repeats = {
        key: replace(repeat, members={c: m for c, m in repeat.members.items() if c != "DrugA"})
        for key, repeat in old.repeats.items()
    }
    conditions = {c: v for c, v in old.conditions.items() if c != "DrugA"}
    return replace(old, conditions=conditions, repeats=repeats)  # used_ids still lists DrugA


def test_p20_removing_the_last_member_of_a_condition_needs_the_recorded_confirmation(folder):
    base = _publish_as_found(folder, "v01")
    candidate = _without_drug_a(_model("v01"))
    assert "DrugA" in candidate.used_ids["conditions"]
    assert _refused(folder, candidate, base) == {Refusal.R_CONFIRMED_ID_CHANGED}
    # Every entity that lost a member folder or a position is a changed entity (4.6 (c)).
    lost = [("repeats", "rep1"), ("repeats", "rep2"), ("channels", "ACTIN"), ("channels", "DNA")]
    lost.append(("technical_repeats", "single"))
    removals = tuple(Reconfirmation(kind, i, None, DAY) for kind, i in lost)
    done = publish_description(folder, candidate, base_sha256=base, confirmations=removals)
    published = read_description(folder / DESCRIPTION_FILENAME)
    assert "DrugA" not in published.conditions and "DrugA" in published.used_ids["conditions"]
    assert len(done.records) == 5 and {r["new_id"] for r in done.records} == {None}


def test_p22_confirmed_on_stays_byte_for_byte_through_an_unrelated_publication(folder):
    base = _publish_as_found(folder, "v01")
    publish_description(folder, _model("v16"), base_sha256=base)
    published = (folder / DESCRIPTION_FILENAME).read_text(encoding="utf-8")
    assert published.count('confirmed_on = "2026-11-06"') == 2
    assert DAY not in published


def test_p22_a_new_date_on_an_unchanged_repeat_is_refused(folder):
    base = _publish_as_found(folder, "v01")
    assert _refused(folder, _model("t05"), base) == {Refusal.R_CONFIRMED_ON_CHANGED}


def test_p22_after_a_reopening_and_a_new_confirmation_the_new_date_is_accepted(folder):
    base = _publish_as_found(folder, "v01")
    candidate = _model("t06")  # a new member and a new folder join: the layout changed
    repeats = dict(candidate.repeats)
    repeats["rep1"] = replace(repeats["rep1"], confirmed_on=DAY)
    candidate = replace(candidate, repeats=repeats)
    publish_description(folder, candidate, base_sha256=base)
    published = read_description(folder / DESCRIPTION_FILENAME)
    assert published.repeats["rep1"].confirmed_on == DAY


def test_the_candidate_is_published_as_the_canonical_bytes_it_was_checked_as(folder):
    publish_description(folder, _model("v18"), base_sha256=ABSENT)
    assert (folder / DESCRIPTION_FILENAME).read_bytes() == canonical_bytes(_model("v01"))
