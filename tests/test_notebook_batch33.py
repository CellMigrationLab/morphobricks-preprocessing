"""Audit batch 33, the notebook side: what is saved is what is shown, of the image checked.

- B1: *Yes, these are right* saves exactly the tags the block shows; a proposal that is not
  shown (its label is tagged elsewhere in the folder) is never saved, and a label is never
  saved at two positions of a folder.
- B3: tags saved for a 3-channel image are not published once the image has 2 channels: the
  save is refused before a new check (the image changed) and after it (the tags are stale),
  until the folder is tagged again.
- B4: a picture is drawn only of the image as the folder check read its header; an image
  changed since (same shape, same header names) is refused with "check the folder again".
- FILE 2: an answer the draft file cannot take (the output folder's lock) is refused, and the
  answers before it are kept: nothing is acknowledged that is not on disk.
"""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from channel_images import write_channels
from morphobricks_preprocessing.description_file import DESCRIPTION_FILENAME, DRAFT_FILENAME
from morphobricks_preprocessing.run.lock import output_lock
from test_notebook_channels import ACTIN, DNA, PHASE, open_screen, set_labels, tree
from test_notebook_check_save import FOLDERS, KINDS, answer_all, exp, opened, tag  # noqa: F401

pytest.importorskip("ipywidgets")
pytest.importorskip("matplotlib")

KEYS = [(*f.split("/"), "single") for f in FOLDERS]


def _rewrite(path: Path, names: list[str], seed: int) -> None:
    """Write another image at the same path, with a later modification time."""
    before = path.stat().st_mtime_ns
    write_channels(path, names, KINDS[: len(names)], seed=seed)
    os.utime(path, ns=(before + 5 * 10**9, before + 5 * 10**9))


# --- B1 -----------------------------------------------------------------------------------------


def test_confirm_saves_only_the_tags_the_block_shows(tmp_path):
    root = tree(tmp_path, lambda f: ["DNA", "ACTIN", "PHASE"])
    nb = open_screen(root, tmp_path / "out")
    set_labels(nb, [ACTIN, DNA, PHASE])
    grid = nb.channel_grid
    grid.cells[(KEYS[0], 1)].value = DNA  # the user tags C1 = DNA: the C0 proposal is hidden
    shown, proposed = grid.effective(grid.layouts[0])
    assert shown == (None, DNA, PHASE) and proposed == {2}
    grid.confirm_buttons[0].click()
    for key in KEYS:  # before the fix: (DNA, DNA, PHASE), a tag never shown
        assert nb.draft_answers.units[key].channels == (None, DNA, PHASE)
    assert b'"DNA", "DNA"' not in (tmp_path / "out" / DRAFT_FILENAME).read_bytes()
    assert not grid.complete  # C0 stays to tag: the block is not answered
    assert "1 channel(s) still to tag" not in grid.status.value  # one per folder, four in all
    assert "4 channel(s) still to tag" in grid.status.value


def test_a_folder_whose_later_image_differs_is_never_grouped_on_its_first_image(tmp_path):
    root = tree(tmp_path, lambda f: ["DAPI", "Actin", "Ch3"])
    for i, folder in enumerate(FOLDERS):  # a second image per folder; one header disagrees
        names = ["DAPI", "Actin", "Phase"] if folder == "ctrl/rep2" else ["DAPI", "Actin", "Ch3"]
        write_channels(root / folder / "b.tif", names, KINDS, seed=i + 20)
    nb = open_screen(root, tmp_path / "out")
    set_labels(nb, [DNA, ACTIN, PHASE])
    grid = nb.channel_grid
    by_key = {u.key: u for u in grid.layouts}
    assert not by_key[KEYS[1]].uniform and by_key[KEYS[0]].uniform
    blocks = [[u.key for u in block] for block in grid.block_list()]
    assert [KEYS[1]] in blocks  # alone, although its first image's header is the others'
    assert [KEYS[0], KEYS[2], KEYS[3]] in blocks
    assert "its images do not all have this header" in repr(grid.blocks.children)
    grid.cells[(KEYS[0], 0)].value = DNA
    assert KEYS[1] not in nb.draft_answers.units  # the block answer does not reach it


def test_a_label_at_two_positions_is_never_saved_whatever_writes_it(tmp_path):
    from morphobricks_preprocessing.channel_answers import with_tags

    root = tree(tmp_path, lambda f: ["DAPI", "Actin", "Ch3"])
    nb = open_screen(root, tmp_path / "out")
    set_labels(nb, [DNA, ACTIN, PHASE])
    layout = nb.channel_grid.layouts[0]
    before = nb.draft_answers
    assert not nb._answer(with_tags(before, layout, (DNA, DNA, PHASE)))
    assert "E_DUPLICATE_CHANNEL_IN_UNIT" in nb._refusal.codes
    assert nb.draft_answers is before


# --- B3 -----------------------------------------------------------------------------------------


def _confirm_axes(nb):
    nb.preview_checked.set(True)
    nb.show_preview()
    nb.confirm_button.click()


def test_tags_saved_for_another_channel_count_are_not_published(exp):  # noqa: F811
    root, masks, out = exp
    nb = opened(*exp)
    answer_all(nb)
    assert nb._check is not None and nb._check.can_save  # a valid 3-channel draft
    for seed, folder in enumerate(FOLDERS):  # now 2 channels, at the same paths
        _rewrite(root / folder / "a.tif", ["DAPI", "Actin"], seed=seed + 9)
    assert not nb.save_experiment()  # before a new check: the image is not the one checked
    assert "changed since the folder check" in nb.save_status.value
    assert not (out / DESCRIPTION_FILENAME).exists()
    nb.check_button.click()  # the new check reads 2 channels; the 3 saved tags are kept on disk
    _confirm_axes(nb)
    assert nb.confirmed_mapping is not None
    assert all(len(nb.draft_answers.units[key].channels) == 3 for key in KEYS)
    nb._refresh_check_save()
    assert "were saved for another image: tag its channels again" in nb.todo.value
    shown = repr(nb.replicates_box.children)  # the stale 3-tag chips are not shown as channels
    assert "3 tags saved for another image (it has 2 channels now)" in shown
    assert ">PHASE<" not in shown
    assert nb.save_button.disabled and not nb.save_experiment()
    assert not (out / DESCRIPTION_FILENAME).exists()
    tag(nb)  # the user retags the folders
    assert all(nb.draft_answers.units[key].channels == (DNA, ACTIN) for key in KEYS)
    nb._refresh_check_save()
    assert "another image" not in nb.todo.value
    assert "tags saved for another image" not in repr(nb.replicates_box.children)
    answer_all(nb)
    assert nb.save_experiment() and (out / DESCRIPTION_FILENAME).exists()


def test_an_unanswered_image_type_keeps_save_and_run_closed_until_answered(exp):  # noqa: F811
    root, masks, out = exp
    nb = opened(*exp)
    answer_all(nb)
    answers = nb.draft_answers  # every other answer given; one image type not (as resumed)
    unit = replace(answers.units[KEYS[2]], image_type=None)
    assert nb._answer(replace(answers, units={**answers.units, KEYS[2]: unit}))
    assert nb.draft_answers.units[KEYS[2]].image_type is None
    nb._refresh_check_save()
    assert "the image type of drug / rep1 is not answered" in nb.todo.value
    assert nb.save_button.disabled and not nb.save_experiment()
    assert not (out / DESCRIPTION_FILENAME).exists() and not nb._run_allowed()
    radio = nb.channel_types.cell(next(u for u in nb._channel_layouts if u.key == KEYS[2]))
    assert radio is not None and nb.channel_types.radios[KEYS[2]].value is None
    nb.channel_types.radios[KEYS[2]].value = "channels"  # the user's answer
    nb._refresh_check_save()
    assert not nb.save_button.disabled and nb.save_experiment()
    assert (out / DESCRIPTION_FILENAME).exists() and nb._run_allowed()


# --- B4 -----------------------------------------------------------------------------------------


def test_a_thumbnail_of_an_image_changed_after_the_check_is_refused(exp):  # noqa: F811
    root = exp[0]
    nb = opened(*exp)
    thumbs, layout = nb._channel_thumbs, nb._channel_layouts[1]
    _rewrite(root / layout.image, ["DAPI", "Actin", "Ch3"], seed=11)  # same shape and names
    for found in (thumbs.get(layout, 0), thumbs.colour(layout), thumbs.tile(layout, False)):
        assert found.png is None and "check the folder again" in found.note
    nb.check_button.click()  # a new check binds the new revision: pictures again
    _confirm_axes(nb)
    assert nb._channel_thumbs is not thumbs
    assert nb._channel_thumbs.get(nb._channel_layouts[1], 0).png is not None


def test_the_step_1_picture_of_an_image_changed_after_the_check_is_refused(exp):  # noqa: F811
    root = exp[0]
    nb = opened(*exp, axes=False)
    previews = nb.label_previews
    _rewrite(root / "ctrl/rep2/a.tif", ["DAPI", "Actin", "Ch3"], seed=12)
    previews.enlarge(previews._leaves["ctrl/rep2"])
    assert "check the folder again" in repr(previews.detail.children)
    previews.tiles.pop("ctrl/rep2")
    previews.draw("ctrl/rep2")
    assert previews.found["ctrl/rep2"].pictures is None
    assert "check the folder again" in previews.found["ctrl/rep2"].problem


def test_the_mask_evidence_of_an_image_changed_after_the_check_is_refused(exp):  # noqa: F811
    root = exp[0]
    nb = opened(*exp)
    tag(nb)
    _rewrite(root / "ctrl/rep1/a.tif", ["DAPI", "Actin", "Ch3"], seed=13)
    nb._mask_found = {}
    found = nb._mask_evidence("Nuclei")
    assert found.picture is None and "check the folder again" in found.problem


def test_mask_evidence_already_read_is_not_shown_once_its_image_changed(exp):  # noqa: F811
    root = exp[0]
    nb = opened(*exp)
    tag(nb)
    assert nb._mask_evidence("Nuclei").picture is not None  # read once, kept
    _rewrite(root / "ctrl/rep1/a.tif", ["DAPI", "Actin", "Ch3"], seed=14)
    found = nb._mask_evidence("Nuclei")  # before the fix: the kept picture of the old image
    assert found.picture is None and "check the folder again" in found.problem


def test_masks_paired_again_are_bound_and_a_changed_mask_is_refused(exp):  # noqa: F811
    root, masks, _ = exp
    nb = opened(*exp)
    tag(nb)
    nb._check_masks()  # the masks are paired again (remasked), without a new folder check
    assert nb.preflight.masks.files and all(m.bound for m in nb.preflight.masks.files)
    mask = masks / "Nuclei" / "ctrl/rep1/a.tif"
    before = mask.stat().st_mtime_ns
    mask.write_bytes(mask.read_bytes())  # another revision of the mask, same pixels
    os.utime(mask, ns=(before + 5 * 10**9, before + 5 * 10**9))
    found = nb._mask_evidence("Nuclei")  # before the fix: drawn, as if it were the one paired
    assert found.picture is None and "check the folder again" in found.problem


# --- FILE 2 -------------------------------------------------------------------------------------


def test_an_answer_refused_by_the_output_lock_is_not_acknowledged(tmp_path):
    root = tree(tmp_path, lambda f: ["DAPI", "Actin", "Ch3"])
    out = tmp_path / "out"
    nb = open_screen(root, out)
    set_labels(nb, [DNA, ACTIN, PHASE])
    before, on_disk = nb.draft_answers, (out / DRAFT_FILENAME).read_bytes()
    with output_lock(out):
        nb.channel_grid.cells[(KEYS[0], 0)].value = DNA
    assert nb.draft_answers == before  # kept as it was: the answer was not saved
    assert (out / DRAFT_FILENAME).read_bytes() == on_disk
    assert "Not saved" in nb.channel_grid.problem.value
    assert "lock" in nb.channel_grid.problem.value
    assert nb.channel_grid.cells[(KEYS[0], 0)].value is None  # the grid shows what is saved
