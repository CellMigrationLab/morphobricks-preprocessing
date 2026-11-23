"""Tag by repeat: a row click saves its tags and its refusals in ONE draft write (review an).

External re-check of 23 November, blocker 1: a row click that filled some units (A) and refused
others (B) wrote A's tags first and B's refusal in a second write. A failure or an interruption
between the two left a draft with A's tags and no refusal of B; after a restart nothing kept B
out of fast mode's header trust. The draft must hold A's tags and B's refusal, or neither.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing.description_file import DRAFT_FILENAME  # noqa: E402
from morphobricks_preprocessing.draft_answers import ChannelAnswer  # noqa: E402
from morphobricks_preprocessing.draft_format import answers_from_bytes  # noqa: E402
from morphobricks_preprocessing.repeat_review import TagReview  # noqa: E402
from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import opened  # noqa: E402
from test_notebook_tag_by_repeat import EXTRA, TAGS, _experiment  # noqa: E402
from test_notebook_tag_by_repeat_oneclick import GONE, LEAD, OTHER, _like  # noqa: E402

REFUSED = (GONE, OTHER)
"""B: their header names P, Q contradict the row's reference (X, Y): the row refuses them."""


class Interrupted(BaseException):
    """The kernel stopped during a write (as ``KeyboardInterrupt`` would)."""


def _draft(out):
    return answers_from_bytes((out / DRAFT_FILENAME).read_bytes())


def _before_the_click(tmp_path, fast):
    """The row of rep2 set but not clicked yet; A (LEAD) untagged, nothing refused."""
    root, masks, out = _experiment(tmp_path, extra=fast, names=_like(REFUSED, fast))
    nb = opened(root, masks, out)
    tags, row = (EXTRA, ("rep2", 4)) if fast else (TAGS, ("rep2", 3))
    set_labels(nb, list(tags))
    grid = nb.channel_grid
    assert grid.same_channels.value == fast
    if fast:
        for index, tag in enumerate(TAGS):
            grid.matrix.template_cells[(("ctrl", "rep1", "t1"), index)].value = tag
    for drop, tag in zip(grid.by_repeat.drops[row], tags, strict=True):
        drop.value = tag
    assert not grid.by_repeat.refused and LEAD not in _draft(out).units
    return nb, (root, masks, out), row, tags


def _click(nb, row, at, how):
    """Click the row; the draft write number ``at`` fails (refused, or the kernel stops)."""
    count, save = [0], nb._save_description

    def write():
        count[0] += 1
        if count[0] == at:
            if how == "interrupted":
                raise Interrupted
            nb._save_problem = "the disk refused the write (test)"
            return False
        return save()

    nb._save_description = write
    try:
        nb.channel_grid.by_repeat.buttons[row].click()
    except Interrupted:
        return count[0], False
    finally:
        nb._save_description = save
    return count[0], True


def _aliases_of_another_unit(nb):
    """Save P and Q as aliases of DNA and ACTIN, as confirming another unit would."""
    answers = nb.draft_answers
    channels = dict(answers.channels)
    for label, name in (("DNA", "P"), ("ACTIN", "Q")):
        old = channels[label]
        channels[label] = replace(old, header_names=(*(old.header_names or ()), name))
    assert isinstance(channels["DNA"], ChannelAnswer)
    assert nb.channel_grid.store.update(replace(answers, channels=channels))


def _untagged(answers, key):
    own = answers.units.get(key)
    return own is None or not any(own.channels)


@pytest.mark.parametrize("how", ["refused", "interrupted"])
@pytest.mark.parametrize("at", [1, 2, 3])
@pytest.mark.parametrize("fast", [True, False])
def test_a_row_click_saves_a_tags_and_b_refusal_together_or_neither(
    tmp_path, request, fast, at, how
):
    if not fast:
        request.getfixturevalue("statement_off")
    nb, exp, row, tags = _before_the_click(tmp_path, fast)
    rows = nb.channel_grid.by_repeat
    before = rows._review()
    writes, alive = _click(nb, row, at, how)
    disk = _draft(exp[2])
    review = disk.tag_review or TagReview()
    a_saved = LEAD in disk.units and disk.units[LEAD].channels == tags
    b_refused = set(REFUSED) <= set(review.refused)
    # at 40aca1d, failing the second write: A's tags on disk, B's refusal not
    assert a_saved == b_refused, (writes, a_saved, b_refused)
    if alive:  # this session: memory is the draft's, never ahead of it
        assert rows._review() == review and not rows.unsaved
        if not a_saved:
            said = nb.channel_grid.problem.value + rows.message
            assert rows._review() == before and "Not saved" in said
    fresh = opened(*exp)  # a restart on the draft as it is on disk
    if not b_refused:  # nothing of the click was kept: the click is to be made again
        assert _untagged(fresh.draft_answers, LEAD) and not fresh.channel_grid.by_repeat.refused
        return
    _aliases_of_another_unit(fresh)  # another unit's aliases now fit B's header
    again = opened(*exp)  # a restart: fast mode trusts the headers it can
    grid, rows = again.channel_grid, again.channel_grid.by_repeat
    for key in REFUSED:  # B stays blocked: never tagged from its header
        assert _untagged(again.draft_answers, key) and key not in grid.matrix.own
        assert key in rows.listed() and key in rows.standing()
    assert not grid.complete and not again.save_experiment()
    rows.override.buttons[OTHER].click()  # the explicit action on B
    rows.override.confirm_button.click()
    assert OTHER not in rows.listed() and GONE in rows.listed()
    assert OTHER in _draft(exp[2]).tag_review.confirmed
