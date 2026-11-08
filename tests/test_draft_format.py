"""The bytes of ``morphobricks.draft.json``: canonical writing and strict reading (contract 18)."""

import pytest

from draft_file_support import (
    REFUSED,
    complete,
)
from morphobricks_preprocessing.draft_answers import (
    DraftAnswers,
)
from morphobricks_preprocessing.draft_format import (
    ABSENT,
    SPEC_COMMIT,
    DraftFileError,
    answers_from_bytes,
    answers_to_bytes,
)
from morphobricks_preprocessing.run.draft_file import (
    new_answers,
)
from spec_pin import read_pin

# --- format: round trip -----------------------------------------------------------------------


def test_the_answers_round_trip_through_the_file_bytes():
    answers = complete()
    data = answers_to_bytes(answers)
    assert data.endswith(b"}\n")
    assert answers_from_bytes(data) == answers
    assert answers_to_bytes(answers_from_bytes(data)) == data  # deterministic bytes


def test_an_unanswered_draft_is_a_valid_file():
    empty = DraftAnswers(ABSENT, SPEC_COMMIT)
    assert answers_from_bytes(answers_to_bytes(empty)) == empty


def test_the_spec_commit_recorded_is_the_pinned_one():
    assert SPEC_COMMIT == read_pin()["commit"]
    assert new_answers.__module__.endswith("draft_file")


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_draft_bytes_that_are_not_exactly_the_format_are_refused_never_repaired(name):
    data, code = REFUSED[name]
    with pytest.raises(DraftFileError) as refusal:
        answers_from_bytes(data)
    assert refusal.value.code == code
