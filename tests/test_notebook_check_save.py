"""A described run: *Check and save* and reopening (Stage 1, PR 16b).

Widgets are driven headless. ``PreprocessingNotebook()`` is the shipped notebook:
the labels toggle, the design card's "comparable" questions and the global Image type card are
retired; every answer goes to ``morphobricks.draft.json``; one click publishes
``morphobricks.toml`` and writes ``experiment_design.csv`` (only then); reopening folds what
did not change and asks again only what did.
"""

import json
import shutil
import zlib

import numpy as np
import pytest

from channel_images import write_channels, write_masks
from morphobricks_preprocessing.description_file import (
    DESCRIPTION_FILENAME,
    DRAFT_FILENAME,
    RECORDS_FILENAME,
)
from morphobricks_preprocessing.design import DESIGN_FILENAME

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from morphobricks_preprocessing import notebook_style as ui  # noqa: E402
from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

KINDS = ["blobs", "filaments", "cells"]
TAGS = ["DNA", "ACTIN", "PHASE"]
FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2"]


def decode(png):
    """Decode an 8-bit grey or RGB PNG without filters other than PNG's own (stdlib only)."""
    assert png.startswith(b"\x89PNG")
    pos, chunks, header = 8, [], None
    while pos < len(png):
        length = int.from_bytes(png[pos : pos + 4], "big")
        kind, data = png[pos + 4 : pos + 8], png[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            header = data
        elif kind == b"IDAT":
            chunks.append(data)
        pos += 12 + length
    width, height = int.from_bytes(header[:4], "big"), int.from_bytes(header[4:8], "big")
    channels = {0: 1, 2: 3, 6: 4}[header[9]]
    raw = zlib.decompress(b"".join(chunks))
    stride = width * channels + 1
    rows = [np.frombuffer(raw[i * stride + 1 : (i + 1) * stride], np.uint8) for i in range(height)]
    assert all(raw[i * stride] == 0 for i in range(height))  # filter type 0 only
    return np.stack(rows).reshape(height, width, channels)


def add_folder(root, masks, folder, seed):
    write_channels(root / folder / "a.tif", ["DAPI", "Actin", "Ch3"], KINDS, seed=seed)
    write_masks(masks, {f"{folder}/a.tif": seed}, {"Nuclei": "labels"})


@pytest.fixture
def exp(tmp_path):
    root, masks = tmp_path / "exp", tmp_path / "masks"
    for i, folder in enumerate(FOLDERS):
        add_folder(root, masks, folder, i + 1)
    return root, masks, tmp_path / "out"


def opened(root, masks, out, *, axes=True):
    nb = PreprocessingNotebook(str(root), output_folder=str(out))
    for step in (
        nb.welcome,
        nb.folder_step,
        nb.axis_step,
        nb.channels_step,
        nb.masks_step,
        nb.check_save_step,
        nb.run_step,
    ):
        step()
    nb.masks_folder.value = str(masks)
    nb.check_button.click()
    if axes:
        nb.preview_checked.set(True)
        nb.show_preview()
        nb.confirm_button.click()
    return nb


def tag(nb):
    """Declare the labels (once) and tag every untagged cell and image type."""
    card = nb.channel_labels
    if not card.confirmed:
        for row, text in zip(card.rows, TAGS, strict=True):
            row.id_box.value = text
        card.confirm.button.click()
    for (_key, index), cell in list(nb.channel_grid.cells.items()):
        if cell.value is None:
            cell.value = TAGS[index]
    for layout in nb.channel_grid.layouts:
        if nb.channel_types.radios[layout.key].value is None:
            nb.channel_types.radios[layout.key].value = "channels"


def answer_all(nb):
    tag(nb)
    question = nb.mask_card.questions.get("Nuclei")
    if question is not None and question.choice.value is None:
        question.choice.value = "instances"
    if nb.reference_boxes["drug"].value is None:
        nb.reference_boxes["drug"].value = "ctrl"
    if nb.reference_boxes["ctrl"].value is None:
        nb.reference_boxes["ctrl"].value = ""
    for toggle in list(nb.same_way.values()):
        if not toggle.value:
            toggle.button.click()


def saved_nb(exp):
    nb = opened(*exp)
    answer_all(nb)
    assert nb.save_experiment()
    return nb


# --- described run: retired cards, no CSV before the save ---------------------------------------


def test_a_described_run_retires_the_labels_toggle_the_comparable_questions_and_image_type(exp):
    root, masks, out = exp
    nb = opened(root, masks, out)
    assert nb.labels_confirmed.value and not ui.is_visible(nb.labels_confirmed.widget)
    assert ui.is_visible(nb.design_card)
    assert not ui.is_visible(nb.ids_confirmed.widget)
    assert not ui.is_visible(nb.image_type_card) and nb.confirmed_image_type is None
    assert "Optional" not in nb.references_card.children[1].value
    assert not (out / DESIGN_FILENAME).exists()  # no template written by the check
    assert "Save the experiment description (Check and save)" in nb.readiness.value
    assert "Confirm the image type" not in nb.readiness.value
    assert nb.run_button.disabled


def test_the_csv_is_written_only_at_save(exp):
    root, masks, out = exp
    nb = opened(root, masks, out)
    answer_all(nb)
    assert (out / DRAFT_FILENAME).exists()  # every answer is in the draft
    assert not (out / DESIGN_FILENAME).exists() and not (out / DESCRIPTION_FILENAME).exists()
    assert nb.save_experiment()
    assert (out / DESIGN_FILENAME).exists() and (out / DESCRIPTION_FILENAME).exists()
    assert not (out / DRAFT_FILENAME).exists()


def test_pairing_shows_a_picture_of_each_folder_once_the_axis_roles_are_confirmed(exp):
    root, masks, out = exp
    nb = opened(root, masks, out, axes=False)
    cell = nb.pairing_grid.children[1].children[1]
    assert "picture once the axis roles are confirmed" in cell.children[1].value
    nb.preview_checked.set(True)
    nb.show_preview()
    nb.confirm_button.click()
    cell = nb.pairing_grid.children[1].children[1]  # rebuilt with its picture
    png = bytes(cell.children[1].children[0].children[0].value)  # the tile's thumbnail
    pixels = decode(png)
    assert pixels.shape[:2] == (64, 64) and pixels.std() > 0  # the folder's own 64 px image


# --- the check screen -----------------------------------------------------------------------------


def test_the_experiment_is_restated_in_words_with_one_question_per_replicate(exp):
    nb = opened(*exp)
    tag(nb)
    nb.mask_card.questions["Nuclei"].choice.value = "instances"
    nb.reference_boxes["drug"].value = "ctrl"
    nb.reference_boxes["ctrl"].value = ""
    assert "drug is compared against ctrl." in nb.words.value
    assert "ctrl is compared against nothing." in nb.words.value
    assert sorted(nb.same_way) == ["rep1", "rep2"]
    assert "Were these prepared and imaged the same way" in nb.same_way["rep1"].prompt.value
    assert "replicate rep1: answer" in nb.todo.value and nb.save_button.disabled
    assert "never change once saved" in nb.short_names.value
    assert "DNA" in nb.short_names.value and "Nuclei" in nb.short_names.value
    nb.same_way["rep1"].button.click()
    nb.same_way["rep2"].button.click()
    assert nb.todo.value == "" and not nb.save_button.disabled
    assert "strict reader accepts the exact bytes" in nb.checks.value
    assert "written for the first time (4 rows)" in nb.checks.value


def test_save_says_saved_and_enables_run(exp):
    nb = saved_nb(exp)
    assert (
        "Saved " in nb.save_status.value and "The run (step 12) can start" in nb.save_status.value
    )
    assert not nb.run_button.disabled
    assert nb.confirmed_image_type == "fluorescence"
    _, _, out = exp
    rows = (out / DESIGN_FILENAME).read_text(encoding="utf-8").splitlines()
    assert rows[1].startswith("ctrl,rep1,rep1,true,1,0,true,")


def test_a_label_some_units_lack_is_said_in_orange(exp):
    nb = opened(*exp)
    tag(nb)
    grid = nb.channel_grid
    unit = ("drug", "rep2", "single")
    nb.channel_labels.confirm.button.click()  # change the labels: add an unused one
    nb.channel_labels.add_button.click()
    nb.channel_labels.rows[-1].id_box.value = "unused"
    nb.channel_labels.confirm.button.click()
    grid.split(unit)  # tagged on its own, outside its block of identical headers
    grid.cells[(unit, 2)].value = "unused"
    box = [b for b in nb.replicates_box.children if "rep2" in b.children[0].value][0]
    assert "drug/rep2 has no PHASE" in box.children[-2].value
    assert "A_SCOPE_INCOMPLETE" in box.children[-2].value


# --- reopening ------------------------------------------------------------------------------------


def test_reopening_with_nothing_changed_folds_every_screen(exp):
    saved_nb(exp)
    nb = opened(*exp)
    assert nb._folded and ui.is_visible(nb.fold_bar)
    assert "nothing changed" in nb.saved_line.value
    for widget in (nb.design_card, nb.names_card, nb.channels_box, nb.masks_box, nb.check_card):
        assert not ui.is_visible(widget)
    assert not nb.run_button.disabled  # saved and current: Run is enabled
    nb.change_saved.click()
    assert not nb._folded and ui.is_visible(nb.names_card) and ui.is_visible(nb.check_card)


def test_a_folder_that_joins_a_replicate_asks_same_way_again_for_it_only(tmp_path):
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(["ctrl/rep1", "ctrl/rep2", "drug/rep1"]):
        add_folder(root, masks, folder, i + 1)
    first = opened(root, masks, out)
    answer_all(first)
    assert first.save_experiment()
    add_folder(root, masks, "drug/rep2", 9)
    nb = opened(root, masks, out)
    assert not nb._folded
    assert "new: drug/rep2" in nb.folder_changes.value
    assert nb.same_way["rep1"].value and not nb.same_way["rep2"].value
    assert nb.run_button.disabled  # the folders changed: save again first
    answer_all(nb)
    assert nb.save_experiment()
    records = (
        (out / RECORDS_FILENAME).read_text(encoding="utf-8")
        if (out / RECORDS_FILENAME).exists()
        else ""
    )
    assert "removed" not in records
    assert "drug,rep2,rep2" in (out / DESIGN_FILENAME).read_text(encoding="utf-8")


def test_a_removed_folder_needs_yes_remove_before_saving(exp):
    root, masks, out = exp
    saved_nb(exp)
    shutil.rmtree(root / "ctrl" / "rep2")
    shutil.rmtree(masks / "Nuclei" / "ctrl" / "rep2")
    nb = opened(root, masks, out)
    assert "removed: ctrl/rep2" in nb.folder_changes.value
    (row,) = nb.removals_box.children
    assert "ctrl/rep2 of ctrl is gone" in row.children[0].value
    answer_all(nb)  # rep2 lost a folder: asked again, and answered
    assert "Yes, remove" in nb.todo.value and nb.save_button.disabled
    row.children[1].click()
    assert not nb.save_button.disabled
    assert nb.save_experiment()
    lines = [json.loads(x) for x in (out / RECORDS_FILENAME).read_text().splitlines()]
    assert any(r["type"] == "removed" for r in lines)
    assert "ctrl,rep2" not in (out / DESIGN_FILENAME).read_text(encoding="utf-8")


def test_the_draft_carries_the_pairing_across_a_reopen(tmp_path):
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/day2"]):
        add_folder(root, masks, folder, i + 1)
    nb = opened(root, masks, out)
    (dropdown,) = [
        row.children[2]
        for row in nb.pairing_grid.children[1:]
        if "day2" in row.children[1].children[0].value
    ]
    dropdown.value = "rep2"
    assert json.loads((out / DRAFT_FILENAME).read_text())["pairing"] == {"drug": {"day2": "rep2"}}
    again = opened(root, masks, out)
    assert again.pairing[("drug", "day2")] == "rep2"
    assert not (out / DESIGN_FILENAME).exists()


# --- the pictures hold the right image ------------------------------------------------------------


def test_each_replicate_row_shows_its_own_folders_picture(exp):
    nb = opened(*exp)
    tag(nb)
    layouts = {layout.key[:2]: layout for layout in nb._channel_layouts}
    seen = {}
    for box in nb.replicates_box.children:
        for row in box.children[1:-2]:
            tile, text = row.children
            picture = tile.children[0].children[0]  # the tile's thumbnail
            folder = text.value.split("<b>")[1].split("</b>")[0]
            key = tuple(folder.split("/"))
            expected = nb._channel_thumbs.get(layouts[key], 0).png
            assert bytes(picture.value) == expected  # the folder's own first image, channel 1
            pixels = decode(bytes(picture.value))
            assert pixels.shape[:2] == (64, 64) and pixels.std() > 0
            seen[key] = pixels
    assert len(seen) == 4
    assert not np.array_equal(seen[("ctrl", "rep1")], seen[("drug", "rep1")])


def test_the_mask_overlay_is_drawn_on_the_image_as_declared(exp):
    nb = opened(*exp)
    question = nb.mask_card.questions["Nuclei"]
    image, mask = (decode(bytes(p.value)) for p in question.pictures.children)
    assert image.shape[:2] == mask.shape[:2] == (64, 64)
    assert image.std() > 0 and mask.std() > 0  # the mask alone, grey, before an answer
    question.choice.value = "instances"
    image, overlay = (decode(bytes(p.value)) for p in question.pictures.children)
    assert overlay.shape == (64, 64, 3)
    coloured = (overlay[..., 0] != overlay[..., 1]) | (overlay[..., 1] != overlay[..., 2])
    assert 0 < coloured.mean() < 0.5  # object outlines in colour over the grey image


def test_replicates_are_listed_in_natural_order_rep2_before_rep10(tmp_path):
    # Display only (PR 17b): the replicate blocks, the names rows and the short names.
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(["ctrl/rep10", "ctrl/rep2", "drug/rep10", "drug/rep2"]):
        add_folder(root, masks, folder, i + 1)
    nb = opened(root, masks, out)
    answer_all(nb)
    titles = [b.children[0].value for b in nb.replicates_box.children]
    assert "rep2" in titles[0] and "rep10" in titles[1]
    assert "Replicates:</b> rep2, rep10" in nb.short_names.value
    rows = [key for key in nb.id_inputs if key[0] == "repeats"]
    assert rows == [("repeats", "rep2"), ("repeats", "rep10")]
