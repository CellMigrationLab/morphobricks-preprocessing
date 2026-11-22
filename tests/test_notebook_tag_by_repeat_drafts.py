"""The tag review belongs to one draft: another folder or output never inherits it.

Owner rule (22 November): a refused unit never counts until the user confirms that unit, and
aliases saved for other units never tag or clear it, also across restarts and draft switches.
TagGrid and RepeatRows live for the whole notebook, so the refusals and confirmations in memory
are tied to the draft they came from (``_NotebookStore.draft_id``).
"""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from dataclasses import replace  # noqa: E402

from morphobricks_preprocessing.description_file import DRAFT_FILENAME  # noqa: E402
from morphobricks_preprocessing.draft_format import answers_from_bytes, answers_to_bytes  # noqa: E402
from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import opened  # noqa: E402
from test_notebook_tag_by_repeat import EXTRA  # noqa: E402
from test_notebook_tag_by_repeat_oneclick import GONE, OTHER  # noqa: E402
from test_notebook_tag_by_repeat_review import _saved_fast  # noqa: E402


def _review(out):
    return answers_from_bytes((out / DRAFT_FILENAME).read_bytes()).tag_review


def _copy_experiment(root, out, tmp_path, name):
    """A second experiment folder and output (B) with the draft of A as it was saved."""
    root2, out2 = tmp_path / f"exp_{name}", tmp_path / f"out_{name}"
    shutil.copytree(root, root2)
    out2.mkdir()
    shutil.copy(out / DRAFT_FILENAME, out2 / DRAFT_FILENAME)
    return root2, out2


def _point(nb, masks, root, out):
    """Point the same notebook at another experiment folder and output, and reach the grid."""
    nb.masks_folder.value = ""
    nb.folder.value = str(root)
    nb.output.value = str(out)
    nb.check_button.click()
    nb.masks_folder.value = str(masks)
    nb.check_button.click()
    nb.preview_checked.set(True)
    nb.show_preview()
    nb.confirm_button.click()
    if not nb.channel_labels.confirmed:  # a draft without labels: the user names them first
        set_labels(nb, list(EXTRA))


def _untagged(nb, key):
    own = nb.draft_answers.units.get(key)
    return own is None or not any(own.channels)


def test_a_draft_with_a_review_replaces_the_memory_of_the_draft_before_it(tmp_path):
    nb, root, masks, out = _saved_fast(tmp_path)  # A: GONE confirmed, OTHER refused
    root_b, out_b = _copy_experiment(root, out, tmp_path, "b")
    # B's own record: only OTHER refused, nothing confirmed (as another session left it)
    review_b = replace(_review(out_b), confirmed={}, refused={OTHER: _review(out_b).refused[OTHER]})
    answers_b = answers_from_bytes((out_b / DRAFT_FILENAME).read_bytes())
    (out_b / DRAFT_FILENAME).write_bytes(answers_to_bytes(replace(answers_b, tag_review=review_b)))
    rows = nb.channel_grid.by_repeat
    assert set(rows.override.confirmed) == {GONE}  # A's confirmation, in memory
    _point(nb, masks, root_b, out_b)
    assert set(rows.refused) == {OTHER} and not rows.override.confirmed  # B's, not A's
    assert OTHER in rows.listed() and _untagged(nb, OTHER)  # not tagged from A's aliases
    assert set(_review(out_b).refused) == {OTHER} and not _review(out_b).confirmed
    assert not rows.override.editing and not rows.override.drafts


def test_a_draft_without_a_review_clears_the_memory_and_shows_nothing_of_the_other(tmp_path):
    nb, root, masks, out = _saved_fast(tmp_path)
    root_b, out_b = tmp_path / "exp_c", tmp_path / "out_c"
    shutil.copytree(root, root_b)
    out_b.mkdir()  # no draft at all in B
    rows = nb.channel_grid.by_repeat
    assert set(rows.refused) == {GONE, OTHER}
    _point(nb, masks, root_b, out_b)
    assert not rows.refused and not rows.override.confirmed and not rows.listed()
    assert "not filled" not in rows.message and not rows.notice
    review = (
        answers_from_bytes((out_b / DRAFT_FILENAME).read_bytes()).tag_review
        if (out_b / DRAFT_FILENAME).exists()
        else None
    )
    assert review is None or (not review.refused and not review.confirmed)


def test_switching_back_restores_the_record_of_the_first_draft_from_its_file(tmp_path):
    nb, root, masks, out = _saved_fast(tmp_path)
    saved_a = _review(out)
    root_b, out_b = tmp_path / "exp_d", tmp_path / "out_d"
    shutil.copytree(root, root_b)
    out_b.mkdir()
    rows = nb.channel_grid.by_repeat
    _point(nb, masks, root_b, out_b)
    assert not rows.refused
    _point(nb, masks, root, out)  # back to A
    assert _review(out) == saved_a  # B never wrote into A's file
    assert set(rows.refused) == {GONE, OTHER} and set(rows.override.confirmed) == {GONE}
    assert set(rows.listed()) == {OTHER} and _untagged(nb, OTHER)


def test_a_failed_save_of_the_review_is_said_on_the_grid_and_retried(tmp_path, monkeypatch):
    nb, root, masks, out = _saved_fast(tmp_path)
    rows = nb.channel_grid.by_repeat
    from morphobricks_preprocessing import notebook_description as nd

    def refuse(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(nd, "save_draft", refuse)
    before = (out / DRAFT_FILENAME).read_bytes()
    rows.refused.pop(OTHER)  # the review changes in memory; the save is refused
    assert not rows.persist() and rows.unsaved
    nb.channel_grid.refresh()
    assert "Not saved" in rows.message
    assert (out / DRAFT_FILENAME).read_bytes() == before  # the file is as it was
    monkeypatch.undo()
    assert rows.persist() and not rows.unsaved  # retried at the next persist
    assert OTHER not in _review(out).refused
    nb.channel_grid.refresh()
    assert "Not saved" not in rows.message


def test_stale_entries_after_the_folder_changed_are_dropped_or_re_referenced(tmp_path):
    nb, root, masks, out = _saved_fast(tmp_path)
    ref = _review(out).refused[OTHER].reference
    assert ref == ("ctrl", "rep2", "t1")
    shutil.rmtree(root / "ctrl" / "rep2" / "t1")  # OTHER's reference is removed
    shutil.rmtree(root / "drug" / "rep2" / "t1")  # GONE (a refused unit) is removed
    for key in ("ctrl/rep2/t1", "drug/rep2/t1"):
        shutil.rmtree(masks / "Nuclei" / key, ignore_errors=True)
    fresh = opened(root, masks, out)  # no crash
    rows = fresh.channel_grid.by_repeat
    assert GONE not in rows.refused and GONE not in rows.override.confirmed
    assert OTHER in rows.refused and OTHER in rows.listed()  # survivor, still listed
    assert rows.refused[OTHER].lead is not None and rows.refused[OTHER].lead.key != ref
    review = _review(out)
    assert set(review.refused) == {OTHER} and review.refused[OTHER].reference != ref
    assert _untagged(fresh, OTHER)
