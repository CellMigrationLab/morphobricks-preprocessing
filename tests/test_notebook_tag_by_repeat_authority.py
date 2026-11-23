"""Tag by repeat: an explicit action on a refused unit is the only release (review am, 23 Nov).

The owner rule: a refused unit is never tagged, counted, unlisted or saveable without an
explicit action on that unit (its editor's Confirm, a one-click confirmation, a deliberate
hand-tag of that unit, or a visibly scoped strict-mode block click). A header or an alias that
matches never releases a refusal on its own, and only a unit removed from the folder drops its
record.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from test_notebook_check_save import answer_all  # noqa: E402
from test_notebook_tag_by_repeat_oneclick import GONE, OTHER, _strict  # noqa: E402


def _hand(nb, key, tags):
    """Tag one unit by hand, on its own (out of its block)."""
    nb.channel_grid.split(key)
    for index, tag in enumerate(tags):
        nb.channel_grid.cells[(key, index)].value = tag


# The Check-and-save button after the last confirmation ----------------------------------------


def test_the_save_button_opens_right_after_the_last_confirmation(tmp_path, statement_off):
    nb, _, rows = _strict(tmp_path, statement_off)
    rows.override.buttons[GONE].click()
    rows.override.confirm_button.click()  # GONE: the row's tags
    _hand(nb, OTHER, ("ACTIN", "DNA", "PHASE"))  # contradicts the aliases GONE saved: listed
    answer_all(nb)
    assert set(rows.listed()) == {OTHER} and nb.save_button.disabled
    rows.override.buttons[OTHER].click()
    for index, tag in enumerate(("ACTIN", "DNA", "PHASE")):
        rows.override.cells[(OTHER, index)].value = tag
    rows.override.confirm_button.click()  # the last listed unit, confirmed as an override
    assert not rows.listed()
    # at 7e9daf4 the confirmation was noted after the save had refreshed the button: disabled
    assert not nb.save_button.disabled
