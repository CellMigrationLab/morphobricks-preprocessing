"""The notebook writes fast mode by default and keeps a published file's mode (Spec ADR 0006).

Conformance plan P40 (a)-(d) on the notebook route: a new description is published as a
fast-mode file (FC1), the list being the tagged unit's tags and the date the day of the user's
action; unticked, it is strict (no ``channel_layout``, no ``channels_from``); a published
file reopens in its own mode (experiment-v1 section 10) and is switched only by the user's
tick, recorded as ``switch_mode`` and accepted by publication only then.
"""

from __future__ import annotations

from datetime import date

import pytest

from morphobricks_preprocessing.description import description_from_bytes
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from test_notebook_check_save import answer_all
from test_notebook_check_save import exp as folders  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from test_notebook_row_fold import stepper  # noqa: E402


def _published(out):
    return description_from_bytes((out / DESCRIPTION_FILENAME).read_bytes())


def _markers(description):
    return {
        u.channels_from
        for r in description.repeats.values()
        for m in r.members.values()
        for u in m.technical_repeats.values()
    }


def test_p40a_a_new_description_is_published_in_fast_mode_by_default(folders):  # noqa: F811
    nb = stepper(*folders)
    assert nb.channel_grid.same_channels.value  # FC1: ticked
    answer_all(nb)
    assert nb.save_experiment()
    published = _published(folders[2])
    layout = published.channel_layout
    assert layout is not None and layout.confirmed_on == date.today().isoformat()
    first = sorted(
        (c.source_folder, m.source_repeat, u)
        for r in published.repeats.values()
        for cid, m in r.members.items()
        for c in [published.conditions[cid]]
        for u in m.technical_repeats.values()
    )[0][2]
    assert layout.channels == first.channels  # the order of the tagged unit
    assert _markers(published) == {"experiment"}


def test_p40b_unticked_the_notebook_publishes_a_strict_file(folders):  # noqa: F811
    nb = stepper(*folders)
    nb.channel_grid.same_channels.value = False
    answer_all(nb)
    assert nb.save_experiment()
    published = _published(folders[2])
    assert published.channel_layout is None and _markers(published) == {None}


def test_p40c_a_published_strict_file_reopens_unticked_and_stays_strict(folders):  # noqa: F811
    nb = stepper(*folders)
    nb.channel_grid.same_channels.value = False
    answer_all(nb)
    assert nb.save_experiment()
    again = stepper(*folders)
    assert not again.channel_grid.same_channels.value
    assert again.draft_answers is None or again.draft_answers.channel_layout is None


def test_10_a_published_fast_file_reopens_ticked_and_is_switched_only_by_the_user(folders):  # noqa: F811
    nb = stepper(*folders)
    answer_all(nb)
    assert nb.save_experiment()
    again = stepper(*folders)
    assert again.channel_grid.same_channels.value  # its own mode, not the default
    again.channel_grid.same_channels.value = False  # the user's switch
    answers = again.draft_answers
    assert answers.channel_layout is None and answers.switch_mode
    answer_all(again)
    assert again.save_experiment()
    assert _published(folders[2]).channel_layout is None
