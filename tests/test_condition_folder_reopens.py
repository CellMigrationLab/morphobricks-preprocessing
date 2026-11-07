"""A condition folder renamed under its ID reopens the repeats of that condition (P33 b).

Experiment-v1 section 10 reopens the N35 confirmation when the pairing of folders changes, and
a condition ID that now holds another folder is such a change. The published file's ``confirmed_on``
may then move with the user's new confirmation, as it may for any other layout change.
"""

from dataclasses import replace

from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_rules import Refusal
from morphobricks_preprocessing.description_transition import transition_issues
from spec_pin import fixtures

V33 = description_from_bytes(
    next(item for item in fixtures("valid") if item.name.startswith("v33")).data
)


def renamed_and_reconfirmed(day):
    conditions = {**V33.conditions, "ctrl": replace(V33.conditions["ctrl"], source_folder="ctl")}
    repeats = {key: replace(repeat, confirmed_on=day) for key, repeat in V33.repeats.items()}
    return replace(V33, conditions=conditions, repeats=repeats)


def test_a_new_confirmation_date_is_accepted_after_a_condition_folder_changed():
    codes = {issue.code for issue in transition_issues(V33, renamed_and_reconfirmed("2026-11-09"))}
    assert Refusal.R_CONFIRMED_ON_CHANGED not in codes
    assert Refusal.R_CONFIRMED_ID_CHANGED in codes  # the reassignment still needs its record


def test_the_date_still_may_not_move_when_nothing_was_reopened():
    unchanged = replace(
        V33, repeats={k: replace(r, confirmed_on="2026-11-09") for k, r in V33.repeats.items()}
    )
    codes = {issue.code for issue in transition_issues(V33, unchanged)}
    assert codes == {Refusal.R_CONFIRMED_ON_CHANGED}
