"""*Tag channels*: one answer per block of identical headers (owner decision Q5, PR 17b).

Folders whose image headers are identical, exactly, are tagged once for the block. The block
answer writes the same per-folder answers into the draft as tagging the folders one by one
(compared byte for byte). Nothing is grouped by guessing: a header without every channel name, a
different name, or different saved tags keep folders apart. The colour (RGB) answer is confirmed
once, on *Image type*; *Tag channels* only restates it.
"""

import pytest

from channel_images import write_rgb
from morphobricks_preprocessing.description_file import DRAFT_FILENAME
from test_notebook_channels import ACTIN, DNA, PHASE, open_screen, set_labels, tree

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

import ipywidgets as widgets  # noqa: E402

from morphobricks_preprocessing.notebook_ui import PreprocessingNotebook  # noqa: E402

FOLDERS = ["ctrl/rep1", "ctrl/rep2", "drug/rep1", "drug/rep2"]
KEYS = [(*f.split("/"), "single") for f in FOLDERS]


def _plain(tmp_path):
    return tree(tmp_path, lambda f: ["DAPI", "Actin", "Ch3"])


def test_identical_headers_are_one_block_with_one_dropdown_per_channel(tmp_path):
    nb = open_screen(_plain(tmp_path), tmp_path / "out")
    set_labels(nb, [DNA, ACTIN, PHASE])
    grid = nb.channel_grid
    assert [[u.key for u in block] for block in grid.block_list()] == [KEYS]
    drops = {id(grid.cells[(key, 0)]) for key in KEYS}
    assert len(drops) == 1  # one answer for the four folders
    assert "4 folder(s) with this header: tagged once" in repr(grid.blocks.children)
    grid.cells[(KEYS[0], 0)].value = DNA
    assert all(nb.draft_answers.units[key].channels == (DNA, None, None) for key in KEYS)


def _tag_by_blocks(nb):
    grid = nb.channel_grid
    for index, tag in enumerate((DNA, ACTIN, PHASE)):
        grid.cells[(KEYS[0], index)].value = tag


def _tag_one_by_one(nb):
    grid = nb.channel_grid
    for key in KEYS:
        grid.split(key)
    for key in KEYS:
        for index, tag in enumerate((DNA, ACTIN, PHASE)):
            grid.cells[(key, index)].value = tag


def test_the_block_answer_writes_the_same_draft_bytes_as_one_folder_at_a_time(tmp_path):
    root = _plain(tmp_path)
    drafts = []
    for out, how in ((tmp_path / "a", _tag_by_blocks), (tmp_path / "b", _tag_one_by_one)):
        nb = open_screen(root, out)
        set_labels(nb, [DNA, ACTIN, PHASE])
        how(nb)
        assert nb.channel_grid.cells  # still a grid of dropdowns
        drafts.append((out / DRAFT_FILENAME).read_bytes())
    assert drafts[0] == drafts[1]
    assert b'"DNA"' in drafts[0] and drafts[0].count(b'"PHASE"') >= len(KEYS)


def test_a_header_without_every_channel_name_is_tagged_folder_by_folder(tmp_path):
    root = tree(tmp_path, lambda f: ["DAPI", None, "Ch3"])
    nb = open_screen(root, tmp_path / "out")
    set_labels(nb, [DNA, ACTIN, PHASE])
    grid = nb.channel_grid
    assert [len(block) for block in grid.block_list()] == [1, 1, 1, 1]  # never guessed equal
    assert "tagged folder by folder" in repr(grid.blocks.children)
    grid.cells[(KEYS[0], 0)].value = DNA
    assert set(nb.draft_answers.units) == {KEYS[0]}


def test_other_header_names_or_other_tags_make_another_block(tmp_path):
    root = tree(
        tmp_path, lambda f: ["DAPI", "Actin", "Ch3"] if "ctrl" in f else ["dapi", "Actin", "Ch3"]
    )
    nb = open_screen(root, tmp_path / "out")
    set_labels(nb, [DNA, ACTIN, PHASE])
    grid = nb.channel_grid
    assert [[u.key for u in b] for b in grid.block_list()] == [KEYS[:2], KEYS[2:]]  # DAPI ≠ dapi
    grid.split(KEYS[1])  # a folder taken out of its block: its own answer
    assert [len(b) for b in grid.block_list()] == [1, 1, 2]
    grid.cells[(KEYS[1], 0)].value = PHASE
    assert nb.draft_answers.units[KEYS[1]].channels == (PHASE, None, None)
    assert KEYS[0] not in nb.draft_answers.units
    assert "in another block (other tags or taken out)" in repr(grid.blocks.children)


def test_a_label_used_twice_in_a_block_is_refused_for_every_folder(tmp_path):
    nb = open_screen(_plain(tmp_path), tmp_path / "out")
    set_labels(nb, [DNA, ACTIN, PHASE])
    grid = nb.channel_grid
    grid.cells[(KEYS[0], 0)].value = DNA
    grid.cells[(KEYS[0], 1)].value = DNA
    assert "one label cannot stand at two positions" in grid.problem.value
    assert all(nb.draft_answers.units[k].channels == (DNA, None, None) for k in KEYS)


def test_the_header_proposal_is_confirmed_once_per_block(tmp_path):
    root = tree(tmp_path, lambda f: ["DNA", "ACTIN", "PHASE"])
    nb = open_screen(root, tmp_path / "out")
    set_labels(nb, ["ACTIN", "DNA", "PHASE"])
    grid = nb.channel_grid
    assert len(grid.confirm_buttons) == 1 and not nb.draft_answers.units
    grid.confirm_buttons[0].click()
    assert all(nb.draft_answers.units[k].channels == (DNA, ACTIN, PHASE) for k in KEYS)


# --- colour images: one confirmation, on Image type -------------------------------------------


def _rgb_stepper(tmp_path):
    root = tmp_path / "exp"
    for i, folder in enumerate(FOLDERS):
        write_rgb(root / folder / "a.tif", seed=i + 1)
    nb = PreprocessingNotebook(str(root), output_folder=str(tmp_path / "out"))
    nb.steps()
    nb.check_button.click()
    nb.quick_confirm.click()
    assert nb.confirmed_mapping is not None
    set_labels(nb, ["DNA"])
    return nb


def test_colour_is_confirmed_on_image_type_only_and_the_tag_page_restates_it(tmp_path):
    nb = _rgb_stepper(tmp_path)
    grid = nb.channel_grid
    assert not grid.confirm_buttons  # no second confirmation of the colour pre-tick
    text = repr(grid.blocks.children)
    assert "confirm it on Image type" in text
    buttons = [w for w in nb.type_rows.rows.children if isinstance(w, widgets.Button)]
    (button,) = [w for w in buttons if "colour (RGB) is right" in w.description]
    button.click()
    assert all(nb.draft_answers.units[k].image_type == "rgb" for k in KEYS)
    assert "Answered colour (RGB) (Image type)" in repr(grid.blocks.children)
    assert not grid.cells and grid.complete


def test_without_the_stepper_the_grid_keeps_its_one_colour_confirmation(tmp_path):
    root = tmp_path / "exp"
    for i, folder in enumerate(FOLDERS):
        write_rgb(root / folder / "a.tif", seed=i + 1)
    nb = open_screen(root, tmp_path / "out")
    set_labels(nb, ["DNA"])
    assert len(nb.channel_grid.confirm_buttons) == 1  # the image type is answered in the grid
