"""Tag by repeat: what never releases a refused unit unasked (review of PR #376, 22 November).

A refused unit counts only once the user confirms THAT unit (owner decision of 22 November).
Neither a change of its row's reference, nor a confirmation of another unit in one save, nor a
change of its channel count, nor a change of its own header lets it count unasked.
"""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from test_notebook_check_save import opened  # noqa: E402
from test_notebook_tag_by_repeat_oneclick import LEAD, OTHER, THIRD  # noqa: E402
from test_notebook_tag_by_repeat_recheck import _recheck  # noqa: E402
from test_notebook_tag_by_repeat_review import _saved_fast  # noqa: E402


def _untagged(nb, key):
    own = nb.draft_answers.units.get(key)
    return own is None or not any(own.channels)


# Blocker 1: a reference change ----------------------------------------------------------------


def _reference_gone(tmp_path):
    """GONE and OTHER refused (P, Q against X, Y), GONE confirmed; the X, Y units are removed."""
    nb, root, masks, out = _saved_fast(tmp_path)
    for key in (LEAD, THIRD):  # every unit before GONE in the row
        shutil.rmtree(root.joinpath(*key))
    return nb, root, masks, out


def test_a_reference_change_never_lets_fast_mode_tag_or_clear_a_refused_unit(tmp_path):
    nb, *_ = _reference_gone(tmp_path)
    rows = nb.channel_grid.by_repeat
    _recheck(nb)  # GONE is the row's reference now, with OTHER's very header names
    assert _untagged(nb, OTHER) and OTHER not in nb.channel_grid.matrix.own
    assert OTHER in rows.standing()  # unconfirmed: never trusted, whatever the reference
    assert OTHER in rows.refused and OTHER in rows.listed()
    assert not nb.channel_grid.complete and not nb.save_experiment()


def test_a_reference_change_after_a_restart_keeps_the_refused_unit_listed(tmp_path):
    _, root, masks, out = _reference_gone(tmp_path)
    nb = opened(root, masks, out)  # a new notebook on the saved draft
    rows = nb.channel_grid.by_repeat
    assert nb.channel_grid.same_channels.value
    assert _untagged(nb, OTHER) and OTHER in rows.listed() and OTHER in rows.refused
    assert not nb.save_experiment()
    assert THIRD not in rows.refused
