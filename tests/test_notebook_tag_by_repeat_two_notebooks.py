"""Two notebooks on one output folder: a stale one never writes over the other's draft (av).

External re-check av (24 November), blocker 2: each notebook holds the whole draft as it read
it, and every save replaces the whole ``morphobricks.draft.json`` under the output folder's
lock. The lock makes writes one at a time, not current: notebook B, opened before notebook A
saved a refusal (with tags) or a confirmation, wrote its stale answers over A's at its next
save of anything, and the refusal, the confirmation and the tags were gone. The notebook now
pins the draft file's generation (the SHA-256 of its bytes) when it reads the draft and after
each save of its own, and every write and the publication check it under the lock: B is
refused, writes nothing, and says to check the folder again, which reads A's answers.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_description  # noqa: E402
from morphobricks_preprocessing.description_file import DRAFT_FILENAME  # noqa: E402
from morphobricks_preprocessing.draft_format import answers_to_bytes  # noqa: E402
from morphobricks_preprocessing.repeat_review import TagReview  # noqa: E402
from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import answer_all, opened  # noqa: E402
from test_notebook_tag_by_repeat import TAGS, _experiment, _set_row  # noqa: E402
from test_notebook_tag_by_repeat_atomic import REFUSED, Interrupted, _draft  # noqa: E402
from test_notebook_tag_by_repeat_oneclick import LEAD, OTHER, _like  # noqa: E402
from test_notebook_tag_by_repeat_recheck import _recheck  # noqa: E402

CHANGED = "was changed since this notebook read or saved it"


def _bytes(out):
    return (out / DRAFT_FILENAME).read_bytes()


def _two(tmp_path, what):
    """Notebooks A and B on one folder; A then saves a refusal (and tags), or a confirmation.

    Returns A, B, the folders and the draft's bytes after A's save. B read the draft before
    A's save (for a confirmation: after A's refusal, before A confirmed OTHER).
    """
    exp = _experiment(tmp_path, names=_like(REFUSED, False))
    a = opened(*exp)
    set_labels(a, list(TAGS))
    if what == "confirmation":
        _set_row(a, ("rep2", 3), TAGS)  # refuses GONE and OTHER; B reads that
    b = opened(*exp)
    rows = a.channel_grid.by_repeat
    if what == "refusal":
        _set_row(a, ("rep2", 3), TAGS)  # fills LEAD, refuses GONE and OTHER: one write
        assert set(REFUSED) <= set(_draft(exp[2]).tag_review.refused)
        assert _draft(exp[2]).units[LEAD].channels == TAGS
    else:
        rows.override.buttons[OTHER].click()
        rows.override.confirm_button.click()
        assert OTHER in _draft(exp[2]).tag_review.confirmed
    return a, b, exp, _bytes(exp[2])


def _kept(out, saved, what):
    """The draft is A's, byte for byte: its refusal, tags and confirmation are all there."""
    assert _bytes(out) == saved
    review = _draft(out).tag_review
    assert set(REFUSED) <= set(review.refused)
    assert _draft(out).units[LEAD].channels == TAGS
    assert (OTHER in review.confirmed) == (what == "confirmation")


def _b_row_click(b):
    rows = b.channel_grid.by_repeat
    _set_row(b, ("rep1", 3), TAGS)  # another repeat folder: nothing of A's units
    return CHANGED in rows.message or CHANGED in b.channel_grid.message


def _b_reference(b):
    b.reference_boxes["drug"].value = "ctrl"  # another card: the references
    return CHANGED in str(b._refusal) and CHANGED in b.references_status.value


def _b_persist(b):
    rows = b.channel_grid.by_repeat
    # B's memory holds a review its draft does not (as after a refused review write, retried
    # at the next persist): a unit's refusal it no longer holds
    rows.refused.pop(next(iter(rows.refused)), None)
    rows.unsaved = True
    return not rows.persist() and CHANGED in str(b._refusal)


B_CHANGES = {"row": _b_row_click, "reference": _b_reference, "persist": _b_persist}


@pytest.mark.parametrize(
    ("what", "change"),
    [
        ("refusal", "row"),
        ("refusal", "reference"),
        ("confirmation", "row"),
        ("confirmation", "reference"),
        ("confirmation", "persist"),  # B holds A's refusal (read before A's confirmation)
    ],
)
def test_a_stale_notebook_is_refused_and_writes_nothing(tmp_path, statement_off, what, change):
    a, b, exp, saved = _two(tmp_path, what)
    before = b.draft_answers
    said = B_CHANGES[change](b)
    _kept(exp[2], saved, what)  # at 8a9ac63 B wrote its stale draft over A's
    assert said  # B says why and what to do
    assert b.draft_answers == before  # B shows nothing it did not save
    assert not b.save_experiment()  # nor does B's Save write anything
    _kept(exp[2], saved, what)


@pytest.mark.parametrize("what", ["refusal", "confirmation"])
def test_after_checking_the_folder_again_b_acts_and_keeps_a_review(tmp_path, statement_off, what):
    a, b, exp, saved = _two(tmp_path, what)
    said = _b_reference(b)
    _kept(exp[2], saved, what)  # at 8a9ac63 B wrote its stale draft over A's
    assert said
    _recheck(b)  # step 1, Check folder: B reads A's draft
    rows = b.channel_grid.by_repeat
    assert set(REFUSED) - {OTHER if what == "confirmation" else None} <= set(rows.listed())
    assert (OTHER in rows.listed()) == (what == "refusal")
    b.reference_boxes["drug"].value = "ctrl"  # now saved, over A's draft as B read it
    assert b._refusal is None and _draft(exp[2]).conditions["drug"].reference == "ctrl"
    review = _draft(exp[2]).tag_review
    assert set(REFUSED) <= set(review.refused) and (OTHER in review.confirmed) == (
        what == "confirmation"
    )
    assert _draft(exp[2]).units[LEAD].channels == TAGS
    assert not b.save_experiment()  # the refused unit B never acted on still blocks the save
    # A is the stale one now: refused in its turn
    a.reference_boxes["ctrl"].value = "drug"
    assert CHANGED in str(a._refusal)
    assert _draft(exp[2]).conditions.get("ctrl") is None


@pytest.mark.parametrize("when", ["before", "after"])
def test_b_interrupted_around_its_write_never_takes_or_keeps_a_newer_draft(
    tmp_path, monkeypatch, statement_off, when
):
    """B's write stops with another writer in between: B writes nothing more until reopened.

    ``before``: A saves its refusal, then B's save stops before it writes (B is stale, and its
    write would be refused). ``after``: B's save writes (B is current), then another program
    replaces the draft and B stops before its save returns. Either way the file holds neither
    the answers B had nor the ones it saved: B goes back to the answers before and every later
    write of B is refused, until B reads the draft again.
    """
    exp = _experiment(tmp_path, names=_like(REFUSED, False))
    a = opened(*exp)
    set_labels(a, list(TAGS))
    b = opened(*exp)
    out, real = exp[2], notebook_description.save_draft
    other = answers_to_bytes(replace(_draft(out), tag_review=TagReview()))

    def stopped(*args, **kwargs):
        monkeypatch.setattr(notebook_description, "save_draft", real)
        if when == "before":
            _set_row(a, ("rep2", 3), TAGS)  # A's refusal and tags, saved
        else:
            real(*args, **kwargs)
            (out / DRAFT_FILENAME).write_bytes(other + b" ")  # another program's draft
        raise Interrupted

    before = b.draft_answers
    monkeypatch.setattr(notebook_description, "save_draft", stopped)
    with pytest.raises(Interrupted):
        b.reference_boxes["drug"].value = "ctrl"
    assert b.draft_answers == before  # the file holds neither: memory as before the save
    saved = _bytes(out)
    b.reference_boxes["ctrl"].value = "drug"  # B's next write: refused, at 8a9ac63 written
    assert _bytes(out) == saved
    assert CHANGED in str(b._refusal)
    assert not b.save_experiment()
    assert _bytes(out) == saved
    if when == "before":
        _kept(out, saved, "refusal")


def test_b_publication_is_refused_when_the_draft_changed_after_its_save(tmp_path):
    """B's check passed on its draft; A's later save is never published on B's check."""
    exp = _experiment(tmp_path)
    b = opened(*exp)
    answer_all(b)
    publish = b._publish

    def a_saves_first():
        a = opened(*exp)  # A reads B's draft as B just saved it: A is current
        a.reference_boxes["drug"].value = ""  # A's answer lands between B's save and publish
        assert a._refusal is None
        return publish()

    b._publish = a_saves_first
    saved = b.save_experiment()
    assert not (exp[2] / "morphobricks.toml").exists()  # at 8a9ac63 published on B's check
    assert not saved and CHANGED in b.save_status.value
    assert _draft(exp[2]).conditions["drug"].reference == ""  # A's answer, kept as a draft
