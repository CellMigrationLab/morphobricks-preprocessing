"""The draft-to-description step (experiment-v1 4.4a, 4.6a): nothing is confirmed for the user."""

import pytest

from draft_file_support import (
    DAY,
    PATHS,
    UNITS,
    complete,
)
from morphobricks_preprocessing import discover_experiment
from morphobricks_preprocessing.description import Channel, Condition
from morphobricks_preprocessing.design import DesignRow, ExperimentDesign, write_experiment_design
from morphobricks_preprocessing.draft import DraftError, draft_from_discovery, migrate_legacy_design
from morphobricks_preprocessing.draft_answers import (
    ConditionAnswer,
    DraftAnswers,
    DraftIncomplete,
    RepeatAnswer,
    UnitAnswer,
    apply_answers,
    confirm_repeat,
    description_from_draft,
)
from morphobricks_preprocessing.draft_format import (
    ABSENT,
    SPEC_COMMIT,
)
from tiff_tree import make_tree


@pytest.fixture
def exp(tmp_path):
    make_tree(tmp_path / "exp", PATHS)
    return discover_experiment(tmp_path / "exp")


def test_confirm_repeat_takes_the_users_day_and_refuses_a_bad_one(exp):
    base = draft_from_discovery(exp)
    assert confirm_repeat(base, "rep1", DAY).repeats["rep1"].confirmed_on == DAY
    for bad in ("", "2026-13-01", "2026-1-1", "today"):
        with pytest.raises(DraftError) as refusal:
            confirm_repeat(base, "rep1", bad)
        assert refusal.value.codes == ("E_DATE_FORMAT",)
    with pytest.raises(DraftError):
        confirm_repeat(base, "rep9", DAY)


# --- draft -> description ---------------------------------------------------------------------


def test_nothing_is_confirmed_for_the_user_and_every_missing_item_is_listed(exp):
    base = draft_from_discovery(exp)
    assert base.conditions["CTRL"].proposed == "CTRL"  # proposed, so the user can accept it
    with pytest.raises(DraftIncomplete) as refusal:
        description_from_draft(base, DraftAnswers(ABSENT, SPEC_COMMIT))
    asked = {(q.code, q.subject) for q in refusal.value.questions}
    assert asked == {
        ("id_pending", "condition CTRL"),
        ("id_pending", "condition DrugA"),
        ("id_pending", "technical repeat single"),
        ("id_pending", "repeat rep1"),
        ("id_pending", "repeat rep2"),
        ("repeat_not_confirmed", "repeat rep1"),
        ("repeat_not_confirmed", "repeat rep2"),
        *(("unit_channels_missing", " / ".join(key)) for key in UNITS),
    }
    assert len(refusal.value.questions) == len(asked)  # each item once
    assert "11 missing" in str(refusal.value)
    assert set(refusal.value.codes) == {c for c, _ in asked}


def test_a_partly_answered_draft_lists_only_what_is_missing(exp):
    answers = complete()
    units = dict(answers.units)
    units[("DrugA", "rep2", "single")] = UnitAnswer(("DNA", "GFP"), "channels")  # not declared
    units.pop(("CTRL", "rep1", "single"))
    repeats = dict(answers.repeats)
    repeats["rep2"] = RepeatAnswer(id="rep2")  # no confirmation day
    answers = DraftAnswers(
        ABSENT,
        SPEC_COMMIT,
        {"CTRL": ConditionAnswer(id="CTRL"), "DrugA": ConditionAnswer()},
        answers.technical_repeats,
        repeats,
        units,
        answers.channels,
        answers.masks,
    )
    with pytest.raises(DraftIncomplete) as refusal:
        description_from_draft(draft_from_discovery(exp), answers)
    assert {(q.code, q.subject) for q in refusal.value.questions} == {
        ("id_pending", "condition DrugA"),
        ("repeat_not_confirmed", "repeat rep2"),
        ("unit_channels_missing", "CTRL / rep1 / single"),
        ("channel_undeclared", "GFP"),
    }


def test_a_unit_with_an_empty_channel_list_is_missing(exp):
    answers = complete()
    units = {**answers.units, ("CTRL", "rep1", "single"): UnitAnswer(())}
    with pytest.raises(DraftIncomplete) as refusal:
        description_from_draft(
            draft_from_discovery(exp),
            DraftAnswers(
                ABSENT,
                SPEC_COMMIT,
                answers.conditions,
                answers.technical_repeats,
                answers.repeats,
                units,
                answers.channels,
                answers.masks,
            ),
        )
    assert [q.code for q in refusal.value.questions] == ["unit_channels_missing"]


def test_a_complete_draft_gives_the_description_the_user_answered(exp):
    description = description_from_draft(draft_from_discovery(exp), complete())
    assert description.conditions == {
        "CTRL": Condition("CTRL"),
        "DrugA": Condition("DrugA", reference="CTRL", target="SRC"),
    }
    assert description.channels == {"DNA": Channel("DAPI", ("DAPI",)), "ACTIN": Channel()}
    assert {
        k: (r.confirmed, r.confirmed_on, r.display_name) for k, r in description.repeats.items()
    } == {
        "rep1": (True, DAY, None),
        "rep2": (True, "2026-11-05", "Second"),
    }
    member = description.repeats["rep2"].members["DrugA"]
    assert (member.source_repeat, member.image_count) == ("rep2", 2)
    unit = member.technical_repeats["single"]
    assert (unit.source_folder, unit.channels, unit.image_type) == (
        "single",
        ("DNA", "ACTIN"),
        None,
    )
    assert description.masks["Nuclei"].source_channels == ("DNA",)
    assert description.used_ids == {
        "conditions": ("CTRL", "DrugA"),
        "channels": ("ACTIN", "DNA"),
        "repeats": ("rep1", "rep2"),
        "technical_repeats": ("single",),
    }


def test_retired_ids_stay_in_used_ids(exp):
    base = draft_from_discovery(exp, used_ids={"repeats": ["old"], "channels": ["GFP"]})
    description = description_from_draft(base, complete())
    assert description.used_ids["repeats"] == ("old", "rep1", "rep2")
    assert description.used_ids["channels"] == ("ACTIN", "DNA", "GFP")


def test_a_legacy_confirmation_is_carried_not_asked_again(tmp_path):
    make_tree(tmp_path / "exp", PATHS)
    rows = [
        DesignRow(c, r, r, control=False, image_count=2, confirmed=True, confirmed_on="2026-10-30")
        for c in ("CTRL", "DrugA")
        for r in ("rep1", "rep2")
    ]
    csv = tmp_path / "legacy.csv"
    write_experiment_design(ExperimentDesign(tuple(rows)), csv)
    draft = migrate_legacy_design(csv.read_bytes(), discover_experiment(tmp_path / "exp"))
    answers = complete()
    only_ids = DraftAnswers(
        ABSENT,
        SPEC_COMMIT,
        {k: ConditionAnswer(id=k) for k in ("CTRL", "DrugA")},
        answers.technical_repeats,
        {k: RepeatAnswer(id=k) for k in ("rep1", "rep2")},
        answers.units,
        answers.channels,
    )
    description = description_from_draft(draft, only_ids)
    assert {r.confirmed_on for r in description.repeats.values()} == {"2026-10-30"}
    assert apply_answers(draft, only_ids).repeats["rep1"].confirmed_on == "2026-10-30"
