"""What the guided channel step records and says (review of #546, F4 and F5).

F4: a unit whose own images disagree is in no row, so no row's tags are recorded for it (on the
reviewed head it was refused with the tags of the first row of its channel count, another
repeat's for a per-repeat answer), and the narrowing choice names the *biological repeat
folder*. F5: a row filled from a broader choice says so, and the image type's button reads
*Validated ✓* once the answer is saved, until a choice changes.
"""

from __future__ import annotations

import pytest

from channel_images import write_channels, write_masks
from morphobricks_preprocessing.description_file import DRAFT_FILENAME
from morphobricks_preprocessing.draft_format import answers_from_bytes

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

import ipywidgets as widgets  # noqa: E402

from morphobricks_preprocessing.notebook_channels_guide import NARROW_OPTIONS  # noqa: E402
from test_notebook_channel_step_acceptance import (  # noqa: E402
    KINDS,
    TAGS3,
    choose,
    experiment,
    opened,
)

NAMES = ["Hoechst", "Actin", "Ch3"]
MIXED = ("drug", "rep2", "single")
"""Its second image names the first two channels the other way round: its images disagree."""
ORDERS = {"rep1": TAGS3, "rep2": ["ACTIN", "DNA", "PHASE"], "rep3": ["PHASE", "ACTIN", "DNA"]}


def _mixed(tmp_path):
    root, masks, out = experiment(tmp_path, names=lambda key, n: NAMES)
    write_channels(root / "drug/rep2/b.tif", ["Actin", "Hoechst", "Ch3"], KINDS[:3], seed=40)
    write_masks(masks, {"drug/rep2/b.tif": 40}, {"Nuclei": "labels"})
    return root, masks, out


def _texts(widget):
    """Return the text of every HTML widget in a widget tree."""
    own = widget.value if isinstance(widget, widgets.HTML) else ""
    return own + "".join(_texts(child) for child in getattr(widget, "children", ()))


def _review(out):
    return answers_from_bytes((out / DRAFT_FILENAME).read_bytes()).tag_review


# --- F4: no other row's tags for a unit whose images disagree -----------------------------------


@pytest.mark.parametrize("scope", ["whole", "repeat"])
def test_a_unit_whose_images_disagree_records_no_row_s_tags(tmp_path, scope):
    exp = _mixed(tmp_path)
    nb = opened(*exp, TAGS3)
    guide = nb.channel_grid.guide
    if scope == "whole":
        guide.same.value = "yes"
        choose(nb, ("whole", 3), TAGS3)
    else:
        guide.same.value = "no"
        guide.narrow.value = "repeat"
        for rep, tags in ORDERS.items():
            choose(nb, ("repeat", rep, 3), tags)
    assert guide.submit()
    rows = nb.channel_grid.by_repeat
    assert MIXED in rows.listed() and rows.refused[MIXED].lead is None
    # on the reviewed head: the first row of its count's tags (rep1's order, DNA ACTIN PHASE)
    assert rows.refused[MIXED].tags == ()
    assert _review(exp[2]).refused[MIXED].tags == ()
    assert nb.draft_answers.units[MIXED].channels == (None, None, None)  # never filled
    assert MIXED not in rows.override.buttons  # no reference while its images disagree
    write_channels(exp[0] / "drug/rep2/b.tif", NAMES, KINDS[:3], seed=40)
    nb.check_button.click()  # its images agree again: still listed, no confirmation came
    nb.preview_checked.set(True)
    nb.show_preview()
    nb.confirm_button.click()
    assert MIXED in rows.listed()
    rows.override.buttons[MIXED].click()  # its editor: every channel chosen, none pre-set
    assert rows.override.drafts[MIXED] == [None, None, None]
    assert "no row's tags were recorded for it" in _texts(nb.channel_grid.widget)


def test_the_narrowing_choice_names_the_biological_repeat_folder():
    assert NARROW_OPTIONS[0][0] == "Same order within each biological repeat folder"


# --- F5: what a row and the image type's button say ---------------------------------------------


def test_a_row_filled_from_a_broader_choice_says_so(tmp_path):
    exp = experiment(tmp_path)
    nb = opened(*exp, TAGS3)
    guide = nb.channel_grid.guide
    guide.same.value = "yes"
    choose(nb, ("whole", 3), TAGS3)  # the form only: nothing saved
    assert "check it before Submit" not in guide.notes[("whole", 3)].value  # the user's own
    guide.same.value = "no"
    guide.narrow.value = "repeat"
    for rep in ORDERS:
        assert guide.values()[("repeat", rep, 3)] == TAGS3  # the broader choice, pre-filled
        assert "check it before Submit" in guide.notes[("repeat", rep, 3)].value
    choose(nb, ("repeat", "rep2", 3), ORDERS["rep2"])
    assert "check it before Submit" not in guide.notes[("repeat", "rep2", 3)].value


def test_the_image_type_button_reads_validated_after_a_save_until_a_choice_changes(tmp_path):
    exp = experiment(tmp_path)
    nb = opened(*exp, [], typed=False)
    line = nb.type_line
    assert line.apply.description.strip() == "Validate" and not line.apply.disabled
    line.apply.click()
    assert line.apply.description.strip() == "Validated ✓" and line.apply.disabled
    line.per_folder.selected_index = 0
    one = ("drug", "rep1", "single")
    nb.channel_types.radios[one].value = "rgb"  # a change of the form: not saved yet
    assert line.apply.description.strip() == "Validate"
    nb.channel_types.radios[one].value = "channels"  # back to what is saved
    assert line.apply.description.strip() == "Validated ✓"
