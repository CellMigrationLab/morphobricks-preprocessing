"""A draft written under another Spec commit is refused, never resumed or published (#223)."""

import json
from dataclasses import replace

import pytest

from draft_file_support import PATHS, complete, snapshot
from morphobricks_preprocessing import discover_experiment
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME, DRAFT_FILENAME
from morphobricks_preprocessing.draft import draft_from_discovery
from morphobricks_preprocessing.draft_format import SPEC_COMMIT, DraftFileError
from morphobricks_preprocessing.run.draft_file import (
    load_draft,
    new_answers,
    publish_draft,
    save_draft,
)
from tiff_tree import make_tree

OTHER = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def exp(tmp_path):
    make_tree(tmp_path / "exp", PATHS)
    return discover_experiment(tmp_path / "exp")


@pytest.fixture
def out(tmp_path):
    return tmp_path.joinpath("out").resolve(strict=False).absolute()


@pytest.fixture(autouse=True)
def _make_out(out):
    out.mkdir()


def stale(out, exp):
    """Save a complete draft, then change only its spec_commit on disk."""
    save_draft(out, draft_from_discovery(exp), complete())
    path = out / DRAFT_FILENAME
    doc = json.loads(path.read_bytes())
    assert doc["spec_commit"] == SPEC_COMMIT != OTHER
    doc["spec_commit"] = OTHER
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def test_load_refuses_a_draft_of_another_spec_commit(exp, out):
    stale(out, exp)
    before = snapshot(out)
    with pytest.raises(DraftFileError) as refusal:
        load_draft(out, draft_from_discovery(exp))
    assert refusal.value.code == "spec_commit_mismatch"
    message = str(refusal.value)
    assert OTHER in message and SPEC_COMMIT in message
    assert "untouched" in message and "fresh draft" in message
    assert snapshot(out) == before  # the stored commit is never rewritten


def test_publish_refuses_and_leaves_an_absent_description_absent(exp, out):
    stale(out, exp)
    before = snapshot(out)
    with pytest.raises(DraftFileError) as refusal:
        publish_draft(out, draft_from_discovery(exp))
    assert refusal.value.code == "spec_commit_mismatch"
    assert not (out / DESCRIPTION_FILENAME).exists()
    assert snapshot(out) == before


def test_publish_refuses_and_keeps_an_existing_description_byte_identical(exp, out):
    save_draft(out, draft_from_discovery(exp), complete())
    publish_draft(out, draft_from_discovery(exp))
    published = (out / DESCRIPTION_FILENAME).read_bytes()
    answers = new_answers(out)
    assert answers.base_sha256 != "absent"
    save_draft(out, draft_from_discovery(exp), complete(answers.base_sha256))
    path = out / DRAFT_FILENAME
    doc = json.loads(path.read_bytes())
    doc["spec_commit"] = OTHER
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    before = snapshot(out)
    with pytest.raises(DraftFileError) as refusal:
        publish_draft(out, draft_from_discovery(exp))
    assert refusal.value.code == "spec_commit_mismatch"
    assert (out / DESCRIPTION_FILENAME).read_bytes() == published
    assert snapshot(out) == before


def test_save_refuses_answers_of_another_spec_commit_and_writes_nothing(exp, out):
    answers = replace(complete(), spec_commit=OTHER)
    with pytest.raises(DraftFileError) as refusal:
        save_draft(out, draft_from_discovery(exp), answers)
    assert refusal.value.code == "spec_commit_mismatch"
    assert list(out.iterdir()) == []


def test_save_refusal_keeps_an_earlier_draft_byte_identical(exp, out):
    save_draft(out, draft_from_discovery(exp), complete())
    before = snapshot(out)
    answers = replace(complete(), spec_commit=OTHER)
    with pytest.raises(DraftFileError):
        save_draft(out, draft_from_discovery(exp), answers)
    assert snapshot(out) == before


def test_a_draft_with_the_current_commit_round_trips_and_publishes(exp, out):
    save_draft(out, draft_from_discovery(exp), complete())
    assert load_draft(out, draft_from_discovery(exp)).answers.spec_commit == SPEC_COMMIT
    publication = publish_draft(out, draft_from_discovery(exp))
    assert publication is not None
    assert (out / DESCRIPTION_FILENAME).exists()
    assert not (out / DRAFT_FILENAME).exists()
