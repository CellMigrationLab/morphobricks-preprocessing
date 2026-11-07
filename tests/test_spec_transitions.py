"""The transition fixtures t01-t12 and the Spec's oracle, for the ``R_*`` comparison.

Each transition file is offered over its named predecessor and must give exactly the listed set
of ``R_*`` codes (or none); the oracle (``refval.transition``) must agree on every ordered pair
of valid and transition files, which also covers the used-ID and ``confirmed_on`` rules in
combinations the 12 fixtures do not reach.

One deliberate difference: the oracle takes a changed ``image_count`` as reopening a repeat's
confirmation (so it accepts a new ``confirmed_on`` beside it). Section 10 says new FOVs in an
existing unit do not reopen it, so the reader refuses that date change (``R_CONFIRMED_ON_CHANGED``);
the pairs are compared with every ``image_count`` set to 1 and the difference has its own test.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import replace

import pytest

from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_rules import Refusal
from morphobricks_preprocessing.description_transition import (
    Reconfirmation,
    transition_issues,
    unused,
    unwaived,
)
from spec_oracle import refval
from spec_pin import fixtures
from spec_text import section

VALID = {item.name.split("_")[0]: item for item in fixtures("valid")}
TRANSITIONS = fixtures("transitions")
ALL = [*fixtures("valid"), *TRANSITIONS]
PREDECESSOR = re.compile(r"predecessor valid/(v\d+)")


def _counts_equal(doc: dict) -> dict:
    """Return the oracle's model with every member's image count set to 1."""
    for repeat in doc["repeats"].values():
        for member in repeat["members"].values():
            member["image_count"] = 1
    return doc


def _codes(old, new) -> set[str]:
    return {issue.code.value for issue in transition_issues(old, new)}


def _predecessor(item) -> str:
    return PREDECESSOR.search(item.header["Expected"]).group(1)


@pytest.mark.parametrize("item", TRANSITIONS, ids=lambda item: item.name)
def test_a_transition_gives_exactly_its_listed_codes(item):
    old = description_from_bytes(VALID[_predecessor(item)].data)
    listed = {c for c in item.expected_codes if c.startswith("R_")}
    assert _codes(old, description_from_bytes(item.data)) == listed


def test_the_codes_are_the_refusals_of_the_text():
    text = "\n".join(section("8"))
    assert {code.value for code in Refusal} <= set(re.findall(r"`(R_[A-Z_]+)`", text))


def test_nothing_is_refused_when_the_candidate_is_the_published_file():
    for item in ALL:
        model = description_from_bytes(item.data)
        assert transition_issues(model, model) == ()


@pytest.mark.parametrize("old", ALL, ids=lambda item: item.name.split("_")[0])
def test_the_oracle_agrees_on_every_ordered_pair(old):
    oracle = refval()
    old_model = description_from_bytes(old.data)
    old_doc = _counts_equal(oracle.model(tomllib.loads(old.data.decode("utf-8"))))
    differences = []
    for new in ALL:
        new_doc = _counts_equal(oracle.model(tomllib.loads(new.data.decode("utf-8"))))
        ours = _codes(old_model, description_from_bytes(new.data))
        theirs = oracle.transition(old_doc, new_doc)
        if ours != theirs:
            differences.append((new.name, sorted(ours), sorted(theirs)))
    assert differences == []


def test_a_reconfirmation_accepts_only_the_change_it_names():
    old = description_from_bytes(VALID["v01"].data)
    by_name = {item.name.split("_")[0]: item for item in TRANSITIONS}
    renamed = transition_issues(old, description_from_bytes(by_name["t01"].data))
    day = Reconfirmation("repeats", "rep1", "Day1", "2026-11-07")
    assert unwaived(renamed, (day,))  # Day2 is still refused
    both = (day, Reconfirmation("repeats", "rep2", "Day2", "2026-11-07"))
    assert unwaived(renamed, both) == () and unused(renamed, both) == ()
    # Another kind, another old ID or no new ID names another change.
    for wrong in (
        Reconfirmation("conditions", "rep1", "Day1", "2026-11-07"),
        Reconfirmation("repeats", "rep1", None, "2026-11-07"),
        Reconfirmation("repeats", "rep1", "Day9", "2026-11-07"),
    ):
        assert len(unwaived(renamed, (wrong, both[1]))) == 1
        assert unused(renamed, (wrong,)) == (wrong,)


def test_a_reconfirmation_never_accepts_a_used_id_or_a_date_refusal():
    old = description_from_bytes(VALID["v01"].data)
    by_name = {item.name.split("_")[0]: item for item in TRANSITIONS}
    issues = transition_issues(old, description_from_bytes(by_name["t02"].data))
    assert {i.code for i in issues} == {Refusal.R_CONFIRMED_ID_CHANGED, Refusal.R_USED_ID_DROPPED}
    left = unwaived(issues, (Reconfirmation("channels", "ACTIN", "Actin", "2026-11-07"),))
    assert {i.code for i in left} == {Refusal.R_USED_ID_DROPPED}
    dated = transition_issues(old, description_from_bytes(by_name["t05"].data))
    assert unwaived(dated, (Reconfirmation("repeats", "rep1", None, "2026-11-07"),)) == dated


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "masks", "old_id": "a", "new_id": None, "confirmed_on": "2026-11-07"},
        {"kind": "repeats", "old_id": "a", "new_id": None, "confirmed_on": "2026-13-07"},
        {"kind": "repeats", "old_id": "a", "new_id": None, "confirmed_on": "20261107"},
        {"kind": "repeats", "old_id": "a", "new_id": None, "confirmed_on": "2026-02-30"},
    ],
)
def test_a_reconfirmation_has_a_known_kind_and_a_real_date(kwargs):
    with pytest.raises(ValueError):
        Reconfirmation(**kwargs)


def test_new_fov_counts_do_not_reopen_a_confirmation_so_a_new_date_is_refused():
    old = description_from_bytes(VALID["v01"].data)
    repeat = old.repeats["rep1"]
    member = repeat.members["CTRL"]
    more = replace(member, image_count=member.image_count + 5)
    members = {**repeat.members, "CTRL": more}
    same_day = replace(repeat, members=members)
    later = replace(repeat, members=members, confirmed_on="2026-11-07")
    assert _codes(old, replace(old, repeats={**old.repeats, "rep1": same_day})) == set()
    assert _codes(old, replace(old, repeats={**old.repeats, "rep1": later})) == {
        "R_CONFIRMED_ON_CHANGED"
    }


def test_a_reopened_confirmation_may_take_a_new_date():
    old = description_from_bytes(VALID["v01"].data)
    repeat = old.repeats["rep1"]
    member = repeat.members["CTRL"]
    unit = member.technical_repeats["single"]
    swapped = replace(unit, channels=tuple(reversed(unit.channels)))
    reopened = replace(
        repeat,
        confirmed_on="2026-11-07",
        members={**repeat.members, "CTRL": replace(member, technical_repeats={"single": swapped})},
    )
    codes = _codes(old, replace(old, repeats={**old.repeats, "rep1": reopened}))
    assert "R_CONFIRMED_ON_CHANGED" not in codes
