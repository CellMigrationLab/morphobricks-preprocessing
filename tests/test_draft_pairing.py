"""Pairing differently named folders and references in the draft answers (Stage 1, PR 14).

The pairing is the user's answer, saved in ``morphobricks.draft.json`` beside the IDs: without
it a resumed draft would put every folder back in the repeat of its own name.
"""

import pytest

from morphobricks_preprocessing import discover_experiment
from morphobricks_preprocessing.draft import DraftError, draft_from_discovery
from morphobricks_preprocessing.draft_answers import (
    ConditionAnswer,
    DraftAnswers,
    RepeatAnswer,
    apply_answers,
)
from morphobricks_preprocessing.draft_format import (
    ABSENT,
    SPEC_COMMIT,
    DraftFileError,
    answers_from_bytes,
    answers_to_bytes,
)
from morphobricks_preprocessing.run.draft_file import load_draft, save_draft
from tiff_tree import make_tree

PATHS = [f"{c}/{r}/a.tif" for c in ("ctrl", "drug") for r in ("rep1", "rep2")]
PATHS[3] = "drug/experiment_23/a.tif"  # drug's second repeat has another folder name
PAIR = {("drug", "experiment_23"): "rep2"}


@pytest.fixture
def base(tmp_path):
    make_tree(tmp_path / "exp", PATHS)
    return draft_from_discovery(discover_experiment(tmp_path / "exp"))


def answers(**tables):
    return DraftAnswers(ABSENT, SPEC_COMMIT, **tables)


def test_by_default_differently_named_folders_are_separate_repeats(base):
    assert sorted(base.repeats) == ["experiment_23", "rep1", "rep2"]


def test_a_paired_folder_joins_the_repeat_of_the_other_condition(base):
    draft = apply_answers(base, answers(pairing=PAIR))
    assert sorted(draft.repeats) == ["rep1", "rep2"]
    assert [m.key for m in draft.repeats["rep2"].members] == [
        ("ctrl", "rep2"),
        ("drug", "experiment_23"),
    ]
    assert draft.repeats["rep2"].id.proposed == "rep2" and draft.repeats["rep2"].id.pending
    assert not draft.repeats["rep2"].confirmed
    assert sorted(base.repeats) == ["experiment_23", "rep1", "rep2"]  # the input is not changed


def test_the_pairing_is_applied_before_the_repeat_answers(base):
    given = answers(
        pairing=PAIR, repeats={"rep2": RepeatAnswer(id="rep2", confirmed_on="2026-11-08")}
    )
    draft = apply_answers(base, given)
    assert draft.repeats["rep2"].id.confirmed == "rep2" and draft.repeats["rep2"].confirmed
    # an answer for the repeat that the pairing dissolved is refused, not moved elsewhere
    stale = answers(pairing=PAIR, repeats={"experiment_23": RepeatAnswer(id="experiment_23")})
    with pytest.raises(DraftError) as refused:
        apply_answers(base, stale)
    assert refused.value.codes == ("unknown_key",)


@pytest.mark.parametrize(
    ("pairing", "code"),
    [
        ({("drug", "nowhere"): "rep2"}, "unknown_key"),
        ({("drug", "experiment_23"): ""}, "pairing_label_empty"),
        ({("drug", "experiment_23"): " rep2"}, "pairing_label_empty"),
    ],
)
def test_a_pairing_the_draft_cannot_have_is_refused(base, pairing, code):
    with pytest.raises(DraftError) as refused:
        apply_answers(base, answers(pairing=pairing))
    assert refused.value.codes == (code,)


def test_a_pairing_after_a_repeat_is_named_is_refused(base):
    named = apply_answers(base, answers(repeats={"rep1": RepeatAnswer(id="rep1")}))
    with pytest.raises(DraftError) as refused:
        named.paired(PAIR)
    assert refused.value.codes == ("pairing_after_confirmation",)


def test_the_pairing_is_saved_and_resumed_from_the_draft_file(base, tmp_path):
    out = tmp_path / "out"
    given = answers(pairing=PAIR, conditions={"ctrl": ConditionAnswer(id="ctrl")})
    save_draft(out, base, given)
    on_disk = answers_from_bytes((out / "morphobricks.draft.json").read_bytes())
    assert dict(on_disk.pairing) == PAIR
    resumed = load_draft(out, base)
    assert sorted(resumed.draft.repeats) == ["rep1", "rep2"]
    assert resumed.draft.conditions["ctrl"].confirmed == "ctrl"
    assert answers_to_bytes(resumed.answers) == answers_to_bytes(given)


def test_the_file_of_an_older_version_without_pairing_is_refused(base):
    old = answers_to_bytes(answers()).replace(b'"version": 3', b'"version": 1')
    with pytest.raises(DraftFileError) as refused:
        answers_from_bytes(old)
    assert refused.value.code == "unknown_version"


@pytest.mark.parametrize(
    ("references", "code", "words"),
    [
        ({"ctrl": "ctrl"}, "E_REFERENCE_SELF", "against itself"),
        ({"ctrl": "drug", "drug": "ctrl"}, "E_REFERENCE_CYCLE", "ctrl → drug → ctrl is a loop"),
    ],
)
def test_a_reference_to_itself_or_a_loop_is_refused_and_never_saved(
    base, tmp_path, references, code, words
):
    given = answers(conditions={c: ConditionAnswer(reference=r) for c, r in references.items()})
    with pytest.raises(DraftError) as refused:
        apply_answers(base, given)
    assert refused.value.codes == (code,)
    assert words in str(refused.value)
    with pytest.raises(DraftError):
        save_draft(tmp_path / "out", base, given)
    assert not (tmp_path / "out" / "morphobricks.draft.json").exists()


def test_a_chain_of_references_is_allowed(base):
    given = answers(conditions={"drug": ConditionAnswer(reference="ctrl")})
    assert dict(apply_answers(base, given).references) == {"drug": "ctrl"}
