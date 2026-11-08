"""Saving, resuming and publishing ``morphobricks.draft.json`` (experiment-v1 10 and 12)."""

from datetime import date

import pytest

from draft_file_support import (
    DAY,
    PATHS,
    REFUSED,
    UNITS,
    complete,
    snapshot,
)
from morphobricks_preprocessing import discover_experiment
from morphobricks_preprocessing.description_file import (
    DESCRIPTION_FILENAME,
    DRAFT_FILENAME,
    read_description,
    sha256_hex,
)
from morphobricks_preprocessing.description_rules import Code, Refusal
from morphobricks_preprocessing.draft import DraftError, draft_from_discovery
from morphobricks_preprocessing.draft_answers import (
    ChannelAnswer,
    ConditionAnswer,
    DraftAnswers,
    DraftIncomplete,
    RepeatAnswer,
    UnitAnswer,
    description_from_draft,
)
from morphobricks_preprocessing.draft_format import (
    ABSENT,
    SPEC_COMMIT,
    DraftFileError,
    answers_to_bytes,
)
from morphobricks_preprocessing.run.description_file import (
    PublicationRefused,
    publish_description,
)
from morphobricks_preprocessing.run.draft_file import (
    load_draft,
    new_answers,
    publish_draft,
    save_draft,
)
from morphobricks_preprocessing.run.lock import LOCK_FILENAME, OutputLockedError, output_lock
from tiff_tree import make_tree


@pytest.fixture
def exp(tmp_path):
    make_tree(tmp_path / "exp", PATHS)
    return discover_experiment(tmp_path / "exp")


@pytest.fixture
def out(tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()
    return folder


def test_save_then_load_resumes_the_same_answers_and_leaves_no_other_file(exp, out):
    base = draft_from_discovery(exp)
    path = save_draft(out, base, complete())
    assert path.name == DRAFT_FILENAME != DESCRIPTION_FILENAME
    assert [p.name for p in out.iterdir()] == [DRAFT_FILENAME]  # no lock, no temporary file left
    resumed = load_draft(out, base)
    assert resumed.answers == complete()
    assert resumed.draft.conditions["DrugA"].confirmed == "DrugA"
    assert resumed.draft.references == {"DrugA": "CTRL"}
    assert resumed.draft.repeats["rep2"].confirmed_on == "2026-11-05"
    assert base.conditions["DrugA"].confirmed is None  # the draft given is never changed
    before = snapshot(out)
    save_draft(out, base, complete())
    assert snapshot(out) == before


def test_without_a_saved_draft_nothing_is_resumed(exp, out):
    assert load_draft(out, draft_from_discovery(exp)) is None


def test_a_new_draft_records_the_published_base_or_absent(out):
    assert new_answers(out).base_sha256 == ABSENT
    published = b"x"
    (out / DESCRIPTION_FILENAME).write_bytes(published)
    assert new_answers(out).base_sha256 == sha256_hex(published)
    assert new_answers(out).spec_commit == SPEC_COMMIT


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_a_draft_file_that_is_not_exactly_the_format_is_refused_never_repaired(name, exp, out):
    data, code = REFUSED[name]
    (out / DRAFT_FILENAME).write_bytes(data)
    with pytest.raises(DraftFileError) as on_disk:
        load_draft(out, draft_from_discovery(exp))
    assert on_disk.value.code == code
    with pytest.raises(DraftFileError):
        publish_draft(out, draft_from_discovery(exp))
    assert (out / DRAFT_FILENAME).read_bytes() == data  # not repaired, not deleted
    assert not (out / DESCRIPTION_FILENAME).exists()


def test_a_symbolic_link_is_never_followed_or_replaced(exp, out, tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_bytes(answers_to_bytes(complete()))
    try:
        (out / DRAFT_FILENAME).symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow symbolic links")
    base = draft_from_discovery(exp)
    for action in (lambda: load_draft(out, base), lambda: save_draft(out, base, complete())):
        with pytest.raises(DraftFileError) as refusal:
            action()
        assert refusal.value.code == Refusal.R_FILE_SYMLINK
    assert (out / DRAFT_FILENAME).is_symlink()
    assert target.read_bytes() == answers_to_bytes(complete())


# --- resume against the base ------------------------------------------------------------------


def test_resume_is_refused_when_the_published_file_changed_since_the_base(exp, out):
    base = draft_from_discovery(exp)
    save_draft(out, base, complete())  # base: absent
    published = out / DESCRIPTION_FILENAME
    published.write_text("something else")
    kept = snapshot(out)
    with pytest.raises(PublicationRefused) as refusal:
        load_draft(out, base)
    assert refusal.value.codes == {Refusal.R_PUBLISHED_FILE_CHANGED}
    with pytest.raises(PublicationRefused):
        publish_draft(out, base)
    assert snapshot(out) == kept


def test_resume_is_refused_when_the_file_the_draft_was_based_on_is_gone_or_replaced(exp, out):
    base = draft_from_discovery(exp)
    published = out / DESCRIPTION_FILENAME
    published.write_bytes(b"first")
    save_draft(out, base, complete(sha256_hex(b"first")))
    assert load_draft(out, base) is not None  # still the base
    published.write_bytes(b"first ")  # one byte more
    with pytest.raises(PublicationRefused) as changed:
        load_draft(out, base)
    assert changed.value.codes == {Refusal.R_PUBLISHED_FILE_CHANGED}
    published.unlink()
    with pytest.raises(PublicationRefused) as gone:
        load_draft(out, base)
    assert gone.value.codes == {Refusal.R_PUBLISHED_FILE_CHANGED}


def test_a_draft_with_no_base_cannot_be_resumed_or_published(exp, out):
    (out / DRAFT_FILENAME).write_bytes(REFUSED["base_missing"][0])
    with pytest.raises(DraftFileError) as refusal:
        load_draft(out, draft_from_discovery(exp))
    assert refusal.value.code == Refusal.R_DRAFT_BASE_MISSING


def test_resume_refuses_an_answer_for_a_folder_the_draft_no_longer_has(exp, out, tmp_path):
    save_draft(out, draft_from_discovery(exp), complete())
    make_tree(tmp_path / "other", [p for p in PATHS if not p.startswith("DrugA/rep2")])
    smaller = draft_from_discovery(discover_experiment(tmp_path / "other"))
    with pytest.raises(DraftError) as refusal:
        load_draft(out, smaller)
    assert refusal.value.codes == ("unknown_key",)
    assert "DrugA / rep2 / single" in str(refusal.value)


# --- what saving refuses ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answers", "code"),
    [
        (
            DraftAnswers(ABSENT, SPEC_COMMIT, conditions={"Nope": ConditionAnswer(id="X")}),
            "unknown_key",
        ),
        (
            DraftAnswers(
                ABSENT, SPEC_COMMIT, conditions={"CTRL": ConditionAnswer(reference="Nope")}
            ),
            "unknown_key",
        ),
        (DraftAnswers(ABSENT, SPEC_COMMIT, technical_repeats={"t1": "t1"}), "unknown_key"),
        (
            DraftAnswers(ABSENT, SPEC_COMMIT, repeats={"rep9": RepeatAnswer(id="rep9")}),
            "unknown_key",
        ),
        (
            DraftAnswers(ABSENT, SPEC_COMMIT, units={("CTRL", "rep1", "t1"): UnitAnswer(("DNA",))}),
            "unknown_key",
        ),
        (
            DraftAnswers(ABSENT, SPEC_COMMIT, conditions={"CTRL": ConditionAnswer(id="control")}),
            "E_FORBIDDEN_FIELD",
        ),
        (
            DraftAnswers(
                ABSENT, SPEC_COMMIT, repeats={"rep1": RepeatAnswer(confirmed_on="2026-02-30")}
            ),
            "E_DATE_FORMAT",
        ),
        (
            DraftAnswers(
                ABSENT,
                SPEC_COMMIT,
                conditions={"CTRL": ConditionAnswer(id="X"), "DrugA": ConditionAnswer(id="x")},
            ),
            "E_ID_CASE_COLLISION",
        ),
    ],
)
def test_an_answer_the_draft_refuses_is_never_saved(answers, code, exp, out):
    with pytest.raises(DraftError) as refusal:
        save_draft(out, draft_from_discovery(exp), answers)
    assert code in refusal.value.codes
    assert list(out.iterdir()) == []


def test_answers_that_cannot_be_a_file_are_never_saved(exp, out):
    for answers in (
        DraftAnswers("nonsense", SPEC_COMMIT),
        DraftAnswers(ABSENT, "main"),
        DraftAnswers(ABSENT, SPEC_COMMIT, channels={"DNA": ChannelAnswer("bad\ud800")}),
    ):
        with pytest.raises(DraftFileError):
            save_draft(out, draft_from_discovery(exp), answers)
    assert list(out.iterdir()) == []


def test_saving_is_refused_while_a_run_holds_the_lock(exp, out):
    with output_lock(out), pytest.raises(OutputLockedError):
        save_draft(out, draft_from_discovery(exp), complete())
    assert list(out.iterdir()) == []


def test_an_incomplete_draft_can_be_saved_after_each_answer_and_resumed(exp, out):
    base = draft_from_discovery(exp)
    first = DraftAnswers(ABSENT, SPEC_COMMIT, conditions={"CTRL": ConditionAnswer(id="CTRL")})
    save_draft(out, base, first)
    second = DraftAnswers(
        ABSENT,
        SPEC_COMMIT,
        conditions={"CTRL": ConditionAnswer(id="CTRL")},
        repeats={"rep1": RepeatAnswer(confirmed_on=DAY)},
    )
    save_draft(out, base, second)
    resumed = load_draft(out, base)
    assert resumed.answers == second
    assert resumed.draft.repeats["rep1"].confirmed
    assert resumed.draft.repeats["rep1"].id.confirmed is None  # the ID is a separate answer


def test_the_date_is_the_users_never_the_clock(exp, out):
    save_draft(out, draft_from_discovery(exp), complete())
    publish_draft(out, draft_from_discovery(exp))
    text = (out / DESCRIPTION_FILENAME).read_text(encoding="utf-8")
    assert date.today().isoformat() not in text
    assert f'confirmed_on = "{DAY}"' in text
    assert 'confirmed_on = "2026-11-05"' in text


# --- publication ------------------------------------------------------------------------------


def test_a_complete_draft_is_published_and_the_draft_is_deleted(exp, out):
    save_draft(out, draft_from_discovery(exp), complete())
    publication = publish_draft(out, draft_from_discovery(exp))
    assert publication.base_sha256 == ABSENT
    assert sorted(p.name for p in out.iterdir()) == [DESCRIPTION_FILENAME]  # draft and lock gone
    published = read_description(out / DESCRIPTION_FILENAME)
    assert published == description_from_draft(draft_from_discovery(exp), complete())
    assert publication.sha256 == sha256_hex((out / DESCRIPTION_FILENAME).read_bytes())
    assert load_draft(out, draft_from_discovery(exp)) is None


def test_a_draft_based_on_a_published_file_publishes_over_it(exp, out):
    description = description_from_draft(draft_from_discovery(exp), complete())
    first = publish_description(out, description, base_sha256=ABSENT)
    answers = complete(first.sha256)
    save_draft(out, draft_from_discovery(exp), answers)
    publication = publish_draft(out, draft_from_discovery(exp))
    assert publication.base_sha256 == first.sha256
    assert not (out / DRAFT_FILENAME).exists()


def refused_leaves_everything(out, action, error):
    before = snapshot(out)
    with pytest.raises(error) as refusal:
        action()
    assert snapshot(out) == before  # draft and published file byte-identical, nothing added
    assert not (out / LOCK_FILENAME).exists()
    return refusal.value


def test_an_incomplete_draft_is_refused_and_nothing_is_written(exp, out):
    answers = complete()
    save_draft(
        out, draft_from_discovery(exp), DraftAnswers(ABSENT, SPEC_COMMIT, answers.conditions)
    )
    refusal = refused_leaves_everything(
        out, lambda: publish_draft(out, draft_from_discovery(exp)), DraftIncomplete
    )
    assert ("unit_channels_missing", "CTRL / rep1 / single") in {
        (q.code, q.subject) for q in refusal.questions
    }
    assert (out / DRAFT_FILENAME).exists()
    assert not (out / DESCRIPTION_FILENAME).exists()


def test_an_incomplete_draft_leaves_an_earlier_published_file_alone(exp, out):
    first = publish_description(
        out, description_from_draft(draft_from_discovery(exp), complete()), base_sha256=ABSENT
    )
    save_draft(out, draft_from_discovery(exp), DraftAnswers(first.sha256, SPEC_COMMIT))
    refused_leaves_everything(
        out, lambda: publish_draft(out, draft_from_discovery(exp)), DraftIncomplete
    )
    assert sha256_hex((out / DESCRIPTION_FILENAME).read_bytes()) == first.sha256


def test_a_description_the_strict_reader_refuses_writes_nothing(exp, out):
    answers = complete()
    channels = {**answers.channels, "bad id": ChannelAnswer()}
    units = {key: UnitAnswer(("bad id", "ACTIN")) for key in UNITS}
    bad = DraftAnswers(
        ABSENT,
        SPEC_COMMIT,
        answers.conditions,
        answers.technical_repeats,
        answers.repeats,
        units,
        channels,
        answers.masks,
    )
    save_draft(out, draft_from_discovery(exp), bad)
    from morphobricks_preprocessing.description import DescriptionError

    refusal = refused_leaves_everything(
        out, lambda: publish_draft(out, draft_from_discovery(exp)), DescriptionError
    )
    assert Code.E_ID_SYNTAX in {issue.code for issue in refusal.issues}


def test_a_changed_published_file_refuses_publication_and_keeps_the_draft(exp, out):
    save_draft(out, draft_from_discovery(exp), complete())
    (out / DESCRIPTION_FILENAME).write_text("changed by someone else")
    refusal = refused_leaves_everything(
        out, lambda: publish_draft(out, draft_from_discovery(exp)), PublicationRefused
    )
    assert refusal.codes == {Refusal.R_PUBLISHED_FILE_CHANGED}


def test_a_held_lock_refuses_publication_and_keeps_the_draft(exp, out):
    save_draft(out, draft_from_discovery(exp), complete())
    before = snapshot(out)
    with output_lock(out), pytest.raises(OutputLockedError):
        publish_draft(out, draft_from_discovery(exp))
    assert snapshot(out) == before


def test_publication_without_a_draft_is_refused(exp, out):
    with pytest.raises(DraftFileError) as refusal:
        publish_draft(out, draft_from_discovery(exp))
    assert refusal.value.code == "no_draft"
    assert list(out.iterdir()) == []
