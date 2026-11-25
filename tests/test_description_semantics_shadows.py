"""E_REPEAT_ID_SHADOWS_SOURCE after the linear rewrite of its check (#505).

The check used to compare every repeat ID with every member's source repeat, normalising both
for each pair. ``_quadratic_membership`` keeps that pair-by-pair scan as the reference: the
reader must give exactly the same issues (codes, paths, values, order) on every vendored Spec
fixture and on documents built to collide by case, by NFC and by case fold.
"""

from __future__ import annotations

import unicodedata

import pytest

from morphobricks_preprocessing import description_semantics
from morphobricks_preprocessing.description_read import read_description_bytes
from morphobricks_preprocessing.description_report import DescriptionIssue, Issues, key_path
from morphobricks_preprocessing.description_rules import Code
from morphobricks_preprocessing.description_walk import member_path, members
from spec_pin import fixtures

ALL = fixtures("valid") + fixtures("invalid") + fixtures("transitions")
SHADOWS = Code.E_REPEAT_ID_SHADOWS_SOURCE


def _quadratic_membership(doc, out):
    """The check as it was before #505, pair by pair (the reference, never shipped)."""
    nfc = description_semantics.nfc
    listed = list(members(doc))
    for repeat, condition, _ in listed:
        if condition not in doc["conditions"]:
            out.add(Code.E_UNKNOWN_CONDITION, member_path(repeat, condition), condition)
    with_members = {condition for _, condition, _ in listed}
    for condition in doc["conditions"]:
        if condition not in with_members:
            out.add(Code.E_CONDITION_WITHOUT_MEMBERS, key_path("conditions", condition), condition)
    owners = {}
    for repeat, condition, member in listed:
        path = f"{member_path(repeat, condition)}.source_repeat"
        owners.setdefault((condition, member["source_repeat"]), {})[repeat] = path
    for (_, source), by_repeat in owners.items():
        if len(by_repeat) > 1:
            for path in by_repeat.values():
                out.add(Code.E_SOURCE_REPEAT_REUSED, path, source)
    for repeat in doc["repeats"]:
        for other, condition, member in listed:
            if (
                other != repeat
                and nfc(member["source_repeat"]).casefold() == nfc(repeat).casefold()
            ):
                value = (repeat, member["source_repeat"])
                out.add(SHADOWS, member_path(other, condition), value)


def _both(doc):
    """Return the issues of the shipped check and of the reference on one document."""
    shipped, reference = Issues(), Issues()
    description_semantics._repeat_membership(doc, shipped)
    _quadratic_membership(doc, reference)
    return shipped.items, reference.items


def _doc(repeats):
    """Return a phase-5 document of ``{repeat: {condition: source_repeat}}``."""
    conditions = {condition for table in repeats.values() for condition in table}
    return {
        "conditions": {condition: {} for condition in sorted(conditions)},
        "repeats": {
            repeat: {"members": {c: {"source_repeat": s} for c, s in table.items()}}
            for repeat, table in repeats.items()
        },
    }


@pytest.mark.parametrize("item", ALL, ids=lambda item: f"{item.kind}/{item.name}")
def test_the_reader_reports_what_the_pair_by_pair_scan_reported(item, monkeypatch):
    shipped = read_description_bytes(item.data)
    monkeypatch.setattr(description_semantics, "_repeat_membership", _quadratic_membership)
    reference = read_description_bytes(item.data)
    assert shipped == reference


def test_the_spec_fixture_of_the_swap_is_still_refused_at_both_members():
    (item,) = [item for item in fixtures("invalid") if item.name == "i75_repeat_id_swap"]
    report = read_description_bytes(item.data)
    assert report.errors == (
        DescriptionIssue(SHADOWS, "repeats.rep2.members.CTRL", ("rep1", "rep1")),
        DescriptionIssue(SHADOWS, "repeats.rep1.members.CTRL", ("rep2", "rep2")),
    )


@pytest.mark.parametrize(
    ("repeat", "source"),
    [
        ("Rep1", "rep1"),
        ("rep1", "REP1"),
        ("café", "café"),  # NFC repeat ID, NFD folder name
        ("Café", "CAFÉ"),  # and another case
        ("Straße", "STRASSE"),  # equal only after the case fold
    ],
)
def test_a_repeat_id_equal_after_nfc_and_case_fold_shadows_the_source(repeat, source):
    doc = _doc({repeat: {"CTRL": "own"}, "other": {"CTRL": source}})
    shipped, reference = _both(doc)
    assert shipped == reference
    assert shipped == [DescriptionIssue(SHADOWS, "repeats.other.members.CTRL", (repeat, source))]


def test_a_repeat_s_own_source_folder_and_a_different_name_are_not_shadows():
    doc = _doc({"rep1": {"CTRL": "REP1"}, "rep2": {"CTRL": "rep²"}})
    assert _both(doc) == ([], [])


def test_the_issues_keep_the_repeat_by_member_order():
    doc = _doc(
        {
            "b": {"CTRL": "A", "DrugA": "c"},
            "a": {"CTRL": "B", "DrugA": "b"},
            "c": {"CTRL": "a", "DrugA": "C"},
        }
    )
    shipped, reference = _both(doc)
    assert shipped == reference
    assert [(issue.path, issue.value) for issue in shipped] == [
        ("repeats.a.members.CTRL", ("b", "B")),
        ("repeats.a.members.DrugA", ("b", "b")),
        ("repeats.b.members.CTRL", ("a", "A")),
        ("repeats.c.members.CTRL", ("a", "a")),
        ("repeats.b.members.DrugA", ("c", "c")),
    ]


def test_many_colliding_repeats_give_the_reference_issues():
    forms = ("x", "X", unicodedata.normalize("NFD", "é"), "é", "É")
    repeats = {
        f"{forms[i % len(forms)]}{i // len(forms)}": {
            "CTRL": f"{forms[(i + 1) % len(forms)]}{(i + 2) // len(forms)}",
            "DrugA": f"{forms[(i + 3) % len(forms)]}{i // len(forms)}",
        }
        for i in range(40)
    }
    shipped, reference = _both(_doc(repeats))
    assert [issue.code for issue in shipped].count(SHADOWS) > 20
    assert shipped == reference
