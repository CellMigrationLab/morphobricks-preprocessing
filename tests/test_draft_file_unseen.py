"""A saved draft is never published over, and never copied aside by a refused save (ay).

External re-check ay (24 November 2026):

- BLOCKER: ``publish_description`` (the command line's ``describe --publish``) published a new
  description while ``morphobricks.draft.json`` held answers it never read, refusals of a tag
  review among them. It is now refused (``R_DRAFT_EXISTS``) under the output folder's lock while
  a draft exists; ``publish_draft``, which publishes that very draft, is unchanged.
- FILE: the notebook copied an unusable draft aside before the generation check of its save,
  so a stale save that was then refused had copied another notebook's newer draft. The copy is
  now part of ``save_draft`` (``keep_previous``), made after the check under the same lock.
"""

import os

import pytest

from draft_file_support import COUNTS, PATHS, complete, snapshot
from morphobricks_preprocessing import discover_experiment
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME, DRAFT_FILENAME
from morphobricks_preprocessing.design import DESIGN_FILENAME
from morphobricks_preprocessing.draft import draft_from_discovery
from morphobricks_preprocessing.draft_answers import DraftAnswers, description_from_draft
from morphobricks_preprocessing.draft_format import ABSENT, SPEC_COMMIT
from morphobricks_preprocessing.run import description_file
from morphobricks_preprocessing.run.description_file import publish_description
from morphobricks_preprocessing.run.draft_file import (
    DraftChanged,
    draft_generation,
    publish_draft,
    save_draft,
)
from tiff_tree import make_tree

UNUSED = "morphobricks.draft.unused-{}.json"


@pytest.fixture
def folders(tmp_path):
    """The experiment of ``test_draft_file`` (discovered) and an empty output folder."""
    make_tree(tmp_path / "exp", PATHS)
    (tmp_path / "out").mkdir()
    return discover_experiment(tmp_path / "exp"), tmp_path / "out"


def _candidate(exp):
    return description_from_draft(draft_from_discovery(exp), complete())


def test_a_publication_without_a_draft_still_publishes(folders):
    exp, out = folders
    publication = publish_description(out, _candidate(exp), base_sha256=ABSENT, write_design=True)
    assert publication.path == out / DESCRIPTION_FILENAME
    assert (out / DESIGN_FILENAME).exists()


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_a_publication_over_a_saved_draft_is_refused_and_writes_nothing(folders, kind):
    exp, out = folders
    if kind == "file":
        save_draft(out, draft_from_discovery(exp), complete())
    else:
        target = out.parent / "elsewhere.json"
        target.write_bytes(b"{}")
        try:
            os.symlink(target, out / DRAFT_FILENAME)
        except (OSError, NotImplementedError):
            pytest.skip("symbolic links cannot be made here")
    before = snapshot(out)
    with pytest.raises(description_file.DraftExists) as caught:
        publish_description(out, _candidate(exp), base_sha256=ABSENT, write_design=True)
    assert caught.value.code == "R_DRAFT_EXISTS" and isinstance(caught.value, ValueError)
    message = str(caught.value)
    assert DRAFT_FILENAME in message and "R_DRAFT_EXISTS" in message
    assert "publish them in the notebook" in message and "remove or archive" in message
    assert snapshot(out) == before  # the draft byte for byte, no TOML, no design, no lock


def test_the_draft_is_checked_under_the_lock(folders, monkeypatch):
    """A draft saved while the publication waited for the lock is seen too."""
    exp, out = folders
    real = description_file.output_lock

    def lock_then_draft(folder):
        save_draft(out, draft_from_discovery(exp), complete())  # just before the lock is taken
        return real(folder)

    monkeypatch.setattr(description_file, "output_lock", lock_then_draft)
    with pytest.raises(description_file.DraftExists):
        publish_description(out, _candidate(exp), base_sha256=ABSENT)
    assert not (out / DESCRIPTION_FILENAME).exists()


def test_publish_draft_still_publishes_its_own_draft(folders):
    exp, out = folders
    save_draft(out, draft_from_discovery(exp), complete())
    expected = draft_generation(out)
    publish_draft(out, draft_from_discovery(exp), channel_counts=COUNTS, expected=expected)
    assert (out / DESCRIPTION_FILENAME).exists() and not (out / DRAFT_FILENAME).exists()


# --- FILE: the unused draft is copied only by a save that passes its generation check --------


def test_a_refused_stale_save_copies_nothing_aside(folders):
    exp, out = folders
    base = draft_from_discovery(exp)
    save_draft(out, base, complete())
    held = draft_generation(out)  # the unusable draft this notebook read
    newer = DraftAnswers(ABSENT, SPEC_COMMIT)  # another notebook's save
    save_draft(out, base, newer, expected=held)
    before = snapshot(out)
    with pytest.raises(DraftChanged):
        save_draft(out, base, complete(), expected=held, keep_previous=True)
    assert snapshot(out) == before and not list(out.glob("*.unused-*"))


def test_a_save_that_passes_copies_the_draft_aside_first_never_over_a_copy(folders):
    exp, out = folders
    base = draft_from_discovery(exp)
    save_draft(out, base, complete())
    previous = (out / DRAFT_FILENAME).read_bytes()
    (out / UNUSED.format(1)).write_bytes(b"an earlier copy")
    empty = DraftAnswers(ABSENT, SPEC_COMMIT)
    save_draft(out, base, empty, expected=draft_generation(out), keep_previous=True)
    assert (out / UNUSED.format(1)).read_bytes() == b"an earlier copy"  # never overwritten
    assert (out / UNUSED.format(2)).read_bytes() == previous
    assert (out / DRAFT_FILENAME).read_bytes() != previous


def test_a_copy_that_cannot_be_written_replaces_nothing(folders, monkeypatch):
    from morphobricks_preprocessing.run import draft_file

    exp, out = folders
    base = draft_from_discovery(exp)
    save_draft(out, base, complete())
    before = snapshot(out)

    real = draft_file.Path.open

    def refuse(self, mode="r", *args, **kwargs):
        if mode == "xb":  # the copy only; the lock file is opened as before
            raise PermissionError("no room")
        return real(self, mode, *args, **kwargs)

    monkeypatch.setattr(draft_file.Path, "open", refuse)
    empty = DraftAnswers(ABSENT, SPEC_COMMIT)
    with pytest.raises(draft_file.UnusedDraftNotKept):
        save_draft(out, base, empty, expected=draft_generation(out), keep_previous=True)
    assert snapshot(out) == before
