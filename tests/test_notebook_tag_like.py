"""Reopen: "Tag like the saved group", and mask evidence in colour for RGB folders (PR 17c).

On a reopen, new folders whose header is exactly that of a saved, tagged block form a block of
their own with **Tag like the saved group**. Its click writes the same draft bytes as choosing
the tags by hand; it is never automatic and never offered for a header without every channel
name. On *Masks*, an RGB folder's evidence is drawn on its colour picture.
"""

import shutil

import pytest

from channel_images import write_channels, write_masks, write_rgb
from morphobricks_preprocessing.description_file import DRAFT_FILENAME
from test_notebook_check_save import KINDS, TAGS, add_folder, answer_all, decode

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

from test_notebook_row_fold import stepper  # noqa: E402

SAVED = ["ctrl/rep1", "ctrl/rep2", "drug/rep1"]
NEW = ("drug", "rep2", "single")


def _reopened(tmp_path, write=add_folder):
    """Save three folders, keep a copy of the output, add drug/rep2 with the same header."""
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(SAVED):
        write(root, masks, folder, i + 1)
    first = stepper(root, masks, out)
    answer_all(first)
    assert first.save_experiment()
    shutil.copytree(out, tmp_path / "copy")
    write(root, masks, "drug/rep2", 9)
    return root, masks, out, tmp_path / "copy"


def _new_block(grid):
    return next(b for b in grid.block_list() if b[0].key == NEW)


def test_tag_like_the_saved_group_writes_the_bytes_of_tagging_by_hand(tmp_path):
    root, masks, out, copy = _reopened(tmp_path)
    clicked = stepper(root, masks, out)
    grid = clicked.channel_grid
    assert len(_new_block(grid)) == 1 and len(grid.like_buttons) == 1
    button = grid.like_buttons[0]
    assert (
        button.description.strip() == "Tag like the saved group (DNA · ACTIN · PHASE; 3 folder(s))"
    )
    unit = clicked.draft_answers.units.get(NEW)
    assert unit is None or not any(unit.channels)  # nothing copied before the click
    button.click()
    assert clicked.draft_answers.units[NEW].channels == tuple(TAGS)
    assert grid.like_buttons == []  # tagged: the button is gone

    by_hand = stepper(root, masks, copy)
    for index, tag in enumerate(TAGS):
        by_hand.channel_grid.cells[(NEW, index)].value = tag
    assert (out / DRAFT_FILENAME).read_bytes() == (copy / DRAFT_FILENAME).read_bytes()


def _nameless(root, masks, folder, seed):
    write_channels(root / folder / "a.tif", ["DAPI", None, "Ch3"], KINDS, seed=seed)
    write_masks(masks, {f"{folder}/a.tif": seed}, {"Nuclei": "labels"})


def test_a_header_without_every_channel_name_is_never_offered_the_saved_tags(tmp_path):
    root, masks, out, _ = _reopened(tmp_path, _nameless)
    nb = stepper(root, masks, out)
    assert _new_block(nb.channel_grid) and nb.channel_grid.like_buttons == []


def test_a_header_that_differs_is_not_offered_the_saved_tags(tmp_path):
    root, masks, out, _ = _reopened(tmp_path)
    write_channels(root / "drug/rep2/a.tif", ["dapi", "Actin", "Ch3"], KINDS, seed=9)  # case
    nb = stepper(root, masks, out)
    assert nb.channel_grid.like_buttons == []


def test_the_mask_evidence_of_an_rgb_folder_is_drawn_in_colour(tmp_path):
    root, masks, out = tmp_path / "exp", tmp_path / "masks", tmp_path / "out"
    for i, folder in enumerate(SAVED):
        write_rgb(root / folder / "a.tif", seed=i + 1)
        write_masks(masks, {f"{folder}/a.tif": i + 1}, {"Nuclei": "labels"})
    nb = stepper(root, masks, out)
    card = nb.channel_labels
    card.rows[0].id_box.value = "DNA"
    card.confirm.button.click()
    question = nb.mask_card.questions["Nuclei"]
    image = decode(bytes(question.pictures.children[0].value))
    assert image.shape[2] == 3 and (image[..., 0] != image[..., 2]).any()  # colour, not grey
    assert "in colour" in question.facts.value
    question.choice.value = "instances"
    overlay = decode(bytes(question.pictures.children[1].value))
    assert overlay.shape == image.shape
