"""The derived ``experiment_design.csv`` equals the Spec's golden files byte for byte (section 11).

Conformance P30 and P32 (derived part). Each golden is the derivation of a named valid fixture;
the oracle's own derivation (``refval.derive_csv``) must agree with both.
"""

from __future__ import annotations

import datetime
import tomllib

import pytest

from frozen_clock import FrozenClock
from morphobricks_preprocessing import discover_experiment, read_confirmed_design
from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.design_derive import derive_design, write_derived_design
from morphobricks_preprocessing.draft import migrate_legacy_design
from morphobricks_preprocessing.draft_answers import (
    ChannelAnswer,
    ConditionAnswer,
    DraftAnswers,
    RepeatAnswer,
    UnitAnswer,
    description_from_draft,
)
from morphobricks_preprocessing.draft_format import ABSENT, SPEC_COMMIT
from spec_oracle import refval
from spec_pin import fixtures, spec_root
from tiff_tree import make_tree

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
GOLDEN = sorted((spec_root() / "fixtures" / "expected").glob("*.experiment_design.csv"))


def test_the_golden_files_are_the_three_the_catalogue_lists():
    assert [p.name.split("_")[0] for p in GOLDEN] == ["v13", "v33", "v34"]


@pytest.mark.parametrize("golden", GOLDEN, ids=lambda p: p.name.split("_")[0])
def test_the_derived_file_equals_the_golden_file_byte_for_byte(golden, tmp_path):
    item = VALID[golden.name.split("_")[0]]
    path = write_derived_design(description_from_bytes(item.data), tmp_path / "design.csv")
    assert path.read_bytes() == golden.read_bytes()


@pytest.mark.parametrize("item", VALID.values(), ids=lambda item: item.name.split("_")[0])
def test_the_derived_file_equals_the_oracles_on_every_valid_fixture(item, tmp_path):
    oracle = refval()
    expected = oracle.derive_csv(oracle.model(tomllib.loads(item.data.decode("utf-8"))))
    path = write_derived_design(description_from_bytes(item.data), tmp_path / "design.csv")
    assert path.read_bytes() == expected


@pytest.mark.parametrize("item", VALID.values(), ids=lambda item: item.name.split("_")[0])
def test_the_derived_file_is_read_back_by_the_strict_reader(item, tmp_path):
    description = description_from_bytes(item.data)
    path = write_derived_design(description, tmp_path / "design.csv")
    design = read_confirmed_design(path).design
    assert design == derive_design(description)
    assert all(row.confirmed for row in design.rows)


def test_the_file_names_folders_never_ids():
    description = description_from_bytes(VALID["v33"].data)  # folder control, ID ctrl
    rows = derive_design(description).rows
    golden = (next(p for p in GOLDEN if p.name.startswith("v33"))).read_text().splitlines()[1:]
    assert [row.condition for row in rows] == [line.split(",")[0] for line in golden]
    assert "ctrl" not in {row.condition for row in rows}
    assert {row.condition for row in rows if row.control} == {"control"}


# P30: the legacy v13 answers, migrated, give back the legacy file -------------------------

V13_FOLDERS = [
    f"{c}/{r}/img{i:02d}.tif"
    for c in ("CTRL", "DrugA", "DrugB")
    for r in ("rep1", "rep2", "rep3")
    for i in range(10)
]
TWO_DATES = {
    b"CTRL,rep1,rep1,true,10,0,true,2026-10-30": b"CTRL,rep1,rep1,true,10,0,true,2026-10-01",
    b"DrugA,rep1,rep1,false,10,0,true,2026-10-30": b"DrugA,rep1,rep1,false,10,0,true,2026-10-15",
    b"DrugB,rep1,rep1,false,10,0,true,2026-10-30": b"DrugB,rep1,rep1,false,10,0,true,2026-10-15",
}


def _only_ids(draft):
    """The user's answers to a migrated v13 draft: the proposed IDs and the unit channels."""
    conditions, repeats = ("CTRL", "DrugA", "DrugB"), ("rep1", "rep2", "rep3")
    return DraftAnswers(
        ABSENT,
        SPEC_COMMIT,
        {c: ConditionAnswer(id=c) for c in conditions},
        {"single": "single"},
        {r: RepeatAnswer(id=r) for r in repeats},
        {(c, r, "single"): UnitAnswer(("DNA", "ACTIN")) for c in conditions for r in repeats},
        {"DNA": ChannelAnswer("DAPI", None), "ACTIN": ChannelAnswer("F-actin", None)},
    )


@pytest.mark.parametrize("two_dates", [False, True], ids=["one_date", "two_dates"])
def test_p30_the_derived_file_of_migrated_legacy_answers_differs_only_in_the_earlier_date(
    two_dates, tmp_path, monkeypatch
):
    """P30: one date everywhere gives the legacy file; rep1 with two dates keeps the latest."""
    make_tree(tmp_path / "exp", V13_FOLDERS)
    golden = (next(p for p in GOLDEN if p.name.startswith("v13"))).read_bytes()
    legacy = golden
    for old, new in TWO_DATES.items() if two_dates else ():
        assert legacy.count(old) == 1
        legacy = legacy.replace(old, new)
    clock = FrozenClock(monkeypatch, datetime.date(2026, 11, 9))
    draft = migrate_legacy_design(legacy, discover_experiment(tmp_path / "exp"))
    description = description_from_draft(draft, _only_ids(draft))
    derived = write_derived_design(description, tmp_path / "design.csv").read_bytes()
    assert clock.reads == 0  # the clock is never read
    changed = [
        (old, new)
        for old, new in zip(legacy.splitlines(), derived.splitlines(), strict=True)
        if old != new
    ]
    if not two_dates:
        assert derived == legacy == golden and draft.notes == ()
        return
    assert description.repeats["rep1"].confirmed_on == "2026-10-15"
    assert changed == [  # the one-date groups rep2 and rep3 are byte-identical
        (
            b"CTRL,rep1,rep1,true,10,0,true,2026-10-01,1",
            b"CTRL,rep1,rep1,true,10,0,true,2026-10-15,1",
        )
    ]
    (note,) = draft.notes
    assert (note.repeat_group, note.kept) == ("rep1", "2026-10-15")
    assert {day for _, _, day in note.dates} == {"2026-10-01", "2026-10-15"}
