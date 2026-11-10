"""An all-RGB experiment needs no channel label (issue 240, experiment-v1 section 4.4b).

R, G and B are the fixed channel IDs of a unit the user declared ``rgb``; no label applies. So
the Stains step is not applicable when every folder's saved image type is ``rgb``, and Check and
save completes and saves without one. Only the user's answer counts: a header's colour pre-tick,
a mixed experiment or an unanswered image type keeps the labels required.
"""

import tomllib
from dataclasses import replace

import pytest

from channel_images import write_channels, write_rgb
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME
from spec_oracle import refval

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.channel_answers import all_colour  # noqa: E402
from morphobricks_preprocessing.channel_labels import UnitLayout  # noqa: E402
from morphobricks_preprocessing.draft_answers import UnitAnswer  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2"]
STAINS = 7  # step 8, 0-based
TAGS = 8


def _open(tmp_path, *, channel_folders=()):
    root = tmp_path / "exp"
    for i, folder in enumerate(FOLDERS):
        if folder in channel_folders:
            write_channels(root / folder / "a.tif", ["DAPI", "Actin", "Ch3"], ["blobs"] * 3, seed=i)
        else:
            write_rgb(root / folder / "a.tif", seed=i + 1)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"), describe=True)
    nb.steps()
    nb.check_button.click()
    nb.quick_confirm.click()
    return nb


def _answer(nb, *, skip=()):
    """Answer the image types (when asked), the reference and each replicate; no label.

    The colour folders of ``skip`` are left on the header's unsaved pre-tick.
    """
    types = nb.channel_types
    for layout in types.layouts:
        if not layout.rgb and types.saved(layout.key) is None:
            types.radios[layout.key].value = "channels"
    types.confirm_header([u.key for u in types.layouts if types.proposed(u) and u.key not in skip])
    nb.reference_boxes["drug"].value = "ctrl"
    nb.reference_boxes["ctrl"].value = ""
    for toggle in list(nb.same_way.values()):
        if not toggle.value:
            toggle.button.click()


def _state(nb, index):
    return nb._stepper.states()[index]


def test_an_all_rgb_experiment_completes_stains_and_check_and_save_without_a_label(tmp_path):
    nb = _open(tmp_path)
    _answer(nb)
    stains = _state(nb, STAINS)
    assert stains.done and stains.note == "not applicable"
    assert stains.text == "not applicable: every folder is colour (RGB)"
    assert "not applicable" in nb._stepper.chips[STAINS].description
    assert "mb-chip-noted" in nb._stepper.chips[STAINS]._dom_classes
    assert _state(nb, TAGS).done and not nb.channel_labels.confirmed
    assert "Not applicable" in nb.channel_labels.na_note.value
    assert not ui.is_visible(nb.channel_labels.body[0])
    assert not ui.is_visible(nb.channel_intro)  # "first name what you stained" would contradict
    assert nb.todo.value == "" and not nb.save_button.disabled
    assert nb.save_experiment()
    assert _state(nb, 10).done


def test_the_saved_file_declares_exactly_r_g_b_and_the_rgb_image_type(tmp_path):
    nb = _open(tmp_path)
    _answer(nb)
    assert nb.save_experiment()
    text = (tmp_path / "out" / DESCRIPTION_FILENAME).read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert sorted(data["channels"]) == ["B", "G", "R"]
    assert all(not v.get("header_names") for v in data["channels"].values())
    units = [
        unit
        for repeat in data["repeats"].values()
        for member in repeat["members"].values()
        for unit in member["technical_repeats"].values()
    ]
    assert len(units) == len(FOLDERS)
    assert all(u["image_type"] == "rgb" and u["channels"] == ["R", "G", "B"] for u in units)


def test_the_header_pre_tick_alone_does_not_make__stains_not_applicable(tmp_path):
    nb = _open(tmp_path)  # the headers say colour; nothing is answered
    assert not nb._stains_not_applicable
    stains = _state(nb, STAINS)
    assert not stains.done and stains.text == "not confirmed"
    assert ui.is_visible(nb.channel_labels.body[0])
    assert "Not applicable" not in nb.channel_labels.na_note.value


def test_a_folder_left_on_the_header_pre_tick_keeps_the_label_required(tmp_path):
    nb = _open(tmp_path)
    key = nb.channel_types.layouts[0].key
    _answer(nb, skip=(key,))  # three folders answered colour, one only pre-ticked
    assert nb.channel_types.layouts and nb.channel_types.saved(key) is None
    assert not nb._stains_not_applicable
    assert not _state(nb, STAINS).done and not _state(nb, TAGS).done
    assert ui.is_visible(nb.channel_labels.body[0]) and ui.is_visible(nb.channel_intro)
    assert nb.save_button.disabled and not nb.save_experiment()


def test_one_unanswered_folder_keeps_the_label_required(tmp_path):
    nb = _open(tmp_path)
    _answer(nb)
    assert _state(nb, STAINS).done
    layout = nb.channel_types.layouts[0]
    nb.channel_types.radios[layout.key].value = "channels"  # now mixed
    assert not nb._stains_not_applicable and not _state(nb, STAINS).done
    assert ui.is_visible(nb.channel_labels.body[0]) and ui.is_visible(nb.channel_intro)
    assert nb.channel_labels.na_note.value == ""


def test_labels_declared_before_every_folder_became_colour_stay_saved_and_unused(tmp_path):
    nb = _open(tmp_path)
    _answer(nb)
    key = nb.channel_types.layouts[0].key
    nb.channel_types.radios[key].value = "channels"
    card = nb.channel_labels
    while len(card.rows) < 3:
        card.add_button.click()
    for row, text in zip(card.rows, ["DNA", "ACTIN", "PHASE"], strict=True):
        row.id_box.value = text
    card.confirm.button.click()
    for (_k, index), cell in list(nb.channel_grid.cells.items()):
        cell.value = card.labels[index] if cell.value is None else cell.value
    nb.channel_types.radios[key].value = "rgb"  # every folder is colour again
    assert nb._stains_not_applicable and card.labels == ["ACTIN", "DNA", "PHASE"]
    assert "stay saved, unused" in card.na_note.value
    # The folder's image type changed after its replicate's "same way?" was answered: that
    # answer was for another layout, so it is asked again, never kept for the user.
    assert nb.save_button.disabled and not nb.save_experiment()
    asked = [label for label, toggle in nb.same_way.items() if not toggle.value]
    assert asked == ["rep1"]
    nb.same_way["rep1"].button.click()
    assert nb.save_experiment()
    text = (tmp_path / "out" / DESCRIPTION_FILENAME).read_text(encoding="utf-8")
    result = refval().validate(text.encode("utf-8"))[0]
    assert not result.errors  # valid; the reader's warning is W_UNUSED_CHANNEL (section 4.3)
    assert {w[0] for w in result.warnings} == {"W_UNUSED_CHANNEL"}


def test_a_stale_colour_answer_that_no_longer_fits_the_image_does_not_count():
    key = ("c", "r", "single")
    answer = UnitAnswer(("R", "G", "B"), "rgb")
    fits = UnitLayout(key, "a.tif", 3, (None, None, None), rgb=True)
    assert all_colour([fits], {key: answer})
    assert not all_colour([replace(fits, channels=4)], {key: answer})
    assert not all_colour([replace(fits, problem="unreadable")], {key: answer})
    assert not all_colour([fits], {})
    assert not all_colour([], {})
