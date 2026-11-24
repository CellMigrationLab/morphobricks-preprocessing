"""Tag by repeat: a refusal is never dropped while its unit is still in the folder (PR #376).

External review of 23 November, blocker B: a refused unit set to colour (RGB) left every row, and
the next re-check dropped its refusal; set back to channels, fast mode tagged it from another
unit's aliases with no listing and no confirmation. Only a unit gone from the folder drops its
refusal. While the unit really is colour its channels are R, G and B (nothing to tag or confirm),
so the kept refusal does not list it or hold the save; set back to channels it is listed again.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing.description_file import DRAFT_FILENAME  # noqa: E402
from morphobricks_preprocessing.draft_format import answers_from_bytes  # noqa: E402
from test_notebook_channels import set_labels  # noqa: E402
from test_notebook_check_save import answer_all, opened  # noqa: E402
from test_notebook_tag_by_repeat import TAGS, _experiment, _set_row  # noqa: E402
from test_notebook_tag_by_repeat_oneclick import (  # noqa: E402
    GONE,
    OTHER,
    _confirm,
    _like,
    _strict,
)
from test_notebook_tag_by_repeat_recheck import _recheck, _rewrite_header  # noqa: E402


def _fast3(tmp_path):
    """Fast mode, three channels: GONE and OTHER refused by the rep2 row, GONE confirmed."""
    root, masks, out = _experiment(tmp_path, names=_like((GONE, OTHER), False))
    nb = opened(root, masks, out)
    set_labels(nb, list(TAGS))
    grid = nb.channel_grid
    assert grid.same_channels.value
    for index, tag in enumerate(TAGS):
        grid.matrix.template_cells[(("ctrl", "rep1", "t1"), index)].value = tag
    _set_row(nb, ("rep2", 3), TAGS)
    rows = grid.by_repeat
    assert set(rows.listed()) == {GONE, OTHER}
    _confirm(nb, rows, GONE, "override", None)
    assert set(rows.listed()) == {OTHER}
    return nb, out, rows


def _untagged(nb, key):
    own = nb.draft_answers.units.get(key)
    return own is None or own.image_type == "rgb" or not any(own.channels)


def test_a_refused_unit_set_to_colour_and_back_is_listed_again_never_tagged(tmp_path):
    nb, out, rows = _fast3(tmp_path)
    types = nb.channel_grid.types
    types.radios[OTHER].value = "rgb"
    _recheck(nb)
    assert OTHER in rows.refused  # at e010834: dropped while the unit is still in the folder
    saved = answers_from_bytes((out / DRAFT_FILENAME).read_bytes()).tag_review
    assert OTHER in saved.refused  # and dropped from the draft too
    assert OTHER not in rows.listed()  # colour: R, G and B, nothing to tag or confirm
    types.radios[OTHER].value = "channels"
    _recheck(nb)
    assert _untagged(nb, OTHER)  # at e010834: tagged from GONE's aliases, unasked
    assert OTHER in rows.standing() and OTHER in rows.listed()
    assert OTHER not in nb.channel_grid.matrix.own
    assert not nb.channel_grid.complete and not nb.save_experiment()
    assert set(rows.override.same_buttons) == {OTHER}  # confirmed only by the user's click
    rows.override.same_buttons[OTHER].click()
    assert not rows.listed() and nb.draft_answers.units[OTHER].channels == TAGS


def test_a_kept_refusal_does_not_hold_the_save_while_its_unit_is_colour(tmp_path, statement_off):
    nb, _, rows = _strict(tmp_path, statement_off)
    _confirm(nb, rows, GONE, "override", None)
    nb.channel_grid.types.radios[OTHER].value = "rgb"
    _recheck(nb)
    assert OTHER in rows.refused and not rows.listed()
    answer_all(nb)
    assert nb.save_experiment()  # colour: its channels are R, G and B, nothing to confirm
    nb.channel_grid.types.radios[OTHER].value = "channels"
    _recheck(nb)
    assert OTHER in rows.listed() and not nb.save_experiment()


def test_fast_mode_a_refused_unit_whose_header_is_fixed_stays_listed_and_says_why(tmp_path):
    nb, _, rows = _fast3(tmp_path)
    _rewrite_header(tmp_path / "exp", OTHER, ["X", "Y", "Ch3"])
    _recheck(nb)  # its header now names what the row's reference names
    assert _untagged(nb, OTHER) and OTHER in rows.standing()
    assert "never tagged from its header" in rows.listed()[OTHER]  # at e010834: no reason given
    rows.override.buttons[OTHER].click()
    assert rows.override.confirm() and OTHER not in rows.listed()
