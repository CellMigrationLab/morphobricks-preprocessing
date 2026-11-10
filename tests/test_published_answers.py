"""Reopening a published description: its answers, retired IDs, reopened repeats, the review.

Library tests for Stage 1 PR 16b (`published_answers`, `description_review`,
`description_candidate`, `description_reopen`, and `publish_draft` writing the derived CSV).
"""

from dataclasses import replace

import pytest

from draft_file_support import COUNTS, DAY, PATHS, complete
from morphobricks_preprocessing import discover_experiment, preflight
from morphobricks_preprocessing.description_candidate import check_candidate
from morphobricks_preprocessing.description_reconcile import CHANNELS_CHANGED, reconcile
from morphobricks_preprocessing.description_reopen import published_state
from morphobricks_preprocessing.description_review import (
    comparisons,
    design_changes,
    replicates,
    short_names,
)
from morphobricks_preprocessing.design_derive import derive_design
from morphobricks_preprocessing.draft import draft_from_discovery
from morphobricks_preprocessing.draft_answers import (
    ConditionAnswer,
    UnitAnswer,
    apply_answers,
    description_from_draft,
)
from morphobricks_preprocessing.published_answers import (
    kept_date,
    published_answers,
    reasked,
    reopened,
    retired_ids,
)
from morphobricks_preprocessing.reconcile_observe import observe_folders
from morphobricks_preprocessing.run.description_file import publish_description
from morphobricks_preprocessing.run.draft_file import publish_draft, save_draft
from tiff_tree import make_tree

SHA = "0" * 64


@pytest.fixture
def exp(tmp_path):
    make_tree(tmp_path / "exp", PATHS)
    return tmp_path / "exp"


def described(root):
    draft = draft_from_discovery(discover_experiment(root))
    return draft, description_from_draft(draft, complete())


def test_the_published_answers_give_the_same_description_back(exp):
    draft, published = described(exp)
    answers = published_answers(published, SHA)
    assert answers.base_sha256 == SHA
    assert description_from_draft(draft, answers) == published
    assert answers.conditions["CTRL"].reference == ""  # no reference: "no comparison"
    assert answers.conditions["DrugA"].reference == "CTRL"


def test_a_repeat_named_otherwise_is_paired_under_its_id(tmp_path):
    make_tree(tmp_path / "exp", ["CTRL/rep1/a.tif", "DrugA/day2/a.tif"])
    draft = draft_from_discovery(discover_experiment(tmp_path / "exp"))
    answers = replace(
        complete(),
        repeats={"rep1": complete().repeats["rep1"]},
        pairing={("DrugA", "day2"): "rep1"},
        units={
            ("CTRL", "rep1", "single"): UnitAnswer(("DNA", "ACTIN"), "channels"),
            ("DrugA", "day2", "single"): UnitAnswer(("DNA", "ACTIN"), "channels"),
        },
    )
    published = description_from_draft(draft, answers)
    again = published_answers(published, SHA)
    assert dict(again.pairing) == {("DrugA", "day2"): "rep1"}
    assert description_from_draft(draft, again) == published


def test_only_the_ids_no_longer_in_use_are_retired(exp):
    _, published = described(exp)
    used = {**published.used_ids, "repeats": ("old", "rep1", "rep2")}
    retired = retired_ids(replace(published, used_ids=used))
    assert retired["repeats"] == ("old",)
    assert retired["conditions"] == ()


def test_a_repeat_that_gained_a_folder_is_asked_again_and_the_others_keep_their_date(tmp_path):
    make_tree(tmp_path / "exp", [p for p in PATHS if not p.startswith("DrugA/rep2")])
    small = draft_from_discovery(discover_experiment(tmp_path / "exp"))
    units = {k: v for k, v in complete().units.items() if k[:2] != ("DrugA", "rep2")}
    published = description_from_draft(small, replace(complete(), units=units))
    make_tree(tmp_path / "exp", ["DrugA/rep2/a.tif"])
    now = draft_from_discovery(discover_experiment(tmp_path / "exp"))
    answers, asked = reopened(now, published_answers(published, SHA), published)
    assert asked == ("rep2",)
    assert answers.repeats["rep2"].confirmed_on is None
    assert answers.repeats["rep2"].id == "rep2"  # the ID is kept
    assert answers.repeats["rep1"].confirmed_on == DAY  # byte for byte (4.4a)


def test_a_unit_s_declared_channel_order_reopens_its_repeat_with_the_same_folders(exp):
    # Experiment-v1 section 10 reopens a repeat when "a unit's ordered channel list" changes:
    # the candidate's declared list, compared with the published one.
    now, published = described(exp)
    saved = published_answers(published, SHA)
    key = next(k for k in saved.units if k[1] == "rep1")
    swapped = tuple(reversed(saved.units[key].channels))
    edited = replace(saved, units={**saved.units, key: replace(saved.units[key], channels=swapped)})
    answers, asked = reopened(now, edited, published)
    assert asked == ("rep1",)
    assert answers.repeats["rep1"].confirmed_on is None and answers.repeats["rep1"].id == "rep1"
    rep2 = saved.repeats["rep2"].confirmed_on
    assert rep2 and answers.repeats["rep2"].confirmed_on == rep2  # not reopened: kept (4.4a)
    again, by_answer = reasked(now, saved, edited, published)  # the same, as the edit is made
    assert by_answer == ("rep1",) and again.repeats == answers.repeats
    kept, none = reopened(now, saved, published)
    assert none == () and kept.repeats == saved.repeats


def test_headers_that_no_longer_fit_reopen_nothing_while_the_declared_list_is_as_saved(exp):
    # Owner decision (review of #257): the folder reconciliation's channels_changed compares the
    # headers with the PUBLISHED file. It is the run's refusal, not a section 10 reopening: the
    # declared ordered channel list did not change, so the date stays (section 4.4a) and a
    # header name the user adds to a channel keeps it too.
    now, published = described(exp)
    observed = observe_folders(discover_experiment(exp))
    changed = next(key for key in observed if key.source_repeat == "rep1")
    observed[changed] = replace(observed[changed], channel_count=7)  # headers state 7 channels
    assert reconcile(published, observed).reopened == {"rep1": (CHANNELS_CHANGED,)}
    saved = published_answers(published, SHA)
    kept, none = reopened(now, saved, published)
    assert none == () and kept.repeats == saved.repeats
    channel = next(iter(saved.channels))
    alias = replace(saved.channels[channel], header_names=("Hoechst",))
    named = replace(saved, channels={**saved.channels, channel: alias})
    after, asked = reasked(now, saved, named, published)
    assert asked == () and after.repeats == saved.repeats


def test_an_untagged_channel_on_the_way_reopens_nothing_and_the_saved_date_is_offered(exp):
    now, published = described(exp)
    saved = published_answers(published, SHA)
    key = next(k for k in saved.units if k[1] == "rep1")
    cleared = (None, *saved.units[key].channels[1:])
    gap = replace(saved, units={**saved.units, key: replace(saved.units[key], channels=cleared)})
    after, asked = reasked(now, saved, gap, published)
    assert asked == () and after.repeats == saved.repeats  # a tag being changed, not a list
    assert kept_date(now, gap, published, "rep1") is None  # the layout differs: the click's day
    assert kept_date(now, saved, published, "rep1") == published.repeats["rep1"].confirmed_on
    assert kept_date(now, saved, published, "nope") is None


def test_the_review_states_comparisons_in_words_and_missing_channels(exp):
    draft = draft_from_discovery(discover_experiment(exp))
    answers = replace(
        complete(),
        conditions={
            "CTRL": ConditionAnswer(id="CTRL", reference=""),
            "DrugA": ConditionAnswer(id="DrugA", display_name="Drug A", reference="CTRL"),
        },
    )
    units = dict(answers.units)
    units[("DrugA", "rep2", "single")] = UnitAnswer(("DNA",), "channels")
    answers = replace(answers, units=units)
    applied = apply_answers(draft, answers)
    assert comparisons(applied, answers) == [
        "CTRL is compared against nothing.",
        "DrugA (Drug A) is compared against CTRL.",
    ]
    rep2 = {view.label: view for view in replicates(applied, answers)}["rep2"]
    assert rep2.missing == ((("DrugA", "rep2", "single"), "ACTIN"),)
    assert ("Channels", ("ACTIN", "DNA")) in short_names(applied, answers)
    assert ("Masks", ("Nuclei",)) in short_names(applied, answers)
    unanswered = replace(answers, conditions={"CTRL": ConditionAnswer(id="CTRL")})
    assert "not answered yet" in comparisons(apply_answers(draft, unanswered), unanswered)[0]


def test_the_csv_changes_are_listed_by_row(exp):
    _, published = described(exp)
    after = derive_design(published)
    assert design_changes(None, after) == ["written for the first time (4 rows)"]
    assert design_changes(after, after) == []
    before = replace(after, rows=after.rows[1:])
    assert design_changes(before, after) == ["new row CTRL/rep1"]


def test_the_candidate_is_checked_on_its_exact_bytes_and_waits_for_the_roles(exp, tmp_path):
    _, published = described(exp)
    check = check_candidate(published, preflight(exp), None)
    assert check.can_save and not check.physical_run and not check.can_run
    assert check.changes == ("written for the first time (4 rows)",)


def test_the_published_state_lists_removed_folders_as_questions(exp, tmp_path):
    draft, published = described(exp)
    out = tmp_path / "out"
    out.mkdir()
    publish_description(out, published, base_sha256="absent")
    for path in (exp / "DrugA" / "rep2").iterdir():
        path.unlink()
    (exp / "DrugA" / "rep2").rmdir()
    state, problem = published_state(out, discover_experiment(exp))
    assert problem == "" and state.changed
    assert [q.condition_id for q in state.removals] == ["DrugA"]
    assert [u.folders.source_repeat for u in state.reconciliation.removed] == ["rep2"]


def test_publish_draft_writes_the_derived_csv_only_with_the_description(exp, tmp_path):
    draft = draft_from_discovery(discover_experiment(exp))
    out = tmp_path / "out"
    save_draft(out, draft, complete())
    assert not (out / "experiment_design.csv").exists()  # saving answers writes no CSV
    publish_draft(out, draft, channel_counts=COUNTS)
    assert (out / "experiment_design.csv").read_bytes().startswith(b"condition,")
