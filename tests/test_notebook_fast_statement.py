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

# the per-unit grid driven through the stepper (the guided step is pinned by its own tests)
pytestmark = pytest.mark.usefixtures("legacy_grid")


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


def test_a_unit_tagged_from_its_header_with_no_click_is_never_marked_unit(tmp_path):
    """Re-review of #362: only the user's action makes a unit "tagged on its own" (section 10).

    drug/rep1/t1's header names its channels ACTIN, DNA, PHASE: fast mode tags it so with no
    click ("from its header names"). The draft must not mark it ``unit`` on its own: it stays
    ``experiment``, so publication follows the list and its images are refused as
    ``header_order`` (FC4, P37 (c)) until the user tags it on its own.
    """
    from morphobricks_preprocessing.channel_matrix import HEADER
    from test_notebook_tag_matrix import LABELLED, _design, _screen, _states, _types

    other = ("drug", "rep1", "t1")
    order = lambda key: ["ACTIN", "DNA", "PHASE"] if key == other else LABELLED  # noqa: E731
    nb = _screen(tmp_path, _design(tmp_path, names_of=order))
    _types(nb)
    assert _states(nb)[other] == HEADER
    units = nb.draft_answers.units
    assert units[other].channels == ("ACTIN", "DNA", "PHASE")  # tagged with no click
    assert {u.channels_from for u in units.values()} == {"experiment"}
    grid = nb.channel_grid
    grid.cells[(other, 0)].value = None  # the user's own click on that unit
    grid.cells[(other, 0)].value = "ACTIN"
    assert nb.draft_answers.units[other].channels_from == "unit"


def test_a_units_image_type_answered_by_the_user_is_never_replaced_by_the_list(tmp_path):
    """Third review of #362: an image type the user picks for one unit is the user's action.

    drug/rep1/t1 answered ``rgb`` with its type button while the list's type is ``channels``:
    it is marked ``unit`` and published with its own answer, never materialised to the list's
    type (FC4: a different image type is refused, never adjusted; section 3.3).
    """
    from morphobricks_preprocessing.draft_answers import fast_units
    from test_notebook_tag_matrix import _design, _screen, _tag_once, _types

    other = ("drug", "rep1", "t1")
    nb = _screen(tmp_path, _design(tmp_path, names_of=lambda key: None))
    _tag_once(nb)
    _types(nb)
    assert nb.draft_answers.units[other].channels_from == "experiment"
    nb.channel_types.radios[other].value = "rgb"  # the user's type button, no cell clicked
    answers = nb.draft_answers
    assert answers.units[other].image_type == "rgb"
    assert answers.units[other].channels_from == "unit"
    kept = fast_units(nb._draft_base, answers).units[other]
    assert kept.image_type == "rgb"  # not replaced by the list's type


def test_a_save_with_no_click_notes_no_user_unit():
    """Only a user's save is noted (note_user_units); a no-click save never is."""
    from dataclasses import replace

    from morphobricks_preprocessing.draft_answers import DraftAnswers, UnitAnswer
    from morphobricks_preprocessing.notebook_channels_view import (
        save_change,
        save_unattended,
        user_units,
    )

    class Store:
        def __init__(self):
            self.saved = DraftAnswers("absent", "0" * 40)

        def answers(self):
            return self.saved

        def update(self, new):
            self.saved = new
            return True

    store, key = Store(), ("c", "r", "t")
    unit = UnitAnswer(("DNA",), "channels")
    save_unattended(store, replace(store.saved, units={key: unit}))
    assert user_units(store, "tags") == user_units(store, "type") == frozenset()
    save_change(store, lambda a: replace(a, units={key: replace(unit, image_type="rgb")}))
    assert user_units(store, "type") == {key} and user_units(store, "tags") == frozenset()


def _published_then_renamed(folders, *, fast):  # noqa: F811
    """Publish (fast or strict), rename drug's channel 1 to Hoechst, reopen, change a reference.

    The reference change is an answer to save; the renamed header contradicts the saved list.
    """
    from channel_images import write_channels
    from test_notebook_check_save import FOLDERS, KINDS, opened

    first = opened(*folders)
    first.channel_grid.same_channels.value = fast
    answer_all(first)
    assert first.save_experiment()
    published = (folders[2] / DESCRIPTION_FILENAME).read_bytes()
    for seed, folder in enumerate(FOLDERS[2:]):
        names = ["Hoechst", "Actin", "Ch3"]
        write_channels(folders[0] / folder / "a.tif", names, KINDS, seed=seed)
    nb = opened(*folders)
    nb.reference_boxes["drug"].value = ""  # a change to save: compared against nothing
    nb._refresh_check_save()
    return nb, published


def test_owner_20_nov_fast_mode_saves_the_draft_but_refuses_to_publish_a_contradiction(
    folders,  # noqa: F811
):
    """Owner decision of 20 November 2026 (ADR 0006 section 10)."""
    from morphobricks_preprocessing.description_file import DRAFT_FILENAME

    nb, before = _published_then_renamed(folders, fast=True)
    assert nb._check is not None and nb._check.can_save  # a valid description
    assert nb._check.fast_refusals and not nb._check.can_publish
    assert nb.save_button.disabled and not nb.save_experiment()
    shown = nb.save_note.value
    assert "Not publishable in fast mode yet" in shown and "Hoechst" in shown
    assert (folders[2] / DESCRIPTION_FILENAME).read_bytes() == before  # nothing published
    assert b'"reference": ""' in (folders[2] / DRAFT_FILENAME).read_bytes()  # the draft saved


def test_owner_20_nov_strict_mode_keeps_saving_with_the_runs_refusal_named(
    folders,  # noqa: F811
):
    """Strict mode is unchanged (owner, 10 November 2026): it saves, the run will stop."""
    nb, _ = _published_then_renamed(folders, fast=False)
    assert not nb._check.fast_refusals and nb._check.can_publish
    assert "You can save, but the run will stop" in nb.save_note.value
    assert nb.save_experiment()
